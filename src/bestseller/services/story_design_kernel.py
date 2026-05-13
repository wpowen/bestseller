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
