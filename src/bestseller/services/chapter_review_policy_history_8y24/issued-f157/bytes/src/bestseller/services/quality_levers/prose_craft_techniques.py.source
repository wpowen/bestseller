"""Prose Craft Techniques loader (``config/prose_craft_techniques.yaml``).

The framework already covers *vivid* prose (``visual_writing`` camera formulas)
and *voice consistency* (``prose_style_anchors``). What it lacks is craft for the
``golden_line`` signature type — the rhetorical structure that makes a single
sentence *memorable / screenshot-worthy*. This module distils a corpus of Chinese
"绝句"-style lines into **transferable technique skeletons** (not copyable phrases)
and renders a compact, genre-aware, rotation-varied writer block.

Design guarantees (see the YAML header for the full rationale):

* **Soft only.** Nothing here feeds a gate / floor / must_rewrite. ``golden_line``
  is one of six signature types and is itself optional, so enriching it never
  blocks a chapter.
* **Techniques, not phrases.** Avoids the template-override regression where a
  phrase bank overrides model output and homogenises every book.
* **Genre-adaptive, anti-purple.** ``genre_emphasis`` routes modern genres to
  structural/colloquial techniques (not 古风 imagery), and a
  ``purple_prose_guard`` names the 辞藻堆砌 failure mode — consistent with the
  framework's "文笔靠具体、不靠华丽修辞" stance.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    as_str_tuple,
    load_yaml,
)

_CONFIG_FILENAME = "prose_craft_techniques.yaml"
_DEFAULT_EMPHASIS_KEY = "default"


# ---------------------------------------------------------------------------
# Typed view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CraftExample:
    """One ``micro_examples`` entry: a synthetic, genre-tagged line."""

    tag: str
    line: str


@dataclass(frozen=True)
class CraftTechnique:
    """One ``techniques.<id>`` entry — a memorable-line skeleton."""

    technique_id: str
    display_name: str
    category: str
    principle: str
    structure: str
    purple_risk: str
    genre_good: tuple[str, ...]
    genre_careful: tuple[str, ...]
    genre_avoid: tuple[str, ...]
    examples: tuple[CraftExample, ...]

    def example_for(self, genre_terms: tuple[str, ...]) -> CraftExample | None:
        """Prefer an example whose tag matches one of the genre terms."""

        if not self.examples:
            return None
        for example in self.examples:
            if example.tag and any(example.tag in term for term in genre_terms):
                return example
        return self.examples[0]


@dataclass(frozen=True)
class PurpleGuardMove:
    """One ``purple_prose_guard.banned_moves`` entry."""

    move_id: str
    bad: str
    why: str
    fix: str


@dataclass(frozen=True)
class ProseCraftConfig:
    """Typed view over the YAML."""

    version: str
    techniques: dict[str, CraftTechnique]
    purple_guard: tuple[PurpleGuardMove, ...]
    genre_emphasis: dict[str, tuple[str, ...]]
    techniques_per_scene: int
    example_per_technique: int
    rotate_by_chapter: bool
    position_hint: str
    hard_rule: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_examples(raw: object) -> tuple[CraftExample, ...]:
    examples: list[CraftExample] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            line = as_str(data.get("line"))
            if not line:
                continue
            examples.append(CraftExample(tag=as_str(data.get("tag")), line=line))
    return tuple(examples)


def _parse_technique(technique_id: str, raw: object) -> CraftTechnique:
    data = as_dict(raw)
    genre_fit = as_dict(data.get("genre_fit"))
    return CraftTechnique(
        technique_id=technique_id,
        display_name=as_str(data.get("display_name"), default=technique_id),
        category=as_str(data.get("category")),
        principle=as_str(data.get("principle")),
        structure=as_str(data.get("structure")),
        purple_risk=as_str(data.get("purple_risk")),
        genre_good=as_str_tuple(genre_fit.get("good")),
        genre_careful=as_str_tuple(genre_fit.get("careful")),
        genre_avoid=as_str_tuple(genre_fit.get("avoid")),
        examples=_parse_examples(data.get("micro_examples")),
    )


def _parse_purple_guard(raw: object) -> tuple[PurpleGuardMove, ...]:
    data = as_dict(raw)
    moves_raw = data.get("banned_moves")
    moves: list[PurpleGuardMove] = []
    if isinstance(moves_raw, (list, tuple)):
        for entry in moves_raw:
            entry_data = as_dict(entry)
            move_id = as_str(entry_data.get("id"))
            bad = as_str(entry_data.get("bad"))
            if not (move_id or bad):
                continue
            moves.append(
                PurpleGuardMove(
                    move_id=move_id,
                    bad=bad,
                    why=as_str(entry_data.get("why")),
                    fix=as_str(entry_data.get("fix")),
                )
            )
    return tuple(moves)


def _parse_genre_emphasis(raw: object) -> dict[str, tuple[str, ...]]:
    data = as_dict(raw)
    emphasis: dict[str, tuple[str, ...]] = {}
    for key, value in data.items():
        key_str = as_str(key)
        ids = as_str_tuple(value)
        if key_str and ids:
            emphasis[key_str] = ids
    return emphasis


@lru_cache(maxsize=1)
def load_prose_craft_techniques() -> ProseCraftConfig:
    """Return the typed view over ``prose_craft_techniques.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    techniques_raw = as_dict(raw.get("techniques"))
    techniques: dict[str, CraftTechnique] = {}
    for technique_id, technique_raw in techniques_raw.items():
        canonical = as_str(technique_id)
        if not canonical:
            continue
        techniques[canonical] = _parse_technique(canonical, technique_raw)

    policy = as_dict(raw.get("injection_policy"))
    return ProseCraftConfig(
        version=as_str(raw.get("version")),
        techniques=techniques,
        purple_guard=_parse_purple_guard(raw.get("purple_prose_guard")),
        genre_emphasis=_parse_genre_emphasis(raw.get("genre_emphasis")),
        techniques_per_scene=max(1, as_int(policy.get("techniques_per_scene"), default=3)),
        example_per_technique=max(1, as_int(policy.get("example_per_technique"), default=1)),
        rotate_by_chapter=bool(policy.get("rotate_by_chapter", True)),
        position_hint=as_str(policy.get("position_hint")),
        hard_rule=as_str(policy.get("hard_rule")),
    )


