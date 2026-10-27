"""Durable next-window proposals, never permission to skip planning or safety.

The existing creation/autowrite entry remains the only producer. A partial
window result is not whole-book completion, even when its local export exists.
No scheduler, provider, gate override, or window mutation lives in this module.
"""
from copy import deepcopy
from collections.abc import Mapping
from uuid import UUID,uuid5,NAMESPACE_URL
from bestseller.infra.db.models import ProjectModel,WorkflowRunModel
from bestseller.services.prompt_source_policy import content_hash

SCHEMA='autonomous-window-continuation.v1'


def is_partial_window_result(result):
 return (isinstance(result,Mapping) and isinstance(result.get('execution_window'),Mapping)
         and result.get('whole_book_complete') is False)


def next_window_proposal(*,window,target_chapters,batch_size=None):
 if not isinstance(window,Mapping):raise ValueError('continuation_window_missing')
 start,end=window.get('start'),window.get('end')
 if (type(start) is not int or type(end) is not int or type(target_chapters) is not int
     or not 1<=start<=end<=target_chapters):raise ValueError('continuation_extent_invalid')
 if batch_size is None:batch_size=end-start+1
 if type(batch_size) is not int or batch_size<1:raise ValueError('continuation_batch_size_invalid')
 if end==target_chapters:return None
 return {'schema_version':'execution-window.v1','purpose':'continuation',
         'start':end+1,'end':min(target_chapters,end+batch_size)}


async def record_window_continuation(session,*,project_id,result,dispatch_authorized=False,requested_model=None,batch_size=None):
 """Persist one bounded, idempotent proposal after a proven completed window.

 This record is an outbox/checkpoint for normal continuation planning, not an
 active window, a generation authorization, or proof the next chapter is ready.
 """
 if not is_partial_window_result(result):return None
 if result.get('requires_human_review') or result.get('final_verdict')!='execution_window_completed':return None
 project=await session.get(ProjectModel,project_id,populate_existing=True,with_for_update=True)
 if project is None:raise ValueError('continuation_project_missing')
 if str(result.get('project_id'))!=str(project.id):raise ValueError('continuation_project_mismatch')
 upstream_id=UUID(str(result.get('project_workflow_run_id') or result.get('workflow_run_id')))
 upstream=await session.get(WorkflowRunModel,upstream_id,populate_existing=True)
 if upstream is None or upstream.project_id!=project.id or upstream.status!='completed':
  raise ValueError('continuation_completed_source_missing')
 receipt=(upstream.metadata_json or {}).get('execution_window_receipt') or {}
 if receipt.get('window_complete') is not True or receipt.get('window')!=result['execution_window']:
  raise ValueError('continuation_window_proof_mismatch')
 if (receipt.get('whole_book_complete') is not False or receipt.get('review_debt')
     or receipt.get('project_review_debt')):raise ValueError('continuation_source_requires_review')
 if batch_size is None:batch_size=result['execution_window']['end']-result['execution_window']['start']+1
 proposal=next_window_proposal(window=result['execution_window'],target_chapters=project.target_chapters,batch_size=batch_size)
 stage='continuation_planning_required' if proposal else 'whole_book_review_required'
 binding={'project_id':str(project.id),'target_chapters':project.target_chapters,
          'source_workflow_id':str(upstream_id),'source_receipt_sha256':content_hash(receipt),
          'completed_window':deepcopy(result['execution_window']),'next_window_proposal':proposal,
          'dispatch_authorized':dispatch_authorized is True,'requested_model':requested_model,'continuation_batch_size':batch_size}
 identity=uuid5(NAMESPACE_URL,SCHEMA+':'+content_hash(binding))
 row=await session.get(WorkflowRunModel,identity,populate_existing=True)
 metadata={'schema':SCHEMA,'binding':binding,'whole_book_complete':False,
           'source_revalidation_required':True,'next_prefix_admission_required':bool(proposal),
           'window_changed':False,'provider_dispatch_authorized':dispatch_authorized is True,'paid_calls':0}
 if row is None:
  row=WorkflowRunModel(id=identity,project_id=project.id,workflow_type='autonomous_continuation',
      scope_type='project',scope_id=project.id,status='pending',current_step=stage,
      requested_by='normal-autowrite-checkpoint',metadata_json=metadata)
  session.add(row);await session.flush()
 elif (row.project_id!=project.id or row.workflow_type!='autonomous_continuation'
       or row.metadata_json.get('schema')!=SCHEMA or row.metadata_json.get('binding')!=binding
       or row.metadata_json.get('provider_dispatch_authorized') is not (dispatch_authorized is True)):
  raise ValueError('continuation_checkpoint_conflict')
 return {'continuation_workflow_run_id':str(identity),'continuation_required':True,
         'next_stage':stage,'next_window_proposal':proposal,'whole_book_complete':False,
         'automatic_dispatch_started':False}
