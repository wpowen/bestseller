"""Actual SQLite: audit an existing stopped binding, never authorize a new one."""
from copy import deepcopy
from datetime import datetime,timezone
from contextlib import asynccontextmanager
import pytest
from sqlalchemy import select,func
from test_execution_phase_handoff import ready,store
from bestseller.infra.db import models as m
from bestseller.services import execution_phase_handoff as h
from bestseller.services.conception_continuation import active_scope,resume_allowed
from bestseller.services.budget import ExecutionBudgetError
from bestseller.web import server
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def stopped(store,tmp_path):
 x=await ready(store,tmp_path)
 async with store.sessions() as s:
  receipt=await h.create(s,task_id=x.task,payload=x.payload);await s.commit()
 x.payload['_execution_phase_handoff']=receipt['receipt_key']
 async with store.sessions() as s:
  b=await s.get(m.ExecutionBudgetModel,x.scope);b.status='unknown';b.calls_used+=1;b.tokens_charged+=1065536
  s.add(m.ExecutionOperationModel(key='new-timeout',scope_id=x.scope,input_hash='actual-unknown-input',status='unknown',owner='real-stopped-dispatcher',lease_until=b.deadline_at))
  s.add(m.ExecutionAttemptModel(id='new-timeout-attempt',operation_key='new-timeout',scope_id=x.scope,input_hash='actual-unknown-input',model_name='openai/MiniMax-M3.1-Flash',status='unknown',reserved_tokens=1065536,actual_tokens=None,created_at=datetime.now(timezone.utc)))
  p=await s.get(m.ProjectModel,store.pid);p.status='paused';p.metadata_json={**p.metadata_json,'execution_terminal':{'code':'execution_result_unknown','terminal':True},'production_paused':True,'production_pause_reason':'execution_result_unknown'}
  await s.commit()
 return x

async def manager_for(store,x,tmp_path,monkeypatch):
 @asynccontextmanager
 async def committed_scope(_settings):
  async with store.sessions() as s:
   yield s
   await s.commit()
 monkeypatch.setattr(server,'session_scope',committed_scope)
 manager=server.WebTaskManager(persist_path=tmp_path/'audit-tasks.json')
 manager._tasks[x.task]=server.WebTaskState(task_id=x.task,task_type='autowrite',status='failed',created_at='2026-10-21T10:00:00+00:00',updated_at='2026-10-21T10:34:24+00:00',project_slug=store.slug,payload=deepcopy(x.payload),error='execution_result_unknown',result={'execution_terminal':{'code':'execution_result_unknown','terminal':True}})
 return manager

async def test_audit_existing_receipt_survives_unknown_but_execution_and_new_handoff_stay_blocked(store,tmp_path,monkeypatch):
 x=await stopped(store,tmp_path);manager=await manager_for(store,x,tmp_path,monkeypatch)
 # This is the production failure: inspection must reach the unknown ledger.
 state=await manager.execution_reconciliation(x.task,settings=store.settings)
 assert state['status']=='unknown'
 with pytest.raises(ExecutionBudgetError):await resume_allowed(store.engine,x.task,x.payload)
 async with store.sessions() as s:
  with pytest.raises(ExecutionBudgetError,match='execution_result_unknown'):await h.create(s,task_id=x.task,payload=x.payload)
  a=await s.get(m.ExecutionAttemptModel,'new-timeout-attempt');assert a.status=='unknown' and a.actual_tokens is None
  b=await s.get(m.ExecutionBudgetModel,x.scope);assert b.tokens_charged==1065566
  assert len((await s.get(m.ProjectModel,store.pid)).metadata_json['execution_phase_handoff_receipts'])==1
 assert manager._tasks[x.task].status=='failed'

