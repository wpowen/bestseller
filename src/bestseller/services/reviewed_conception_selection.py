"""Select the exact quality-reviewed initial source before any planning call."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db.models import (ProjectModel,PlanningArtifactVersionModel,LlmRunModel,
    ExecutionOperationModel,ExecutionAttemptModel,ChapterModel,WorkflowRunModel)
from bestseller.services.conception_snapshots import snapshot_hash,promote_conception_snapshot


def assert_snapshot_quality(content):
    from bestseller.services.conception import _final_packaging_audit_matches_text,_project_final_canonical_body
    from bestseller.services.conception_approval_bridge import _contract_complete
    contract=content.get('concept_contract') or {}
    if not _contract_complete(content,target_chapters=contract.get('target_chapters')):
        raise ValueError('reviewed_selection_contract_incomplete')
    promotion=((content.get('conception_artifacts') or {}).get('story_appeal') or {}).get('final_packaging_promotion') or {}
    report=promotion.get('seed_fidelity') or {}
    if (promotion.get('draft_eligible') is not True or report.get('passed') is not True
        or report.get('hard_conflicts') or report.get('evaluator_error')
        or report.get('normalization_version')!='final-packaging-semantic.v2'
        or not report.get('review_llm_run_ids')
        or not _final_packaging_audit_matches_text(report,title=content.get('title'),synopsis=content.get('synopsis'))):
        raise ValueError('reviewed_selection_current_quality_required')
    body=_project_final_canonical_body(premise=content.get('premise',''),writing_profile=content.get('writing_profile') or {},
        story_spine=content.get('story_spine') or {},story_architecture=contract.get('story_architecture') or {},
        world_model=content.get('world_model') or {})
    if snapshot_hash(body)!=report.get('canonical_body_sha256'):
        raise ValueError('reviewed_selection_story_version_changed')
    return report


async def select_reviewed_conception_snapshot(session,settings,*,project_id,snapshot_id,expected_hash,actor,reason):
    from bestseller.services.conception import _normalize_final_packaging_semantic_report
    if not actor.strip() or not reason.strip():raise ValueError('reviewed_selection_audit_required')
    project=await session.get(ProjectModel,project_id,with_for_update=True,populate_existing=True)
    if project is None:raise ValueError('reviewed_selection_project_missing')
    snapshot=await session.get(PlanningArtifactVersionModel,snapshot_id,with_for_update=True)
    metadata=deepcopy(project.metadata_json or {})
    if (snapshot is None or snapshot.project_id!=project_id or snapshot.artifact_type!='conception_snapshot'
        or snapshot_hash(snapshot.content)!=expected_hash
        or metadata.get('conception_snapshot_id')!=str(snapshot_id)):
        raise ValueError('reviewed_selection_stale_snapshot')
    prior=metadata.get('reviewed_conception_selection') or {}
    if snapshot.status=='canonical' and prior.get('snapshot_id')==str(snapshot_id):
        if metadata.get('creation_canonical_snapshot_hash')!=snapshot_hash(snapshot.content):
            raise ValueError('reviewed_selection_canonical_changed')
        return prior
    if snapshot.status!='validated':raise ValueError('reviewed_selection_validated_snapshot_required')
    if await session.scalar(select(ChapterModel.id).where(ChapterModel.project_id==project_id).limit(1)):
        raise ValueError('reviewed_selection_empty_book_required')
    if await session.scalar(select(WorkflowRunModel.id).where(
        WorkflowRunModel.project_id==project_id,WorkflowRunModel.status.in_(['running','pending','queued']),
        WorkflowRunModel.id!=snapshot.source_run_id).limit(1)):
        raise ValueError('reviewed_selection_active_workflow')
    content=snapshot.content;report=assert_snapshot_quality(content)
    if int(content['concept_contract']['target_chapters'])!=project.target_chapters:
        raise ValueError('reviewed_selection_target_changed')
    intent=await session.get(PlanningArtifactVersionModel,UUID(metadata.get('creation_intent_snapshot_id','')))
    if (intent is None or intent.project_id!=project_id or intent.artifact_type!='creation_intent'
        or intent.status!='validated' or snapshot_hash(intent.content)!=content.get('creation_intent_hash')
        or snapshot_hash(intent.content)!=metadata.get('creation_intent_contract_hash')):
        raise ValueError('reviewed_selection_intent_changed')
    # The terminal semantic verdict must be a real complete provider receipt
    # reviewing this exact title/blurb. Historic scores cannot be attached to a
    # new source by changing a status flag or copying a normalized passed bit.
    candidates=[]
    for raw_id in report['review_llm_run_ids']:
        run=await session.get(LlmRunModel,UUID(raw_id))
        if run is not None and run.logical_role=='critic' and str(run.prompt_template or '').startswith(('conception_final_packaging_seed_fidelity_judge','conception_final_packaging_seed_fidelity_arbitration')):
            candidates.append(run)
    if not candidates:raise ValueError('reviewed_selection_paid_evidence_missing')
    run=candidates[-1];opkey=(run.metadata_json or {}).get('execution_receipt',{}).get('operation_key')
    op=await session.get(ExecutionOperationModel,opkey) if opkey else None
    result=(op.result_json or {}) if op is not None else {}
    if (op is None or op.status!='completed' or run.finish_reason!='stop'
        or result.get('finish_reason')!='stop' or result.get('model_name')!=run.model_name
        or result.get('input_tokens')!=run.input_tokens or result.get('output_tokens')!=run.output_tokens):
        raise ValueError('reviewed_selection_paid_evidence_missing')
    paid = await session.scalar(select(ExecutionAttemptModel).where(
        ExecutionAttemptModel.operation_key==op.key,ExecutionAttemptModel.status=='completed',
        ExecutionAttemptModel.actual_tokens==run.input_tokens+run.output_tokens))
    if paid is None:raise ValueError('reviewed_selection_settled_attempt_required')
    raw=str(result.get('content') or '').strip()
    if raw.startswith('```'):raw=raw[raw.find('\n')+1:raw.rfind('```')].strip()
    raw_report=json.loads(raw)
    if not isinstance(raw_report,dict) or type(raw_report.get('passed')) is not bool or not isinstance(raw_report.get('hard_conflicts'),list):
        raise ValueError('reviewed_selection_paid_verdict_invalid')
    normalized=_normalize_final_packaging_semantic_report(raw_report)
    if not normalized['passed'] or normalized.get('evaluator_error'):
        raise ValueError('reviewed_selection_paid_verdict_rejected')
    directory=Path(settings.artifact_store.local_dir).resolve();path=(directory/str(run.request_payload_ref or '')).resolve()
    if not run.request_payload_ref or not path.is_relative_to(directory) or not path.is_file():
        raise ValueError('reviewed_selection_request_evidence_missing')
    request=json.loads(path.read_text());messages=request.get('messages') or {};user=messages.get('user') or {}
    text=user.get('text','')
    if (messages.get('source')!='system_user' or user.get('truncated')
        or f"最终书名：{content['title']}\n" not in text
        or not any(f"{label}：{content['synopsis']}" in text for label in ('最终上架简介','待审上架简介'))):
        raise ValueError('reviewed_selection_review_subject_changed')
    from bestseller.services.conception import _project_final_canonical_body,_render_seed_fidelity_story_assets,_CANONICAL_PACKAGING_BODY_BUDGETS
    reviewed_body=_project_final_canonical_body(premise=content['premise'],writing_profile=content.get('writing_profile') or {},
        story_spine=content.get('story_spine') or {},story_architecture=content['concept_contract'].get('story_architecture') or {},
        world_model=content.get('world_model') or {})
    if _render_seed_fidelity_story_assets(reviewed_body,budgets=_CANONICAL_PACKAGING_BODY_BUDGETS) not in text:
        raise ValueError('reviewed_selection_review_body_changed')
    await promote_conception_snapshot(session,project_id,snapshot_id,expected_lock_version=project.lock_version,actor=actor,reason=reason)
    metadata=deepcopy(project.metadata_json or {})
    for key in ('premise','synopsis','tags','writing_profile','story_spine','world_model','concept_contract',
                'hook_spec','commercial_brief','conception_artifacts'):
        if key in content:metadata[key]=deepcopy(content[key])
    metadata.update(conception_approved=True,planning_status='conception_approved',conception_snapshot_status='canonical',
        concept_contract_version='2',hook_card=deepcopy(content['concept_contract']['hook_card']),
        seriality_proof=deepcopy(content['concept_contract']['seriality_proof']))
    receipt={'schema':'reviewed-conception-selection.v1','snapshot_id':str(snapshot_id),
        'snapshot_hash':snapshot_hash(snapshot.content),'quality_run_id':str(run.id),'quality_operation':op.key,
        'actor':actor,'reason':reason,'grade_reused_for_changed_content':False}
    metadata['reviewed_conception_selection']=receipt
    project.title=content['title'];project.metadata_json=metadata
    from bestseller.services.book_design import ensure_project_book_design_snapshot
    ensure_project_book_design_snapshot(project,force_rebuild=True)
    project.metadata_json={**project.metadata_json,'book_design_adoption_source':{
        'kind':'adopted_conception','snapshot_id':str(snapshot_id),'content_hash':snapshot_hash(snapshot.content)}}
    await session.flush()
    return receipt
