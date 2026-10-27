"""Offline transactional proof: objective continuation keeps old unknowns frozen."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from bestseller.infra.db import models as m
from bestseller.services import independent_acceptance as independent, conception_continuation as continuation
from bestseller.services import conception_completion_repair as repair
from bestseller.services.budget import ExecutionLedger, ExecutionBudgetError, completion_authorization, execution_snapshot
from bestseller.settings import BudgetSettings
from test_independent_acceptance import case, request_role
from test_conception_continuation import setup
from bestseller.services import llm

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unknown", "reconciled_charged"])
async def test_partial_usage_never_releases_unknown_or_quarantine(case, state):
    async with case.factory() as s:
        row = await s.get(m.ExecutionBudgetModel, case.old_scope)
        before = await independent.old_cost_proof(s, row)
        a = await s.get(m.ExecutionAttemptModel, "54"); a.status = state
        s.add(m.ExecutionOperationModel(key="partial54", scope_id=case.old_scope, input_hash=a.input_hash,
            status="unusable_provider_return", owner="offline", lease_until=row.deadline_at,
            result_json={"attempt_id":"54", "response":{"stream_complete":False,
                "usage":{"prompt_tokens":10,"completion_tokens":20}}}))
        await s.commit()
        after = await independent.old_cost_proof(s, row)
        assert after["tokens_upper"] == before["tokens_upper"]
        assert after["standard_micro_cny_upper"] == before["standard_micro_cny_upper"]
        assert a.actual_tokens is None and a.reserved_tokens == 6000


async def failed_leaf(case):
    x = await setup(case); await x.prepare()
    scope = await continuation.consume(case.engine, x.task_id, case.payload)
    now = datetime.now(timezone.utc); rid = uuid4()
    async with case.factory() as s:
        row = await s.get(m.ExecutionBudgetModel, scope)
        row.status="unknown"; row.calls_used=2; row.tokens_charged=1000330
        row.deadline_at=now-timedelta(seconds=1)
        result={"content":"retained packaging", "provider":"openai", "model_name":independent.MODEL,
            "input_tokens":10,"output_tokens":20,"finish_reason":"stop","llm_run_id":str(rid),
            "metadata":{"paid_call_origin":{"prompt_template":"packaging","logical_role":"critic"}}}
        s.add(m.ExecutionOperationModel(key="packaging",scope_id=scope,input_hash="packaging-hash",
            status="completed",owner="offline",lease_until=now,result_json=result))
        s.add(m.LlmRunModel(id=rid,logical_role="critic",provider="openai",model_name=independent.MODEL,
            prompt_template="packaging",input_tokens=10,output_tokens=20,metadata_json={}))
        for key,status,reserved,actual in [("packaging","completed",1000030,30),("hook-v1","unknown",1000300,None)]:
            s.add(m.ExecutionAttemptModel(id="attempt-"+key,scope_id=scope,operation_key=key,input_hash=key+"-hash",
                status=status,reserved_tokens=reserved,actual_tokens=actual,model_name=independent.MODEL,created_at=now))
        s.add(m.ExecutionOperationModel(key="hook-v1",scope_id=scope,input_hash="hook-v1-hash",status="unknown",owner="offline",lease_until=now))
        s.add(m.ExecutionOperationModel(key="hook-failure",scope_id=scope,input_hash="hook-v1-hash",status="transport_failure_receipt",
            owner="offline",lease_until=now,result_json={"attempt_id":"attempt-hook-v1","prompt_template":"hook_pull_judge","physical_sends":1}))
        s.add(m.ExecutionOperationModel(key="packaging-return",scope_id=scope,input_hash="packaging-hash",status="transport_result",
            owner="offline",lease_until=now,result_json={"attempt_id":"attempt-packaging","result":{"input_tokens":10,"output_tokens":20}}))
        await s.commit()
    async def prepare():
        return await repair.prepare(case.engine,task_id=x.task_id,payload=case.payload,source_scope=scope,
            authorization_id="offline-explicit-hook-v2",authorization_ref="offline newer user completion instruction",
            actor="offline test",objective="same four chapter story through verified export",
            repaired_template="hook_pull_judge",repaired_version="v2")
    return x, scope, prepare


@pytest.mark.asyncio
async def test_objective_handoff_preserves_history_and_original_expiry(case):
    x, source, prepare = await failed_leaf(case)
    async with case.factory() as s:
        before=independent.snapshot(await s.get(m.ExecutionBudgetModel, source))
    grant=await prepare(); assert await prepare()==grant
    req=grant["request"]; assert len(req["cached_results"])==28
    assert "hook-v1-hash" in req["blocked_input_hashes"]
    scope=await continuation.consume(case.engine,x.task_id,case.payload)
    async with case.factory() as s:
        row=await s.get(m.ExecutionBudgetModel,scope)
        assert execution_snapshot(row)["effective_limits"] == {"max_calls":None,"max_tokens":None,"deadline_at":None}
        assert independent.utc(row.deadline_at)<datetime.now(timezone.utc)
        assert independent.snapshot(await s.get(m.ExecutionBudgetModel,source))==before
        assert (await s.get(m.ExecutionAttemptModel,"attempt-hook-v1")).status=="unknown"
        assert (await s.get(m.ExecutionAttemptModel,"attempt-hook-v1")).actual_tokens is None
        assert (await s.get(m.ExecutionBudgetModel,scope)).calls_used==0
    request,role=request_role();request=request.model_copy(update={"prompt_template":"packaging"})
    from bestseller.services.budget import bind_execution_scope
    with bind_execution_scope(scope):
        async with case.factory() as s:
            result=await continuation.adopt_cached(s,request,"packaging-hash")
            assert result["content"]=="retained packaging"
            assert result["metadata"]["execution_receipt"]["source_scope"]==source
    with pytest.raises(ValueError):await continuation.consume(case.engine,x.task_id,case.payload)


@pytest.mark.asyncio
async def test_objective_bypasses_only_aggregate_limits_not_route_or_unknown(case):
    x,source,prepare=await failed_leaf(case);grant=await prepare()
    scope=await continuation.consume(case.engine,x.task_id,case.payload)
    ledger=ExecutionLedger(case.engine,scope,BudgetSettings())
    request,role=request_role();request=request.model_copy(update={"user_prompt":"Versioned corrected hook input, not the isolated request."});payload=llm._effective_input_payload(request,role)
    proof=await independent.quote(ledger,payload,role.api_base)
    async with case.factory() as s:
        row=await s.get(m.ExecutionBudgetModel,scope)
        row.calls_used=row.max_calls+1;row.tokens_charged=row.max_tokens+1;await s.commit()
    claim=await ledger.claim("fixed-v2",proof["input_hash"])
    attempt=await ledger.reserve(claim,role.model,proof["input_hash"],1006500,endpoint=role.api_base,independent_acceptance_proof=proof)
    assert attempt
    for h,endpoint in [("hook-v1-hash",role.api_base),("other-hash","https://not-authorized.invalid/v1")]:
        claim=await ledger.claim("blocked-"+h,h)
        with pytest.raises(ExecutionBudgetError):
            await ledger.reserve(claim,role.model,h,1006500,endpoint=endpoint,
                independent_acceptance_proof={**proof,"input_hash":h})


@pytest.mark.asyncio
async def test_live_reserved_source_prevents_new_handoff(case):
    x,source,prepare=await failed_leaf(case)
    async with case.factory() as s:
        a=await s.get(m.ExecutionAttemptModel,"attempt-hook-v1");a.status="reserved";await s.commit()
    with pytest.raises(ValueError,match="in-flight"):
        await prepare()
    async with case.factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.ExecutionBudgetModel))==3
