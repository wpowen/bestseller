from datetime import UTC, datetime, timedelta

import pytest
import test_execution_budget_core as core_fixtures

from bestseller.infra.db.models import ExecutionBudgetModel
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger
from bestseller.services.execution_authorization import authorize_provider_completion
from bestseller.settings import BudgetSettings

ledger_db = core_fixtures.ledger_db


async def authorized(engine, scope="provider-test"):
    ledger = ExecutionLedger(
        engine, scope, BudgetSettings(execution_max_calls=1, execution_max_tokens=1)
    )
    await ledger.check()
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, ledger.scope_id)
        row.calls_used, row.tokens_charged = 139, 1019595
        row.deadline_at = datetime.now(UTC) - timedelta(days=2)
        await session.commit()
    args = {
        "scope_id": ledger.scope_id,
        "idempotency_key": "human-approval",
        "actor": "user",
        "authorization_ref": "user-message",
        "objective": "first chapter adoption and knowledge readback",
        "expected_max_calls": 1,
        "expected_max_tokens": 1,
    }
    first = await authorize_provider_completion(engine, **args)
    assert await authorize_provider_completion(engine, **args) == first
    return ledger, first


@pytest.mark.asyncio
async def test_expired_aggregate_preserved_and_provider_scoped_calls_continue(ledger_db):
    ledger, receipt = await authorized(ledger_db)
    for n in range(2):
        claim = await ledger.claim(str(n), str(n))
        assert ledger.deadline_at > datetime.now(UTC)
        attempt = await ledger.reserve(
            claim, "openai/MiniMax-M3", str(n), 2000000, endpoint="https://api.minimaxi.com/v1"
        )
        await ledger.settle(attempt, "completed", actual_tokens=500)
        await ledger.finish(claim, {"content": "result"})
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, ledger.scope_id)
        assert (row.calls_used, row.tokens_charged) == (141, 1020595)
        assert (row.max_calls, row.max_tokens) == (1, 1)
        assert row.deadline_at.replace(tzinfo=UTC) < datetime.now(UTC)
        assert row.metadata_json["provider_authorizations"]["human-approval"] == receipt
        assert row.status == "active"
    status = await ledger.reconciliation_status()
    assert status["execution_open"] and status["provider_authorization"]["provider"] == "minimax"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,endpoint",
    [
        ("openai/other", "https://api.minimaxi.com/v1"),
        ("openai/MiniMax-M3", "https://other.example/v1"),
        ("openai/MiniMax-M3", "http://api.minimaxi.com/v1"),
        ("openai/MiniMax-M3", None),
    ],
)
async def test_no_fallback_or_endpoint_escape(ledger_db, model, endpoint):
    ledger, _ = await authorized(ledger_db)
    claim = await ledger.claim("blocked", "hash")
    with pytest.raises(ExecutionBudgetError, match="execution_provider_not_authorized"):
        await ledger.reserve(claim, model, "hash", 10, endpoint=endpoint)
    async with ledger.sessions() as session:
        assert (await session.get(ExecutionBudgetModel, ledger.scope_id)).calls_used == 139


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,code", [("unknown", "execution_result_unknown"), ("cancelled", "execution_cancelled")]
)
async def test_unlimited_is_not_unstoppable(ledger_db, state, code):
    ledger, _ = await authorized(ledger_db)
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, ledger.scope_id)
        row.status = state
        await session.commit()
    with pytest.raises(ExecutionBudgetError, match=code):
        await ledger.check()


@pytest.mark.asyncio
async def test_closed_objective_cannot_send(ledger_db):
    ledger, _ = await authorized(ledger_db)
    from bestseller.services.execution_authorization import close_provider_completion

    args = {"scope_id": ledger.scope_id, "actor": "operator", "evidence_sha256": "a" * 64}
    receipt = await close_provider_completion(ledger_db, **args)
    assert await close_provider_completion(ledger_db, **args) == receipt
    with pytest.raises(ValueError, match="other evidence"):
        await close_provider_completion(ledger_db, **{**args, "evidence_sha256": "b" * 64})
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_closed"):
        await ledger.claim("extra-chapter", "hash")


