"""A repair sub-limit may narrow a native ledger, never create credit."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_execution_result_durability import ledger
from test_execution_result_durability import result_db as result_db

from bestseller.infra.db.models import ExecutionBudgetModel
from bestseller.services import budget

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def binding(scope, cap):
    factory = getattr(budget, "bind_additional_call_ceiling", None)
    assert callable(factory), "missing additional native reservation ceiling"
    return factory(scope, cap)


async def test_additional_ceiling_denies_before_attempt_and_keeps_global_ledger(result_db):
    native = ledger(result_db)
    claim = await native.claim("first", "h")
    with binding(native.scope_id, 1):
        attempt = await native.reserve(claim, "openai/synthetic", "h", 100)
        await native.settle(attempt, "completed", actual_tokens=7, claim=claim)
        another = await native.claim("second", "h")
        with pytest.raises(budget.ExecutionBudgetError, match="whole_repair_call_limit"):
            await native.reserve(another, "openai/synthetic", "h", 100)
    async with async_sessionmaker(result_db)() as s:
        row = await s.get(ExecutionBudgetModel, native.scope_id)
        assert (row.calls_used, row.tokens_charged, row.max_calls, row.max_tokens, row.status) == (
            1,
            7,
            4,
            100000,
            "active",
        )


async def test_nested_limits_take_minimum_and_foreign_scope_is_unaffected(result_db):
    native = ledger(result_db)
    claim = await native.claim("one", "h")
    with binding("different-scope", 1), binding(native.scope_id, 1), binding(native.scope_id, 99):
        attempt = await native.reserve(claim, "openai/synthetic", "h", 100)
        await native.settle(attempt, "completed", actual_tokens=7, claim=claim)
        second = await native.claim("two", "h")
        with pytest.raises(budget.ExecutionBudgetError, match="whole_repair_call_limit"):
            await native.reserve(second, "openai/synthetic", "h", 100)
    with binding(native.scope_id, 99):
        # The native ceiling remains authoritative, even for a looser sub-limit.
        for i in range(3):
            c = await native.claim("other" + str(i), "h")
            a = await native.reserve(c, "openai/synthetic", "h", 100)
            await native.settle(a, "completed", actual_tokens=7, claim=c)
        c = await native.claim("global-exhausted", "h")
        with pytest.raises(budget.ExecutionBudgetError, match="execution_budget_exhausted"):
            await native.reserve(c, "openai/synthetic", "h", 100)
