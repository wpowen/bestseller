"""Actual SQLite/source/physical proof and one atomically consumed dispatch."""
from copy import deepcopy
import json
from uuid import UUID

import pytest
from sqlalchemy.exc import DBAPIError
from test_outline_domain_recovery import (  # noqa: F401 - actual shared pytest fixtures
    case as domain_case, source_fixture, source_case, store, record, install_provider,
)

from bestseller.infra.db import models as db
from bestseller.services import llm, outline_domain_recovery as old
from bestseller.services import outline_domain_transport_recovery as service
from bestseller.services.budget import bind_execution_scope, ExecutionLedger, ExecutionBudgetError

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


@pytest.fixture
async def migration(domain_case):  # noqa: F811 - imported pytest fixture
    c=domain_case
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            project,context,validation,returns,proof,anchor=await old._source(session,c.settings,
                args=c.args,source_proposal_workflow_id=c.source_id)
            prepared=await old._prepare_recovery(session,project,context,validation,returns,proof)
            identity=old.recovery_workflow_id(c.source_id,prepared)
            state={'schema':old.SCHEMA,'phase':'prepared','execution_scope_id':c.scope,
                'source_proposal_workflow_id':str(c.source_id),'source_evidence':deepcopy(proof),
                'execution_anchor':anchor,'prepared':prepared,'actor':'offline operator',
                'authorization_ref':'existing completion fixture','protocol_attempts':[],'request_contracts':{}}
            owner=db.WorkflowRunModel(id=identity,project_id=c.project.id,workflow_type=old.WORKFLOW,
                scope_type='workflow_run',scope_id=c.source_id,status='running',current_step='retained_native_fields',
                metadata_json=deepcopy(state))
            session.add(owner);await session.flush();c.session=session
            feedback=[]
            for index in range(2):
                request,contract=old._build_request(c.settings,project,identity,prepared,feedback)
                calls=[{'id':'retained-empty-'+str(index),'type':'function','function':{
                    'name':'submit_outline_domain_corrections','arguments':'{"corrections":{}}'}}]
                _,run=await record(c,request,content='',calls=calls)
                fresh=await service._Reader(session).get(db.LlmRunModel,run.id)
                paid=await old._verified_paid(session,c.settings,project,identity,fresh,request,contract)
                with pytest.raises(ValueError) as error:old._parse_apply(paid.completion,prepared)
                item=old._feedback(index,paid,error.value)
                state['request_contracts'][str(index)]=contract
                state['protocol_attempts'].append({**old._attempt_receipt(paid),'error':item['error']})
                feedback.append(item)
            state.update(phase='terminal_error',error_type='ValueError')
            owner.status='failed';owner.current_step='domain_recovery_requires_attention';owner.metadata_json=deepcopy(state)
            await session.commit()
    c.parent_id=identity;c.parent_state=deepcopy(state)
    return c


async def recover(c,**overrides):
    kwargs={'actor':'offline operator','authorization_ref':'existing completion fixture','explicit_user_approval':True}
    kwargs.update(overrides)
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            return await service.recover_outline_domain_transport(session,c.settings,**c.args,
                parent_domain_workflow_id=c.parent_id,**kwargs)


async def child(c):
    async with c.factory() as session:
        return await session.get(db.WorkflowRunModel,service.transport_workflow_id(c.parent_id))


async def test_readonly_preflight_proves_two_empty_returns_and_writes_nothing(migration):
    c=migration
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            project,prepared,evidence,anchor=await service._source(session,c.settings,
                args=c.args,parent_domain_workflow_id=c.parent_id)
            identity=service.transport_workflow_id(c.parent_id)
            request,contract=service._build_request(c.settings,project,identity,prepared,
                authorization_ref='existing completion fixture',prior_workflow_id=c.parent_id,
                source_evidence=evidence,actor='offline operator')
            role=service.budget.resolve_request_role(c.settings,project,request)
            binding=service._grant_binding(project,request,contract,evidence,
                authorization_ref='existing completion fixture',prior_workflow_id=c.parent_id,
                endpoint=role.api_base,actor='offline operator')
            assert not session.new and not session.dirty
            assert await session.get(db.WorkflowRunModel,identity) is None
            budget=await session.get(db.ExecutionBudgetModel,c.scope)
            assert not (budget.metadata_json or {}).get('single_dispatch_grants')
    assert len(anchor)==4 and evidence['prior_purpose_physical_attempts']==2
    assert evidence['maximum_purpose_physical_attempts']==3
    assert binding['dispatch_scope']=='workflow'
    decoded=service._decode(request,request)
    assert decoded['candidate_payload']==prepared['candidate']
    assert 'wire_transition' in decoded['source_context']
    assert 'rejected_response' not in decoded['source_context']['wire_transition']
    assert c.new_calls==[]


