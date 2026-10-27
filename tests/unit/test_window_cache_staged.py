"""Offline physical payloads + real SQLite + public gateway; no provider IO."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib,json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import MetaData,JSON,ForeignKeyConstraint,select,func,event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from bestseller.infra.db import models as m
from bestseller.services import llm,conception_continuation as recovery,independent_acceptance as independent
from bestseller.services.budget import bind_execution_scope,ExecutionBudgetError,execution_operation_id
from bestseller.services.reviews import chapter_review_body_fingerprint
from bestseller.settings import load_settings
from test_conception_continuation import setup
from test_independent_acceptance import case as case

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]
PARAMETERS=('model_tier','model_catalog_key','temperature_override','max_tokens_override','cache_system')

@pytest.fixture
async def window(case,tmp_path,monkeypatch,request):
    source_options=getattr(request,'param',{})
    chain=await setup(case);await chain.prepare()
    scope=await recovery.consume(case.engine,chain.task_id,case.payload)
    meta=MetaData()
    for model in (m.ProjectModel,m.WorkflowRunModel,m.ChapterModel,m.ChapterDraftVersionModel):
        table=model.__table__.to_metadata(meta)
        for col in table.columns:
            if isinstance(col.type,JSONB):col.type=JSON();col.server_default=None
        for constraint in list(table.constraints):
            if isinstance(constraint,ForeignKeyConstraint):table.constraints.remove(constraint)
        table.foreign_keys.clear()
        for index in table.indexes:
            if index.dialect_options['postgresql'].get('where') is not None:
                index.dialect_options['sqlite']['where']=index.dialect_options['postgresql']['where']
    async with case.engine.begin() as c:await c.run_sync(meta.create_all)
    monkeypatch.setenv('BESTSELLER__ARTIFACT_STORE__LOCAL_DIR',json.dumps(str(tmp_path)))
    settings=load_settings();settings.llm.mock=False;settings.llm.retry.rate_limit_fallback_enabled=False
    role=settings.llm.critic.model_copy(update={'model':independent.MODEL,'model_override':None,
        'api_base':'https://api.minimaxi.com/v1','temperature':0.25,'max_tokens':4096,
        'stream':False,'thinking_type':None,'reasoning_effort':None,'n_candidates':1})
    settings.llm.critic=role
    project_id,workflow_id=uuid4(),uuid4()
    request=llm.LLMCompletionRequest(logical_role='critic',model_tier='strong',
        system_prompt='Evaluate the complete stable window.',user_prompt='Three complete synthetic chapters.',
        fallback_response='unused',max_tokens_override=4096,project_id=project_id,workflow_run_id=workflow_id,
        prompt_template='chapter_window_quality_judge',prompt_version='v1',
        metadata={'judge_scope':'window','window_size':3})
    effective=llm._effective_input_payload(request,role);hash_=llm._effective_input_hash(request,role)
    keys=[];run_ids=[];cycle_ids=[];namespaces=[];draft_ids=[];now=datetime.now(timezone.utc)
    chapter_id=uuid4();cycles={}
    async with case.factory() as s:
        # Explicit IDs and original workflow ownership; no project provider override.
        s.add(m.ProjectModel(id=project_id,slug='window-fixture-'+uuid4().hex,title='Synthetic window',
            genre='other',target_word_count=8000,target_chapters=4,metadata_json={}))
        s.add(m.WorkflowRunModel(id=workflow_id,project_id=project_id,workflow_type='review',status='running'))
        row=await s.get(m.ExecutionBudgetModel,scope);metadata=deepcopy(row.metadata_json)
        ref='explicit offline completion authorization'
        metadata[independent.KEY]['request']['completion_authorization_ref']=ref
        metadata[independent.KEY]['request_sha256']=independent.digest(metadata[independent.KEY]['request'])
        metadata['provider_authorization']={'mode':'completion_bound','status':'active','authorization_ref':ref,
            'models':[independent.MODEL],'endpoint_hosts':['api.minimaxi.com'],'objective':'same book complete'}
        row.metadata_json=metadata;row.calls_used=2;row.tokens_charged=60
        for index in range(2):
            origin_workflow=uuid4();run_id=uuid4();attempt_id=uuid4().hex
            # Native original chapter cycles, with the newer result scoring higher.
            body=f'synthetic chapter body {index}';fingerprint=chapter_review_body_fingerprint(body)
            body_hash=fingerprint['digest'];draft_id=uuid4();draft_ids.append(draft_id)
            original_policy=hashlib.sha256(b'original historical policy').hexdigest()
            revision=original_policy if source_options.get('recovery_cycle') else 'initial'
            cycle_id=hashlib.sha256(
                f'chapter-critic-cycle.v1\n{project_id}\n{chapter_id}\n{body_hash}\n{revision}'.encode()).hexdigest()
            if index==0:cycle_id=source_options.get('cycle_id',cycle_id)
            namespace=f'chapter-critic:{cycle_id}:1'
            cycles[cycle_id]={'schema_version':'chapter-critic-cycle.v1','cycle_id':cycle_id,
                'source_draft_id':str(draft_id),'source_body_hash':body_hash,
                'policy_source_hash':original_policy,
                'review_workflow_run_id':str(origin_workflow),'state':'completed',
                'attempts':[{'attempt':1,'state':'completed','operation_namespace':namespace,
                    'draft_id':str(draft_id),'body_fingerprint':fingerprint}]}
            s.add(m.ChapterDraftVersionModel(id=draft_id,project_id=project_id,chapter_id=chapter_id,
                version_no=index+1,content_md=body,is_current=False))
            cycle_ids.append(cycle_id);namespaces.append(namespace)
            # These are actual paid roots, not contradictory metadata aliases.
            producer_namespace=source_options.get('namespace',namespace) if index==0 else namespace
            slot=source_options.get('slot',0) if index==0 else 0
            operation_id=execution_operation_id(producer_namespace,
                subject='critic:chapter_window_quality_judge:v1',revision=hash_)+f':sample:{slot}'
            key=hashlib.sha256(f'{scope}\n{operation_id}'.encode()).hexdigest()
            keys.append(key);run_ids.append(run_id)
            s.add(m.WorkflowRunModel(id=origin_workflow,project_id=project_id,workflow_type='chapter_pipeline',
                scope_type='chapter',scope_id=chapter_id,status='completed',
                metadata_json={'critic_cycle_id':cycle_id}))
            original=request.model_copy(update={'workflow_run_id':origin_workflow})
            receipt={'schema':'effective-input.v1','hash':hash_,'model':role.model}
            execution={'policy':'execution-budget.v1','scope_id':scope,'operation_key':key,
                'attempts':[{'attempt_id':attempt_id,'status':'completed'}]}
            metadata_paid={'paid_call_origin':llm._paid_call_origin(original,hash_),
                'effective_input_receipt':receipt}
            value=llm.LLMCompletionResult(content=json.dumps({'score':0.4+index*0.5}),provider='openai',
                model_name=role.model,input_tokens=10,output_tokens=20,finish_reason='stop',
                metadata=metadata_paid).model_dump(mode='json')
            # Native transport persists the core receipt before complete_text
            # enriches the completed operation and LlmRun. Keep both witnesses.
            transport_value=deepcopy(value)
            if source_options.get('native_receipt'):
                value['metadata']['effective_input_receipt']={**receipt,
                    'messages_source':'system_user','tools_included':False,
                    'legacy_prompt_hash':('a'*64 if source_options.get('declared_legacy_hash') else
                        llm._hash_prompt(original.system_prompt,original.user_prompt))}
            if source_options.get('enriched_transport'):
                transport_value=deepcopy(value)
            s.add(m.ExecutionOperationModel(key=key,scope_id=scope,input_hash=hash_,status='completed',
                owner='original-paid',lease_until=now,result_json=value))
            s.add(m.ExecutionAttemptModel(id=attempt_id,operation_key=key,scope_id=scope,input_hash=hash_,
                model_name=role.model,reserved_tokens=1004096,actual_tokens=30,status='completed',created_at=now))
            transport=hashlib.sha256(f'transport-result.v1\n{scope}\n{attempt_id}'.encode()).hexdigest()
            s.add(m.ExecutionOperationModel(key=transport,scope_id=scope,input_hash=hash_,status='transport_result',
                owner='original-paid',lease_until=now,result_json={'operation_key':key,'attempt_id':attempt_id,
                    'input_hash':hash_,'result':transport_value}))
            request_ref=f'{run_id}.request.json';response_ref=f'{run_id}.response.json'
            run_metadata={**deepcopy(value['metadata']),'execution_receipt':execution,'judge_scope':'window','window_size':3}
            if source_options.get('source_alias') and index==0:run_metadata['sample_slot']=slot
            s.add(m.LlmRunModel(id=run_id,project_id=project_id,workflow_run_id=origin_workflow,
                logical_role='critic',provider='openai',model_name=role.model,prompt_template=request.prompt_template,
                prompt_version='v1',prompt_hash=hash_,input_tokens=10,output_tokens=20,finish_reason='stop',
                request_payload_ref=request_ref,response_payload_ref=response_ref,metadata_json=run_metadata,
                created_at=now+timedelta(seconds=index)))
            identity={'llm_run_id':str(run_id),'project_id':str(project_id),'workflow_run_id':str(origin_workflow),
                'logical_role':'critic','prompt_template':request.prompt_template,'prompt_version':'v1'}
            (tmp_path/request_ref).write_text(json.dumps({'schema_version':'llm-payload.v1','kind':'request',
                'replayable':True,'replayability_reasons':[],**identity,
                'requested':{k:getattr(request,k) for k in PARAMETERS},
                'effective':{'model':role.model,'provider':'openai','temperature':role.temperature,
                    'max_tokens':4096,'stream':role.stream,'thinking_type':None,'reasoning_effort':'low',
                    'timeout_seconds':role.timeout_seconds},
                'messages':{'source':'system_user','replayable':True,
                    'system':llm._bounded_text(request.system_prompt),'user':llm._bounded_text(request.user_prompt)}}))
            (tmp_path/response_ref).write_text(json.dumps({'schema_version':'llm-payload.v1','kind':'response',
                'replayable':True,'replayability_reasons':[],**identity,'content':llm._bounded_text(value['content']),
                'finish_reason':'stop','fallback_used':False,'model':role.model,'provider':'openai',
                'input_tokens':10,'output_tokens':20}))
        s.add(m.ChapterModel(id=chapter_id,project_id=project_id,chapter_number=3,
            chapter_goal='Synthetic original window endpoint',metadata_json={'chapter_critic_cycles':cycles}))
        await s.commit()
    calls=[]
    async def deny(*args,**kwargs):
        calls.append('gateway');raise AssertionError('No provider dispatch authorized in this test')
    async def deny_network(*args,**kwargs):
        raise AssertionError('Network forbidden')
    monkeypatch.setattr(llm,'_call_litellm_with_retry',deny)
    monkeypatch.setattr(httpx.AsyncClient,'send',deny_network)
    return SimpleNamespace(case=case,scope=scope,request=request,settings=settings,role=role,effective=effective,
        hash=hash_,keys=keys,run_ids=run_ids,cycle_ids=cycle_ids,namespaces=namespaces,chapter_id=chapter_id,draft_ids=draft_ids,
        calls=calls,root=tmp_path,monkeypatch=monkeypatch,source_options=source_options)

async def public(w,request=None,namespace='resumed-native-window'):
    with bind_execution_scope(w.scope),llm.bind_execution_operation(namespace):
        async with w.case.factory() as s:
            return await llm.complete_text(s,w.settings,request or w.request)

async def snapshot(w):
    async with w.case.factory() as s:
        budget=independent.snapshot(await s.get(m.ExecutionBudgetModel,w.scope))
        roots=[(key,deepcopy((await s.get(m.ExecutionOperationModel,key)).result_json)) for key in w.keys]
        attempts=[{a.key:getattr(row,a.key) for a in row.__mapper__.column_attrs}
            for row in await s.scalars(select(m.ExecutionAttemptModel).where(m.ExecutionAttemptModel.scope_id==w.scope))]
        return budget,roots,attempts


async def receipt_witnesses(w):
    """Freeze historical physical/ORM witnesses, excluding new adoption receipts."""
    rows=[];files=[]
    async with w.case.factory() as s:
        for run_id in w.run_ids:
            run=await s.get(m.LlmRunModel,run_id)
            attempt_id=run.metadata_json['execution_receipt']['attempts'][0]['attempt_id']
            key=hashlib.sha256(f'transport-result.v1\n{w.scope}\n{attempt_id}'.encode()).hexdigest()
            transport=await s.get(m.ExecutionOperationModel,key)
            rows.extend(None if row is None else deepcopy({a.key:getattr(row,a.key) for a in row.__mapper__.column_attrs})
                for row in (run,transport))
            files.extend((ref,(w.root/ref).read_bytes() if ref and (w.root/ref).is_file() else None)
                for ref in (run.request_payload_ref,run.response_payload_ref))
    return await snapshot(w),rows,files


@pytest.mark.parametrize('window',[{}, {'native_receipt':True},
    {'native_receipt':True,'enriched_transport':True}],indirect=True,
    ids=['core-core','native-completed-transport-core','native-completed-transport-full'])
async def test_exact_receipt_lifecycle_reuses_paid_window_without_rewriting_witnesses(window):
    w=window
    async with w.case.factory() as s:
        for run_id,key in zip(w.run_ids,w.keys):
            run=await s.get(m.LlmRunModel,run_id);op=await s.get(m.ExecutionOperationModel,key)
            request=json.loads((w.root/run.request_payload_ref).read_text())
            core={'schema':'effective-input.v1','hash':op.input_hash,'model':run.model_name}
            native={**core,'messages_source':'system_user','tools_included':False,
                'legacy_prompt_hash':llm._hash_prompt(
                    request['messages']['system']['text'],request['messages']['user']['text'])}
            receipt=run.metadata_json['effective_input_receipt']
            assert receipt==(native if w.source_options.get('native_receipt') else core)
            assert op.result_json['metadata']['effective_input_receipt']==receipt
            attempt_id=run.metadata_json['execution_receipt']['attempts'][0]['attempt_id']
            transport=await s.get(m.ExecutionOperationModel,
                hashlib.sha256(f'transport-result.v1\n{w.scope}\n{attempt_id}'.encode()).hexdigest())
            assert transport.result_json['result']['metadata']['effective_input_receipt']==(
                native if w.source_options.get('enriched_transport') else core)
    before=await receipt_witnesses(w)
    first=await public(w);second=await public(w,namespace='another-native-lifecycle-resume')
    assert first.llm_run_id==second.llm_run_id==w.run_ids[0]
    assert json.loads(first.content)['score']==0.4 and first.content==second.content
    assert len(first.metadata['execution_receipt']['duplicate_witnesses'])==2
    assert await receipt_witnesses(w)==before and not w.calls


@pytest.mark.parametrize('window',[{'native_receipt':True}],indirect=True)
@pytest.mark.parametrize('target',['run','root','paid','all'])
@pytest.mark.parametrize('changed_input',[False,True],ids=['strict-reuse','financial-history'])
@pytest.mark.parametrize('mutation',[
    *['missing_'+key for key in ('schema','hash','model','messages_source','tools_included','legacy_prompt_hash')],
    *['wrong_'+key for key in ('schema','hash','model','messages_source','tools_included','legacy_prompt_hash')],
    'tools_zero','tools_none','extra','partial_messages','partial_tools','partial_legacy'])
async def test_malformed_receipt_lifecycle_fails_closed(window,target,mutation,changed_input):
    w=window
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[0]);op=await s.get(m.ExecutionOperationModel,w.keys[0])
        attempt_id=run.metadata_json['execution_receipt']['attempts'][0]['attempt_id']
        transport=await s.get(m.ExecutionOperationModel,
            hashlib.sha256(f'transport-result.v1\n{w.scope}\n{attempt_id}'.encode()).hexdigest())
        receipt=deepcopy(run.metadata_json['effective_input_receipt'])
        if mutation.startswith('missing_'):receipt.pop(mutation.removeprefix('missing_'))
        elif mutation.startswith('wrong_'):
            key=mutation.removeprefix('wrong_')
            receipt[key]=True if key=='tools_included' else 'unverified'
        elif mutation=='tools_zero':receipt['tools_included']=0
        elif mutation=='tools_none':receipt['tools_included']=None
        elif mutation=='extra':receipt['unrecognized_extra']='unverified'
        elif mutation.startswith('partial_'):
            key={'partial_messages':'messages_source','partial_tools':'tools_included',
                'partial_legacy':'legacy_prompt_hash'}[mutation]
            receipt={k:v for k,v in receipt.items() if k in ('schema','hash','model',key)}
        if target in ('run','all'):
            metadata=deepcopy(run.metadata_json);metadata['effective_input_receipt']=deepcopy(receipt)
            run.metadata_json=metadata;flag_modified(run,'metadata_json')
        if target in ('root','all'):
            value=deepcopy(op.result_json);value['metadata']['effective_input_receipt']=deepcopy(receipt)
            op.result_json=value;flag_modified(op,'result_json')
        if target in ('paid','all'):
            value=deepcopy(transport.result_json)
            value['result']['metadata']['effective_input_receipt']=deepcopy(receipt)
            transport.result_json=value;flag_modified(transport,'result_json')
        await s.commit()
    before=await receipt_witnesses(w)
    with pytest.raises(ExecutionBudgetError,match='execution_success_audit_missing'):
        await public(w,w.request.model_copy(update={'user_prompt':'New authorized window.'}) if changed_input else None)
    assert await receipt_witnesses(w)==before and not w.calls
    async with w.case.factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.ExecutionOperationModel).where(
            m.ExecutionOperationModel.status.in_(['recovered_success_reference','review_revision'])))==0


@pytest.mark.parametrize('window',[{'native_receipt':True}],indirect=True)
@pytest.mark.parametrize('target',['run','root'])
@pytest.mark.parametrize('changed_input',[False,True])
async def test_recognized_run_and_root_shapes_must_still_match(window,target,changed_input):
    w=window
    async with w.case.factory() as s:
        if target=='run':
            row=await s.get(m.LlmRunModel,w.run_ids[0]);value=deepcopy(row.metadata_json)
        else:
            row=await s.get(m.ExecutionOperationModel,w.keys[0]);value=deepcopy(row.result_json)
        metadata=value if target=='run' else value['metadata']
        metadata['effective_input_receipt']={k:v for k,v in metadata['effective_input_receipt'].items()
            if k in ('schema','hash','model')}
        if target=='run':row.metadata_json=value
        else:row.result_json=value
        await s.commit()
    before=await receipt_witnesses(w)
    with pytest.raises(ExecutionBudgetError,match='execution_success_audit_missing'):
        await public(w,w.request.model_copy(update={'user_prompt':'New authorized window.'}) if changed_input else None)
    assert await receipt_witnesses(w)==before and not w.calls


async def test_duplicate_earliest_paid_result_public_gateway_no_new_credit(window):
    w=window;before=await snapshot(w)
    first=await public(w);second=await public(w,namespace='yet-another-resume')
    assert json.loads(first.content)['score']==0.4 and first.content==second.content
    assert first.llm_run_id==w.run_ids[0] and second.llm_run_id==w.run_ids[0]
    receipt=first.metadata['execution_receipt']
    assert receipt['source_operation_key']==w.keys[0]
    assert [x['operation_key'] for x in receipt['duplicate_witnesses']]==w.keys
    assert len(receipt['duplicate_witnesses'])==2
    assert await snapshot(w)==before and not w.calls
    async with w.case.factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.ExecutionOperationModel).where(
            m.ExecutionOperationModel.status=='recovered_success_reference'))==1

@pytest.mark.parametrize('window',[{'slot':1},{'namespace':'unknown-producer'},
    {'namespace':'random-'+uuid4().hex},
    {'namespace':'chapter-critic:'+'e'*64+':1'},{'cycle_id':'d'*64}],indirect=True,
    ids=['paid-slot1-no-alias','unknown-namespace','random-namespace','unrecorded-cycle','noncanonical-cycle'])
async def test_original_paid_identity_must_prove_native_slot_zero(window):
    w=window;before=await snapshot(w)
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[0])
        payload=json.loads((w.root/run.request_payload_ref).read_text())
        # Historical retention omitted identity/slot. Absence proves no slot.
        assert not {'operation_id','sample_slot','sample_index','candidate_index'} & payload['requested'].keys()
        assert not {'sample_slot','sample_index','candidate_index'} & run.metadata_json.keys()
    with pytest.raises(ExecutionBudgetError,match='execution_success_audit_missing'):
        await public(w)
    assert await snapshot(w)==before and not w.calls
    async with w.case.factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.ExecutionOperationModel).where(
            m.ExecutionOperationModel.status=='recovered_success_reference'))==0


async def test_native_original_slot_zero_is_proved_without_retained_slot_metadata(window):
    w=window
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[0])
        payload=json.loads((w.root/run.request_payload_ref).read_text())
        assert 'operation_id' not in payload['requested'] and 'sample_slot' not in payload['requested']
    original_id=execution_operation_id(w.namespaces[0],
        subject='critic:chapter_window_quality_judge:v1',revision=w.hash)+':sample:0'
    assert w.keys[0]==hashlib.sha256(f'{w.scope}\n{original_id}'.encode()).hexdigest()
    before=await snapshot(w);result=await public(w)
    assert result.llm_run_id==w.run_ids[0] and not w.calls and await snapshot(w)==before


@pytest.mark.parametrize('mutation',['cycle_missing','cycle_id','cycle_owner','cycle_schema',
    'attempt_missing','attempt_index','attempt_bool','attempt_namespace','workflow_kind','workflow_scope',
    'chapter_project','draft_missing','draft_body','draft_owner','source_body','attempt_draft','attempt_body'])
async def test_original_cycle_provenance_fail_closed(window,mutation):
    w=window
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[0])
        workflow=await s.get(m.WorkflowRunModel,run.workflow_run_id)
        chapter=await s.get(m.ChapterModel,w.chapter_id)
        draft=await s.get(m.ChapterDraftVersionModel,w.draft_ids[0])
        metadata=deepcopy(chapter.metadata_json);cycle=metadata['chapter_critic_cycles'][w.cycle_ids[0]]
        if mutation=='cycle_missing':metadata['chapter_critic_cycles'].pop(w.cycle_ids[0])
        elif mutation=='cycle_id':cycle['cycle_id']='f'*64
        elif mutation=='cycle_owner':cycle['review_workflow_run_id']=str(uuid4())
        elif mutation=='cycle_schema':cycle['schema_version']='unknown'
        elif mutation=='attempt_missing':cycle['attempts']=[]
        elif mutation=='attempt_index':cycle['attempts'][0]['attempt']=2
        elif mutation=='attempt_bool':cycle['attempts'][0]['attempt']=True
        elif mutation=='attempt_namespace':cycle['attempts'][0]['operation_namespace']='unknown-producer'
        elif mutation=='workflow_kind':workflow.workflow_type='review'
        elif mutation=='workflow_scope':workflow.scope_id=uuid4()
        elif mutation=='chapter_project':chapter.project_id=uuid4()
        elif mutation=='draft_missing':cycle['source_draft_id']=str(uuid4())
        elif mutation=='draft_body':draft.content_md+=' changed'
        elif mutation=='draft_owner':draft.chapter_id=uuid4()
        elif mutation=='source_body':cycle['source_body_hash']='f'*64
        elif mutation=='attempt_draft':cycle['attempts'][0]['draft_id']=str(uuid4())
        elif mutation=='attempt_body':cycle['attempts'][0]['body_fingerprint']['digest']='f'*64
        chapter.metadata_json=metadata;flag_modified(chapter,'metadata_json');await s.commit()
    with pytest.raises(ExecutionBudgetError):await public(w)
    assert not w.calls


@pytest.mark.parametrize('window',[{}, {'recovery_cycle':True}],indirect=True,ids=['initial','recovery'])
@pytest.mark.parametrize('history',['candidate','rebind'])
async def test_original_provenance_survives_current_editor_alias_and_policy_history(window,history):
    w=window
    async with w.case.factory() as s:
        for run_id in w.run_ids:
            run=await s.get(m.LlmRunModel,run_id);workflow=await s.get(m.WorkflowRunModel,run.workflow_run_id)
            workflow.metadata_json={'critic_cycle_id':'f'*64,'policy_compatibility_history':['new policy']}
        chapter=await s.get(m.ChapterModel,w.chapter_id);metadata=deepcopy(chapter.metadata_json)
        metadata['chapter_review_active_cycle']={'cycle_id':'f'*64}
        for cycle in metadata['chapter_critic_cycles'].values():
            if history=='candidate':
                cycle['candidate_selection_reconciliations']=[{
                    'schema':'candidate-selection-review-compatibility.v1','cycle_id':cycle['cycle_id'],
                    'source_draft_id':cycle['source_draft_id'],'source_body_hash':cycle['source_body_hash'],
                    'previous_policy_hash':cycle['policy_source_hash'],'current_policy_hash':'f'*64}]
            else:
                cycle['policy_rebind_history']=[{'proof':{'before_records':{'critic':deepcopy(cycle)},
                    'previous_policy_source_hash':cycle['policy_source_hash'],'current_policy_source_hash':'f'*64}}]
            cycle['policy_source_hash']='f'*64
        chapter.metadata_json=metadata;await s.commit()
    result=await public(w)
    assert result.llm_run_id==w.run_ids[0] and not w.calls


@pytest.mark.parametrize('window',[{}, {'native_receipt':True}],indirect=True,ids=['core','native'])
@pytest.mark.parametrize('mutation',['sample','sample_alias','project','model','version','role','endpoint',
    'unknown','paused','old_policy','workflow_owner','run_project','origin','run_finish','fallback',
    'attempt_unknown','tokens','transport','request_missing','response_missing','request_truncated',
    'response_changed','retention','input_receipt','missing_origin','result_model','current_workflow_owner'])
async def test_fail_closed_before_gateway(window,mutation):
    w=window;r=w.request
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[0]);op=await s.get(m.ExecutionOperationModel,w.keys[0])
        budget=await s.get(m.ExecutionBudgetModel,w.scope)
        if mutation=='sample':r=r.model_copy(update={'sample_slot':1})
        elif mutation=='sample_alias':r=r.model_copy(update={'metadata':{**r.metadata,'sample_index':1}})
        elif mutation=='project':r=r.model_copy(update={'project_id':uuid4()})
        elif mutation=='model':w.settings.llm.critic.model='openai/other-model'
        elif mutation=='version':r=r.model_copy(update={'prompt_version':'v0'})
        elif mutation=='role':r=r.model_copy(update={'logical_role':'writer'})
        elif mutation=='endpoint':w.settings.llm.critic.api_base='https://other.invalid/v1'
        elif mutation=='unknown':op.status='unknown'
        elif mutation=='paused':
            meta=deepcopy(budget.metadata_json);meta['provider_authorization']['status']='paused';budget.metadata_json=meta
        elif mutation=='old_policy':
            meta=deepcopy(budget.metadata_json);meta.pop('provider_authorization');budget.metadata_json=meta
        elif mutation=='workflow_owner':
            workflow=await s.get(m.WorkflowRunModel,run.workflow_run_id);workflow.project_id=uuid4()
        elif mutation=='run_project':run.project_id=uuid4()
        elif mutation=='current_workflow_owner':
            workflow=await s.get(m.WorkflowRunModel,r.workflow_run_id);workflow.project_id=uuid4()
        elif mutation=='missing_origin':
            value=deepcopy(op.result_json);value['metadata'].pop('paid_call_origin');op.result_json=value
        elif mutation=='result_model':op.result_json={**op.result_json,'model_name':'other'}
        elif mutation=='origin':
            value=deepcopy(op.result_json);value['metadata']['paid_call_origin']['project_id']=str(uuid4());op.result_json=value
        elif mutation=='run_finish':run.finish_reason='length'
        elif mutation=='fallback':op.result_json={**op.result_json,'fallback_used':True}
        elif mutation in {'attempt_unknown','tokens'}:
            attempt=await s.get(m.ExecutionAttemptModel,run.metadata_json['execution_receipt']['attempts'][0]['attempt_id'])
            if mutation=='tokens':attempt.actual_tokens=0
            else:attempt.status='unknown'
        elif mutation=='transport':
            attempt=run.metadata_json['execution_receipt']['attempts'][0]['attempt_id']
            key=hashlib.sha256(f'transport-result.v1\n{w.scope}\n{attempt}'.encode()).hexdigest()
            await s.delete(await s.get(m.ExecutionOperationModel,key))
        elif mutation=='request_missing':(w.root/run.request_payload_ref).unlink()
        elif mutation=='response_missing':(w.root/run.response_payload_ref).unlink()
        elif mutation=='request_truncated':
            path=w.root/run.request_payload_ref;payload=json.loads(path.read_text());payload['messages']['user']['truncated']=True;path.write_text(json.dumps(payload))
        elif mutation=='response_changed':
            path=w.root/run.response_payload_ref;payload=json.loads(path.read_text());payload['content']['text']='changed';path.write_text(json.dumps(payload))
        elif mutation=='retention':
            r=r.model_copy(update={'metadata':{**r.metadata,'source_complete_retention':{'schema':'invalid'}}})
        elif mutation=='input_receipt':
            meta=deepcopy(run.metadata_json);meta['effective_input_receipt']['hash']='f'*64;run.metadata_json=meta
        await s.commit()
    with pytest.raises((ExecutionBudgetError,ValueError)):
        await public(w,r)
    assert not w.calls
    async with w.case.factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.ExecutionOperationModel).where(
            m.ExecutionOperationModel.status=='recovered_success_reference'))==0

@pytest.mark.parametrize('window',[{}, {'slot':1},{'namespace':'unknown-producer'},{'slot':1,'source_alias':True},
    {'native_receipt':True},{'native_receipt':True,'slot':1},
    {'native_receipt':True,'namespace':'unknown-producer'},
    {'native_receipt':True,'slot':1,'source_alias':True},
    {'native_receipt':True,'enriched_transport':True},
    {'native_receipt':True,'enriched_transport':True,'declared_legacy_hash':True}],indirect=True,
    ids=['native-zero','retained-paid-slot1','retained-unknown-namespace','retained-explicit-alias',
        'enriched-native-zero','enriched-paid-slot1','enriched-unknown-namespace','enriched-explicit-alias',
        'enriched-transport','declared-legacy-hash-only'])
@pytest.mark.parametrize('physical',['present','missing_refs','missing_files','changed_files'])
@pytest.mark.parametrize('input_change',['body','parameter'])
async def test_changed_window_passes_to_ordinary_gateway_with_authorization(window,physical,input_change):
    w=window
    async with w.case.factory() as s:
        for run_id in w.run_ids:
            run=await s.get(m.LlmRunModel,run_id)
            if physical=='missing_refs':run.request_payload_ref=run.response_payload_ref=None
            elif physical=='missing_files':
                (w.root/run.request_payload_ref).unlink();(w.root/run.response_payload_ref).unlink()
            elif physical=='changed_files':
                (w.root/run.request_payload_ref).write_text('not an original request')
                (w.root/run.response_payload_ref).write_text('not an original response')
        await s.commit()
    before=await receipt_witnesses(w)
    class Captured(BaseException):pass
    captured=[]
    async def capture(request,role,retry):
        call=llm._execution_call_var.get()
        assert call is not None and call.claim.cached_result is None
        assert call.ledger.scope_id==w.scope
        payload=llm._effective_input_payload(request,role)
        proof=await independent.quote(call.ledger,payload,role.api_base)
        attempt=await call.ledger.reserve(call.claim,role.model,proof['input_hash'],
            proof['input_tokens_upper']+proof['output_tokens_upper'],endpoint=role.api_base,
            independent_acceptance_proof=proof)
        await call.ledger.settle(attempt,'not_sent',actual_tokens=0)
        captured.append(request)
        raise Captured()
    w.monkeypatch.setattr(llm,'_call_litellm_with_retry',capture)
    r=w.request.model_copy(update=({'user_prompt':'A substantively changed complete window.'}
        if input_change=='body' else {'temperature_override':0.7}))
    assert llm._effective_input_hash(r,w.role)!=w.hash
    with pytest.raises(Captured):await public(w,r)
    assert len(captured)==1
    async with w.case.factory() as s:
        revision=await s.scalar(select(m.ExecutionOperationModel).where(m.ExecutionOperationModel.status=='review_revision'))
        assert revision.result_json['grade_reused'] is False
        assert revision.result_json['source_evidence_mode']=='financial_history_only'
        proofs=revision.result_json['source_proofs']
        assert len(proofs)==2
        assert {p['operation_key'] for p in proofs}==set(w.keys)
        assert all(p['proof_kind']=='financial_history_only' and p['grade_reusable'] is False
            and p['physical_payload_verified'] is False and 'value' not in p for p in proofs)
        for p in proofs:
            evidence=p['physical_payload_evidence']
            for kind in ('request','response'):
                if physical=='missing_refs':assert evidence[kind]=={'status':'not_retained'}
                else:
                    assert evidence[kind]=={'status':'reference_recorded_not_validated',
                        'ref':f"{p['source_llm_run_id']}.{kind}.json"}
        assert await s.scalar(select(func.count()).select_from(m.ExecutionOperationModel).where(
            m.ExecutionOperationModel.status=='recovered_success_reference'))==0
        budget=await s.get(m.ExecutionBudgetModel,w.scope)
        # Existing reserve semantics keep the unsent attempt in the call count.
        assert budget.calls_used==3 and budget.tokens_charged==60
    after=await receipt_witnesses(w)
    assert after[1:]==before[1:] and after[0][1]==before[0][1]
    old_attempts={row['id']:row for row in before[0][2]}
    assert {row['id']:row for row in after[0][2] if row['id'] in old_attempts}==old_attempts
    new_attempts=[row for row in after[0][2] if row['id'] not in old_attempts]
    assert len(new_attempts)==1 and new_attempts[0]['status']=='not_sent' and new_attempts[0]['actual_tokens']==0


@pytest.mark.parametrize('window',[{'slot':1}],indirect=True)
@pytest.mark.parametrize('mutation',['transport','paid_result','charge'])
async def test_new_window_with_nonstable_source_still_requires_financial_proof(window,mutation):
    w=window
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[0])
        if mutation=='transport':
            attempt=run.metadata_json['execution_receipt']['attempts'][0]['attempt_id']
            key=hashlib.sha256(f'transport-result.v1\n{w.scope}\n{attempt}'.encode()).hexdigest()
            await s.delete(await s.get(m.ExecutionOperationModel,key))
        elif mutation=='paid_result':
            attempt=run.metadata_json['execution_receipt']['attempts'][0]['attempt_id']
            key=hashlib.sha256(f'transport-result.v1\n{w.scope}\n{attempt}'.encode()).hexdigest()
            transport=await s.get(m.ExecutionOperationModel,key);value=deepcopy(transport.result_json)
            value['result']['content']='changed';transport.result_json=value
        else:
            budget=await s.get(m.ExecutionBudgetModel,w.scope);budget.tokens_charged=0
        await s.commit()
    revised=w.request.model_copy(update={'user_prompt':'New complete window, requiring a fresh review.'})
    with pytest.raises(ExecutionBudgetError):await public(w,revised)
    assert not w.calls


@pytest.mark.parametrize('index',[0,1],ids=['earliest','other-duplicate'])
@pytest.mark.parametrize('kind',['request','response'])
@pytest.mark.parametrize('loss',['not_retained','evidence_loss'])
async def test_same_hash_requires_every_original_physical_witness(window,index,kind,loss):
    w=window
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[index]);field=kind+'_payload_ref'
        if loss=='not_retained':setattr(run,field,None)
        else:(w.root/getattr(run,field)).unlink()
        await s.commit()
    before=await receipt_witnesses(w)
    with pytest.raises(ExecutionBudgetError,match='execution_success_audit_missing'):await public(w)
    assert await receipt_witnesses(w)==before and not w.calls
    async with w.case.factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.ExecutionOperationModel).where(
            m.ExecutionOperationModel.status.in_(['recovered_success_reference','review_revision'])))==0


@pytest.mark.parametrize('mutation',[
    'root_unknown','attempt_unknown','extra_unknown','reserved','actual','charge','calls',
    'transport_missing','transport_status','transport_scope','transport_input','transport_operation',
    'transport_attempt','transport_content','transport_raw','transport_tools','transport_fallback_source',
    'run_tokens','result_fallback','result_model','run_provider','run_finish',
    'workflow_owner','current_workflow_owner','run_project','paused','auth_missing','scope_unknown',
    'model','endpoint','grant_changed'])
async def test_changed_input_financial_tamper_and_authority_fail_closed_without_payloads(window,mutation):
    w=window
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[1]);op=await s.get(m.ExecutionOperationModel,w.keys[1])
        attempt=await s.get(m.ExecutionAttemptModel,run.metadata_json['execution_receipt']['attempts'][0]['attempt_id'])
        transport=await s.get(m.ExecutionOperationModel,
            hashlib.sha256(f'transport-result.v1\n{w.scope}\n{attempt.id}'.encode()).hexdigest())
        budget=await s.get(m.ExecutionBudgetModel,w.scope)
        for run_id in w.run_ids:
            historical=await s.get(m.LlmRunModel,run_id)
            historical.request_payload_ref=historical.response_payload_ref=None
        if mutation=='root_unknown':op.status='unknown'
        elif mutation=='attempt_unknown':attempt.status='unknown'
        elif mutation=='extra_unknown':
            s.add(m.ExecutionAttemptModel(id=uuid4().hex,operation_key=op.key,scope_id=w.scope,
                input_hash=w.hash,model_name=w.role.model,status='unknown',reserved_tokens=100,
                actual_tokens=None,created_at=datetime.now(timezone.utc)))
        elif mutation=='reserved':attempt.reserved_tokens=29
        elif mutation=='actual':attempt.actual_tokens=29
        elif mutation=='charge':budget.tokens_charged=29
        elif mutation=='calls':budget.calls_used=0
        elif mutation=='transport_missing':await s.delete(transport)
        elif mutation=='transport_status':transport.status='unknown'
        elif mutation=='transport_scope':transport.scope_id=w.case.old_scope
        elif mutation=='transport_input':transport.input_hash='f'*64
        elif mutation.startswith('transport_'):
            value=deepcopy(transport.result_json);field=mutation.removeprefix('transport_')
            if field in ('operation','attempt'):value[field+'_key' if field=='operation' else 'attempt_id']='wrong'
            else:
                field={'raw':'raw_message','tools':'tool_calls'}.get(field,field)
                value['result'][field]=[] if field=='tool_calls' else 'tampered'
            transport.result_json=value
        elif mutation=='run_tokens':run.input_tokens+=1
        elif mutation=='result_fallback':op.result_json={**op.result_json,'fallback_used':True}
        elif mutation=='result_model':op.result_json={**op.result_json,'model_name':'other'}
        elif mutation=='run_provider':run.provider='fallback'
        elif mutation=='run_finish':run.finish_reason='length'
        elif mutation=='workflow_owner':
            workflow=await s.get(m.WorkflowRunModel,run.workflow_run_id);workflow.project_id=uuid4()
        elif mutation=='current_workflow_owner':
            workflow=await s.get(m.WorkflowRunModel,w.request.workflow_run_id);workflow.project_id=uuid4()
        elif mutation=='run_project':run.project_id=uuid4()
        elif mutation in ('paused','auth_missing','grant_changed'):
            metadata=deepcopy(budget.metadata_json)
            if mutation=='paused':metadata['provider_authorization']['status']='paused'
            elif mutation=='auth_missing':metadata.pop('provider_authorization')
            else:metadata[independent.KEY]['request']['endpoint']='https://unapproved.invalid/v1'
            budget.metadata_json=metadata
        elif mutation=='scope_unknown':budget.status='unknown'
        elif mutation=='model':w.settings.llm.critic.model='openai/other-model'
        elif mutation=='endpoint':w.settings.llm.critic.api_base='https://unapproved.invalid/v1'
        else:raise AssertionError(mutation)
        await s.commit()
    before=await receipt_witnesses(w)
    revised=w.request.model_copy(update={'user_prompt':'New window needs fresh authorized judgment.'})
    with pytest.raises(ExecutionBudgetError):await public(w,revised)
    assert await receipt_witnesses(w)==before and not w.calls
    async with w.case.factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.ExecutionOperationModel).where(
            m.ExecutionOperationModel.status.in_(['recovered_success_reference','review_revision'])))==0


@pytest.mark.parametrize('target',['run','root','paid','all'])
@pytest.mark.parametrize('field',['schema','status','project_id','workflow_run_id','step_run_id',
    'logical_role','prompt_template','prompt_version','prompt_hash','extra'])
async def test_new_input_requires_exact_declared_origins_without_old_payloads(window,target,field):
    w=window
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[0]);op=await s.get(m.ExecutionOperationModel,w.keys[0])
        run.request_payload_ref=run.response_payload_ref=None
        attempt=run.metadata_json['execution_receipt']['attempts'][0]['attempt_id']
        transport=await s.get(m.ExecutionOperationModel,
            hashlib.sha256(f'transport-result.v1\n{w.scope}\n{attempt}'.encode()).hexdigest())
        for kind,row in (('run',run),('root',op),('paid',transport)):
            if target not in (kind,'all'):continue
            value=deepcopy(row.metadata_json if kind=='run' else row.result_json)
            metadata=value if kind=='run' else value['metadata'] if kind=='root' else value['result']['metadata']
            metadata['paid_call_origin'][field]='unverified'
            if kind=='run':row.metadata_json=value
            else:row.result_json=value
        await s.commit()
    before=await receipt_witnesses(w)
    with pytest.raises(ExecutionBudgetError):
        await public(w,w.request.model_copy(update={'user_prompt':'A new authorized input.'}))
    assert await receipt_witnesses(w)==before and not w.calls


@pytest.mark.parametrize('window',[{'native_receipt':True,'enriched_transport':True}],indirect=True)
@pytest.mark.parametrize('changed_input',[False,True])
async def test_native_legacy_hash_declared_in_transport_must_match_completed(window,changed_input):
    w=window
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[1])
        if changed_input:run.request_payload_ref=run.response_payload_ref=None
        attempt=run.metadata_json['execution_receipt']['attempts'][0]['attempt_id']
        transport=await s.get(m.ExecutionOperationModel,
            hashlib.sha256(f'transport-result.v1\n{w.scope}\n{attempt}'.encode()).hexdigest())
        value=deepcopy(transport.result_json)
        value['result']['metadata']['effective_input_receipt']['legacy_prompt_hash']='a'*64
        transport.result_json=value;await s.commit()
    before=await receipt_witnesses(w)
    with pytest.raises(ExecutionBudgetError):
        await public(w,w.request.model_copy(update={'user_prompt':'New body.'}) if changed_input else None)
    assert await receipt_witnesses(w)==before and not w.calls


@pytest.mark.parametrize('historical_hash',['not-a-hash','F'*64])
async def test_financial_history_cannot_accept_a_consistently_malformed_effective_hash(window,historical_hash):
    w=window
    def replaced(value):
        if isinstance(value,dict):return {key:replaced(item) for key,item in value.items()}
        if isinstance(value,list):return [replaced(item) for item in value]
        return historical_hash if value==w.hash else value
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[0]);op=await s.get(m.ExecutionOperationModel,w.keys[0])
        attempt=await s.get(m.ExecutionAttemptModel,run.metadata_json['execution_receipt']['attempts'][0]['attempt_id'])
        transport=await s.get(m.ExecutionOperationModel,
            hashlib.sha256(f'transport-result.v1\n{w.scope}\n{attempt.id}'.encode()).hexdigest())
        run.prompt_hash=historical_hash;run.metadata_json=replaced(run.metadata_json)
        run.request_payload_ref=run.response_payload_ref=None
        op.input_hash=attempt.input_hash=transport.input_hash=historical_hash
        op.result_json=replaced(op.result_json);transport.result_json=replaced(transport.result_json)
        await s.commit()
    before=await receipt_witnesses(w)
    with pytest.raises(ExecutionBudgetError):
        await public(w,w.request.model_copy(update={'user_prompt':'New body.'}))
    assert await receipt_witnesses(w)==before and not w.calls

async def test_reordered_json_input_is_not_restored_for_window(window):
    w=window
    # The generic semantic JSON restoration must not widen this exact-input class.
    async with w.case.factory() as s:
        changed=w.request.model_copy(update={'user_prompt':'different bytes'})
        restored=await recovery.restore_retained_serialization(s,w.settings,changed,w.role)
        assert restored.user_prompt==changed.user_prompt

@pytest.mark.parametrize('mutation',['run','chapter','draft'])
async def test_all_duplicate_sources_revalidated_after_discovery_without_inventory_under_locks(window,mutation):
    w=window;original=recovery._retained_sources;discovery=[];ended=[];locks=[];mutated=[]
    async def scan(read,*args,**kwargs):
        assert not locks
        discovery.append(read.sync_session.get_transaction())
        return await original(read,*args,**kwargs)
    def end(session,transaction):
        if session.get_bind() is w.case.engine.sync_engine:ended.append(transaction)
    def execute(state):
        if state.session.get_bind() is not w.case.engine.sync_engine:return
        if getattr(state.statement,'_for_update_arg',None) is not None:
            locks.append(True);assert discovery[-1] in ended
        if locks:
            sql=str(state.statement)
            if 'FROM execution_operations' in sql:assert 'execution_operations.key =' in sql
            if 'FROM llm_runs' in sql:assert 'llm_runs.id =' in sql
            if 'FROM chapters' in sql:assert 'chapters.id =' in sql
            if 'FROM chapter_draft_versions' in sql:assert 'chapter_draft_versions.id =' in sql
    class Boundary(AsyncSession):
        async def scalar(self,statement,*args,**kwargs):
            if discovery and not mutated and getattr(statement,'_for_update_arg',None) is not None:
                mutated.append(True)
                async with w.case.factory() as writer:
                    # A non-selected duplicate must still be frozen and checked.
                    if mutation=='run':
                        run=await writer.get(m.LlmRunModel,w.run_ids[1]);run.finish_reason='length'
                    elif mutation=='chapter':
                        chapter=await writer.get(m.ChapterModel,w.chapter_id);metadata=deepcopy(chapter.metadata_json)
                        metadata['chapter_critic_cycles'][w.cycle_ids[1]]['attempts'][0]['operation_namespace']='changed'
                        chapter.metadata_json=metadata
                    else:
                        draft=await writer.get(m.ChapterDraftVersionModel,w.draft_ids[1]);draft.content_md+=' changed'
                    await writer.commit()
            return await super().scalar(statement,*args,**kwargs)
    w.monkeypatch.setattr(recovery,'_retained_sources',scan)
    w.monkeypatch.setattr(recovery,'async_sessionmaker',lambda *a,**k:async_sessionmaker(*a,**k,class_=Boundary))
    event.listen(Session,'do_orm_execute',execute);event.listen(Session,'after_transaction_end',end)
    try:
        with pytest.raises(ExecutionBudgetError):await public(w)
        assert locks and mutated and not w.calls
    finally:
        event.remove(Session,'do_orm_execute',execute);event.remove(Session,'after_transaction_end',end)


async def test_three_commercial_samples_and_primary_writer_remain_ordinary(window):
    w=window;claims=[]
    class Captured(BaseException):pass
    async def capture(request,role,retry):
        call=llm._execution_call_var.get()
        assert call is not None and call.claim.cached_result is None
        claims.append(call.claim.key)
        raise Captured()
    w.monkeypatch.setattr(llm,'_call_litellm_with_retry',capture)
    for slot in range(3):
        request=w.request.model_copy(update={'prompt_template':'chapter_commercial_quality_judge',
            'sample_slot':slot,'metadata':{'judge_scope':'chapter'}})
        with pytest.raises(Captured):await public(w,request,namespace='independent-commercial')
    assert len(set(claims))==3
    for template,role in [('chapter_review','critic'),('chapter_write','writer')]:
        request=w.request.model_copy(update={'prompt_template':template,'logical_role':role})
        with pytest.raises(Captured):await public(w,request)
    assert len(set(claims))==5


async def test_hash_cannot_alias_a_changed_effective_payload(window):
    w=window
    request=w.request.model_copy(update={'sample_slot':0,'operation_id':'native'})
    effective=deepcopy(w.effective);effective['messages'][1]['content']='changed window'
    with bind_execution_scope(w.scope):
        async with w.case.factory() as s:
            with pytest.raises(ExecutionBudgetError):
                await recovery.adopt_cached(s,request,w.hash,effective)


async def test_earliest_tie_breaks_by_operation_key_not_score(window):
    w=window
    async with w.case.factory() as s:
        first=await s.get(m.LlmRunModel,w.run_ids[0]);second=await s.get(m.LlmRunModel,w.run_ids[1])
        second.created_at=first.created_at;await s.commit()
    result=await public(w)
    assert result.metadata['execution_receipt']['source_operation_key']==min(w.keys)


async def test_retained_request_guard_still_precedes_adoption(window):
    from bestseller.services.retained_request_guard import bind_retained_request,RetainedRequestRejected,retained_attempts_sha256
    w=window
    async with w.case.factory() as s:
        op=await s.get(m.ExecutionOperationModel,w.keys[0]);await s.get(m.LlmRunModel,w.run_ids[0])
        attempts=[{a.key:getattr(row,a.key) for a in row.__mapper__.column_attrs}
            for row in await s.scalars(select(m.ExecutionAttemptModel).where(m.ExecutionAttemptModel.operation_key==op.key))]
    binding={'scope_id':w.scope,'project_id':str(w.request.project_id),'workflow_run_id':str(w.request.workflow_run_id),
        'logical_role':'critic','prompt_template':w.request.prompt_template,'prompt_version':'v1','model':w.role.model,
        'input_hash':w.hash,'operation_key':w.keys[0],'result_sha256':independent.digest(op.result_json),
        'attempts_sha256':retained_attempts_sha256(attempts)}
    with bind_retained_request(binding):
        with pytest.raises(RetainedRequestRejected,match='operation_changed'):
            await public(w,namespace='a-different-operation')
    assert not w.calls


async def test_single_settled_window_is_reused_on_first_resumed_call(window):
    w=window
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[1]);op=await s.get(m.ExecutionOperationModel,w.keys[1])
        await s.delete(run);await s.delete(op);await s.commit()
    result=await public(w)
    assert result.llm_run_id==w.run_ids[0] and not w.calls
    assert len(result.metadata['execution_receipt']['duplicate_witnesses'])==1


async def test_semantically_equivalent_json_remains_a_new_window_input(window):
    w=window
    # The legacy restoration helper accepts serialization-only differences for
    # other roles. Window reuse must remain byte/effective-input exact.
    original=w.request.model_copy(update={'user_prompt':'Facts {"a":1,"b":2}'})
    revised=original.model_copy(update={'user_prompt':'Facts {"b":2,"a":1}'})
    async with w.case.factory() as s:
        run=await s.get(m.LlmRunModel,w.run_ids[0])
        path=w.root/run.request_payload_ref
        payload=json.loads(path.read_text());payload['messages']['user']=llm._bounded_text(original.user_prompt)
        path.write_text(json.dumps(payload))
        with bind_execution_scope(w.scope):
            restored=await recovery.restore_retained_serialization(s,w.settings,revised,w.role)
    assert restored.user_prompt==revised.user_prompt


@pytest.mark.parametrize('indexes',[(1,),(0,1)])
async def test_malformed_known_duplicates_are_never_filtered_into_a_new_draw(window,indexes):
    w=window
    async with w.case.factory() as s:
        for index in indexes:
            op=await s.get(m.ExecutionOperationModel,w.keys[index])
            value=deepcopy(op.result_json);value['metadata'].pop('paid_call_origin');op.result_json=value
        await s.commit()
    with pytest.raises(ExecutionBudgetError):await public(w)
    assert not w.calls


async def test_hidden_unknown_attempt_is_not_a_paid_success(window):
    w=window
    async with w.case.factory() as s:
        s.add(m.ExecutionAttemptModel(id=uuid4().hex,operation_key=w.keys[1],scope_id=w.scope,
            input_hash=w.hash,model_name=w.role.model,status='unknown',reserved_tokens=100,
            actual_tokens=None,created_at=datetime.now(timezone.utc)))
        await s.commit()
    with pytest.raises(ExecutionBudgetError):await public(w)
    assert not w.calls


async def test_nonselected_witness_cannot_hide_by_changing_only_its_root_hash(window):
    w=window
    async with w.case.factory() as s:
        op=await s.get(m.ExecutionOperationModel,w.keys[1]);op.input_hash='f'*64;await s.commit()
    with pytest.raises(ExecutionBudgetError):await public(w)
    assert not w.calls
