"""Structured story-design kernel.

The kernel is the durable contract between high-level book conception and
chapter planning.  It captures what should change, why each line exists, and
which defaults must not leak into unrelated books.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bestseller.services.story_shape_router import StoryShape

PlotLineType = Literal[
    "main",
    "subplot",
    "relationship",
    "character",
    "world",
    "theme",
    "mystery",
    "progression",
]
ReverseOutlineStatus = Literal["not_started", "draft", "verified", "needs_repair"]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "；".join(item for item in (_text(item) for item in value) if item)
    if isinstance(value, dict):
        for key in (
            "value",
            "description",
            "summary",
            "strategic_value",
            "required_outcome",
            "trigger_condition",
            "current_status",
            "challenge_risk",
            "vector",
            "change_vector",
            "constraint",
        ):
            text = _text(value.get(key))
            if text:
                return text
        parts = [item for item in (_text(v) for v in value.values()) if item]
        return "；".join(parts)
    return ""


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = _text(data.get(key))
        if text:
            return text
    return ""


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text for text in (_text(item) for item in value) if text]
    text = _text(value)
    return [text] if text else []


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class PremiseContract(BaseModel, frozen=True):
    """The project-level promise that prevents generic plot fallback."""

    unique_hook: str = Field(min_length=1)
    core_question: str = Field(min_length=1)
    commercial_pull: str = Field(min_length=1)
    forbidden_defaults: list[str] = Field(default_factory=list)


class CharacterConflictContract(BaseModel, frozen=True):
    """A character line described as goal, pressure, choice, and change."""

    character_key: str = Field(min_length=1)
    external_goal: str = Field(min_length=1)
    internal_need: str = Field(min_length=1)
    pressure_source: str = Field(min_length=1)
    choice_axis: str = Field(min_length=1)
    change_vector: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        for key in ("external_goal", "internal_need", "choice_axis"):
            if key in data:
                data[key] = _text(data.get(key))
        if not _text(data.get("pressure_source")):
            data["pressure_source"] = _first_text(
                data,
                "pressure_trigger",
                "pressure",
                "conflict_source",
                "temptation",
                "scene_test",
            )
        if not _text(data.get("change_vector")):
            data["change_vector"] = _first_text(
                data,
                "payoff_mode",
                "transformation",
                "arc_shift",
                "boundary_line",
                "choice_axis",
                "scene_test",
            )
        return data


class WorldConflictContract(BaseModel, frozen=True):
    """A world/system rule that can create visible story pressure."""

    axis: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    visible_cost: str = Field(min_length=1)
    escalation_path: str = Field(min_length=1)


class WorldviewInvariant(BaseModel, frozen=True):
    """A hard rule that makes the book's world behave consistently."""

    key: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    violation_cost: str = Field(min_length=1)
    narrative_use: str = Field(min_length=1)


class WorldviewSystem(BaseModel, frozen=True):
    """A reusable world system such as magic, law, economy, technology, or social order."""

    name: str = Field(min_length=1)
    operating_logic: str = Field(min_length=1)
    resources_or_authority: str = Field(min_length=1)
    limits: str = Field(min_length=1)
    costs: str = Field(min_length=1)
    failure_modes: list[str] = Field(default_factory=list)


class WorldviewFaction(BaseModel, frozen=True):
    """A force in the world with resources, motives, and pressure on the protagonist."""

    name: str = Field(min_length=1)
    public_role: str = Field(min_length=1)
    hidden_agenda: str = Field(min_length=1)
    resources: str = Field(min_length=1)
    pressure_on_protagonist: str = Field(min_length=1)


class WorldviewLocation(BaseModel, frozen=True):
    """A repeatable location whose rules can generate scenes and conflicts."""

    name: str = Field(min_length=1)
    surface_function: str = Field(min_length=1)
    hidden_function: str = Field(min_length=1)
    conflict_sources: list[str] = Field(min_length=1)
    evidence_or_resource_types: list[str] = Field(default_factory=list)


