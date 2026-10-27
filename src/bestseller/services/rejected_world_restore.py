"""Restore only an exact settled, independently rejected World proposal.

This is an unapproved repair input, never a paid-input replay or grade promotion.
The generic retained-generation opaque guard is not changed or caught.
"""
from copy import deepcopy
import hashlib,json
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db.models import LlmRunModel,ExecutionOperationModel,ExecutionAttemptModel,WorkflowRunModel,PlanningArtifactVersionModel
from bestseller.services import world_semantic_revision as revision
from bestseller.services.source_bound_planning_content import CONTENT_POLICY
from bestseller.services.source_bound_planning_submission import parse_planning_submission
SCHEMA='rejected-world-repair-restoration.v1'

async def _verified_review(session,project,*,book,baseline,candidate,report,expected_review_id=None,kind):
    from bestseller.services import planner as p
    try:await p._assert_bound_planning_canonical(session,project)
    except (ValueError,TypeError,KeyError):return None
    report=deepcopy(report or {})
    if expected_review_id is not None and report.get('llm_run_id')!=expected_review_id:return None
    if kind=='rejected':
        if report.get('decision')!='revise' or not (report.get('feedback') or report.get('core_conflicts')):return None
    elif kind=='approved':
        if not p._source_bound_report_is_draft_eligible(report):return None
    else:return None
    if not p._source_bound_world_review_matches_subject(report,project=project,
        authority=p._source_bound_design_snapshot(project),book_spec=book,baseline=baseline,
        candidate=candidate,allowed_fields=revision.FIELDS):return None
    if not await revision.completed_candidate(session,project,candidate):return None
    try:rid=UUID(str(report['llm_run_id']))
    except (ValueError,TypeError,KeyError):return None
    run=await session.get(LlmRunModel,rid,populate_existing=True)
    key=((run.metadata_json or {}).get('execution_receipt') or {}).get('operation_key') if run else None
    op=await session.get(ExecutionOperationModel,key,populate_existing=True) if key else None
    raw=(op.result_json or {}) if op else {}
    if (not run or not op or run.project_id!=project.id or run.logical_role!='critic'
        or run.prompt_template!='planner_source_bound_expansion_semantic_review'
        or op.scope_id!=(project.metadata_json or {}).get('execution_scope_id') or op.status!='completed'
        or op.input_hash!=run.prompt_hash or raw.get('fallback_used') is not False
        or run.provider in (None,'','unknown','mock','fixture','test','fallback','placeholder')
        or raw.get('provider')!=run.provider or raw.get('model_name')!=run.model_name
        or run.finish_reason not in ('stop','end_turn','completed') or raw.get('finish_reason')!=run.finish_reason
        or type(run.input_tokens) is not int or type(run.output_tokens) is not int
        or run.input_tokens<0 or run.output_tokens<0
        or raw.get('input_tokens')!=run.input_tokens or raw.get('output_tokens')!=run.output_tokens):return None
    owner=await session.get(WorkflowRunModel,run.workflow_run_id) if run.workflow_run_id else None
    if (owner is None or owner.project_id!=project.id or (owner.metadata_json or {}).get('execution_scope_id')!=op.scope_id):return None
    attempts=list(await session.scalars(select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key==key)))
    if len(attempts)!=1:return None
    a=attempts[0]
    if (a.status!='completed' or a.scope_id!=op.scope_id or a.input_hash!=op.input_hash
        or a.model_name!=run.model_name or a.actual_tokens!=run.input_tokens+run.output_tokens):return None
    text=str(raw.get('content') or '')
    if hashlib.sha256(text.encode()).hexdigest()!=report.get('raw_response_hash'):return None
    try:verdict=p._parse_source_bound_review_json(text)
    except ValueError:return None
    if (verdict.get('decision')!=report.get('decision') or verdict.get('candidate_hash')!=report.get('candidate_hash')
        or verdict.get('authority_snapshot_hash')!=report.get('authority_snapshot_hash')
        or verdict.get('core_conflicts')!=report.get('core_conflicts')
        or verdict.get('feedback')!=report.get('feedback')):return None
    return report

async def verified_rejection(session,project,*,book,baseline,candidate,expected_review_id=None):
    return await _verified_review(session,project,book=book,baseline=baseline,candidate=candidate,
        report=(project.metadata_json or {}).get('source_bound_expansion_review'),expected_review_id=expected_review_id,kind='rejected')

async def approved_artifact_report(session,project,*,book,baseline,candidate,artifact_id):
    from bestseller.services import planner as p
    try:aid=UUID(str(artifact_id))
    except (ValueError,TypeError):return None
    artifact=await session.get(PlanningArtifactVersionModel,aid,populate_existing=True)
    if (artifact is None or artifact.project_id!=project.id or artifact.artifact_type!='world_spec'
        or artifact.status!='approved' or p._without_planning_meta(artifact.content)!=p._without_planning_meta(candidate)):return None
    meta=p._mapping(artifact.content.get('_meta'))
    report=meta.get('source_bound_expansion_review_receipt') or {}
    if (meta.get('source_content_policy')!=CONTENT_POLICY or meta.get('source_bound_expansion_draft_eligible') is not True
        or (report.get('world_revision') or {}).get('schema')!=revision.SCHEMA):return None
    return await _verified_review(session,project,book=book,baseline=baseline,candidate=candidate,report=report,kind='approved')

