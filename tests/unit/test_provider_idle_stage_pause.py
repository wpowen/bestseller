from copy import deepcopy

import pytest

from bestseller.infra.db.models import ExecutionBudgetModel, ExecutionOperationModel
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.execution_authorization import set_provider_completion_pause
from test_provider_completion_authorization import authorized, ledger_db  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_status", ["pending", "unknown"])
async def test_idle_pause_refuses_unsettled_operation_without_changing_policy(
    ledger_db, operation_status,
):
    ledger, _ = await authorized(ledger_db)
    claim = await ledger.claim("inflight", "exact-input")
    async with ledger.sessions() as session:
        op = await session.get(ExecutionOperationModel, claim.key)
        op.status = operation_status
        await session.commit()
        before = deepcopy((await session.get(ExecutionBudgetModel, ledger.scope_id)).metadata_json)
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_inflight"):
        await set_provider_completion_pause(
            ledger_db, scope_id=ledger.scope_id, paused=True, require_idle=True,
            actor="fixture-operator", reason="stage repair after current paid return",
        )
    async with ledger.sessions() as session:
        assert (await session.get(ExecutionBudgetModel, ledger.scope_id)).metadata_json == before


@pytest.mark.asyncio
async def test_idle_pause_after_settlement_keeps_paid_result_and_blocks_new_send(ledger_db):
    ledger, _ = await authorized(ledger_db)
    claim = await ledger.claim("done", "exact-input")
    attempt = await ledger.reserve(
        claim, "openai/MiniMax-M3", "exact-input", 100,
        endpoint="https://api.minimaxi.com/v1",
    )
    result = {"content": "known complete paid result", "model_name": "openai/MiniMax-M3"}
    await ledger.settle(attempt, "completed", actual_tokens=25, claim=claim, result=result)
    paused = await set_provider_completion_pause(
        ledger_db, scope_id=ledger.scope_id, paused=True, require_idle=True,
        actor="fixture-operator", reason="safe stage handoff",
    )
    assert paused["status"] == "paused"
    async with ledger.sessions() as session:
        op = await session.get(ExecutionOperationModel, claim.key)
        assert op.status == "completed" and op.result_json == result
        assert (await session.get(ExecutionBudgetModel, ledger.scope_id)).calls_used == 140
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_paused"):
        await ledger.claim("new", "next-input")
