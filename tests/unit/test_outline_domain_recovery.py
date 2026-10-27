"""Real SQLite/native physical custody for a distinct typed correction purpose."""
from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from test_outline_revision_proposal_v2 import (  # noqa: F401 - pytest fixture dependencies
    proposal as source_fixture, record_completion as native_record,
    source_case, store,
)
from test_source_bound_outline_integrity import source as independent_outline

from bestseller.infra.db import models as db
from bestseller.services import llm, planner, outline_domain_recovery as service
from bestseller.services import outline_revision_proposal as legacy
from bestseller.services import outline_revision_proposal_v2 as native
from bestseller.services.budget import bind_execution_scope
from bestseller.services.planning_capacity_continuation import _accounting
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader

ACTUAL_DOMAIN_VALIDATOR = planner._validate_generated_volume_outline_or_raise
pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def record(c, request, *, content, calls=None):
    seeded=None
    if request.operation_id:
        # Exercise the actual independent reservation and atomic one-send grant.
        from bestseller.services.budget import ExecutionLedger
        role=service.budget.resolve_request_role(c.settings,c.project,request)
        digest=llm._effective_input_hash(request,role)
        await c.session.commit()
        ledger=ExecutionLedger(c.session.bind,c.scope,c.settings.budget)
        claim=await ledger.claim(request.operation_id,digest)
        aid=await ledger.reserve(claim,role.model,digest,30,endpoint=role.api_base,
            single_dispatch_id=request.metadata['single_dispatch_authorization_id'],
            workflow_id=str(request.workflow_run_id),template=request.prompt_template,version=request.prompt_version)
        seeded=db.LlmRunModel(id=uuid4(),logical_role=request.logical_role,
            provider=llm._provider_from_model(role.model),model_name=role.model,metadata_json={'execution_receipt':{
            'operation_key':claim.key,'attempts':[{'attempt_id':aid}]}})
        transport=db.ExecutionOperationModel(key=hashlib.sha256(f'transport-result.v1\n{c.scope}\n{aid}'.encode()).hexdigest(),
            scope_id=c.scope,input_hash=digest,status='transport_result',owner='offline-fixture',lease_until=c.budget.deadline_at)
        c.session.add_all([seeded,transport])
        await c.session.flush()
    with c.session.no_autoflush:
        completion, run = await native_record(c, request, content=content, calls=calls,run=seeded)
    if 'domain_transport_grant_sha256' in request.metadata:
        # Actual llm telemetry copies request metadata; paid caches deliberately
        # do not. Do not fabricate this field in the provider/transport result.
        run.metadata_json={**run.metadata_json,
            'domain_transport_grant_sha256':request.metadata['domain_transport_grant_sha256']}
    key = run.metadata_json['execution_receipt']['operation_key']
    aid = run.metadata_json['execution_receipt']['attempts'][0]['attempt_id']
    tkey = hashlib.sha256(f'transport-result.v1\n{c.scope}\n{aid}'.encode()).hexdigest()
    for operation_key in (key, tkey):
        row = await c.session.get(db.ExecutionOperationModel, operation_key)
        value = deepcopy(row.result_json)
        paid = value['result'] if operation_key == tkey else value
        paid['llm_run_id'] = None
        row.result_json = value
    await c.session.flush()
    return completion, run


