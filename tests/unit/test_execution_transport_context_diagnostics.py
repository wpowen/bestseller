"""Exercise actual send guards with no network and distinguish context defects."""
import httpx
import pytest

from bestseller.services import llm
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.execution_error_diagnostics import safe_error_chain
from test_execution_boundary_readonly_sqlite import admitted
from gateway_ledger_support import gateway_ledger as gateway_ledger

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("missing,reason", [("call", "missing_execution_call"), ("counter", "missing_send_counter")])
async def test_hook_missing_context_is_specific_and_never_reaches_send(gateway_ledger, missing, reason):
    _, _, _, call = await admitted(gateway_ledger)
    call_token = llm._execution_call_var.set(None if missing == "call" else call)
    count_token = llm._transport_send_count.set(None if missing == "counter" else [0])
    try:
        with pytest.raises(ExecutionBudgetError) as raised:
            await llm._guard_provider_http_send(httpx.Request("POST", "https://offline.invalid/", content=b"{}"))
        assert raised.value.code == "execution_ledger_unavailable"
        assert safe_error_chain(raised.value)[0]["guard_reason"] == reason
        assert call.attempts[0].get("wire_body_hash") is None
    finally:
        llm._transport_send_count.reset(count_token)
        llm._execution_call_var.reset(call_token)


@pytest.mark.asyncio
async def test_actual_httpx_wrapper_missing_counter_records_reason_and_sends_zero(gateway_ledger):
    _, _, _, call = await admitted(gateway_ledger)
    calls = []
    async def reply(request):
        calls.append(request)
        return httpx.Response(200, json={"offline": True})
    llm._install_physical_transport_guard()
    call_token = llm._execution_call_var.set(call)
    count_token = llm._transport_send_count.set(None)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            with pytest.raises(ExecutionBudgetError) as raised:
                await client.post("https://offline.invalid/", json={"public": "museum"})
        assert calls == []
        assert safe_error_chain(raised.value)[0]["guard_reason"] == "physical_guard_missing_counter"
    finally:
        llm._transport_send_count.reset(count_token)
        llm._execution_call_var.reset(call_token)


@pytest.mark.parametrize("value", ["private credentials must not appear", {"secret": "private"}])
def test_arbitrary_reason_text_is_not_retained(value):
    error = ExecutionBudgetError("execution_ledger_unavailable")
    error.guard_reason = value
    assert "guard_reason" not in safe_error_chain(error)[0]
