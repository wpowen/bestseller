"""Stage-aware methodology compiler for prompt injection.

The compiler reuses existing YAML loaders/renderers where they exist and
degrades to an empty block when optional configuration is missing. It is
intentionally text-first: callers can prepend ``CompiledMethodology.text`` to
their existing prompt without adopting a new prompt object model.
"""

from __future__ import annotations

# ruff: noqa: RUF003
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from bestseller.services.litstyle_prose import render_prose_lever_framing
from bestseller.services.methodology_book_selector import render_book_methodology_block
from bestseller.services.methodology_bridge import render_phase_block
from bestseller.services.prompt_packs import (
    PromptPack,
    get_prompt_pack,
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
)
from bestseller.services.quality_levers._loader import load_yaml
from bestseller.services.quality_levers.chapter_position_profiles import (
    render_chapter_position_block,
)
from bestseller.services.quality_levers.character_embodiment import (
    extract_embodiment,
    render_embodiment_block,
)
from bestseller.services.quality_levers.cinematic_pov import (
    render_cinematic_pov_block,
)
from bestseller.services.quality_levers.emotion_choreography import (
    render_emotion_choreography_block,
)
from bestseller.services.quality_levers.imagery_system import (
    extract_imagery_system,
    render_imagery_system_block,
)
from bestseller.services.quality_levers.information_choreography import (
    render_information_choreography_block,
)
from bestseller.services.quality_levers.material_concreteness import (
    render_concretization_directive,
)
from bestseller.services.quality_levers.prose_craft_techniques import (
    render_prose_craft_block,
)
from bestseller.services.quality_levers.prose_prompt_fusion import (
    render_prose_prompt_fusion_block,
)
from bestseller.services.quality_levers.prose_style_anchors import (
    render_style_anchor_block,
)
from bestseller.services.quality_levers.rhythm_engineering import render_rhythm_block
from bestseller.services.quality_levers.scene_grounding import (
    render_scene_grounding_block,
)


class MethodologyStage(StrEnum):
    CONCEPTION = "conception"
    OUTLINE_BOOK = "outline_book"
    OUTLINE_VOLUME = "outline_volume"
    OUTLINE_CHAPTER = "outline_chapter"
    PROSE_SCENE = "prose_scene"
    REVIEW = "review"


class ChapterPosition(StrEnum):
    OPENING = "opening"
    EARLY = "early"
    MIDGAME = "midgame"
    CLIMAX = "climax"
    ENDGAME = "endgame"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CompiledMethodology:
    text: str
    used_sources: tuple[str, ...]
    estimated_tokens: int


@dataclass(frozen=True)
class _Section:
    key: str
    text: str
    source: str


SECTION_PRIORITY: dict[MethodologyStage, tuple[str, ...]] = {
    MethodologyStage.CONCEPTION: (
        "prompt_pack_global_rules",
        "prompt_pack_profile_overrides",
        "writing_methodology_conception",
        "book_methodology_current",
        "emotion_choreography_summary",
        "public_emotion_core",
    ),
    MethodologyStage.OUTLINE_BOOK: (
        "prompt_pack_global_rules",
        "writing_methodology_planner",
        "book_methodology_current",
        "emotion_choreography_summary",
        "rhythm_engineering_framework",
        "information_choreography_framework",
    ),
    MethodologyStage.OUTLINE_VOLUME: (
        "writing_methodology_volume",
        "book_methodology_current",
        "emotion_choreography_summary",
        "rhythm_engineering_framework",
        "information_choreography_framework",
        "chapter_position_current",
    ),
    MethodologyStage.OUTLINE_CHAPTER: (
        "prompt_pack_chapter_review",
        "writing_methodology_prewrite",
        "book_methodology_current",
        "emotion_choreography_current",
        "rhythm_engineering_current",
        "information_choreography_current",
        "chapter_position_current",
    ),
    MethodologyStage.PROSE_SCENE: (
        # Proven #1 prose lever (单人入戏): raw per-scene first-person interiority.
        # Highest priority so it is never starved by the token budget; it is small.
        "character_embodiment_current",
        # 镜头化·体验优先 (always-on, ~50% of文风质量): write experience not info,
        # real-time camera, effective reaction-shots. Top always-on block so the
        # budget never starves it (embodiment above is data-dependent / often empty).
        "cinematic_pov_current",
        # 2026-06 prose prompt arena winner blend: stakes-clock + question-chain
        # + embodiment + concrete materials + payoff feedback + signature image.
        # It turns abstract methodology into page actions, so it belongs before
        # optional craft/imagery flourish and before abstract bridge sections.
        "prompt_pack_scene_writer",
        "prose_prompt_fusion_current",
        "writing_methodology_scene",
        "book_methodology_current",
        "prose_style_anchors",
        "prose_lever_framing",
        "material_concretization_current",
        "scene_grounding_current",
        "prose_craft_techniques",
        "imagery_system_current",
        "public_emotion_role_tags",
        "emotion_choreography_current",
        "rhythm_engineering_current",
        "information_choreography_current",
        "chapter_position_current",
    ),
    MethodologyStage.REVIEW: (
        "prompt_pack_chapter_review",
        "writing_methodology_review",
        "book_methodology_current",
        "prose_style_anchors",
    ),
}


