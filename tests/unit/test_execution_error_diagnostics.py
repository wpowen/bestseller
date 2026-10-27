import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ExecutionOperationModel
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger, current_execution_scope
from bestseller.services.execution_error_diagnostics import safe_error_chain
from bestseller.settings import load_settings
from gateway_ledger_support import gateway_ledger as gateway_ledger

pytestmark = pytest.mark.unit


def diagnostic():
    class DriverFailure(Exception):
        sqlstate = "55P03"
    try:
        try:
            raise DBAPIError("SELECT secret_sql_text", {"key": "secret_parameter"}, DriverFailure("secret_driver_message"))
        except DBAPIError as inner:
            raise ExecutionBudgetError("execution_ledger_unavailable") from inner
    except ExecutionBudgetError as error:
        return safe_error_chain(error)


def test_driver_code_and_locations_survive_without_exception_text_or_sql():
    chain = diagnostic()
    text = json.dumps(chain)
    assert all(secret not in text for secret in ("secret_sql_text", "secret_parameter", "secret_driver_message", "statement", "params", "locals"))
    assert chain[0]["execution_code"] == "execution_ledger_unavailable"
    assert chain[-1]["sqlstate"] == "55P03"
    assert chain[0]["frames"][0]["function"] == "diagnostic"


def test_cycles_and_depth_are_bounded():
    root = RuntimeError("private message")
    current = root
    for _ in range(30):
        child = RuntimeError("private message")
        current.__cause__, current = child, child
    current.__cause__ = root
    assert len(safe_error_chain(root)) == 8


def test_deep_sdk_traceback_keeps_the_actual_innermost_raise_site():
    def actual_guard():
        raise RuntimeError("private message")
    def sdk_stack(depth):
        if depth:
            return sdk_stack(depth - 1)
        return actual_guard()
    try:
        sdk_stack(30)
    except RuntimeError as error:
        frames = safe_error_chain(error)[0]["frames"]
    assert len(frames) == 12
    assert frames[-1]["function"] == "actual_guard"


def test_actual_failure_journal_retains_safe_driver_diagnostics(gateway_ledger):
    async def exercise():
        ledger = ExecutionLedger(gateway_ledger, current_execution_scope(), load_settings().budget)
        claim = await ledger.claim("isolated-diagnostic", "b" * 64)
        attempt = await ledger.reserve(claim, "offline", "b" * 64, 100)
        await ledger.record_transport_failure(attempt, {"schema": "transport-failure.v1",
            "physical_sends": 0, "exception_type": "ExecutionBudgetError", "error_chain": diagnostic()})
        async with AsyncSession(gateway_ledger) as session:
            row = await session.scalar(select(ExecutionOperationModel).where(
                ExecutionOperationModel.status == "transport_failure_receipt"))
            assert row.result_json["error_chain"] == diagnostic()
            assert "secret_" not in json.dumps(row.result_json)
        await ledger.settle(attempt, "not_sent", actual_tokens=0)
        await ledger.finish(claim, None)
    asyncio.run(exercise())