def native_second(before, cast):
    """All values are explicit unrelated fixture text, never runtime defaults."""
    reference = independent_outline()
    manifest = planner._chapter_outline_identity_manifest(cast)
    names = [entry['name'] for entry in manifest]
    selected = list(dict.fromkeys(names))[:2]
    assert selected[0] == 'Mira'
    patches = []
    for chapter, sample in zip(before['chapters'], reference['chapters'], strict=True):
        number = chapter['chapter_number']
        for field in ('goal','opening_situation','main_conflict','hook_description','protagonist_flaw'):
            patches.append(dict(chapter_number=number,scope='chapter',
                field='chapter_goal' if field=='goal' else field,value=sample[field]))
        causal = {**sample['causal_contract'], 'chapter_function': 'Advance the independent observation.',
            'visible_action_or_reaction': sample['goal'],
            'gain_or_reveal': f'The chart at station {number} records a second tide interval.',
            'next_reader_desire': f'Will the separate station {number} notebook match the harbor signal?',
            'resistance': sample['main_conflict']}
        for field,value in causal.items():
            if field in planner._OUTLINE_PATCH_CAUSAL_FIELDS:
                patches.append(dict(chapter_number=number,scope='causal_contract',field=field,value=value))
        if number == 4:
            patches.append(dict(chapter_number=number,scope='causal_contract',field='final_state',
                value='Mira fixes the vane in the marked lighthouse cradle and closes the crossing file.'))
            patches.append(dict(chapter_number=number,scope='chapter',field='methodology_contract',
                value={'is_climax':False,'pacing_mode':'resolution','hooks_to_plant':[]}))
        scene = sample['scenes'][0]
        fields = {'scene_type':scene['scene_type'],'time_label':scene['time_label'],
            'participants':[selected[0],*[name+' (observer)' for name in selected[1:]]],
            'purpose':{**scene['purpose'],'emotion':'Unease gives way to a deliberate check.',
                'conflict_stakes':sample['main_conflict']},
            'entry_state':sample['opening_situation'],'exit_state':sample['hook_description'],
            'action_sequence':scene['action_sequence'],'signature_image':'A brass reticle catches the last light.',
            'cut_point':sample['hook_description']}
        for field,value in fields.items():
            patches.append(dict(chapter_number=number,scene_number=1,scope='scene',field=field,value=value))
    return json.dumps({'patches':patches},ensure_ascii=False)