async def reuse_approved(session,*,project,book,baseline,workflow_run_id):
    from bestseller.services import planner as p
    artifacts=list(await session.scalars(select(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.project_id==project.id,PlanningArtifactVersionModel.artifact_type=='world_spec',
        PlanningArtifactVersionModel.status=='approved').order_by(PlanningArtifactVersionModel.version_no.desc()).limit(5)))
    for artifact in artifacts:
        report=await approved_artifact_report(session,project,book=book,baseline=baseline,
            candidate=artifact.content,artifact_id=artifact.id)
        if report is None:continue
        owner=await session.get(WorkflowRunModel,workflow_run_id)
        if (owner is None or owner.project_id!=project.id or owner.status!='running'
            or (owner.metadata_json or {}).get('execution_scope_id')!=(project.metadata_json or {}).get('execution_scope_id')):
            raise p.PlannerFallbackError('Reviewed World reuse owner changed')
        candidate=deepcopy(artifact.content);candidate['_meta']={**p._mapping(candidate.get('_meta')),
            'reviewed_world_reuse_artifact_id':str(artifact.id),'paid_current_input_asserted':False}
        record={'artifact_id':str(artifact.id),'candidate_hash':report['candidate_hash'],
            'review_llm_run_id':report['llm_run_id'],'canonical_baseline_hash':report['canonical_baseline_hash'],
            'source_generation_llm_run_id':candidate['_meta']['generation_llm_run_id'],
            'authority':'existing_independently_reviewed_corrected_world','provider_calls':0}
        m=deepcopy(owner.metadata_json or {});history=deepcopy(m.get('world_reviewed_artifact_reuses') or {})
        history[str(artifact.id)]=record;owner.metadata_json={**m,'world_reviewed_artifact_reuses':history}
        from bestseller.services.progress_context import emit_activity
        emit_activity('world_reviewed_artifact_reused',{'artifact_id':str(artifact.id),'review_id':report['llm_run_id']})
        return candidate
    return None

async def restore(session,*,project,book,baseline,workflow_run_id):
    from bestseller.services import planner as p
    report=(project.metadata_json or {}).get('source_bound_expansion_review') or {}
    fields=report.get('changed_fields') or []
    if (report.get('decision')!='revise' or report.get('reason')!='reviewed'
        or not fields or not set(fields)<=revision.FIELDS):return None
    # A recognized World rejection with broken evidence must stop, never become
    # a miss that submits a replacement request.
    matches=[]
    runs=list(await session.scalars(select(LlmRunModel).where(LlmRunModel.project_id==project.id,
        LlmRunModel.logical_role=='planner',LlmRunModel.prompt_template=='planner_world_spec')
        .order_by(LlmRunModel.created_at.desc()).limit(20)))
    for run in runs:
        key=((run.metadata_json or {}).get('execution_receipt') or {}).get('operation_key')
        op=await session.get(ExecutionOperationModel,key,populate_existing=True) if key else None
        if not op or op.status!='completed':continue
        try:body,_,_=parse_planning_submission(op.result_json or {},kind='world_spec')
        except (ValueError,TypeError,KeyError):continue
        if p._approved_payload_hash({'world_spec':p._without_planning_meta(body),'cast_spec':{}})!=report.get('candidate_hash'):continue
        candidate=deepcopy(body)
        candidate['_meta']={**p._mapping(candidate.get('_meta')),
            'source_content_policy':CONTENT_POLICY,'source_compiler':'source-bound-model-completion.v3',
            'generation_llm_run_id':str(run.id),'creator_authorship_asserted':False,'approved_complete':False,
            'planning_status':'rejected_pending_revision','restored_rejection_review_id':report.get('llm_run_id'),
            'repair_restoration_policy':SCHEMA,'paid_current_input_asserted':False}
        verified=await verified_rejection(session,project,book=book,baseline=baseline,candidate=candidate)
        if verified:matches.append((candidate,run,verified))
    if len(matches)!=1:raise p.PlannerFallbackError('Rejected World restoration evidence missing, mismatched or ambiguous')
    candidate,run,verified=matches[0]
    owner=await session.get(WorkflowRunModel,workflow_run_id)
    if (owner is None or owner.project_id!=project.id or owner.status!='running'
        or (owner.metadata_json or {}).get('execution_scope_id')!=(project.metadata_json or {}).get('execution_scope_id')):
        raise p.PlannerFallbackError('Rejected World restoration owner changed')
    record={'schema':SCHEMA,'candidate_hash':verified['candidate_hash'],'source_llm_run_id':str(run.id),
        'review_llm_run_id':verified['llm_run_id'],'review_raw_response_hash':verified['raw_response_hash'],
        'canonical_baseline_hash':verified['canonical_baseline_hash'],
        'authority_snapshot_hash':verified['authority_snapshot_hash'],
        'authority':'rejected_candidate_pending_revision','inherited_decision':'revise',
        'approval_inherited':False,'provider_calls':0,'unknown_reconciled':False}
    record_key=hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    meta=deepcopy(owner.metadata_json or {});history=deepcopy(meta.get('world_repair_restorations') or {})
    if record_key in history and history[record_key]!=record:raise p.PlannerFallbackError('Rejected World restoration audit conflict')
    history[record_key]=record;owner.metadata_json={**meta,'world_repair_restorations':history}
    from bestseller.services.progress_context import emit_activity
    emit_activity('world_rejected_candidate_restored',{'source_run_id':str(run.id),'review_id':verified['llm_run_id'],
        'candidate_hash':verified['candidate_hash'],'authority':'rejected_pending_revision'})
    return candidate
