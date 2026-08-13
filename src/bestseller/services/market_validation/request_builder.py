"""Single resolver turning creation-time facts into a validation request.

Every creation entrypoint names the genre differently: the web quickstart
carries a synthetic preset key (``custom-xianxia``) plus the validated
``genre_intent_contract``; the CLI/API path writes ``genre_canonical``; older
rows only have the display labels. Feeding the wrong one in silently produces
an empty category mapping — the report still renders, just with the heat and
competitor sections skipped. So resolution lives here once and every call site
uses it.
"""


from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
from typing import Any

from bestseller.domain.market_validation import MarketValidationRequest

logger = logging.getLogger(__name__)


def _canonicalize(genre: str, sub_genre: str = "") -> str:
    try:
        from bestseller.services.genre_taxonomy import canonicalize

        return canonicalize(genre or None, sub_genre or None) or ""
    except Exception:
        logger.debug("canonicalize failed for %r/%r", genre, sub_genre, exc_info=True)
        return ""


def _is_known_genre_key(key: str) -> bool:
    if not key:
        return False
    try:
        from bestseller.services.genre_taxonomy import get_genre

        return get_genre(key) is not None
    except Exception:
        return False


def _sub_key_from_label(genre_key: str, sub_genre_label: str) -> str:
    if not (genre_key and sub_genre_label):
        return ""
    try:
        from bestseller.services.genre_taxonomy import get_genre

        genre = get_genre(genre_key)
        if genre is None:
            return ""
        for sub in genre.sub_genres:
            if sub_genre_label in (sub.key, sub.label):
                return sub.key
    except Exception:
        logger.debug("sub-genre lookup failed for %r", sub_genre_label, exc_info=True)
    return ""


def resolve_taxonomy_keys(
    *,
    metadata: Mapping[str, Any] | None = None,
    genre_label: str = "",
    sub_genre_label: str = "",
    fallback_genre_key: str = "",
) -> tuple[str, str]:
    """Resolve canonical ``(genre_key, sub_genre_key)`` from creation facts.

    Priority: validated ``genre_intent_contract`` → ``genre_canonical`` →
    canonicalized labels → canonicalized fallback key. Unresolvable input
    yields ``("", "")`` so callers degrade the mapped sections instead of
    querying with a bogus key.
    """

    meta = metadata if isinstance(metadata, Mapping) else {}
    genre_key = ""
    sub_genre_key = ""

    contract = meta.get("genre_intent_contract")
    if isinstance(contract, Mapping):
        candidate = str(contract.get("genre_key") or "").strip()
        if _is_known_genre_key(candidate):
            genre_key = candidate
            sub_genre_key = str(contract.get("sub_genre_key") or "").strip()

    if not genre_key:
        candidate = str(meta.get("genre_canonical") or "").strip()
        if _is_known_genre_key(candidate):
            genre_key = candidate

    if not genre_key:
        genre_key = _canonicalize(genre_label, sub_genre_label)

    if not genre_key and fallback_genre_key:
        genre_key = _canonicalize(fallback_genre_key)

    if not _is_known_genre_key(genre_key):
        genre_key = ""

    if genre_key and not sub_genre_key:
        sub_genre_key = _sub_key_from_label(genre_key, sub_genre_label)

    return genre_key, sub_genre_key


def build_creation_request(
    *,
    metadata: Mapping[str, Any] | None = None,
    genre_label: str = "",
    sub_genre_label: str = "",
    title: str | Sequence[str] = (),
    concept: str = "",
    blurb: str = "",
    fallback_genre_key: str = "",
    channel: str = "",
    project_slug: str = "",
) -> MarketValidationRequest:
    """Build a validation request from one book's creation-time facts.

    ``title`` accepts a single name (creation paths, which have exactly one)
    or several candidates (the CLI, which compares a shortlist).
    """

    genre_key, sub_genre_key = resolve_taxonomy_keys(
        metadata=metadata,
        genre_label=genre_label,
        sub_genre_label=sub_genre_label,
        fallback_genre_key=fallback_genre_key,
    )
    raw_titles = (title,) if isinstance(title, str) else tuple(title or ())
    clean_titles = tuple(
        stripped for stripped in (str(item).strip() for item in raw_titles) if stripped
    )
    return MarketValidationRequest(
        genre_key=genre_key,
        genre_label=str(genre_label or "").strip(),
        sub_genre_key=sub_genre_key,
        sub_genre_label=str(sub_genre_label or "").strip(),
        channel=str(channel or "").strip(),
        concept=str(concept or "").strip()[:2000],
        title_candidates=clean_titles,
        blurb=str(blurb or "").strip()[:5000],
        project_slug=str(project_slug or "").strip(),
    )
