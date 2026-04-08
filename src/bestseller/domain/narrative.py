from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class PlotArcRead(BaseModel):
    id: UUID
    arc_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=4000)
    arc_type: str = Field(min_length=1, max_length=64)
    promise: str = Field(min_length=1)
    core_question: str = Field(min_length=1)
    target_payoff: str | None = None
    status: str = Field(min_length=1, max_length=32)
    scope_level: str = Field(min_length=1, max_length=32)
    scope_volume_number: int | None = Field(default=None, ge=1)
    scope_chapter_number: int | None = Field(default=None, ge=1)
    description: str | None = None


class ArcBeatRead(BaseModel):
    id: UUID
    plot_arc_id: UUID
    arc_code: str = Field(min_length=1, max_length=64)
    beat_order: int = Field(ge=1)
    scope_level: str = Field(min_length=1, max_length=32)
    scope_volume_number: int | None = Field(default=None, ge=1)
    scope_chapter_number: int | None = Field(default=None, ge=1)
    scope_scene_number: int | None = Field(default=None, ge=1)
    beat_kind: str = Field(min_length=1, max_length=64)
    title: str | None = None
    summary: str = Field(min_length=1)
    emotional_shift: str | None = None
    information_release: str | None = None
    expected_payoff: str | None = None
    status: str = Field(min_length=1, max_length=32)


class ClueRead(BaseModel):
    id: UUID
    clue_code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=4000)
    clue_type: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    plot_arc_id: UUID | None = None
    planted_in_volume_number: int | None = Field(default=None, ge=1)
    planted_in_chapter_number: int | None = Field(default=None, ge=1)
    planted_in_scene_number: int | None = Field(default=None, ge=1)
    expected_payoff_by_volume_number: int | None = Field(default=None, ge=1)
    expected_payoff_by_chapter_number: int | None = Field(default=None, ge=1)
    expected_payoff_by_scene_number: int | None = Field(default=None, ge=1)
    actual_paid_off_chapter_number: int | None = Field(default=None, ge=1)
    actual_paid_off_scene_number: int | None = Field(default=None, ge=1)
    reveal_guard: str | None = None
    status: str = Field(min_length=1, max_length=32)


class PayoffRead(BaseModel):
    id: UUID
    payoff_code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=4000)
    description: str = Field(min_length=1)
    plot_arc_id: UUID | None = None
    source_clue_id: UUID | None = None
    target_volume_number: int | None = Field(default=None, ge=1)
    target_chapter_number: int | None = Field(default=None, ge=1)
    target_scene_number: int | None = Field(default=None, ge=1)
    actual_chapter_number: int | None = Field(default=None, ge=1)
    actual_scene_number: int | None = Field(default=None, ge=1)
    status: str = Field(min_length=1, max_length=32)


class ChapterContractRead(BaseModel):
    id: UUID
    chapter_id: UUID
    chapter_number: int = Field(ge=1)
    contract_summary: str = Field(min_length=1)
    opening_state: dict[str, object] = Field(default_factory=dict)
    core_conflict: str | None = None
    emotional_shift: str | None = None
    information_release: str | None = None
    closing_hook: str | None = None
    primary_arc_codes: list[str] = Field(default_factory=list)
    supporting_arc_codes: list[str] = Field(default_factory=list)
    active_arc_beat_ids: list[str] = Field(default_factory=list)
    planted_clue_codes: list[str] = Field(default_factory=list)
    due_payoff_codes: list[str] = Field(default_factory=list)


class SceneContractRead(BaseModel):
    id: UUID
    chapter_id: UUID
    scene_card_id: UUID
    chapter_number: int = Field(ge=1)
    scene_number: int = Field(ge=1)
    contract_summary: str = Field(min_length=1)
    entry_state: dict[str, object] = Field(default_factory=dict)
    exit_state: dict[str, object] = Field(default_factory=dict)
    core_conflict: str | None = None
    emotional_shift: str | None = None
    information_release: str | None = None
    tail_hook: str | None = None
    thematic_task: str | None = None
    dramatic_irony_intent: str | None = None
    transition_type: str | None = Field(default=None, max_length=32)  # hard_cut / time_skip / parallel_crosscut / flashback
    subplot_codes: list[str] = Field(default_factory=list)
    arc_codes: list[str] = Field(default_factory=list)
    arc_beat_ids: list[str] = Field(default_factory=list)
    planted_clue_codes: list[str] = Field(default_factory=list)
    payoff_codes: list[str] = Field(default_factory=list)


class EmotionTrackRead(BaseModel):
    id: UUID
    track_code: str = Field(min_length=1, max_length=64)
    track_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=4000)
    character_a_label: str = Field(min_length=1, max_length=4000)
    character_b_label: str = Field(min_length=1, max_length=4000)
    relationship_type: str | None = None
    summary: str = Field(min_length=1)
    desired_payoff: str | None = None
    trust_level: float = Field(ge=0, le=1)
    attraction_level: float = Field(ge=0, le=1)
    distance_level: float = Field(ge=0, le=1)
    conflict_level: float = Field(ge=0, le=1)
    intimacy_stage: str = Field(min_length=1, max_length=64)
    last_shift_chapter_number: int | None = Field(default=None, ge=1)
    status: str = Field(min_length=1, max_length=32)


class AntagonistPlanRead(BaseModel):
    id: UUID
    plan_code: str = Field(min_length=1, max_length=64)
    antagonist_character_id: UUID | None = None
    antagonist_label: str = Field(min_length=1, max_length=4000)
    title: str = Field(min_length=1, max_length=4000)
    threat_type: str = Field(min_length=1, max_length=64)
    goal: str = Field(min_length=1)
    current_move: str = Field(min_length=1)
    next_countermove: str = Field(min_length=1)
    escalation_condition: str | None = None
    reveal_timing: str | None = None
    scope_volume_number: int | None = Field(default=None, ge=1)
    target_chapter_number: int | None = Field(default=None, ge=1)
    pressure_level: float = Field(ge=0, le=1)
    status: str = Field(min_length=1, max_length=32)


