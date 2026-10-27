"""A paused unsent admission is resumable; a sent timeout remains charged unknown."""
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel
from bestseller.services import budget, llm
from bestseller.services.progress_context import TaskCancelledError
from test_execution_budget_core import ledger_db, GatewaySession, gateway_request, gateway_settings  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def native_sdk(monkeypatch, handle):
    async def complete(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await client.post("https://synthetic.invalid/completion", json={"model": kwargs["model"]})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="real transport fixture", tool_calls=None),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3),
        )
    monkeypatch.setattr(llm, "_get_litellm", lambda: SimpleNamespace(acompletion=complete))
    monkeypatch.setattr(llm, "_ensure_shared_litellm_http_client", lambda: None)


async def snapshot(engine, scope):
    async with async_sessionmaker(engine)() as s:
        row = await s.get(ExecutionBudgetModel, scope)
        attempts = list(await s.scalars(select(ExecutionAttemptModel)))
        ops = list(await s.scalars(select(ExecutionOperationModel)))
        return row, attempts, ops


async def test_pause_after_reservation_before_wire_resumes_same_identity_without_credit_reset(
    ledger_db, monkeypatch,
):
    wires = []
    async def handle(request):
        wires.append(1)
        return httpx.Response(200, json={"ok": True})
    native_sdk(monkeypatch, handle)
    original = llm._execution_boundary
    boundaries = 0
    async def pause_before_wire():
        nonlocal boundaries
        boundaries += 1
        if boundaries == 2:
            raise TaskCancelledError("operator pause")
        await original()
    monkeypatch.setattr(llm, "_execution_boundary", pause_before_wire)
    scope = "test:unsent-pause"
    request = gateway_request(operation_id="stable-body-review")
    with budget.bind_execution_scope(scope):
        with pytest.raises(TaskCancelledError):
            await llm.complete_text(GatewaySession(ledger_db), gateway_settings(calls=4), request)
    row, attempts, ops = await snapshot(ledger_db, scope)
    deadline = row.deadline_at
    assert row.status == "active" and row.calls_used == 1 and row.tokens_charged == 0
    assert attempts[0].status == "not_sent" and wires == []
    failure = next(o.result_json for o in ops if o.status == "transport_failure_receipt")
    assert failure["physical_sends"] == 0 and failure["operator_interruption"] is True
    monkeypatch.setattr(llm, "_execution_boundary", original)
    with budget.bind_execution_scope(scope):
        result = await llm.complete_text(GatewaySession(ledger_db), gateway_settings(calls=4), request)
    assert result.content == "real transport fixture" and wires == [1]
    row, _, ops = await snapshot(ledger_db, scope)
    assert (row.calls_used, row.tokens_charged, row.deadline_at) == (2, 5, deadline)
    assert len([o for o in ops if o.status == "unsent_reclaim_receipt"]) == 1


async def test_sent_timeout_records_safe_diagnostics_and_keeps_unknown_reservation(ledger_db, monkeypatch):
    async def timeout(request):
        raise httpx.ReadTimeout("sk-private-test-secret must never be recorded", request=request)
    native_sdk(monkeypatch, timeout)
    scope = "test:wire-timeout"
    with budget.bind_execution_scope(scope):
        with pytest.raises(budget.ExecutionBudgetError, match="execution_result_unknown"):
            await llm.complete_text(GatewaySession(ledger_db), gateway_settings(),
                                    gateway_request(operation_id="wire-timeout"))
    row, attempts, ops = await snapshot(ledger_db, scope)
    assert row.status == "unknown" and row.tokens_charged == attempts[0].reserved_tokens
    assert attempts[0].status == "unknown"
    failure = next(o.result_json for o in ops if o.status == "transport_failure_receipt")
    assert failure["physical_sends"] == 1 and failure["exception_type"] == "ReadTimeout"
    assert "sk-private-test-secret" not in str([o.result_json for o in ops])


async def test_local_sdk_failure_is_not_paid_unknown_or_global_terminal(ledger_db, monkeypatch):
    async def broken_sdk(*args):
        raise RuntimeError("sk-private-test-secret")
    monkeypatch.setattr(llm, "_call_litellm", broken_sdk)
    scope = "test:local-sdk-failure"
    with budget.bind_execution_scope(scope):
        with pytest.raises(budget.ExecutionProviderNotSentError) as caught:
            await llm.complete_text(GatewaySession(ledger_db), gateway_settings(),
                                    gateway_request(operation_id="same-local-request"))
        assert caught.value.terminal is False
        budget.raise_if_execution_stopped()
    row, attempts, ops = await snapshot(ledger_db, scope)
    assert row.status == "active" and row.calls_used == 1 and row.tokens_charged == 0
    assert attempts[0].status == "not_sent"
    assert "sk-private-test-secret" not in str([o.result_json for o in ops])


async def test_quarantined_unknown_remains_visible_after_scope_reopens(ledger_db):
    from bestseller.settings import BudgetSettings
    ledger = budget.ExecutionLedger(ledger_db, "test:visible-uncertainty", BudgetSettings(
        execution_max_calls=4, execution_max_tokens=10000, execution_deadline_seconds=60,
    ))
    claim = await ledger.claim("uncertain", "hash")
    attempt = await ledger.reserve(claim, "openai/synthetic", "hash", 200)
    await ledger.settle(attempt, "unknown")
    await ledger.reconcile(budget.ExecutionReconciliation(
        reconciliation_id="quarantine-proof", attempt_id=attempt, expected_input_hash="hash",
        decision="quarantine_charged", evidence_kind="dispatcher_exit", evidence_sha256="a" * 64,
        actor="test-operator", remote_operation_closed=False, local_dispatcher_stopped=True,
    ))
    status = await ledger.reconciliation_status()
    assert status["execution_open"] and status["attempts"] == []
    assert status["quarantined_remote_outcomes"] == 1
    assert status["calls_used"] == 1 and status["tokens_charged"] == 200