# 爽文融合 (enable_shuangwen_fusion) PROSE_SCENE ordering.
#
# Same sections as the default PROSE_SCENE priority — nothing is removed, so
# 文采 fully coexists — but the 爽点 engines (弹簧法情绪压缩/释放、节奏、信息
# 节奏、章节爽点) are lifted ABOVE the literary-flourish levers (留白框架 / 金句 /
# 意象). Reason: sections are filled greedily in rank order until the token
# budget is exhausted, so whatever sits last is the first to be starved. In the
# default order the 爽点 engines sit last (ranks 11–14) and get dropped under
# runtime budget pressure, which is why output reads literary instead of 爽.
#
# The anti-作文 grounding levers (物料具体化 / 镜头锚定) stay high — concrete,
# camera-grounded action is what makes a 爽点 land, so they precede the spring.
_PROSE_SCENE_SHUANGWEN_PRIORITY: tuple[str, ...] = (
    "character_embodiment_current",
    "cinematic_pov_current",
    "prompt_pack_scene_writer",
    "prose_prompt_fusion_current",
    "writing_methodology_scene",
    "book_methodology_current",
    "prose_style_anchors",
    "material_concretization_current",  # 具体化 = 爽点落地，留高位
    "scene_grounding_current",  # 镜头锚定 = 爽点可视，留高位
    "public_emotion_role_tags",
    "emotion_choreography_current",  # 弹簧法 · 爽点核心 → 顶到文采润色之前
    "rhythm_engineering_current",
    "information_choreography_current",
    "chapter_position_current",
    "prose_lever_framing",  # 留白/文采框架 → 降到爽点之后
    "prose_craft_techniques",  # 金句 → 降后
    "imagery_system_current",  # 意象 → 降后
)


_EMPTY = CompiledMethodology(text="", used_sources=(), estimated_tokens=0)


