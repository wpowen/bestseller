"""Bounded durable next-action execution, used by the normal ARQ worker.

Unknown/denied actions stop; retries are never manufactured here. Completed
stages survive restarts, and one deterministic queue ID owns one checkpoint.
"""
from copy import deepcopy
from uuid import UUID,uuid4
from sqlalchemy import select,update
from bestseller.infra.db.models import ProjectModel,WorkflowRunModel
from bestseller.services.autonomous_continuation import SCHEMA
from bestseller.services.prompt_source_policy import content_hash

STAGES=('plan','admit','materialize','activate','write')


async def enqueue_checkpoint(session,redis,checkpoint_id):
 row=await session.get(WorkflowRunModel,UUID(str(checkpoint_id)),populate_existing=True,with_for_update=True)
 if row is None or row.workflow_type!='autonomous_continuation':raise ValueError('continuation_checkpoint_missing')
 meta=deepcopy(row.metadata_json or {})
 if meta.get('schema')=='source-bound-book-run.v1':
  from bestseller.services.book_run import enqueue
  from bestseller.settings import apply_runtime_llm_profile,get_settings
  return await enqueue(session,redis,row,settings=apply_runtime_llm_profile(get_settings()))
 if meta.get('schema')=='pending-manuscript-continuation.v1':
  from bestseller.services.pending_manuscript_continuation import enqueue
  return await enqueue(session,redis,row)
 if meta.get('schema')!=SCHEMA:raise ValueError('continuation_checkpoint_schema')
 if meta.get('provider_dispatch_authorized') is not True:
  row.status='machine_blocked';row.current_step='external_authorization_required'
  row.metadata_json={**meta,'blocked_reason':'external_authorization_required'}
  await session.commit();return {'dispatched':False,'blocked_reason':'external_authorization_required'}
 if row.status in {'completed','machine_blocked','failed','cancelled'}:return {'dispatched':False,'state':row.status}
 project=await session.get(ProjectModel,row.project_id)
 if project is None:raise ValueError('continuation_project_missing')
 exists=getattr(redis,'exists',None)
 if meta.get('queued_job_id'):
  job=meta['queued_job_id']
  if not callable(exists) or await exists('arq:job:'+job,'arq:in-progress:'+job,'arq:result:'+job,'bestseller:pipeline-live:'+project.slug):
   return {'dispatched':False,'job_id':job,'reused':True}
 # A clean restart after committed stage progress gets a different job ID;
 # replaying the same checkpoint/cursor still deduplicates to the same job.
 cursor=content_hash(meta.get('stage_outputs') or {})[:16]
 job_id=str(row.id)+':'+cursor
 # ARQ deduplicates this same ID even if the process exits between enqueue and
 # the following DB commit. The persisted checkpoint is the durable outbox.
 await redis.enqueue_job('run_autonomous_continuation_task',workflow_run_id=job_id,
                         payload={'checkpoint_id':str(row.id),'project_slug':project.slug,'resume_after_restart':bool(meta.get('execution_owner'))},_job_id=job_id)
 hset=getattr(redis,'hset',None)
 if callable(hset):
  await hset('task:'+job_id+':meta',mapping={'task_name':'run_autonomous_continuation_task','project_slug':project.slug,'checkpoint_id':str(row.id)})
  expire=getattr(redis,'expire',None)
  if callable(expire):await expire('task:'+job_id+':meta',7*86400)
 row.metadata_json={**meta,'queued_job_id':job_id,'dispatch_count':1}
 await session.commit();return {'dispatched':True,'job_id':job_id}


async def recover_pending_checkpoints(session,redis):
 rows=list(await session.scalars(select(WorkflowRunModel).where(
  WorkflowRunModel.workflow_type=='autonomous_continuation',WorkflowRunModel.status.in_(['pending','running','awaiting_authority','awaiting_reference']))))
 results=[]
 for row in rows:
  project=await session.get(ProjectModel,row.project_id)
  if project is None or project.status=='archived' or (project.metadata_json or {}).get('library_archived'):
   results.append({'dispatched':False,'blocked_reason':'project_archived_or_missing','workflow_id':str(row.id)})
   continue
  if (row.metadata_json or {}).get('schema')=='source-bound-book-run.v1':
   from bestseller.services.book_run import enqueue
   from bestseller.settings import apply_runtime_llm_profile,get_settings
   results.append(await enqueue(session,redis,row,settings=apply_runtime_llm_profile(get_settings())));continue
  if (row.metadata_json or {}).get('schema')=='pending-manuscript-continuation.v1':
   project=await session.get(ProjectModel,row.project_id)
   if project is None or await redis.exists('bestseller:pipeline-live:'+project.slug):continue
   from bestseller.services.pending_manuscript_continuation import enqueue
   results.append(await enqueue(session,redis,row));continue
  if row.status not in {'pending','running'}:continue
  if row.status=='running':
   project=await session.get(ProjectModel,row.project_id)
   if project is None:continue
   exists=getattr(redis,'exists',None)
   if not callable(exists) or await exists('bestseller:pipeline-live:'+project.slug):continue
   meta=deepcopy(row.metadata_json or {})
   if meta.get('stage_started') is not None:
    row.status='machine_blocked';row.current_step='interrupted_stage_requires_reconciliation'
    row.metadata_json={**meta,'blocked_reason':row.current_step,'automatic_retry':False}
    await session.commit();results.append({'dispatched':False,'blocked_reason':row.current_step});continue
   row.status='pending';row.metadata_json={**meta,'queued_job_id':None}
   await session.commit()
  results.append(await enqueue_checkpoint(session,redis,row.id))
 return results


