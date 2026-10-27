"""Explicit approved-conception -> project execution binding, without regrant.

The original creation binding is immutable. A new ledger receipt proves the
allowed phase transition, exact selected source and unchanged author controls.
"""
from copy import deepcopy
from datetime import datetime,timezone
import json
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db.models import (ExecutionBudgetModel,ExecutionOperationModel,ExecutionAttemptModel,
    ProjectModel,PlanningArtifactVersionModel,LlmRunModel,WorkflowRunModel)
from bestseller.services.independent_acceptance import policy,payload_identity,digest,validate_old,MODEL
from bestseller.services.budget import execution_scope_block_code,completion_authorization
from bestseller.services.conception_snapshots import snapshot_hash
SCHEMA='execution-phase-handoff.v1'

async def proof(session,*,task_id,payload,lock=False,audit_only=False):
    if type(audit_only) is not bool:raise ValueError('phase_handoff_audit_mode_invalid')
    scope=payload.get('_active_continuation_scope')
    if (not isinstance(scope,str) or not scope.startswith('conception-continuation:'+task_id+':')
        or payload.get('_run_conception') is not False):
        raise ValueError('phase_handoff_transition_not_allowed')
    b=await session.get(ExecutionBudgetModel,scope,with_for_update=lock,populate_existing=True)
    grant=policy(b) if b else None;auth=completion_authorization(b) if b else None
    if (not grant or not grant.get('submitted_at')
        or grant['request'].get('continuation_task_id')!=task_id):
        raise ValueError('phase_handoff_authorization_unavailable')
    if not audit_only:
        code=execution_scope_block_code(b)
        if code:
            # Preserve the ledger stop through active_scope's binding-error mapping.
            from bestseller.services.budget import ExecutionBudgetError
            raise ExecutionBudgetError(code,scope)
    if not auth or auth.get('status')!='active':
        raise ValueError('phase_handoff_authorization_unavailable')
    original_hash=grant['request']['payload_sha256']
    if payload_identity({**payload,'_run_conception':True})!=original_hash:
        raise ValueError('phase_handoff_author_intent_changed')
    await validate_old(session,grant,lock=lock)
    stmt=select(ProjectModel).where(ProjectModel.slug==payload.get('slug'))
    if lock:stmt=stmt.with_for_update().execution_options(populate_existing=True)
    project=await session.scalar(stmt)
    m=deepcopy(project.metadata_json or {}) if project else {}
    selection=m.get('reviewed_conception_selection') or {};sid=m.get('creation_canonical_snapshot_id')
    if (not project or m.get('execution_scope_id')!=scope or m.get('conception_approved') is not True
        or not sid or selection.get('snapshot_id')!=sid
        or selection.get('snapshot_hash')!=m.get('creation_canonical_snapshot_hash')):
        raise ValueError('phase_handoff_selected_canonical_required')
    snap=await session.get(PlanningArtifactVersionModel,UUID(sid),with_for_update=lock,populate_existing=True)
    if (snap is None or snap.project_id!=project.id or snap.status!='canonical'
        or snap.artifact_type!='conception_snapshot' or snapshot_hash(snap.content)!=selection['snapshot_hash']):
        raise ValueError('phase_handoff_canonical_changed')
    from bestseller.services.reviewed_conception_selection import assert_snapshot_quality
    report=assert_snapshot_quality(snap.content)
    intent_id=m.get('creation_intent_snapshot_id')
    intent=await session.get(PlanningArtifactVersionModel,UUID(intent_id)) if intent_id else None
    if (intent is None or intent.project_id!=project.id or intent.artifact_type!='creation_intent'
        or intent.status!='validated' or snapshot_hash(intent.content)!=snap.content.get('creation_intent_hash')
        or snapshot_hash(intent.content)!=m.get('creation_intent_contract_hash')):
        raise ValueError('phase_handoff_frozen_intent_changed')
    if (payload.get('premise')!=snap.content.get('premise') or payload.get('title')!=snap.content.get('title')
        or project.title!=snap.content.get('title') or int(payload.get('target_chapters') or 0)!=project.target_chapters
        or int(payload.get('target_words') or 0)!=project.target_word_count):
        raise ValueError('phase_handoff_project_projection_changed')
    qid=selection.get('quality_run_id');opkey=selection.get('quality_operation')
    q=await session.get(LlmRunModel,UUID(qid)) if qid else None
    op=await session.get(ExecutionOperationModel,opkey) if opkey else None;r=op.result_json or {} if op else {}
    origin=(q.metadata_json or {}).get('paid_call_origin',{}) if q else {}
    quality_workflow=None
    same_book=bool(q and q.project_id==project.id)
    if q and q.project_id is None:
        wid=origin.get('workflow_run_id')
        quality_workflow=await session.get(WorkflowRunModel,UUID(wid)) if wid else None
        wm=quality_workflow.metadata_json or {} if quality_workflow else {}
        same_book=bool((q.metadata_json or {}).get('conception_task_id')==task_id
            and quality_workflow and quality_workflow.project_id is None
            and quality_workflow.workflow_type=='conception_initial' and quality_workflow.status=='completed'
            and wm.get('execution_scope_id')==scope and wm.get('creation_intent_hash')==snapshot_hash(intent.content))
    if (not q or not op or qid not in report['review_llm_run_ids'] or op.scope_id!=scope or op.status!='completed'
        or (q.metadata_json or {}).get('execution_receipt',{}).get('operation_key')!=opkey
        or not same_book or q.logical_role!='critic' or q.model_name!=MODEL or r.get('model_name')!=q.model_name
        or q.finish_reason!='stop' or r.get('finish_reason')!='stop' or r.get('fallback_used') is not False
        or r.get('input_tokens')!=q.input_tokens or r.get('output_tokens')!=q.output_tokens):
        raise ValueError('phase_handoff_actual_quality_receipt_required')
    raw=str(r.get('content') or '').strip()
    if raw.startswith('```'):raw=raw[raw.find('\n')+1:raw.rfind('```')].strip()
    verdict=json.loads(raw)
    if verdict.get('passed') is not True or verdict.get('hard_conflicts')!=[]:
        raise ValueError('phase_handoff_quality_not_passed')
    paid=await session.scalar(select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key==opkey,
        ExecutionAttemptModel.scope_id==scope,ExecutionAttemptModel.status=='completed',
        ExecutionAttemptModel.input_hash==op.input_hash,ExecutionAttemptModel.model_name==q.model_name,
        ExecutionAttemptModel.actual_tokens==q.input_tokens+q.output_tokens))
    if paid is None:raise ValueError('phase_handoff_quality_not_settled')
    terminal=m.get('execution_terminal') or {}
    original_handoff_stop = (terminal.get('code')=='execution_continuation_binding_invalid'
        and m.get('production_pause_reason')=='execution_continuation_binding_invalid' and project.status=='paused')
    audit_unknown_stop = (audit_only
        and terminal.get('code')=='execution_result_unknown'
        and m.get('production_pause_reason')=='execution_result_unknown' and project.status=='paused')
    audit_ledger_stop = False
    if (audit_only and terminal.get('code')=='execution_ledger_unavailable'
        and m.get('production_pause_reason')=='execution_ledger_unavailable' and project.status=='paused'):
        from bestseller.services.failed_stream_recovery import verified_stopped_partial
        audit_ledger_stop = bool(await verified_stopped_partial(session,project=project,scope_id=scope))
    if terminal and not (original_handoff_stop or audit_unknown_stop or audit_ledger_stop):
        raise ValueError('phase_handoff_other_stop_preserved')
    core={'schema':SCHEMA,'scope_id':scope,'task_id':task_id,'project_id':str(project.id),
        'from_phase':'approved_conception','to_phase':'project_execution',
        'original_binding_sha256':original_hash,'derived_binding_sha256':payload_identity(payload),
        'canonical_id':sid,'canonical_hash':selection['snapshot_hash'],'intent_hash':snapshot_hash(intent.content),
        'quality_run_id':qid,'quality_operation':opkey,'quality_result_sha256':digest(r),
        'quality_origin_workflow_id':str(quality_workflow.id) if quality_workflow else None,
        'authorization_ref':auth['authorization_ref'],'unknown_reconciled':False,'credit_released':False,
        'grade_reused_for_changed_content':False,'actor':'web-ui same-project phase continuation'}
    return core,project,b