def compile_methodology(
    *,
    stage: MethodologyStage,
    prompt_pack_key: str | None,
    language: str = "zh-CN",
    chapter_no: int | None = None,
    chapter_position: ChapterPosition | None = None,
    token_budget: int = 1500,
    story_bible: Mapping[str, Any] | None = None,
    shuangwen_mode: bool = False,
    include_writing_methodology_bridge: bool = True,
) -> CompiledMethodology:
    """Compile a stage-aware methodology block.

    ``include_writing_methodology_bridge`` (default True) controls the
    ``writing_methodology · <phase>`` bridge section (the abstract
    emotion_engineering / hook_design / core_loop / conflict_stakes /
    pacing_guidance / … "方法论说教" subsections). The prompt-ablation ladder
    (2026-06-10, 仙侠 ch1 n=4 + 探案 ch87 n=3) found this C1-rules group is
    net-zero-to-negative for prose quality across both genres while costing
    ~7k chars — and all related gates (show_dont_tell / methodology_framework /
    opening_three_function) are soft, so dropping it can't trigger a
    reject→repair loop. The scene-writer path passes False to free the
    PROSE_SCENE token budget for the A/B-proven craft levers (embodiment /
    物料具体化 / 金句 / 意象) that this说教 was starving. Outline/conception/
    review callers keep True (those phases use the planner-phase bridge, which
    is where methodology belongs — "bake into the plan, not the prose").

    English paths are intentionally left unchanged for Sprint 1. Missing YAML
    or prompt packs are treated as absent sections, never as fatal errors.

    ``story_bible`` is optional and backward-compatible: when a book has a
    designed ``imagery_system`` in its bible, PROSE_SCENE renders a soft
    per-chapter imagery-recall block. Absent → that block is simply skipped.
    """

    if str(language or "").lower().startswith("en") or token_budget <= 0:
        return _EMPTY

    try:
        stage = MethodologyStage(stage)
    except ValueError:
        return _EMPTY

    pack = get_prompt_pack(prompt_pack_key)
    chapter_number = max(int(chapter_no or 1), 1)
    position = chapter_position or _infer_position(chapter_number)
    sections = _sections_for_stage(
        stage=stage,
        pack=pack,
        prompt_pack_key=prompt_pack_key,
        chapter_number=chapter_number,
        chapter_position=position,
        language=language,
        story_bible=story_bible,
        include_writing_methodology_bridge=include_writing_methodology_bridge,
    )
    if not sections:
        return _EMPTY

    ordered = _prioritize_sections(
        _priority_for_stage(stage, shuangwen_mode=shuangwen_mode), sections
    )
    selected: list[_Section] = []
    used = 0
    for section in ordered:
        tokens = _estimate_tokens(section.text, language=language)
        if tokens <= 0:
            continue
        if used + tokens <= token_budget:
            selected.append(section)
            used += tokens
            continue
        if not selected:
            truncated = _truncate_to_budget(section.text, token_budget, language=language)
            if truncated:
                selected.append(_Section(section.key, truncated, section.source))
                used = _estimate_tokens(truncated, language=language)
        break

    if not selected:
        return _EMPTY

    heading = _heading_for_stage(stage)
    body = "\n\n".join(section.text.strip() for section in selected if section.text.strip())
    sources = tuple(dict.fromkeys(section.source for section in selected if section.source))
    return CompiledMethodology(
        text=f"{heading}\n{body}",
        used_sources=sources,
        estimated_tokens=min(_estimate_tokens(body, language=language), token_budget),
    )