async def _verify_upstream(session,project,row,outputs):
 await session.refresh(project)
 from bestseller.services.creation_configuration import read_window
 from bestseller.services.feedback import validate_adopted_knowledge_source,_matching_feedback_receipt
 from bestseller.infra.db.models import ChapterModel,ChapterDraftVersionModel
 binding=row.metadata_json['binding']
 upstream=await session.get(WorkflowRunModel,UUID(binding['source_workflow_id']),populate_existing=True)
 if upstream is None or upstream.project_id!=project.id or upstream.status!='completed':raise ValueError('continuation_upstream_not_completed')
 receipt=(upstream.metadata_json or {}).get('execution_window_receipt') or {}
 if content_hash(receipt)!=binding['source_receipt_sha256'] or receipt.get('window_complete') is not True:raise ValueError('continuation_upstream_receipt_changed')
 current=read_window(project)
 expected=(dict(schema_version='execution-window.v1',purpose='continuation',start=outputs['plan']['from_chapter'],end=outputs['plan']['through_chapter']) if 'activate' in outputs else binding['completed_window'])
 if current is None or current.model_dump(mode='json')!=expected:raise ValueError('continuation_project_window_changed')
 source_rows=receipt.get('sources')
 expected_numbers=list(range(binding['completed_window']['start'],binding['completed_window']['end']+1))
 if not isinstance(source_rows,list) or [v.get('chapter_number') for v in source_rows]!=expected_numbers:raise ValueError('continuation_prior_source_inventory')
 for source in source_rows:
  did=source.get('source_chapter_draft_version_id')
  if not did:raise ValueError('continuation_prior_source_missing')
  draft=await session.get(ChapterDraftVersionModel,UUID(did),populate_existing=True)
  if draft is None:raise ValueError('continuation_prior_source_missing')
  chapter=await session.get(ChapterModel,draft.chapter_id,populate_existing=True)
  if chapter is None or chapter.chapter_number!=source['chapter_number']:raise ValueError('continuation_prior_source_scope')
  lineage,error=await validate_adopted_knowledge_source(session,project_id=project.id,chapter_id=chapter.id,source_chapter_draft_version_id=draft.id,chapter_md=draft.content_md,lock=False)
  if error is not None or {**lineage,'chapter_number':chapter.chapter_number}!=source or _matching_feedback_receipt(chapter,lineage) is None:raise ValueError('continuation_prior_body_or_knowledge_changed')


