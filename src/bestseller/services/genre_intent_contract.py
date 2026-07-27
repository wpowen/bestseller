"""Immutable user-owned genre intent carried through novel creation.

The taxonomy resolver is deterministic and framework-owned.  LLM agents may
suggest surface facets, but they must not choose the book's genre, sub-genre,
or prompt pack.  This module keeps that boundary explicit and serializable.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bestseller.services.genre_taxonomy import ResolvedSelection, resolve_selection
from bestseller.services.story_enhancers import StoryEnhancerSelection

# CJK tripwire terms are matched as substrings (Chinese has no word boundaries).
# Strong signals that the global forensic/funeral hybrid lane leaked into a
# genre-native book. These are not blanket bans on fantasy corpses; they are
# final-tripwire terms for unexplained modern/professional ontology.
#
# ADMISSION TEST — a term belongs here only if it denotes something that CANNOT
# exist in a pre-modern world (an institution, a technology, a profession born
# of modern science). Classical words must stay out: 收尸/入殓/殡葬 are rites
# older than the genre itself, and banning them killed a 玄幻 book whose
# premise was a sect corpse-handler (2026-07-25, custom-xuanhuan-1784908885)
# — while ``writing_presets`` was simultaneously SELLING 收尸人 as a
# genre-native profession and the tournament tests used it as a valid seed.
# ``test_ontology_tripwire_false_positives`` enforces both halves of this rule.
_GENRE_NATIVE_MODERNITY_CJK: tuple[str, ...] = (
    "手机",
    "微信",
    "短信",
    "写字楼",
    "职场",
    "现代都市",
    # Forensic science — the discipline itself is modern.
    "尸检",
    "法医",
    "法医鉴定",
    # Modern institutions/technology, not rites.
    "殡仪馆",
    "停尸房",
    "器官移植",
)

# Latin/ASCII tripwire terms MUST match on word boundaries. A serialized
# writing profile is full of ordinary English (appeal, approach, apply, mapping,
# happen, application…) that merely contains these letters; a naive substring
# match falsely fired and killed whole books at the final conception tripwire.
_GENRE_NATIVE_MODERNITY_ASCII: tuple[str, ...] = ("app",)
_ASCII_VIOLATION_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(re.escape(term) for term in _GENRE_NATIVE_MODERNITY_ASCII)
    + r")(?![A-Za-z])",
    re.IGNORECASE,
)

# Retained for backward compatibility with callers/tests that enumerate the
# full tripwire vocabulary.
_GENRE_NATIVE_MODERNITY_VIOLATIONS: tuple[str, ...] = (
    *_GENRE_NATIVE_MODERNITY_ASCII,
    *_GENRE_NATIVE_MODERNITY_CJK,
)


class GenreIntentContract(BaseModel, frozen=True):
    """The authoritative creation-time genre contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["genre-intent.v1"] = "genre-intent.v1"
    source: Literal["user", "legacy_inferred"] = "user"
    channel_key: str | None = None
    genre_key: str = Field(min_length=1, max_length=64)
    genre_label: str = Field(min_length=1, max_length=120)
    sub_genre_key: str | None = None
    sub_genre_label: str | None = None
    tags: tuple[str, ...] = ()
    # The sub-genre's own default_tags vs what the user actually ticked. Kept
    # apart so prompts stop presenting genre defaults as explicit user choices.
    default_tags: tuple[str, ...] = ()
    user_tags: tuple[str, ...] = ()
    category_key: str | None = None
    prompt_pack_key: str = Field(min_length=1, max_length=128)
    audience_orientation: str | None = None
    narrative_scale: str | None = None
    tone_preference: str | None = None
    allowed_modernity: Literal["genre_native", "modern", "hybrid"] = "genre_native"
    explicit_enhancers: StoryEnhancerSelection = StoryEnhancerSelection()

    def contract_hash(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _allowed_modernity(resolved: ResolvedSelection) -> Literal["genre_native", "modern", "hybrid"]:
    """Which ontology this book may natively use — declared by the taxonomy.

    Was a 2-line hardcode covering only 都市 / urban-cultivation, which left the
    other 19 genres on ``genre_native``. Consequences (all verified live):
    a 悬疑推理 book was forbidden its own core vocabulary (法医/尸检/停尸房), and
    现代言情 / 现实(行业职场) / 游戏竞技 / 末世 books could not mention
    手机/微信/写字楼/职场 without the final conception tripwire raising and
    killing the book. Now each genre/sub-genre declares it in the YAML.
    """

    declared = str(getattr(resolved, "allowed_modernity", "") or "").strip()
    if declared in ("genre_native", "modern", "hybrid"):
        return declared  # type: ignore[return-value]
    # Safety net for selections resolved outside the taxonomy (no declaration).
    if resolved.sub_genre_key == "urban-cultivation" or resolved.genre_key == "urban":
        return "modern"
    return "genre_native"


def build_genre_intent_contract(
    resolved: ResolvedSelection,
    *,
    source: Literal["user", "legacy_inferred"] = "user",
    audience_orientation: str | None = None,
    narrative_scale: str | None = None,
    tone_preference: str | None = None,
    enhancers: StoryEnhancerSelection | None = None,
) -> GenreIntentContract:
    """Build a contract from a taxonomy result without model inference."""

    if not resolved.genre_key or not resolved.pack or not resolved.genre_str:
        raise ValueError("A genre intent requires a resolved genre and prompt pack")
    return GenreIntentContract(
        source=source,
        channel_key=resolved.channel,
        genre_key=resolved.genre_key,
        genre_label=resolved.genre_str,
        sub_genre_key=resolved.sub_genre_key,
        sub_genre_label=resolved.sub_genre_str,
        tags=resolved.tags,
        default_tags=resolved.default_tags,
        user_tags=resolved.user_tags,
        category_key=resolved.category,
        prompt_pack_key=resolved.pack,
        audience_orientation=audience_orientation,
        narrative_scale=narrative_scale,
        tone_preference=tone_preference,
        allowed_modernity=_allowed_modernity(resolved),
        explicit_enhancers=enhancers or StoryEnhancerSelection(),
    )


def contract_from_selection(
    selection: dict[str, Any],
    *,
    source: Literal["user", "legacy_inferred"] = "user",
    audience_orientation: str | None = None,
    narrative_scale: str | None = None,
    tone_preference: str | None = None,
    enhancers: StoryEnhancerSelection | None = None,
) -> GenreIntentContract:
    """Resolve a UI selection and immediately freeze its downstream intent."""

    tags = [item for item in (selection.get("tags") or []) if isinstance(item, str)]
    resolved = resolve_selection(
        selection.get("channel"),
        selection.get("genre"),
        selection.get("sub_genre"),
        tags,
    )
    return build_genre_intent_contract(
        resolved,
        source=source,
        audience_orientation=audience_orientation,
        narrative_scale=narrative_scale,
        tone_preference=tone_preference,
        enhancers=enhancers,
    )


def contract_from_payload(payload: dict[str, Any]) -> GenreIntentContract | None:
    """Read a persisted contract defensively; invalid data never becomes authority."""

    raw = payload.get("genre_intent_contract")
    if not isinstance(raw, dict):
        return None
    try:
        return GenreIntentContract.model_validate(raw)
    except ValueError:
        return None


def detect_genre_native_ontology_violations(
    text: str,
    contract: GenreIntentContract,
) -> tuple[str, ...]:
    """Find high-signal modern ontology leakage in generated text.

    This is intentionally narrow: it is not a blanket ban on contemporary
    vocabulary, only a final tripwire for the recurring APP/phone/workplace/
    forensic-modern drift in a genre-native contract.
    """

    if contract.allowed_modernity != "genre_native":
        return ()
    haystack = str(text or "")
    hits = [term for term in _GENRE_NATIVE_MODERNITY_CJK if term in haystack]
    if _ASCII_VIOLATION_RE.search(haystack):
        hits.append("app")
    return tuple(hits)
