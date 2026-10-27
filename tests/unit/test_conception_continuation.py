import json
from datetime import datetime,timedelta,timezone
from uuid import uuid4
from types import SimpleNamespace
import pytest
from sqlalchemy import MetaData,JSON,ForeignKeyConstraint,select,func
from sqlalchemy.dialects.postgresql import JSONB
from bestseller.infra.db import models as m
from bestseller.services import conception_continuation as recovery,independent_acceptance as independent,llm
from bestseller.services.budget import ExecutionLedger,ExecutionBudgetError,bind_execution_scope
from bestseller.settings import BudgetSettings
from test_independent_acceptance import case,request_role

pytestmark=pytest.mark.unit

async def setup(case):
    parent=await case.prepare();parent_scope=parent['request']['scope']
    task_id=parent_scope.split(':',1)[1]
    await independent.consume_ui(case.engine,case.payload)
    meta=MetaData();table=m.LlmRunModel.__table__.to_metadata(meta)
    for c in table.columns:
        if isinstance(c.type,JSONB):c.type=JSON();c.server_default=None
    for constraint in list(table.constraints):
        if isinstance(constraint,ForeignKeyConstraint):table.constraints.remove(constraint)
    async with case.engine.begin() as c:await c.run_sync(table.create)
    request,role=request_role();blocked=llm._effective_input_hash(request,role)
    past=datetime.now(timezone.utc)-timedelta(hours=1)
    async with case.factory() as s:
        row=await s.get(m.ExecutionBudgetModel,parent_scope)
        row.status='unknown';row.deadline_at=past;row.calls_used=28;row.tokens_charged=1032768+27*30
        for n in range(28):
            bad=n==27;key='source'+str(n);hash_='success'+str(n) if not bad else blocked
            run_id=uuid4();template='stage'+str(n)
            result={'content':'preserved source','provider':'openai','model_name':independent.MODEL,
                'llm_run_id':str(run_id),'input_tokens':10,'output_tokens':20,'finish_reason':'stop',
                'metadata':{'paid_call_origin':{'prompt_template':template,'logical_role':'critic'}}}
            s.add(m.ExecutionOperationModel(key=key,scope_id=parent_scope,input_hash=hash_,status='unknown' if bad else 'completed',
                owner='fixture',lease_until=past,result_json=None if bad else result))
            s.add(m.ExecutionAttemptModel(id='new'+str(n),operation_key=key,scope_id=parent_scope,input_hash=hash_,model_name=independent.MODEL,
                reserved_tokens=1032768 if bad else 1000,actual_tokens=None if bad else 30,status='unknown' if bad else 'completed',created_at=past))
            if not bad:
                s.add(m.ExecutionOperationModel(key='known-return'+str(n),scope_id=parent_scope,input_hash=hash_,status='transport_result',
                    owner='fixture',lease_until=past,result_json={'attempt_id':'new'+str(n),'result':{'input_tokens':10,'output_tokens':20}}))
                s.add(m.LlmRunModel(id=run_id,logical_role='critic',provider='openai',model_name=independent.MODEL,prompt_template=template,
                    input_tokens=10,output_tokens=20,metadata_json={}))
        await s.commit()
    async def prepare():
        return await recovery.prepare(case.engine,task_id=task_id,payload=case.payload,source_scope=parent_scope,
            authorization_id='explicit-same-story-fixture',authorization_ref='offline direct continuation authorization',actor='fixture')
    return SimpleNamespace(task_id=task_id,parent_scope=parent_scope,prepare=prepare,blocked=blocked)

@pytest.mark.asyncio
async def test_finite_linked_window_and_unknowns_unchanged(case):
    x=await setup(case)
    async with case.factory() as s:before=[independent.snapshot(await s.get(m.ExecutionBudgetModel,k)) for k in (case.old_scope,x.parent_scope)]
    grant=await x.prepare();assert await x.prepare()==grant
    req=grant['request'];scope=req['scope']
    assert req['new_calls']==100 and req['old_proof']['calls']==84
    assert req['combined_cost_micro_cny_upper']<75_000_000
    assert len(req['cached_results'])==27
    assert await independent.consume_ui(case.engine,case.payload) is None
    with pytest.raises(ValueError):await recovery.consume(case.engine,x.task_id,{**case.payload,'concept_seed':'changed'})
    assert await recovery.consume(case.engine,x.task_id,case.payload)==scope
    with pytest.raises(ValueError):await recovery.consume(case.engine,x.task_id,case.payload)
    async with case.factory() as s:
        row=await s.get(m.ExecutionBudgetModel,scope)
        assert independent.utc(row.deadline_at)==datetime.fromisoformat(independent.policy(row)['submitted_at'])+timedelta(hours=2)
        assert [independent.snapshot(await s.get(m.ExecutionBudgetModel,k)) for k in (case.old_scope,x.parent_scope)]==before
        assert (await s.get(m.ExecutionOperationModel,'source27')).status=='unknown'