@pytest.fixture
async def case(source_fixture, store, monkeypatch):  # noqa: F811 - imported pytest fixtures
    c = source_fixture
    monkeypatch.setattr(planner,'_validate_generated_volume_outline_or_raise',ACTUAL_DOMAIN_VALIDATOR)
    c.settings.llm.planner.model = 'openai/MiniMax-M3.1-Flash'
    c.settings.llm.planner.thinking_type = None
    c.settings.budget.execution_max_calls=c.budget.max_calls
    c.settings.budget.execution_max_tokens=c.budget.max_tokens
    c.settings.budget.max_tokens_per_project=c.budget.max_tokens
    c.accounting = True
    from bestseller.services.book_design import ensure_project_book_design_snapshot
    c.project.metadata_json={**deepcopy(c.project.metadata_json),
        'creation_protagonist_name':'Mira','creation_protagonist_source':'user'}
    ensure_project_book_design_snapshot(c.project)
    # This new scratch source is explicitly locked to its already validated
    # selected design; all four asset lineage hashes remain unchanged.
    c.project.metadata_json={**c.project.metadata_json,'book_design_snapshot_status':'locked'}
    assert planner._source_bound_cast_enabled(c.project)
    table = db.ChapterDraftVersionModel.__table__.to_metadata(MetaData())
    for col in table.c:
        if isinstance(col.type,JSONB):col.type=JSON()
        if col.server_default is not None and '::' in str(col.server_default.arg):col.server_default=None
    for constraint in list(table.constraints):
        if isinstance(constraint,ForeignKeyConstraint):table.constraints.remove(constraint)
    async with store[1].begin() as connection:
        await connection.run_sync(lambda conn:table.create(conn,checkfirst=True))
    original_retainer = llm._persist_prompt_payloads
    def retain(**kwargs):
        role = kwargs['effective_role_settings']
        kwargs['effective_stream'] = llm._effective_input_payload(kwargs['request'],role)['stream']
        return original_retainer(**kwargs)
    monkeypatch.setattr(llm,'_persist_prompt_payloads',retain)
    # Supply an actual typed cast in this new scratch fixture before seeding
    # any historical requests. Selection is re-pinned through its real API.
    from bestseller.services import foundation_selection
    cast = {**deepcopy(c.assets['cast_spec'].content),
        'protagonist': {'name': 'Mira', 'role': 'protagonist', 'aliases': ['M']},
        'supporting_cast': [{'name':'Len','role':'assistant','aliases':[]} ]}
    c.assets['cast_spec'].content = cast
    beat=deepcopy(c.beat.content)
    for item in beat['beats']:
        item['continuity_contract']={'fact_boundary':'Mira sees the marked station register only.'}
    c.beat.content=beat
    from types import SimpleNamespace
    from bestseller.services.conception_snapshots import snapshot_hash
    detached=SimpleNamespace(**{key:deepcopy(getattr(c.project,key)) for key in
        ('id','target_chapters','target_word_count','metadata_json','language')})
    detached._source_bound_planning_canonical={'id':str(c.canonical.id),
        'content_hash':snapshot_hash(c.canonical.content),'content':deepcopy(c.canonical.content)}
    projection=planner._compile_source_bound_volume_plan(detached,c.project.metadata_json['premise'],
        *(c.assets[kind].content for kind in foundation_selection.KINDS[:3]))
    volume=deepcopy(c.assets['volume_plan'].content)
    volume[0]['_meta']['source_projection_sha256']=planner._approved_payload_hash(projection)
    c.assets['volume_plan'].content=volume
    async with c.factory() as selected:
        selected.add_all([c.project,c.assets['cast_spec'],c.assets['volume_plan'],c.beat]); await selected.commit()
        live = await selected.get(db.ProjectModel,c.project.id)
        await foundation_selection.pin_completed_foundation(selected,project=live,
            workflow_id=c.assets['volume_plan'].source_run_id,settings=c.settings)
        await selected.commit()
        c.project.metadata_json=deepcopy(live.metadata_json)
    c.args = dict(project_slug=c.project.slug,parent_artifact_id=c.outline.id,
        parent_materialization_workflow_id=c.owner.id,beat_artifact_id=c.beat.id)
    async with c.factory() as session:
        assert not list(await session.scalars(select(db.ExecutionAttemptModel)))
        budget_row = await session.get(db.ExecutionBudgetModel,c.scope)
        budget_row.calls_used=budget_row.tokens_charged=0
        await session.commit()
        with bind_execution_scope(c.scope):
            project,context,validation = await legacy._inputs(session,c.settings,**c.args)
            family=native.semantic_family(project.id,c.scope,context)
            identity=native.proposal_workflow_id(family)
            directives=native._directives(context)
            first,contract=native._request_contract(c.settings,project,identity,family,context,validation,directives)
            state={'schema':native.SCHEMA,'phase':'prepared','source_context':context,
                'validation_context':validation,'semantic_family':family,'directives':directives,
                'request_contract':contract,'actor':'fixture operator','authorization_ref':'offline explicit fixture',
                'execution_scope_id':c.scope,'capacity_predecessor':None,
                'execution_before_dispatch':await _accounting(_Reader(session),c.scope,{})}
            state['binding_sha256']=content_hash({'context':context,'validation':validation,
                'directives':directives,'request_contract':contract})
            owner=db.WorkflowRunModel(id=identity,project_id=project.id,workflow_type=native.WORKFLOW,
                scope_type='planning_artifact',scope_id=c.outline.id,status='running',current_step='historical_native_first',metadata_json=deepcopy(state))
            session.add(owner);await session.flush();c.session=session
            raw1=json.dumps({'patches':[{'chapter_number':1,'scope':'scene','field':'purpose','value':{'story':'Observe the station.'}}]})
            await record(c,first,content=raw1)
            token,proof=await native._retained_protocol_failure(session,c.settings,project,identity,state)
            state['retained_protocol_proof']=proof
            second,second_contract=native._request_contract(c.settings,project,identity,family,context,
                validation,directives,feedback=[token.feedback(context['compiler_candidate'])])
            state['retained_protocol_request_contract']=second_contract
            c.raw2=native_second(context['compiler_candidate'],validation['cast_spec'])
            await record(c,second,content=c.raw2)
            state.update(phase='terminal_error',error_type='PlannerFallbackError')
            owner.status='failed';owner.current_step='proposal_requires_recovery_v2';owner.metadata_json=deepcopy(state)
            await session.commit()
    c.source_id=identity
    c.before_state=deepcopy(state)
    c.new_calls=[]
    return c