def _sections_for_stage(
    *,
    stage: MethodologyStage,
    pack: PromptPack | None,
    prompt_pack_key: str | None,
    chapter_number: int,
    chapter_position: ChapterPosition,
    language: str,
    story_bible: Mapping[str, Any] | None = None,
    include_writing_methodology_bridge: bool = True,
) -> list[_Section]:
    pack_source = f"prompt_packs/{prompt_pack_key}.yaml" if prompt_pack_key else "prompt_packs"
    sections: list[_Section] = []

    if stage in {MethodologyStage.CONCEPTION, MethodologyStage.OUTLINE_BOOK}:
        pack_block = render_prompt_pack_prompt_block(pack)
        if pack_block:
            sections.append(_Section("prompt_pack_global_rules", pack_block, pack_source))
    if stage is MethodologyStage.CONCEPTION and pack is not None and pack.writing_profile_overrides:
        sections.append(
            _Section(
                "prompt_pack_profile_overrides",
                _format_mapping(
                    "【writing_profile_overrides 摘要】",
                    pack.writing_profile_overrides,
                ),
                pack_source,
            )
        )

    fragment_key = {
        MethodologyStage.OUTLINE_CHAPTER: "chapter_review",
        MethodologyStage.PROSE_SCENE: "scene_writer",
        MethodologyStage.REVIEW: "chapter_review",
    }.get(stage)
    if fragment_key:
        fragment = render_prompt_pack_fragment(pack, fragment_key)
        if not fragment and fragment_key == "scene_writer":
            fragment = render_prompt_pack_fragment(pack, "segment_writer")
        if fragment:
            sections.append(
                _Section(
                    f"prompt_pack_{fragment_key}",
                    f"【prompt_pack.{fragment_key}】\n{fragment}",
                    pack_source,
                )
            )

    phase = {
        MethodologyStage.CONCEPTION: "planner",
        MethodologyStage.OUTLINE_BOOK: "planner",
        MethodologyStage.OUTLINE_VOLUME: "planner",
        MethodologyStage.OUTLINE_CHAPTER: "prewrite",
        MethodologyStage.PROSE_SCENE: "scene",
        MethodologyStage.REVIEW: "review",
    }[stage]
    bridge_block = (
        render_phase_block(pack, phase=phase, heading=f"writing_methodology · {phase}")
        if include_writing_methodology_bridge
        else ""
    )
    if bridge_block:
        sections.append(
            _Section(
                f"writing_methodology_{phase}",
                bridge_block,
                "writing_methodology.yaml",
            )
        )

    book_block = _safe(
        render_book_methodology_block,
        stage=stage.value,
        scope=_book_methodology_scope(stage),
        language="zh-CN",
        chapter_no=chapter_number,
        chapter_position=chapter_position.value,
        max_cards=4,
        token_budget=700,
    )
    if book_block:
        sections.append(
            _Section(
                "book_methodology_current",
                book_block,
                "methodology_books/books_core_selector",
            )
        )

    if stage in {
        MethodologyStage.CONCEPTION,
        MethodologyStage.OUTLINE_BOOK,
        MethodologyStage.OUTLINE_VOLUME,
        MethodologyStage.OUTLINE_CHAPTER,
        MethodologyStage.PROSE_SCENE,
    }:
        _append_block(
            sections,
            key="emotion_choreography_current",
            text=_safe(render_emotion_choreography_block),
            source="emotion_choreography.yaml",
        )
    if stage in {
        MethodologyStage.OUTLINE_BOOK,
        MethodologyStage.OUTLINE_VOLUME,
        MethodologyStage.OUTLINE_CHAPTER,
        MethodologyStage.PROSE_SCENE,
    }:
        _append_block(
            sections,
            key="rhythm_engineering_current",
            text=_safe(render_rhythm_block),
            source="rhythm_engineering.yaml",
        )
        _append_block(
            sections,
            key="information_choreography_current",
            text=_safe(render_information_choreography_block, chapter_number=chapter_number),
            source="information_choreography.yaml",
        )
    if stage in {MethodologyStage.PROSE_SCENE, MethodologyStage.REVIEW}:
        _append_block(
            sections,
            key="prose_style_anchors",
            text=_safe(render_style_anchor_block, anchor_ids=("anti_ai_voice",)),
            source="prose_style_anchors.yaml",
        )
    if stage is MethodologyStage.PROSE_SCENE:
        # Character embodiment (单人入戏) — the proven #1 prose lever (3 A/B exps,
        # two judge families): raw first-person interiority for THIS scene, generated
        # at draft time and threaded through story_bible. Rendered VERBATIM (the
        # group-sim arm proved any summary hop re-abstracts and erases the gain).
        # No-ops when the scene context has no embodiment (graceful degrade).
        embodiment_text = extract_embodiment(story_bible)
        if embodiment_text:
            _append_block(
                sections,
                key="character_embodiment_current",
                text=_safe(render_embodiment_block, interiority=embodiment_text),
                source="character_embodiment.py",
            )
        # 镜头化·体验优先 — the always-on #1 anti-AI prose discipline. Placed
        # before every other always-on block so the budget never starves it.
        # Soft: shapes how the writer drafts (experience not information, real-time
        # camera, legible reaction-shots); never a gate.
        _append_block(
            sections,
            key="cinematic_pov_current",
            text=_safe(render_cinematic_pov_block, language="zh"),
            source="cinematic_pov.yaml",
        )
        # Arena-proven fusion block. Unlike the abstract methodology bridge, this
        # is already operationalized as concrete page actions, so it remains on
        # in lean writer mode.
        _append_block(
            sections,
            key="prose_prompt_fusion_current",
            text=_safe(
                render_prose_prompt_fusion_block,
                language=language,
                position=chapter_position.value,
            ),
            source="prose_prompt_arena_fusion",
        )
        # Framing FIRST (anti-regression): the writer-levers A/B showed a budget
        # writer reads the stacked 留白/克制 guards as "write less" and cuts ~30%
        # length → lower文采. This总则 reframes: 文采=更具体不更短; pick 1-2 techniques;
        # 留白 deletes author-narration, not plot. Cheap + high-priority.
        _append_block(
            sections,
            key="prose_lever_framing",
            text=_safe(render_prose_lever_framing, language="zh"),
            source="litstyle_prose.py",
        )
        # Layer 3 — material concretization. A/B proved abstract §default material
        # is the dominant cause of essay-like prose; this directive tells the
        # writer to instantiate abstract mechanism material into the book's
        # concrete people/objects/actions before writing. Soft, genre-neutral.
        _append_block(
            sections,
            key="material_concretization_current",
            text=_safe(
                render_concretization_directive,
                genre_terms=_pack_genre_terms(pack),
                chapter_number=chapter_number,
            ),
            source="material_concreteness.yaml",
        )
        # Whole-chapter camera discipline (定场/转场/设定外显/专名节流). Soft only:
        # treats the "像作文" failure mode — author summary, floating jump-cuts,
        # name floods. Orthogonal to visual_writing (单段) / prose_craft (单句).
        _append_block(
            sections,
            key="scene_grounding_current",
            text=_safe(
                render_scene_grounding_block,
                genre_terms=_pack_genre_terms(pack),
                chapter_number=chapter_number,
            ),
            source="scene_grounding.yaml",
        )
        # Genre-aware 文采 (golden-line) craft. Soft only: this is *how to write*
        # an optional signature line, never a gate. Genre terms route modern
        # genres away from 古风 imagery (see prose_craft_techniques.yaml).
        _append_block(
            sections,
            key="prose_craft_techniques",
            text=_safe(
                render_prose_craft_block,
                genre_terms=_pack_genre_terms(pack),
                chapter_number=chapter_number,
            ),
            source="prose_craft_techniques.yaml",
        )
        # Book-level imagery system (LitStyle imagery_system dimension). Soft recall
        # of THIS book's 2-3 designed core images, telling the writer to advance an
        # image's meaning a step when it recurs. No-ops when the bible has no
        # designed imagery_system (graceful degrade). Genre-routed anti-purple.
        imagery_artifact = extract_imagery_system(story_bible)
        if imagery_artifact:
            _append_block(
                sections,
                key="imagery_system_current",
                text=_safe(
                    render_imagery_system_block,
                    artifact=imagery_artifact,
                    genre_terms=_pack_genre_terms(pack),
                    chapter_number=chapter_number,
                ),
                source="imagery_system.yaml",
            )
    if stage in {
        MethodologyStage.OUTLINE_VOLUME,
        MethodologyStage.OUTLINE_CHAPTER,
        MethodologyStage.PROSE_SCENE,
    }:
        positions = _position_profile_ids(chapter_position)
        _append_block(
            sections,
            key="chapter_position_current",
            text=_safe(
                render_chapter_position_block,
                positions=positions,
                chapter_number=chapter_number,
            ),
            source="chapter_position_profiles.yaml",
        )
    if stage in {MethodologyStage.CONCEPTION, MethodologyStage.PROSE_SCENE}:
        public_emotion = _public_emotion_block(stage)
        if public_emotion:
            sections.append(
                _Section(
                    "public_emotion_core"
                    if stage is MethodologyStage.CONCEPTION
                    else "public_emotion_role_tags",
                    public_emotion,
                    "public_emotion_methodology.yaml",
                )
            )

    return sections