@pytest.mark.asyncio
async def test_success_adoption_is_exact_read_only_origin_and_no_send(case):
    x=await setup(case);grant=await x.prepare();scope=await recovery.consume(case.engine,x.task_id,case.payload)
    request,_=request_role();request=request.model_copy(update={'prompt_template':'stage0'})
    with bind_execution_scope(scope):
        async with case.factory() as s:
            result=await recovery.adopt_cached(s,request,'success0')
            assert result['content']=='preserved source'
            assert result['metadata']['execution_receipt']['scope_id']==scope
            assert result['metadata']['execution_receipt']['source_operation_key']=='source0'
            assert (await s.get(m.ExecutionBudgetModel,scope)).calls_used==0
            assert await s.scalar(select(func.count()).select_from(m.ExecutionAttemptModel).where(m.ExecutionAttemptModel.scope_id==scope))==0
            with pytest.raises(ExecutionBudgetError,match='preflight_changed'):
                await recovery.adopt_cached(s,request,'changed-success-input')
            with pytest.raises(ExecutionBudgetError,match='unknown_replay_forbidden'):
                await recovery.adopt_cached(s,request,x.blocked)

@pytest.mark.asyncio
async def test_versioned_finalization_and_original_wire_hash_forbidden(case):
    x=await setup(case);grant=await x.prepare();scope=await recovery.consume(case.engine,x.task_id,case.payload)
    request,role=request_role()
    with bind_execution_scope(scope):
        async with case.factory() as s:
            revised=await recovery.version_request(s,request.model_copy(update={'prompt_template':'conception_finalize'}))
            assert revised.prompt_template=='conception_finalize_continuation_v2'
            assert revised.user_prompt!=request.user_prompt
            assert revised.metadata['supersedes_unknown_operation']=='source27'
    ledger=ExecutionLedger(case.engine,scope,BudgetSettings())
    proof=await independent.quote(ledger,llm._effective_input_payload(request,role),role.api_base)
    claim=await ledger.claim('forbidden-original-input',proof['input_hash'])
    with pytest.raises(ExecutionBudgetError,match='unknown_replay_forbidden'):
        await ledger.reserve(claim,role.model,proof['input_hash'],1006500,endpoint=role.api_base,independent_acceptance_proof=proof)
    async with case.factory() as s:
        assert (await s.get(m.ExecutionBudgetModel,scope)).calls_used==0


@pytest.mark.asyncio
async def test_original_cache_without_run_id_uses_existing_paid_audit(case):
    x=await setup(case)
    async with case.factory() as session:
        op=await session.get(m.ExecutionOperationModel,'source0')
        run_id=op.result_json['llm_run_id']
        from uuid import UUID
        run=await session.get(m.LlmRunModel,UUID(run_id))
        run.metadata_json={'execution_receipt':{'operation_key':op.key}}
        op.result_json={**op.result_json,'llm_run_id':None}
        await session.commit()
    grant=await x.prepare();scope=await recovery.consume(case.engine,x.task_id,case.payload)
    request,_=request_role();request=request.model_copy(update={'prompt_template':'stage0'})
    with bind_execution_scope(scope):
        async with case.factory() as session:
            result=await recovery.adopt_cached(session,request,'success0')
            assert result['llm_run_id']==run_id
            assert (await session.get(m.ExecutionOperationModel,'source0')).result_json['llm_run_id'] is None
            assert (await session.get(m.ExecutionBudgetModel,scope)).calls_used==0


@pytest.mark.asyncio
async def test_control_resume_never_renews_or_unfreezes(case):
    x=await setup(case);grant=await x.prepare();scope=await recovery.consume(case.engine,x.task_id,case.payload)
    payload={**case.payload,'_active_continuation_scope':scope}
    async with case.factory() as session:
        before=(await session.get(m.ExecutionBudgetModel,scope)).deadline_at
    assert await recovery.resume_allowed(case.engine,x.task_id,payload)==scope
    async with case.factory() as session:
        row=await session.get(m.ExecutionBudgetModel,scope)
        assert row.deadline_at==before
        row.status='unknown';await session.commit()
    with pytest.raises(ExecutionBudgetError,match='execution_result_unknown'):
        await recovery.resume_allowed(case.engine,x.task_id,payload)


