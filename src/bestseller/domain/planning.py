from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from bestseller.domain.enums import ArtifactType


class PlanningArtifactCreate(BaseModel):
    artifact_type: ArtifactType
    content: Any = Field(default_factory=dict)
    scope_ref_id: UUID | None = None
    notes: str | None = None


class PlanningArtifactRecord(BaseModel):
    artifact_type: ArtifactType
    artifact_id: UUID
    version_no: int = Field(ge=1)


class PlanningArtifactSummary(BaseModel):
    artifact_id: UUID
    artifact_type: ArtifactType
    version_no: int = Field(ge=1)
    scope_ref_id: UUID | None = None
    status: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    created_at: datetime
    notes: str | None = None


class PlanningArtifactDetail(PlanningArtifactSummary):
    content: Any = Field(default_factory=dict)


class NovelPlanningResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    premise: str = Field(min_length=1)
    artifacts: list[PlanningArtifactRecord] = Field(default_factory=list)
    volume_count: int = Field(ge=0)
    chapter_count: int = Field(ge=0)
    llm_run_ids: list[UUID] = Field(default_factory=list)


class AutowriteResult(BaseModel):
    project_id: UUID
    project_slug: str = Field(min_length=1)
    planning_workflow_run_id: UUID
    story_bible_workflow_run_id: UUID | None = None
    outline_workflow_run_id: UUID | None = None
    narrative_graph_workflow_run_id: UUID | None = None
    narrative_tree_workflow_run_id: UUID | None = None
    project_workflow_run_id: UUID
    repair_workflow_run_id: UUID | None = None
    repair_attempted: bool = False
    review_report_id: UUID | None = None
    quality_score_id: UUID | None = None
    export_artifact_id: UUID | None = None
    output_path: str | None = None
    output_dir: str | None = None
    output_files: list[str] = Field(default_factory=list)
    export_status: str = Field(default="not_exported", min_length=1)
    chapter_count: int = Field(ge=0)
    final_verdict: str | None = None
    requires_human_review: bool = False
