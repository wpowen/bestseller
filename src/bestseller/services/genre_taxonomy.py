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
    # "genre_native" | "modern" | "hybrid"; None = inherit from the parent genre.
    allowed_modernity: str | None = None


class Genre(BaseModel, frozen=True):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=60)
    channel: tuple[str, ...] = ()
    heat: int = 0
    category_default: str = Field(min_length=1, max_length=64)
    pack_default: str | None = None
    aliases: tuple[str, ...] = ()
    sub_genres: tuple[SubGenre, ...] = ()
    # Which ontology this genre may natively use. Declared here (data) instead of
    # hardcoded in genre_intent_contract, where a 2-line check covered only 都市
    # and left 悬疑推理 forbidden from 法医/尸检 — its own core vocabulary.
    allowed_modernity: str | None = None


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
    # The sub-genre's own default_tags, kept separate from the user's picks so
    # downstream prompts can stop presenting them as "the user explicitly chose
    # this". ``tags`` stays the merged list for routing/back-compat.
    default_tags: tuple[str, ...] = ()
    user_tags: tuple[str, ...] = ()
    # Resolved from sub-genre → genre declaration; None = caller's default.
    allowed_modernity: str | None = None


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
    """Resolve a sub-genre by canonical key, display label, or alias.

    ``resolve_selection``'s docstring and the REST schema both promise that
    genre/sub_genre accept "canonical keys **or free-form labels**". Matching
    only ``sub.key`` silently dropped every label-based caller — the 62 preset
    cards (``server.py`` legacy path), the CLI, and the REST API all pass the
    Chinese label — and took the sub-genre's ``pack`` / ``power_system`` /
    ``default_tags`` down with it (37/62 presets resolved sub_genre=None).
    Key wins over label wins over alias, so canonical keys stay authoritative.
    """

    genre = get_genre(genre_key)
    if genre is None or not sub_key:
        return None
    probe = str(sub_key).strip()
    if not probe:
        return None
    lowered = probe.lower()
    for sub in genre.sub_genres:
        if sub.key == probe or sub.key.lower() == lowered:
            return sub
    for sub in genre.sub_genres:
        if sub.label.strip() == probe or sub.label.strip().lower() == lowered:
            return sub
    for sub in genre.sub_genres:
        for alias in sub.aliases:
            cleaned = str(alias).strip()
            if cleaned and (cleaned == probe or cleaned.lower() == lowered):
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


# Cultivation/martial "spine" precedence. ``_CANON_PRIORITY`` ranks the
# horror/suspense/game *flavor* genres ABOVE 修仙/玄幻, so a hybrid like
# 诡异修仙+规则怪谈 (or 宗门经营 → game) canonicalised to suspense/game and every
# downstream resolver inherited the wrong genre. The spine is the load-bearing
# axis: when a clear cultivation/martial spine token is present, it wins over a
# flavor genre. Centralising the rule here (the single canonical front door)
# replaces the per-resolver keyword guards that previously had to be patched one
# by one — every consumer that goes through ``canonicalize`` now agrees.
_SPINE_TOKENS_BY_GENRE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("xianxia", ("诡异修仙", "修仙", "修真", "仙侠", "炼气", "渡劫", "金丹", "元婴", "道种")),
    ("xuanhuan", ("玄幻", "高武", "武道", "斗气", "灵气复苏", "极道")),
)
# Flavor genres that a cultivation/martial spine should override when both match.
_SPINE_OVERRIDABLE_FLAVOR_GENRES: frozenset[str] = frozenset(
    {"suspense", "occult", "game", "infinite-flow"}
)


def _spine_genre_in(haystack: str) -> str | None:
    """Return the cultivation/martial spine genre present in *haystack*, if any."""
    for genre_key, tokens in _SPINE_TOKENS_BY_GENRE:
        if any(token in haystack for token in tokens):
            return genre_key
    return None


@lru_cache(maxsize=1)
def _pack_category_index() -> dict[str, str]:
    """Map each known prompt-pack key → its review category.

    Built from genre/sub-genre ``pack`` ↔ ``category`` pairs in the taxonomy.
    Used to tell whether two different packs belong to the SAME family (e.g.
    xianxia-upgrade-core and xuanhuan-power-fantasy are both action-progression)
    so the contamination guard does not "correct" one cultivation pack into a
    sibling cultivation pack.
    """
    index: dict[str, str] = {}
    for genre in load_genre_taxonomy().genres:
        if genre.pack_default:
            index.setdefault(genre.pack_default, genre.category_default)
        for sub in genre.sub_genres:
            if sub.pack and sub.category:
                index.setdefault(sub.pack, sub.category)
    return index


