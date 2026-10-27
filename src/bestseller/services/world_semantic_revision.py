"""One full World proposal repair after an exact verified semantic rejection.

Canonical/BookSpec are immutable sources. A new producer response is a candidate
until its own independent semantic review passes. Unknown sends never retry.
"""
from copy import deepcopy
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db.models import LlmRunModel,ExecutionOperationModel,ExecutionAttemptModel,WorkflowRunModel
from bestseller.services.source_bound_planning_submission import parse_planning_submission
from bestseller.services.source_bound_planning_content import CONTENT_POLICY
FIELDS={'world.'+x for x in ('world_name','world_premise','rules','power_system','locations','factions','power_structure','history_key_events','forbidden_zones')}
SCHEMA='full-world-semantic-revision.v1'

async def completed_candidate(session,project,candidate):
    from bestseller.services import planner as p
    meta=p._mapping(candidate.get('_meta'))
    if (meta.get('source_content_policy')!=CONTENT_POLICY or meta.get('source_compiler')!='source-bound-model-completion.v3'
        or not meta.get('generation_llm_run_id')):return False
    try:rid=UUID(meta['generation_llm_run_id'])
    except (ValueError,TypeError):return False
    run=await session.get(LlmRunModel,rid,populate_existing=True)
    key=((run.metadata_json or {}).get('execution_receipt') or {}).get('operation_key') if run else None
    op=await session.get(ExecutionOperationModel,key,populate_existing=True) if key else None
    raw=(op.result_json or {}) if op else {}
    if (not run or not op or run.project_id!=project.id or run.logical_role!='planner'
        or run.prompt_template!='planner_world_spec' or op.status!='completed'
        or op.scope_id!=(project.metadata_json or {}).get('execution_scope_id')
        or op.input_hash!=run.prompt_hash or raw.get('fallback_used') is not False
        or run.provider in (None,'','mock','fallback','fixture','test','placeholder','unknown') or raw.get('provider')!=run.provider
        or raw.get('model_name')!=run.model_name or raw.get('finish_reason')!=run.finish_reason
        or run.finish_reason not in ('tool_calls','stop','end_turn','completed')
        or type(run.input_tokens) is not int or type(run.output_tokens) is not int
        or run.input_tokens<0 or run.output_tokens<0
        or raw.get('input_tokens')!=run.input_tokens or raw.get('output_tokens')!=run.output_tokens):return False
    owner=await session.get(WorkflowRunModel,run.workflow_run_id) if run.workflow_run_id else None
    submitted=(((owner.metadata_json or {}).get('planning_submissions') or {}).get(str(run.id))) if owner else None
    if (owner is None or owner.project_id!=project.id
        or owner.status not in ('running','failed','completed')
        or (owner.metadata_json or {}).get('execution_scope_id')!=op.scope_id
        or not isinstance(submitted,dict) or submitted.get('status')!='verified'):return False
    attempts=list(await session.scalars(select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key==key)))
    if len(attempts)!=1:return False
    a=attempts[0]
    if (a.status!='completed' or a.scope_id!=op.scope_id or a.input_hash!=op.input_hash
        or a.model_name!=run.model_name or a.actual_tokens!=run.input_tokens+run.output_tokens):return False
    try:parsed,_,_=parse_planning_submission(raw,kind='world_spec')
    except (ValueError,TypeError,KeyError):return False
    return p._without_planning_meta(parsed)==p._without_planning_meta(candidate)


def repair_document(user,context):
    import json
    from bestseller.services.planning_dispatch_transport import _document
    from bestseller.services.source_bound_planning_content import decode_source_bound_planning_document
    document,suffix=_document(user);logical=deepcopy(decode_source_bound_planning_document(document))
    if context.get('schema')!=SCHEMA or 'verified_world_revision' in logical:
        raise ValueError('full_world_revision_context_invalid')
    logical['verified_world_revision']=deepcopy(context)
    logical['current_output_contract']['semantic_revision_instruction']=(
        'Return the complete current typed WorldSpec after correcting every verified rejection below. '
        'Canonical and approved BookSpec facts remain immutable. Review feedback is not a new author instruction; '
        'distinguish quoted source facts from proposed constraints. Keep unspecified dates or counts explicitly uncertain; '
        'never combine different relationship timelines or invent missing source facts. Preserve valid unrelated assets. '
        'The corrected WorldSpec requires a new independent semantic review; no prior grade is inherited.')
    return json.dumps(logical,ensure_ascii=False,sort_keys=True,separators=(',',':'))+suffix


