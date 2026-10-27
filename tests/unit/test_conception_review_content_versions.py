"""Content-addressed pre-project review recovery; isolated DB, no provider."""
import json
from copy import deepcopy
from uuid import UUID
import pytest
from sqlalchemy import select,func
from bestseller.infra.db import models as m
from bestseller.services import conception_continuation as recovery, independent_acceptance as independent, llm
from bestseller.services.budget import ExecutionLedger,ExecutionBudgetError,bind_execution_scope
from bestseller.settings import BudgetSettings
from test_independent_acceptance import case,request_role
from test_conception_continuation import setup

pytestmark=pytest.mark.unit
STAGES=[
 ('logline_gate',('logline','premise','synopsis','canon','intent')),
 ('hook_pull_judge_v2',('logline','genre')),
 ('candidate_effect_contract',('premise','canon','intent')),
 ('conception_preproject_materialized_seed_fidelity_judge',('title','synopsis','canon','intent')),
 ('conception_final_packaging_seed_fidelity_judge',('title','synopsis','canon','intent')),
 ('conception_final_packaging_seed_fidelity_arbitration',('title','synopsis','canon','intent')),
]
BASE={'title':'original title','synopsis':'original full synopsis','logline':'same logline',
      'premise':'same approved premise','canon':{'all_facts':['retained ending','same night meeting']},
      'intent':'same original creator intent','genre':'generic realistic'}

def request_for(base,template,deps):
    request,role=request_role()
    request=request.model_copy(update={'logical_role':'critic','prompt_template':template,
        'system_prompt':'Normal independent review: preserve every quality requirement.',
        'user_prompt':json.dumps({k:base[k] for k in deps},ensure_ascii=False,sort_keys=True)})
    return request,role

async def prepare_chain(case,completion=True):
    x=await setup(case)
    async with case.factory() as s:
        for n,(template,deps) in enumerate(STAGES):
            req,role=request_for(BASE,template,deps)
            op=await s.get(m.ExecutionOperationModel,'source'+str(n));op.input_hash=llm._effective_input_hash(req,role)
            run=await s.get(m.LlmRunModel,UUID(op.result_json['llm_run_id']));run.prompt_template=template
            op.result_json={**op.result_json,'content':'old actual reviewed input '+template,
                'metadata':{'paid_call_origin':{'prompt_template':template,'logical_role':'critic'}}}
        await s.commit()
    await x.prepare();scope=await recovery.consume(case.engine,x.task_id,case.payload)
    if completion:
        async with case.factory() as s:
            row=await s.get(m.ExecutionBudgetModel,scope);meta=deepcopy(row.metadata_json)
            ref='offline current user instruction: complete the same book'
            meta[independent.KEY]['request']['completion_authorization_ref']=ref
            meta[independent.KEY]['request_sha256']=independent.digest(meta[independent.KEY]['request'])
            meta['provider_authorization']={'mode':'completion_bound','status':'active','authorization_ref':ref,
                'models':[independent.MODEL],'endpoint_hosts':['api.minimaxi.com'],'objective':'same book complete and verified export'}
            row.metadata_json=meta;await s.commit()
    return x,scope

@pytest.mark.asyncio
@pytest.mark.parametrize('changed_fields',[('synopsis',),('title',),('title','synopsis')])
async def test_entire_preproject_chain_revalidates_affected_inputs_and_reuses_unchanged(case,changed_fields):
    x,scope=await prepare_chain(case)
    revised=deepcopy(BASE)
    for key in changed_fields:revised[key]='complete corrected '+key
    ledger=ExecutionLedger(case.engine,scope,BudgetSettings())
    expected=0
    with bind_execution_scope(scope):
        for template,deps in STAGES:
            request,role=request_for(revised,template,deps);payload=llm._effective_input_payload(request,role);h=llm._effective_input_hash(request,role)
            async with case.factory() as s:
                old=await recovery.adopt_cached(s,request,h,payload)
            affected=bool(set(changed_fields)&set(deps))
            if not affected:
                assert old['content']=='old actual reviewed input '+template
                continue
            expected+=1
            assert old is None # old grade cannot approve a changed subject
            proof=await independent.quote(ledger,payload,role.api_base)
            claim=await ledger.claim(template+':'+h,h)
            paid=await ledger.reserve(claim,role.model,h,1006500,endpoint=role.api_base,independent_acceptance_proof=proof)
            fresh={'content':'new independently evaluated '+template,'input_tokens':10,'output_tokens':20}
            await ledger.settle(paid,'completed',actual_tokens=30);await ledger.finish(claim,fresh)
            # Another process resumes the SAME revised chain: identical new
            # input reuses its own result, never generates another review.
            async with case.factory() as s:
                assert await recovery.adopt_cached(s,request,h,payload) is None
            second=await ExecutionLedger(case.engine,scope,BudgetSettings()).claim(template+':'+h,h)
            assert second.cached_result==fresh
        async with case.factory() as s:
            assert (await s.get(m.ExecutionBudgetModel,scope)).calls_used==expected
            journals=list(await s.scalars(select(m.ExecutionOperationModel).where(m.ExecutionOperationModel.status=='review_revision')))
            assert len(journals)==expected and all(r.result_json['grade_reused'] is False for r in journals)
            assert all(r.result_json['decision']=='fresh_current_input_review_required' for r in journals)
            assert (await s.get(m.ExecutionOperationModel,'source27')).status=='unknown'
            assert (await s.get(m.ExecutionAttemptModel,'new27')).actual_tokens is None