def test_json_order_is_the_only_allowed_prompt_difference():
    assert recovery.serialization_equivalent('Facts {"a":1,"b":[2,3]} end','Facts {"b":[2,3],"a":1} end')
    assert not recovery.serialization_equivalent('Facts {"a":1} end','Changed {"a":1} end')
    assert not recovery.serialization_equivalent('{"a":1}','{"a":1.0}')
    assert not recovery.serialization_equivalent('{"b":[2,3]}','{"b":[3,2]}')


@pytest.mark.asyncio
async def test_retained_original_bytes_restored_only_after_semantic_proof(case,tmp_path):
    x=await setup(case);request,role=request_role()
    request=request.model_copy(update={'prompt_template':'stage0','system_prompt':'synthetic system','user_prompt':'Facts {"a":1,"b":[2,3]} end'})
    original_hash=llm._effective_input_hash(request,role)
    retained=tmp_path/'source-request.json';retained.write_text(json.dumps({'messages':{'source':'system_user',
        'system':{'text':request.system_prompt,'truncated':False},'user':{'text':request.user_prompt,'truncated':False}}}))
    async with case.factory() as session:
        op=await session.get(m.ExecutionOperationModel,'source0');op.input_hash=original_hash
        from uuid import UUID
        run=await session.get(m.LlmRunModel,UUID(op.result_json['llm_run_id']));run.request_payload_ref='source-request.json'
        await session.commit()
    grant=await x.prepare();scope=await recovery.consume(case.engine,x.task_id,case.payload)
    from bestseller.settings import load_settings
    settings=load_settings(env={});settings.artifact_store.local_dir=tmp_path
    changed=request.model_copy(update={'user_prompt':'Facts {"b":[2,3],"a":1} end'})
    assert llm._effective_input_hash(changed,role)!=original_hash
    with bind_execution_scope(scope):
        async with case.factory() as session:
            restored=await recovery.restore_retained_serialization(session,settings,changed,role)
            assert restored.user_prompt==request.user_prompt
            assert llm._effective_input_hash(restored,role)==original_hash
            bad=changed.model_copy(update={'user_prompt':'Facts {"a":99,"b":[2,3]} end'})
            assert (await recovery.restore_retained_serialization(session,settings,bad,role)).user_prompt==bad.user_prompt


@pytest.mark.asyncio
async def test_current_window_finalizer_reuse_twice_no_send_no_window_reset(case, tmp_path):
    x=await setup(case);await x.prepare();scope=await recovery.consume(case.engine,x.task_id,case.payload)
    request,role=request_role()
    request=request.model_copy(update={'prompt_template':'conception_finalize_continuation_v2',
        'system_prompt':'frozen system','user_prompt':'Facts {"a":1,"b":[2,3]}'})
    hash_=llm._effective_input_hash(request,role);run_id=uuid4();key='current-finalizer'
    retained={'messages':{'source':'system_user','system':{'text':request.system_prompt},'user':{'text':request.user_prompt}}}
    (tmp_path/'current.json').write_text(json.dumps(retained))
    async with case.factory() as session:
        before=independent.snapshot(await session.get(m.ExecutionBudgetModel,scope))
        value={'content':'genuine finalizer result','provider':'openai','model_name':independent.MODEL,
            'llm_run_id':None,'input_tokens':10,'output_tokens':20,'finish_reason':'stop',
            'metadata':{'paid_call_origin':{'prompt_template':request.prompt_template,'logical_role':request.logical_role}}}
        session.add(m.ExecutionOperationModel(key=key,scope_id=scope,input_hash=hash_,status='completed',
            owner='fixture',lease_until=datetime.now(timezone.utc)+timedelta(hours=1),result_json=value))
        session.add(m.LlmRunModel(id=run_id,logical_role=request.logical_role,provider='openai',model_name=independent.MODEL,
            prompt_template=request.prompt_template,input_tokens=10,output_tokens=20,request_payload_ref='current.json',
            metadata_json={'execution_receipt':{'operation_key':key}}))
        await session.commit()
    from bestseller.settings import load_settings
    settings=load_settings(env={});settings.artifact_store.local_dir=tmp_path
    changed=request.model_copy(update={'user_prompt':'Facts {"b":[2,3],"a":1}'})
    with bind_execution_scope(scope):
        async with case.factory() as session:
            restored=await recovery.restore_retained_serialization(session,settings,changed,role)
            assert restored.user_prompt==request.user_prompt
            for _ in range(2):
                result=await recovery.adopt_cached(session,restored,hash_)
                assert result['content']=='genuine finalizer result'
                assert result['llm_run_id']==str(run_id)
                assert result['metadata']['execution_receipt']['source_scope']==scope
            assert independent.snapshot(await session.get(m.ExecutionBudgetModel,scope))==before
            assert await session.scalar(select(func.count()).select_from(m.ExecutionAttemptModel).where(m.ExecutionAttemptModel.scope_id==scope))==0
            with pytest.raises(ExecutionBudgetError,match='preflight_changed'):
                await recovery.adopt_cached(session,request,'changed-content')
    assert await recovery.resume_allowed(case.engine,x.task_id,{**case.payload,'_active_continuation_scope':scope})==scope
    async with case.factory() as session:
        row=await session.get(m.ExecutionBudgetModel,scope)
        row.deadline_at=datetime.now(timezone.utc)-timedelta(seconds=1);await session.commit()
    with pytest.raises(ExecutionBudgetError):
        await recovery.resume_allowed(case.engine,x.task_id,{**case.payload,'_active_continuation_scope':scope})


