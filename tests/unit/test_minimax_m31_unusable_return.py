"""Actual offline SDK + SQLite: preserve failed paid returns, never retry unknown."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import json

import httpx
from openai import AsyncOpenAI
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel
from bestseller.services import llm, minimax_m31_protocol as protocol
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger, OperationClaim, current_execution_scope
from bestseller.settings import LLMRoleSettings, load_settings
from gateway_ledger_support import gateway_ledger as gateway_ledger, LedgerBoundSession

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["execution_result_unknown", "execution_budget_exhausted", "execution_cancelled"])
@pytest.mark.parametrize("genre", ["现实", "航海科幻"])
async def test_methodology_advisory_never_swallows_execution_terminal(monkeypatch, code, genre):
    from bestseller.services import concept_methodology_agent as agent
    terminal = ExecutionBudgetError(code, "conception:isolated-fiction")
    async def stop(*args, **kwargs):
        raise terminal
    monkeypatch.setattr(agent, "complete_text", stop)
    settings = SimpleNamespace(pipeline=SimpleNamespace(concept_methodology_heat_search=False))
    with pytest.raises(ExecutionBudgetError) as caught:
        await agent.select_concept_methodology(None, settings, genre=genre)
    assert caught.value is terminal


def role(effort=None):
    return LLMRoleSettings(model="openai/MiniMax-M3.1-Flash", temperature=.2,
        max_tokens=6500, timeout_seconds=30, api_base="https://api.minimaxi.com/v1",
        reasoning_effort=effort)


@pytest.mark.parametrize("cap,explicit,expected", [
    (1, None, "low"), (1800, None, "low"), (4096, None, "low"),
    (4097, None, "low"), (32768, None, "max"), (1800, "max", "max"),
    (1800, "high", "high"), (32768, "low", "low"),
])
def test_small_default_reasoning_matches_identity_and_wire_without_raising_cap(cap, explicit, expected):
    req = llm.LLMCompletionRequest(logical_role="planner", system_prompt="Public fixture",
        user_prompt="Unrelated museum scheduling question", fallback_response="not a grade",
        max_tokens_override=cap)
    selected = role(explicit)
    payload = llm._effective_input_payload(req, selected)
    wire = protocol.wire_options({"max_tokens": payload["max_tokens"]}, selected)
    assert payload["reasoning_effort"] == wire["reasoning_effort"] == expected
    assert payload["max_tokens"] == wire["max_completion_tokens"] == cap
    if explicit is None and cap <= 4096:
        assert llm._effective_input_hash(req, selected) != llm._effective_input_hash(req, role("max"))
    assert selected.reasoning_effort == explicit


@pytest.mark.parametrize("cap", [4097, 6500, 8192, 9000, 16000])
def test_narrowed_role_budget_does_not_repeat_the_medium_cap_reasoning_starvation(cap):
    selected = role().model_copy(update={"max_tokens": 32768})
    req = llm.LLMCompletionRequest(logical_role="planner", system_prompt="Public fixture",
        user_prompt="Museum scheduling", fallback_response="not a grade", max_tokens_override=cap)
    payload = llm._effective_input_payload(req, selected)
    wire = protocol.wire_options({"max_tokens": payload["max_tokens"]}, selected)
    assert payload["reasoning_effort"] == wire["reasoning_effort"] == "low"
    assert payload["max_tokens"] == wire["max_completion_tokens"] == cap
    explicit = selected.model_copy(update={"reasoning_effort": "max"})
    assert llm._effective_input_payload(req, explicit)["reasoning_effort"] == "max"


@pytest.mark.asyncio
@pytest.mark.parametrize("malformation", ["empty_stop", "no_choices", "legacy_function"])
async def test_unusable_actual_sdk_body_is_durable_but_unknown_stays_charged_and_stopped(
    gateway_ledger, monkeypatch, malformation,
):
    raw = {"id": "offline-physical-return", "object": "chat.completion", "created": 1,
        "model": "MiniMax-M3.1-Flash", "choices": [{"index": 0, "finish_reason": "length",
        "message": {"role": "assistant", "content": None, "reasoning_content": "retained incomplete thought"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 1800, "total_tokens": 1900}}
    if malformation == "no_choices":
        raw["choices"] = []
    if malformation == "empty_stop":
        # A complete reasoning-only length return is now accounted for as a
        # spent invalid completion; unexplained empty stop is still unknown.
        raw["choices"][0]["finish_reason"] = "stop"
    if malformation == "legacy_function":
        raw["choices"][0]["message"].update(content="answer", function_call={"name": "legacy", "arguments": "{}"})
    sends = []
    async def physical_send(request):
        assert request.url.host == "api.minimaxi.com"
        body = json.loads(request.content)
        assert body["max_completion_tokens"] == 1800 and body["reasoning_effort"] == "low"
        sends.append(body)
        return httpx.Response(200, json=raw)
    async with httpx.AsyncClient(transport=httpx.MockTransport(physical_send)) as http:
        sdk = AsyncOpenAI(api_key="offline-not-a-secret", base_url="https://api.minimaxi.com/v1",
            http_client=http, max_retries=0)
        async def acompletion(**kwargs):
            allowed = {k: v for k, v in kwargs.items() if k not in {
                "model", "timeout", "num_retries", "max_retries", "drop_params", "api_base", "api_key",
                "base_url", "allowed_openai_params"}}
            return await sdk.chat.completions.create(model="MiniMax-M3.1-Flash", **allowed)
        monkeypatch.setattr(llm, "_get_litellm", lambda: SimpleNamespace(acompletion=acompletion))
        monkeypatch.setattr(llm, "_ensure_shared_litellm_http_client", lambda: None)
        settings = load_settings(env={})
        settings.llm.mock = False
        settings.llm.planner = role()
        settings.llm.retry.max_attempts = 3
        settings.llm.retry.rate_limit_fallback_enabled = False
        req = llm.LLMCompletionRequest(workflow_run_id=uuid4(), logical_role="planner",
            system_prompt="Only this public museum fixture.", user_prompt="Make a schedule.",
            fallback_response="never used", max_tokens_override=1800)
        with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
            await llm.complete_text(LedgerBoundSession(), settings, req)
        with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
            await llm.complete_text(LedgerBoundSession(), settings, req)
    assert len(sends) == 1
    async with AsyncSession(gateway_ledger) as session:
        attempt = (await session.scalars(select(ExecutionAttemptModel))).one()
        budget = await session.get(ExecutionBudgetModel, current_execution_scope())
        receipt = (await session.scalars(select(ExecutionOperationModel).where(
            ExecutionOperationModel.status == "unusable_provider_return"))).one()
        op = await session.get(ExecutionOperationModel, attempt.operation_key)
        assert attempt.status == op.status == budget.status == "unknown"
        assert attempt.actual_tokens is None
        before = (budget.calls_used, budget.tokens_charged, budget.deadline_at, budget.status)
        assert budget.calls_used == 1 and budget.tokens_charged == attempt.reserved_tokens
        record = deepcopy(receipt.result_json)
        assert record["response"]["usage"]["completion_tokens"] == 1800
        assert record["response"]["choices"] == json.loads(json.dumps(record["response"]["choices"]))
        if raw["choices"]:
            assert record["response"]["choices"][0]["message"]["reasoning_content"] == "retained incomplete thought"
        assert record["usable"] is record["adoption_authorized"] is record["credit_released"] is False
        assert "result" not in record and len(record["wire_body_hash"]) == 64
        claim = OperationClaim(op.key, op.owner)
    ledger = ExecutionLedger(gateway_ledger, current_execution_scope(), settings.budget)
    await ledger.record_provider_return(claim, attempt.id, record["response"], wire_body_hash=record["wire_body_hash"])
    with pytest.raises(ExecutionBudgetError, match="execution_reconciliation_conflict"):
        await ledger.record_provider_return(claim, attempt.id, {**record["response"], "id": "changed"},
            wire_body_hash=record["wire_body_hash"])
    async with AsyncSession(gateway_ledger) as session:
        budget = await session.get(ExecutionBudgetModel, current_execution_scope())
        assert (budget.calls_used, budget.tokens_charged, budget.deadline_at, budget.status) == before
        saved = await session.get(ExecutionOperationModel, receipt.key)
        assert saved.result_json == record