def get_craft_technique(technique_id: str) -> CraftTechnique | None:
    """Look up one technique."""

    if not technique_id:
        return None
    return load_prose_craft_techniques().techniques.get(technique_id)


# ---------------------------------------------------------------------------
# Genre routing + rotation
# ---------------------------------------------------------------------------


def resolve_genre_emphasis_key(
    genre_terms: tuple[str, ...] | list[str],
    config: ProseCraftConfig | None = None,
) -> str:
    """Pick the ``genre_emphasis`` key best matching the supplied genre terms.

    Matching is substring-based and order-stable: the first emphasis key (other
    than ``default``) that appears inside any genre term wins. Falls back to
    ``default`` — so a modern genre never accidentally inherits 古风 imagery.
    """

    config = config or load_prose_craft_techniques()
    terms = tuple(as_str(term) for term in genre_terms if as_str(term))
    if not terms:
        return _DEFAULT_EMPHASIS_KEY
    for key in config.genre_emphasis:
        if key == _DEFAULT_EMPHASIS_KEY:
            continue
        if any(key in term for term in terms):
            return key
    return _DEFAULT_EMPHASIS_KEY


def select_techniques(
    *,
    genre_terms: tuple[str, ...] | list[str] = (),
    chapter_number: int = 1,
    config: ProseCraftConfig | None = None,
) -> tuple[CraftTechnique, ...]:
    """Return the genre-appropriate technique subset for this chapter.

    The emphasis list is a *superset*; we expose ``techniques_per_scene`` of
    them, rotating the window by chapter number so consecutive chapters do not
    keep recommending the same skeletons (an anti-homogenisation guard).
    """

    config = config or load_prose_craft_techniques()
    key = resolve_genre_emphasis_key(genre_terms, config)
    ordered_ids = config.genre_emphasis.get(key) or config.genre_emphasis.get(
        _DEFAULT_EMPHASIS_KEY, ()
    )
    resolved = [config.techniques[tid] for tid in ordered_ids if tid in config.techniques]
    if not resolved:
        return ()

    want = min(config.techniques_per_scene, len(resolved))
    if config.rotate_by_chapter and len(resolved) > want:
        start = (max(1, int(chapter_number)) - 1) % len(resolved)
        window = [resolved[(start + offset) % len(resolved)] for offset in range(want)]
        return tuple(window)
    return tuple(resolved[:want])


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def _render_purple_guard_line(config: ProseCraftConfig) -> str:
    if not config.purple_guard:
        return ""
    bads = "、".join(move.bad for move in config.purple_guard if move.bad)
    if not bads:
        return ""
    return f"⚠ 文采≠辞藻堆砌，忌：{bads}。"


def render_prose_craft_block(
    *,
    genre_terms: tuple[str, ...] | list[str] = (),
    chapter_number: int = 1,
) -> str:
    """Render the compact, writer-facing 文采 craft fragment.

    Returns ``""`` when the config has no techniques, so callers can treat it as
    an optional section that degrades gracefully.
    """

    config = load_prose_craft_techniques()
    techniques = select_techniques(
        genre_terms=genre_terms, chapter_number=chapter_number, config=config
    )
    if not techniques:
        return ""

    terms = tuple(as_str(term) for term in genre_terms if as_str(term))
    lines: list[str] = [
        "【文采技法 · 金句/签名段怎么写（可选 soft，不硬性、不是每句都要）】",
        "本场若要落一个「值得读者截图摘抄」的金句/签名段，可任选下列骨架之一；"
        "正文主体仍走 show-don't-tell，不要全篇都这样写：",
        "【用量硬上限】全场技法句合计≤2处，通感/陌生化≤1处，且必须贴当下事件；"
        "严禁感官动词错配的怪喻（香味撞上来/蒸汽舀进脑仁）——写不出贴切的就写平实白话。",
    ]
    for technique in techniques:
        example = technique.example_for(terms)
        line = f"- {technique.display_name}：{technique.structure}"
        if example and example.line:
            line += f"｜例：{example.line}"
        lines.append(line)
    if config.position_hint:
        lines.append(f"位置：{config.position_hint}")
    guard = _render_purple_guard_line(config)
    if guard:
        lines.append(guard)
    return "\n".join(lines)


__all__ = [
    "CraftExample",
    "CraftTechnique",
    "ProseCraftConfig",
    "PurpleGuardMove",
    "get_craft_technique",
    "load_prose_craft_techniques",
    "render_prose_craft_block",
    "resolve_genre_emphasis_key",
    "select_techniques",
]
