from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
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
    title: str = Field(min_length=1, max_length=4000)
    genre: str = Field(min_length=1, max_length=4000)
    sub_genre: str | None = Field(default=None, max_length=4000)
    audience: str | None = Field(default=None, max_length=4000)
    target_word_count: int = Field(gt=0)
    target_chapters: int = Field(gt=0)
    premise: str = Field(min_length=1)
    expectations: BenchmarkExpectation = Field(default_factory=BenchmarkExpectation)


class BenchmarkSuiteSpec(BaseModel):
    suite_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=4000)
    description: str | None = None
    cases: list[BenchmarkCaseSpec] = Field(default_factory=list)


class BenchmarkSuiteCatalogEntry(BaseModel):
    suite_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=4000)
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
    title: str = Field(min_length=1, max_length=4000)
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
    title: str = Field(min_length=1, max_length=4000)
    started_at: datetime
    completed_at: datetime
    report_path: str | None = None
    case_results: list[BenchmarkCaseResult] = Field(default_factory=list)
    passed_case_count: int = Field(ge=0)
    failed_case_count: int = Field(ge=0)


ModelPilotRoleName = Literal["planner", "writer", "critic", "summarizer", "editor"]


class ModelPilotRoleOverride(BaseModel):
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, ge=1)
    stream: bool | None = None
    n_candidates: int | None = Field(default=None, ge=1)
    api_base: str | None = None
    api_key_env: str | None = None
    model_override: str | None = None
    rate_limit_fallback_model: str | None = None
    rate_limit_fallback_api_base: str | None = None
    rate_limit_fallback_api_key_env: str | None = None
    rate_limit_fallback_stream: bool | None = None

    def to_settings_update(self) -> dict[str, Any]:
        """Return only YAML-provided fields, preserving explicit null clears."""
        return {field: getattr(self, field) for field in self.model_fields_set}


class ModelPilotVariantSpec(BaseModel):
    variant_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=4000)
    enabled: bool = True
    disable_rate_limit_fallback: bool = True
    roles: dict[ModelPilotRoleName, ModelPilotRoleOverride] = Field(default_factory=dict)
    notes: str | None = None


class ModelPilotQualityExpectation(BaseModel):
    require_no_fallback: bool = True
    min_resolution_completeness: float = Field(default=0.8, ge=0, le=1)
    require_sample_quality_parity: bool = False
    sample_quality_min_chapter_count: int = Field(default=30, ge=1)
    sample_quality_min_review_score: float = Field(default=0.82, ge=0, le=1)
    sample_quality_min_scorecard_quality: float = Field(default=80.0, ge=0)
    sample_quality_min_reference_distance: float = Field(default=0.72, ge=0, le=1)


class ModelPilotSpec(BaseModel):
    pilot_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=4000)
    description: str | None = None
    book: BenchmarkCaseSpec
    quality: ModelPilotQualityExpectation = Field(default_factory=ModelPilotQualityExpectation)
    variants: list[ModelPilotVariantSpec] = Field(default_factory=list)


class ModelPilotCatalogEntry(BaseModel):
    pilot_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=4000)
    description: str | None = None
    path: str = Field(min_length=1)
    enabled_variant_count: int = Field(ge=0)
    variant_ids: list[str] = Field(default_factory=list)


class ModelUsageSummary(BaseModel):
    request_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_latency_ms: int = Field(ge=0)
    model_counts: dict[str, int] = Field(default_factory=dict)
    provider_counts: dict[str, int] = Field(default_factory=dict)
    role_counts: dict[str, int] = Field(default_factory=dict)


def empty_model_usage_summary() -> ModelUsageSummary:
    return ModelUsageSummary(
        request_count=0,
        fallback_count=0,
        total_input_tokens=0,
        total_output_tokens=0,
        total_latency_ms=0,
    )


class ModelPilotVariantResult(BaseModel):
    pilot_id: str = Field(min_length=1, max_length=64)
    variant_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=4000)
    project_id: UUID | None = None
    project_slug: str = Field(min_length=1)
    chapter_count: int = Field(ge=0)
    final_verdict: str | None = None
    requires_human_review: bool = False
    export_status: str = Field(min_length=1)
    output_dir: str | None = None
    output_files: list[str] = Field(default_factory=list)
    review_overall_score: float | None = Field(default=None, ge=0, le=1)
    review_resolution_completeness: float | None = Field(default=None, ge=0, le=1)
    review_findings_count: int = Field(ge=0)
    usage: ModelUsageSummary = Field(default_factory=empty_model_usage_summary)
    sample_quality_parity: dict[str, Any] | None = None
    checks: list[BenchmarkCheckResult] = Field(default_factory=list)
    passed: bool = False
    error: str | None = None


class ModelPilotRunResult(BaseModel):
    pilot_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=4000)
    started_at: datetime
    completed_at: datetime
    report_path: str | None = None
    variant_results: list[ModelPilotVariantResult] = Field(default_factory=list)
    passed_variant_count: int = Field(ge=0)
    failed_variant_count: int = Field(ge=0)
