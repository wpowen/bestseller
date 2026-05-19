from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from bestseller.domain.evaluation import (
    BenchmarkCaseSpec,
    BenchmarkCheckResult,
    ModelPilotCatalogEntry,
    ModelPilotRunResult,
    ModelPilotSpec,
    ModelPilotVariantResult,
    ModelPilotVariantSpec,
    ModelUsageSummary,
)
from bestseller.domain.project import ProjectCreate
from bestseller.infra.db.models import LlmRunModel, ProjectModel, WorkflowRunModel
from bestseller.services.sample_quality_parity_gate import (
    SampleQualityParityReport,
    SampleQualityParityThresholds,
    evaluate_sample_quality_parity,
)
from bestseller.services.consistency import review_project_consistency
from bestseller.services.pipelines import run_autowrite_pipeline
from bestseller.settings import AppSettings

ModelPilotProgressCallback = Callable[[str, dict[str, Any] | None], None]

_ROOT_DIR = Path(__file__).resolve().parents[3]
_MODEL_PILOT_DIR = _ROOT_DIR / "examples" / "model_pilots"
_BUILTIN_PILOTS: dict[str, Path] = {
    "short-complete-30": _MODEL_PILOT_DIR / "short_complete_30.yaml",
}
_SLUG_CHARS_RE = re.compile(r"[^a-z0-9_-]+")


