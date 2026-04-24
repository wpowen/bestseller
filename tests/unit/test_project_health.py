from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ClueModel, ProjectModel
from bestseller.services import project_health
from bestseller.services.hype_engine import HypeType
from bestseller.services.setup_payoff_tracker import SetupPayoffDebt, SetupPayoffReport
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self) -> None:
        self.scalar_results: list[object | None] = [12]
        self.scalars_results: list[list[object]] = []

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    async def scalars(self, stmt: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)


def build_settings():
    return load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={},
    )


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=60,
        language="zh-CN",
        metadata_json={
            "truth_version": 2,
            "truth_updated_at": "2026-04-23T00:00:00+00:00",
            "truth_last_changed_artifact_type": "book_spec",
            "_truth_artifact_fingerprints": {},
            "_truth_change_log": [],
        },
    )
    project.id = uuid4()
    return project


@pytest.mark.asyncio
async def test_build_project_health_report_aggregates_story_risks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    overdue_clue = ClueModel(
        project_id=project.id,
        clue_code="blood-lotus",
        label="血莲印",
        description="血莲印的来历",
        status="active",
        metadata_json={},
    )
    overdue_clue.expected_payoff_by_chapter_number = 10
    overdue_clue.planted_in_chapter_number = 3
    session = FakeSession()
    session.scalars_results = [[overdue_clue]]

    async def fake_get_project_by_slug(_session, slug: str) -> ProjectModel:
        return project

    async def fake_truth_statuses(_session, _project):
        return (
            type(
                "TruthStatus",
                (),
                {
                    "component": "story_bible",
                    "workflow_type": "materialize_story_bible",
                    "status": "stale",
                    "required_truth_version": 2,
                    "materialized_truth_version": 1,
                    "materialized_at": "2026-04-22T00:00:00+00:00",
                    "workflow_run_id": uuid4(),
                    "detail": "older truth version",
                },
            )(),
        )

    async def fake_build_revealed_ledger(_session, _project_id, up_to_chapter=None):
        return type(
            "Ledger",
            (),
            {
                "overused_hooks": lambda self: (
                    type(
                        "HookUsage",
                        (),
                        {
                            "hook_type": "mystery",
                            "total_count": 6,
                            "recent_count": 4,
                            "recent_chapters": (8, 9, 10, 11),
                        },
                    )(),
                )
            },
        )()

    async def fake_load_setup_inputs(_session, project_id):
        return [
            (1, "沈砚被羞辱后反击，血莲印忽然亮起？"),
            (2, "敌人冷笑围住他，沈砚当场打脸，门外却传来追杀声！"),
            (3, "禁令压城，新的名单送到手中，真相只差最后一页。"),
        ], [(1, HypeType.COUNTERATTACK), (2, HypeType.FACE_SLAP), (3, None)]

    def fake_analyze_setup_payoff(**kwargs):
        return SetupPayoffReport(
            setups=(),
            payoffs=(),
            debts=(
                SetupPayoffDebt(
                    setup_chapter=5,
                    window_end_chapter=10,
                    matched_keywords=("羞辱",),
                ),
            ),
            payoff_window_chapters=5,
        )

    monkeypatch.setattr(project_health, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(project_health, "get_truth_materialization_statuses", fake_truth_statuses)
    monkeypatch.setattr(project_health, "build_revealed_ledger", fake_build_revealed_ledger)
    monkeypatch.setattr(project_health, "_load_setup_payoff_inputs", fake_load_setup_inputs)
    monkeypatch.setattr(project_health, "analyze_setup_payoff", fake_analyze_setup_payoff)

    report = await project_health.build_project_health_report(
        session,
        build_settings(),
        "my-story",
    )

    assert report["truth_version"] == 2
    assert report["stale_truth_components"][0]["component"] == "story_bible"
    assert report["overdue_clues"][0]["clue_code"] == "blood-lotus"
    assert report["overused_hooks"][0]["hook_type"] == "mystery"
    assert report["setup_payoff_debts"][0]["setup_chapter"] == 5
    assert report["golden_three"]["strong_hype_chapters"] >= 2


@pytest.mark.asyncio
async def test_repair_project_health_dry_run_plans_safe_materializations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {
        "project_slug": "my-story",
        "stale_truth_components": [
            {"component": "story_bible"},
            {"component": "narrative_graph"},
        ],
        "overdue_clues": [{"clue_code": "blood-lotus"}],
        "setup_payoff_debts": [],
        "golden_three": {"issue_codes": ["GOLDEN_THREE_LOW_HYPE"]},
    }

    async def fake_build_report(_session, _settings, project_slug):
        assert project_slug == "my-story"
        return before

    monkeypatch.setattr(project_health, "build_project_health_report", fake_build_report)

    result = await project_health.repair_project_health(
        FakeSession(),  # type: ignore[arg-type]
        build_settings(),
        "my-story",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["actions"][0]["component"] == "story_bible"
    assert result["actions"][0]["status"] == "planned"
    assert result["actions"][1]["component"] == "narrative_graph"
    assert result["actions"][2]["action"] == "review_overdue_clues"
    assert result["actions"][3]["action"] == "strengthen_golden_three"


@pytest.mark.asyncio
async def test_repair_project_health_applies_stale_truth_materializations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = [
        {
            "project_slug": "my-story",
            "stale_truth_components": [{"component": "chapter_outline"}],
            "overdue_clues": [],
            "setup_payoff_debts": [],
            "golden_three": {"issue_codes": []},
        },
        {
            "project_slug": "my-story",
            "stale_truth_components": [],
            "overdue_clues": [],
            "setup_payoff_debts": [],
            "golden_three": {"issue_codes": []},
        },
    ]
    materialized: list[str] = []

    async def fake_build_report(_session, _settings, _project_slug):
        return reports.pop(0)

    async def fake_materialize(_session, _project_slug, component, *, requested_by):
        materialized.append(f"{component}:{requested_by}")
        return {"workflow_run_id": "wf-1"}

    monkeypatch.setattr(project_health, "build_project_health_report", fake_build_report)
    monkeypatch.setattr(project_health, "_materialize_truth_component", fake_materialize)

    result = await project_health.repair_project_health(
        FakeSession(),  # type: ignore[arg-type]
        build_settings(),
        "my-story",
        requested_by="tester",
        dry_run=False,
    )

    assert materialized == ["chapter_outline:tester"]
    assert result["actions"][0]["status"] == "completed"
    assert result["after"]["stale_truth_components"] == []