class WorldviewRevealStep(BaseModel, frozen=True):
    """A staged world reveal that should not leak before its slot."""

    stage: str = Field(min_length=1)
    reveal: str = Field(min_length=1)
    earliest_chapter: int | None = None
    earliest_volume: int | None = None
    unlock_condition: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("reveal")):
            data["reveal"] = _first_text(data, "reveals", "reader_requirement", "summary")
        if not _text(data.get("unlock_condition")):
            data["unlock_condition"] = (
                _first_text(data, "trigger", "chapter_range", "reader_requirement")
                or "通过该阶段的具体事件、证据或代价触发揭示。"
            )
        return data


class WorldviewIntegrationContract(BaseModel, frozen=True):
    """How volumes and chapters must consume the world instead of explaining it."""

    chapter_rule: str = Field(min_length=1)
    volume_rule: str = Field(min_length=1)
    reveal_rule: str = Field(min_length=1)
    continuity_rule: str = Field(min_length=1)


class DistilledWorldMechanismBinding(BaseModel, frozen=True):
    """A distilled aggregate mechanism adapted into this book's worldview."""

    aggregate_key: str = Field(min_length=1)
    mechanism_id: str = Field(min_length=1)
    design_role: str = Field(min_length=1)
    source_confidence: float = Field(ge=0.0, le=1.0)
    required_project_binding: str = Field(min_length=1)
    state_variables: list[str] = Field(default_factory=list)
    required_cost: str = ""
    anti_copy_boundaries: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data.setdefault(
            "aggregate_key",
            _first_text(
                data,
                "aggregate",
                "source_aggregate",
                "bucket",
                "category",
                "mechanism_key",
                "binding_key",
                "key",
            )
            or "project-specific",
        )
        data.setdefault(
            "mechanism_id",
            _first_text(
                data,
                "mechanism",
                "mechanism_key",
                "binding_key",
                "key",
                "name",
                "id",
            )
            or data["aggregate_key"],
        )
        data.setdefault(
            "design_role",
            _first_text(
                data,
                "role",
                "story_role",
                "narrative_role",
                "binding_type",
                "purpose",
                "narrative_function",
                "example_scene",
                "adaptation",
                "constraint",
                "description",
            )
            or "world_pressure",
        )
        data["source_confidence"] = _float_or_default(
            data.get("source_confidence", data.get("confidence")),
            0.7,
        )
        data.setdefault(
            "required_project_binding",
            _first_text(
                data,
                "project_binding",
                "required_binding",
                "binding",
                "constraint",
                "adaptation",
                "binding_detail",
                "application",
                "chapter_binding",
                "execution_rule",
                "rule",
                "description",
            )
            or "该机制必须绑定到本书的具体规则、状态变量或章节代价。",
        )
        data["state_variables"] = _text_list(
            data.get("state_variables") or data.get("states") or data.get("variables")
        )
        data["anti_copy_boundaries"] = _text_list(data.get("anti_copy_boundaries"))
        data["required_cost"] = _text(
            data.get("required_cost") or data.get("cost") or data.get("visible_cost")
        )
        return data


