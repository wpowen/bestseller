"""Canonical genre taxonomy — the single source of truth for 题材.

The framework historically expressed 题材 in five mutually inconsistent ways
(preset cards, ``dimensions.yaml`` ``primary_genre``, the 13
``novel_categories``, ``material_library`` free-form strings, and prompt
packs).  This module loads ``config/genre_taxonomy.yaml`` — a
``channel → genre → sub_genre → tag`` tree with ``aliases`` — and exposes
helpers that *converge* those layers:

* :func:`canonicalize` maps any free-form genre string to a canonical genre
  key (treating sub-genre labels and aliases as synonyms).
* :func:`retrieval_aliases` returns the material-library buckets that a
  selection can safely draw on (fixes cross-layer 0-hit retrieval).
* :func:`resolve_selection` turns a user selection
  ``(channel, genre, sub_genre, tags)`` into the concrete downstream carriers
  ``(genre_str, sub_genre_str, category, pack, tags, power_system)``.

Follows the same YAML-loading pattern as ``novel_categories.py`` /
``prompt_packs.py``.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Channel(BaseModel, frozen=True):
    key: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=32)


class TagDef(BaseModel, frozen=True):
    key: str = Field(min_length=1, max_length=40)
    label_en: str = ""


class SubGenre(BaseModel, frozen=True):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=60)
    category: str | None = None
    pack: str | None = None
    power_system: str | None = None
    default_tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


class Genre(BaseModel, frozen=True):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=60)
    channel: tuple[str, ...] = ()
    heat: int = 0
    category_default: str = Field(min_length=1, max_length=64)
    pack_default: str | None = None
    aliases: tuple[str, ...] = ()
    sub_genres: tuple[SubGenre, ...] = ()


class GenreTaxonomy(BaseModel, frozen=True):
    version: int = 1
    channels: tuple[Channel, ...] = ()
    tags_additional: tuple[TagDef, ...] = ()
    genres: tuple[Genre, ...] = ()


class ResolvedSelection(BaseModel, frozen=True):
    """Concrete downstream carriers derived from a user selection."""

    channel: str | None = None
    genre_key: str | None = None
    sub_genre_key: str | None = None
    genre_str: str = ""
    sub_genre_str: str | None = None
    category: str | None = None
    pack: str | None = None
    power_system: str | None = None
    tags: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _taxonomy_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "genre_taxonomy.yaml"


def _dimensions_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "config"
        / "facets"
        / "dimensions.yaml"
    )


@lru_cache(maxsize=1)
def load_genre_taxonomy() -> GenreTaxonomy:
    """Load and validate ``config/genre_taxonomy.yaml`` (cached)."""
    path = _taxonomy_path()
    if not path.exists():
        return GenreTaxonomy()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return GenreTaxonomy.model_validate(raw)
    except Exception:
        logger.warning("Failed to load genre taxonomy from %s", path, exc_info=True)
        return GenreTaxonomy()


# ---------------------------------------------------------------------------
# Listing accessors
# ---------------------------------------------------------------------------


def list_channels() -> list[Channel]:
    return list(load_genre_taxonomy().channels)


def list_genres(channel: str | None = None) -> list[Genre]:
    genres = load_genre_taxonomy().genres
    if channel is None:
        return list(genres)
    return [g for g in genres if channel in g.channel]


def get_genre(key: str | None) -> Genre | None:
    if not key:
        return None
    for genre in load_genre_taxonomy().genres:
        if genre.key == key:
            return genre
    return None


def get_sub_genre(genre_key: str | None, sub_key: str | None) -> SubGenre | None:
    genre = get_genre(genre_key)
    if genre is None or not sub_key:
        return None
    for sub in genre.sub_genres:
        if sub.key == sub_key:
            return sub
    return None


def iter_sub_genres() -> list[tuple[Genre, SubGenre]]:
    out: list[tuple[Genre, SubGenre]] = []
    for genre in load_genre_taxonomy().genres:
        for sub in genre.sub_genres:
            out.append((genre, sub))
    return out


# ---------------------------------------------------------------------------
# Alias index / canonicalization
# ---------------------------------------------------------------------------


# Tie-break priority when several aliases of equal length match as substrings.
# Mirrors the intent of the existing keyword routers: rule/survival/urban win
# over the generic cultivation catch-all (see prompt_packs.infer_default).
_CANON_PRIORITY: tuple[str, ...] = (
    "apocalypse",
    "suspense",
    "occult",
    "infinite-flow",
    "game",
    "gu-yan",
    "xian-yan",
    "female-derivative",
    "female-growth",
    "fantasy-romance",
    "pure-love",
    "urban",
    "xianxia",
    "xuanhuan",
    "scifi",
    "wuxia",
    "history",
    "military",
    "light-novel",
    "realistic",
)


def _priority_index(genre_key: str) -> int:
    try:
        return _CANON_PRIORITY.index(genre_key)
    except ValueError:
        return len(_CANON_PRIORITY)


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    """Map every alias string → canonical genre key.

    Sources: each genre's key/label/aliases and each sub-genre's
    label/aliases.  Longer, more specific strings are kept so that exact and
    longest-substring matching can both work.
    """
    index: dict[str, str] = {}

    def _put(token: str | None, genre_key: str) -> None:
        token = (token or "").strip()
        if not token:
            return
        # Do not let a generic alias overwrite a more specific existing mapping
        # to a different genre; first writer of a given exact string wins, but
        # prefer the higher-priority genre on conflict for determinism.
        existing = index.get(token)
        if existing is None:
            index[token] = genre_key
        elif existing != genre_key:
            if _priority_index(genre_key) < _priority_index(existing):
                index[token] = genre_key

    for genre in load_genre_taxonomy().genres:
        _put(genre.key, genre.key)
        _put(genre.label, genre.key)
        for alias in genre.aliases:
            _put(alias, genre.key)
        for sub in genre.sub_genres:
            _put(sub.label, genre.key)
            for alias in sub.aliases:
                _put(alias, genre.key)
    return index


def canonicalize(genre_str: str | None, sub_genre: str | None = None) -> str | None:
    """Return the canonical genre key for a free-form genre/sub-genre string.

    Strategy: exact-match the genre string, then the sub_genre string, then
    longest-substring containment (priority-broken on ties).  Returns ``None``
    when nothing matches (caller should fall back to legacy routing).
    """
    index = _alias_index()

    for candidate in (genre_str, sub_genre):
        token = (candidate or "").strip()
        if token and token in index:
            return index[token]

    haystack = f"{genre_str or ''} {sub_genre or ''}"
    best_key: str | None = None
    best_len = 0
    for alias, genre_key in index.items():
        if alias and alias in haystack:
            alias_len = len(alias)
            if alias_len > best_len or (
                alias_len == best_len
                and best_key is not None
                and _priority_index(genre_key) < _priority_index(best_key)
            ):
                best_key = genre_key
                best_len = alias_len
    return best_key


def retrieval_aliases(genre_str: str | None, sub_genre: str | None = None) -> tuple[str, ...]:
    """Return material-library buckets a selection may safely draw on.

    Resolves the canonical genre and returns its label + alias strings (the
    free-form buckets that ``material_library`` is seeded under, e.g. a
    ``天灾囤货`` book widens to both ``末日`` and ``末世``).  Conservative: stays
    within a single canonical genre's own reader promise.  Always includes the
    original string so callers can union safely.
    """
    values: list[str] = []

    def _add(value: str | None) -> None:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)

    _add(genre_str)
    _add(sub_genre)
    canon = canonicalize(genre_str, sub_genre)
    genre = get_genre(canon)
    if genre is not None:
        _add(genre.label)
        for alias in genre.aliases:
            _add(alias)
    return tuple(values)


# ---------------------------------------------------------------------------
# Tag pool
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _trope_tag_keys() -> frozenset[str]:
    """Keys from dimensions.yaml ``trope_tags`` ∪ taxonomy ``tags_additional``."""
    keys: set[str] = set()
    path = _dimensions_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for dim in raw.get("dimensions", []):
            if dim.get("name") == "trope_tags":
                for value in dim.get("values", []):
                    key = value.get("key") if isinstance(value, dict) else value
                    if key:
                        keys.add(str(key))
    except Exception:
        logger.debug("Could not load trope_tags from dimensions.yaml", exc_info=True)
    for tag in load_genre_taxonomy().tags_additional:
        keys.add(tag.key)
    return frozenset(keys)


def tag_pool() -> frozenset[str]:
    return _trope_tag_keys()


def is_known_tag(value: str | None) -> bool:
    token = (value or "").strip()
    return bool(token) and token in _trope_tag_keys()


# ---------------------------------------------------------------------------
# Selection resolution
# ---------------------------------------------------------------------------


def _resolve_genre(genre: str | None) -> Genre | None:
    """Resolve a genre identifier that may be a key OR a free-form string."""
    direct = get_genre(genre)
    if direct is not None:
        return direct
    return get_genre(canonicalize(genre))


def resolve_selection(
    channel: str | None,
    genre: str | None,
    sub_genre: str | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
) -> ResolvedSelection:
    """Turn a user selection into concrete downstream carriers.

    ``genre``/``sub_genre`` accept canonical keys or free-form labels.
    Returns the composed genre string, mapped ``novel_category`` and
    ``prompt_pack``, the merged tag list, and the power system — everything the
    project record and downstream routers need.
    """
    g = _resolve_genre(genre)
    sub = get_sub_genre(g.key, sub_genre) if g is not None else None

    genre_str = (sub.label if sub else (g.label if g else (genre or ""))).strip()
    sub_str = sub.label if sub else None
    category = (sub.category if sub and sub.category else None) or (
        g.category_default if g else None
    )
    pack = (sub.pack if sub and sub.pack else None) or (g.pack_default if g else None)
    power_system = sub.power_system if sub else None

    merged: list[str] = []
    for tag in [*(list(sub.default_tags) if sub else []), *(list(tags or []))]:
        cleaned = (tag or "").strip()
        if cleaned and cleaned not in merged:
            merged.append(cleaned)

    return ResolvedSelection(
        channel=channel,
        genre_key=g.key if g else None,
        sub_genre_key=sub.key if sub else None,
        genre_str=genre_str or (genre or ""),
        sub_genre_str=sub_str,
        category=category,
        pack=pack,
        power_system=power_system,
        tags=tuple(merged),
    )