def test_only_known_presend_control_failures_allow_same_window_resume():
    assert recovery.resumable_control_error('model_request_capacity_exceeded:{}')
    assert recovery.resumable_control_error('Traceback\nbestseller.services.llm_capacity.ModelRequestCapacityError: model_request_capacity_exceeded:{}')
    for value in ('execution_result_unknown','TimeoutError','cancelled','random error',None):
        assert not recovery.resumable_control_error(value)


@pytest.mark.asyncio
async def test_layout_resume_needs_real_completed_audit_and_passes_same_gate(case):
    from test_story_architecture_layout import misplaced
    x=await setup(case);await x.prepare();scope=await recovery.consume(case.engine,x.task_id,case.payload)
    payload={**case.payload,'genre':'玄幻','sub_genre':'异世大陆','target_chapters':60}
    with pytest.raises(ExecutionBudgetError,match='success_source_missing'):
        await recovery.assert_reconciliation_recovery_source(case.engine,scope,payload)
    key='actual-reconciliation';run_id=uuid4();raw={'story_architecture':misplaced()}
    value={'content':json.dumps(raw,ensure_ascii=False),'model_name':independent.MODEL,'input_tokens':10,'output_tokens':20,
        'metadata':{'paid_call_origin':{'prompt_template':'conception_concept_seed_final_premise_reconciliation','logical_role':'planner'}}}
    async with case.factory() as s:
        before=independent.snapshot(await s.get(m.ExecutionBudgetModel,scope))
        s.add(m.ExecutionOperationModel(key=key,scope_id=scope,input_hash='real-input',status='completed',owner='fixture',
            lease_until=datetime.now(timezone.utc)+timedelta(hours=1),result_json=value))
        s.add(m.ExecutionAttemptModel(id='known-complete',operation_key=key,scope_id=scope,input_hash='real-input',model_name=independent.MODEL,
            reserved_tokens=1000,actual_tokens=30,status='completed',created_at=datetime.now(timezone.utc)))
        s.add(m.LlmRunModel(id=run_id,logical_role='planner',provider='openai',model_name=independent.MODEL,
            prompt_template='conception_concept_seed_final_premise_reconciliation',input_tokens=10,output_tokens=20,
            metadata_json={'execution_receipt':{'operation_key':key}}))
        await s.commit()
    for _ in range(2):
        proof=await recovery.assert_reconciliation_recovery_source(case.engine,scope,payload)
        assert proof['source_operation']==key and proof['placement_normalization']['moved']
    async with case.factory() as s:
        assert independent.snapshot(await s.get(m.ExecutionBudgetModel,scope))==before
        op=await s.get(m.ExecutionOperationModel,key)
        changed=json.loads(op.result_json['content'])
        for unit in changed['story_architecture']['grammar']['causal_units']:unit.pop('cost_mode')
        op.result_json={**op.result_json,'content':json.dumps(changed)};await s.commit()
    with pytest.raises(ExecutionBudgetError,match='success_source_missing'):
        await recovery.assert_reconciliation_recovery_source(case.engine,scope,payload)


