"""Operator halt is reversible; execution-ledger termination remains durable.

Exercise worker control with in-memory rows and replace no provider calls.
No task body in this file performs generation, queue dispatch or DB I/O.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.services import production_control
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope
from bestseller.services.progress_context import (
    TaskCancelledError,
    check_cancellation,
    set_cancellation_checker,
)
from bestseller.worker import tasks

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["pause", "stop"])
async def test_operator_halt_then_run_does_not_poison_execution(monkeypatch, intent):
    project = SimpleNamespace(id=uuid4(), metadata_json={})
    row = SimpleNamespace(
        desired_state=intent,
        state_reason="operator request",
        requested_by="operator",
        requested_at=None,
        command_serial=1,
        resume_pending=False,
        resume_pending_at=None,
        detail_json={},
    )

    class Session:
        async def scalar(self, _stmt):
            return project

        async def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: row)

        async def flush(self):
            pass

    session = Session()

    @asynccontextmanager
    async def session_scope():
        yield session

    persisted = AsyncMock()
    monkeypatch.setattr(tasks, "get_server_session", session_scope)
    monkeypatch.setattr(tasks, "assert_execution_scope_open", AsyncMock())
    monkeypatch.setattr(tasks, "_persist_execution_terminal", persisted)
    reporter = SimpleNamespace(emit=AsyncMock())
    monkeypatch.setattr(tasks, "RedisProgressReporter", lambda *_args: reporter)
    completed = []

    @tasks._execution_terminal_guard
    async def operation(ctx, workflow_run_id, payload):
        await tasks._raise_if_project_halted(payload["project_slug"])
        completed.append(workflow_run_id)
        return {"status": "completed"}

    with bind_execution_scope(f"project:{project.id}"):
        with pytest.raises(TaskCancelledError):
            await operation({"redis": object()}, "first", {"project_slug": "test-book"})
        assert completed == []
        persisted.assert_not_awaited()
        assert "execution_terminal" not in project.metadata_json

        await production_control.request_run(session, project.id, actor="operator")
        result = await operation({"redis": object()}, "resumed", {"project_slug": "test-book"})

    assert result == {"status": "completed"}
    assert completed == ["resumed"]
    assert row.command_serial == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["execution_cancelled", "execution_result_unknown"])
async def test_operator_handling_does_not_reopen_ledger_terminal(monkeypatch, code):
    persisted = AsyncMock()
    monkeypatch.setattr(tasks, "_persist_execution_terminal", persisted)
    reporter = SimpleNamespace(emit=AsyncMock())
    monkeypatch.setattr(tasks, "RedisProgressReporter", lambda *_args: reporter)

    @tasks._execution_terminal_guard
    async def operation(ctx, workflow_run_id, payload):
        raise ExecutionBudgetError(code, "project:test-book")

    with bind_execution_scope("project:test-book"):
        result = await operation({"redis": object()}, "run", {"project_slug": "test-book"})
    assert result["terminal"] is True
    assert result["code"] == code
    persisted.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("halt_kind", ["archived", "halted"])
async def test_quality_closure_refuses_stopped_project_before_loading_runner(
    monkeypatch,
    halt_kind,
):
    monkeypatch.setattr(tasks, "set_ambient", lambda *_args: None)
    monkeypatch.setattr(tasks, "make_sync_callback", lambda *_args: None)
    monkeypatch.setattr(tasks, "set_cancellation_checker", lambda *_args: None)
    reporter = SimpleNamespace(emit=AsyncMock())
    monkeypatch.setattr(tasks, "RedisProgressReporter", lambda *_args: reporter)
    skipped = {"status": f"skipped_{halt_kind}", "project_slug": "test-book"}
    monkeypatch.setattr(
        tasks,
        "_skip_archived_project_if_needed",
        AsyncMock(return_value=skipped if halt_kind == "archived" else None),
    )
    monkeypatch.setattr(
        tasks,
        "_skip_halted_project_if_needed",
        AsyncMock(return_value=skipped if halt_kind == "halted" else None),
    )

    def forbidden_runner():
        pytest.fail("stopped project reached the closure runner")

    monkeypatch.setattr(tasks, "_load_closure_runner_module", forbidden_runner)
    result = await tasks.run_book_quality_closure_task.__wrapped__(
        {"redis": object()}, "closure", {"project_slug": "test-book"}
    )
    assert result == skipped
    reporter.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_quality_closure_binds_own_stop_check_for_nested_work(monkeypatch):
    monkeypatch.setattr(tasks, "set_ambient", lambda *_args: None)
    monkeypatch.setattr(tasks, "make_sync_callback", lambda *_args: None)
    reporter = SimpleNamespace(emit=AsyncMock())
    monkeypatch.setattr(tasks, "RedisProgressReporter", lambda *_args: reporter)
    monkeypatch.setattr(tasks, "_skip_archived_project_if_needed", AsyncMock(return_value=None))
    monkeypatch.setattr(tasks, "_skip_halted_project_if_needed", AsyncMock(return_value=None))
    halted = AsyncMock(side_effect=TaskCancelledError("operator paused closure"))
    monkeypatch.setattr(tasks, "_raise_if_project_halted", halted)

    @asynccontextmanager
    async def heartbeat(*_args, **_kwargs):
        yield

    async def runner(_args):
        await check_cancellation()
        pytest.fail("closure crossed the operator stop boundary")

    monkeypatch.setattr(tasks, "_workflow_db_heartbeat", heartbeat)
    monkeypatch.setattr(tasks, "_load_closure_runner_module", lambda: SimpleNamespace(_run=runner))
    set_cancellation_checker(None)
    try:
        with pytest.raises(TaskCancelledError, match="operator paused closure"):
            await tasks.run_book_quality_closure_task.__wrapped__(
                {"redis": object()}, "closure", {"project_slug": "test-book"}
            )
    finally:
        set_cancellation_checker(None)
    halted.assert_awaited_once_with("test-book")