class WorldStateVariable(BaseModel, frozen=True):
    """A measurable world variable that should change through planning."""

    key: str = Field(min_length=1)
    variable_type: str = Field(min_length=1)
    current_value: str = ""
    desired_direction: str = Field(min_length=1)
    change_triggers: list[str] = Field(min_length=1)
    failure_mode: str = Field(min_length=1)
    source_mechanism_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data.setdefault("key", _first_text(data, "variable_key", "id", "name"))
        key_text = _text(data.get("key"))
        if not _text(data.get("variable_type")):
            haystack = f"{key_text} {_text(data.get('name'))} {_text(data.get('description'))}"
            if any(token in haystack for token in ("信任", "关系", "同盟", "trust", "ally")):
                variable_type = "relationship"
            elif any(token in haystack for token in ("线索", "真相", "证据", "clue", "truth")):
                variable_type = "information"
            elif any(token in haystack for token in ("风险", "压力", "暴露", "risk", "pressure")):
                variable_type = "risk"
            elif any(token in haystack for token in ("分数", "积分", "资源", "score", "resource")):
                variable_type = "resource"
            else:
                variable_type = "state"
            data["variable_type"] = variable_type
        if "current_value" in data:
            data["current_value"] = _text(data.get("current_value"))
        if not _text(data.get("desired_direction")):
            data["desired_direction"] = (
                _first_text(
                    data,
                    "direction",
                    "target_direction",
                    "trajectory",
                    "target_state",
                    "desired_state",
                    "change_vector",
                )
                or "随剧情推进发生可验证变化。"
            )
        triggers = _text_list(
            data.get("change_triggers")
            or data.get("triggers")
            or data.get("trigger")
            or data.get("update_events")
            or data.get("change_events")
        )
        data["change_triggers"] = triggers or [
            "章节关键选择、线索验证或规则代价触发变化。"
        ]
        if not _text(data.get("failure_mode")):
            data["failure_mode"] = (
                _first_text(
                    data,
                    "failure",
                    "risk_if_static",
                    "failure_if_static",
                    "risk",
                    "anti_pattern",
                )
                or "状态变量停滞会导致章节只推进事件而不改变故事状态。"
            )
        data["source_mechanism_ids"] = _text_list(data.get("source_mechanism_ids"))
        return data


class WorldAssetLedgerItem(BaseModel, frozen=True):
    """A world asset whose value must carry cost, exposure, and attention."""

    key: str = Field(min_length=1)
    asset_type: str = Field(min_length=1)
    value: str = Field(min_length=1)
    cost: str = Field(min_length=1)
    exposure_risk: str = Field(min_length=1)
    attention_sources: list[str] = Field(default_factory=list)
    source_mechanism_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data.setdefault("key", _first_text(data, "asset_key", "asset_id", "id", "name"))
        data.setdefault("asset_type", _first_text(data, "type", "category", "name") or "story_asset")
        data.setdefault(
            "value",
            _first_text(
                data,
                "strategic_value",
                "value_description",
                "description",
                "narrative_value",
                "purpose",
                "asset_key",
                "key",
                "name",
            ),
        )
        data.setdefault(
            "cost",
            _first_text(
                data,
                "cost_to_obtain",
                "cost_visible",
                "cost_description",
                "visible_cost",
            )
            or "使用或保留该资产会带来可见成本或选择代价。",
        )
        data.setdefault(
            "exposure_risk",
            _first_text(data, "exposure", "risk", "failure_if_lost", "failure_if_misused")
            or "资产暴露会引发对手注意或关系风险。",
        )
        return data


class WorldAuthorityClaim(BaseModel, frozen=True):
    """A legitimacy claim by a faction or institution over a world target."""

    claimant: str = Field(min_length=1)
    target: str = Field(min_length=1)
    claim_basis: str = Field(min_length=1)
    legitimacy: str = Field(min_length=1)
    conflict_with: list[str] = Field(default_factory=list)
    escalation_path: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("claimant")):
            data["claimant"] = (
                _first_text(
                    data,
                    "holder",
                    "authority_holder",
                    "entity",
                    "source",
                    "authority_key",
                    "key",
                )
                or "未命名权威主体"
            )
        if not _text(data.get("target")):
            data["target"] = _first_text(data, "target", "scope", "authority_key", "key")
        if not _text(data.get("claim_basis")):
            data["claim_basis"] = _first_text(
                data,
                "claim",
                "legitimacy_source",
                "basis",
                "authority_type",
                "claim_type",
            )
        if not _text(data.get("legitimacy")):
            data["legitimacy"] = _first_text(
                data,
                "current_status",
                "legitimacy_source",
                "authority_type",
                "claim_type",
            )
        if not _text(data.get("escalation_path")):
            data["escalation_path"] = _first_text(
                data,
                "challenge_condition",
                "challenge_risk",
                "risk",
                "limitation",
                "limits",
            )
        data["target"] = _text(data.get("target")) or "未命名权威对象"
        data["claim_basis"] = _text(data.get("claim_basis")) or "该权威拥有叙事中的既定依据。"
        data["legitimacy"] = _text(data.get("legitimacy")) or "合法性需要在情节中持续验证。"
        data["escalation_path"] = _text(data.get("escalation_path")) or "权威受挑战时升级为公开冲突。"
        data["conflict_with"] = _text_list(data.get("conflict_with"))
        return data


