from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class SceneKnowledgeRefreshResult(BaseModel):
    project_id: UUID
    chapter_id: UUID
    scene_id: UUID
    chapter_number: int = Field(gt=0)
    scene_number: int = Field(gt=0)
    canon_fact_ids: list[UUID] = Field(default_factory=list)
    timeline_event_ids: list[UUID] = Field(default_factory=list)
    canon_facts_created: int = Field(default=0, ge=0)
    canon_facts_reused: int = Field(default=0, ge=0)
    timeline_events_created: int = Field(default=0, ge=0)
    timeline_events_reused: int = Field(default=0, ge=0)
    summary_text: str = Field(min_length=1)
    llm_run_id: UUID | None = None