async def prepare(c,session):
    project,context,validation,returns,proof,anchor=await service._source(session,c.settings,
        args=c.args,source_proposal_workflow_id=c.source_id)
    from bestseller.services import planning_domain_field_protocol as protocol
    candidate,_=protocol.prepare_domain_candidate(context['compiler_candidate'],returns[1].completion.content)
    diagnostics,_=await service._diagnostics(session,project,candidate,validation)
    assert diagnostics['readiness']['passed'],diagnostics['readiness']['blocking_findings']
    prepared=await service._prepare_recovery(session,project,context,validation,returns,proof)
    return project,prepared


def corrections(prepared):
    result={}
    for path in prepared['contract']['paths']:
        spec=prepared['contract']['parameters']['properties']['corrections']['properties'][path]
        if 'enum' in spec:
            result[path]=deepcopy(spec['enum'][0])
        elif path.endswith('/title'):
            index=int(path.split('/')[2]);result[path]=independent_outline()['chapters'][index]['title']
        else:
            raise AssertionError('Unexpected actual fixture target: '+path)
    return result


def install_provider(c,monkeypatch,*,bad_first=False,domain_bad=False,after=None):
    async def complete(session,settings,request):
        from bestseller.services import planning_domain_field_protocol as protocol
        flat=request.prompt_version=='3.7-domain-fields-flat'
        async with c.factory() as other:
            owner=await other.get(db.WorkflowRunModel,request.workflow_run_id)
            assert owner.status=='running' and (owner.current_step.startswith('dispatch_domain_fields_')
                or owner.current_step=='dispatch_flat_fields_once')
            state=deepcopy(owner.metadata_json)
            index=len(c.new_calls)
            contract=state['request_contract'] if flat else state['request_contracts'][str(index)]
            assert contract['effective_input_sha256']==llm._effective_input_hash(
                request,service.budget.resolve_request_role(settings,c.project,request))
        values=corrections(state['prepared'])
        if bad_first and not c.new_calls:
            values.pop(next(iter(values)))
        if domain_bad:
            # Valid native schema, but real source integrity rejects duplicate titles.
            for path in values:
                if path.endswith('/title'):values[path]='The duplicate harbor plate'
        if flat:
            from bestseller.services import planning_domain_field_flat_transport as flat_protocol
            values={key:values[path] for key,path in state['prepared']['wire_contract']['id_to_path'].items()
                if path in values}
        call={'id':'offline-domain-'+str(index),'type':'function','function':{
            'name':flat_protocol.TOOL if flat else protocol.TOOL_NAME,
            'arguments':json.dumps(values if flat else {'corrections':values},ensure_ascii=False)}}
        c.new_calls.append(request.model_dump(mode='json'))
        c.session=session
        completion,run=await record(c,request,content='',calls=[call])
        if after:await after(session,run)
        return completion
    monkeypatch.setattr(llm,'complete_text',complete)


async def recover(c):
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            return await service.recover_outline_domain_fields(session,c.settings,**c.args,
                source_proposal_workflow_id=c.source_id,actor='offline operator',
                authorization_ref='explicit typed correction fixture',explicit_user_approval=True)


async def owned(c):
    from bestseller.services import outline_domain_transport_recovery as transport
    async with c.factory() as session:
        return list(await session.scalars(select(db.WorkflowRunModel).where(
            db.WorkflowRunModel.workflow_type.in_([service.WORKFLOW,transport.WORKFLOW]))))


