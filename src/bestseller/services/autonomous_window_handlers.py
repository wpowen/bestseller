"""Normal production adapters for bounded continuation stages.

All planning, approval, materialization, chapter adoption/knowledge and export
remain owned by their existing APIs. No draft body or grade is synthesized here.
"""
from copy import deepcopy
from pathlib import Path
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db import models as m


def planning_review_source(project, artifact, *, workflow_id, volume_number):
 """Recover the exact reviewed candidate before framework artifact enrichment.

 The artifact and checkpoint must have identical complete story bodies. Keep
 the original candidate, verdict and hash; a new prefix still needs its own
 hard admission. Producer-added _meta is retained in the artifact/audit only.
 """
 from bestseller.services import chapter_outline_readiness as ready
 from bestseller.services.outline_recovery_checkpoint import matching_checkpoint
 from bestseller.services.prompt_source_policy import content_hash
 if artifact.project_id!=project.id:raise ValueError('continuation_planner_artifact_scope')
 body=ready._body(artifact.content);matches={}
 for bucket in (project.metadata_json.get('outline_commercial_judge_checkpoints') or {}).values():
  for key in ('latest_candidate','best','active_repair_branch'):
   record=bucket.get(key) or {}
   if (record.get('workflow_run_id')==str(workflow_id)
       and matching_checkpoint(record,project.metadata_json,volume_number)
       and ready._body(record['candidate'])==body):
    matches[content_hash(record)]=record
 if len(matches)!=1:raise ValueError('continuation_planner_review_source_missing_or_ambiguous')
 record=next(iter(matches.values()))
 return deepcopy(record['candidate']),{'schema':'planner-reviewed-source-handoff.v1',
  'planning_workflow_id':str(workflow_id),'artifact_id':str(artifact.id),
  'artifact_sha256':content_hash(artifact.content),'candidate_sha256':record['candidate_hash'],
  'story_body_sha256':content_hash(body),'original_report_sha256':content_hash(record['report']),
  'original_verdict_changed':False,'write_authorized':False}


