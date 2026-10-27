"""Trusted operator selection of a working repair source, never eligibility/adoption.

Automatic best-draft selection keeps its strict completed-review rules. This
separate, explicit operator entry preserves all rejected/unknown evidence and
all repair counters. It grants no new allowance; the caller must use an existing remaining local repair slot.
"""
from copy import deepcopy
from uuid import UUID,NAMESPACE_URL,uuid5
from sqlalchemy import select
from bestseller.infra.db.models import ChapterDraftVersionModel,ChapterModel,SceneCardModel,WorkflowRunModel
from bestseller.services.prompt_source_policy import content_hash


def need(ok,reason):
    if not ok:raise ValueError('inspected_repair_source_'+reason)


async def select_inspected_repair_source(session,settings,*,project,chapter,
        expected_current_id,expected_current_body_hash,source_id,source_body_hash,
        inspection,actor,authorization_ref,explicit_user_approval=False):
    from bestseller.services.drafts import authoritative_word_count_for_language
    from bestseller.services.chapter_outline_readiness import verify_outline_prefix_readiness
    from bestseller.services.operator_action_approval import record_operator_action_approval
    from bestseller.services.word_targets import chapter_rewrite_length_band
    need(explicit_user_approval is True,'human_approval_required')
    need(isinstance(inspection,dict) and inspection.get('repair_scope')==['pov','source_history_scope']
         and inspection.get('new_length_expansion') is False and inspection.get('grade_asserted') is False,'inspection_scope')
    await session.execute(select(ChapterModel.id).where(ChapterModel.id==chapter.id).with_for_update())
    current=await session.scalar(select(ChapterDraftVersionModel).where(ChapterDraftVersionModel.chapter_id==chapter.id,
        ChapterDraftVersionModel.is_current.is_(True)).with_for_update())
    source=await session.get(ChapterDraftVersionModel,source_id,populate_existing=True,with_for_update=True)
    if current is not None and current.id == source_id and content_hash(current.content_md) == source_body_hash:
        matches=[entry for entry in (chapter.metadata_json or {}).get('deterministic_audit_inspected_repair_source_selection',[])
            if entry['binding'].get('previous_draft_id')==str(expected_current_id)
            and entry['binding'].get('previous_body_hash')==expected_current_body_hash
            and entry['binding'].get('source_draft_id')==str(source_id)
            and entry['binding'].get('source_body_hash')==source_body_hash
            and entry['binding'].get('inspection')==inspection]
        need(len(matches)==1,'already_selected_receipt_missing')
        row=await session.get(WorkflowRunModel,UUID(matches[0]['workflow_id']))
        need(row is not None and row.status=='completed' and row.metadata_json.get('binding')==matches[0]['binding'],'already_selected_receipt_changed')
        from bestseller.services.operator_action_approval import require_operator_action_approval
        await require_operator_action_approval(session,approval_id=UUID(row.metadata_json['approval_id']),
            purpose='working_repair_source_selection',binding=row.metadata_json['binding'],capability='select_for_existing_local_repair')
        return {'workflow_id':str(row.id),'approval_id':row.metadata_json['approval_id'],'binding':deepcopy(row.metadata_json['binding']),
            'already_selected':True,'current_local_used':int(chapter.metadata_json.get('chapter_first_local_repair_count') or 0)}
    need(current is not None and current.id==expected_current_id and content_hash(current.content_md)==expected_current_body_hash,'current_changed')
    need(source is not None and source.project_id==project.id and source.chapter_id==chapter.id
         and source.promotion_state=='candidate' and content_hash(source.content_md)==source_body_hash,'source_changed')
    promoted=await session.scalar(select(ChapterDraftVersionModel.id).where(ChapterDraftVersionModel.chapter_id==chapter.id,
        ChapterDraftVersionModel.promotion_state=='promoted').limit(1))
    need(promoted is None,'adopted_chapter_protected')
    meta=deepcopy(chapter.metadata_json or {})
    local_used=int(meta.get('chapter_first_local_repair_count') or 0)
    local_cap=int(project.metadata_json.get('chapter_first_local_repair_max_attempts') or 2)
    total_used=int(meta.get('auto_repair_total_attempts') or 0)
    total_cap=int(settings.pipeline.chapter_auto_repair_total_max_attempts or 0)
    need(local_used<local_cap and (total_cap<=0 or total_used<total_cap),'existing_local_budget_exhausted')
    band=chapter_rewrite_length_band(settings,chapter.target_word_count,language=project.language,
        direction='normal',role='editor',project=project)
    words=authoritative_word_count_for_language(source.content_md,language=project.language)
    need(band.hard_min<=words<=band.hard_max,'not_a_length_qualified_source')
    marker=meta.get('outline_prefix_readiness') or {}
    need(bool(marker.get('artifact_id')),'approved_source_marker_required')
    scenes=list(await session.scalars(select(SceneCardModel).where(SceneCardModel.chapter_id==chapter.id).order_by(SceneCardModel.scene_number)))
    readiness=await verify_outline_prefix_readiness(session,project=project,artifact_id=UUID(marker['artifact_id']),chapter=chapter,scenes=scenes,settings=settings)
    binding={'project_id':str(project.id),'chapter_id':str(chapter.id),'previous_draft_id':str(current.id),
        'previous_body_hash':expected_current_body_hash,'source_draft_id':str(source.id),'source_body_hash':source_body_hash,
        'inspection':deepcopy(inspection),'source_readiness_hash':content_hash(readiness),
        'budget_snapshot':{'local_used':local_used,'local_cap':local_cap,'total_used':total_used,'total_cap':total_cap},
        'selection_only':True,'adoption_authorized':False,'grade_asserted':False,'attempts_reset':False}
    identity=uuid5(NAMESPACE_URL,'bestseller:inspected-repair-source:'+content_hash(binding))
    binding['target_id']=str(identity)
    approval=await record_operator_action_approval(session,purpose='working_repair_source_selection',binding=binding,
        capabilities=['select_for_existing_local_repair'],actor=actor,authorization_ref=authorization_ref,explicit_user_approval=True)
    row=WorkflowRunModel(id=identity,project_id=project.id,workflow_type='working_repair_source_selection',
        scope_type='chapter',scope_id=chapter.id,status='completed',current_step='working_source_only',requested_by=actor,
        metadata_json={'binding':binding,'approval_id':str(approval)})
    session.add(row)
    # Reuse the established body supersession path, with the true operator
    # origin rather than claiming an AI-style transformation.
    from bestseller.services.pipelines import _select_deslop_candidate
    await _select_deslop_candidate(session,chapter=chapter,candidate=source,language=project.language,
        selected_reason='inspected_existing_local_repair_source',selection_origin='inspected_repair_source')
    meta=deepcopy(chapter.metadata_json or {})
    history=deepcopy(meta.get('deterministic_audit_inspected_repair_source_selection') or [])
    history.append({'workflow_id':str(identity),'binding':binding})
    chapter.metadata_json={**meta,'deterministic_audit_inspected_repair_source_selection':history}
    await session.flush()
    return {'workflow_id':str(identity),'approval_id':str(approval),'binding':binding}
