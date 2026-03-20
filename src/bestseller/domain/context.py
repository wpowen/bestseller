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
    PayoffRead,
    PlotArcRead,
    SceneContractRead,
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
