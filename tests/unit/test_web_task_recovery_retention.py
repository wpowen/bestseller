from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

import bestseller.services.llm as llm
from bestseller.web import server

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("Retention/reload must not dispatch, load settings, or contact services")

    monkeypatch.setattr(server.threading.Thread, "start", forbidden)
    monkeypatch.setattr(server.WebTaskManager, "_run_with_slot", forbidden)
    monkeypatch.setattr(server, "load_settings", forbidden)
    monkeypatch.setattr(server, "session_scope", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)


def _task(task_id: str, status: str = "failed", **changes) -> server.WebTaskState:
    old = datetime.now(UTC) - timedelta(days=3)
    stamp = old.isoformat()
    task = server.WebTaskState(
        task_id=task_id,
        task_type="autowrite",
        status=status,
        created_at=(old - timedelta(days=2)).isoformat(),
        updated_at=stamp,
        project_slug=None,  # Book identity need not have been resolved yet.
        title="Saved original task",
        current_stage=status,
        progress_events=[{"timestamp": stamp, "stage": status,
                          "payload": {"history": [{"chapter": 2, "accepted": False}]}}],
        milestone_events=[{"timestamp": stamp, "stage": "checkpoint",
                           "payload": {"receipt": {"source": "original"}}}],
        result={"chapters_written": 2, "target_chapters": 30,
                "checkpoint": {"receipts": ["prior-review"]}},
        error="Saved terminal reason" if status == "failed" else None,
        cancel_requested=status == "cancelled",
        payload={"premise": "Saved original input",
                 "continuation": {"history": [{"scope": [1, 2], "receipt": "prior"}]}},
        runtime_provenance={"task_id": task_id, "process": {"fingerprint": "original"}},
    )
    for key, value in changes.items():
        setattr(task, key, value)
    return task


@pytest.mark.parametrize("status", ["failed", "incomplete", "cancelled", "completed"])
@pytest.mark.parametrize("result", [None, {}, {"whole_book_complete": False},
                                    {"whole_book_complete": None},
                                    {"whole_book_complete": 1},
                                    {"whole_book_complete": "true"},
                                    {"checkpoint": {"whole_book_complete": True}}])
def test_original_envelope_survives_eviction_and_disk_reload(
    tmp_path: Path, status: str, result: dict | None,
) -> None:
    path = tmp_path / ".web_tasks.json"
    manager = server.WebTaskManager(persist_path=path)
    original = _task("original-task", status, result=deepcopy(result))
    ordinary = _task("ordinary-old", task_type="repair")
    expected = deepcopy(original.to_dict())
    assert len(expected) == 15
    with manager._lock:
        manager._tasks = {t.task_id: t for t in [original, ordinary]}
        manager._save_to_disk()

    # Evicting the control forces a real durable rewrite of the survivor.
    assert manager.evict_old_tasks() == 1
    assert original.to_dict() == expected
    assert manager.get_task(original.task_id) == expected
    assert json.loads(path.read_text()) == [expected]
    persisted = path.read_bytes()

    reloaded = server.WebTaskManager(persist_path=path)
    assert reloaded.get_task(original.task_id) == expected
    assert reloaded.list_tasks() == [expected]
    assert reloaded._pending_auto_resume_ids == []
    assert reloaded._executing_task_ids == set()
    assert reloaded.evict_old_tasks(max_age_seconds=0) == 0
    assert path.read_bytes() == persisted


@pytest.mark.parametrize("status", ["failed", "incomplete", "cancelled", "completed"])
def test_positive_completion_and_ordinary_old_tasks_still_expire(
    tmp_path: Path, status: str,
) -> None:
    path = tmp_path / ".web_tasks.json"
    manager = server.WebTaskManager(persist_path=path)
    tasks = [
        _task("whole-book-done", status, result={"whole_book_complete": True}),
        _task("ordinary-old", status, task_type="repair"),
        _task("no-payload", status, payload=None),
        _task("empty-payload", status, payload={}),
        _task("non-dict-payload", status, payload=["legacy"]),
        _task("ordinary-recent", status, task_type="repair",
              updated_at=datetime.now(UTC).isoformat()),
    ]
    expected = deepcopy(tasks[-1].to_dict())
    with manager._lock:
        manager._tasks = {t.task_id: t for t in tasks}
        manager._save_to_disk()
    assert manager.evict_old_tasks() == 5
    assert json.loads(path.read_text()) == [expected]
    reloaded = server.WebTaskManager(persist_path=path)
    assert reloaded.list_tasks() == [expected]
    assert reloaded._pending_auto_resume_ids == []


@pytest.mark.parametrize("status", ["running", "queued"])
def test_active_tasks_remain_protected(status: str) -> None:
    manager = server.WebTaskManager()
    original = _task("active-original", status, payload=None,
                     result={"whole_book_complete": True})
    expected = deepcopy(original.to_dict())
    manager._tasks[original.task_id] = original
    assert manager.evict_old_tasks(max_age_seconds=0) == 0
    assert original.to_dict() == expected
