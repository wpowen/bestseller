"""Definite request rejection must not become repeated paid requests."""

import pytest

from bestseller.services import llm
from bestseller.services.budget import ExecutionBudgetError
from bestseller.settings import LLMRoleSettings, RetrySettings


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 402, 403, 422])
async def test_permanent_request_rejection_is_terminal_before_retry_or_fallback(
    monkeypatch, status
):
    calls = []

    class RejectedError(Exception):
        status_code = status

    async def reject(request, role):
        calls.append(request)
        # Even the old broad quota-text heuristic cannot route a 4xx auth or
        # content rejection into rate retries or another provider.
        raise RejectedError("quota exceeded; rejected request")

    async def no_backoff(*args):
        pytest.fail("permanent rejection must not enter backoff")

    monkeypatch.setattr(llm, "_paid_transport_attempt", reject)
    monkeypatch.setattr(llm, "_execution_backoff", no_backoff)
    request = llm.LLMCompletionRequest(
        logical_role="writer", system_prompt="s", user_prompt="u", fallback_response="not accepted"
    )
    role = LLMRoleSettings(model="openai/test", temperature=0.5, max_tokens=100, timeout_seconds=10)
    with pytest.raises(ExecutionBudgetError) as error:
        await llm._call_litellm_with_retry(request, role, RetrySettings(max_attempts=5))
    assert error.value.code.startswith("execution_provider_")
    assert len(calls) == 1