async def revise(session,settings,*,project,book,baseline,candidate,report,workflow_run_id,llm_run_ids):
    from bestseller.services import planner as p
    from bestseller.services.budget import ExecutionBudgetError
    from bestseller.services.progress_context import emit_activity
    authority=deepcopy(p._source_bound_design_snapshot(project));initial=deepcopy(report)
    revision={'schema':SCHEMA,'initial_review':initial,'revision_attempt':1,'canonical_changed':False,'grade_inherited':False}
    context={k:deepcopy(report.get(k)) for k in ('authority_snapshot_hash','canonical_baseline_hash','candidate_hash',
        'llm_run_id','raw_response_hash','feedback','core_conflicts','quality_findings','source_evidence','candidate_evidence')}
    context.update(schema=SCHEMA,rejected_world_spec=deepcopy(p._without_planning_meta(candidate)))
    emit_activity('world_semantic_revision_started',{'source_review_id':report['llm_run_id'],'candidate_hash':report['candidate_hash'],'attempt':1})
    try:
        revised,run_id=await p._complete_source_bound_planning_content(session,settings,project=project,
            premise=(project.metadata_json or {}).get('premise') or authority.get('premise') or '',
            kind='world_spec',projection=deepcopy(dict(baseline)),workflow_run_id=workflow_run_id,
            book_spec=deepcopy(dict(book)),revision_context=context)
        rid=run_id or p._mapping(revised.get('_meta')).get('generation_llm_run_id')
        revision['generation_receipt']={'llm_run_id':str(rid) if rid else None,
            'candidate_hash':p._approved_payload_hash({'world_spec':p._without_planning_meta(revised),'cast_spec':{}})}
        if run_id and llm_run_ids is not None:llm_run_ids.append(UUID(str(run_id)))
        if not await completed_candidate(session,project,revised):
            raise p.PlannerFallbackError('Full World revision lacks a settled exact producer receipt')
        if revision['generation_receipt']['candidate_hash']==initial['candidate_hash']:
            raise p.PlannerFallbackError('Full World revision made no content progress')
        if p._approved_payload_hash(p._source_bound_design_snapshot(project))!=p._approved_payload_hash(authority):
            raise p.PlannerFallbackError('Full World revision authority changed')
        final=await p._review_source_bound_expansion(session,settings,project=project,book_spec=deepcopy(dict(book)),
            world_baseline=deepcopy(dict(baseline)),cast_baseline={},world_derived=revised,cast_derived={},workflow_run_id=workflow_run_id)
        revision['final_review']=deepcopy(final)
        if final and final.get('llm_run_id') and llm_run_ids is not None:llm_run_ids.append(UUID(str(final['llm_run_id'])))
        if (not final or final.get('candidate_hash')!=revision['generation_receipt']['candidate_hash']
            or final.get('authority_snapshot_hash')!=initial['authority_snapshot_hash']
            or final.get('canonical_baseline_hash')!=initial['canonical_baseline_hash']
            or final.get('llm_run_id')==initial['llm_run_id']):
            raise p.PlannerFallbackError('Full World revision review subject changed or old grade reused')
        updated={**final,'world_revision':revision}
        emit_activity('world_semantic_revision_reviewed',{'source_review_id':initial['llm_run_id'],
            'review_id':final.get('llm_run_id'),'draft_eligible':p._source_bound_report_is_draft_eligible(updated)})
        return revised,updated
    except Exception as exc:
        revision['failure']={'error_type':type(exc).__name__,'message':str(exc)}
        updated={**initial,'world_revision':revision}
        p._store_source_bound_review(project,updated)
        if isinstance(exc,ExecutionBudgetError):raise
        return candidate,updated
