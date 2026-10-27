from uuid import uuid4

import pytest
from bestseller.infra.db.models import ExecutionBudgetModel
from bestseller.services.budget import ExecutionLedger, ExecutionBudgetError
from bestseller.settings import BudgetSettings
from test_execution_budget_core import ledger_db  # noqa: F401


@pytest.mark.asyncio
async def test_operator_phase_authorization_preserves_usage_and_is_idempotent(ledger_db):
    from bestseller.services.execution_authorization import authorize_execution_phase
    scope = f"operator-test:{uuid4()}"
    limits = BudgetSettings(execution_max_calls=1, execution_max_tokens=1000)
    ledger = ExecutionLedger(ledger_db, scope, limits)
    claim = await ledger.claim("done", "input")
    attempt = await ledger.reserve(claim, "offline", "input", 100)
    paid = {"content": "saved", "model_name": "offline"}
    await ledger.settle(attempt, "completed", actual_tokens=30, claim=claim, result=paid)
    await ledger.finish(claim, paid)
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, scope)
        deadline = row.deadline_at
    args = dict(scope_id=scope, idempotency_key="plan-and-first-chapter", actor="local-operator",
                reason="User requested bounded production verification", authorization_ref="local-user-message",
                expected_max_calls=1, expected_max_tokens=1000, additional_calls=3,
                additional_tokens=1000, additional_seconds=60)
    first = await authorize_execution_phase(ledger_db, **args)
    assert await authorize_execution_phase(ledger_db, **args) == first
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, scope)
        assert (row.calls_used, row.tokens_charged) == (1, 30)
        assert (row.max_calls, row.max_tokens) == (4, 2000)
        assert row.deadline_at.timestamp() == deadline.timestamp() + 60
        assert len(row.metadata_json["operator_authorizations"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unknown", "cancelled"])
async def test_operator_budget_does_not_unfreeze_unknown_or_cancelled_work(ledger_db, state):
    from bestseller.services.execution_authorization import authorize_execution_phase
    scope = f"operator-test:{uuid4()}"
    ledger = ExecutionLedger(ledger_db, scope, BudgetSettings())
    await ledger.claim("original", "input")
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, scope)
        row.status = state
        await session.commit()
    with pytest.raises(ExecutionBudgetError):
        await authorize_execution_phase(ledger_db, scope_id=scope, idempotency_key="phase",
            actor="operator", reason="bounded verification", authorization_ref="user request",
            expected_max_calls=64, expected_max_tokens=1000000,
            additional_calls=3, additional_tokens=1000, additional_seconds=60)
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, scope)
        assert row.status == state and "operator_authorizations" not in row.metadata_json


@pytest.mark.asyncio
async def test_missing_operator_evidence_rejected_before_mutation(ledger_db):
    from bestseller.services.execution_authorization import authorize_execution_phase
    with pytest.raises(ValueError):
        await authorize_execution_phase(ledger_db, scope_id="operator-test:missing", idempotency_key="phase",
            actor="", reason="", authorization_ref="", expected_max_calls=1, expected_max_tokens=1000,
            additional_calls=1, additional_tokens=1000, additional_seconds=60)


@pytest.mark.asyncio
async def test_unsettled_send_cannot_be_reclassified_as_new_phase(ledger_db):
    from bestseller.services.execution_authorization import authorize_execution_phase
    scope = f"operator-test:{uuid4()}"
    ledger = ExecutionLedger(ledger_db, scope, BudgetSettings())
    claim = await ledger.claim("unsettled", "input")
    await ledger.reserve(claim, "offline", "input", 100)
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_inflight"):
        await authorize_execution_phase(ledger_db, scope_id=scope, idempotency_key="phase",
            actor="operator", reason="bounded verification", authorization_ref="user request",
            expected_max_calls=64, expected_max_tokens=1000000,
            additional_calls=3, additional_tokens=1000, additional_seconds=60)


@pytest.mark.asyncio
async def test_expired_window_restarts_once_from_audited_now_without_new_credit(ledger_db):
    from datetime import UTC, datetime, timedelta
    from bestseller.services.execution_authorization import authorize_execution_phase
    scope = f"operator-test:{uuid4()}"
    ledger = ExecutionLedger(ledger_db, scope, BudgetSettings())
    claim = await ledger.claim("closed-unsent", "input")
    attempt = await ledger.reserve(claim, "offline", "input", 100)
    await ledger.settle(attempt, "not_sent", actual_tokens=0)
    await ledger.finish(claim, None)
    old = datetime.now(UTC) - timedelta(hours=3)
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, scope)
        row.deadline_at = old
        await session.commit()
    args = dict(scope_id=scope, idempotency_key="one-hour-original-task",
        actor="human-fixture", reason="continue original end-to-end verification",
        authorization_ref="explicit-user-continue", expected_max_calls=64,
        expected_max_tokens=1000000, additional_calls=0, additional_tokens=0,
        additional_seconds=3600, renew_from_now=True)
    first = await authorize_execution_phase(ledger_db, **args)
    assert await authorize_execution_phase(ledger_db, **args) == first
    start = datetime.fromisoformat(first["authorized_at"])
    end = datetime.fromisoformat(first["after"]["deadline_at"])
    assert (end - start).total_seconds() == 3600
    assert first["before"]["deadline_at"] == old.isoformat()
    assert first["request"]["deadline_anchor"] == "authorization_time"
    for key in ("max_calls", "max_tokens", "calls_used", "tokens_charged"):
        assert first["before"][key] == first["after"][key]
    assert first["after"]["calls_used"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["active", "calls", "tokens", "too_long", "unknown", "cancelled"])
async def test_restart_rejects_active_or_frozen_scope_and_resource_increases(ledger_db, change):
    from datetime import UTC, datetime, timedelta
    from bestseller.services.execution_authorization import authorize_execution_phase
    scope = f"operator-test:{uuid4()}"
    ledger = ExecutionLedger(ledger_db, scope, BudgetSettings())
    await ledger.check()
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, scope)
        if change != "active":
            row.deadline_at = datetime.now(UTC) - timedelta(hours=3)
        if change in {"unknown", "cancelled"}:
            row.status = change
        await session.commit()
        before = (row.max_calls, row.max_tokens, row.calls_used, row.tokens_charged, row.deadline_at.replace(tzinfo=UTC).isoformat(), row.status)
    args = dict(scope_id=scope, idempotency_key="restart-rejected", actor="fixture",
        reason="explicit bounded resume", authorization_ref="fixture-user",
        expected_max_calls=64, expected_max_tokens=1000000,
        additional_calls=int(change == "calls"), additional_tokens=int(change == "tokens"),
        additional_seconds=3601 if change == "too_long" else 3600, renew_from_now=True)
    with pytest.raises((ValueError, ExecutionBudgetError)):
        await authorize_execution_phase(ledger_db, **args)
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, scope)
        assert (row.max_calls, row.max_tokens, row.calls_used, row.tokens_charged, row.deadline_at.replace(tzinfo=UTC).isoformat(), row.status) == before
        assert not (row.metadata_json or {}).get("operator_authorizations")