async def test_actual_old_double_paid_custody_and_exact_scope_baseline(case):
    c=case
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            project,context,validation,returns,proof,anchor=await service._source(session,c.settings,
                args=c.args,source_proposal_workflow_id=c.source_id)
    assert len(returns)==len(anchor)==2
    assert all(paid.completion.llm_run_id is None for paid in returns)
    assert proof['existing_accounting']['calls_used']==2
    assert proof['existing_accounting']['tokens_charged']==36
    assert all(chapter.get('title') is None for chapter in context['compiler_candidate']['chapters'])
    assert c.new_calls==[]


async def test_actual_dynamic_contract_and_full_domain(case):
    from bestseller.services import planning_domain_field_protocol as protocol
    c=case
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            project,prepared=await prepare(c,session)
            candidate,receipt=protocol.apply_domain_corrections(prepared['candidate'],corrections(prepared),
                validation_report=prepared['validation_report'],identity_manifest=prepared['identity_manifest'],
                contract=prepared['contract'])
            from bestseller.services.outline_semantic_gate import evaluate_outline_semantic_gate,hard_contract_findings
            findings=[item.to_dict() for item in hard_contract_findings(evaluate_outline_semantic_gate(candidate))]
            assert not findings,json.dumps(findings,ensure_ascii=False)
            clean=await service._full_domain(session,project,candidate,prepared['validation_context'])
            request,contract=service._build_request(c.settings,project,
                service.recovery_workflow_id(c.source_id,prepared),prepared,[])
    assert clean==candidate
    assert receipt['non_targets_unchanged']
    assert len(prepared['contract']['paths'])==8
    assert service._decode(request,request)['candidate_payload']==prepared['candidate']
    assert contract['effective_input_sha256']


async def test_public_actual_native_success_completed_reuse_zero_resend(case,monkeypatch):
    c=case;install_provider(c,monkeypatch)
    before={key:path.read_bytes() for key,path in c.paths.items()}
    result=await recover(c)
    reused=await recover(c)
    assert content_hash(result)==content_hash(reused)
    assert len(c.new_calls)==1
    assert all(c.paths[key].read_bytes()==value for key,value in before.items())
    async with c.factory() as session:
        old=await session.get(db.WorkflowRunModel,c.source_id)
        budget=await session.get(db.ExecutionBudgetModel,c.scope)
        assert old.status=='failed' and old.metadata_json==c.before_state
        assert budget.calls_used==3 and budget.tokens_charged==66
    receipt=result['proposal_receipt']
    assert receipt['full_domain_and_readiness_passed']
    assert not receipt['grade_asserted'] and not receipt['adoption_granted']
    assert receipt['paid_attempt']['paid_proof']
    assert 'verification_pending' not in (await owned(c))[0].metadata_json['paid_attempt']


async def test_public_flat_empty_fields_uses_one_slot_never_renews(case,monkeypatch):
    c=case;install_provider(c,monkeypatch,bad_first=True)
    with pytest.raises(ValueError):await recover(c)
    with pytest.raises(ValueError,match='terminal_or_inflight_child_owner'):await recover(c)
    assert len(c.new_calls)==1
    owner=(await owned(c))[0]
    assert owner.status=='failed' and owner.metadata_json['paid_attempt']['paid_proof']
    assert owner.metadata_json['failure_diagnostics']


async def test_domain_rejection_retains_paid_proof_and_diagnostics_no_reopen(case,monkeypatch):
    c=case;install_provider(c,monkeypatch,domain_bad=True)
    with pytest.raises(planner.PlannerFallbackError):await recover(c)
    owner=(await owned(c))[0]
    entry=owner.metadata_json['paid_attempt']
    assert owner.status=='failed' and len(c.new_calls)==1
    assert entry['paid_proof'] and entry['immutable_rows']
    assert owner.metadata_json['error_type']=='OutlineContractValidationError'
    with pytest.raises(ValueError,match='terminal_or_inflight_child_owner'):await recover(c)
    assert len(c.new_calls)==1