def pack_category(pack_key: str | None) -> str | None:
    """Return the review category a prompt-pack serves, or ``None`` if unknown."""
    if not pack_key:
        return None
    return _pack_category_index().get(pack_key)


def canonicalize(genre_str: str | None, sub_genre: str | None = None) -> str | None:
    """Return the canonical genre key for a free-form genre/sub-genre string.

    Strategy: exact-match the genre string, then the sub_genre string, then
    longest-substring containment (priority-broken on ties).  A cultivation/
    martial spine overrides a matched flavor genre (see ``_SPINE_TOKENS_BY_GENRE``).
    Returns ``None`` when nothing matches (caller should fall back to legacy
    routing).
    """
    index = _alias_index()

    haystack = f"{genre_str or ''} {sub_genre or ''}"
    for candidate in (genre_str, sub_genre):
        token = (candidate or "").strip()
        if token and token in index:
            matched = index[token]
            # Spine precedence still applies to exact matches: an exact
            # "诡异修仙" must not fall to occult when the spine is unambiguous.
            if matched in _SPINE_OVERRIDABLE_FLAVOR_GENRES:
                spine = _spine_genre_in(haystack)
                if spine is not None:
                    return spine
            return matched

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
    if best_key in _SPINE_OVERRIDABLE_FLAVOR_GENRES:
        spine = _spine_genre_in(haystack)
        if spine is not None:
            return spine
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


def _resolve_genre(genre: str | None, sub_genre: str | None = None) -> Genre | None:
    """Resolve a genre identifier that may be a key OR a free-form string.

    ``sub_genre`` participates in canonicalisation — ``canonicalize`` is built to
    take the pair, and some presets only resolve through the combination
    (青春成长 alone → None; 青春成长 + 校园群像 → light-novel).

    But handing both to ``canonicalize`` at once lets any stray token inside a
    free-form sub-label outvote the genre the user actually picked: 惊悚灵异 +
    驱魔探案综合 → suspense (「探案」 wins), 历史宫廷 + 宫廷悬疑 → suspense. The
    taxonomy pick silently not taking effect is the very complaint this pass is
    about. Yet the sub sometimes *should* win: 奇幻冒险 + 无限闯关 → infinite-flow
    is right, because 无限闯关 is a real sub-genre that xuanhuan does not own.

    The line between those two is whether the sub is a taxonomy citizen:

    * genre alone resolves, and owns the sub  → genre (nothing to refine)
    * genre alone resolves, sub is a real sub-genre of another genre → that
      genre (the sub is more specific, and it is a declared citizen)
    * genre alone resolves, sub is just a free-form label → genre (a label must
      never outvote the pick)
    * genre alone resolves to nothing → let the pair rescue it
      (青春成长 → None; 青春成长 + 校园群像 → light-novel)
    """

    base = get_genre(genre) or get_genre(canonicalize(genre, None))
    if base is None:
        return get_genre(canonicalize(genre, sub_genre))
    if not sub_genre or get_sub_genre(base.key, sub_genre) is not None:
        return base
    paired = get_genre(canonicalize(genre, sub_genre))
    if (
        paired is not None
        and paired.key != base.key
        and get_sub_genre(paired.key, sub_genre) is not None
    ):
        return paired
    return base


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
    g = _resolve_genre(genre, sub_genre)
    sub = get_sub_genre(g.key, sub_genre) if g is not None else None

    # ``genre_str`` is deliberately the MOST SPECIFIC label (sub-genre when one
    # resolved) — it is the composed display/routing string and is asserted as
    # such by the taxonomy tests. Consumers that need the parent must read
    # ``genre_key`` and look the genre up (see writing_presets.synthesize_genre_preset).
    genre_str = (sub.label if sub else (g.label if g else (genre or ""))).strip()
    sub_str = sub.label if sub else None
    category = (sub.category if sub and sub.category else None) or (
        g.category_default if g else None
    )
    pack = (sub.pack if sub and sub.pack else None) or (g.pack_default if g else None)
    power_system = sub.power_system if sub else None

    sub_defaults = [t.strip() for t in (sub.default_tags if sub else ()) if (t or "").strip()]
    picked = [t.strip() for t in (tags or []) if (t or "").strip()]
    merged: list[str] = []
    for tag in [*sub_defaults, *picked]:
        if tag not in merged:
            merged.append(tag)

    modernity = (sub.allowed_modernity if sub else None) or (
        g.allowed_modernity if g else None
    )

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
        default_tags=tuple(dict.fromkeys(sub_defaults)),
        user_tags=tuple(dict.fromkeys(picked)),
        allowed_modernity=modernity,
    )
