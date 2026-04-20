from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from bestseller.domain.narrative import (
    AntagonistPlanRead,
    ArcBeatRead,
    ChapterContractRead,
    ClueRead,
    EmotionTrackRead,
    EndingContractRead,
    PacingCurvePointRead,
    PayoffRead,
    PlotArcRead,
    ReaderKnowledgeEntryRead,
    RelationshipEventRead,
    SceneContractRead,
    SubplotScheduleEntryRead,
)
from bestseller.domain.narrative_tree import NarrativeTreeNodeRead
from bestseller.domain.retrieval import RetrievedChunk


class RecentSceneSummary(BaseModel):
    chapter_number: int = Field(ge=1)
    scene_number: int = Field(ge=1)
    scene_title: str | None = None
    summary: str = Field(min_length=1)
    story_purpose: str | None = None
    emotion_purpose: str | None = None
    opening_lines: str | None = None
    # Last ~300 chars of the scene prose — lets the next scene writer see what
    # content was already written at the END of this scene, preventing the LLM
    # from inadvertently repeating key dialog or action that appeared mid/late.
    closing_lines: str | None = None
    # For the IMMEDIATELY preceding scene within the same chapter, we provide
    # an extended tail (last ~1000 chars) so the writer sees any key dialog or
    # action that occurred in the middle-to-end of that scene.  AI-generated
    # summaries are lossy and often omit specific quotes; this raw text is the
    # reliable anti-repetition signal.
    extended_tail: str | None = None


class TimelineEventContext(BaseModel):
    chapter_number: int | None = Field(default=None, ge=0)
    scene_number: int | None = Field(default=None, ge=0)
    event_name: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    story_time_label: str = Field(min_length=1)
    consequences: list[str] = Field(default_factory=list)
    summary: str | None = None


class ParticipantCanonFactContext(BaseModel):
    subject_label: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    chapter_number: int | None = Field(default=None, ge=0)
    scene_number: int | None = Field(default=None, ge=0)
    value: dict[str, Any] = Field(default_factory=dict)


class HardFactContext(BaseModel):
    """Single enumerable ``hard fact`` extracted from a chapter's end-state.

    Persisted in ``chapter_state_snapshots.facts`` and injected into the next
    chapter's writing prompt as a strict continuity constraint.
    """

    name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    unit: str | None = None
    kind: str = Field(min_length=1)
    subject: str | None = None
    notes: str | None = None
    source_quote: str | None = None


class ChapterStateSnapshotContext(BaseModel):
    """Frozen end-of-chapter hard-fact state injected into the next chapter."""

    chapter_number: int = Field(ge=1)
    facts: list[HardFactContext] = Field(default_factory=list)