class WorldSceneTemplateBinding(BaseModel, frozen=True):
    """A distilled scene pattern used to make world rules executable."""

    key: str = Field(min_length=1)
    template_name: str = Field(min_length=1)
    use_case: str = Field(min_length=1)
    required_change: list[str] = Field(min_length=1)
    source_mechanism_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data.setdefault(
            "key",
            _first_text(data, "template_key", "template_id", "scene_id", "id", "name"),
        )
        data.setdefault(
            "template_name",
            _first_text(data, "template_name", "name", "title", "template_key", "scene_id"),
        )
        data.setdefault(
            "use_case",
            _first_text(
                data,
                "trigger_condition",
                "trigger",
                "purpose",
                "scene_use_case",
                "required_outcome",
                "expected_outcome",
                "structure",
                "variation",
                "obligation",
            ),
        )
        if not _text(data.get("use_case")):
            data["use_case"] = (
                _first_text(data, "template_name", "key")
                or "该场景用于触发世界规则或推动状态变化。"
            )
        if not data.get("required_change"):
            data["required_change"] = _text_list(
                [
                    data.get("required_outcome"),
                    data.get("expected_outcome"),
                    data.get("required_elements"),
                    data.get("structure"),
                    data.get("variation"),
                    data.get("obligation"),
                    data.get("scene_structure"),
                    data.get("cost_requirement"),
                    data.get("resolution_variations"),
                    data.get("consequence_type"),
                    data.get("tone"),
                    data.get("anti_repeat_rule"),
                ]
            )
        else:
            data["required_change"] = _text_list(data.get("required_change"))
        if not data["required_change"]:
            data["required_change"] = [
                _first_text(data, "use_case", "template_name", "key")
                or "该场景必须造成可见状态变化。"
            ]
        return data


class WorldviewKernel(BaseModel, frozen=True):
    """The book-specific operating system for world behavior and story pressure.

    This is intentionally framework-level: every book can have a different
    world, but every book's world must expose the same kinds of contracts so
    volume planning, chapter outlines, drafting, and review can obey them.
    """

    premise: str = Field(min_length=1)
    uniqueness_principle: str = Field(min_length=1)
    invariants: list[WorldviewInvariant] = Field(min_length=1)
    systems: list[WorldviewSystem] = Field(min_length=1)
    factions: list[WorldviewFaction] = Field(default_factory=list)
    locations: list[WorldviewLocation] = Field(default_factory=list)
    reveal_ladder: list[WorldviewRevealStep] = Field(default_factory=list)
    integration_contract: WorldviewIntegrationContract
    distilled_mechanism_bindings: list[DistilledWorldMechanismBinding] = Field(
        default_factory=list
    )
    state_variables: list[WorldStateVariable] = Field(default_factory=list)
    asset_ledger: list[WorldAssetLedgerItem] = Field(default_factory=list)
    authority_claims: list[WorldAuthorityClaim] = Field(default_factory=list)
    scene_templates: list[WorldSceneTemplateBinding] = Field(default_factory=list)
    anti_copy_boundaries: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data["anti_copy_boundaries"] = _text_list(data.get("anti_copy_boundaries"))
        return data


class StructureStrategy(BaseModel, frozen=True):
    """How the book converts its premise into repeated chapter movement."""

    macro_strategy: str = Field(min_length=1)
    chapter_engine: str = Field(min_length=1)
    pacing_rule: str = Field(min_length=1)
    freshness_rule: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        for key in ("macro_strategy", "chapter_engine", "pacing_rule", "freshness_rule"):
            if key in data:
                data[key] = _text(data.get(key))
        return data


