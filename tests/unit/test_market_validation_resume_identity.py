"""Advisory identity repair never clears a terminal or dispatches a provider."""
import json
from uuid import uuid4
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func
from test_independent_acceptance import case,request_role
from test_conception_continuation import setup
from bestseller.infra.db import models as m
from bestseller.services import llm,conception_continuation as recovery,independent_acceptance as independent
from bestseller.services.budget import bind_execution_scope,ExecutionBudgetError
from bestseller.services.market_validation.judge import judge_collisions
from bestseller.domain.market_validation import MarketBookObservation
pytestmark=pytest.mark.unit

@pytest.mark.asyncio
async def test_advisory_binds_stable_phase_and_propagates_execution_stops(monkeypatch):
    seen=[]
    async def complete(session,settings,request):
        seen.append((llm._semantic_operation_namespace.get(),request.user_prompt))
        return SimpleNamespace(content='{"collisions":[]}',fallback_used=False)
    monkeypatch.setattr(llm,'complete_text',complete)
    books=[MarketBookObservation(platform='fixture',title='真实候选',intro='现实家庭合成设定')]
    for _ in range(2):assert await judge_collisions(None,None,concept='相同故事',competitors=books)==([],True)
    assert seen[0]==seen[1] and seen[0][0]==('market-validation-collision.v1',0)
    assert llm._semantic_operation_namespace.get() is None
    async def blocked(*args):raise ExecutionBudgetError('execution_operation_identity_required','fixture')
    monkeypatch.setattr(llm,'complete_text',blocked)
    with pytest.raises(ExecutionBudgetError,match='identity_required'):
        await judge_collisions(None,None,concept='相同故事',competitors=books)
    assert llm._semantic_operation_namespace.get() is None
    assert recovery.resumable_control_error('execution_operation_identity_required') # explicit UI recovery only; no automatic dispatch

async def market_source(case,fault=None,legacy=False):
    x=await setup(case);await x.prepare();scope=await recovery.consume(case.engine,x.task_id,case.payload)
    req,role=request_role();req=req.model_copy(update={'logical_role':'critic','prompt_template':'market_validation_collision_judge','user_prompt':'New synthetic market concept and exact competitor evidence'})
    h=llm._effective_input_hash(req,role);rid=uuid4();now=datetime.now(timezone.utc)
    import hashlib
    semantic=hashlib.sha256(json.dumps({'input_hash':h,'role':'critic','template':req.prompt_template,'version':'v1'},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    opkey=hashlib.sha256((scope+'\n'+semantic+':slot:0').encode()).hexdigest() if legacy else 'market-paid'
    result={'content':'{"collisions":[{"title":"真实候选","similarity":"high"}]}','provider':'openai','model_name':independent.MODEL,
        'input_tokens':10,'output_tokens':20,'finish_reason':'stop','fallback_used':False,
        'metadata':{'paid_call_origin':{'prompt_template':req.prompt_template,'logical_role':'critic'}}}
    async with case.factory() as s:
        op=m.ExecutionOperationModel(key=opkey,scope_id=scope,input_hash=h,status='completed',owner='fixture',lease_until=now,result_json=result)
        a=m.ExecutionAttemptModel(id='market-attempt',scope_id=scope,operation_key=op.key,input_hash=h,model_name=independent.MODEL,
            status='completed',reserved_tokens=1000900,actual_tokens=30,created_at=now)
        run=m.LlmRunModel(id=rid,logical_role='critic',provider='openai',model_name=independent.MODEL,prompt_template=req.prompt_template,
            input_tokens=10,output_tokens=20,finish_reason='stop',metadata_json={'execution_receipt':{'operation_key':op.key}})
        if fault=='unsettled':a.actual_tokens=None
        if fault=='unknown':a.status='unknown';a.actual_tokens=None
        if fault=='wrong_hash':a.input_hash='other'
        if fault=='wrong_model':a.model_name='other'
        if fault=='wrong_role':run.logical_role='editor'
        if fault=='wrong_usage':run.output_tokens=21
        if fault=='length':op.result_json={**result,'finish_reason':'length'}
        if fault=='fallback':op.result_json={**result,'fallback_used':True}
        s.add_all([op,a,run]);await s.commit()
    return scope,req,h,rid,result

@pytest.mark.asyncio
async def test_exact_settled_market_result_reused_with_high_collision_preserved_and_no_send(case):
    scope,req,h,rid,result=await market_source(case)
    with bind_execution_scope(scope):
        async with case.factory() as s:
            before=independent.snapshot(await s.get(m.ExecutionBudgetModel,scope))
            reused=await recovery.adopt_cached(s,req,h)
            assert reused['content']==result['content'] and reused['llm_run_id']==str(rid)
            assert reused['metadata']['execution_receipt']['source_operation_key']=='market-paid'
            assert independent.snapshot(await s.get(m.ExecutionBudgetModel,scope))==before
            assert (await s.get(m.ExecutionOperationModel,'source27')).status=='unknown'
            assert (await s.get(m.ExecutionAttemptModel,'new27')).actual_tokens is None
            assert await s.scalar(select(func.count()).select_from(m.ExecutionAttemptModel).where(m.ExecutionAttemptModel.scope_id==scope))==1
            with pytest.raises(ExecutionBudgetError,match='preflight_changed'):
                await recovery.adopt_cached(s,req,'changed-prompt')

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['unsettled','unknown','wrong_hash','wrong_model','wrong_role','wrong_usage','length','fallback'])
async def test_incomplete_market_audit_cannot_be_reused(case,fault):
    scope,req,h,rid,result=await market_source(case,fault)
    with bind_execution_scope(scope):
        async with case.factory() as s:
            with pytest.raises(ExecutionBudgetError,match='success_audit_missing'):
                await recovery.adopt_cached(s,req,h)
            assert (await s.get(m.ExecutionBudgetModel,scope)).calls_used==0
