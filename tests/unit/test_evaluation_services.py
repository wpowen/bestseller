from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.evaluation import BenchmarkCaseSpec, BenchmarkSuiteCatalogEntry, BenchmarkSuiteSpec
from bestseller.services import evaluation as evaluation_services


pytestmark = pytest.mark.unit


def test_list_and_load_benchmark_suites_include_sample_books() -> None:
    suites = evaluation_services.list_benchmark_suites()

    assert any(item.suite_id == "sample-books" for item in suites)
    suite = evaluation_services.load_benchmark_suite("sample-books")
    assert suite.suite_id == "sample-books"
    assert len(suite.cases) == 3


@pytest.mark.asyncio
async def test_run_benchmark_suite_aggregates_case_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    suite = BenchmarkSuiteSpec(
        suite_id="sample-books",
        title="样书评测基线",
        cases=[
            BenchmarkCaseSpec(
                case_id="urban-mystery",
                title="都市悬疑样书",
                genre="urban-mystery",
                target_word_count=12000,
                target_chapters=4,
                premise="一次旧案重查撬开新的阴谋。",
            )
        ],
    )
    settings = SimpleNamespace(output=SimpleNamespace(base_dir=str(tmp_path)))

    async def fake_run_autowrite_pipeline(session, settings, **kwargs):
        project_payload = kwargs["project_payload"]
        return SimpleNamespace(
            project_id=uuid4(),
            project_slug=project_payload.slug,
            chapter_count=4,
            final_verdict="pass",
            requires_human_review=False,
            export_status="exported",
            output_dir=str(tmp_path / project_payload.slug),
            output_files=[str(tmp_path / project_payload.slug / "project.md")],
        )

    async def fake_review_project_consistency(session, settings, project_slug, **kwargs):
        return (
            SimpleNamespace(
                scores=SimpleNamespace(overall=0.84),
                findings=[],
            ),
            None,
            None,
        )

    async def fake_build_narrative_overview(session, project_slug):
        return SimpleNamespace(
            plot_arcs=[
                SimpleNamespace(arc_type="main_plot"),
                SimpleNamespace(arc_type="mystery"),
            ],
            emotion_tracks=[SimpleNamespace(track_code="bond-001")],
            antagonist_plans=[SimpleNamespace(plan_code="antagonist-001")],
        )

    monkeypatch.setattr(evaluation_services, "run_autowrite_pipeline", fake_run_autowrite_pipeline)
    monkeypatch.setattr(evaluation_services, "review_project_consistency", fake_review_project_consistency)
    monkeypatch.setattr(evaluation_services, "build_narrative_overview", fake_build_narrative_overview)
    monkeypatch.setattr(
        evaluation_services,
        "_write_suite_report",
        lambda settings, result: str(tmp_path / "bench-report.json"),
    )

    result = await evaluation_services.run_benchmark_suite(
        object(),
        settings,
        suite=suite,
        slug_prefix="bench",
    )

    assert result.passed_case_count == 1
    assert result.failed_case_count == 0
    assert result.report_path == str(tmp_path / "bench-report.json")
    assert result.case_results[0].passed is True
    assert result.case_results[0].review_overall_score == 0.84
    assert "main_plot" in result.case_results[0].plot_arc_types
