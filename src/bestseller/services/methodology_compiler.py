"""Stage-aware methodology compiler for prompt injection.

The compiler reuses existing YAML loaders/renderers where they exist and
degrades to an empty block when optional configuration is missing. It is
intentionally text-first: callers can prepend ``CompiledMethodology.text`` to
their existing prompt without adopting a new prompt object model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

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
from bestseller.services.quality_levers.emotion_choreography import (
    render_emotion_choreography_block,
)
from bestseller.services.quality_levers.information_choreography import (
    render_information_choreography_block,
)
from bestseller.services.quality_levers.prose_style_anchors import (
    render_style_anchor_block,
)
from bestseller.services.quality_levers.rhythm_engineering import render_rhythm_block


class MethodologyStage(str, Enum):
    CONCEPTION = "conception"
    OUTLINE_BOOK = "outline_book"
    OUTLINE_VOLUME = "outline_volume"
    OUTLINE_CHAPTER = "outline_chapter"
    PROSE_SCENE = "prose_scene"
    REVIEW = "review"


class ChapterPosition(str, Enum):
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
        "emotion_choreography_summary",
        "public_emotion_core",
    ),
    MethodologyStage.OUTLINE_BOOK: (
        "prompt_pack_global_rules",
        "writing_methodology_planner",
        "emotion_choreography_summary",
        "rhythm_engineering_framework",
        "information_choreography_framework",
    ),
    MethodologyStage.OUTLINE_VOLUME: (
        "writing_methodology_volume",
        "emotion_choreography_summary",
        "rhythm_engineering_framework",
        "information_choreography_framework",
        "chapter_position_current",
    ),
    MethodologyStage.OUTLINE_CHAPTER: (
        "prompt_pack_chapter_review",
        "writing_methodology_prewrite",
        "emotion_choreography_current",
        "rhythm_engineering_current",
        "information_choreography_current",
        "chapter_position_current",
    ),
    MethodologyStage.PROSE_SCENE: (
        "prompt_pack_scene_writer",
        "writing_methodology_scene",
        "prose_style_anchors",
        "public_emotion_role_tags",
        "emotion_choreography_current",
        "rhythm_engineering_current",
        "information_choreography_current",
        "chapter_position_current",
    ),
    MethodologyStage.REVIEW: (
        "prompt_pack_chapter_review",
        "writing_methodology_review",
        "prose_style_anchors",
    ),
}


_EMPTY = CompiledMethodology(text="", used_sources=(), estimated_tokens=0)


def compile_methodology(
    *,
    stage: MethodologyStage,
    prompt_pack_key: str | None,
    language: str = "zh-CN",
    chapter_no: int | None = None,
    chapter_position: ChapterPosition | None = None,
    token_budget: int = 1500,
) -> CompiledMethodology:
    """Compile a stage-aware methodology block.

    English paths are intentionally left unchanged for Sprint 1. Missing YAML
    or prompt packs are treated as absent sections, never as fatal errors.
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
    )
    if not sections:
        return _EMPTY

    ordered = _prioritize_sections(stage, sections)
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
                _format_mapping("【writing_profile_overrides 摘要】", pack.writing_profile_overrides),
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
                _Section(f"prompt_pack_{fragment_key}", f"【prompt_pack.{fragment_key}】\n{fragment}", pack_source)
            )

    phase = {
        MethodologyStage.CONCEPTION: "planner",
        MethodologyStage.OUTLINE_BOOK: "planner",
        MethodologyStage.OUTLINE_VOLUME: "planner",
        MethodologyStage.OUTLINE_CHAPTER: "prewrite",
        MethodologyStage.PROSE_SCENE: "scene",
        MethodologyStage.REVIEW: "review",
    }[stage]
    bridge_block = render_phase_block(pack, phase=phase, heading=f"writing_methodology · {phase}")
    if bridge_block:
        sections.append(_Section(f"writing_methodology_{phase}", bridge_block, "writing_methodology.yaml"))

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


def _prioritize_sections(stage: MethodologyStage, sections: list[_Section]) -> list[_Section]:
    priority = SECTION_PRIORITY.get(stage, ())
    rank = {key: index for index, key in enumerate(priority)}
    return sorted(sections, key=lambda item: rank.get(item.key, len(priority)))


def _append_block(sections: list[_Section], *, key: str, text: str, source: str) -> None:
    if text.strip():
        sections.append(_Section(key, text.strip(), source))


def _safe(func: Any, /, **kwargs: Any) -> str:
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
            preview = "；".join(f"{k}={v}" for k, v in list(value.items())[:6])
        elif isinstance(value, list):
            preview = "；".join(str(item) for item in value[:8])
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
