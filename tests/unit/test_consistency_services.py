from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel, QualityScoreModel, ReviewReportModel
from bestseller.services import consistency as consistency_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        scalar_results: list[object | None] | None = None,
        scalars_results: list[list[object]] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
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

    async def scalars(self, stmt: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)

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


def test_evaluate_project_consistency_v2_flags_narrative_regressions() -> None:
    result = consistency_services.evaluate_project_consistency(
        settings=build_settings(),
        chapter_count=4,
        chapter_draft_count=4,
        complete_chapter_count=4,
        scene_count=8,
        approved_scene_count=8,
        scene_summary_count=8,
        timeline_event_count=8,
        pending_rewrite_count=0,
        project_export_count=1,
        chapter_export_count=4,
        main_plot_progression=0.5,
        main_plot_chapter_count=2,
        mystery_balance=0.4,
        clue_count=4,
        payoff_count=1,
        overdue_clue_count=2,
        emotional_continuity=0.45,
        emotion_track_count=2,
        stale_emotion_track_count=2,
        character_arc_progression=0.35,
        protagonist_arc_step_count=1,
        protagonist_snapshot_chapter_count=2,
        world_rule_consistency=0.5,
        world_rule_count=3,
        grounded_world_rule_count=0,
        antagonist_pressure=0.3,
        antagonist_count=1,
        antagonist_plan_count=1,
        active_antagonist_plan_count=0,
    )

    categories = {finding.category for finding in result.findings}
    assert result.verdict == "attention"
    assert "main_plot_progression" in categories
    assert "mystery_balance" in categories
    assert "emotion_continuity" in categories
    assert "character_arc_progression" in categories
    assert "world_rule_consistency" in categories
    assert "antagonist_pressure" in categories


def test_evaluate_project_consistency_v3_flags_supporting_cast_subplot_and_resolution() -> None:
    result = consistency_services.evaluate_project_consistency(
        settings=build_settings(),
        chapter_count=10,
        chapter_draft_count=10,
        complete_chapter_count=10,
        scene_count=20,
        approved_scene_count=20,
        scene_summary_count=20,
        timeline_event_count=20,
        pending_rewrite_count=0,
        project_export_count=1,
        chapter_export_count=10,
        # New dimensions — all underperforming
        supporting_character_count=6,
        supporting_with_arc_count=1,
        supporting_with_voice_count=0,
        dormant_subplot_count=3,
        total_subplot_count=4,
        open_arc_count=5,
        open_clue_count=4,
        is_final_volume=True,
    )

    categories = {finding.category for finding in result.findings}
    assert result.verdict == "attention"
    assert "supporting_cast_depth" in categories
    assert "subplot_health" in categories
    assert "resolution_completeness" in categories
    assert result.scores.supporting_cast_depth < 0.75
    assert result.scores.subplot_health < 0.75
    assert result.scores.resolution_completeness < 1.0


@pytest.mark.asyncio
async def test_review_project_consistency_persists_report_and_quality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    monkeypatch.setattr(consistency_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[
            2, 2, 2, 3, 3, 3, 3, 0, 1, 2,  # original 10 scalar counts
            0,  # total_volume_count
        ],
        scalars_results=[
            [],  # chapter_contracts
            [],  # plot_arcs
            [],  # clues
            [],  # payoffs
            [],  # emotion_tracks
            [],  # antagonist_plans
            [],  # world_rules
            [],  # protagonists
            [],  # antagonists
            [],  # chapter_drafts
            [],  # supporting_characters
        ],
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