async def execute_checkpoint(session,checkpoint_id,*,handlers,permit,owner_id=None,resume_after_restart=False):
 row=await session.get(WorkflowRunModel,UUID(str(checkpoint_id)),populate_existing=True,with_for_update=True)
 if row is None or row.workflow_type!='autonomous_continuation':raise ValueError('continuation_checkpoint_missing')
 if row.status=='completed':return deepcopy(row.metadata_json['execution_result'])
 if row.status in {'machine_blocked','failed','cancelled'}:
  return {'state':row.status,'blocked_reason':row.current_step,'whole_book_complete':False}
 meta=deepcopy(row.metadata_json or {});binding=meta['binding']
 owner_id=owner_id or str(uuid4())
 if row.status=='running' and meta.get('execution_owner') not in (None,owner_id):
  if not resume_after_restart:return {'state':'already_running','whole_book_complete':False}
  if meta.get('stage_started') is not None:
   row.status='machine_blocked';row.current_step='interrupted_stage_requires_reconciliation'
   row.metadata_json={**meta,'blocked_reason':row.current_step};await session.commit()
   return {'state':'machine_blocked','blocked_reason':row.current_step,'whole_book_complete':False}
 if row.status=='pending':
  claimed=await session.execute(update(WorkflowRunModel).where(WorkflowRunModel.id==row.id,WorkflowRunModel.status=='pending').values(status='running',metadata_json={**meta,'execution_owner':owner_id}).execution_options(synchronize_session=False))
  await session.commit();await session.refresh(row)
  if claimed.rowcount!=1:return {'state':'already_running','whole_book_complete':False}
 else:
  row.metadata_json={**meta,'execution_owner':owner_id}
  await session.commit()
 meta=deepcopy(row.metadata_json)
 project=await session.get(ProjectModel,row.project_id)
 if project is None or project.target_chapters!=binding['target_chapters']:
  row.status='machine_blocked';row.current_step='continuation_goal_changed'
  row.metadata_json={**meta,'blocked_reason':row.current_step,'automatic_retry':False}
  await session.commit();return {'state':'machine_blocked','blocked_reason':row.current_step,'whole_book_complete':False}
 allowed,reason=await permit(session,project,row)
 if meta.get('provider_dispatch_authorized') is not True or not allowed:
  reason=reason or 'external_authorization_required'
  row.status='machine_blocked';row.current_step=reason;row.metadata_json={**meta,'blocked_reason':reason}
  await session.commit();return {'state':'machine_blocked','blocked_reason':reason,'whole_book_complete':False}
 stages=STAGES if binding['next_window_proposal'] else ('review_and_export',)
 outputs=deepcopy(meta.get('stage_outputs') or {})
 for stage in stages:
  if stage in outputs:continue
  allowed,reason=await permit(session,project,row)
  if not allowed:
   row.status='machine_blocked';row.current_step=reason or 'external_authorization_required'
   row.metadata_json={**row.metadata_json,'blocked_reason':row.current_step};await session.commit()
   return {'state':'machine_blocked','blocked_reason':row.current_step,'whole_book_complete':False}
  try:
   await _verify_upstream(session,project,row,outputs)
  except ValueError as exc:
   row.status='machine_blocked';row.current_step=str(exc)
   row.metadata_json={**row.metadata_json,'blocked_reason':str(exc)};await session.commit()
   return {'state':'machine_blocked','blocked_reason':str(exc),'whole_book_complete':False}
  if stage not in handlers:
   reason='continuation_handler_missing:'+stage
   row.status='machine_blocked';row.current_step=reason
   row.metadata_json={**row.metadata_json,'blocked_reason':reason,'automatic_retry':False}
   await session.commit();return {'state':'machine_blocked','blocked_reason':reason,'whole_book_complete':False}
  row.status='running';row.current_step=stage
  row.metadata_json={**row.metadata_json,'stage_started':stage,'stage_outputs':outputs}
  await session.commit()
  try:
   output=await handlers[stage](session,project,row,deepcopy(outputs))
   if not isinstance(output,dict):raise ValueError('continuation_stage_receipt_missing:'+stage)
   if output.get('blocked_reason') or output.get('requires_human_review'):
    reason=str(output.get('blocked_reason') or 'stage_requires_review')
    row.status='machine_blocked';row.current_step=reason
    row.metadata_json={**row.metadata_json,'blocked_reason':reason,'blocked_stage':stage,'blocked_output':output}
    await session.commit();return {'state':'machine_blocked','blocked_reason':reason,'whole_book_complete':False}
   outputs[stage]=output
   row.metadata_json={**row.metadata_json,'stage_started':None,'stage_outputs':outputs}
   await session.commit()
  except Exception as exc:
   checkpoint_uuid=UUID(str(checkpoint_id))
   await session.rollback();row=await session.get(WorkflowRunModel,checkpoint_uuid,populate_existing=True)
   original=getattr(exc,'orig',None)
   row.status='machine_blocked';row.current_step='continuation_stage_unknown'
   row.metadata_json={**row.metadata_json,'blocked_stage':stage,'blocked_reason':'continuation_stage_unknown',
    'error':{'type':type(exc).__name__,'message':str(original)[:1000] if original else str(exc)[:1000]},'automatic_retry':False}
   await session.commit();raise
 last=outputs[stages[-1]]
 complete=bool(stages==('review_and_export',) and last.get('whole_book_complete') is True
               and last.get('export_artifact_id') and last.get('output_path'))
 result={'state':'completed','whole_book_complete':complete,'stage_outputs':outputs,
         'project_id':str(project.id),'next_checkpoint':last.get('continuation_workflow_run_id')}
 row.status='completed';row.current_step='whole_book_completed' if complete else 'next_window_completed'
 row.metadata_json={**row.metadata_json,'stage_started':None,'execution_result':result}
 await session.commit();return result
