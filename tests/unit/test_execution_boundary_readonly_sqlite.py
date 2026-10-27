"""The real pre-send boundary reads permission, never reserves or writes."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, ProjectModel
from bestseller.services import llm
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger, current_execution_scope
from bestseller.settings import load_settings
from gateway_ledger_support import gateway_ledger as gateway_ledger

pytestmark = pytest.mark.unit


async def admitted(engine):
    ledger = ExecutionLedger(engine, current_execution_scope(), load_settings().budget)
    claim = await ledger.claim("synthetic-boundary", "d" * 64)
    attempt = await ledger.reserve(claim, "offline", "d" * 64, 100)
    call = llm._ExecutionCall(ledger, claim)
    call.attempts.append({"attempt_id": attempt, "effective_input_hash": "d" * 64})
    return ledger, claim, attempt, call


@pytest.mark.asyncio
async def test_admitted_boundary_succeeds_during_unrelated_writer_without_mutating_ledger(gateway_ledger):
    import asyncio
    ledger, claim, attempt, call = await admitted(gateway_ledger)
    deadline = ledger.deadline_at
    token = llm._execution_call_var.set(call)
    try:
        async with AsyncSession(gateway_ledger) as writer:
            await writer.execute(update(ProjectModel).where(ProjectModel.slug == "novel").values(title="Uncommitted fixture"))
            await asyncio.wait_for(llm._execution_boundary(), timeout=1)
            await writer.rollback()
        assert ledger.deadline_at == deadline
        async with AsyncSession(gateway_ledger) as session:
            row = await session.get(ExecutionBudgetModel, ledger.scope_id)
            paid = await session.get(ExecutionAttemptModel, attempt)
            op = await session.get(ExecutionOperationModel, claim.key)
            assert (row.calls_used, row.tokens_charged, row.status) == (1, 100, "active")
            assert paid.status == "reserved" and paid.actual_tokens is None
            assert op.status == "pending" and op.owner == claim.owner
    finally:
        llm._execution_call_var.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [("active", None), ("paused", "execution_authorization_paused"),
    ("closed", "execution_authorization_closed")])
async def test_completion_permission_keeps_its_finite_transport_horizon_and_pause_fences(gateway_ledger, status, code):
    ledger, _, attempt, call = await admitted(gateway_ledger)
    async with AsyncSession(gateway_ledger) as session:
        row = await session.get(ExecutionBudgetModel, ledger.scope_id)
        row.deadline_at = datetime.now(timezone.utc) - timedelta(days=2)
        row.metadata_json = {"provider_authorization": {"mode": "completion_bound", "status": status,
            "authorization_ref": "synthetic-human", "objective": "isolated museum fixture",
            "models": ["offline"], "endpoint_hosts": ["offline.invalid"]}}
        await session.commit()
    ledger.deadline_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    horizon = ledger.deadline_at
    token = llm._execution_call_var.set(call)
    try:
        if code:
            with pytest.raises(ExecutionBudgetError, match=code):
                await llm._execution_boundary()
        else:
            await llm._execution_boundary()
        assert ledger.deadline_at == horizon
        async with AsyncSession(gateway_ledger) as session:
            row = await session.get(ExecutionBudgetModel, ledger.scope_id)
            paid = await session.get(ExecutionAttemptModel, attempt)
            assert (row.calls_used, row.tokens_charged) == (1, 100)
            assert paid.status == "reserved"
    finally:
        llm._execution_call_var.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("state,code", [("unknown", "execution_result_unknown"),
    ("cancelled", "execution_cancelled"), ("expired", "execution_deadline_exhausted"),
    ("exhausted", "execution_budget_exhausted"), ("stopped", "execution_operation_stopped"),
    ("foreign_owner", "execution_operation_stopped"), ("missing", "execution_scope_missing")])
async def test_read_boundary_preserves_every_authoritative_stop(gateway_ledger, state, code):
    ledger, claim, attempt, call = await admitted(gateway_ledger)
    async with AsyncSession(gateway_ledger) as session:
        row = await session.get(ExecutionBudgetModel, ledger.scope_id)
        op = await session.get(ExecutionOperationModel, claim.key)
        if state == "expired":
            row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif state in {"unknown", "cancelled", "exhausted"}:
            row.status = state
        elif state == "stopped":
            op.status = "stopped"
        elif state == "foreign_owner":
            op.owner = "different-dispatcher"
        else:
            await session.delete(row)
        await session.commit()
    token = llm._execution_call_var.set(call)
    try:
        with pytest.raises(ExecutionBudgetError, match=code):
            await llm._execution_boundary()
        async with AsyncSession(gateway_ledger) as session:
            paid = await session.get(ExecutionAttemptModel, attempt)
            assert paid.status == "reserved" and paid.actual_tokens is None
            assert len(list(await session.scalars(select(ExecutionAttemptModel)))) == 1
    finally:
        llm._execution_call_var.reset(token)


@pytest.mark.asyncio
async def test_stream_liveness_uses_one_snapshot_without_historical_result_payload(gateway_ledger):
    from sqlalchemy import event
    ledger, claim, attempt, call = await admitted(gateway_ledger)
    statements = []
    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(gateway_ledger.sync_engine,"before_cursor_execute",capture)
    try:
        await ledger.assert_result_adoptable(claim,allow_pending=True)
    finally:
        event.remove(gateway_ledger.sync_engine,"before_cursor_execute",capture)
    assert len(statements)==1
    query=statements[0]
    assert "EXISTS" in query.upper() and "execution_attempts" in query
    assert "execution_operations.result_json" not in query
    assert "AS authorization" in query
    async with AsyncSession(gateway_ledger) as session:
        row=await session.get(ExecutionBudgetModel,ledger.scope_id)
        paid=await session.get(ExecutionAttemptModel,attempt)
        assert (row.calls_used,row.tokens_charged,row.status)==(1,100,"active")
        assert paid.status=="reserved" and paid.actual_tokens is None
