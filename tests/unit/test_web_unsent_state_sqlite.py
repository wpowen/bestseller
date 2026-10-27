"""Real task persistence and SQLite permission checks; no production/provider."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ExecutionBudgetModel, ExecutionAttemptModel
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger, ExecutionProviderNotSentError
from bestseller.services.retry_ledger import execution_terminal_code
from bestseller.settings import load_settings
from bestseller.web import server
from gateway_ledger_support import gateway_ledger as gateway_ledger

pytestmark = pytest.mark.unit


def prepare(monkeypatch, tmp_path, engine, stop=None):
    settings = load_settings()
    task = server.WebTaskState(task_id="synthetic-unsent", task_type="autowrite", status="running",
        created_at="2026-10-20T00:00:00+00:00", updated_at="2026-10-20T00:00:00+00:00",
        payload={"_run_conception": True})
    manager = server.WebTaskManager(persist_path=tmp_path / "test-web-unsent.json")
    manager._tasks[task.task_id] = task
    scope = f"conception:{task.task_id}"

    @asynccontextmanager
    async def sessions(_settings):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
            await session.commit()

    monkeypatch.setattr(server, "session_scope", sessions)
    monkeypatch.setattr(server, "load_settings", lambda: settings)

    async def seed():
        ledger = ExecutionLedger(engine, scope, settings.budget)
        claim = await ledger.claim("offline-unsent", "a" * 64)
        attempt = await ledger.reserve(claim, "offline-model", "a" * 64, 100)
        await ledger.record_transport_failure(attempt, {"schema": "transport-failure.v1",
            "physical_sends": 0, "exception_type": "ExecutionBudgetError"})
        await ledger.settle(attempt, "not_sent", actual_tokens=0)
        await ledger.finish(claim, None)
        async with AsyncSession(engine) as session:
            row = await session.get(ExecutionBudgetModel, scope)
            if stop == "expired":
                row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            elif stop:
                row.status = stop
            await session.commit()
        return attempt

    attempt = asyncio.run(seed())

    def snapshot():
        async def read():
            async with AsyncSession(engine) as session:
                row = await session.get(ExecutionBudgetModel, scope)
                paid = await session.get(ExecutionAttemptModel, attempt)
                return (row.status, row.calls_used, row.tokens_charged, row.max_calls,
                    row.max_tokens, row.deadline_at, row.metadata_json,
                    paid.status, paid.actual_tokens, paid.reserved_tokens)
        return asyncio.run(read())

    return manager, task, snapshot


def unsent_error():
    error = ExecutionProviderNotSentError("execution_provider_not_sent")
    error.__cause__ = ExecutionBudgetError("execution_ledger_unavailable")
    return error


def test_unsent_failure_keeps_original_task_retryable_without_replenishment(monkeypatch, tmp_path, gateway_ledger):
    manager, task, snapshot = prepare(monkeypatch, tmp_path, gateway_ledger)
    before = snapshot()
    manager._stop_execution_task(task.task_id, unsent_error())
    assert task.status == "failed"
    assert execution_terminal_code(task.result) is None
    assert task.result["execution_recoverable"] == {"terminal": False, "code": "execution_provider_not_sent"}
    assert snapshot() == before
    assert before[1:3] == (1, 0)  # Definite zero usage does not replenish calls.
    restored = server.WebTaskManager(persist_path=manager._persist_path)
    assert restored._tasks[task.task_id].result == task.result
    assert not restored._pending_auto_resume_ids


@pytest.mark.parametrize("state,code", [("unknown", "execution_result_unknown"),
    ("cancelled", "execution_cancelled"), ("expired", "execution_deadline_exhausted"),
    ("exhausted", "execution_budget_exhausted")])
def test_unsent_failure_does_not_bypass_current_scope_stop(monkeypatch, tmp_path, gateway_ledger, state, code):
    manager, task, snapshot = prepare(monkeypatch, tmp_path, gateway_ledger, state)
    before = snapshot()
    manager._stop_execution_task(task.task_id, unsent_error())
    assert execution_terminal_code(task.result) == code
    assert "execution_recoverable" not in task.result
    assert snapshot() == before
    restored = server.WebTaskManager(persist_path=manager._persist_path)
    assert execution_terminal_code(restored._tasks[task.task_id].result) == code
    assert not restored._pending_auto_resume_ids


def test_late_unsent_failure_preserves_existing_terminal_receipt(monkeypatch, tmp_path, gateway_ledger):
    manager, task, snapshot = prepare(monkeypatch, tmp_path, gateway_ledger)
    manager._mark_execution_terminal(task.task_id, ExecutionBudgetError("execution_result_unknown"))
    before, result = snapshot(), dict(task.result)
    manager._stop_execution_task(task.task_id, unsent_error())
    assert task.result == result
    assert snapshot() == before