def _priority_for_stage(
    stage: MethodologyStage, *, shuangwen_mode: bool
) -> tuple[str, ...]:
    """Section ordering for a stage; 爽文 mode reprioritises only PROSE_SCENE."""

    if shuangwen_mode and stage == MethodologyStage.PROSE_SCENE:
        return _PROSE_SCENE_SHUANGWEN_PRIORITY
    return SECTION_PRIORITY.get(stage, ())


def _prioritize_sections(
    priority: tuple[str, ...], sections: list[_Section]
) -> list[_Section]:
    rank = {key: index for index, key in enumerate(priority)}
    return sorted(sections, key=lambda item: rank.get(item.key, len(priority)))


def _append_block(sections: list[_Section], *, key: str, text: str, source: str) -> None:
    if text.strip():
        sections.append(_Section(key, text.strip(), source))


def _pack_genre_terms(pack: PromptPack | None) -> tuple[str, ...]:
    """Collect genre/tag/key/name strings from a prompt pack for craft routing.

    The 文采 renderer matches these (substring) against ``genre_emphasis`` keys,
    so a wider net (genres + tags + pack key + display name) gives the best
    chance of routing to the right technique set; it falls back to ``default``.
    """

    if pack is None:
        return ()
    terms: list[str] = []
    for value in (*(pack.genres or ()), *(pack.tags or ())):
        text = str(value).strip()
        if text:
            terms.append(text)
    for attr in ("key", "name"):
        text = str(getattr(pack, attr, "") or "").strip()
        if text:
            terms.append(text)
    return tuple(dict.fromkeys(terms))