class PlotTreeNode(BaseModel, frozen=True):
    """A plot line plus its dependency contract."""

    key: str = Field(min_length=1)
    line_type: PlotLineType
    label: str = Field(min_length=1)
    role: str = Field(min_length=1)
    current_state: str = Field(min_length=1)
    target_state: str = Field(min_length=1)
    dependency_on_mainline: str = ""
    failure_if_removed: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        line_type = _text(data.get("line_type")).lower()
        if line_type in {"antagonist", "villain", "opposition", "rival", "supporting"}:
            data["line_type"] = "subplot"
        elif line_type in {"romance", "emotion", "emotional"}:
            data["line_type"] = "relationship"
        elif line_type in {
            "backstory",
            "back_story",
            "history",
            "origin",
            "case_backstory",
            "past_case",
            "truth_backstory",
        }:
            data["line_type"] = "mystery"
        elif line_type in {"power", "leveling", "upgrade", "growth"}:
            data["line_type"] = "progression"
        elif line_type.startswith("main_") or line_type in {"mainline", "main-line"}:
            data["line_type"] = "main"
        return data

    @model_validator(mode="after")
    def _require_non_main_dependency(self) -> PlotTreeNode:
        if self.line_type != "main" and not self.dependency_on_mainline.strip():
            raise ValueError("non-main plot lines must explain dependency_on_mainline")
        return self


class BeatScheduleItem(BaseModel, frozen=True):
    """A planned movement unit over one chapter or a chapter range."""

    chapter_range: str = Field(min_length=1)
    duty: str = Field(min_length=1)
    state_change: str = Field(min_length=1)
    payoff: str = Field(min_length=1)
    hook_or_aftereffect: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("state_change")):
            data["state_change"] = _first_text(
                data,
                "state_changes",
                "story_change",
                "plot_change",
                "emotional_change",
                "world_change",
                "reader_state_change",
                "change",
            )
        if not _text(data.get("payoff")):
            data["payoff"] = _first_text(
                data,
                "reader_payoff",
                "payoff_or_aftereffect",
                "closure_requirements",
                "duty",
            )
        if not _text(data.get("hook_or_aftereffect")):
            data["hook_or_aftereffect"] = _first_text(
                data,
                "hook",
                "aftereffect",
                "next_pressure",
                "payoff_or_aftereffect",
                "payoff",
            )
        return data


class FourCausesContract(BaseModel, frozen=True):
    """Book-level purpose/material/form/force contract from writing-principle analysis."""

    purpose_result: str = Field(min_length=1)
    material_basis: list[str] = Field(min_length=1)
    formal_pattern: str = Field(min_length=1)
    driving_forces: list[str] = Field(min_length=1)
    proof_criteria: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("purpose_result")):
            data["purpose_result"] = _first_text(
                data,
                "purpose",
                "theme_result",
                "desired_result",
                "target_result",
                "目的结果",
            )
        data["material_basis"] = _text_list(
            data.get("material_basis") or data.get("materials") or data.get("material")
        )
        if not _text(data.get("formal_pattern")):
            data["formal_pattern"] = _first_text(
                data,
                "form",
                "structure_form",
                "pattern",
                "formal_strategy",
            )
        data["driving_forces"] = _text_list(
            data.get("driving_forces")
            or data.get("forces")
            or data.get("force")
            or data.get("动力")
        )
        data["proof_criteria"] = _text_list(
            data.get("proof_criteria") or data.get("success_criteria") or data.get("proof")
        )
        return data


