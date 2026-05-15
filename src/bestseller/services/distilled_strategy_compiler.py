# ruff: noqa: RUF001
"""Compile anonymous distillation aggregates into project strategy cards.

``distilled_design_reference`` renders aggregate assets directly into planner
prompt text.  This module is the next layer up: it selects a smaller set of
mechanisms, assigns design roles, records maturity, and describes how each
mechanism must be transformed for the current project before it can influence
planning or drafting.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bestseller.services.distillation_assets import calculate_aggregate_maturity_score
from bestseller.services.distilled_design_reference import load_distilled_design_reference

DesignRole = Literal[
    "series_engine",
    "world_pressure",
    "character_pressure",
    "volume_escalation",
    "chapter_rhythm",
    "craft_control",
    "anti_cliche",
]

_ROLE_ORDER: tuple[DesignRole, ...] = (
    "series_engine",
    "world_pressure",
    "character_pressure",
    "volume_escalation",
    "chapter_rhythm",
    "craft_control",
    "anti_cliche",
)


class SelectedMechanism(BaseModel, frozen=True):
    """One aggregate mechanism selected for this project."""

    model_config = ConfigDict(extra="ignore")

    mechanism_id: str = Field(min_length=1)
    source_confidence: float = Field(ge=0.0, le=1.0)
    design_role: DesignRole
    adaptation_instruction: str = Field(min_length=1)
    required_project_specific_binding: str = Field(min_length=1)
    failure_mode: str = Field(min_length=1)


class DistilledStrategyCard(BaseModel, frozen=True):
    """Project-facing strategy distilled from mature-fiction aggregate assets."""

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    aggregate_key: str = Field(min_length=1)
    maturity_score: float = Field(ge=0.0, le=1.0)
    maturity_status: str = "unsafe"
    source_count: int = 0
    selected_mechanisms: list[SelectedMechanism] = Field(default_factory=list)
    required_state_variables: list[str] = Field(default_factory=list)
    required_change_vectors: list[str] = Field(default_factory=list)
    reader_reward_mix: list[str] = Field(default_factory=list)
    world_design_paths: list[str] = Field(default_factory=list)
    character_design_paths: list[str] = Field(default_factory=list)
    volume_design_paths: list[str] = Field(default_factory=list)
    chapter_execution_patterns: list[str] = Field(default_factory=list)
    craft_controls: list[str] = Field(default_factory=list)
    anti_copy_boundaries: list[str] = Field(default_factory=list)
    world_mechanism_bindings: list[dict[str, Any]] = Field(default_factory=list)
    worldview_bindings: dict[str, Any] = Field(default_factory=dict)
    transformation_requirements: list[str] = Field(default_factory=list)
    plan_consumption_checks: list[str] = Field(default_factory=list)


def _as_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _truncate(value: object, limit: int = 140) -> str:
    text = _text(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _string_items(value: object, *, limit: int = 8) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        text = _text(item)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _dedupe(values: Sequence[object], *, limit: int = 12) -> list[str]:
    out: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _confidence(row: Mapping[str, Any]) -> float:
    for key in ("max_confidence", "confidence"):
        try:
            value = float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
        return max(0.0, min(value, 1.0))
    return 0.0


def _role_for_row(row: Mapping[str, Any]) -> DesignRole:
    candidate_type = _text(row.get("candidate_type") or row.get("dimension")).lower()
    promotion_target = _text(row.get("promotion_target")).lower()
    haystack = f"{candidate_type} {promotion_target}"
    if "anti_cliche" in haystack or "anti-cliche" in haystack or "forbid" in haystack:
        return "anti_cliche"
    if any(token in haystack for token in ("world", "power", "faction", "locale", "rule")):
        return "world_pressure"
    if any(token in haystack for token in ("character", "relationship", "emotion")):
        return "character_pressure"
    if "dialogue" in haystack or "craft" in haystack or "style" in haystack:
        return "craft_control"
    if "scene" in haystack or "chapter" in haystack:
        return "chapter_rhythm"
    if any(token in haystack for token in ("volume", "arc", "escalation")):
        return "volume_escalation"
    return "series_engine"


def _project_binding(project_context: Mapping[str, Any] | None) -> str:
    context = _as_mapping(project_context)
    for key in ("unique_hook", "reader_promise", "premise", "dramatic_question"):
        value = _text(context.get(key))
        if value:
            return f"绑定到本项目的 {key}: {_truncate(value, 90)}"
    return "绑定到本项目的独特设定、人物选择、世界规则或资源代价，禁止直接套用来源样本。"


def _mechanism_id(row: Mapping[str, Any]) -> str:
    return _text(row.get("mechanism_id") or row.get("slug") or row.get("name") or "mechanism")


def _selected_mechanism(
    row: Mapping[str, Any],
    *,
    project_context: Mapping[str, Any] | None,
) -> SelectedMechanism:
    mechanism_id = _mechanism_id(row)
    role = _role_for_row(row)
    summary = _truncate(row.get("summary") or row.get("narrative_summary"), 100)
    binding = _project_binding(project_context)
    return SelectedMechanism(
        mechanism_id=mechanism_id,
        source_confidence=_confidence(row),
        design_role=role,
        adaptation_instruction=(
            f"只借用抽象机制“{mechanism_id}”的结构功能"
            f"{f'：{summary}' if summary else ''}；必须改写为本项目独有的因果链。"
        ),
        required_project_specific_binding=binding,
        failure_mode=(
            "若只复述机制名、无项目专属世界规则/人物选择/资源代价，或复用来源桥段组合，"
            "则视为未消费蒸馏策略。"
        ),
    )


def _select_mechanisms(
    rows: Sequence[object],
    *,
    project_context: Mapping[str, Any] | None,
    max_mechanisms: int,
) -> list[SelectedMechanism]:
    candidates = [
        row
        for row in (_as_mapping(item) for item in rows)
        if _mechanism_id(row)
    ]
    candidates.sort(key=_confidence, reverse=True)
    selected: list[SelectedMechanism] = []
    used_ids: set[str] = set()
    for role in _ROLE_ORDER:
        for row in candidates:
            mechanism_id = _mechanism_id(row)
            if mechanism_id in used_ids or _role_for_row(row) != role:
                continue
            selected.append(_selected_mechanism(row, project_context=project_context))
            used_ids.add(mechanism_id)
            break
        if len(selected) >= max_mechanisms:
            return selected
    for row in candidates:
        mechanism_id = _mechanism_id(row)
        if mechanism_id in used_ids:
            continue
        selected.append(_selected_mechanism(row, project_context=project_context))
        used_ids.add(mechanism_id)
        if len(selected) >= max_mechanisms:
            break
    return selected


def _manifest_maturity(ref: Mapping[str, Any]) -> tuple[float, str]:
    manifest = _as_mapping(ref.get("manifest"))
    try:
        score = float(manifest.get("maturity_score"))
    except (TypeError, ValueError):
        score = calculate_aggregate_maturity_score(
            source_count=int(manifest.get("source_count") or 0),
            mechanism_rows=len(_as_list(ref.get("mechanisms"))),
            author_craft_rows=len(_as_list(ref.get("author_craft"))),
            anti_copy_blocked_combinations=len(
                _string_items(_as_mapping(ref.get("anti_copy")).get("blocked_combinations"))
            ),
            grammar_state_variables=len(
                _string_items(_as_mapping(ref.get("grammar")).get("state_variables"))
            ),
            grammar_change_vectors=len(
                _string_items(_as_mapping(ref.get("grammar")).get("chapter_change_vectors"))
            ),
            book_design_rows=len(_as_list(ref.get("book_designs"))),
            volume_design_rows=len(_usable_volume_rows(ref.get("volume_paths"))),
            fallback_volume_rows=len(_fallback_volume_rows(ref.get("volume_paths"))),
        )
    status = _text(manifest.get("maturity_status")) or (
        "production" if score >= 0.7 else "review" if score >= 0.3 else "pilot"
    )
    return max(0.0, min(score, 1.0)), status


def _is_fallback_volume_row(row: Mapping[str, Any]) -> bool:
    if row.get("distillation_fallback") is True:
        return True
    haystack = " ".join(
        _text(part)
        for part in (
            row.get("arc_function"),
            row.get("dominant_engine"),
            row.get("setup_payoff_rhythm"),
            " ".join(_text(item) for item in _as_list(row.get("state_progression"))),
        )
    ).lower()
    return "fallback aggregation" in haystack or "llm output fallback" in haystack


def _usable_volume_rows(value: object) -> list[dict[str, Any]]:
    return [
        row
        for row in (_as_mapping(item) for item in _as_list(value))
        if row and not _is_fallback_volume_row(row)
    ]


def _fallback_volume_rows(value: object) -> list[dict[str, Any]]:
    return [
        row
        for row in (_as_mapping(item) for item in _as_list(value))
        if row and _is_fallback_volume_row(row)
    ]


def _material_summaries(rows: Sequence[object], dimensions: set[str], *, limit: int) -> list[str]:
    summaries: list[str] = []
    for row in (_as_mapping(item) for item in rows):
        if _text(row.get("dimension")) not in dimensions:
            continue
        name = _text(row.get("name") or row.get("slug"))
        summary = _truncate(row.get("narrative_summary"), 100)
        if name and summary:
            summaries.append(f"{name}: {summary}")
        elif name:
            summaries.append(name)
        if len(summaries) >= limit:
            break
    return summaries


def _craft_controls(rows: Sequence[object], *, limit: int = 8) -> list[str]:
    controls: list[str] = []
    for row in (_as_mapping(item) for item in rows):
        controls.extend(_string_items(row.get("dialogue_system"), limit=2))
        controls.extend(_string_items(row.get("description_strategy"), limit=2))
        controls.extend(_string_items(row.get("exposition_strategy"), limit=2))
        controls.extend(_string_items(row.get("hooking_and_transitions"), limit=2))
        if len(controls) >= limit:
            break
    return _dedupe(controls, limit=limit)


def _anti_copy_boundaries(ref: Mapping[str, Any], *, limit: int = 12) -> list[str]:
    grammar = _as_mapping(ref.get("grammar"))
    anti_copy = _as_mapping(ref.get("anti_copy"))
    return _dedupe(
        [
            *_string_items(grammar.get("forbidden_defaults"), limit=8),
            *_string_items(anti_copy.get("replacement_policy"), limit=6),
            *_string_items(anti_copy.get("blocked_combinations"), limit=8),
        ],
        limit=limit,
    )


def compile_distilled_strategy_card(
    *,
    category_key: str | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    project_context: Mapping[str, Any] | None = None,
    repo_root: Path | None = None,
    max_mechanisms: int = 8,
) -> DistilledStrategyCard | None:
    """Compile the best matching aggregate into a project strategy card."""

    ref = load_distilled_design_reference(
        category_key=category_key,
        genre=genre,
        sub_genre=sub_genre,
        repo_root=repo_root,
    )
    if not ref:
        return None

    grammar = _as_mapping(ref.get("grammar"))
    manifest = _as_mapping(ref.get("manifest"))
    mechanisms = _as_list(ref.get("mechanisms"))
    materials = _as_list(ref.get("materials"))
    selected = _select_mechanisms(
        mechanisms,
        project_context=project_context,
        max_mechanisms=max_mechanisms,
    )
    score, status = _manifest_maturity(ref)
    required_state_variables = _dedupe(
        [
            *_string_items(grammar.get("state_variables"), limit=8),
            *[
                state
                for row in (_as_mapping(item) for item in materials)
                for state in _string_items(
                    _as_mapping(row.get("content_json")).get("state_variables")
                )
            ],
        ],
        limit=10,
    )
    required_change_vectors = _dedupe(
        _string_items(grammar.get("chapter_change_vectors"), limit=10),
        limit=10,
    )
    reader_reward_mix = _dedupe(_string_items(grammar.get("reader_rewards"), limit=10), limit=10)
    world_design_paths = _material_summaries(
        materials,
        {"world_settings", "power_systems", "factions", "locale_templates"},
        limit=6,
    )
    character_design_paths = _material_summaries(
        materials,
        {"character_archetypes", "character_templates", "emotion_arcs", "dialogue_styles"},
        limit=6,
    )
    volume_design_paths = [
        _truncate(
            row.get("arc_function") or row.get("dominant_engine") or row.get("setup_payoff_rhythm"),
            120,
        )
        for row in _usable_volume_rows(ref.get("volume_paths"))
        if _text(row.get("arc_function") or row.get("dominant_engine"))
    ][:6]
    chapter_execution_patterns = _material_summaries(
        materials,
        {"scene_templates"},
        limit=6,
    )
    craft_controls = _craft_controls(_as_list(ref.get("author_craft")), limit=8)
    anti_copy_boundaries = _anti_copy_boundaries(ref, limit=12)
    aggregate_key = _text(
        ref.get("key") or manifest.get("aggregate_key") or category_key or "unknown"
    )
    try:
        from bestseller.services.distilled_worldview_bridge import (
            build_distilled_worldview_bindings,
        )

        worldview_bindings = build_distilled_worldview_bindings(
            {
                "aggregate_key": aggregate_key,
                "selected_mechanisms": [
                    item.model_dump(mode="json") for item in selected
                ],
                "required_state_variables": required_state_variables,
                "anti_copy_boundaries": anti_copy_boundaries,
            },
            aggregate_materials=[_as_mapping(item) for item in materials],
        )
    except Exception:
        worldview_bindings = {}
    transformation_requirements = [
        f"{item.mechanism_id}: {item.required_project_specific_binding}"
        for item in selected
    ]
    if status in {"pilot", "review"}:
        transformation_requirements.append(
            f"aggregate maturity is {status}/{score:.2f}; "
            "use as directional strategy, not a hard template."
        )
    plan_consumption_checks = [
        "Every selected mechanism must appear through a project-specific world rule, "
        "character choice, resource cost, or payoff window.",
        "StoryDesignKernel and VolumePlan must include measurable state changes, "
        "not only mechanism names.",
        "Anti-copy boundaries must remain visible in planning and draft prompts.",
    ]
    for state in required_state_variables[:4]:
        plan_consumption_checks.append(f"Plan should track state variable: {state}.")

    return DistilledStrategyCard(
        aggregate_key=aggregate_key,
        maturity_score=score,
        maturity_status=status,
        source_count=int(manifest.get("source_count") or 0),
        selected_mechanisms=selected,
        required_state_variables=required_state_variables,
        required_change_vectors=required_change_vectors,
        reader_reward_mix=reader_reward_mix,
        world_design_paths=world_design_paths,
        character_design_paths=character_design_paths,
        volume_design_paths=volume_design_paths,
        chapter_execution_patterns=chapter_execution_patterns,
        craft_controls=craft_controls,
        anti_copy_boundaries=anti_copy_boundaries,
        world_mechanism_bindings=_as_list(
            _as_mapping(worldview_bindings).get("distilled_mechanism_bindings")
        ),
        worldview_bindings=_as_mapping(worldview_bindings),
        transformation_requirements=transformation_requirements,
        plan_consumption_checks=plan_consumption_checks,
    )


def distilled_strategy_card_to_dict(card: DistilledStrategyCard) -> dict[str, Any]:
    return card.model_dump(mode="json")


def distilled_strategy_card_from_dict(data: Mapping[str, Any]) -> DistilledStrategyCard:
    return DistilledStrategyCard.model_validate(dict(data))


def render_distilled_strategy_card_block(
    card: DistilledStrategyCard | Mapping[str, Any] | None,
    *,
    phase: str = "architecture",
    language: str = "zh-CN",
    max_mechanisms: int = 5,
) -> str:
    """Render a compact phase-aware strategy block for planner prompts."""

    if card is None:
        return ""
    if isinstance(card, Mapping):
        card = distilled_strategy_card_from_dict(card)
    is_en = str(language or "").lower().startswith("en")
    title = (
        f"## Distilled Strategy Card ({card.aggregate_key} / {phase})"
        if is_en
        else f"## 蒸馏策略卡({card.aggregate_key} / {phase})"
    )
    lines = [
        title,
        (
            f"- Maturity: {card.maturity_status} / {card.maturity_score:.2f}; "
            f"sources={card.source_count}"
            if is_en
            else (
                f"- 成熟度: {card.maturity_status} / {card.maturity_score:.2f}; "
                f"来源数={card.source_count}"
            )
        ),
        (
            "- Use only transformed mechanisms; never copy source names, plot chains, "
            "or distinctive combinations."
            if is_en
            else "- 只使用转化后的机制；不得复制来源专名、剧情链或标志性组合。"
        ),
    ]
    if card.selected_mechanisms:
        lines.append("- Selected mechanisms:" if is_en else "- 已选机制:")
        for item in card.selected_mechanisms[:max_mechanisms]:
            lines.append(
                f"  - [{item.design_role}] {item.mechanism_id}: "
                f"{item.required_project_specific_binding}"
            )
    if phase == "world" and card.world_mechanism_bindings:
        lines.append("- World mechanisms:" if is_en else "- 世界机制:")
        for item in card.world_mechanism_bindings[:max_mechanisms]:
            row = _as_mapping(item)
            states = _string_items(row.get("state_variables"), limit=4)
            state_text = f" -> states: {', '.join(states)}" if states else ""
            lines.append(f"  - {_text(row.get('mechanism_id'))}{state_text}")
        worldview_states = [
            _text(state.get("key"))
            for state in (
                _as_mapping(item)
                for item in _as_list(
                    _as_mapping(card.worldview_bindings).get("state_variables")
                )
            )
            if _text(state.get("key"))
        ]
        if worldview_states:
            label = "- World state variables: " if is_en else "- 世界状态变量: "
            lines.append(label + ", ".join(_dedupe(worldview_states, limit=8)))
    phase_map = {
        "architecture": card.transformation_requirements,
        "world": card.world_design_paths or card.required_state_variables,
        "cast": card.character_design_paths,
        "story_design": card.required_change_vectors or card.transformation_requirements,
        "volume_plan": card.volume_design_paths or card.plan_consumption_checks,
        "chapter_outline": (
            card.required_state_variables
            + card.chapter_execution_patterns
            + card.reader_reward_mix
        ),
        "craft": card.craft_controls,
    }
    phase_items = phase_map.get(phase, [])
    if phase_items:
        lines.append("- Phase obligations:" if is_en else "- 本阶段义务:")
        for item in phase_items[:6]:
            lines.append(f"  - {item}")
    if card.anti_copy_boundaries:
        label = "- Anti-copy boundaries: " if is_en else "- 反抄袭边界: "
        lines.append(label + "; ".join(card.anti_copy_boundaries[:8]))
    return "\n".join(lines)


def render_all_distilled_strategy_blocks(
    card: DistilledStrategyCard | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
    phases: Sequence[str] | None = None,
) -> dict[str, str]:
    selected = tuple(
        phases
        or (
            "architecture",
            "world",
            "cast",
            "story_design",
            "volume_plan",
            "chapter_outline",
            "craft",
        )
    )
    return {
        phase: block
        for phase in selected
        if (
            block := render_distilled_strategy_card_block(
                card,
                phase=phase,
                language=language,
            )
        )
    }


__all__ = [
    "DesignRole",
    "DistilledStrategyCard",
    "SelectedMechanism",
    "compile_distilled_strategy_card",
    "distilled_strategy_card_from_dict",
    "distilled_strategy_card_to_dict",
    "render_all_distilled_strategy_blocks",
    "render_distilled_strategy_card_block",
]