@pytest.mark.parametrize('status',['running','failed','cancelled'])
async def test_existing_terminal_or_inflight_owner_never_resends(case,monkeypatch,status):
    c=case;install_provider(c,monkeypatch)
    async with c.factory() as session:
        session.add(db.WorkflowRunModel(id=uuid4(),project_id=c.project.id,workflow_type=service.WORKFLOW,
            scope_type='workflow_run',scope_id=c.source_id,status=status,current_step='retained',
            metadata_json={'schema':service.SCHEMA,'execution_scope_id':c.scope,
                'source_proposal_workflow_id':str(c.source_id)}))
        await session.commit()
    with pytest.raises(ValueError,match='terminal_or_inflight_domain_owner'):await recover(c)
    assert c.new_calls==[]


@pytest.mark.parametrize('tamper',['source','physical','attempt','orphan_return'])
async def test_source_physical_fee_and_inventory_tamper_blocks_before_owner(case,monkeypatch,tamper):
    c=case;install_provider(c,monkeypatch)
    async with c.factory() as session:
        if tamper=='source':
            row=await session.get(db.PlanningArtifactVersionModel,c.beat.id)
            body=deepcopy(row.content);body['unknown_changed']='different current source';row.content=body
        elif tamper=='physical':
            path=next(path for key,path in c.paths.items() if key.endswith(':response'))
            data=json.loads(path.read_text());data['content']['text']='changed full raw';path.write_text(json.dumps(data))
        elif tamper=='attempt':
            row=(await session.scalars(select(db.ExecutionAttemptModel))).first()
            row.actual_tokens+=1
        else:
            session.add(db.ExecutionOperationModel(key='orphan-native-return',scope_id=c.scope,
                input_hash='retained',status='completed',owner='offline orphan fixture',
                lease_until=c.budget.deadline_at,result_json={'metadata':{
                    'paid_call_origin':{'workflow_run_id':str(c.source_id)}}}))
        await session.commit()
    with pytest.raises(ValueError):await recover(c)
    assert c.new_calls==[] and await owned(c)==[]


@pytest.mark.parametrize('durable',[False,True])
async def test_initial_commit_cancellation_no_replacement_or_provider(case,monkeypatch,durable):
    import asyncio
    from bestseller.services import pipelines
    c=case;install_provider(c,monkeypatch)
    original=pipelines._checkpoint_commit
    fired=False
    async def commit(session):
        nonlocal fired
        if not fired:
            fired=True
            if durable:await original(session)
            raise asyncio.CancelledError('initial owner commit fixture')
        await original(session)
    monkeypatch.setattr(pipelines,'_checkpoint_commit',commit)
    with pytest.raises(asyncio.CancelledError,match='initial owner'):await recover(c)
    owners=await owned(c)
    assert c.new_calls==[] and len(owners)==int(durable)
    if durable:
        assert owners[0].status=='failed'
        assert owners[0].metadata_json['error_type']=='CancelledError'


@pytest.mark.parametrize('change',['owner_cancel','control_pause'])
async def test_fresh_presend_owner_and_control_fence(case,monkeypatch,change):
    from bestseller.services import pipelines
    c=case;install_provider(c,monkeypatch)
    original=pipelines._checkpoint_commit
    fired=False
    async def commit(session):
        nonlocal fired
        await original(session)
        async with c.factory() as other:
            from bestseller.services import outline_domain_transport_recovery as transport
            owner=(await other.scalars(select(db.WorkflowRunModel).where(
                db.WorkflowRunModel.workflow_type==transport.WORKFLOW))).first()
            if not fired and owner and owner.current_step=='dispatch_flat_fields_once':
                fired=True
                if change=='owner_cancel':owner.status='cancelled'
                else:
                    control=await other.get(db.BookProductionControlModel,c.project.id)
                    if control is None:
                        control=db.BookProductionControlModel(project_id=c.project.id);other.add(control)
                    control.desired_state='pause'
                await other.commit()
    monkeypatch.setattr(pipelines,'_checkpoint_commit',commit)
    with pytest.raises(ValueError,match='child_owner_changed|control_halted'):await recover(c)
    assert fired and c.new_calls==[]
    if change=='owner_cancel':assert (await owned(c))[0].status=='cancelled'
