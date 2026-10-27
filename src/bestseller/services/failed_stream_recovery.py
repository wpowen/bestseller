"""Read-only proof for a ledger-check interruption with an unusable real stream.

This never sends, adopts, releases credit, marks an attempt unsent, or clears
an unrelated stop. The explicit reconciliation API remains the only writer.
"""
import hashlib,json
from uuid import UUID
from sqlalchemy import select,func
from bestseller.infra.db.models import ExecutionAttemptModel as Attempt,ExecutionOperationModel as Operation,WorkflowRunModel as Workflow

CODE='execution_ledger_unavailable'

def key(kind,scope,attempt):return hashlib.sha256((kind+'\n'+scope+'\n'+attempt).encode()).hexdigest()

async def verified_stopped_partial(session, *, project, scope_id, attempt_id=None):
 owners=list(await session.scalars(select(Workflow).where(Workflow.project_id==project.id).order_by(Workflow.created_at.desc()).limit(2)))
 if not owners or (len(owners)>1 and owners[0].created_at==owners[1].created_at):return None
 active=await session.scalar(select(func.count()).select_from(Workflow).where(Workflow.project_id==project.id,Workflow.status.in_(['running','queued','pending','stopping'])))
 if active:return None
 return await _partial_for_owner(session,project=project,scope_id=scope_id,owner=owners[0],attempt_id=attempt_id)

async def _partial_for_owner(session, *, project, scope_id, owner, attempt_id=None):
 if (owner is None or owner.project_id!=project.id or owner.status!='failed' or owner.error_message!=CODE
     or (owner.metadata_json or {}).get('execution_scope_id')!=scope_id
     or (project.metadata_json or {}).get('execution_scope_id')!=scope_id):return None
 failures=list(await session.scalars(select(Operation).where(Operation.scope_id==scope_id,Operation.status=='transport_failure_receipt',Operation.result_json['workflow_run_id'].as_string()==str(owner.id))))
 if attempt_id is not None:failures=[row for row in failures if (row.result_json or {}).get('attempt_id')==attempt_id]
 if len(failures)!=1:return None
 failure=failures[0].result_json or {};aid=failure.get('attempt_id')
 if (not isinstance(aid,str) or failure.get('schema')!='transport-failure.v1'
     or failure.get('physical_sends')!=1 or failure.get('partial_stream_observed') is not True
     or failure.get('operator_interruption') is not False or failure.get('project_id')!=str(project.id)
     or not any(item.get('execution_code')==CODE for item in failure.get('error_chain',[]) if isinstance(item,dict))):return None
 attempt=await session.get(Attempt,aid);operation=await session.get(Operation,failure.get('operation_key'))
 if (attempt is None or operation is None or attempt.scope_id!=scope_id or operation.scope_id!=scope_id
     or attempt.operation_key!=operation.key or attempt.input_hash!=operation.input_hash
     or attempt.actual_tokens is not None or attempt.reserved_tokens<=0
     or attempt.status not in {'reserved','unknown','reconciled_charged'}
     or operation.status not in {'pending','unknown','stopped'}
     or operation.result_json is not None or failures[0].key!=key('transport-failure.v1',scope_id,aid)):return None
 saved=await session.get(Operation,key('provider-return.v1',scope_id,aid))
 if saved is None or saved.status!='unusable_provider_return':return None
 record=saved.result_json or {};response=record.get('response') or {}
 encoded=json.dumps(response,ensure_ascii=False,sort_keys=True,allow_nan=False)
 choices=response.get('choices') or [];usage=response.get('usage') or {}
 if (record.get('operation_key')!=operation.key or record.get('attempt_id')!=aid
     or record.get('input_hash')!=attempt.input_hash or record.get('usable') is not False
     or record.get('adoption_authorized') is not False or record.get('credit_released') is not False
     or record.get('response_sha256')!=hashlib.sha256(encoded.encode()).hexdigest()
     or response.get('stream_complete') is not False or not response.get('id')
     or not isinstance(response.get('chunks_observed'),int) or response['chunks_observed']<1
     or len(choices)!=1 or choices[0].get('finish_reason') is not None
     or usage.get('prompt_tokens') is not None or usage.get('completion_tokens') is not None):return None
 complete=await session.get(Operation,key('transport-result.v1',scope_id,aid))
 if complete is not None:return None
 return {'attempt_id':aid,'operation_key':operation.key,'input_hash':attempt.input_hash,
         'workflow_id':str(owner.id),'partial_response_sha256':record['response_sha256'],
         'attempt_status':attempt.status,'operation_status':operation.status,
         'remote_outcome':'unknown','adoption_authorized':False,'credit_released':False}

async def verified_quarantine(session,*,project,scope_id,receipt):
 if (receipt.get('scope_id')!=scope_id or receipt.get('decision')!='quarantine_charged'
     or receipt.get('operation_replay_allowed') is not False
     or receipt.get('remote_outcome')!='unknown_quarantined' or receipt.get('credit_released') is not False):return False
 proof=await verified_stopped_partial(session,project=project,scope_id=scope_id,attempt_id=receipt.get('attempt_id'))
 if (not proof or proof['attempt_status']!='reconciled_charged' or proof['operation_status']!='stopped'):return False
 identifier=receipt.get('reconciliation_id')
 if not isinstance(identifier,str):return False
 saved=await session.get(Operation,hashlib.sha256(('reconciliation.v1\n'+scope_id+'\n'+identifier).encode()).hexdigest())
 record=(saved.result_json or {}) if saved else {}
 return bool(saved and saved.status=='reconciliation_receipt' and record.get('receipt')==receipt
     and record.get('evidence_kind')=='dispatcher_exit' and record.get('local_dispatcher_stopped') is True
     and record.get('remote_operation_closed') is False)

async def retained_quarantine(session, *, project, scope_id, receipt):
 """Read an immutable prior quarantine for genuinely different delivery units.

 Unlike the operator-clear proof, a newer workflow may now own the project.
 This authorizes no send, adoption, credit or reopening of the stopped key.
 """
 if (receipt.get('scope_id')!=scope_id or receipt.get('decision')!='quarantine_charged'
     or receipt.get('operation_replay_allowed') is not False
     or receipt.get('remote_outcome')!='unknown_quarantined' or receipt.get('credit_released') is not False):return None
 aid=receipt.get('attempt_id');identifier=receipt.get('reconciliation_id')
 if not isinstance(aid,str) or not isinstance(identifier,str):return None
 failure=await session.get(Operation,key('transport-failure.v1',scope_id,aid))
 if not failure:return None
 try:owner_id=UUID((failure.result_json or {})['workflow_run_id'])
 except (KeyError,ValueError,TypeError):return None
 owner=await session.get(Workflow,owner_id)
 proof=await _partial_for_owner(session,project=project,scope_id=scope_id,owner=owner,attempt_id=aid)
 if not proof or proof['attempt_status']!='reconciled_charged' or proof['operation_status']!='stopped':return None
 saved=await session.get(Operation,hashlib.sha256(('reconciliation.v1\n'+scope_id+'\n'+identifier).encode()).hexdigest())
 record=(saved.result_json or {}) if saved else {}
 if not (saved and saved.status=='reconciliation_receipt' and record.get('receipt')==receipt
     and record.get('evidence_kind')=='dispatcher_exit' and record.get('local_dispatcher_stopped') is True
     and record.get('remote_operation_closed') is False):return None
 return {**proof,'reconciliation_id':identifier,'operation_replay_allowed':False}