class MacroStructureContract(BaseModel, frozen=True):
    """How large-scale plot movement should distribute rhythm and repetition."""

    structure_type: str = Field(min_length=1)
    mainline_rule: str = Field(min_length=1)
    subline_rule: str = ""
    rhythm_rule: str = ""
    anti_homogeneity_rule: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("structure_type")):
            data["structure_type"] = _first_text(
                data,
                "type",
                "macro_type",
                "macro_structure",
                "structure",
            )
        if not _text(data.get("mainline_rule")):
            data["mainline_rule"] = _first_text(
                data,
                "main_rule",
                "mainline",
                "primary_rule",
                "progression_rule",
            )
        if "subline_rule" in data:
            data["subline_rule"] = _text(data.get("subline_rule"))
        if not _text(data.get("rhythm_rule")):
            data["rhythm_rule"] = _first_text(data, "rhythm", "pacing_rule", "line_rhythm")
        if not _text(data.get("anti_homogeneity_rule")):
            data["anti_homogeneity_rule"] = _first_text(
                data,
                "anti_repeat_rule",
                "freshness_rule",
                "anti_template_rule",
            )
        return data


class ReaderDesireContract(BaseModel, frozen=True):
    """Reader desire type plus payoff and risk-control strategy."""

    desire_type: str = Field(min_length=1)
    reader_expectation: str = Field(min_length=1)
    payoff_mode: str = Field(min_length=1)
    risk_control: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("desire_type")):
            data["desire_type"] = _first_text(data, "type", "reader_desire", "need_type")
        if not _text(data.get("reader_expectation")):
            data["reader_expectation"] = _first_text(
                data,
                "expectation",
                "reader_waiting",
                "reader_want",
                "expected_emotion",
            )
        if not _text(data.get("payoff_mode")):
            data["payoff_mode"] = _first_text(
                data,
                "payoff",
                "reward",
                "satisfaction_mode",
                "closure_mode",
            )
        if "risk_control" in data:
            data["risk_control"] = _text(data.get("risk_control"))
        return data


class EventPatternContract(BaseModel, frozen=True):
    """Reusable event-unit pattern, not a per-chapter six-step mandate."""

    pattern_type: str = Field(min_length=1)
    use_case: str = Field(min_length=1)
    reader_effect: str = Field(min_length=1)
    anti_repetition_rule: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("pattern_type")):
            data["pattern_type"] = _first_text(data, "type", "event_type", "role")
        if not _text(data.get("use_case")):
            data["use_case"] = _first_text(data, "when_to_use", "use", "scenario")
        if not _text(data.get("reader_effect")):
            data["reader_effect"] = _first_text(
                data,
                "effect",
                "reader_reward",
                "reader_impact",
                "emotion_effect",
            )
        if not _text(data.get("anti_repetition_rule")):
            data["anti_repetition_rule"] = _first_text(
                data,
                "anti_repeat_rule",
                "freshness_rule",
                "risk_control",
            )
        return data


class StoryDesignKernel(BaseModel, frozen=True):
    """Validated story design contract for planning and gates."""

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    shape: StoryShape
    reader_promise: str = Field(min_length=1)
    premise_contract: PremiseContract
    character_conflict_contracts: list[CharacterConflictContract] = Field(min_length=1)
    world_conflict_contracts: list[WorldConflictContract] = Field(default_factory=list)
    worldview_kernel: WorldviewKernel | None = None
    four_causes_contract: FourCausesContract | None = None
    macro_structure_contract: MacroStructureContract | None = None
    reader_desire_matrix: list[ReaderDesireContract] = Field(default_factory=list)
    event_pattern_inventory: list[EventPatternContract] = Field(default_factory=list)
    structure_strategy: StructureStrategy
    plot_tree: list[PlotTreeNode] = Field(min_length=1)
    beat_schedule: list[BeatScheduleItem] = Field(min_length=1)
    change_vectors: list[str] = Field(min_length=1)
    uniqueness_constraints: list[str] = Field(default_factory=list)
    reverse_outline_status: ReverseOutlineStatus = "not_started"

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data.setdefault(
            "shape",
            {
                "length_class": "novella",
                "publication_mode": "web_serial",
                "outline_depth": "chapter",
                "primary_duties": ["forward_pull", "resolution_completeness"],
                "ending_contract": "close the current story loop",
            },
        )
        if "reader_promise" in data:
            data["reader_promise"] = _text(data.get("reader_promise"))
        data["change_vectors"] = _text_list(data.get("change_vectors"))
        data["uniqueness_constraints"] = _text_list(data.get("uniqueness_constraints"))
        return data

    @model_validator(mode="after")
    def _require_mainline_and_changes(self) -> StoryDesignKernel:
        if not any(node.line_type == "main" for node in self.plot_tree):
            raise ValueError("story design kernel requires at least one main plot line")
        if not any(vector.strip() for vector in self.change_vectors):
            raise ValueError("story design kernel requires at least one change vector")
        return self


