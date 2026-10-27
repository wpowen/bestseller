"""Isolated, offline proof of linked acceptance admission. No live grant."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

import httpx
from openai import AsyncOpenAI
import pytest
from sqlalchemy import MetaData, JSON, ForeignKeyConstraint, select, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bestseller.infra.db import models as m
from bestseller.services import independent_acceptance as independent, llm
from bestseller.services.budget import ExecutionLedger, ExecutionBudgetError
from bestseller.settings import BudgetSettings, LLMRoleSettings

pytestmark = pytest.mark.unit


@pytest.fixture
async def case(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "independent_acceptance_test.sqlite3"))
    meta = MetaData()
    for model in (m.ExecutionBudgetModel, m.ExecutionOperationModel, m.ExecutionAttemptModel):
        table = model.__table__.to_metadata(meta)
        for column in table.columns:
            if isinstance(column.type, JSONB): column.type = JSON()
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint): table.constraints.remove(constraint)
    async with engine.begin() as c:
        await c.run_sync(meta.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    old_scope = "conception:original-fixture"
    old_deadline = datetime.now(timezone.utc) - timedelta(hours=1)
    async with factory() as s:
        s.add(m.ExecutionBudgetModel(scope_id=old_scope, max_calls=64, max_tokens=1000000,
            calls_used=56, tokens_charged=200000, deadline_at=old_deadline, status="unknown", metadata_json={"original": "preserved"}))
        for number in range(56):
            identity = str(number)
            unknown = number in (54, 55)
            unsent = number == 53
            s.add(m.ExecutionOperationModel(key="op"+identity, scope_id=old_scope,
                input_hash="hash"+identity, status="unknown" if unknown else "completed",
                owner="fixture", lease_until=old_deadline))
            s.add(m.ExecutionAttemptModel(id=identity, operation_key="op"+identity, scope_id=old_scope,
                input_hash="hash"+identity, model_name=independent.MODEL,
                status="unknown" if unknown else "not_sent" if unsent else "completed",
                reserved_tokens=6000 if number == 54 else 67132 if number == 55 else 100,
                actual_tokens=None if unknown else 0 if unsent else 30, created_at=old_deadline))
            if not unknown and not unsent:
                s.add(m.ExecutionOperationModel(key="return"+identity, scope_id=old_scope,
                    input_hash="hash"+identity, status="transport_result", owner="fixture", lease_until=old_deadline,
                    result_json={"attempt_id": identity, "result": {"input_tokens": 10, "output_tokens": 20}}))
        await s.commit()
    payload = {"concept_seed": "Independent synthetic story.", "production_profile": "complete",
               "target_chapters": 4, "target_words": 8000, "project_type": "fanqie_short",
               "llm_model_id": "minimax-m3.1-flash", "_run_conception": True}
    async def prepare(cap=5000000):
        return await independent.prepare(engine, old_scope=old_scope, authorization_id="isolated-new-test",
            actor="offline fixture", authorization_ref="fixture-explicit-token-cap", payload=payload,
            endpoint="https://api.minimaxi.com/v1", combined_token_cap=cap)
    yield SimpleNamespace(engine=engine, factory=factory, old_scope=old_scope, payload=payload, prepare=prepare)
    await engine.dispose()


@pytest.mark.asyncio
async def test_unproven_three_million_target_creates_no_authorization(case):
    with pytest.raises(ValueError, match="strict_combined_token_cap_not_proven"):
        await case.prepare(3000000)
    async with case.factory() as session:
        assert await session.scalar(select(func.count()).select_from(m.ExecutionBudgetModel)) == 1
        old = await session.get(m.ExecutionBudgetModel, case.old_scope)
        assert (old.status, old.calls_used, old.tokens_charged, old.max_calls) == ("unknown", 56, 200000, 64)


@pytest.mark.asyncio
async def test_submission_starts_exact_two_hours_once_without_changing_old_scope(case):
    receipt = await case.prepare()
    assert (await case.prepare()) == receipt
    scope = receipt["request"]["scope"]
    ledger = ExecutionLedger(case.engine, scope, BudgetSettings())
    with pytest.raises(ExecutionBudgetError, match="execution_submission_not_authorized"):
        await ledger.claim("premature", "hash")
    assert await independent.consume_ui(case.engine, {**case.payload, "concept_seed": "wrong"}) is None
    assert await independent.consume_ui(case.engine, case.payload) == scope.split(":", 1)[1]
    assert await independent.consume_ui(case.engine, case.payload) is None
    async with case.factory() as session:
        row = await session.get(m.ExecutionBudgetModel, scope)
        policy = independent.policy(row)
        started = datetime.fromisoformat(policy["submitted_at"])
        assert independent.utc(row.deadline_at) == started + timedelta(hours=2)
        assert row.calls_used == 0 and row.max_calls == 128
        old = await session.get(m.ExecutionBudgetModel, case.old_scope)
        assert independent.snapshot(old) == receipt["request"]["old_snapshot"]


async def armed(case):
    receipt = await case.prepare()
    await independent.consume_ui(case.engine, case.payload)
    scope = receipt["request"]["scope"]
    return receipt, ExecutionLedger(case.engine, scope, BudgetSettings())


def request_role():
    request = llm.LLMCompletionRequest(logical_role="critic", system_prompt="Offline fixture only.",
        user_prompt="Independent supplied story.", fallback_response="not an approval", prompt_template="conception_review")
    role = LLMRoleSettings(model=independent.MODEL, temperature=1, max_tokens=6500,
        timeout_seconds=30, api_base="https://api.minimaxi.com/v1")
    return request, role


@pytest.mark.asyncio
async def test_missing_or_local_estimate_proof_is_rejected_before_reserving(case):
    receipt, ledger = await armed(case)
    request, role = request_role()
    payload = llm._effective_input_payload(request, role)
    proof = await independent.quote(ledger, payload, role.api_base)
    claim = await ledger.claim("new", proof["input_hash"])
    for evidence in (None, {**proof, "input_tokens_upper": 100}):
        with pytest.raises(ExecutionBudgetError, match="execution_acceptance_reservation_invalid"):
            await ledger.reserve(claim, independent.MODEL, proof["input_hash"], 100,
                endpoint=role.api_base, independent_acceptance_proof=evidence)
    async with case.factory() as session:
        row = await session.get(m.ExecutionBudgetModel, ledger.scope_id)
        assert (row.max_calls, row.max_tokens, row.calls_used) == (128, 2000000, 0)
        assert await session.scalar(select(func.count()).select_from(m.ExecutionAttemptModel).where(m.ExecutionAttemptModel.scope_id == ledger.scope_id)) == 0


@pytest.mark.asyncio
async def test_changed_old_snapshot_or_paid_server_tools_rejected(case):
    receipt, ledger = await armed(case)
    request, role = request_role()
    payload = llm._effective_input_payload(request, role)
    with pytest.raises(ValueError, match="finite_provider_contract_not_applicable"):
        await independent.quote(ledger, {**payload, "tools": [{"type": "web_search"}]}, role.api_base)
    proof = await independent.quote(ledger, payload, role.api_base)
    claim = await ledger.claim("new", proof["input_hash"])
    async with case.factory() as session:
        old = await session.get(m.ExecutionBudgetModel, case.old_scope)
        old.calls_used += 1
        await session.commit()
    with pytest.raises(ExecutionBudgetError, match="execution_original_acceptance_changed"):
        await ledger.reserve(claim, independent.MODEL, proof["input_hash"], 1006500,
            endpoint=role.api_base, independent_acceptance_proof=proof)


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", [False, True])
async def test_actual_offline_sdk_reserves_full_context_serially_and_never_replays_unknown(case, monkeypatch, interrupt):
    receipt, ledger = await armed(case)
    request, role = request_role()
    seen = []
    active = maximum = 0
    async def wire(http_request):
        nonlocal active, maximum
        assert http_request.url.host == "api.minimaxi.com"
        active += 1
        maximum = max(maximum, active)
        seen.append(json.loads(http_request.content))
        await asyncio.sleep(.01)
        active -= 1
        if interrupt and len(seen) == 2:
            raise httpx.ReadTimeout("offline synthetic timeout")
        return httpx.Response(200, json={"id": "fixture", "object": "chat.completion", "created": 1,
            "model": "MiniMax-M3.1-Flash", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "offline response", "reasoning_content": "retained"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
        sdk = AsyncOpenAI(api_key="offline-fixture", base_url=role.api_base, http_client=http, max_retries=0)
        async def acompletion(**kwargs):
            assert kwargs["max_retries"] == kwargs["num_retries"] == 0
            allowed = {k:v for k,v in kwargs.items() if k not in {"model","timeout","num_retries","max_retries","drop_params","api_base","api_key","base_url","allowed_openai_params"}}
            return await sdk.chat.completions.create(model="MiniMax-M3.1-Flash", **allowed)
        monkeypatch.setattr(llm, "_get_litellm", lambda: SimpleNamespace(acompletion=acompletion))
        monkeypatch.setattr(llm, "_ensure_shared_litellm_http_client", lambda: None)
        async def call(index):
            claim = await ledger.claim("independent-"+str(index), llm._effective_input_hash(request, role))
            token = llm._execution_call_var.set(llm._ExecutionCall(ledger, claim))
            try:
                return await llm._paid_transport_attempt(request, role)
            finally:
                llm._execution_call_var.reset(token)
        returns = await asyncio.gather(call(1), call(2), return_exceptions=True)
        assert maximum == 1 and len(seen) == 2
        if interrupt:
            assert any(isinstance(x, ExecutionBudgetError) and x.code == "execution_result_unknown" for x in returns)
            with pytest.raises(ExecutionBudgetError): await call(3)
            assert len(seen) == 2
        else:
            assert all(x[0] == "offline response" for x in returns)
    async with case.factory() as session:
        rows = list(await session.scalars(select(m.ExecutionAttemptModel).where(m.ExecutionAttemptModel.scope_id == ledger.scope_id)))
        assert len(rows) == 2 and all(x.reserved_tokens == 1006500 for x in rows)
        budget = await session.get(m.ExecutionBudgetModel, ledger.scope_id)
        assert budget.calls_used == 2
        assert budget.tokens_charged == (1006530 if interrupt else 60)
