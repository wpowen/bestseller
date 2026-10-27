"""Web worker status projection with in-process Redis doubles only."""

from datetime import UTC, datetime
from importlib import import_module
import json
import sys
from types import SimpleNamespace

import pytest

from bestseller.web import server

JOB_ID = "repair:heal:projection-book"
BASE_TS = 1_900_000_000.0
DEADLINE = {"terminal": True, "code": "execution_deadline_exhausted"}


def _event(index, message, event_type="progress", **data):
    return {
        "ts": BASE_TS + index,
        "message": message,
        "event_type": event_type,
        "data": data,
    }


def _install_redis(monkeypatch, events, job_state="running"):
    # Load router dependencies before replacing Redis's top-level module; this
    # imports its asyncio types without creating a client or connecting anywhere.
    import_module("bestseller.api.routers.tasks")

    class FakeRedis:
        state = job_state

        def exists(self, *keys):
            owned_key = (
                f"arq:in-progress:{JOB_ID}" if self.state == "running" else f"arq:result:{JOB_ID}"
            )
            return int(owned_key in keys)

        def zscore(self, key, member):
            return None

        def lrange(self, key, start, stop):
            if key == f"task:{JOB_ID}:milestones":
                return []
            assert key == f"task:{JOB_ID}:progress"
            rows = [json.dumps(event) for event in events]
            return rows[start:] if stop == -1 else rows[start : stop + 1]

        def close(self):
            pass

    client = FakeRedis()
    monkeypatch.setitem(
        sys.modules, "redis", SimpleNamespace(from_url=lambda *args, **kwargs: client)
    )
    return client


def _manager(tmp_path):
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    manager._tasks["card"] = server.WebTaskState(
        task_id="card",
        task_type="repair",
        status="running",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        project_slug="projection-book",
        current_stage="project_repair_started",
    )
    return manager


def _project(surface, monkeypatch, tmp_path, events, job_state="running"):
    _install_redis(monkeypatch, events, job_state)
    if surface == "snapshot":
        snapshot = server._load_worker_heal_progress_snapshot("redis://stub", JOB_ID)
        assert snapshot is not None
        return snapshot["status"], snapshot["progress_events"]
    manager = _manager(tmp_path)
    assert manager.sync_progress_from_worker_redis("redis://stub") == 1
    task = manager._tasks["card"]
    return task.status, task.progress_events


def _assert_history(history, events):
    # Projection must retain even late/stale receipts for diagnosis.
    assert [row["stage"] for row in history] == [row["message"] for row in events]
    assert [row["payload"] for row in history] == [row["data"] for row in events]


def test_normalisation_preserves_explicit_event_types_and_legacy_dictionary_shape():
    legacy = {"ts": BASE_TS, "message": "chapter_started", "data": {"chapter_number": 1}}
    typed = _event(1, "execution_deadline_exhausted", "failed", **DEADLINE)
    events, stage, payload = server._normalise_worker_progress_events(
        [json.dumps(legacy), json.dumps(typed)]
    )
    assert events[0] == {
        "timestamp": datetime.fromtimestamp(BASE_TS, UTC).isoformat(),
        "stage": "chapter_started",
        "payload": {"chapter_number": 1},
    }
    assert events[1]["event_type"] == "failed"
    assert events[1]["stage"] == typed["message"]
    assert stage == typed["message"]
    assert payload == DEADLINE


@pytest.mark.parametrize("surface", ["snapshot", "sync"])
@pytest.mark.parametrize("job_state", ["running", "result"])
@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (_event(0, "execution_deadline_exhausted", "failed"), "failed"),
        (_event(0, "completed", "completed", **DEADLINE), "failed"),
        (_event(0, "skipped_halted", "skipped_halted", reason="operator_pause"), "cancelled"),
        (_event(0, "cancelled", "cancelled", reason="operator_stop"), "cancelled"),
        (_event(0, "quality_gate_waiting", "machine_repair_required"), "incomplete"),
        (_event(0, "chapter_pipeline_started"), "running"),
    ],
    ids=[
        "typed-failure",
        "terminal-over-completed",
        "halted",
        "cancelled",
        "attention",
        "progress",
    ],
)
def test_worker_event_terminal_status(surface, job_state, event, expected, monkeypatch, tmp_path):
    # An ARQ result is transport evidence; progress alone cannot prove completion.
    if job_state == "result" and expected == "running":
        expected = "incomplete"
    status, history = _project(surface, monkeypatch, tmp_path, [event], job_state)
    _assert_history(history, [event])
    assert status == expected


