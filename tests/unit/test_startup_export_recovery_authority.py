from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import json
import os
import hashlib
from datetime import UTC, datetime

import pytest

from bestseller.infra.db.models import ChapterModel, ProjectModel
from bestseller.web import server

pytestmark = pytest.mark.unit


def _exports(tmp_path: Path):
    originals = {}
    for slug in ("harbor-mystery", "desert-journey"):
        directory = tmp_path / slug
        directory.mkdir()
        for name, body in (("project.md", f"# {slug}\n"),
                           ("chapter-001.md", "# First chapter\nSaved export.\n")):
            path = directory / name
            path.write_text(body, encoding="utf-8")
            originals[path] = path.read_bytes()
    return originals


def _settings(tmp_path: Path):
    return SimpleNamespace(output=SimpleNamespace(base_dir=str(tmp_path)),
                           redis=SimpleNamespace(url=""))


def _memory_session(monkeypatch):
    rows = []
    entries = []

    class Session:
        def add(self, row):
            if getattr(row, "id", None) is None:
                row.id = uuid4()
            rows.append(row)

        async def flush(self):
            pass

    @asynccontextmanager
    async def scope(_settings):
        entries.append(True)
        yield Session()

    async def projects(_session):
        return []

    async def project(_session, _slug):
        return None

    monkeypatch.setattr(server, "session_scope", scope)
    monkeypatch.setattr(server, "list_projects", projects)
    monkeypatch.setattr(server, "get_project_by_slug", project)
    monkeypatch.setattr(server, "is_project_delete_tombstoned", lambda *_: False)
    return rows, entries


@pytest.mark.asyncio
async def test_export_scan_requires_explicit_operator_authority(tmp_path, monkeypatch):
    originals = _exports(tmp_path)
    rows, entries = _memory_session(monkeypatch)
    result = await server._recover_projects_from_output(_settings(tmp_path))
    assert result == []
    assert entries == []
    assert rows == []
    assert all(path.read_bytes() == body for path, body in originals.items())


@pytest.mark.asyncio
async def test_explicit_export_recovery_keeps_originals_and_marks_provenance(tmp_path, monkeypatch):
    originals = _exports(tmp_path)
    rows, entries = _memory_session(monkeypatch)
    result = await server._recover_projects_from_output(
        _settings(tmp_path), operator_opt_in=True,
    )
    assert len(result) == 2
    assert len(entries) == 1
    assert len([row for row in rows if isinstance(row, ProjectModel)]) == 2
    assert len([row for row in rows if isinstance(row, ChapterModel)]) == 2
    assert all(row.metadata_json["recovery_authority"] == "operator_opt_in_unverified_export"
               for row in rows)
    assert all(path.read_bytes() == body for path, body in originals.items())


@pytest.mark.asyncio
async def test_export_history_does_not_adopt_or_claim_completed_chapters(tmp_path, monkeypatch):
    originals = _exports(tmp_path)
    rows, _entries = _memory_session(monkeypatch)
    manager = server.WebTaskManager()
    assert await manager._recover_tasks_from_output(_settings(tmp_path)) == 2
    assert rows == []
    for task in manager.list_tasks():
        assert task["status"] == "history"
        assert task["current_stage"] == "export_history_unverified"
        assert task["payload"] is None
        assert task["result"]["adopted"] is False
        assert task["result"]["chapters_written"] == 0
        assert task["result"]["chapters_on_disk"] == 1
        assert all(event["stage"] != "chapter_pipeline_completed"
                   for event in task["progress_events"])
    assert all(path.read_bytes() == body for path, body in originals.items())


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [1, 50])
async def test_existing_project_exports_are_history_not_execution(tmp_path, monkeypatch, target):
    originals = _exports(tmp_path)
    rows, _ = _memory_session(monkeypatch)
    old = datetime(2026, 9, 9, tzinfo=UTC)

    async def project(_session, slug):
        return SimpleNamespace(slug=slug, title="Saved project", target_chapters=target,
                               metadata_json={}, created_at=old, updated_at=old)

    monkeypatch.setattr(server, "get_project_by_slug", project)
    manager = server.WebTaskManager(persist_path=tmp_path / ".web_tasks.json")
    assert await manager._recover_tasks_from_output(_settings(tmp_path)) == 2
    for task in manager.list_tasks():
        assert task["task_type"] == "export_history"
        assert task["status"] == "history"
        assert task["payload"] is None
        assert task["created_at"] == task["updated_at"] == ""
        assert task["result"]["chapters_written"] == 0
        assert task["result"]["adopted"] is False
        assert task["result"]["recovered_from_output"] is True
        assert {e["stage"] for e in task["progress_events"]} == {"export_history_found", "chapter_export_found"}
        assert all(e["timestamp"] is None for e in task["progress_events"])
    before = manager._persist_path.read_bytes()
    for path in originals:
        os.utime(path, None)  # reader/export cache refresh is not writing progress
    assert await manager._recover_tasks_from_output(_settings(tmp_path)) == 0
    assert manager._persist_path.read_bytes() == before
    assert manager._pending_auto_resume_ids == []
    assert rows == []
    assert all(path.read_bytes() == body for path, body in originals.items())