@pytest.mark.asyncio
async def test_objective_cannot_close_with_unsettled_send(ledger_db):
    from bestseller.services.execution_authorization import close_provider_completion

    ledger, _ = await authorized(ledger_db)
    claim = await ledger.claim("still-running", "hash")
    await ledger.reserve(
        claim, "openai/MiniMax-M3", "hash", 200, endpoint="https://api.minimaxi.com/v1"
    )
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_inflight"):
        await close_provider_completion(
            ledger_db, scope_id=ledger.scope_id, actor="operator", evidence_sha256="a" * 64
        )
    # The reserved attempt remains charged and the policy stays active; it is
    # never converted into a completed objective to hide an unknown result.
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, ledger.scope_id)
        assert row.metadata_json["provider_authorization"]["status"] == "active"
        assert row.calls_used == 140


@pytest.mark.asyncio
async def test_stage_pause_retains_inflight_paid_result_then_reuses_after_handoff(ledger_db):
    from bestseller.services.execution_authorization import set_provider_completion_pause

    ledger, _ = await authorized(ledger_db)
    claim = await ledger.claim("inflight", "hash")
    attempt = await ledger.reserve(
        claim, "openai/MiniMax-M3", "hash", 200, endpoint="https://api.minimaxi.com/v1"
    )
    args = {"scope_id": ledger.scope_id, "actor": "operator", "reason": "narrow detail window"}
    await set_provider_completion_pause(ledger_db, paused=True, **args)
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_inflight"):
        await set_provider_completion_pause(ledger_db, paused=False, **args)
    paid = {"content": "known paid result", "model_name": "openai/MiniMax-M3"}
    await ledger.settle(attempt, "completed", actual_tokens=30, claim=claim, result=paid)
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_paused"):
        await ledger.assert_result_adoptable(claim)
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_paused"):
        await ledger.claim("new-send", "hash")
    await set_provider_completion_pause(ledger_db, paused=False, **args)
    replay = await ledger.claim("inflight", "hash")
    assert replay.cached_result == paid
    await ledger.assert_result_adoptable(replay, cached=True)
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, ledger.scope_id)
        assert (row.calls_used, row.tokens_charged) == (140, 1019625)
        assert [r["status"] for r in row.metadata_json["provider_pause_history"]] == [
            "paused",
            "active",
        ]


@pytest.mark.asyncio
async def test_new_objective_needs_new_authority_and_preserves_closed_receipts(ledger_db):
    from bestseller.services.execution_authorization import close_provider_completion

    ledger, _ = await authorized(ledger_db)
    close_args = {"scope_id": ledger.scope_id, "actor": "operator", "evidence_sha256": "a" * 64}
    first = await close_provider_completion(ledger_db, **close_args)
    grant = {
        "scope_id": ledger.scope_id,
        "idempotency_key": "second-objective",
        "actor": "user",
        "authorization_ref": "user-message",
        "objective": "next separately authorized objective",
        "expected_max_calls": 1,
        "expected_max_tokens": 1,
    }
    with pytest.raises(ValueError, match="new operator authorization"):
        await authorize_provider_completion(ledger_db, **grant)
    await authorize_provider_completion(
        ledger_db, **{**grant, "authorization_ref": "new-user-message"}
    )
    await ledger.check()
    second = await close_provider_completion(
        ledger_db, **{**close_args, "evidence_sha256": "b" * 64}
    )
    assert first["authorization_ref"] != second["authorization_ref"]
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, ledger.scope_id)
        assert len(row.metadata_json["completion_receipts"]) == 2
        assert len(row.metadata_json["provider_authorizations"]) == 2
        assert (row.calls_used, row.tokens_charged) == (139, 1019595)