@pytest.mark.parametrize("surface", ["snapshot", "sync"])
def test_explicit_clean_completion_with_arq_result(surface, monkeypatch, tmp_path):
    events = [_event(0, "completed", "completed", chapters_written=2)]
    status, history = _project(surface, monkeypatch, tmp_path, events, "result")
    _assert_history(history, events)
    assert status == "completed"


@pytest.mark.parametrize("surface", ["snapshot", "sync"])
def test_failed_root_is_not_revived_by_late_progress(surface, monkeypatch, tmp_path):
    events = [
        _event(0, "project_repair_started", attempt_id="attempt-a"),
        _event(1, "failed", "failed", attempt_id="attempt-a", error="provider error"),
        _event(2, "chapter_pipeline_started", attempt_id="attempt-a"),
    ]
    status, history = _project(surface, monkeypatch, tmp_path, events)
    _assert_history(history, events)
    assert status == "failed"


@pytest.mark.parametrize("surface", ["snapshot", "sync"])
@pytest.mark.parametrize("identity_key", ["attempt_id", "task_attempt_id", "workflow_run_id"])
@pytest.mark.parametrize("late_terminal", [False, True], ids=["new-root", "stale-terminal"])
def test_new_root_can_start_a_new_attempt_and_ignore_old_terminal(
    surface, identity_key, late_terminal, monkeypatch, tmp_path
):
    old = {identity_key: "old-run"}
    new = {identity_key: "new-run"}
    events = [
        _event(0, "project_repair_started", **old),
        _event(1, "execution_deadline_exhausted", "failed", **old, **DEADLINE),
        _event(2, "project_repair_started", **new),
        _event(3, "project_repair_chapter_started", **new),
    ]
    if late_terminal:
        events.append(_event(4, "failed", "failed", **old, **DEADLINE))
    status, history = _project(surface, monkeypatch, tmp_path, events)
    _assert_history(history, events)
    assert status == "running"


@pytest.mark.parametrize("surface", ["snapshot", "sync"])
@pytest.mark.parametrize("child_stage", ["scene_started", "project_repair_chapter_started"])
def test_changed_child_workflow_cannot_reopen_failed_root(
    surface, child_stage, monkeypatch, tmp_path
):
    events = [
        _event(0, "project_repair_started", workflow_run_id="root-run"),
        _event(1, "failed", "failed", workflow_run_id="root-run"),
        _event(2, child_stage, workflow_run_id="different-child-run"),
    ]
    status, history = _project(surface, monkeypatch, tmp_path, events)
    _assert_history(history, events)
    assert status == "failed"


def test_sync_keeps_failed_state_across_polls_when_only_late_progress_arrives(
    monkeypatch, tmp_path
):
    events = [
        _event(0, "project_repair_started", attempt_id="attempt-a"),
        _event(1, "failed", "failed", attempt_id="attempt-a"),
    ]
    _install_redis(monkeypatch, events)
    manager = _manager(tmp_path)
    assert manager.sync_progress_from_worker_redis("redis://stub") == 1
    task = manager._tasks["card"]
    assert task.status == "failed"
    events.append(_event(2, "chapter_pipeline_started", attempt_id="attempt-a"))
    assert manager.sync_progress_from_worker_redis("redis://stub") == 1
    _assert_history(task.progress_events, events)
    assert task.status == "failed"


