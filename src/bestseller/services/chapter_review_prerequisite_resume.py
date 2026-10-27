"""Explicit prerequisite recovery reopens the ORIGINAL waiting logical slot.

No new cycle, round, grade, credit or commit. Original waiting reports/history
remain present. The unchanged current body and signed current dispatch authority
are revalidated before a lifecycle transition.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db.models import ChapterModel
from bestseller.services.operator_action_approval import require
from bestseller.services.prompt_source_policy import content_hash


def original_waiting_attempt(cycle, *, proof, authority_receipt_id):
    require(proof.get('reuse_kind')=='waiting_infrastructure'
        and proof.get('cycle_id')==cycle.get('cycle_id')
        and proof.get('draft_id')==cycle.get('source_draft_id')
        and proof.get('body_sha256')==cycle.get('source_body_hash'), 'prerequisite_resume_binding')
    require(cycle.get('state')=='waiting_infrastructure','prerequisite_resume_state')
    attempts=cycle.get('attempts')
    require(isinstance(attempts,list) and bool(attempts) and len(attempts)<=2,'prerequisite_resume_attempts')
    entry=attempts[-1]
    require(entry.get('state')=='waiting_infrastructure' and entry.get('attempt')==len(attempts)
        and entry.get('operation_namespace')==f"chapter-critic:{cycle['cycle_id']}:{len(attempts)}"
        and bool(entry.get('infrastructure_history'))
        and not any(k in entry for k in ('outcome','review_result','review_report_id','quality_score_id','rewrite_task_id')),
        'prerequisite_resume_entry')
    out=deepcopy(cycle);out['state']='prepared';out['attempts'][-1]['state']='prepared'
    out['attempts'][-1]['prerequisite_resume_history']=[*entry.get('prerequisite_resume_history',[]),{
        'authority_receipt_id':str(authority_receipt_id), 'prior_waiting_history_sha256':content_hash(entry['infrastructure_history']),
        'same_operation_namespace':entry['operation_namespace'],'new_attempt_credit':False,
        'control_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}]
    return out


async def resume_original_waiting_prerequisites(session,settings,*,chapter_id,cycle_id,authority_receipt_id,target):
    from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing
    from bestseller.services import exact_unknown_business as exact,pipelines
    chapter=await session.scalar(select(ChapterModel).where(ChapterModel.id==chapter_id).with_for_update())
    proof=await validate_retained_review_before_processing(session,settings,chapter)
    authority=await session.get(pipelines.WorkflowRunModel,UUID(str(authority_receipt_id)))
    require(authority is not None and authority.workflow_type=='exact_unknown_new_business','prerequisite_resume_authority')
    await exact.inspect_exact_unknown_business(session,ambiguous_roots=authority.metadata_json['plan']['baseline']['root_keys'],**target)
    verified=session.info.get('exact_unknown_business_verified') or {}
    require(verified.get('receipt_id')==str(authority_receipt_id),'prerequisite_resume_current_authority')
    cycles=chapter.metadata_json.get('chapter_critic_cycles') or {};cycle=cycles.get(cycle_id)
    require(isinstance(cycle,dict),'prerequisite_resume_cycle')
    changed=original_waiting_attempt(cycle,proof=proof,authority_receipt_id=authority_receipt_id)
    pipelines._store_chapter_critic_cycle(chapter,changed)
    await session.flush()
    return {'cycle_id':cycle_id,'attempt':len(changed['attempts']),
        'operation_namespace':changed['attempts'][-1]['operation_namespace'],
        'old_waiting_history_retained':True,'new_attempt_credit':False,'model_calls':0}
