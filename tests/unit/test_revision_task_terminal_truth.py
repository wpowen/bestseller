"""Terminal UI states must preserve rejected edits and operator halts."""

from contextlib import asynccontextmanager
import json
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from support.recovery_entry_seams import offline_review_resume, offline_web_owner  # noqa: F401

from bestseller.web import server


def _manager(tmp_path, task_type="chapter_revision"):
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    manager._tasks["task"] = server.WebTaskState(
        task_id="task",
        task_type=task_type,
        status="running",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        project_slug="novel",
        title="Novel",
        current_stage="running",
    )
    return manager


@pytest.mark.parametrize("ok", [False, True])
def test_revision_worker_preserves_service_result_and_truthful_terminal_state(
    tmp_path, monkeypatch, ok
):
    manager = _manager(tmp_path)
    result = {
        "ok": ok,
        "rejected_reason": None if ok else "requirements_not_met",
        "requirements": [{"requirement": "resolve ending", "passed": ok}],
        "result_version_no": 5 if ok else None,
    }

    @asynccontextmanager
    async def scope(_settings):
        yield object()

    async def apply(*args, **kwargs):
        return result

    monkeypatch.setattr(server, "load_settings", lambda: object())
    monkeypatch.setattr(server, "session_scope", scope)
    monkeypatch.setattr(server, "apply_chapter_revision_task", apply)
    manager._run_chapter_revision_worker(
        "task",
        {
            "project_slug": "novel",
            "chapter_number": 20,
            "operation": "issue",
            "rewrite_task_id": str(uuid4()),
        },
    )
    task = manager._tasks["task"]
    assert task.status == ("completed" if ok else "failed")
    assert task.result == result
    assert task.current_stage == ("completed" if ok else "chapter_revision_rejected")
    stages = [event["stage"] for event in task.progress_events]
    assert ("chapter_revision_completed" in stages) is ok
    persisted = json.loads((tmp_path / "tasks.json").read_text())
    assert "requirements_not_met" in json.dumps(persisted) if not ok else task.error is None


def _fake_redis(monkeypatch, job_state):
    event = json.dumps(
        {
            "ts": 1790000000.0,
            "message": "skipped_halted",
            "data": {"status": "skipped_halted", "reason": "operator_pause", "intent": "pause"},
            "event_type": "skipped_halted",
        }
    )

    class Redis:
        def exists(self, *keys):
            return int(
                any(
                    ("in-progress" in key if job_state == "running" else "result" in key)
                    and "autowrite:heal:novel" in key
                    for key in keys
                )
            )

        def zscore(self, *args):
            return None

        def lrange(self, key, *args):
            return [event] if key == "task:autowrite:heal:novel:progress" else []

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(from_url=lambda *a, **kw: Redis()))


@pytest.mark.parametrize("job_state", ["running", "result"])
def test_halted_worker_snapshot_is_terminal_even_before_arq_cleanup(monkeypatch, job_state):
    _fake_redis(monkeypatch, job_state)
    snapshot = server._load_worker_heal_progress_snapshot("redis://stub", "autowrite:heal:novel")
    assert snapshot["status"] == "cancelled"
    assert snapshot["current_stage"] == "skipped_halted"
    assert snapshot["latest_payload"]["intent"] == "pause"
    assert snapshot["status"] in server._REUSABLE_TASK_STATUSES


@pytest.mark.parametrize("job_state", ["running", "result"])
def test_halted_worker_sync_closes_existing_card_and_keeps_reason(tmp_path, monkeypatch, job_state):
    _fake_redis(monkeypatch, job_state)
    manager = _manager(tmp_path, "autowrite")
    assert manager.sync_progress_from_worker_redis("redis://stub") == 1
    task = manager._tasks["task"]
    assert task.status == "cancelled"
    assert task.current_stage == "skipped_halted"
    assert task.result["intent"] == "pause"
    assert task.error == "operator_pause"
