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


class WorldStateVariable(BaseModel, frozen=True):
    """A measurable world variable that should change through planning."""

    key: str = Field(min_length=1)
    variable_type: str = Field(min_length=1)
    current_value: str = ""
    desired_direction: str = Field(min_length=1)
    change_triggers: list[str] = Field(min_length=1)
    failure_mode: str = Field(min_length=1)
    source_mechanism_ids: list[str] = Field(default_factory=list)


class WorldAssetLedgerItem(BaseModel, frozen=True):
    """A world asset whose value must carry cost, exposure, and attention."""

    key: str = Field(min_length=1)
    asset_type: str = Field(min_length=1)
    value: str = Field(min_length=1)
    cost: str = Field(min_length=1)
    exposure_risk: str = Field(min_length=1)
    attention_sources: list[str] = Field(default_factory=list)
    source_mechanism_ids: list[str] = Field(default_factory=list)


class WorldAuthorityClaim(BaseModel, frozen=True):
    """A legitimacy claim by a faction or institution over a world target."""

    claimant: str = Field(min_length=1)
    target: str = Field(min_length=1)
    claim_basis: str = Field(min_length=1)
    legitimacy: str = Field(min_length=1)
    conflict_with: list[str] = Field(default_factory=list)
    escalation_path: str = Field(min_length=1)


class WorldSceneTemplateBinding(BaseModel, frozen=True):
    """A distilled scene pattern used to make world rules executable."""

    key: str = Field(min_length=1)
    template_name: str = Field(min_length=1)
    use_case: str = Field(min_length=1)
    required_change: list[str] = Field(min_length=1)
    source_mechanism_ids: list[str] = Field(default_factory=list)


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


class StructureStrategy(BaseModel, frozen=True):
    """How the book converts its premise into repeated chapter movement."""

    macro_strategy: str = Field(min_length=1)
    chapter_engine: str = Field(min_length=1)
    pacing_rule: str = Field(min_length=1)
    freshness_rule: str = Field(min_length=1)


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
    structure_strategy: StructureStrategy
    plot_tree: list[PlotTreeNode] = Field(min_length=1)
    beat_schedule: list[BeatScheduleItem] = Field(min_length=1)
    change_vectors: list[str] = Field(min_length=1)
    uniqueness_constraints: list[str] = Field(default_factory=list)
    reverse_outline_status: ReverseOutlineStatus = "not_started"

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
