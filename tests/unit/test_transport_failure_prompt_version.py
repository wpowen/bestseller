"""Keep the declared version on the actual durable failure, never invent one."""
import httpx
import pytest
from test_execution_budget_core import (  # noqa: F401
    GatewaySession,
    gateway_request,
    gateway_settings,
)
from test_execution_budget_core import (
    ledger_db as ledger_db,
)
from test_provider_interrupt_transport_evidence import native_sdk, snapshot

from bestseller.services import budget, llm

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("version", ["7.0-citation-tuple-submission", "legacy-v0", None])
async def test_real_gateway_timeout_keeps_only_the_declared_prompt_version(
    ledger_db, monkeypatch, version,
):
    sends = []

    async def timeout(request):
        sends.append(1)
        raise httpx.ReadTimeout("private-failure-text-must-not-be-retained", request=request)

    native_sdk(monkeypatch, timeout)
    scope = "test:failure-version"
    request = gateway_request(operation_id="fixed-version-attempt", prompt_version=version)
    with budget.bind_execution_scope(scope):
        with pytest.raises(budget.ExecutionBudgetError, match="execution_result_unknown"):
            await llm.complete_text(GatewaySession(ledger_db), gateway_settings(), request)
    row, attempts, operations = await snapshot(ledger_db, scope)
    failures = [o.result_json for o in operations if o.status == "transport_failure_receipt"]
    assert len(failures) == 1 and sends == [1]
    failure = failures[0]
    assert "prompt_version" in failure
    assert failure["prompt_version"] == version
    assert failure["prompt_template"] == request.prompt_template
    assert failure["logical_role"] == request.logical_role
    assert row.status == "unknown" and row.calls_used == 1
    assert attempts[0].status == "unknown" and attempts[0].actual_tokens is None
    assert row.tokens_charged == attempts[0].reserved_tokens > 0
    assert "private-failure-text" not in str(failures)
