"""Persisted RUNNING is history, not a worker heartbeat."""
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
import subprocess

import pytest

from bestseller.web import server

pytestmark = pytest.mark.unit


class _Rows:
    """The read projections consume SQLAlchemy mapping rows and tuple rows."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    def mappings(self):
        return iter(self.rows)

    def __iter__(self):
        return iter(self.rows)


def _activity_row(slug, run):
    return {"slug": slug, "id": uuid4(), "workflow_type": "chapter_pipeline",
            "status": run.status, "current_step": None,
            "created_at": run.created_at, "updated_at": run.updated_at,
            "planner_heartbeat_at": None, "latest_step_at": None}


@pytest.mark.asyncio
async def test_execution_progress_does_not_run_september_history(monkeypatch):
    project = SimpleNamespace(id=uuid4(), slug="restored-history", title="Restored",
                              status="revising", target_chapters=50, language="zh-CN", metadata_json={})
    old = {"status": "running", "updated_at": datetime(2026, 9, 9, 4, 19, 10, tzinfo=UTC),
           "created_at": datetime(2026, 9, 9, 4, 9, 42, tzinfo=UTC),
           "workflow_run_id": uuid4(), "workflow_type": "chapter_pipeline",
           "current_step": None, "error_message": None, "planner_heartbeat_at": None,
           "latest_step_at": None, "failed_step_count": 0}

    class Session:
        async def execute(self, statement):
            assert statement.is_select
            if "workflow_run_id" in statement.selected_columns.keys():
                assert set(statement.selected_columns.keys()) == set(old)
                return _Rows([old])
            return _Rows()

        async def scalar(self, statement):
            assert statement.is_select
            return 0

    @asynccontextmanager
    async def scope(_):
        yield Session()

    async def get_project(*_):
        return project

    monkeypatch.setattr(server, "session_scope", scope)
    monkeypatch.setattr(server, "get_project_by_slug", get_project)
    value = await server._load_execution_progress_payload(SimpleNamespace(), project.slug)
    assert value["execution_active"] is False
    assert value["activity_status"] == "stale"
    assert not any(p["status"] == "running" for p in value["phases"])
    assert old["status"] == value["current"]["status"] == "running"  # no historical rewrite


@pytest.mark.parametrize("status", ["pending", "queued", "running"])
@pytest.mark.parametrize("proof", ["old", "missing", "recent_update", "recent_step", "owner", "future"])
def test_activity_requires_recent_positive_evidence(status, proof):
    now = datetime.now(UTC)
    old = now - timedelta(days=35)
    run = {"status": status, "updated_at": old, "created_at": old}
    if proof == "missing":
        run = {"status": status}
    elif proof == "recent_update":
        run["updated_at"] = now - timedelta(minutes=10)
    elif proof == "recent_step":
        run["steps"] = [{"created_at": (now - timedelta(minutes=2)).isoformat()}]
    elif proof == "future":
        run["updated_at"] = now + timedelta(days=10)
    value = server._workflow_activity_evidence(run, now=now, owner_active=proof == "owner")
    expected = proof in {"recent_update", "recent_step", "owner"}
    assert value["execution_active"] is expected
    assert value["status"] == ("active" if expected else "unknown" if proof == "missing" else "stale")
    assert run["status"] == status


@pytest.mark.asyncio
async def test_project_activity_query_does_not_count_old_status_only():
    now = datetime.now(UTC)
    project_id = uuid4()
    old = SimpleNamespace(status="running", updated_at=now - timedelta(days=35),
                          created_at=now - timedelta(days=35), metadata_json={})
    live = SimpleNamespace(status="running", updated_at=now, created_at=now, metadata_json={})

    class Session:
        async def execute(self, statement):
            assert statement.is_select
            rows = [_activity_row("old-book", old), _activity_row("live-book", live)]
            assert set(statement.selected_columns.keys()) == set(rows[0])
            return _Rows(rows)

    result = await server._load_workflow_activity_by_project(Session(), [project_id])
    assert result["old-book"]["execution_active"] is False
    assert result["old-book"]["status"] == "stale"
    assert result["live-book"]["execution_active"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("light", [True, False])
async def test_projects_api_preserves_history_without_counting_it_active(monkeypatch, light):
    now = datetime.now(UTC)
    project = SimpleNamespace(id=uuid4(), slug="restored-project", title="Restored", genre="fiction",
                              status="revising", language="zh-CN", metadata_json={}, project_type="linear",
                              target_chapters=50, target_word_count=130000, current_chapter_number=1,
                              created_at=now - timedelta(days=35), updated_at=now)
    run = SimpleNamespace(status="running", updated_at=now - timedelta(days=35),
                          created_at=now - timedelta(days=35), metadata_json={})

    class Session:
        async def execute(self, statement):
            assert statement.is_select
            row = _activity_row(project.slug, run)
            assert set(statement.selected_columns.keys()) == set(row)
            return _Rows([row])

    @asynccontextmanager
    async def scope(_):
        yield Session()

    async def projects(*_, **__):
        return [project]

    async def empty(*_, **__):
        return {}

    monkeypatch.setattr(server, "session_scope", scope)
    monkeypatch.setattr(server, "list_projects", projects)
    for name in ["_load_project_content_stats", "_load_project_chapter_state_counts",
                 "_load_project_autonomous_repair_task_counts", "_load_latest_project_repair_workflow_snapshots"]:
        monkeypatch.setattr(server, name, empty)
    settings = SimpleNamespace(redis=SimpleNamespace(url=None))
    row, = await server._load_projects_payload(settings, light=light)
    assert row["has_active_workflow"] is row["execution_active"] is False
    assert row["activity_status"] == "stale"
    assert row["book_state"] == "needs_attention"
    assert row["workflow_activity"]["stale_workflow_count"] == 1
    card = server._project_row_to_dashboard_task(row)
    assert card["status"] == "incomplete"
    assert card["activity_status"] == "stale"
    assert card["execution_active"] is False
    assert run.status == "running"


def test_stale_activity_is_not_running_in_actual_page_functions():
    from test_task_projection_adoption_progress import _js_functions
    functions = _js_functions(["isActiveTask", "dashboardProjectStatus", "dashboardProjectStage",
                               "repairPhaseOf", "classifyBookState",
                               "taskExecutionTerminalCode", "taskExecutionStopPresentation"])
    script = functions + """
