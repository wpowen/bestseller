from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel, QualityScoreModel, ReviewReportModel
from bestseller.services import consistency as consistency_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, scalar_results: list[object | None] | None = None) -> None:
        self.scalar_results = list(scalar_results or [])
        self.added: list[object] = []
        self.executed: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is None or "id" not in table.c:
                continue
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    async def execute(self, stmt: object) -> None:
        self.executed.append(stmt)


def build_settings():
    return load_settings(env={})


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="sci-fi",
        target_word_count=90000,
        target_chapters=18,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def test_evaluate_project_consistency_returns_pass_for_complete_project() -> None:
    result = consistency_services.evaluate_project_consistency(
        settings=build_settings(),
        chapter_count=2,
        chapter_draft_count=2,
        complete_chapter_count=2,
        scene_count=3,
        approved_scene_count=3,
        scene_summary_count=3,
        timeline_event_count=3,
        pending_rewrite_count=0,
        project_export_count=1,
        chapter_export_count=2,
    )

    assert result.verdict == "pass"
    assert result.scores.overall >= 0.8
    assert result.findings == []


def test_evaluate_project_consistency_returns_attention_when_coverage_is_missing() -> None:
    result = consistency_services.evaluate_project_consistency(
        settings=build_settings(),
        chapter_count=3,
        chapter_draft_count=1,
        complete_chapter_count=1,
        scene_count=6,
        approved_scene_count=3,
        scene_summary_count=2,
        timeline_event_count=2,
        pending_rewrite_count=2,
        project_export_count=0,
        chapter_export_count=1,
    )

    assert result.verdict == "attention"
    assert len(result.findings) >= 3
    assert result.recommended_actions


@pytest.mark.asyncio
async def test_review_project_consistency_persists_report_and_quality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    monkeypatch.setattr(consistency_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[2, 2, 2, 3, 3, 3, 3, 0, 1, 2],
    )

    result, report, quality = await consistency_services.review_project_consistency(
        session,
        build_settings(),
        "my-story",
    )

    assert result.verdict == "pass"
    assert isinstance(report, ReviewReportModel)
    assert isinstance(quality, QualityScoreModel)
    assert report.id is not None
    assert quality.id is not None
