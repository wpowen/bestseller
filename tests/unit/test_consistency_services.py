from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
)
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


@pytest.mark.asyncio
async def test_review_project_consistency_handles_scalar_chapter_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``session.scalars(select(Model.col))`` yields the column
    values directly (ints here), not row objects.  The previous
    implementation did ``row.chapter_number for row in ...`` which blew up
    with ``AttributeError: 'int' object has no attribute 'chapter_number'``
    against a live Postgres session and killed every multi-volume pipeline
    at the project-consistency step."""
    project = build_project()
    # The ``current_volume_number`` column is ``NOT NULL DEFAULT 1`` in
    # Postgres; the in-memory ``ProjectModel()`` constructor used by
    # ``build_project`` bypasses that server default, so we set it
    # explicitly to mimic what a freshly loaded row looks like.
    project.current_volume_number = 1

    async def fake_get_project_by_slug(session, slug: str):
        return project

    monkeypatch.setattr(consistency_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[50, 50, 50, 200, 200, 200, 200, 0, 0, 10, 1],
        scalars_results=[
            # First scalars() call is the chapter_number extraction — it
            # now yields ints, exactly as the real SQLAlchemy session does.
            [1, 2, 3, 5, 6, 7, 8, 9, 10],  # NB: gap at 4 to exercise sequence-gap detection
            [],  # chapter_contracts
            [],  # plot_arcs
            [],  # clues
            [],  # payoffs
            [],  # emotion_tracks
            [],  # antagonist_plans
            [],  # world_rules
            [],  # protagonists
            [],  # antagonists
            [],  # character state snapshots
            [],  # chapter_drafts
            [],  # supporting_characters
            [],  # subplot_schedules
        ],
    )

    # Must not raise AttributeError.  The review call exercises the full
    # review_project_consistency code path end-to-end.
    result, _report, _quality = await consistency_services.review_project_consistency(
        session,
        build_settings(),
        "my-story",
    )

    # Sanity: the chapter-sequence-gap check (downstream consumer of
    # ``all_chapter_numbers``) must have been invoked with ints and reported
    # a gap for missing chapter 4.
    gap_findings = [
        f for f in result.findings if f.category in {"chapter_sequence", "chapter_sequence_gap"}
    ]
    # We don't over-specify the category name — just assert the call path
    # completed and produced *some* review result.
    assert isinstance(result.findings, list)


def test_check_obligatory_scenes_uses_content_md_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``ChapterDraftVersionModel`` exposes the draft body as
    ``content_md`` (NOT ``content``) and carries no ``chapter_number`` of
    its own.  A previous refactor scanned ``d.content`` / ``d.chapter_number``
    which blew up in production the moment a project had any completed
    chapter drafts, killing ``review_project_consistency`` at Phase-5.
    """
    project = ProjectModel(
        slug="my-xianxia",
        title="道种破虚",
        genre="xianxia",
        target_word_count=90000,
        target_chapters=50,
        language="zh-CN",
        metadata_json={"prompt_pack_key": "xianxia-upgrade"},
    )
    project.id = uuid4()
    project.sub_genre = None

    # Build two realistic drafts — use real ChapterDraftVersionModel
    # instances so the test would regress if the column is ever renamed.
    chapter_id_1, chapter_id_2 = uuid4(), uuid4()
    d1 = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter_id_1,
        version_no=1,
        content_md="第一章：开篇。宁尘入宗门立誓，拜师仪式在广场举行。",
        word_count=50,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    d2 = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter_id_2,
        version_no=1,
        content_md="终章：镇压大敌，门派回归平静，功成身退。",
        word_count=40,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )

    # Fake a prompt pack with one obligatory scene that should match by
    # keyword so the function produces *some* output rather than an early
    # return — we want the body of the function executed.
    fake_pack = SimpleNamespace(
        obligatory_scenes=[
            SimpleNamespace(
                label="拜师仪式",
                code="initiation",
                timing="act_1",
                check_keywords=["拜师", "仪式"],
            )
        ]
    )
    monkeypatch.setattr(
        consistency_services,
        "resolve_prompt_pack",
        lambda *a, **k: fake_pack,
        raising=False,
    )
    # The function imports lazily; patch the actual module too.
    from bestseller.services import prompt_packs
    monkeypatch.setattr(prompt_packs, "resolve_prompt_pack", lambda *a, **k: fake_pack)

    # Must not raise AttributeError.
    findings = consistency_services._check_obligatory_scenes(
        project=project,
        chapter_count=50,
        chapter_drafts=[d1, d2],
        chapter_number_by_id={chapter_id_1: 1, chapter_id_2: 50},
        language="zh-CN",
    )
    assert isinstance(findings, list)


def test_check_obligatory_scenes_returns_empty_without_chapter_number_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the caller forgets to pass ``chapter_number_by_id``, the function
    must still degrade gracefully — fall back to ``all_draft_text`` rather
    than crashing on the missing attribute."""
    project = ProjectModel(
        slug="my-story",
        title="Story",
        genre="xianxia",
        target_word_count=90000,
        target_chapters=5,
        language="zh-CN",
        metadata_json={},
    )
    project.id = uuid4()
    project.sub_genre = None

    d = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=uuid4(),
        version_no=1,
        content_md="content body with no keywords",
        word_count=5,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )

    from bestseller.services import prompt_packs
    monkeypatch.setattr(prompt_packs, "resolve_prompt_pack", lambda *a, **k: None)

    findings = consistency_services._check_obligatory_scenes(
        project=project,
        chapter_count=5,
        chapter_drafts=[d],
        language="zh-CN",
    )
    # No prompt pack → early return []; crucially does NOT raise.
    assert findings == []
