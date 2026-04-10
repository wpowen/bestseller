from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ScenePipelineResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    chapter_id: UUID
    scene_id: UUID
    chapter_number: int = Field(gt=0)
    scene_number: int = Field(gt=0)
    current_draft_id: UUID
    current_draft_version_no: int = Field(gt=0)
    final_verdict: str = Field(min_length=1, max_length=16)
    review_report_id: UUID | None = None
    quality_score_id: UUID | None = None
    rewrite_task_id: UUID | None = None
    review_iterations: int = Field(ge=0)
    rewrite_iterations: int = Field(ge=0)
    canon_fact_count: int = Field(default=0, ge=0)
    timeline_event_count: int = Field(default=0, ge=0)
    reached_revision_limit: bool = False
    requires_human_review: bool = False
    llm_run_ids: list[UUID] = Field(default_factory=list)


class ChapterPipelineSceneSummary(BaseModel):
    scene_number: int = Field(gt=0)
    workflow_run_id: UUID
    final_verdict: str = Field(min_length=1, max_length=16)
    rewrite_iterations: int = Field(ge=0)
    canon_fact_count: int = Field(default=0, ge=0)
    timeline_event_count: int = Field(default=0, ge=0)
    requires_human_review: bool = False
    current_draft_version_no: int = Field(gt=0)


class ChapterPipelineResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    chapter_id: UUID
    chapter_number: int = Field(gt=0)
    scene_results: list[ChapterPipelineSceneSummary] = Field(default_factory=list)
    chapter_draft_id: UUID | None = None
    chapter_draft_version_no: int | None = None
    final_verdict: str | None = None
    review_report_id: UUID | None = None
    quality_score_id: UUID | None = None
    rewrite_task_id: UUID | None = None
    chapter_review_iterations: int = Field(default=0, ge=0)
    chapter_rewrite_iterations: int = Field(default=0, ge=0)
    export_artifact_id: UUID | None = None
    output_path: str | None = None
    requires_human_review: bool = False


class ProjectPipelineChapterSummary(BaseModel):
    chapter_number: int = Field(gt=0)
    workflow_run_id: UUID
    chapter_draft_version_no: int | None = None
    export_artifact_id: UUID | None = None
    requires_human_review: bool = False
    approved_scene_count: int = Field(default=0, ge=0)


class ProjectPipelineResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    project_slug: str = Field(min_length=1)
    chapter_results: list[ProjectPipelineChapterSummary] = Field(default_factory=list)
    story_bible_workflow_run_id: UUID | None = None
    materialization_workflow_run_id: UUID | None = None
    narrative_graph_workflow_run_id: UUID | None = None
    narrative_tree_workflow_run_id: UUID | None = None
    review_report_id: UUID | None = None
    quality_score_id: UUID | None = None
    final_verdict: str | None = None
    export_artifact_id: UUID | None = None
    output_path: str | None = None
    requires_human_review: bool = False


class ProjectRepairChapterSummary(BaseModel):
    chapter_number: int = Field(gt=0)
    workflow_run_id: UUID
    source_task_ids: list[UUID] = Field(default_factory=list)
    requires_human_review: bool = False


class ProjectRepairResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    project_slug: str = Field(min_length=1)
    pending_rewrite_task_count: int = Field(ge=0)
    superseded_task_count: int = Field(ge=0)
    processed_chapters: list[ProjectRepairChapterSummary] = Field(default_factory=list)
    review_report_id: UUID | None = None
    quality_score_id: UUID | None = None
    final_verdict: str | None = None
    export_artifact_id: UUID | None = None
    output_path: str | None = None
    remaining_pending_rewrite_count: int = Field(ge=0)
    requires_human_review: bool = False