async def test_migration_success_completed_cache_proves_all_and_preserves_old_rows(migration,monkeypatch):
    c=migration;install_provider(c,monkeypatch)
    raw_before={key:path.read_bytes() for key,path in c.paths.items()}
    result=await recover(c)
    assert await recover(c)==result and len(c.new_calls)==1
    async with c.factory() as session:
        old_parent=await session.get(db.WorkflowRunModel,c.parent_id)
        old_native=await session.get(db.WorkflowRunModel,c.source_id)
        budget=await session.get(db.ExecutionBudgetModel,c.scope)
        assert old_parent.status=='failed' and old_parent.metadata_json==c.parent_state
        assert old_native.status=='failed' and old_native.metadata_json==c.before_state
        assert budget.calls_used==5 and budget.tokens_charged==126
        grants=budget.metadata_json['single_dispatch_grants']
        assert len(grants)==1 and next(iter(grants.values()))['status']=='spent'
        completed=await session.get(db.WorkflowRunModel,service.transport_workflow_id(c.parent_id))
        state=completed.metadata_json
        telemetry=await session.get(db.LlmRunModel,UUID(state['paid_attempt']['llm_run_id']))
        marker='domain_transport_grant_sha256'
        assert telemetry.metadata_json[marker]==state['request_contract']['grant_binding_sha256']
        # Real gateway caches whitelist metadata. The independent physical
        # witness is the literal input seed, never an invented paid marker.
        for item in state['paid_attempt']['immutable_rows']['operations']:
            operation=await session.get(db.ExecutionOperationModel,item['key'])
            value=operation.result_json
            paid=value['result'] if value.get('schema')=='transport-result.v1' else value
            assert marker not in paid['metadata']
    assert all(c.paths[key].read_bytes()==raw for key,raw in raw_before.items())
    assert not result['proposal_receipt']['grade_asserted']
    assert result['proposal_receipt']['source_evidence']['maximum_purpose_physical_attempts']==3


async def test_actual_reservation_blocks_second_physical_attempt(migration,monkeypatch):
    c=migration;install_provider(c,monkeypatch)
    await recover(c)
    request=llm.LLMCompletionRequest.model_validate(c.new_calls[0])
    role=service.budget.resolve_request_role(c.settings,c.project,request)
    with bind_execution_scope(c.scope):
        ledger=ExecutionLedger(c.factory.kw['bind'],c.scope,c.settings.budget)
        digest=llm._effective_input_hash(request,role)
        claim=await ledger.claim('different-operation-cannot-renew-purpose',digest)
        with pytest.raises(ExecutionBudgetError,match='single_dispatch'):
            await ledger.reserve(claim,role.model,digest,30,endpoint=role.api_base,
                single_dispatch_id=request.metadata['single_dispatch_authorization_id'],
                workflow_id=str(request.workflow_run_id),template=request.prompt_template,version=request.prompt_version)
        fallback=await ledger.claim('fallback-cannot-drop-grant','different-fallback-input')
        with pytest.raises(ExecutionBudgetError,match='single_dispatch'):
            await ledger.reserve(fallback,role.model+'-fallback','different-fallback-input',30,
                endpoint=role.api_base,single_dispatch_id=None,workflow_id=str(request.workflow_run_id),
                template=request.prompt_template,version=request.prompt_version)
    async with c.factory() as session:
        assert (await session.get(db.ExecutionBudgetModel,c.scope)).calls_used==5


@pytest.mark.parametrize('change',['old_raw','old_attempt','source','parent_state','orphan_return'])
async def test_parent_source_custody_tamper_zero_dispatch(migration,monkeypatch,change):
    c=migration;install_provider(c,monkeypatch)
    async with c.factory() as session:
        if change=='old_raw':
            path=list(c.paths.values())[-1]
            value=json.loads(path.read_text());value['content']['text']='changed original';path.write_text(json.dumps(value))
        elif change=='old_attempt':
            aid=c.parent_state['protocol_attempts'][0]['immutable_rows']['attempt_id']
            row=await session.get(db.ExecutionAttemptModel,aid);row.actual_tokens+=1
        elif change=='source':
            row=await session.get(db.PlanningArtifactVersionModel,c.beat.id)
            row.content={**row.content,'different_current_source':True}
        elif change=='parent_state':
            row=await session.get(db.WorkflowRunModel,c.parent_id)
            row.metadata_json={**row.metadata_json,'actor':'changed old authorization'}
        else:
            session.add(db.ExecutionOperationModel(key='extra-parent-return',scope_id=c.scope,input_hash='extra',
                status='completed',owner='fixture',lease_until=c.budget.deadline_at,result_json={'metadata':{
                    'paid_call_origin':{'workflow_run_id':str(c.parent_id)}}}))
        await session.commit()
    if change=='parent_state':
        # Row contents are bound at this first admission; arbitrary prior audit
        # extension alone is not source authority. Use an actual receipt change.
        async with c.factory() as session:
            row=await session.get(db.WorkflowRunModel,c.parent_id)
            state=deepcopy(row.metadata_json);state['protocol_attempts'][0]['error']='different failure'
            row.metadata_json=state;await session.commit()
    with pytest.raises(ValueError):await recover(c)
    assert c.new_calls==[] and await child(c) is None


