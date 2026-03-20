from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.evaluation import (
    BenchmarkCaseResult,
    BenchmarkCaseSpec,
    BenchmarkCheckResult,
    BenchmarkSuiteCatalogEntry,
    BenchmarkSuiteRunResult,
    BenchmarkSuiteSpec,
)
from bestseller.domain.project import ProjectCreate
from bestseller.services.consistency import review_project_consistency
from bestseller.services.narrative import build_narrative_overview
from bestseller.services.pipelines import run_autowrite_pipeline
from bestseller.settings import AppSettings


BenchmarkProgressCallback = Callable[[str, dict[str, Any] | None], None]

_ROOT_DIR = Path(__file__).resolve().parents[3]
_BENCHMARK_DIR = _ROOT_DIR / "examples" / "benchmarks"
_BUILTIN_SUITES: dict[str, Path] = {
    "sample-books": _BENCHMARK_DIR / "sample_books.yaml",
}


def _emit_progress(
    progress: BenchmarkProgressCallback | None,
    stage: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if progress is None:
        return
    progress(stage, payload)


def _load_suite_file(path: Path) -> BenchmarkSuiteSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return BenchmarkSuiteSpec.model_validate(data)


def list_benchmark_suites() -> list[BenchmarkSuiteCatalogEntry]:
    entries: list[BenchmarkSuiteCatalogEntry] = []
    for suite_id, path in sorted(_BUILTIN_SUITES.items()):
        spec = _load_suite_file(path)
        entries.append(
            BenchmarkSuiteCatalogEntry(
                suite_id=suite_id,
                title=spec.title,
                description=spec.description,
                path=str(path),
                case_count=len(spec.cases),
                case_ids=[case.case_id for case in spec.cases],
            )
        )
    return entries


def load_benchmark_suite(
    suite_id: str = "sample-books",
    *,
    suite_file: Path | None = None,
) -> BenchmarkSuiteSpec:
    if suite_file is not None:
        return _load_suite_file(suite_file)
    if suite_id not in _BUILTIN_SUITES:
        available = ", ".join(sorted(_BUILTIN_SUITES))
        raise ValueError(f"Unknown benchmark suite '{suite_id}'. Available: {available}")
    return _load_suite_file(_BUILTIN_SUITES[suite_id])


def _selected_cases(
    suite: BenchmarkSuiteSpec,
    case_ids: Iterable[str] | None,
) -> list[BenchmarkCaseSpec]:
    requested = {case_id for case_id in (case_ids or []) if case_id}
    if not requested:
        return list(suite.cases)
    selected = [case for case in suite.cases if case.case_id in requested]
    missing = sorted(requested - {case.case_id for case in selected})
    if missing:
        raise ValueError(f"Unknown benchmark case(s): {', '.join(missing)}")
    return selected


def _benchmark_slug(prefix: str, case_id: str, started_at: datetime) -> str:
    return f"{prefix}-{case_id}-{started_at.strftime('%Y%m%d%H%M%S')}"


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


def _evaluate_case_checks(
    *,
    case: BenchmarkCaseSpec,
    chapter_count: int,
    final_verdict: str | None,
    export_status: str,
    output_files: list[str],
    review_overall_score: float,
    plot_arc_types: list[str],
    emotion_track_count: int,
    antagonist_plan_count: int,
) -> list[BenchmarkCheckResult]:
    expected = case.expectations
    file_names = {Path(item).name for item in output_files}
    checks: list[BenchmarkCheckResult] = []
    checks.append(
        _check_result(
            "chapter_count",
            passed=chapter_count >= expected.min_chapter_count,
            actual=chapter_count,
            expected=f">={expected.min_chapter_count}",
            message="章节产出数量达标。"
            if chapter_count >= expected.min_chapter_count
            else "章节产出数量不足。",
        )
    )
    checks.append(
        _check_result(
            "final_verdict",
            passed=(final_verdict or "") in expected.allowed_final_verdicts,
            actual=final_verdict,
            expected=expected.allowed_final_verdicts,
            message="项目审校结论在允许范围内。"
            if (final_verdict or "") in expected.allowed_final_verdicts
            else "项目审校结论不在允许范围内。",
        )
    )
    checks.append(
        _check_result(
            "overall_score",
            passed=review_overall_score >= expected.min_overall_score,
            actual=review_overall_score,
            expected=f">={expected.min_overall_score}",
            message="项目评分达到基线。"
            if review_overall_score >= expected.min_overall_score
            else "项目评分低于基线。",
        )
    )
    checks.append(
        _check_result(
            "export_status",
            passed=(not expected.require_export) or export_status.startswith("exported"),
            actual=export_status,
            expected="exported*",
            message="导出产物已生成。"
            if (not expected.require_export) or export_status.startswith("exported")
            else "导出产物未生成。",
        )
    )
    if expected.require_project_markdown:
        checks.append(
            _check_result(
                "project_markdown",
                passed="project.md" in file_names,
                actual=sorted(file_names),
                expected="project.md",
                message="整书 Markdown 已导出。" if "project.md" in file_names else "整书 Markdown 缺失。",
            )
        )
    if expected.required_arc_types:
        missing_arc_types = sorted(set(expected.required_arc_types) - set(plot_arc_types))
        checks.append(
            _check_result(
                "required_arc_types",
                passed=not missing_arc_types,
                actual=plot_arc_types,
                expected=expected.required_arc_types,
                message="叙事线类型覆盖达标。" if not missing_arc_types else f"缺少叙事线类型: {', '.join(missing_arc_types)}。",
            )
        )
    if expected.require_emotion_track:
        checks.append(
            _check_result(
                "emotion_tracks",
                passed=emotion_track_count > 0,
                actual=emotion_track_count,
                expected=">0",
                message="关系/情绪线已生成。" if emotion_track_count > 0 else "关系/情绪线缺失。",
            )
        )
    if expected.require_antagonist_plan:
        checks.append(
            _check_result(
                "antagonist_plans",
                passed=antagonist_plan_count > 0,
                actual=antagonist_plan_count,
                expected=">0",
                message="反派推进计划已生成。" if antagonist_plan_count > 0 else "反派推进计划缺失。",
            )
        )
    return checks


def _write_suite_report(
    settings: AppSettings,
    result: BenchmarkSuiteRunResult,
) -> str:
    benchmark_dir = (Path(settings.output.base_dir) / "benchmarks").resolve()
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    report_path = benchmark_dir / f"{result.suite_id}-{result.started_at.strftime('%Y%m%d%H%M%S')}.json"
    report_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(report_path)


async def run_benchmark_suite(
    session: AsyncSession,
    settings: AppSettings,
    *,
    suite: BenchmarkSuiteSpec,
    case_ids: list[str] | None = None,
    slug_prefix: str = "benchmark",
    requested_by: str = "benchmark",
    progress: BenchmarkProgressCallback | None = None,
) -> BenchmarkSuiteRunResult:
    selected_cases = _selected_cases(suite, case_ids)
    started_at = datetime.now(UTC)
    case_results: list[BenchmarkCaseResult] = []

    for case in selected_cases:
        case_started_at = datetime.now(UTC)
        project_slug = _benchmark_slug(slug_prefix, case.case_id, case_started_at)
        _emit_progress(
            progress,
            "benchmark_case_started",
            {"suite_id": suite.suite_id, "case_id": case.case_id, "project_slug": project_slug},
        )
        autowrite_result = await run_autowrite_pipeline(
            session,
            settings,
            project_payload=ProjectCreate(
                slug=project_slug,
                title=case.title,
                genre=case.genre,
                sub_genre=case.sub_genre,
                audience=case.audience,
                target_word_count=case.target_word_count,
                target_chapters=case.target_chapters,
            ),
            premise=case.premise,
            requested_by=requested_by,
            export_markdown=True,
            auto_repair_on_attention=True,
            progress=None,
        )
        review_result, _, _ = await review_project_consistency(
            session,
            settings,
            project_slug,
            expect_project_export=case.expectations.require_export,
        )
        overview = await build_narrative_overview(session, project_slug)
        plot_arc_types = sorted({item.arc_type for item in overview.plot_arcs})
        checks = _evaluate_case_checks(
            case=case,
            chapter_count=autowrite_result.chapter_count,
            final_verdict=autowrite_result.final_verdict,
            export_status=autowrite_result.export_status,
            output_files=list(autowrite_result.output_files),
            review_overall_score=review_result.scores.overall,
            plot_arc_types=plot_arc_types,
            emotion_track_count=len(overview.emotion_tracks),
            antagonist_plan_count=len(overview.antagonist_plans),
        )
        case_result = BenchmarkCaseResult(
            suite_id=suite.suite_id,
            case_id=case.case_id,
            title=case.title,
            project_id=autowrite_result.project_id,
            project_slug=autowrite_result.project_slug,
            chapter_count=autowrite_result.chapter_count,
            final_verdict=autowrite_result.final_verdict,
            requires_human_review=autowrite_result.requires_human_review,
            export_status=autowrite_result.export_status,
            output_dir=autowrite_result.output_dir,
            output_files=list(autowrite_result.output_files),
            review_overall_score=review_result.scores.overall,
            review_findings_count=len(review_result.findings),
            plot_arc_types=plot_arc_types,
            emotion_track_count=len(overview.emotion_tracks),
            antagonist_plan_count=len(overview.antagonist_plans),
            checks=checks,
            passed=all(check.passed for check in checks),
        )
        case_results.append(case_result)
        _emit_progress(
            progress,
            "benchmark_case_completed",
            {
                "suite_id": suite.suite_id,
                "case_id": case.case_id,
                "project_slug": project_slug,
                "passed": case_result.passed,
                "overall_score": review_result.scores.overall,
            },
        )

    completed_at = datetime.now(UTC)
    suite_result = BenchmarkSuiteRunResult(
        suite_id=suite.suite_id,
        title=suite.title,
        started_at=started_at,
        completed_at=completed_at,
        case_results=case_results,
        passed_case_count=sum(1 for item in case_results if item.passed),
        failed_case_count=sum(1 for item in case_results if not item.passed),
    )
    suite_result.report_path = _write_suite_report(settings, suite_result)
    _emit_progress(
        progress,
        "benchmark_suite_completed",
        {
            "suite_id": suite.suite_id,
            "passed_case_count": suite_result.passed_case_count,
            "failed_case_count": suite_result.failed_case_count,
            "report_path": suite_result.report_path,
        },
    )
    return suite_result