def receipt_key(core):return digest({'schema':SCHEMA,'binding':core})

async def validate_binding(session,*,task_id,payload,audit_only=False):
    core,project,_=await proof(session,task_id=task_id,payload=payload,audit_only=audit_only)
    key=receipt_key(core)
    row=await session.get(ExecutionOperationModel,key)
    if (payload.get('_execution_phase_handoff')!=key or row is None or row.status!='phase_handoff'
        or row.scope_id!=core['scope_id'] or row.input_hash!=core['derived_binding_sha256']
        or (row.result_json or {}).get('binding')!=core
        or key not in (project.metadata_json or {}).get('execution_phase_handoff_receipts',[])):
        raise ValueError('phase_handoff_receipt_required')
    return core['scope_id']

async def create(session,*,task_id,payload):
    core,project,b=await proof(session,task_id=task_id,payload=payload,lock=True)
    scope=core['scope_id'];key=receipt_key(core)
    if await session.scalar(select(ExecutionAttemptModel.id).where(ExecutionAttemptModel.scope_id==scope,
        ExecutionAttemptModel.status.in_(['reserved','unknown'])).limit(1)):
        raise ValueError('phase_handoff_pending_or_unknown_request')
    if await session.scalar(select(WorkflowRunModel.id).where(WorkflowRunModel.project_id==project.id,
        WorkflowRunModel.status.in_(['running','queued','pending','stopping'])).limit(1)):
        raise ValueError('phase_handoff_active_writer')
    m=deepcopy(project.metadata_json or {});existing=await session.get(ExecutionOperationModel,key)
    if existing:
        if (existing.status!='phase_handoff' or existing.scope_id!=scope or (existing.result_json or {}).get('binding')!=core
            or key not in m.get('execution_phase_handoff_receipts',[])):
            raise ValueError('phase_handoff_audit_conflict')
        return {'receipt_key':key,**existing.result_json}
    before={'execution_terminal':m.get('execution_terminal'),'project_status':project.status,
        'production_paused':m.get('production_paused'),'production_pause_reason':m.get('production_pause_reason'),
        'requires_human_review':m.get('requires_human_review')}
    receipt={'binding':core,'previous_projection':before,'provider_calls':0,'budget_regranted':False,
        'created_at':datetime.now(timezone.utc).isoformat()}
    session.add(ExecutionOperationModel(key=key,scope_id=scope,input_hash=core['derived_binding_sha256'],
        status='phase_handoff',owner='explicit-same-project-phase-handoff',lease_until=b.deadline_at,result_json=receipt))
    history=list(m.get('execution_phase_handoff_receipts') or [])
    if len(history)>=16:raise ValueError('phase_handoff_history_limit')
    m['execution_phase_handoff_receipts']=[*history,key]
    if m.get('execution_terminal'):
        m.pop('execution_terminal');m.update(production_paused=False,production_pause_reason=None,requires_human_review=False)
        project.status='planning'
    project.metadata_json=m
    await session.flush()
    return {'receipt_key':key,**receipt}