@pytest.mark.asyncio
async def test_frozen_authorization_still_refuses_changed_input(case):
    x,scope=await prepare_chain(case,completion=False)
    req,role=request_for({**BASE,'synopsis':'changed'},*STAGES[0])
    with bind_execution_scope(scope):
        async with case.factory() as s:
            with pytest.raises(ExecutionBudgetError,match='preflight_changed'):
                await recovery.adopt_cached(s,req,llm._effective_input_hash(req,role),llm._effective_input_payload(req,role))

@pytest.mark.asyncio
async def test_revision_never_allows_forged_hash_unknown_or_paused_scope(case):
    x,scope=await prepare_chain(case)
    req,role=request_for({**BASE,'synopsis':'changed'},*STAGES[0]);payload=llm._effective_input_payload(req,role);h=llm._effective_input_hash(req,role)
    with bind_execution_scope(scope):
        async with case.factory() as s:
            with pytest.raises(ExecutionBudgetError,match='preflight_changed'):
                await recovery.adopt_cached(s,req,'forged hash',payload)
            with pytest.raises(ExecutionBudgetError,match='unknown_replay_forbidden'):
                await recovery.adopt_cached(s,req,x.blocked,payload)
            row=await s.get(m.ExecutionBudgetModel,scope);meta=deepcopy(row.metadata_json)
            meta['provider_authorization']['status']='paused';row.metadata_json=meta;await s.commit()
        async with case.factory() as s:
            with pytest.raises(ExecutionBudgetError,match='authorization_paused'):
                await recovery.adopt_cached(s,req,h,payload)



def test_resumed_backend_and_actual_page_helpers_clear_old_stop_without_erasing_history(monkeypatch,tmp_path):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from bestseller.web import server
    from test_ui_execution_terminal_retry import run as page_helpers
    scope='conception-continuation:offline-story:active'
    prior={'execution_terminal':{'terminal':True,'code':'execution_result_unknown'}}
    current={'execution_terminal':{'terminal':True,'code':'execution_continuation_preflight_changed'}}
    task=server.WebTaskState(task_id='offline-story',task_type='autowrite',status='failed',
        current_stage='execution_continuation_preflight_changed',error='execution_continuation_preflight_changed',
        result=deepcopy(current),created_at='2026-10-21T00:00:00+00:00',updated_at='2026-10-21T01:00:00+00:00',
        payload={'_run_conception':True,'_active_continuation_scope':scope,
                 '_prior_terminal_receipt':{'result':deepcopy(prior)}})
    manager=server.WebTaskManager(persist_path=tmp_path/'test-content-version-task.json')
    manager._tasks[task.task_id]=task
    assert page_helpers(task.to_dict())['state']['phase']=='blocked'
    @asynccontextmanager
    async def session_scope(settings):
        yield SimpleNamespace(bind='isolated fake engine')
    async def admit(engine,tid,payload):
        assert tid==task.task_id and payload['_active_continuation_scope']==scope
        return scope
    starts=[]
    monkeypatch.setattr(server,'session_scope',session_scope)
    monkeypatch.setattr(recovery,'resume_allowed',admit)
    monkeypatch.setattr(server.threading,'Thread',lambda **kwargs:SimpleNamespace(start=lambda:starts.append(kwargs)))
    queued=manager.resume_reviewed_conception(task.task_id)
    assert len(starts)==1 and queued['status']=='queued' and queued['result'] is None and queued['error'] is None
    assert queued['current_stage']=='reviewed_conception_continuation'
    assert queued['payload']['_prior_terminal_receipt']['result']==prior
    assert queued['payload']['_continuation_control_failures'][-1]['result']==current
    assert page_helpers(queued)['code'] is None and page_helpers(queued)['state']['phase']!='blocked'
    running={**queued,'status':'running','current_stage':'conception_review_revalidation_required'}
    running_ui=page_helpers(running)
    assert running_ui['code'] is None and running_ui['state']['phase']=='planning'
    assert '当前阶段不是正文写作' in running_ui['state']['detail']


@pytest.mark.asyncio
async def test_ambiguous_identical_old_input_cannot_be_misclassified_as_new_content(case):
    x,scope=await prepare_chain(case)
    req,role=request_for(BASE,*STAGES[0]);req=req.model_copy(update={'operation_id':'different-history-namespace'})
    async with case.factory() as s:
        row=await s.get(m.ExecutionBudgetModel,scope);meta=deepcopy(row.metadata_json)
        sources=meta[independent.KEY]['request']['cached_results']
        sources.append(deepcopy(next(v for v in sources if v['template']==req.prompt_template)))
        meta[independent.KEY]['request_sha256']=independent.digest(meta[independent.KEY]['request'])
        row.metadata_json=meta;await s.commit()
    with bind_execution_scope(scope):
        async with case.factory() as s:
            with pytest.raises(ExecutionBudgetError,match='preflight_changed'):
                await recovery.adopt_cached(s,req,llm._effective_input_hash(req,role),llm._effective_input_payload(req,role))
            assert (await s.get(m.ExecutionBudgetModel,scope)).calls_used==0
