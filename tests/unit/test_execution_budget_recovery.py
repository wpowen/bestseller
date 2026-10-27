"""Recovery must not replenish or bypass an execution's lifetime limits.

These tests use synthetic projects and task files; no provider or production DB.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from bestseller.services import retry_ledger as ledger
from bestseller.services.budget import (
    ExecutionBudgetError, bind_execution_scope, record_execution_stop,
)
from bestseller.worker import self_heal, tasks
from bestseller.web.server import WebTaskManager, WebTaskState


class TerminalBudgetError(RuntimeError):
    terminal = True
    code = "execution_budget_exhausted"


@pytest.mark.parametrize("trigger", list(ledger.RetryTrigger))
def test_every_trigger_respects_lifetime_ceiling(trigger):
    decision = ledger.evaluate_retry(
        ledger.RetryChain(lifetime_attempts=20), trigger=trigger
    )
    assert decision.exhausted
    assert decision.chain.lifetime_attempts == 20


def test_alternating_reasons_share_and_migrate_old_retry_credit():
    metadata = {"retry_chain": {
        "generation_gate:outline_a": {"lifetime_attempts": 12, "auto_attempts_used": 2},
        "generation_gate:outline_b": {"lifetime_attempts": 8, "auto_attempts_used": 2},
    }}
    scope = ledger.generation_gate_scope("outline_c:renamed")
    chain = ledger.load_chain(metadata, scope=scope)
    assert chain.lifetime_attempts == 20
    assert ledger.evaluate_retry(chain, trigger=ledger.RetryTrigger.MANUAL).exhausted
    saved = ledger.store_chain(metadata, scope=scope, chain=chain)
    assert ledger.load_chain(saved, scope=scope) == chain  # migration is idempotent
    assert metadata["retry_chain"]["generation_gate:outline_a"]  # retain evidence


def test_terminal_cause_is_not_reclassified_as_a_planning_gate():
    try:
        raise TerminalBudgetError("limit")
    except TerminalBudgetError as cause:
        wrapped = RuntimeError("Whole-book outline semantic gate failed")
        wrapped.__cause__ = cause
    assert tasks._generation_gate_block(wrapped) is None


@pytest.mark.parametrize("code", [
    "execution_budget_exhausted", "execution_result_unknown",
    "execution_cancelled", "execution_ledger_unavailable",
])
def test_terminal_state_precedes_auto_resumable_gate(code):
    project = SimpleNamespace(metadata_json={
        "execution_terminal": {"terminal": True, "code": code},
        "last_generation_gate_reason": "volume_outline_gate_failed",
        "generation_gate_auto_retry_needed": True,
        "production_paused": True,
    })
    assert self_heal._project_resume_is_blocked(project)
    assert not self_heal._project_has_stale_auto_resumable_generation_gate(project)


def test_fingerprint_churn_without_measured_progress_does_not_reopen_credit():
    metadata = {"self_heal_last_chapters_total": 2, "self_heal_no_progress_attempts": 2,
                "self_heal_last_progress_fingerprint": "validator-a"}
    updated, abandoned = self_heal._compute_heal_progress_state(
        metadata, 2, progress_fingerprint="validator-b", max_attempts=3)
    assert abandoned
    assert updated["self_heal_no_progress_attempts"] == 3


@pytest.mark.asyncio
async def test_gate_budget_unavailable_stops_dispatch(monkeypatch):
    @asynccontextmanager
    async def unavailable():
        raise OSError("synthetic unavailable DB")
        yield
    monkeypatch.setattr(tasks, "get_server_session", unavailable)
    with pytest.raises(RuntimeError) as raised:
        await tasks._consume_generation_gate_retry_budget("synthetic", reason="outline_gate")
    assert raised.value.terminal
    assert raised.value.code == "execution_ledger_unavailable"


def _manager(tmp_path):
    manager = WebTaskManager(persist_path=tmp_path / "test-task-state.json")
    task = WebTaskState(task_id="stable-task", task_type="autowrite", status="failed",
                        created_at="2026-10-15T00:00:00+00:00",
                        updated_at="2026-10-15T00:00:00+00:00",
                        payload={"_run_conception": True})
    manager._tasks[task.task_id] = task
    return manager, task


def test_web_terminal_receipt_survives_restart_and_resume(tmp_path):
    manager, task = _manager(tmp_path)
    manager._mark_execution_terminal(task.task_id, TerminalBudgetError("limit"))
    restored = WebTaskManager(persist_path=manager._persist_path)
    result = restored.resume_autowrite_task(task.task_id, {"_run_conception": True})
    assert result["current_stage"] == "execution_budget_exhausted"
    assert result["result"]["execution_terminal"]["code"] == "execution_budget_exhausted"
    assert not restored._pending_auto_resume_ids


def test_late_progress_cannot_resurrect_web_terminal_task(tmp_path):
    manager, task = _manager(tmp_path)
    manager._mark_execution_terminal(task.task_id, TerminalBudgetError("limit"))
    manager._push_progress(task.task_id, "llm_complete", {"result": "late"})
    assert task.status == "failed"
    assert task.current_stage == "execution_budget_exhausted"


@pytest.mark.asyncio
async def test_worker_terminal_exception_is_returned_without_retry(monkeypatch):
    writes = []
    async def persist(project_slug, error, workflow_run_id=None):
        writes.append((project_slug, error.code))
    monkeypatch.setattr(tasks, "_persist_execution_terminal", persist)
    @tasks._execution_terminal_guard
    async def operation(ctx, workflow_run_id, payload):
        raise TerminalBudgetError("limit")
    class Redis:
        async def rpush(self, *args):
            return 1
        async def publish(self, *args):
            return 1
    with bind_execution_scope("project:synthetic"):
        result = await operation({"redis": Redis()}, "run-a", {"project_slug": "synthetic"})
    assert result["status"] == "execution_budget_exhausted"
    assert result["terminal"] is True
    assert writes == [("synthetic", "execution_budget_exhausted")]


@pytest.mark.asyncio
async def test_worker_latch_rejects_swallowed_budget_error_before_success(monkeypatch):
    writes = []
    async def persist(slug, error, workflow_run_id=None):
        writes.append(error.code)
    monkeypatch.setattr(tasks, "_persist_execution_terminal", persist)
    @tasks._execution_terminal_guard
    async def operation(ctx, workflow_run_id, payload):
        try:
            error = ExecutionBudgetError("execution_budget_exhausted", "project:synthetic")
            record_execution_stop(error)
            raise error
        except Exception:
            return {"status": "completed", "source": "static_fallback"}
    with bind_execution_scope("project:synthetic"):
        result = await operation({"redis": object()}, "run-a", {"project_slug": "synthetic"})
    assert result["status"] == "execution_budget_exhausted"
    assert writes == ["execution_budget_exhausted"]


def test_web_slot_latch_rejects_swallowed_budget_error(tmp_path):
    manager, task = _manager(tmp_path)
    def worker(task_id, payload):
        error = ExecutionBudgetError("execution_result_unknown", "conception:stable-task")
        record_execution_stop(error)
        try:
            raise error
        except Exception:
            manager._mark_completed(task_id, {"source": "static_fallback"})
    with bind_execution_scope("conception:stable-task"):
        manager._run_with_slot(task.task_id, worker, task.payload)
    assert task.status == "failed"
    assert task.current_stage == "execution_result_unknown"
    assert manager.resume_autowrite_task(task.task_id, task.payload)["status"] == "failed"


@pytest.mark.asyncio
async def test_authoritative_budget_readback_survives_new_session(tmp_path):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from bestseller.infra.db.models import ExecutionBudgetModel
    path = tmp_path / "recovery_budget_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(ExecutionBudgetModel.__table__.create)
        sessions = async_sessionmaker(engine)
        async with sessions() as session, session.begin():
            session.add(ExecutionBudgetModel(
                scope_id="conception:stable-task", status="unknown", calls_used=1,
                tokens_charged=100, max_calls=2, max_tokens=500,
                deadline_at=datetime.now(timezone.utc) + timedelta(minutes=1)))
        async with sessions() as session:
            with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
                await ledger.assert_execution_scope_open(session, "conception:stable-task")
            row = await session.get(ExecutionBudgetModel, "conception:stable-task")
            assert row.calls_used == 1 and row.tokens_charged == 100
    finally:
        await engine.dispose()


def test_legacy_resume_count_adopts_without_free_credit():
    chain = ledger.load_chain({"generation_gate_auto_resume_count": 20}, scope="generation_gate")
    assert chain.lifetime_attempts == 20
    assert ledger.evaluate_retry(chain, trigger=ledger.RetryTrigger.RECOVERY).exhausted


def test_creation_scope_survives_project_id_and_worker_resume():
    from uuid import uuid4
    project = SimpleNamespace(id=uuid4(), metadata_json={"execution_scope_id": "conception:stable-task"})
    assert ledger.project_execution_scope(project) == "conception:stable-task"
    project.metadata_json = {}
    assert ledger.project_execution_scope(project) == f"project:{project.id}"


@pytest.mark.asyncio
async def test_real_project_worker_does_not_commit_or_enqueue_swallowed_failure(monkeypatch):
    """Exercise the decorated production entrypoint, not just its pure helper."""
    from uuid import uuid4
    import bestseller.services.pipelines as pipelines
    import bestseller.services.projects as projects
    project = SimpleNamespace(id=uuid4(), metadata_json={}, slug="synthetic")
    events = []
    class Session:
        async def scalar(self, query):
            return project
    @asynccontextmanager
    async def session_scope():
        yield Session()
        events.append("session_commit")
    @asynccontextmanager
    async def guard(*args, **kwargs):
        yield
    class Reporter:
        async def emit(self, stage, data=None, **kwargs):
            events.append(stage)
    async def skip(*args, **kwargs):
        return None
    async def acquire(*args, **kwargs):
        return "acquired"
    async def get_project(*args, **kwargs):
        return project
    async def unchanged_native_admission(*args, **kwargs):
        assert kwargs['project_id'] == project.id
        return {'status': 'unchanged'}
    async def persist(slug, error, workflow_run_id=None):
        events.append("terminal_persisted")
    async def pipeline(**kwargs):
        events.clear()  # discard preflight read transaction, observe work txn
        error = ExecutionBudgetError("execution_budget_exhausted", f"project:{project.id}")
        record_execution_stop(error)
        try:
            raise error
        except Exception:
            return SimpleNamespace(model_dump=lambda **kwargs: {"status": "completed"})
    monkeypatch.setattr(tasks, "get_server_session", session_scope)
    monkeypatch.setattr(tasks, "_persist_execution_terminal", persist)
    monkeypatch.setattr(tasks, "RedisProgressReporter", lambda *args: Reporter())
    monkeypatch.setattr(tasks, "make_sync_callback", lambda reporter: None)
    for name in ("_skip_archived_project_if_needed", "_skip_halted_project_if_needed",
                 "_skip_outline_replan_project_if_needed"):
        monkeypatch.setattr(tasks, name, skip)
    monkeypatch.setattr(tasks, "_acquire_pipeline_live", acquire)
    monkeypatch.setattr(tasks, "_pipeline_live_guard", guard)
    monkeypatch.setattr(tasks, "_workflow_db_heartbeat", guard)
    monkeypatch.setattr(pipelines, "run_project_pipeline", pipeline)
    monkeypatch.setattr(projects, "get_project_by_slug", get_project)
    # Keep this unit on swallowed financial failure/transaction semantics;
    # native SQL admission has its own actual SQLite/PG integration coverage.
    from bestseller.services import chapter_review_reconciliation
    monkeypatch.setattr(chapter_review_reconciliation, "prepare_native_review_resume", unchanged_native_admission)
    result = await tasks.run_project_pipeline_task(
        {"redis": object()}, str(uuid4()), {"project_slug": "synthetic"})
    assert result["status"] == "execution_budget_exhausted"
    assert "terminal_persisted" in events
    assert "session_commit" not in events
    assert "completed" not in events


@pytest.mark.asyncio
async def test_commit_failure_cannot_return_retry_permission(monkeypatch):
    project = SimpleNamespace(metadata_json={})
    class Session:
        async def scalar(self, query):
            return project
    @asynccontextmanager
    async def failed_commit():
        yield Session()
        raise OSError("synthetic commit failure")
    monkeypatch.setattr(tasks, "get_server_session", failed_commit)
    with pytest.raises(ExecutionBudgetError, match="execution_ledger_unavailable"):
        await tasks._consume_generation_gate_retry_budget("synthetic", reason="outline")