def _legacy_export_task():
    slug = "restored-countdown"
    stamp = "2026-10-15T15:36:07.476371+00:00"
    return {
        "task_id": f"recovered-{slug}", "task_type": "autowrite", "status": "incomplete",
        "project_slug": slug, "title": "Saved project", "created_at": stamp, "updated_at": stamp,
        "current_stage": "incomplete_recovered", "error": "Recovered: 2/30 chapters on disk",
        "runtime_provenance": None, "payload": {"slug": slug, "premise": "saved private payload"},
        "result": {"project_slug": slug, "chapters_written": 2, "chapters_on_disk": 2,
                   "adopted": False, "project_exists": True, "target_chapters": 30},
        "progress_events": [{"timestamp": stamp, "stage": "project_pipeline_started", "payload": {"chapter_count": 30}},
                            {"timestamp": stamp, "stage": "chapter_pipeline_completed", "payload": {"chapter_number": 1}},
                            {"timestamp": stamp, "stage": "chapter_pipeline_completed", "payload": {"chapter_number": 4}}],
        "milestone_events": [], "cancel_requested": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("producer", ["v0", "v1"])
async def test_legacy_export_cache_archives_before_reload_and_cannot_resume(tmp_path, monkeypatch, queued, producer):
    _memory_session(monkeypatch)
    original = _legacy_export_task()
    if producer == "v0":
        original["result"] = {k: original["result"][k]
                              for k in ["project_slug", "chapters_written", "target_chapters"]}
    if queued:
        original.update(status="queued", current_stage="auto_resume_pending")
    path = tmp_path / ".web_tasks.json"
    raw = json.dumps([original], ensure_ascii=False, indent=2).encode()
    path.write_bytes(raw)
    manager = server.WebTaskManager(persist_path=path)
    task, = manager.list_tasks()
    assert task["status"] == "history"
    assert task["task_type"] == "export_history"
    assert task["payload"] is None
    assert task["created_at"] == task["updated_at"] == ""
    assert task["result"]["legacy_cache_entry"] == original
    assert task["result"]["chapters_written"] == 0
    assert all(e["stage"] in {"export_history_found", "chapter_export_found"} for e in task["progress_events"])
    assert all(e["timestamp"] is None for e in task["progress_events"])
    assert manager._pending_auto_resume_ids == []
    backup, = tmp_path.glob(".web_tasks.json.before-export-recovery-*.json")
    assert backup.read_bytes() == raw
    assert backup.stat().st_mode & 0o777 == 0o600
    assert hashlib.sha256(raw).hexdigest() in backup.name
    persisted = path.read_bytes()
    assert server.WebTaskManager(persist_path=path).list_tasks() == manager.list_tasks()
    assert path.read_bytes() == persisted
    assert len(list(tmp_path.glob(".web_tasks.json.before-export-recovery-*.json"))) == 1


@pytest.mark.parametrize("change", ["prefix_only", "manual_runtime", "different_id", "v0_execution_fields", "v0_count_mismatch"])
def test_export_cache_migration_does_not_guess_manual_task_provenance(tmp_path, change):
    original = _legacy_export_task()
    if change == "prefix_only":
        original["result"] = {"project_slug": original["project_slug"]}
    elif change == "manual_runtime":
        original["runtime_provenance"] = {"kind": "real_execution", "task_id": original["task_id"]}
    elif change == "different_id":
        original["task_id"] = "manual-task"
    else:
        original["result"] = {k: original["result"][k]
                              for k in ["project_slug", "chapters_written", "target_chapters"]}
        if change == "v0_execution_fields":
            original["progress_events"][1]["payload"]["workflow_run_id"] = str(uuid4())
        else:
            original["result"]["chapters_written"] = 3
    path = tmp_path / ".web_tasks.json"
    raw = json.dumps([original]).encode()
    path.write_bytes(raw)
    task, = server.WebTaskManager(persist_path=path).list_tasks()
    assert task["task_type"] == "autowrite"
    assert task["payload"] == original["payload"]
    assert task["progress_events"] == original["progress_events"]
    assert path.read_bytes() == raw
    assert not list(tmp_path.glob(".web_tasks.json.before-export-recovery-*.json"))


def test_empty_zombie_resume_does_not_wait_for_or_query_redis(monkeypatch):
    manager = server.WebTaskManager()

    def forbidden(*_, **__):
        pytest.fail("empty pending set must not touch Redis or database")

    monkeypatch.setattr(server, "_wait_for_self_heal_scan", forbidden)
    monkeypatch.setattr(server, "_fetch_heal_owned_slugs_by_kind", forbidden)
    monkeypatch.setattr(server, "_load_active_workflow_slugs_by_slug", forbidden)
    monkeypatch.setattr(server, "_load_existing_project_slugs", forbidden)
    assert manager.auto_resume_zombies(redis_url="redis://must-not-connect", settings=SimpleNamespace()) == []
    assert manager._pending_auto_resume_ids == []


def test_fresh_reader_export_cannot_rescue_watchdog_or_refresh_task_time(tmp_path, monkeypatch):
    slug = "reader-cache-refresh"
    directory = tmp_path / slug
    directory.mkdir()
    exported = directory / "chapter-004.md"
    exported.write_text("Rebuilt reader cache, not new writing.", encoding="utf-8")
    original = exported.read_bytes()
    old = "2026-09-09T04:19:10+00:00"
    manager = server.WebTaskManager()
    task = server.WebTaskState(task_id="actual-stuck-task", task_type="autowrite", status="running",
                               created_at=old, updated_at=old, project_slug=slug)
    manager._tasks[task.task_id] = task
    monkeypatch.setattr(server, "load_settings", lambda: _settings(tmp_path))
    before = task.to_dict()
    assert manager._rescue_from_disk_heartbeat(task.task_id, 2700) is False
    assert task.to_dict() == before
    failures = []
    monkeypatch.setattr(manager, "_rescue_from_worker_heal_job", lambda _: False)
    monkeypatch.setattr(manager, "_mark_unowned_delegated_task_incomplete", lambda _: False)
    monkeypatch.setattr(manager, "_mark_machine_repair_gate_incomplete", lambda _: False)
    monkeypatch.setattr(manager, "_mark_failed", lambda tid, reason: failures.append(tid))
    assert manager.watchdog_sweep() == 1
    assert failures == [task.task_id]
    assert task.updated_at == old
    assert exported.read_bytes() == original


@pytest.mark.parametrize("flag, expected", [
    (None, False), ("", False), ("false", False), ("typo", False),
    ("0", False), ("true", True), ("  YES  ", True), ("1", True),
])
@pytest.mark.parametrize("auto_resume", ["false", "true"])
def test_real_startup_keeps_export_adoption_separate_from_auto_resume(
    tmp_path, monkeypatch, flag, expected, auto_resume,
):
    _exports(tmp_path)
    rows, entries = _memory_session(monkeypatch)
    monkeypatch.setenv("BESTSELLER_AUTO_RESUME_ON_STARTUP", auto_resume)
    monkeypatch.delenv("BESTSELLER_WEB_TOKEN", raising=False)
    if flag is None:
        monkeypatch.delenv("BESTSELLER_RECOVER_EXPORTS_ON_STARTUP", raising=False)
    else:
        monkeypatch.setenv("BESTSELLER_RECOVER_EXPORTS_ON_STARTUP", flag)
    monkeypatch.setattr(server, "load_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(server, "initialize_process_runtime", lambda *_: None)
    monkeypatch.setattr(server.threading.Thread, "start", lambda *_: None)

    async def no_history(*_):
        return 0

    async def no_archives(*_):
        return set()

    monkeypatch.setattr(server.WebTaskManager, "_recover_tasks_from_output", no_history)
    monkeypatch.setattr(server.WebTaskManager, "auto_resume_zombies", lambda *_, **kwargs: [])
    monkeypatch.setattr(server, "_load_library_archived_project_slugs", no_archives)
    handlers = []

    class HttpServer:
        def __init__(self, _address, handler):
            handlers.append(handler)

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(server, "_FastThreadingHTTPServer", HttpServer)
    server.serve_web_app()
    assert len(entries) == int(expected)
    assert len(rows) == (4 if expected else 0)

    # An authenticated explicit POST remains an operator opt-in even when
    # startup import is disabled. No HTTP socket or database is used here.
    if flag is None:
        handler = object.__new__(handlers[0])
        handler.path = "/api/projects/recover"
        handler.headers = {}
        responses = []
        handler._send_json = lambda payload, **kwargs: responses.append(payload)
        handler.do_POST()
        assert responses[0]["recovered"] == 2
        assert len(entries) == 1
