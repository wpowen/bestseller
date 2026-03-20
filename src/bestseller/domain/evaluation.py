from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BenchmarkExpectation(BaseModel):
    min_overall_score: float = Field(default=0.6, ge=0, le=1)
    require_export: bool = True
    require_project_markdown: bool = True
    min_chapter_count: int = Field(default=1, ge=1)
    allowed_final_verdicts: list[str] = Field(default_factory=lambda: ["pass", "attention"])
    required_arc_types: list[str] = Field(default_factory=list)
    require_emotion_track: bool = False
    require_antagonist_plan: bool = False


class BenchmarkCaseSpec(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    genre: str = Field(min_length=1, max_length=100)
    sub_genre: str | None = Field(default=None, max_length=100)
    audience: str | None = Field(default=None, max_length=200)
    target_word_count: int = Field(gt=0)
    target_chapters: int = Field(gt=0)
    premise: str = Field(min_length=1)
    expectations: BenchmarkExpectation = Field(default_factory=BenchmarkExpectation)


class BenchmarkSuiteSpec(BaseModel):
    suite_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    cases: list[BenchmarkCaseSpec] = Field(default_factory=list)


class BenchmarkSuiteCatalogEntry(BaseModel):
    suite_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    path: str = Field(min_length=1)
    case_count: int = Field(ge=0)
    case_ids: list[str] = Field(default_factory=list)


class BenchmarkCheckResult(BaseModel):
    check_name: str = Field(min_length=1, max_length=64)
    passed: bool
    actual: object | None = None
    expected: object | None = None
    message: str | None = None


class BenchmarkCaseResult(BaseModel):
    suite_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    project_id: UUID
    project_slug: str = Field(min_length=1)
    chapter_count: int = Field(ge=0)
    final_verdict: str | None = None
    requires_human_review: bool = False
    export_status: str = Field(min_length=1)
    output_dir: str | None = None
    output_files: list[str] = Field(default_factory=list)
    review_overall_score: float = Field(ge=0, le=1)
    review_findings_count: int = Field(ge=0)
    plot_arc_types: list[str] = Field(default_factory=list)
    emotion_track_count: int = Field(ge=0)
    antagonist_plan_count: int = Field(ge=0)
    checks: list[BenchmarkCheckResult] = Field(default_factory=list)
    passed: bool = False


class BenchmarkSuiteRunResult(BaseModel):
    suite_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    started_at: datetime
    completed_at: datetime
    report_path: str | None = None
    case_results: list[BenchmarkCaseResult] = Field(default_factory=list)
    passed_case_count: int = Field(ge=0)
    failed_case_count: int = Field(ge=0)