class ThemeArcRead(BaseModel):
    """Tracks a thematic thread through the novel."""

    id: UUID
    theme_code: str = Field(min_length=1, max_length=64)
    theme_statement: str = Field(min_length=1)
    symbol_set: list[str] = Field(default_factory=list)
    evolution_stages: list[str] = Field(default_factory=list)  # 引入→质疑→深化→升华
    current_stage: str = Field(default="introduced", min_length=1, max_length=32)
    status: str = Field(default="active", min_length=1, max_length=32)


class MotifPlacementRead(BaseModel):
    """Ledger entry for a symbolic motif placement (analogous to Clue/Payoff)."""

    id: UUID
    theme_arc_id: UUID
    motif_label: str = Field(min_length=1, max_length=4000)
    placement_type: str = Field(min_length=1, max_length=32)  # plant / echo / transform / resolve
    volume_number: int | None = Field(default=None, ge=1)
    chapter_number: int | None = Field(default=None, ge=1)
    scene_number: int | None = Field(default=None, ge=1)
    description: str | None = None
    status: str = Field(default="planned", min_length=1, max_length=32)


class SubplotScheduleEntryRead(BaseModel):
    """Per-chapter subplot prominence plan."""

    id: UUID
    plot_arc_id: UUID
    arc_code: str = Field(min_length=1, max_length=64)
    chapter_number: int = Field(ge=1)
    prominence: str = Field(min_length=1, max_length=16)  # primary / secondary / mention / dormant
    notes: str | None = None


class RelationshipEventRead(BaseModel):
    """A specific event that materially changed a relationship."""

    id: UUID
    character_a_label: str = Field(min_length=1, max_length=4000)
    character_b_label: str = Field(min_length=1, max_length=4000)
    chapter_number: int = Field(ge=1)
    scene_number: int | None = Field(default=None, ge=1)
    event_description: str = Field(min_length=1)
    relationship_change: str = Field(min_length=1)
    is_milestone: bool = False


class ReaderKnowledgeEntryRead(BaseModel):
    """Tracks what the reader knows at a given chapter that characters may not."""

    id: UUID
    chapter_number: int = Field(ge=1)
    knowledge_item: str = Field(min_length=1)
    audience: str = Field(min_length=1, max_length=16)  # reader_only / character_only / both
    source_clue_code: str | None = None


class EndingContractRead(BaseModel):
    """Checklist for ensuring a satisfying ending."""

    id: UUID
    arcs_to_resolve: list[str] = Field(default_factory=list)
    clues_to_payoff: list[str] = Field(default_factory=list)
    relationships_to_close: list[str] = Field(default_factory=list)
    thematic_final_expression: str | None = None
    denouement_plan: str | None = None
    status: str = Field(default="planned", min_length=1, max_length=32)


class PacingCurvePointRead(BaseModel):
    """A single point on the tension curve for a chapter."""

    id: UUID | None = None
    chapter_number: int = Field(ge=1)
    tension_level: float = Field(ge=0, le=10)
    scene_type_plan: str | None = None  # e.g. introspection, conflict, aftermath
    notes: str | None = None


class NarrativeGraphMaterializationResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    plot_arc_count: int = Field(default=0, ge=0)
    arc_beat_count: int = Field(default=0, ge=0)
    clue_count: int = Field(default=0, ge=0)
    payoff_count: int = Field(default=0, ge=0)
    chapter_contract_count: int = Field(default=0, ge=0)
    scene_contract_count: int = Field(default=0, ge=0)
    emotion_track_count: int = Field(default=0, ge=0)
    antagonist_plan_count: int = Field(default=0, ge=0)
    theme_arc_count: int = Field(default=0, ge=0)
    motif_placement_count: int = Field(default=0, ge=0)
    subplot_schedule_count: int = Field(default=0, ge=0)
    relationship_event_count: int = Field(default=0, ge=0)
    reader_knowledge_count: int = Field(default=0, ge=0)
    ending_contract_count: int = Field(default=0, ge=0)
    pacing_curve_point_count: int = Field(default=0, ge=0)
    source_artifact_ids: dict[str, UUID] = Field(default_factory=dict)


class NarrativeOverview(BaseModel):
    project_id: UUID
    project_slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    plot_arcs: list[PlotArcRead] = Field(default_factory=list)
    arc_beats: list[ArcBeatRead] = Field(default_factory=list)
    clues: list[ClueRead] = Field(default_factory=list)
    payoffs: list[PayoffRead] = Field(default_factory=list)
    chapter_contracts: list[ChapterContractRead] = Field(default_factory=list)
    scene_contracts: list[SceneContractRead] = Field(default_factory=list)
    emotion_tracks: list[EmotionTrackRead] = Field(default_factory=list)
    antagonist_plans: list[AntagonistPlanRead] = Field(default_factory=list)
    theme_arcs: list[ThemeArcRead] = Field(default_factory=list)
    motif_placements: list[MotifPlacementRead] = Field(default_factory=list)
    subplot_schedule: list[SubplotScheduleEntryRead] = Field(default_factory=list)
    relationship_events: list[RelationshipEventRead] = Field(default_factory=list)
    reader_knowledge: list[ReaderKnowledgeEntryRead] = Field(default_factory=list)
    ending_contract: EndingContractRead | None = None
    pacing_curve: list[PacingCurvePointRead] = Field(default_factory=list)
