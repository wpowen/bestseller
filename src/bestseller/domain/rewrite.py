from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class RewriteImpactRecord(BaseModel):
    id: UUID | None = None
    impacted_type: str = Field(min_length=1)
    impacted_id: UUID
    impact_level: str = Field(pattern="^(must|should|may)$")
    impact_score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


class RewriteImpactAnalysisResult(BaseModel):
    rewrite_task_id: UUID
    project_id: UUID
    source_chapter_number: int = Field(gt=0)
    source_scene_number: int = Field(gt=0)
    impact_count: int = Field(ge=0)
    max_impact_level: str = Field(pattern="^(must|should|may|none)$")
    impacts: list[RewriteImpactRecord] = Field(default_factory=list)


class RewriteCascadeChapterResult(BaseModel):
    chapter_number: int = Field(gt=0)
    workflow_run_id: UUID
    requires_human_review: bool = False


class RewriteCascadeResult(BaseModel):
    rewrite_task_id: UUID
    project_id: UUID
    processed_chapters: list[RewriteCascadeChapterResult] = Field(default_factory=list)
    impact_count: int = Field(ge=0)
    refreshed: bool = True