@pytest.mark.parametrize('fault',['intent','missing_receipt','forged_receipt','canonical','unrelated_stop'])
async def test_audit_does_not_exempt_changed_or_unproven_binding(store,tmp_path,monkeypatch,fault):
 x=await stopped(store,tmp_path)
 if fault=='intent':x.payload['concept_seed']='changed author intent'
 if fault=='missing_receipt':x.payload.pop('_execution_phase_handoff')
 async with store.sessions() as s:
  if fault=='forged_receipt':(await s.get(m.ExecutionOperationModel,x.payload['_execution_phase_handoff'])).result_json={'binding':{}}
  p=await s.get(m.ProjectModel,store.pid)
  if fault=='canonical':p.metadata_json={**p.metadata_json,'creation_canonical_snapshot_hash':'changed'}
  if fault=='unrelated_stop':p.metadata_json={**p.metadata_json,'execution_terminal':{'code':'quality_rejected'}}
  await s.commit()
 manager=await manager_for(store,x,tmp_path,monkeypatch)
 with pytest.raises(ExecutionBudgetError,match='binding_invalid'):await manager.execution_reconciliation(x.task,settings=store.settings)
 async with store.sessions() as s:
  a=await s.get(m.ExecutionAttemptModel,'new-timeout-attempt');assert a.status=='unknown' and a.reserved_tokens==1065536

async def test_explicit_quarantine_preserves_full_hold_and_old_unknown_without_auto_dispatch(store,tmp_path,monkeypatch):
 x=await stopped(store,tmp_path);manager=await manager_for(store,x,tmp_path,monkeypatch)
 payload={'reconciliation_id':'fixture-explicit-quarantine','attempt_id':'new-timeout-attempt','expected_input_hash':'actual-unknown-input','decision':'quarantine_charged','evidence_kind':'dispatcher_exit','evidence_sha256':'a'*64,'actor':'offline authorized operator','remote_operation_closed':False,'local_dispatcher_stopped':True,'result':None}
 receipt=await manager.execution_reconciliation(x.task,payload,settings=store.settings)
 assert receipt['status']=='active' and receipt['automatic_resume'] is False and receipt['credit_released'] is False
 assert receipt['operation_replay_allowed'] is False and receipt['remote_outcome']=='unknown_quarantined'
 assert manager._tasks[x.task].status=='failed' and manager._tasks[x.task].current_stage=='execution_reconciled_manual_resume_required'
 async with store.sessions() as s:
  a=await s.get(m.ExecutionAttemptModel,'new-timeout-attempt');assert (a.status,a.reserved_tokens,a.actual_tokens)==('reconciled_charged',1065536,None)
  old=await s.get(m.ExecutionAttemptModel,'frozen-hold');assert (old.status,old.reserved_tokens,old.actual_tokens)==('unknown',67132,None)
  b=await s.get(m.ExecutionBudgetModel,x.scope);assert (b.calls_used,b.tokens_charged)==(2,1065566)
  p=await s.get(m.ProjectModel,store.pid);assert p.status=='paused' and p.metadata_json['production_paused'] is True
  assert p.metadata_json['production_pause_reason']=='execution_reconciled_manual_resume_required'
  assert 'execution_terminal' not in p.metadata_json
  assert len(p.metadata_json['execution_phase_handoff_receipts'])==1
  assert await s.scalar(select(func.count()).select_from(m.WorkflowRunModel).where(m.WorkflowRunModel.status=='running'))==0
 state=await manager.execution_reconciliation(x.task,settings=store.settings)
 assert state['status']=='active'
 assert await active_scope(store.engine,x.task,x.payload)==x.scope
 repeated=await manager.execution_reconciliation(x.task,payload,settings=store.settings)
 assert repeated==receipt
 async with store.sessions() as s:
  p=await s.get(m.ProjectModel,store.pid)
  assert len(p.metadata_json['execution_reconciliation_history'])==1
  assert (await s.get(m.ExecutionBudgetModel,x.scope)).tokens_charged==1065566
 assert store.calls==[]


async def test_continuation_scope_namespace_retained_and_lookalikes_rejected():
 from types import SimpleNamespace
 from uuid import uuid4
 from bestseller.services.retry_ledger import project_execution_scope
 pid=uuid4()
 valid='conception-continuation:original-task:authorized'
 assert project_execution_scope(SimpleNamespace(id=pid,metadata_json={'execution_scope_id':valid}))==valid
 for invalid in ('conception-continuationevil:task','conception-continuationx:task','x'*257):
  assert project_execution_scope(SimpleNamespace(id=pid,metadata_json={'execution_scope_id':invalid}))==f'project:{pid}'
