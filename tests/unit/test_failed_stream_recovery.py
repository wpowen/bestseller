"""Real SQLite operator recovery of a journaled, locally interrupted stream."""
from copy import deepcopy
import hashlib,json
from uuid import uuid4
from datetime import datetime,timezone,timedelta
import pytest
from test_stopped_handoff_reconciliation import stopped,manager_for,store
from bestseller.infra.db import models as m
from bestseller.services import execution_phase_handoff as h
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.conception_continuation import resume_allowed
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]
CODE='execution_ledger_unavailable'

def key(kind,scope,attempt):return hashlib.sha256((kind+'\n'+scope+'\n'+attempt).encode()).hexdigest()

async def interrupted(store,tmp_path,monkeypatch):
 x=await stopped(store,tmp_path)
 async with store.sessions() as s:
  b=await s.get(m.ExecutionBudgetModel,x.scope);b.status='active'
  a=await s.get(m.ExecutionAttemptModel,'new-timeout-attempt');a.status='reserved'
  op=await s.get(m.ExecutionOperationModel,'new-timeout');op.status='pending'
  wf=m.WorkflowRunModel(id=uuid4(),project_id=store.pid,workflow_type='generate_foundation_plan',status='failed',current_step='generate_cast_spec',error_message=CODE,metadata_json={'execution_scope_id':x.scope},created_at=datetime.now(timezone.utc));s.add(wf)
  p=await s.get(m.ProjectModel,store.pid);p.metadata_json={**p.metadata_json,'execution_terminal':{'code':CODE,'terminal':True},'production_pause_reason':CODE}
  diagnostic={'schema':'transport-failure.v1','attempt_id':a.id,'operation_key':op.key,'workflow_run_id':str(wf.id),'project_id':str(store.pid),'physical_sends':1,'partial_stream_observed':True,'operator_interruption':False,'error_chain':[{'type':'ExecutionBudgetError','execution_code':CODE},{'type':'TimeoutError'}]}
  partial={'id':'synthetic-provider-response','stream_complete':False,'chunks_observed':3,'choices':[{'finish_reason':None,'message':{'role':'assistant','content':'unfinished JSON','reasoning_content':''}}],'usage':{'prompt_tokens':None,'completion_tokens':None}}
  returned={'schema':'provider-return.v1','attempt_id':a.id,'operation_key':op.key,'input_hash':a.input_hash,'model_name':a.model_name,'wire_body_hash':'b'*64,'response':partial,'response_sha256':hashlib.sha256(json.dumps(partial,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest(),'usable':False,'adoption_authorized':False,'credit_released':False}
  for kind,status,value in [('transport-failure.v1','transport_failure_receipt',diagnostic),('provider-return.v1','unusable_provider_return',returned)]:
   s.add(m.ExecutionOperationModel(key=key(kind,x.scope,a.id),scope_id=x.scope,input_hash=a.input_hash,status=status,owner=op.owner,lease_until=op.lease_until,result_json=value))
  await s.commit();x.interrupted_workflow_id=wf.id
 manager=await manager_for(store,x,tmp_path,monkeypatch);task=manager._tasks[x.task];task.error=CODE;task.current_stage=CODE;task.result={'execution_terminal':{'code':CODE,'terminal':True}}
 return x,manager

async def test_ledger_failure_can_be_audited_without_opening_execution(store,tmp_path,monkeypatch):
 x,manager=await interrupted(store,tmp_path,monkeypatch)
 result=await manager.execution_reconciliation(x.task,settings=store.settings)
 assert result['status']=='active'
 with pytest.raises(ExecutionBudgetError):await resume_allowed(store.engine,x.task,x.payload)
 async with store.sessions() as s:
  with pytest.raises(ValueError):await h.create(s,task_id=x.task,payload=x.payload)
  a=await s.get(m.ExecutionAttemptModel,'new-timeout-attempt');assert a.status=='reserved' and a.actual_tokens is None
 assert store.calls==[]

async def test_explicit_failed_stream_quarantine_keeps_hold_and_requires_manual_resume(store,tmp_path,monkeypatch):
 x,manager=await interrupted(store,tmp_path,monkeypatch)
 evidence={'reconciliation_id':'synthetic-ledger-stream-quarantine','attempt_id':'new-timeout-attempt','expected_input_hash':'actual-unknown-input','decision':'quarantine_charged','evidence_kind':'dispatcher_exit','evidence_sha256':'c'*64,'actor':'offline authorized operator','remote_operation_closed':False,'local_dispatcher_stopped':True,'result':None}
 result=await manager.execution_reconciliation(x.task,evidence,settings=store.settings)
 assert result['ready_for_manual_resume'] and not result['automatic_resume'] and not result['credit_released']
 assert result['operation_replay_allowed'] is False
 assert manager._tasks[x.task].current_stage=='execution_reconciled_manual_resume_required' and manager._tasks[x.task].error is None
 async with store.sessions() as s:
  a=await s.get(m.ExecutionAttemptModel,'new-timeout-attempt');assert (a.status,a.reserved_tokens,a.actual_tokens)==('reconciled_charged',1065536,None)
  assert (await s.get(m.ExecutionOperationModel,'new-timeout')).status=='stopped'
  p=await s.get(m.ProjectModel,store.pid);assert p.status=='paused' and p.metadata_json['production_pause_reason']=='execution_reconciled_manual_resume_required' and 'execution_terminal' not in p.metadata_json
  b=await s.get(m.ExecutionBudgetModel,x.scope);assert (b.calls_used,b.tokens_charged)==(2,1065566)
  assert (await s.get(m.ExecutionAttemptModel,'frozen-hold')).status=='unknown'
 repeated=await manager.execution_reconciliation(x.task,evidence,settings=store.settings);assert repeated==result
 async with store.sessions() as s:
  assert len((await s.get(m.ProjectModel,store.pid)).metadata_json['execution_reconciliation_history'])==1
  assert (await s.get(m.WorkflowRunModel,x.interrupted_workflow_id)).error_message==CODE
 assert store.calls==[]

@pytest.mark.parametrize('fault',['no_partial','hash','complete','physical_zero','owner_active','newer_workflow'])
async def test_failed_stream_audit_does_not_exempt_unproven_or_live_owner(store,tmp_path,monkeypatch,fault):
 x,manager=await interrupted(store,tmp_path,monkeypatch)
 async with store.sessions() as s:
  returned=await s.get(m.ExecutionOperationModel,key('provider-return.v1',x.scope,'new-timeout-attempt'))
  if fault=='no_partial':await s.delete(returned)
  if fault=='hash':returned.result_json={**returned.result_json,'response_sha256':'d'*64}
  if fault=='complete':
   raw=deepcopy(returned.result_json);raw['response']['stream_complete']=True;raw['response_sha256']=hashlib.sha256(json.dumps(raw['response'],ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest();returned.result_json=raw
  if fault=='physical_zero':
   failure=await s.get(m.ExecutionOperationModel,key('transport-failure.v1',x.scope,'new-timeout-attempt'));failure.result_json={**failure.result_json,'physical_sends':0}
  if fault=='owner_active':(await s.get(m.WorkflowRunModel,x.interrupted_workflow_id)).status='running'
  if fault=='newer_workflow':
   owner=await s.get(m.WorkflowRunModel,x.interrupted_workflow_id)
   s.add(m.WorkflowRunModel(project_id=store.pid,workflow_type='generate_foundation_plan',status='failed',error_message=CODE,metadata_json={'execution_scope_id':x.scope},created_at=owner.created_at+timedelta(seconds=1)))
  await s.commit()
 with pytest.raises(ExecutionBudgetError,match='binding_invalid'):await manager.execution_reconciliation(x.task,settings=store.settings)
 async with store.sessions() as s:
  a=await s.get(m.ExecutionAttemptModel,'new-timeout-attempt');assert a.status=='reserved' and a.reserved_tokens==1065536 and a.actual_tokens is None
 assert store.calls==[]

@pytest.mark.parametrize('fault',['new_terminal','new_budget_stop','newer_workflow'])
async def test_repeated_quarantine_cannot_clear_a_new_stop_or_new_owner(store,tmp_path,monkeypatch,fault):
 x,manager=await interrupted(store,tmp_path,monkeypatch)
 evidence={'reconciliation_id':'synthetic-race-quarantine','attempt_id':'new-timeout-attempt','expected_input_hash':'actual-unknown-input','decision':'quarantine_charged','evidence_kind':'dispatcher_exit','evidence_sha256':'c'*64,'actor':'offline authorized operator','remote_operation_closed':False,'local_dispatcher_stopped':True,'result':None}
 first=await manager.execution_reconciliation(x.task,evidence,settings=store.settings)
 assert first['ready_for_manual_resume']
 async with store.sessions() as s:
  p=await s.get(m.ProjectModel,store.pid)
  if fault=='new_terminal':p.metadata_json={**p.metadata_json,'execution_terminal':{'code':'execution_cancelled','terminal':True}}
  if fault=='new_budget_stop':p.metadata_json={**p.metadata_json,'execution_budget':{'code':'execution_authorization_paused','terminal':True}}
  if fault=='newer_workflow':
   owner=await s.get(m.WorkflowRunModel,x.interrupted_workflow_id)
   s.add(m.WorkflowRunModel(project_id=store.pid,workflow_type='generate_foundation_plan',status='failed',error_message=CODE,metadata_json={'execution_scope_id':x.scope},created_at=owner.created_at+timedelta(seconds=1)))
  await s.commit()
 if fault=='new_terminal':
  with pytest.raises(ExecutionBudgetError,match='binding_invalid'):await manager.execution_reconciliation(x.task,evidence,settings=store.settings)
 else:
  repeated=await manager.execution_reconciliation(x.task,evidence,settings=store.settings)
  assert not repeated['ready_for_manual_resume']
 async with store.sessions() as s:
  p=await s.get(m.ProjectModel,store.pid)
  assert len(p.metadata_json['execution_reconciliation_history'])==1
  assert (await s.get(m.ExecutionAttemptModel,'new-timeout-attempt')).reserved_tokens==1065536
 assert store.calls==[]