def handlers(settings,*,progress=None,closure=None):
 async def plan(session,project,row,outputs):
  from bestseller.services import chapter_outline_readiness as ready
  proposal=row.metadata_json['binding']['next_window_proposal'];start=proposal['start']
  metadata=project.metadata_json or {};rolling=metadata.get('rolling_outline_plan') or {}
  candidates=list(await session.scalars(select(m.WorkflowRunModel).where(
   m.WorkflowRunModel.project_id==project.id,m.WorkflowRunModel.workflow_type==ready.WORKFLOW_TYPE,
   m.WorkflowRunModel.status=='completed').order_by(m.WorkflowRunModel.created_at.desc())))
  for candidate in candidates:
   state=candidate.metadata_json or {};source=state.get('source_payload')
   if not isinstance(source,dict) or state.get('phase')!='completed':continue
   window=ready.outline_window(source)
   if window and window[0]<=start<=window[1] and [window[0],window[1]]==[rolling.get('window_start'),rolling.get('window_end')]:
    return {'source_payload':deepcopy(source),'from_chapter':start,'through_chapter':min(proposal['end'],window[1]),'parent_workflow_id':str(candidate.id)}
  from bestseller.services.foundation_selection import load_selected_foundation_artifacts
  from bestseller.services.rolling_outline import load_rolling_outline_plan,build_rolling_outline_plan,promote_rolling_outline
  from bestseller.services.planner import generate_volume_plan
  from bestseller.services.pipelines import _volume_plan_for_rolling_window
  assets=await load_selected_foundation_artifacts(session,project=project,settings=settings)
  contents={key:deepcopy(value.content) for key,value in assets.items()}
  schedule=metadata.get('rolling_outline_windows') or []
  window=next((w for w in schedule if w['window_start']<=start<=w['window_end']),None)
  if window is None:raise ValueError('continuation_normal_rolling_schedule_missing')
  snapshot_hash=metadata.get('book_design_snapshot_hash')
  macro,_=load_rolling_outline_plan(metadata.get('macro_outline_plan') or {},rolling,source_snapshot_hash=snapshot_hash,require_approved=False)
  if [rolling.get('window_start'),rolling.get('window_end')]!=[window['window_start'],window['window_end']]:
   # Only a proven completed prior window can authorize this new planned view.
   # Existing Canon and confirmed chapters are never rewritten by the view.
   if start!=window['window_start']:raise ValueError('continuation_rolling_frontier_mismatch')
   if row.metadata_json['binding'].get('source_lane')=='pending_reference':
    from bestseller.services.planning_continuity_context import build_authorized_planning_context
    prior_sources=await build_authorized_planning_context(session,project_id=project.id,before_chapter=start,project=project)
   else:
    prior=await session.get(m.WorkflowRunModel,UUID(row.metadata_json['binding']['source_workflow_id']))
    prior_sources=prior.metadata_json['execution_window_receipt']['sources']
   fresh=build_rolling_outline_plan(macro,current_state_snapshot={'current_chapter':start-1,'adopted_sources':deepcopy(prior_sources)},next_macro_anchor=macro.slots[window['window_end']].to_dict() if window['window_end']<macro.total_chapters else 'book_complete',source_snapshot_hash=snapshot_hash,window_start=start,window_end=window['window_end'],window_size=window['window_end']-start+1,confirmed_chapters=tuple(range(1,start)))
   project.metadata_json={**metadata,'rolling_outline_plan':promote_rolling_outline(fresh,'planned').to_dict()}
   await session.commit()
  canonical=contents['volume_plan'];base=next(v for v in canonical if v['volume_number']==window['volume_number'])
  entry={**base,'rolling_window_index':window['window_index'],'start_chapter_number':window['window_start'],'end_chapter_number':window['window_end'],'chapter_count_target':window['window_end']-window['window_start']+1,'chapter_range':[window['window_start'],window['window_end']]}
  result=await generate_volume_plan(session,settings,project.slug,window['volume_number'],book_spec=contents['book_spec'],world_spec=contents['world_spec'],cast_spec=contents['cast_spec'],volume_plan=_volume_plan_for_rolling_window(canonical,entry),requested_by='autonomous-normal-window',progress=progress)
  await session.commit()
  ids=[a.artifact_id for a in result.artifacts if str(a.artifact_type)=='chapter_outline_batch']
  if not ids:raise ValueError('continuation_normal_outline_artifact_missing')
  artifact=await session.get(m.PlanningArtifactVersionModel,ids[-1])
  source,source_receipt=planning_review_source(project,artifact,workflow_id=result.workflow_run_id,volume_number=window['volume_number'])
  return {'source_payload':deepcopy(source),'from_chapter':start,'through_chapter':min(proposal['end'],window['window_end']),'planning_workflow_id':str(result.workflow_run_id),'planning_source_receipt':source_receipt}

 async def admit(session,project,row,outputs):
  from bestseller.services.chapter_outline_readiness import approve_chapter_outline_prefix
  p=outputs['plan'];source=p['source_payload']
  if p.get('parent_workflow_id'):
   from bestseller.services.prefix_source_proposal import register_prefix_source_proposal
   await register_prefix_source_proposal(session,project=project,parent_workflow_id=UUID(p['parent_workflow_id']),source_payload=source,from_chapter=p['from_chapter'],through_chapter=p['through_chapter'],actor='normal-autonomous-book-run',authorization_ref='explicit whole-book run checkpoint '+str(row.id),explicit_user_approval=True)
   await session.commit()
  result=await approve_chapter_outline_prefix(session,settings,project=project,source_payload=source,from_chapter=p['from_chapter'],through_chapter=p['through_chapter'],requested_by='normal-autonomous-book-run',max_repair_rounds=2,allow_editorial_debt=True)
  await session.commit()
  if result.get('draft_eligible') is not True:return {**result,'blocked_reason':'continuation_prefix_not_admitted'}
  return result

 async def materialize(session,project,row,outputs):
  from bestseller.domain.workflow import ChapterOutlineBatchInput
  from bestseller.services.workflows import materialize_chapter_outline_batch
  admitted=outputs['admit'];p=outputs['plan']
  artifact=await session.get(m.PlanningArtifactVersionModel,UUID(admitted['artifact_id']))
  if admitted.get('draft_eligible') is not True or admitted['chapter_numbers']!=list(range(p['from_chapter'],p['through_chapter']+1)):
   raise ValueError('continuation_unapproved_materialization')
  result=await materialize_chapter_outline_batch(session,project.slug,ChapterOutlineBatchInput.model_validate(artifact.content),source_artifact_id=artifact.id,prune_missing_planned=False)
  await session.commit();return result.model_dump(mode='json')

 async def activate(session,project,row,outputs):
  from bestseller.services.creation_configuration import configuration_view,save_project_configuration
  from bestseller.services.prefix_window_activation import activate_verified_prefix_window
  view=configuration_view(project);end=outputs['plan']['through_chapter']
  result=await save_project_configuration(session,project,{'configuration':view['configuration'],'execution_window':{'schema_version':'execution-window.v1','purpose':'continuation','start':outputs['plan']['from_chapter'],'end':end}},expected_hash=view['configuration_hash'],expected_window_hash=view['execution_window_hash'])
  if result.get('status')!='saved':raise ValueError('continuation_configuration_not_saved')
  receipt=await activate_verified_prefix_window(session,settings,project=project,actor='normal-autonomous-book-run',reason='Previously closed window, normal exact successor prefix proof; no approval of unreviewed tail')
  await session.commit();return receipt

 async def write(session,project,row,outputs):
  from bestseller.services.pipelines import run_project_pipeline
  from bestseller.services.autonomous_continuation import record_window_continuation
  p=outputs['plan']
  result=await run_project_pipeline(session,settings,project.slug,chapter_numbers=set(range(p['from_chapter'],p['through_chapter']+1)),chapter_first=True,stop_on_chapter_failure=True,export_markdown=True,materialize_outline=False,materialize_story_bible=False,materialize_narrative_graph=False,materialize_narrative_tree=False,requested_by='normal-autonomous-book-run',progress=progress)
  await session.commit();value=result.model_dump(mode='json')
  if result.requires_human_review or result.final_verdict!='execution_window_completed':return {**value,'blocked_reason':'continuation_chapter_review_or_knowledge_incomplete'}
  from bestseller.services.creation_configuration import read_window
  next_result={**value,'project_workflow_run_id':str(result.workflow_run_id),'execution_window':read_window(project).model_dump(mode='json'),'whole_book_complete':False}
  checkpoint=await record_window_continuation(session,project_id=project.id,result=next_result,dispatch_authorized=True,requested_model=row.metadata_json['binding'].get('requested_model'),batch_size=row.metadata_json['binding']['continuation_batch_size'])
  await session.commit();return {**value,**(checkpoint or {})}

 async def review_and_export(session,project,row,outputs):
  if closure is None:raise ValueError('continuation_whole_book_closure_handler_missing')
  result=await closure(project.slug,str(row.id))
  if not isinstance(result,dict) or result.get('requires_human_review') or result.get('passed') is not True:
   return {'blocked_reason':'whole_book_review_not_verified','review_result':result}
  from bestseller.services.exports import export_project_markdown
  artifact,path=await export_project_markdown(session,settings,project.slug,created_by_run_id=row.id)
  await session.commit()
  if not Path(path).is_file():raise ValueError('continuation_final_export_missing')
  return {'whole_book_complete':True,'export_artifact_id':str(artifact.id),'output_path':str(path),'review_result':result}

 return dict(plan=plan,admit=admit,materialize=materialize,activate=activate,write=write,review_and_export=review_and_export)