def _emit_progress(
    progress: ModelPilotProgressCallback | None,
    stage: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if progress is None:
        return
    progress(stage, payload)


def _load_pilot_file(path: Path) -> ModelPilotSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ModelPilotSpec.model_validate(data)


def list_model_pilots() -> list[ModelPilotCatalogEntry]:
    entries: list[ModelPilotCatalogEntry] = []
    for pilot_id, path in sorted(_BUILTIN_PILOTS.items()):
        spec = _load_pilot_file(path)
        entries.append(
            ModelPilotCatalogEntry(
                pilot_id=pilot_id,
                title=spec.title,
                description=spec.description,
                path=str(path),
                enabled_variant_count=sum(1 for variant in spec.variants if variant.enabled),
                variant_ids=[variant.variant_id for variant in spec.variants],
            )
        )
    return entries


def load_model_pilot(
    pilot_id: str = "short-complete-30",
    *,
    pilot_file: Path | None = None,
) -> ModelPilotSpec:
    if pilot_file is not None:
        return _load_pilot_file(pilot_file)
    if pilot_id not in _BUILTIN_PILOTS:
        available = ", ".join(sorted(_BUILTIN_PILOTS))
        raise ValueError(f"Unknown model pilot '{pilot_id}'. Available: {available}")
    return _load_pilot_file(_BUILTIN_PILOTS[pilot_id])


def apply_model_pilot_variant(
    settings: AppSettings,
    variant: ModelPilotVariantSpec,
) -> AppSettings:
    """Return a settings copy with one pilot model variant applied."""
    llm_settings = settings.llm.model_copy(deep=True)
    for role_name, override in variant.roles.items():
        role_settings = getattr(llm_settings, role_name)
        setattr(
            llm_settings,
            role_name,
            role_settings.model_copy(update=override.to_settings_update()),
        )
    if variant.disable_rate_limit_fallback:
        llm_settings.retry = llm_settings.retry.model_copy(
            update={"rate_limit_fallback_enabled": False}
        )
    return settings.model_copy(update={"llm": llm_settings})


def _selected_variants(
    spec: ModelPilotSpec,
    variant_ids: Iterable[str] | None,
    *,
    include_disabled: bool = False,
) -> list[ModelPilotVariantSpec]:
    requested = {variant_id for variant_id in (variant_ids or []) if variant_id}
    if requested:
        selected = [variant for variant in spec.variants if variant.variant_id in requested]
        missing = sorted(requested - {variant.variant_id for variant in selected})
        if missing:
            raise ValueError(f"Unknown model pilot variant(s): {', '.join(missing)}")
        return selected
    if include_disabled:
        return list(spec.variants)
    return [variant for variant in spec.variants if variant.enabled]


def _slug_piece(value: str) -> str:
    normalized = _SLUG_CHARS_RE.sub("-", value.strip().lower()).strip("-")
    return normalized or "pilot"


def _variant_project_slug(
    *,
    slug_prefix: str,
    variant_id: str,
    started_at: datetime,
) -> str:
    suffix = started_at.strftime("%Y%m%d%H%M%S")
    base = _slug_piece(f"{slug_prefix}-{variant_id}")
    limit = max(3, 64 - len(suffix) - 1)
    base = base[:limit].strip("-_") or "pilot"
    return f"{base}-{suffix}"


def _check_result(
    check_name: str,
    *,
    passed: bool,
    actual: object | None,
    expected: object | None,
    message: str,
) -> BenchmarkCheckResult:
    return BenchmarkCheckResult(
        check_name=check_name,
        passed=passed,
        actual=actual,
        expected=expected,
        message=message,
    )


def _as_mapping(value: object | None) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, Mapping) else {}
    if hasattr(value, "to_dict"):
        dumped = value.to_dict()
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _get_value(value: object | None, key: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _nested_value(value: object | None, path: tuple[str, ...]) -> object | None:
    current = value
    for key in path:
        current = _get_value(current, key)
        if current is None:
            return None
    return current


def _float_or_none(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_mapping(
    sources: Iterable[object | None],
    paths: Iterable[tuple[str, ...]],
) -> Mapping[str, Any] | None:
    for source in sources:
        for path in paths:
            payload = _as_mapping(_nested_value(source, path))
            if payload:
                return payload
    return None


def _first_float(
    sources: Iterable[object | None],
    paths: Iterable[tuple[str, ...]],
) -> float | None:
    for source in sources:
        for path in paths:
            value = _float_or_none(_nested_value(source, path))
            if value is not None:
                return value
    return None


async def _collect_pilot_run_metadata(
    session: AsyncSession,
    autowrite_result: object,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not hasattr(session, "execute"):
        return {}, {}
    project_metadata: Mapping[str, Any] = {}
    workflow_metadata: Mapping[str, Any] = {}
    try:
        project_result = await session.execute(
            select(ProjectModel.metadata_json).where(
                ProjectModel.id == _get_value(autowrite_result, "project_id")
            )
        )
        project_metadata = _as_mapping(project_result.scalar_one_or_none())
    except Exception:
        project_metadata = {}
    try:
        workflow_result = await session.execute(
            select(WorkflowRunModel.metadata_json).where(
                WorkflowRunModel.id == _get_value(
                    autowrite_result, "project_workflow_run_id"
                )
            )
        )
        workflow_metadata = _as_mapping(workflow_result.scalar_one_or_none())
    except Exception:
        workflow_metadata = {}
    return project_metadata, workflow_metadata


def _sample_quality_thresholds(
    spec: ModelPilotSpec,
    case: BenchmarkCaseSpec,
) -> SampleQualityParityThresholds:
    return SampleQualityParityThresholds(
        min_chapter_count=max(
            spec.quality.sample_quality_min_chapter_count,
            case.expectations.min_chapter_count,
        ),
        min_review_overall_score=spec.quality.sample_quality_min_review_score,
        min_scorecard_quality_score=spec.quality.sample_quality_min_scorecard_quality,
        min_reference_distance=spec.quality.sample_quality_min_reference_distance,
        require_no_llm_fallback=spec.quality.require_no_fallback,
        require_export=case.expectations.require_export,
    )


def _evaluate_sample_quality_parity_for_variant(
    *,
    spec: ModelPilotSpec,
    case: BenchmarkCaseSpec,
    autowrite_result: object,
    review_result: object,
    usage: ModelUsageSummary,
    review_overall_score: float | None,
    project_metadata: Mapping[str, Any],
    workflow_metadata: Mapping[str, Any],
) -> SampleQualityParityReport:
    review_scores = _get_value(review_result, "scores")
    sources: tuple[object | None, ...] = (
        _get_value(review_result, "sample_quality_parity"),
        _get_value(review_result, "sample_quality_parity_inputs"),
        _get_value(review_result, "metadata"),
        _get_value(autowrite_result, "sample_quality_parity"),
        _get_value(autowrite_result, "sample_quality_parity_inputs"),
        _get_value(autowrite_result, "metadata"),
        project_metadata,
        workflow_metadata,
        review_result,
        autowrite_result,
    )
    scorecard_quality_score = _first_float(
        sources,
        (
            ("scorecard_quality_score",),
            ("scorecard", "quality_score"),
            ("scorecard_report", "quality_score"),
        ),
    )
    whole_book_quality_report = _first_mapping(
        sources,
        (
            ("whole_book_quality_report",),
            ("whole_book_gate_report",),
            ("whole_book_quality",),
        ),
    )
    premium_gate_report = _first_mapping(
        sources,
        (
            ("premium_book_gate_report",),
            ("premium_gate_report",),
            ("premium_book_gate",),
        ),
    )
    reference_distance_score = _first_float(
        sources,
        (
            ("reference_distance_score",),
            ("sample_reference_distance_score",),
            ("anti_copy", "reference_distance_score"),
            ("anti_copy_report", "reference_distance_score"),
        ),
    )
    return evaluate_sample_quality_parity(
        category_key=case.genre,
        chapter_count=int(_get_value(autowrite_result, "chapter_count") or 0),
        final_verdict=(
            str(_get_value(autowrite_result, "final_verdict"))
            if _get_value(autowrite_result, "final_verdict") is not None
            else None
        ),
        review_overall_score=review_overall_score
        if review_overall_score is not None
        else _float_or_none(_get_value(review_scores, "overall")),
        scorecard_quality_score=scorecard_quality_score,
        whole_book_quality_report=whole_book_quality_report,
        premium_gate_report=premium_gate_report,
        reference_distance_score=reference_distance_score,
        fallback_count=usage.fallback_count,
        export_status=str(_get_value(autowrite_result, "export_status") or ""),
        thresholds=_sample_quality_thresholds(spec, case),
    )


def _evaluate_variant_checks(
    *,
    spec: ModelPilotSpec,
    case: BenchmarkCaseSpec,
    chapter_count: int,
    final_verdict: str | None,
    export_status: str,
    output_files: list[str],
    review_overall_score: float | None,
    resolution_completeness: float | None,
    fallback_count: int,
    sample_quality_parity_report: SampleQualityParityReport | None = None,
) -> list[BenchmarkCheckResult]:
    expected = case.expectations
    file_names = {Path(item).name for item in output_files}
    overall = review_overall_score if review_overall_score is not None else 0.0
    resolution = resolution_completeness if resolution_completeness is not None else 0.0
    checks = [
        _check_result(
            "chapter_count",
            passed=chapter_count >= expected.min_chapter_count,
            actual=chapter_count,
            expected=f">={expected.min_chapter_count}",
            message=(
                "章节数量达标。"
                if chapter_count >= expected.min_chapter_count
                else "章节数量不足。"
            ),
        ),
        _check_result(
            "final_verdict",
            passed=(final_verdict or "") in expected.allowed_final_verdicts,
            actual=final_verdict,
            expected=expected.allowed_final_verdicts,
            message="整书审校结论在允许范围内。"
            if (final_verdict or "") in expected.allowed_final_verdicts
            else "整书审校结论不在允许范围内。",
        ),
        _check_result(
            "overall_score",
            passed=overall >= expected.min_overall_score,
            actual=overall,
            expected=f">={expected.min_overall_score}",
            message=(
                "整书评分达到基线。"
                if overall >= expected.min_overall_score
                else "整书评分低于基线。"
            ),
        ),
        _check_result(
            "resolution_completeness",
            passed=resolution >= spec.quality.min_resolution_completeness,
            actual=resolution,
            expected=f">={spec.quality.min_resolution_completeness}",
            message="完结收束度达到基线。"
            if resolution >= spec.quality.min_resolution_completeness
            else "完结收束度不足, 可能存在未回收主线/线索。",
        ),
        _check_result(
            "export_status",
            passed=(not expected.require_export) or export_status.startswith("exported"),
            actual=export_status,
            expected="exported*",
            message="导出产物已生成。"
            if (not expected.require_export) or export_status.startswith("exported")
            else "导出产物未生成。",
        ),
    ]
    if expected.require_project_markdown:
        checks.append(
            _check_result(
                "project_markdown",
                passed="project.md" in file_names,
                actual=sorted(file_names),
                expected="project.md",
                message=(
                    "整书 Markdown 已导出。"
                    if "project.md" in file_names
                    else "整书 Markdown 缺失。"
                ),
            )
        )
    if spec.quality.require_no_fallback:
        checks.append(
            _check_result(
                "no_llm_fallback",
                passed=fallback_count == 0,
                actual=fallback_count,
                expected=0,
                message="未发生 LLM fallback。"
                if fallback_count == 0
                else "发生过 LLM fallback, 本轮模型对比结果不干净。",
            )
        )
    if spec.quality.require_sample_quality_parity:
        if sample_quality_parity_report is None:
            checks.append(
                _check_result(
                    "sample_quality_parity",
                    passed=False,
                    actual=None,
                    expected="ready",
                    message="缺少样本同标质量门报告。",
                )
            )
        else:
            finding_codes = [
                finding.code for finding in sample_quality_parity_report.findings
            ]
            checks.append(
                _check_result(
                    "sample_quality_parity",
                    passed=sample_quality_parity_report.passed,
                    actual={
                        "readiness_level": sample_quality_parity_report.readiness_level,
                        "finding_codes": finding_codes,
                        "metrics": dict(sample_quality_parity_report.metrics),
                    },
                    expected=sample_quality_parity_report.thresholds.to_dict(),
                    message="样本同标质量门已通过。"
                    if sample_quality_parity_report.passed
                    else "样本同标质量门未通过。",
                )
            )
    return checks


async def collect_model_usage_summary(
    session: AsyncSession,
    project_id: object,
) -> ModelUsageSummary:
    result = await session.execute(
        select(LlmRunModel).where(LlmRunModel.project_id == project_id)
    )
    runs = list(result.scalars())
    model_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    fallback_count = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency_ms = 0
    for run in runs:
        model_counts[str(run.model_name)] += 1
        provider_counts[str(run.provider)] += 1
        role_counts[str(run.logical_role)] += 1
        total_input_tokens += int(run.input_tokens or 0)
        total_output_tokens += int(run.output_tokens or 0)
        total_latency_ms += int(run.latency_ms or 0)
        metadata = run.metadata_json or {}
        if (
            run.provider == "fallback"
            or str(run.model_name).startswith("fallback-")
            or metadata.get("retry_exhausted") is True
        ):
            fallback_count += 1
    return ModelUsageSummary(
        request_count=len(runs),
        fallback_count=fallback_count,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_latency_ms=total_latency_ms,
        model_counts=dict(sorted(model_counts.items())),
        provider_counts=dict(sorted(provider_counts.items())),
        role_counts=dict(sorted(role_counts.items())),
    )


def _write_pilot_report(
    settings: AppSettings,
    result: ModelPilotRunResult,
) -> str:
    pilot_dir = (Path(settings.output.base_dir) / "model-pilots").resolve()
    pilot_dir.mkdir(parents=True, exist_ok=True)
    report_path = pilot_dir / f"{result.pilot_id}-{result.started_at.strftime('%Y%m%d%H%M%S')}.json"
    report_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(report_path)


def _failure_result(
    *,
    spec: ModelPilotSpec,
    variant: ModelPilotVariantSpec,
    project_slug: str,
    error: BaseException,
) -> ModelPilotVariantResult:
    error_text = f"{type(error).__name__}: {error}"
    return ModelPilotVariantResult(
        pilot_id=spec.pilot_id,
        variant_id=variant.variant_id,
        label=variant.label,
        project_slug=project_slug,
        chapter_count=0,
        export_status="failed",
        review_findings_count=0,
        checks=[
            _check_result(
                "variant_run",
                passed=False,
                actual=error_text,
                expected="completed",
                message="模型变体运行失败。",
            )
        ],
        passed=False,
        error=error_text,
    )


async def run_model_pilot(
    session: AsyncSession,
    settings: AppSettings,
    *,
    spec: ModelPilotSpec,
    variant_ids: list[str] | None = None,
    include_disabled: bool = False,
    slug_prefix: str = "pilot30",
    requested_by: str = "model-pilot",
    export_markdown: bool = True,
    auto_repair_on_attention: bool = True,
    progress: ModelPilotProgressCallback | None = None,
) -> ModelPilotRunResult:
    selected = _selected_variants(
        spec,
        variant_ids,
        include_disabled=include_disabled,
    )
    started_at = datetime.now(UTC)
    variant_results: list[ModelPilotVariantResult] = []
    case = spec.book

    for variant in selected:
        variant_started_at = datetime.now(UTC)
        project_slug = _variant_project_slug(
            slug_prefix=slug_prefix,
            variant_id=variant.variant_id,
            started_at=variant_started_at,
        )
        _emit_progress(
            progress,
            "model_pilot_variant_started",
            {
                "pilot_id": spec.pilot_id,
                "variant_id": variant.variant_id,
                "project_slug": project_slug,
            },
        )
        variant_settings = apply_model_pilot_variant(settings, variant)
        try:
            autowrite_result = await run_autowrite_pipeline(
                session,
                variant_settings,
                project_payload=ProjectCreate(
                    slug=project_slug,
                    title=f"{case.title}({variant.label})",
                    genre=case.genre,
                    sub_genre=case.sub_genre,
                    audience=case.audience,
                    target_word_count=case.target_word_count,
                    target_chapters=case.target_chapters,
                    metadata={
                        "premise": case.premise,
                        "model_pilot_id": spec.pilot_id,
                        "model_pilot_variant_id": variant.variant_id,
                        "complete_story_required": True,
                    },
                ),
                premise=case.premise,
                requested_by=requested_by,
                export_markdown=export_markdown,
                auto_repair_on_attention=auto_repair_on_attention,
                progress=progress,
            )
            review_result, _, _ = await review_project_consistency(
                session,
                variant_settings,
                project_slug,
                expect_project_export=case.expectations.require_export,
            )
            usage = await collect_model_usage_summary(session, autowrite_result.project_id)
            resolution_completeness = float(review_result.scores.resolution_completeness)
            review_overall_score = float(review_result.scores.overall)
            project_metadata, workflow_metadata = await _collect_pilot_run_metadata(
                session,
                autowrite_result,
            )
            sample_quality_parity_report = None
            if spec.quality.require_sample_quality_parity:
                sample_quality_parity_report = _evaluate_sample_quality_parity_for_variant(
                    spec=spec,
                    case=case,
                    autowrite_result=autowrite_result,
                    review_result=review_result,
                    usage=usage,
                    review_overall_score=review_overall_score,
                    project_metadata=project_metadata,
                    workflow_metadata=workflow_metadata,
                )
            checks = _evaluate_variant_checks(
                spec=spec,
                case=case,
                chapter_count=autowrite_result.chapter_count,
                final_verdict=autowrite_result.final_verdict,
                export_status=autowrite_result.export_status,
                output_files=list(autowrite_result.output_files),
                review_overall_score=review_overall_score,
                resolution_completeness=resolution_completeness,
                fallback_count=usage.fallback_count,
                sample_quality_parity_report=sample_quality_parity_report,
            )
            variant_result = ModelPilotVariantResult(
                pilot_id=spec.pilot_id,
                variant_id=variant.variant_id,
                label=variant.label,
                project_id=autowrite_result.project_id,
                project_slug=autowrite_result.project_slug,
                chapter_count=autowrite_result.chapter_count,
                final_verdict=autowrite_result.final_verdict,
                requires_human_review=autowrite_result.requires_human_review,
                export_status=autowrite_result.export_status,
                output_dir=autowrite_result.output_dir,
                output_files=list(autowrite_result.output_files),
                review_overall_score=review_overall_score,
                review_resolution_completeness=resolution_completeness,
                review_findings_count=len(review_result.findings),
                usage=usage,
                sample_quality_parity=sample_quality_parity_report.to_dict()
                if sample_quality_parity_report is not None
                else None,
                checks=checks,
                passed=all(check.passed for check in checks),
            )
        except Exception as exc:
            variant_result = _failure_result(
                spec=spec,
                variant=variant,
                project_slug=project_slug,
                error=exc,
            )
        variant_results.append(variant_result)
        _emit_progress(
            progress,
            "model_pilot_variant_completed",
            {
                "pilot_id": spec.pilot_id,
                "variant_id": variant.variant_id,
                "project_slug": project_slug,
                "passed": variant_result.passed,
                "overall_score": variant_result.review_overall_score,
                "fallback_count": variant_result.usage.fallback_count,
            },
        )

    completed_at = datetime.now(UTC)
    pilot_result = ModelPilotRunResult(
        pilot_id=spec.pilot_id,
        title=spec.title,
        started_at=started_at,
        completed_at=completed_at,
        variant_results=variant_results,
        passed_variant_count=sum(1 for result in variant_results if result.passed),
        failed_variant_count=sum(1 for result in variant_results if not result.passed),
    )
    pilot_result.report_path = _write_pilot_report(settings, pilot_result)
    _emit_progress(
        progress,
        "model_pilot_completed",
        {
            "pilot_id": spec.pilot_id,
            "passed_variant_count": pilot_result.passed_variant_count,
            "failed_variant_count": pilot_result.failed_variant_count,
            "report_path": pilot_result.report_path,
        },
    )
    return pilot_result
