"""Boundaries for the one-window first-chapter recovery driver."""
# ruff: noqa: E501

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.services import first_chapter_recovery as recovery
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit

PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")
REPORT_ID = UUID("00000000-0000-0000-0000-000000000009")


def _artifact(number: int, *, project_id=PROJECT_ID, version=10, status="approved"):
    return SimpleNamespace(
        id=uuid4(), project_id=project_id, status=status,
        artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE.value,
        version_no=version,
        content={"chapters": [{"chapter_number": number, "title": f"第{number}章"}]},
    )


def _chapters(*numbers: int):
    return [{"chapter_number": n, "title": f"第{n}章"} for n in numbers]


def test_merge_rejects_old_failed_wrong_project_duplicate_and_outside_window():
    good = _artifact(1, version=11)
    cases = [
        ([_artifact(1, version=9)], {1}, 10, "OLD_OR_UNAPPROVED"),
        ([_artifact(1, project_id=UUID("00000000-0000-0000-0000-000000000002"), version=11)], {1}, 10, "OLD_OR_UNAPPROVED"),
        ([_artifact(1, status="failed", version=11)], {1}, 10, "OLD_OR_UNAPPROVED"),
        ([good, _artifact(1, version=12)], {1}, 10, "DUPLICATE_OUTLINE"),
        ([_artifact(2, version=11)], {1}, 10, "OUTLINE_OUTSIDE_WINDOW"),
    ]
    for artifacts, expected, prior, code in cases:
        with pytest.raises(ValueError, match=f"FIRST_CHAPTER_{code}"):
            recovery.merge_current_attempt_outline(
                artifacts, expected_numbers=expected, project_id=PROJECT_ID, prior_version=prior
            )


def test_merge_requires_complete_current_window_and_only_supplied_ids():
    artifacts = [_artifact(1, version=11), _artifact(2, version=12)]
    merged = recovery.merge_current_attempt_outline(
        artifacts, expected_numbers={1, 2}, project_id=PROJECT_ID, prior_version=10
    )
    assert [c["chapter_number"] for c in merged["chapters"]] == [1, 2]
    assert merged["batch_name"] == "revised-source-first-window"
    # A prior artifact is not implicitly included in the returned source IDs;
    # the caller must provide each successful current-attempt artifact.
    assert "source_outline_artifact_ids" not in merged


class _Session:
    def __init__(self, project, prior=8):
        self.project = project
        self.values = [project, prior]
        self.commits = 0
        self.rollbacks = 0

    async def scalar(self, _statement):
        return self.values.pop(0) if self.values else None

    async def scalars(self, _statement):
        return [_artifact(n, version=9) for n in range(1, 10)]

    async def refresh(self, _obj):
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _driver_setup(monkeypatch, *, marker=None):
    report = str(REPORT_ID)
    project = SimpleNamespace(
        id=PROJECT_ID, slug="book", status="paused", current_chapter_number=0,
        metadata_json={
            "book_design_snapshot_hash": "snapshot",
            "derived_input_revision": {
                "report_id": report, "projection_pending": False,
                "projection_status": "completed", "book_artifact_id": "book-id",
                "world_artifact_id": "world-id", "cast_artifact_id": "cast-id",
                "volume_artifact_id": "volume-id",
            },
            "macro_outline_plan": {}, "rolling_outline_plan": {},
            **(marker or {}),
        },
    )
    session = _Session(project)
    async def control(*_args, **_kwargs):
        return SimpleNamespace(halted=True, intent=SimpleNamespace(value="pause"))
    monkeypatch.setattr(recovery, "load_control_state", control)
    async def noop(*_args, **_kwargs):
        return None
    monkeypatch.setattr(recovery, "rearm_outline_recovery_for_revision", noop)
    monkeypatch.setattr(recovery, "load_rolling_outline_plan", lambda *a, **k: (None, SimpleNamespace(window_start=1, window_end=9)))
    monkeypatch.setattr(recovery, "_normalize_volume_plan_payload", lambda value: value)
    monkeypatch.setattr(recovery, "_expand_volume_plan_into_rolling_windows", lambda *a, **k: [{"start_chapter_number": 1, "end_chapter_number": 9, "volume_number": 1}])
    async def latest(_session, *, project_id, artifact_type):
        return SimpleNamespace(id={
            ArtifactType.BOOK_SPEC: "book-id", ArtifactType.WORLD_SPEC: "world-id",
            ArtifactType.CAST_SPEC: "cast-id", ArtifactType.VOLUME_PLAN: "volume-id",
        }[artifact_type], status="approved", content={})
    monkeypatch.setattr(recovery, "get_latest_planning_artifact", latest)
    return session, project


@pytest.mark.asyncio
async def test_driver_calls_planner_once_for_first_window_and_never_dispatches_50(monkeypatch):
    session, _ = _driver_setup(monkeypatch)
    calls = []
    workflow_id = uuid4()
    async def planner(*args, **kwargs):
        calls.append(kwargs)
        session.project.metadata_json.update({
            "outline_semantic_gate_report": {"promotion_allowed": True},
            "outline_commercial_last_approved": {"workflow_run_id": str(workflow_id)},
        })
        return SimpleNamespace(workflow_run_id=workflow_id, artifacts=[SimpleNamespace(artifact_id=_artifact(1).id, artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE.value)])
    monkeypatch.setattr(recovery, "generate_volume_plan", planner)
    async def import_artifact(*_args, **_kwargs):
        return SimpleNamespace(id=uuid4())
    monkeypatch.setattr(recovery, "import_planning_artifact", import_artifact)
    monkeypatch.setattr(recovery, "materialize_latest_chapter_outline_batch", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "materialize_latest_narrative_graph", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "materialize_latest_narrative_tree", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "_release_approved_outline_replan_gate", lambda *a, **k: True)
    # Make the async materializers awaitable without allowing any second plan.
    for name in ("materialize_latest_chapter_outline_batch", "materialize_latest_narrative_graph", "materialize_latest_narrative_tree"):
        async def async_noop(*_args, **_kwargs):
            return None
        monkeypatch.setattr(recovery, name, async_noop)
    settings = load_settings(env={})
    settings.pipeline.rolling_outline_window_size = 9
    result = await recovery.recover_first_outline_window(session, settings, project_slug="book", report_id=REPORT_ID)
    assert result["status"] == "outline_ready"
    assert len(calls) == 1
    assert calls[0]["volume_plan"] == [{"start_chapter_number": 1, "end_chapter_number": 9, "volume_number": 1}]


@pytest.mark.asyncio
async def test_driver_preflight_rejects_incomplete_projection_before_planner(monkeypatch):
    session, _ = _driver_setup(monkeypatch, marker={"derived_input_revision": {"report_id": str(REPORT_ID), "projection_pending": True, "projection_status": "pending"}})
    monkeypatch.setattr(recovery, "generate_volume_plan", lambda *_a, **_k: pytest.fail("planner must not run"))
    with pytest.raises(ValueError, match="FIRST_CHAPTER_PROJECTION_REQUIRED"):
        await recovery.recover_first_outline_window(session, SimpleNamespace(), project_slug="book", report_id=REPORT_ID)