@pytest.mark.parametrize(
    "structured", [False, True], ids=["ordinary-failure", "execution-terminal"]
)
def test_sync_preserves_failure_when_redis_tail_loses_terminal(structured, monkeypatch, tmp_path):
    events = [
        _event(0, "project_repair_started", attempt_id="attempt-a"),
        _event(1, "failed", "failed", attempt_id="attempt-a", **(DEADLINE if structured else {})),
    ]
    original = list(events)
    _install_redis(monkeypatch, events)
    manager = _manager(tmp_path)
    assert manager.sync_progress_from_worker_redis("redis://stub") == 1
    task = manager._tasks["card"]
    assert task.status == "failed"

    # Simulate Redis list trimming: the next poll can no longer see the failure.
    late = _event(2, "scene_started", attempt_id="attempt-a")
    events[:] = [late]
    assert manager.sync_progress_from_worker_redis("redis://stub") == 1
    _assert_history(task.progress_events, [*original, late])
    assert task.status == "failed"


@pytest.mark.parametrize("identity_key", ["attempt_id", "task_attempt_id", "workflow_run_id"])
def test_same_manager_new_attempt_reopens_after_failed_poll_and_ignores_old_receipt(
    identity_key, monkeypatch, tmp_path
):
    old = {identity_key: "old-run"}
    new = {identity_key: "new-run"}
    events = [
        _event(0, "project_repair_started", **old),
        _event(1, "execution_deadline_exhausted", "failed", **old, **DEADLINE),
    ]
    original = list(events)
    _install_redis(monkeypatch, events)
    manager = _manager(tmp_path)
    assert manager.sync_progress_from_worker_redis("redis://stub") == 1
    task = manager._tasks["card"]
    assert task.status == "failed"

    restarted = [
        _event(2, "project_repair_started", **new),
        _event(3, "scene_started", **new),
    ]
    events[:] = restarted
    assert manager.sync_progress_from_worker_redis("redis://stub") == 1
    assert manager._tasks["card"] is task
    assert task.status == "running"

    late = _event(4, "execution_deadline_exhausted", "failed", **old, **DEADLINE)
    events.append(late)
    assert manager.sync_progress_from_worker_redis("redis://stub") == 1
    _assert_history(task.progress_events, original + restarted + [late])
    assert task.status == "running"


@pytest.mark.parametrize("fresh_progress", [False, True], ids=["unchanged-tail", "fresh-progress"])
def test_sync_active_to_result_without_completion_receipt_is_incomplete(
    fresh_progress, monkeypatch, tmp_path
):
    events = [
        _event(0, "project_repair_started", attempt_id="attempt-a"),
        _event(1, "scene_started", attempt_id="attempt-a"),
    ]
    client = _install_redis(monkeypatch, events)
    manager = _manager(tmp_path)
    assert manager.sync_progress_from_worker_redis("redis://stub") == 1
    task = manager._tasks["card"]
    assert task.status == "running"

    client.state = "result"
    if fresh_progress:
        events.append(_event(2, "scene_completed", attempt_id="attempt-a"))
    manager.sync_progress_from_worker_redis("redis://stub")
    _assert_history(task.progress_events, events)
    assert task.status == "incomplete"


@pytest.mark.parametrize("surface", ["snapshot", "sync"])
@pytest.mark.parametrize("job_state", ["running", "result"])
def test_completed_receipt_with_adopted_false_is_incomplete(
    surface, job_state, monkeypatch, tmp_path
):
    events = [_event(0, "completed", "completed", adopted=False)]
    status, history = _project(surface, monkeypatch, tmp_path, events, job_state)
    _assert_history(history, events)
    assert status == "incomplete"


@pytest.mark.parametrize("first_stage", ["completed", "cancelled"])
def test_sync_checks_late_failure_after_projected_terminal(first_stage, monkeypatch, tmp_path):
    events = [
        _event(0, "project_repair_started", attempt_id="a"),
        _event(1, first_stage, first_stage, attempt_id="a"),
    ]
    _install_redis(monkeypatch, events)
    manager = _manager(tmp_path)
    manager.sync_progress_from_worker_redis("redis://stub")
    assert manager._tasks["card"].status == first_stage
    events.append(_event(2, "failed", "failed", attempt_id="a", **DEADLINE))
    manager.sync_progress_from_worker_redis("redis://stub")
    assert manager._tasks["card"].status == "failed"