def story_design_kernel_from_dict(data: dict[str, Any]) -> StoryDesignKernel:
    """Validate and hydrate a kernel from persisted or LLM-produced data."""

    return StoryDesignKernel.model_validate(data)


def story_design_kernel_to_dict(kernel: StoryDesignKernel) -> dict[str, Any]:
    """Serialize a kernel using JSON-compatible values."""

    return kernel.model_dump(mode="json")


def render_story_design_kernel_prompt_block(
    kernel: StoryDesignKernel | dict[str, Any] | None,
    *,
    max_plot_lines: int = 8,
    max_beat_items: int = 8,
) -> str:
    """Render the kernel as a compact prompt block for downstream planners."""

    if kernel is None:
        return ""
    if isinstance(kernel, dict):
        kernel = story_design_kernel_from_dict(kernel)

    lines = [
        "## Story Design Kernel",
        f"- Reader promise: {kernel.reader_promise}",
        (
            "- Shape: "
            f"{kernel.shape.length_class} / {kernel.shape.publication_mode} / "
            f"{kernel.shape.outline_depth}"
        ),
        f"- Primary duties: {', '.join(kernel.shape.primary_duties)}",
        f"- Unique hook: {kernel.premise_contract.unique_hook}",
        f"- Core question: {kernel.premise_contract.core_question}",
        f"- Commercial pull: {kernel.premise_contract.commercial_pull}",
        f"- Change vectors: {', '.join(kernel.change_vectors)}",
    ]
    if kernel.uniqueness_constraints:
        lines.append(f"- Uniqueness constraints: {', '.join(kernel.uniqueness_constraints)}")
    if kernel.premise_contract.forbidden_defaults:
        lines.append(
            f"- Forbidden defaults: {', '.join(kernel.premise_contract.forbidden_defaults)}"
        )
    if kernel.four_causes_contract is not None:
        contract = kernel.four_causes_contract
        lines.extend(
            [
                "### Four causes contract",
                f"- Purpose/result: {contract.purpose_result}",
                f"- Material basis: {', '.join(contract.material_basis)}",
                f"- Formal pattern: {contract.formal_pattern}",
                f"- Driving forces: {', '.join(contract.driving_forces)}",
            ]
        )
        if contract.proof_criteria:
            lines.append(f"- Proof criteria: {', '.join(contract.proof_criteria)}")
    if kernel.macro_structure_contract is not None:
        contract = kernel.macro_structure_contract
        lines.extend(
            [
                "### Macro structure contract",
                f"- Structure type: {contract.structure_type}",
                f"- Mainline rule: {contract.mainline_rule}",
            ]
        )
        if contract.subline_rule:
            lines.append(f"- Subline rule: {contract.subline_rule}")
        if contract.rhythm_rule:
            lines.append(f"- Rhythm rule: {contract.rhythm_rule}")
        if contract.anti_homogeneity_rule:
            lines.append(f"- Anti-homogeneity rule: {contract.anti_homogeneity_rule}")
    if kernel.reader_desire_matrix:
        lines.append("### Reader desire matrix")
        for item in kernel.reader_desire_matrix[:6]:
            risk = f"; risk={item.risk_control}" if item.risk_control else ""
            lines.append(
                f"- {item.desire_type}: expectation={item.reader_expectation}; "
                f"payoff={item.payoff_mode}{risk}"
            )
    if kernel.event_pattern_inventory:
        lines.append("### Event pattern inventory")
        for item in kernel.event_pattern_inventory[:8]:
            anti = (
                f"; anti-repeat={item.anti_repetition_rule}"
                if item.anti_repetition_rule
                else ""
            )
            lines.append(
                f"- {item.pattern_type}: use={item.use_case}; "
                f"reader_effect={item.reader_effect}{anti}"
            )
    if kernel.worldview_kernel is not None:
        worldview = kernel.worldview_kernel
        lines.extend(
            [
                "### Worldview kernel",
                f"- Premise: {worldview.premise}",
                f"- Uniqueness principle: {worldview.uniqueness_principle}",
            ]
        )
        for invariant in worldview.invariants[:4]:
            lines.append(
                "- Invariant "
                f"{invariant.key}: {invariant.rule}; cost={invariant.violation_cost}; "
                f"use={invariant.narrative_use}"
            )
        for system in worldview.systems[:4]:
            lines.append(
                f"- System {system.name}: {system.operating_logic}; "
                f"limits={system.limits}; costs={system.costs}"
            )
        for binding in worldview.distilled_mechanism_bindings[:4]:
            state_text = (
                f"; states={', '.join(binding.state_variables[:4])}"
                if binding.state_variables
                else ""
            )
            cost_text = f"; cost={binding.required_cost}" if binding.required_cost else ""
            lines.append(
                "- Distilled mechanism "
                f"{binding.mechanism_id} ({binding.aggregate_key}/"
                f"{binding.design_role}, confidence={binding.source_confidence:.2f}): "
                f"{binding.required_project_binding}{state_text}{cost_text}"
            )
        for variable in worldview.state_variables[:4]:
            lines.append(
                "- World state "
                f"{variable.key}: type={variable.variable_type}; "
                f"current={variable.current_value}; direction={variable.desired_direction}; "
                f"triggers={', '.join(variable.change_triggers[:3])}; "
                f"failure={variable.failure_mode}"
            )
        for asset in worldview.asset_ledger[:4]:
            attention = (
                f"; attention={', '.join(asset.attention_sources[:3])}"
                if asset.attention_sources
                else ""
            )
            lines.append(
                "- World asset "
                f"{asset.key} ({asset.asset_type}): value={asset.value}; "
                f"cost={asset.cost}; exposure={asset.exposure_risk}{attention}"
            )
        for claim in worldview.authority_claims[:4]:
            conflict = (
                f"; conflict={', '.join(claim.conflict_with[:3])}"
                if claim.conflict_with
                else ""
            )
            lines.append(
                "- Authority claim "
                f"{claim.claimant} -> {claim.target}: basis={claim.claim_basis}; "
                f"legitimacy={claim.legitimacy}; escalation={claim.escalation_path}"
                f"{conflict}"
            )
        for template in worldview.scene_templates[:4]:
            lines.append(
                "- World scene template "
                f"{template.key}: {template.template_name}; use={template.use_case}; "
                f"required_change={', '.join(template.required_change[:4])}"
            )
        if worldview.anti_copy_boundaries:
            lines.append(
                "- World anti-copy boundaries: "
                f"{', '.join(worldview.anti_copy_boundaries[:8])}"
            )
        if worldview.integration_contract:
            contract = worldview.integration_contract
            lines.append(
                "- World integration: "
                f"chapter={contract.chapter_rule}; volume={contract.volume_rule}; "
                f"reveal={contract.reveal_rule}; continuity={contract.continuity_rule}"
            )

    lines.append("### Plot tree")
    for node in kernel.plot_tree[:max_plot_lines]:
        dependency = (
            f"; depends on mainline: {node.dependency_on_mainline}"
            if node.dependency_on_mainline
            else ""
        )
        lines.append(
            f"- [{node.line_type}] {node.label}: {node.current_state} -> "
            f"{node.target_state}{dependency}; removal failure: {node.failure_if_removed}"
        )

    lines.append("### Beat schedule")
    for beat in kernel.beat_schedule[:max_beat_items]:
        lines.append(
            f"- {beat.chapter_range}: {beat.duty}; change={beat.state_change}; "
            f"payoff={beat.payoff}; hook/aftereffect={beat.hook_or_aftereffect}"
        )
    return "\n".join(lines)