const assert=require('node:assert/strict');
const stale={status:'running',project_status:'revising',activity_status:'stale',
 has_active_workflow:true,execution_active:true,synthetic_project:true};
assert.equal(isActiveTask(stale),false);
assert.equal(dashboardProjectStatus(stale),'incomplete');
assert.equal(dashboardProjectStage(stale),'workflow_activity_stale');
assert.equal(classifyBookState(stale).label,'待恢复');
assert.equal(isActiveTask({...stale,activity_status:'unknown'}),false);
"""
    result = subprocess.run(["/opt/homebrew/bin/node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_stale_repair_workflow_is_recovery_work_not_an_active_worker():
    now = datetime.now(UTC)
    project = SimpleNamespace(slug="restored-repair", title="Restored", status="revising",
                              target_chapters=50, metadata_json={}, created_at=now, updated_at=now)
    old = {"status": "running", "current_step": "repair_chapter_1",
           "updated_at": (now - timedelta(days=35)).isoformat(),
           "created_at": (now - timedelta(days=35)).isoformat()}
    repair = server._build_project_repair_status_payload(project, [], {}, old)
    assert repair["is_repairing"] is False
    assert repair["phase"] == "needs_attention"
    task = server._build_db_repair_task_summary(project, repair)
    assert task["status"] == "incomplete"
    assert task["execution_active"] is False
    assert task["activity_status"] == "stale"
    assert old["status"] == "running"
    owned = server._attach_repair_heal_owner_to_db_summary(task, "verified-owner", None)
    assert owned["execution_active"] is True
    assert owned["activity_status"] == "active"
