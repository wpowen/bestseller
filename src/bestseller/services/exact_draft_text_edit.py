"""Explicit source-bound text edits on an unadopted draft; no model or grade.

Trusted callers supply exact edit data and human authorization. This is not
an automatic detector/repair rule, and never mutates a provider response.
"""
from copy import deepcopy
import hashlib
from uuid import UUID,uuid5,NAMESPACE_URL
from sqlalchemy import select,func
from bestseller.infra.db.models import ChapterModel,ChapterDraftVersionModel,WorkflowRunModel
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.operator_action_approval import require_operator_action_approval


def _need(ok,code):
    if not ok:raise ValueError('exact_draft_edit_'+code)


async def prepare_exact_draft_text_edit(session,*,project_id,chapter_id,draft_id,body_sha256,edits):
    chapter=await session.get(ChapterModel,chapter_id)
    draft=await session.get(ChapterDraftVersionModel,draft_id)
    _need(chapter is not None and chapter.project_id==project_id and draft is not None
          and draft.project_id==project_id and draft.chapter_id==chapter_id,'scope')
    _need(draft.is_current and draft.promotion_state=='candidate','current_unadopted_required')
    source=draft.content_md
    _need(hashlib.sha256(source.encode()).hexdigest()==body_sha256,'body_changed')
    _need(isinstance(edits,list) and bool(edits),'edits_required')
    result=source;spans=[]
    for edit in edits:
        _need(isinstance(edit,dict) and set(edit)=={'before','after'},'edit_shape')
        before,after=edit['before'],edit['after']
        _need(isinstance(before,str) and before and isinstance(after,str) and before!=after,'edit_text')
        _need(source.count(before)==1,'source_occurrence_not_unique')
        a=source.index(before);b=a+len(before)
        _need(not any(a<y and x<b for x,y in spans),'overlapping_edits')
        spans.append((a,b))
    for (a,b),edit in sorted(zip(spans,edits),reverse=True):result=result[:a]+edit['after']+result[b:]
    _need(bool(result.strip()),'empty_result')
    plan={'schema':'exact-draft-text-edit.v1','project_id':str(project_id),'chapter_id':str(chapter_id),
        'source_draft_id':str(draft_id),'source_body_sha256':body_sha256,'edits':deepcopy(edits),
        'edit_spans':[{'start':a,'end':b,'offset_unit':'unicode_codepoints',
            'before_sha256':hashlib.sha256(edit['before'].encode()).hexdigest(),
            'after_sha256':hashlib.sha256(edit['after'].encode()).hexdigest()}
            for (a,b),edit in zip(spans,edits)],
        'result_body_sha256':hashlib.sha256(result.encode()).hexdigest(),'result_content_md':result,
        'model_calls':0,'grade_asserted':False,'adoption_authorized':False}
    plan['plan_sha256']=content_hash(plan)
    plan['target_id']=str(uuid5(NAMESPACE_URL,'bestseller:exact-draft-text-edit:'+plan['plan_sha256']))
    return plan


async def apply_exact_draft_text_edit(session,*,plan,approval_id):
    identity=UUID(plan['target_id'])
    prior=await session.get(WorkflowRunModel,identity)
    if prior is not None:
        _need(prior.status=='completed' and prior.metadata_json['plan']==plan,'receipt_conflict')
        await require_operator_action_approval(session,approval_id=prior.metadata_json['approval_id'],
            purpose='explicit_draft_text_edit',binding=plan,capability='apply_exact_text_edits')
        return prior.metadata_json['result']
    chapter=await session.scalar(select(ChapterModel).where(ChapterModel.id==UUID(plan['chapter_id'])).with_for_update())
    fresh=await prepare_exact_draft_text_edit(session,project_id=UUID(plan['project_id']),chapter_id=chapter.id,
        draft_id=UUID(plan['source_draft_id']),body_sha256=plan['source_body_sha256'],edits=plan['edits'])
    _need(fresh==plan,'plan_changed')
    await require_operator_action_approval(session,approval_id=approval_id,purpose='explicit_draft_text_edit',binding=plan,capability='apply_exact_text_edits')
    source=await session.get(ChapterDraftVersionModel,UUID(plan['source_draft_id']))
    version=int(await session.scalar(select(func.max(ChapterDraftVersionModel.version_no)).where(ChapterDraftVersionModel.chapter_id==chapter.id)) or 0)+1
    from bestseller.services.drafts import authoritative_word_count_for_language
    from bestseller.infra.db.models import ProjectModel
    project=await session.get(ProjectModel,source.project_id)
    candidate=ChapterDraftVersionModel(project_id=source.project_id,chapter_id=chapter.id,version_no=version,
        content_md=plan['result_content_md'],word_count=authoritative_word_count_for_language(plan['result_content_md'],language=project.language),
        assembled_from_scene_draft_ids=deepcopy(source.assembled_from_scene_draft_ids),is_current=False,promotion_state='candidate',
        llm_run_id=None,promotion_metadata={'origin':'operator_exact_text_edit','parent_draft_id':str(source.id),
            'parent_version_no':source.version_no,'source_body_sha256':plan['source_body_sha256'],
            'source_llm_run_id':str(source.llm_run_id) if source.llm_run_id else None,'text_edit_plan_sha256':plan['plan_sha256'],
            'operator_approval_id':str(approval_id),'model_generated_this_version':False})
    session.add(candidate);await session.flush()
    from bestseller.services.pipelines import _select_deslop_candidate
    await _select_deslop_candidate(session,chapter=chapter,candidate=candidate,selected_reason='explicit_source_bound_text_edit',selection_origin='operator_exact_text_edit')
    chapter.production_state='pending'
    result={'candidate_id':str(candidate.id),'version':version,'body_sha256':plan['result_body_sha256'],
        'source_draft_id':str(source.id),'new_model_calls':0,'grade_asserted':False,'adopted':False}
    session.add(WorkflowRunModel(id=identity,project_id=source.project_id,workflow_type='explicit_draft_text_edit',scope_type='chapter',scope_id=chapter.id,
        status='completed',current_step='candidate_saved',requested_by='authorized-assistant-editor',
        metadata_json={'plan':deepcopy(plan),'approval_id':str(approval_id),'result':result}))
    await session.flush();return result