@pytest.mark.asyncio
async def test_changed_repaired_review_is_new_subject_but_exact_and_unknown_guards_remain(case):
    from bestseller.services.conception import _packaging_review_template
    x=await setup(case)
    async with case.factory() as session:
        run=await session.get(m.ExecutionOperationModel,'source0')
        from uuid import UUID
        model=await session.get(m.LlmRunModel,UUID(run.result_json['llm_run_id']))
        model.prompt_template='conception_final_packaging_seed_fidelity_judge'
        run.result_json={**run.result_json,'metadata':{'paid_call_origin':{'prompt_template':model.prompt_template,'logical_role':'critic'}}}
        await session.commit()
    await x.prepare();scope=await recovery.consume(case.engine,x.task_id,case.payload)
    request,_=request_role()
    initial=request.model_copy(update={'prompt_template':'conception_final_packaging_seed_fidelity_judge'})
    revised=initial.model_copy(update={'prompt_template':_packaging_review_template(initial.prompt_template,
        title='new corrected title',synopsis='new full synopsis',ctx={'_packaging_candidate_revision_v2':True})})
    with bind_execution_scope(scope):
        async with case.factory() as session:
            assert await recovery.adopt_cached(session,revised,'fresh-candidate-hash') is None
            with pytest.raises(ExecutionBudgetError,match='preflight_changed'):
                await recovery.adopt_cached(session,initial,'fresh-candidate-hash')
            with pytest.raises(ExecutionBudgetError,match='unknown_replay_forbidden'):
                await recovery.adopt_cached(session,revised,x.blocked)
            assert (await session.get(m.ExecutionBudgetModel,scope)).calls_used==0


@pytest.mark.asyncio
async def test_completed_scope_proposal_reuse_never_adopts_unknown_or_a_grade(case):
    from bestseller.services.conception_packaging_recovery import collect_completed_scope_packaging_proposals
    x=await setup(case);await x.prepare();scope=await recovery.consume(case.engine,x.task_id,case.payload)
    run_id=uuid4();key='completed-repair-proposal';now=datetime.now(timezone.utc)
    content=json.dumps({'title_candidates':['corrected complete title'],'synopsis_candidates':['complete corrected synopsis']})
    template='conception_final_packaging_seed_fidelity_repair_v2'
    async with case.factory() as session:
        session.add(m.ExecutionOperationModel(key=key,scope_id=scope,input_hash='candidate-input',status='unknown',
            owner='fixture',lease_until=now,result_json={'content':content,'model_name':independent.MODEL,
                'input_tokens':10,'output_tokens':20,'finish_reason':'stop',
                'metadata':{'paid_call_origin':{'prompt_template':template,'logical_role':'editor'}}}))
        session.add(m.ExecutionAttemptModel(id='candidate-attempt',operation_key=key,scope_id=scope,input_hash='candidate-input',
            model_name=independent.MODEL,reserved_tokens=1032768,actual_tokens=None,status='unknown',created_at=now))
        session.add(m.LlmRunModel(id=run_id,logical_role='editor',provider='openai',model_name=independent.MODEL,prompt_template=template,
            input_tokens=10,output_tokens=20,metadata_json={'execution_receipt':{'operation_key':key}}))
        await session.commit()
    with bind_execution_scope(scope):
        async with case.factory() as session:
            assert await collect_completed_scope_packaging_proposals(session)==[]
        async with case.factory() as session:
            op=await session.get(m.ExecutionOperationModel,key);op.status='completed'
            attempt=await session.get(m.ExecutionAttemptModel,'candidate-attempt');attempt.status='completed';attempt.actual_tokens=30
            await session.commit()
        async with case.factory() as session:
            pool=await collect_completed_scope_packaging_proposals(session)
            assert len(pool)==1 and pool[0]['title_candidates']==['corrected complete title']
            assert pool[0]['source_operation']==key and pool[0]['grade_reused'] is False
            assert pool[0]['requires_full_current_audit'] is True
            assert (await session.get(m.ExecutionBudgetModel,scope)).calls_used==0
            attempt=await session.get(m.ExecutionAttemptModel,'candidate-attempt');attempt.actual_tokens=None
            await session.commit()
        async with case.factory() as session:
            with pytest.raises(ExecutionBudgetError,match='success_audit_missing'):
                await collect_completed_scope_packaging_proposals(session)