class SceneWriterContextPacket(BaseModel):
    project_id: UUID
    project_slug: str = Field(min_length=1)
    chapter_id: UUID
    scene_id: UUID
    chapter_number: int = Field(ge=1)
    scene_number: int = Field(ge=1)
    query_text: str = Field(min_length=1)
    story_bible: dict[str, Any] = Field(default_factory=dict)
    recent_scene_summaries: list[RecentSceneSummary] = Field(default_factory=list)
    recent_timeline_events: list[TimelineEventContext] = Field(default_factory=list)
    participant_canon_facts: list[ParticipantCanonFactContext] = Field(default_factory=list)
    active_plot_arcs: list[PlotArcRead] = Field(default_factory=list)
    active_arc_beats: list[ArcBeatRead] = Field(default_factory=list)
    unresolved_clues: list[ClueRead] = Field(default_factory=list)
    planned_payoffs: list[PayoffRead] = Field(default_factory=list)
    active_emotion_tracks: list[EmotionTrackRead] = Field(default_factory=list)
    active_antagonist_plans: list[AntagonistPlanRead] = Field(default_factory=list)
    chapter_contract: ChapterContractRead | None = None
    scene_contract: SceneContractRead | None = None
    tree_context_nodes: list[NarrativeTreeNodeRead] = Field(default_factory=list)
    retrieval_chunks: list[RetrievedChunk] = Field(default_factory=list)
    hard_fact_snapshot: ChapterStateSnapshotContext | None = None
    contradiction_warnings: list[str] = Field(default_factory=list)
    participant_knowledge_states: list[dict[str, Any]] = Field(default_factory=list)
    arc_summaries: list[dict[str, Any]] = Field(default_factory=list)
    world_snapshot: dict[str, Any] | None = None

    # ── Phase-1 wiring: previously orphaned narrative models ──
    pacing_target: PacingCurvePointRead | None = None
    subplot_schedule: list[SubplotScheduleEntryRead] = Field(default_factory=list)
    ending_contract: EndingContractRead | None = None
    reader_knowledge_entries: list[ReaderKnowledgeEntryRead] = Field(default_factory=list)
    relationship_milestones: list[RelationshipEventRead] = Field(default_factory=list)

    # ── Phase-2 wiring: structure template beat ──
    structure_beat_name: str | None = None
    structure_beat_description: str | None = None

    # ── Phase-3 wiring: Swain scene/sequel pattern ──
    swain_pattern: str | None = None
    scene_skeleton: dict[str, str] | None = None

    # ── Phase-5 wiring: genre obligatory scenes due ──
    genre_obligations_due: list[dict[str, str]] = Field(default_factory=list)

    # ── Phase-6 wiring: foreshadowing gap warning ──
    foreshadowing_gap_warning: str | None = None

    # ── Character identity constraints (Tier 0 — never dropped) ──
    identity_constraint_block: str | None = None
    # ── Identity registry for review scoring (list of CharacterIdentity) ──
    identity_registry: list[Any] = Field(default_factory=list)

    # ── Overused phrase avoidance block (injected after chapter completion) ──
    overused_phrase_block: str | None = None

    # ── Genre-specific constraint block ──
    genre_constraint_block: str | None = None

    # ── Opening diversity block: recent chapter openings to avoid repeating ──
    opening_diversity_block: str | None = None

    # ── Stage A: conflict diversity block (per-scene, 4-axis taxonomy) ──
    conflict_diversity_block: str | None = None

    # ── Stage B: scene-purpose diversity block (24 purpose taxonomy) ──
    scene_purpose_diversity_block: str | None = None

    # ── Stage B: environment 7-d diversity block ──
    env_diversity_block: str | None = None

    # ── Stage C: POV character arc + inner structure (lie/want/need/ghost) ──
    arc_beat_block: str | None = None

    # ── Stage C: 5-layer thinking contract (SENSATION→RATIONALIZATION) ──
    five_layer_block: str | None = None

    # ── Stage D: cliffhanger diversity (7 hook types, forbid same-type runs) ──
    cliffhanger_diversity_block: str | None = None

    # ── Stage D: chapter tension target + flat-rhythm warning ──
    tension_target_block: str | None = None

    # ── Stage B+: location ledger (same-location reframe + visit cap) ──
    location_ledger_block: str | None = None

    # ── Scene scope isolation: enforce scene-only scope + earlier-scene recaps ──
    # Tells the writer which beats/content belong to THIS scene only and which
    # earlier scenes in the chapter are already written (must not be rewritten
    # or paraphrased in this scene).
    scene_scope_isolation_block: str | None = None

    # ── Pipeline-level duplication findings (broad scope) ──
    # Pre-computed findings from check_scene_duplication in pipelines.py.
    # Forwarded to review_scene_draft so cross-project matches flow into
    # duplication_score + findings without re-running the scan.
    pipeline_duplication_findings: list[dict[str, Any]] = Field(default_factory=list)


class ChapterSceneContext(BaseModel):
    scene_number: int = Field(ge=1)
    title: str | None = None
    scene_type: str = Field(min_length=1)
    status: str = Field(min_length=1)
    participants: list[str] = Field(default_factory=list)
    story_purpose: str | None = None
    emotion_purpose: str | None = None
    summary: str | None = None


class ChapterWriterContextPacket(BaseModel):
    project_id: UUID
    project_slug: str = Field(min_length=1)
    chapter_id: UUID
    chapter_number: int = Field(ge=1)
    query_text: str = Field(min_length=1)
    chapter_goal: str = Field(min_length=1)
    story_bible: dict[str, Any] = Field(default_factory=dict)
    chapter_scenes: list[ChapterSceneContext] = Field(default_factory=list)
    previous_scene_summaries: list[RecentSceneSummary] = Field(default_factory=list)
    recent_timeline_events: list[TimelineEventContext] = Field(default_factory=list)
    active_plot_arcs: list[PlotArcRead] = Field(default_factory=list)
    active_arc_beats: list[ArcBeatRead] = Field(default_factory=list)
    unresolved_clues: list[ClueRead] = Field(default_factory=list)
    planned_payoffs: list[PayoffRead] = Field(default_factory=list)
    active_emotion_tracks: list[EmotionTrackRead] = Field(default_factory=list)
    active_antagonist_plans: list[AntagonistPlanRead] = Field(default_factory=list)
    chapter_contract: ChapterContractRead | None = None
    tree_context_nodes: list[NarrativeTreeNodeRead] = Field(default_factory=list)
    retrieval_chunks: list[RetrievedChunk] = Field(default_factory=list)
    hard_fact_snapshot: ChapterStateSnapshotContext | None = None

    # ── Phase-1 wiring: previously orphaned narrative models ──
    pacing_target: PacingCurvePointRead | None = None
    subplot_schedule: list[SubplotScheduleEntryRead] = Field(default_factory=list)
    ending_contract: EndingContractRead | None = None
    reader_knowledge_entries: list[ReaderKnowledgeEntryRead] = Field(default_factory=list)
    relationship_milestones: list[RelationshipEventRead] = Field(default_factory=list)

    # ── Phase-2 wiring: structure template beat ──
    structure_beat_name: str | None = None
    structure_beat_description: str | None = None

    # ── Phase-5 wiring: genre obligatory scenes due ──
    genre_obligations_due: list[dict[str, str]] = Field(default_factory=list)

    # ── Phase-6 wiring: foreshadowing gap warning ──
    foreshadowing_gap_warning: str | None = None