def _safe(func: Callable[..., object], /, **kwargs: object) -> str:
    try:
        return str(func(**kwargs) if kwargs else func() or "").strip()
    except Exception:
        return ""


def _public_emotion_block(stage: MethodologyStage) -> str:
    try:
        raw = load_yaml("public_emotion_methodology.yaml")
    except Exception:
        return ""
    if not isinstance(raw, dict) or not raw:
        return ""
    keys = (
        ("core_loop", "reader_empathy_loop", "role_tags", "contrast_rules")
        if stage is MethodologyStage.CONCEPTION
        else ("role_tags", "contrast_rules", "emotion_buttons", "anti_patterns")
    )
    subset = {key: raw.get(key) for key in keys if raw.get(key)}
    if not subset:
        subset = dict(list(raw.items())[:4])
    return _format_mapping("【public_emotion_methodology · 公众情绪方法】", subset)


def _format_mapping(title: str, data: dict[str, Any]) -> str:
    lines = [title]
    for key, value in data.items():
        if isinstance(value, dict):
            preview = ";".join(f"{k}={v}" for k, v in list(value.items())[:6])
        elif isinstance(value, list):
            preview = ";".join(str(item) for item in value[:8])
        else:
            preview = str(value)
        if preview.strip():
            lines.append(f"- {key}: {preview}")
    return "\n".join(lines)


def _position_profile_ids(position: ChapterPosition) -> tuple[str, ...]:
    mapping = {
        ChapterPosition.OPENING: ("opening", "golden_three", "first_three_chapters"),
        ChapterPosition.EARLY: ("early", "early_growth"),
        ChapterPosition.MIDGAME: ("midgame", "middle_chapter"),
        ChapterPosition.CLIMAX: ("climax", "major_twist_chapter"),
        ChapterPosition.ENDGAME: ("endgame", "finale"),
        ChapterPosition.UNKNOWN: (),
    }
    return mapping.get(position, ())


def _infer_position(chapter_number: int) -> ChapterPosition:
    if chapter_number <= 3:
        return ChapterPosition.OPENING
    if chapter_number <= 20:
        return ChapterPosition.EARLY
    return ChapterPosition.MIDGAME


def _heading_for_stage(stage: MethodologyStage) -> str:
    names = {
        MethodologyStage.CONCEPTION: "立项",
        MethodologyStage.OUTLINE_BOOK: "全书大纲",
        MethodologyStage.OUTLINE_VOLUME: "卷规划",
        MethodologyStage.OUTLINE_CHAPTER: "章纲",
        MethodologyStage.PROSE_SCENE: "正文场景",
        MethodologyStage.REVIEW: "评审",
    }
    return f"【题材方法论·{names[stage]}】"


def _book_methodology_scope(stage: MethodologyStage) -> str:
    mapping = {
        MethodologyStage.CONCEPTION: "book",
        MethodologyStage.OUTLINE_BOOK: "book",
        MethodologyStage.OUTLINE_VOLUME: "volume",
        MethodologyStage.OUTLINE_CHAPTER: "chapter",
        MethodologyStage.PROSE_SCENE: "scene",
        MethodologyStage.REVIEW: "chapter",
    }
    return mapping[stage]


def _estimate_tokens(text: str, *, language: str) -> int:
    divisor = 4.0 if str(language or "").lower().startswith("en") else 2.5
    return max(0, int(len(text) / divisor))


def _truncate_to_budget(text: str, token_budget: int, *, language: str) -> str:
    divisor = 4.0 if str(language or "").lower().startswith("en") else 2.5
    char_budget = max(0, int(token_budget * divisor * 0.9))
    if char_budget <= 0:
        return ""
    return text[:char_budget].rstrip()


__all__ = [
    "ChapterPosition",
    "CompiledMethodology",
    "MethodologyStage",
    "compile_methodology",
]