@pytest.mark.parametrize('status',['running','failed','cancelled'])
async def test_failed_or_inflight_child_never_reopens(migration,monkeypatch,status):
    c=migration;install_provider(c,monkeypatch)
    async with c.factory() as session:
        session.add(db.WorkflowRunModel(id=service.transport_workflow_id(c.parent_id),project_id=c.project.id,
            workflow_type=service.WORKFLOW,scope_type='workflow_run',scope_id=c.parent_id,
            status=status,current_step='frozen',metadata_json={'schema':service.SCHEMA,'mode':service.TRANSITION}))
        await session.commit()
    with pytest.raises(ValueError,match='terminal_or_inflight_child_owner'):await recover(c)
    assert not c.new_calls


async def test_empty_third_return_preserves_paid_receipt_and_denies_fourth(migration,monkeypatch):
    c=migration;install_provider(c,monkeypatch,bad_first=True)
    with pytest.raises(ValueError):await recover(c)
    row=await child(c)
    assert row.status=='failed' and row.metadata_json['paid_attempt']['paid_proof']
    with pytest.raises(ValueError,match='terminal_or_inflight_child_owner'):await recover(c)
    assert len(c.new_calls)==1


async def test_failed_legacy_owner_blocks_fresh_version_reset(migration):
    c=migration
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            with pytest.raises(ValueError,match='legacy_semantic_purpose_owner'):
                await service.propose_flat_outline_domain_fields(session,c.settings,**c.args,
                    source_proposal_workflow_id=c.source_id,actor='offline operator',
                    authorization_ref='another reference cannot renew',explicit_user_approval=True)
    assert c.new_calls==[]


async def test_db_exception_diagnostics_cannot_persist_sql_or_values(migration,monkeypatch):
    c=migration
    async def fail(*args,**kwargs):
        raise DBAPIError('SECRET SQL',{'secret':'PRIVATE PARAM'},RuntimeError('PRIVATE DRIVER'),False)
    monkeypatch.setattr(llm,'complete_text',fail)
    with pytest.raises(DBAPIError):await recover(c)
    state=(await child(c)).metadata_json
    failure=json.dumps(state['failure_diagnostics'])
    assert 'error' not in state and 'SECRET' not in failure and 'PRIVATE' not in failure
    assert state['error_type']=='DBAPIError'


async def test_joint_state_result_grant_accounting_tamper_cannot_change_physical_witness(migration,monkeypatch):
    c=migration;install_provider(c,monkeypatch)
    await recover(c)
    async with c.factory() as session:
        row=await session.get(db.WorkflowRunModel,service.transport_workflow_id(c.parent_id))
        state=deepcopy(row.metadata_json)
        state['source_evidence']['native_source']['existing_accounting']['calls_used']=0
        project=await service._Reader(session).get(db.ProjectModel,c.project.id)
        # Rebuild all mutable coordinator/grant receipts together, leaving the
        # actual physical request, telemetry and return evidence untouched.
        with bind_execution_scope(c.scope):
            request,contract=service._build_request(c.settings,project,row.id,state['prepared'],
                authorization_ref=state['authorization_ref'],prior_workflow_id=c.parent_id,
                source_evidence=state['source_evidence'],actor=state['actor'])
            binding=service._grant_binding(project,request,contract,state['source_evidence'],
                authorization_ref=state['authorization_ref'],prior_workflow_id=c.parent_id,
                endpoint=service.budget.resolve_request_role(c.settings,project,request).api_base,actor=state['actor'])
        state['request_contract']=contract;state['grant_binding']=binding
        state['result']['proposal_receipt']['source_evidence']=deepcopy(state['source_evidence'])
        row.metadata_json=state
        budget=await session.get(db.ExecutionBudgetModel,c.scope)
        metadata=deepcopy(budget.metadata_json)
        metadata['single_dispatch_grants'][request.metadata['single_dispatch_authorization_id']]['binding']=binding
        budget.metadata_json=metadata;await session.commit()
    with pytest.raises(ValueError):await recover(c)
    assert len(c.new_calls)==1
