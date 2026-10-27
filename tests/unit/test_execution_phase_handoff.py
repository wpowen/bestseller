"""Actual file-SQLite phase handoff; provider forbidden by the store fixture."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from test_creation_configuration_slice import store
from test_reviewed_conception_selection import setup as reviewed_setup
from bestseller.infra.db import models as m
from bestseller.services import execution_phase_handoff as h,independent_acceptance as i
from bestseller.services.reviewed_conception_selection import select_reviewed_conception_snapshot
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.conception_continuation import active_scope
from bestseller.services.budget import ExecutionBudgetError
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def ready(store,tmp_path):
 settings,sid,content,_=await reviewed_setup(store,tmp_path)
 task='phase-test';scope='conception-continuation:'+task+':authorized';now=datetime.now(timezone.utc)
 original=dict(concept_seed='same original creator intent',production_profile='complete',project_type='fanqie_short',
     target_chapters=12,target_words=78000,llm_model_id='minimax-m3.1-flash',_run_conception=True)
 async with store.sessions() as s:
  old=m.ExecutionBudgetModel(scope_id='frozen-old',max_calls=64,max_tokens=1000000,calls_used=1,tokens_charged=67132,deadline_at=now-timedelta(days=1),status='unknown',metadata_json={'original':'retained'})
  s.add(old);await s.flush();frozen=i.snapshot(old)
  s.add(m.ExecutionOperationModel(key='frozen-unknown',scope_id=old.scope_id,input_hash='never-replay',status='unknown',owner='original',lease_until=now))
  s.add(m.ExecutionAttemptModel(id='frozen-hold',operation_key='frozen-unknown',scope_id=old.scope_id,input_hash='never-replay',model_name=i.MODEL,status='unknown',reserved_tokens=67132,actual_tokens=None,created_at=now))
  req={'scope':scope,'counter_sha256':i.CONTRACT_SHA256,'new_calls':100,'new_tokens':9000000,'authorization_ref':'offline explicit approved project continuation',
       'model':i.MODEL,'window_seconds':7200,'continuation_task_id':task,'payload_sha256':i.payload_identity(original),'old_snapshot':frozen}
  grant={'schema':i.SCHEMA,'request':req,'request_sha256':i.digest(req),'submitted_at':now.isoformat()}
  s.add(m.ExecutionBudgetModel(scope_id=scope,max_calls=100,max_tokens=9000000,calls_used=1,tokens_charged=30,deadline_at=now+timedelta(hours=2),status='active',metadata_json={i.KEY:grant,'provider_authorization':{'mode':'completion_bound','status':'active','authorization_ref':'offline existing user authority','models':[i.MODEL],'endpoint_hosts':['api.minimaxi.com'],'objective':'same-book completion'}}))
  op=await s.get(m.ExecutionOperationModel,'actual-paid-fixture');op.scope_id=scope;op.result_json={**op.result_json,'fallback_used':False}
  a=await s.get(m.ExecutionAttemptModel,'fixture-attempt');a.scope_id=scope
  await s.commit()
 async with store.sessions() as s:
  await select_reviewed_conception_snapshot(s,settings,project_id=store.pid,snapshot_id=sid,expected_hash=snapshot_hash(content),actor='offline operator',reason='same reviewed version')
  p=await s.get(m.ProjectModel,store.pid);p.status='paused';p.metadata_json={**p.metadata_json,'execution_scope_id':scope,'execution_terminal':{'code':'execution_continuation_binding_invalid','terminal':True},'production_paused':True,'production_pause_reason':'execution_continuation_binding_invalid'}
  await s.commit()
 derived={**original,'_run_conception':False,'_active_continuation_scope':scope,'slug':store.slug,'title':content['title'],'premise':content['premise']}
 return SimpleNamespace(task=task,scope=scope,original=original,payload=derived,frozen=frozen,grant=deepcopy(grant),sid=sid)

async def test_legal_transition_preserves_original_unknown_and_audit_is_idempotent(store,tmp_path):
 x=await ready(store,tmp_path)
 with pytest.raises(ExecutionBudgetError,match='binding_invalid'):await active_scope(store.engine,x.task,x.payload)
 assert await active_scope(store.engine,x.task,{**x.original,'_active_continuation_scope':x.scope})==x.scope
 async with store.sessions() as s:
  receipt=await h.create(s,task_id=x.task,payload=x.payload);await s.commit()
 x.payload['_execution_phase_handoff']=receipt['receipt_key']
 assert await active_scope(store.engine,x.task,x.payload)==x.scope
 async with store.sessions() as s:
  assert receipt==await h.create(s,task_id=x.task,payload=x.payload)
  await s.commit()
 async with store.sessions() as s:
  b=await s.get(m.ExecutionBudgetModel,x.scope);p=await s.get(m.ProjectModel,store.pid)
  assert i.policy(b)==x.grant and b.calls_used==1 and b.tokens_charged==30
  assert i.snapshot(await s.get(m.ExecutionBudgetModel,'frozen-old'))==x.frozen
  hold=await s.get(m.ExecutionAttemptModel,'frozen-hold');assert (hold.status,hold.reserved_tokens,hold.actual_tokens)==('unknown',67132,None)
  assert p.metadata_json['execution_phase_handoff_receipts']==[receipt['receipt_key']]
  assert p.metadata_json['protected_authored']=='保留' and p.metadata_json['conception_approved'] is True
  assert p.status=='planning' and not p.metadata_json['production_paused']
  assert receipt['previous_projection']['execution_terminal']['code']=='execution_continuation_binding_invalid'
  assert receipt['provider_calls']==0 and receipt['budget_regranted'] is False
  assert receipt['binding']['original_binding_sha256']==x.grant['request']['payload_sha256']
  assert receipt['binding']['derived_binding_sha256']!=receipt['binding']['original_binding_sha256']
  assert await s.scalar(select(func.count()).select_from(m.ExecutionOperationModel).where(m.ExecutionOperationModel.status=='phase_handoff'))==1

@pytest.mark.parametrize('field,value',[('llm_model_id','different'),('concept_seed','different'),('production_profile','deep'),('project_type','different'),('target_chapters',13),('target_words',8000),('_run_conception',True),('premise','different'),('title','different')])
async def test_changed_author_controls_or_projection_rejected(store,tmp_path,field,value):
 x=await ready(store,tmp_path)
 async with store.sessions() as s:
  with pytest.raises(ValueError,match='phase_handoff_'):await h.create(s,task_id=x.task,payload={**x.payload,field:value})
  await s.rollback()
  assert await s.scalar(select(func.count()).select_from(m.ExecutionOperationModel).where(m.ExecutionOperationModel.status=='phase_handoff'))==0

@pytest.mark.parametrize('fault',['canonical','intent','selection','quality_unknown','quality_rejected','quality_wrong_project','quality_unsettled','pending','unknown','active_writer','other_stop','ancestor_changed','forged_receipt','missing_projection_receipt'])
async def test_unverified_dependency_and_unknown_never_authorize_transition(store,tmp_path,fault):
 x=await ready(store,tmp_path)
 async with store.sessions() as s:
  if fault in ('forged_receipt','missing_projection_receipt'):
   r=await h.create(s,task_id=x.task,payload=x.payload);x.payload['_execution_phase_handoff']=r['receipt_key'];await s.commit()
  p=await s.get(m.ProjectModel,store.pid)
  if fault=='canonical':
   snap=await s.get(m.PlanningArtifactVersionModel,x.sid);snap.content={**snap.content,'premise':'changed after review'}
  if fault=='intent':
   p.metadata_json={**p.metadata_json,'creation_intent_contract_hash':'changed'}
  if fault=='selection':p.metadata_json={**p.metadata_json,'reviewed_conception_selection':{}}
  if fault=='quality_unknown':(await s.get(m.ExecutionOperationModel,'actual-paid-fixture')).status='unknown'
  if fault=='quality_rejected':
   op=await s.get(m.ExecutionOperationModel,'actual-paid-fixture');op.result_json={**op.result_json,'content':'{"passed":false,"hard_conflicts":["unresolved"]}'}
  if fault=='quality_wrong_project':
   op=await s.get(m.ExecutionOperationModel,'actual-paid-fixture');run=await s.scalar(select(m.LlmRunModel).where(m.LlmRunModel.project_id==store.pid));run.project_id=uuid4()
  if fault=='quality_unsettled':
   a=await s.get(m.ExecutionAttemptModel,'fixture-attempt');a.actual_tokens=None
  if fault in ('pending','unknown'):
   s.add(m.ExecutionAttemptModel(id='in-flight',operation_key='pending-op',scope_id=x.scope,input_hash='pending',model_name=i.MODEL,status='reserved' if fault=='pending' else 'unknown',reserved_tokens=1000,actual_tokens=None,created_at=datetime.now(timezone.utc)))
  if fault=='active_writer':s.add(m.WorkflowRunModel(project_id=store.pid,workflow_type='test',status='running'))
  if fault=='other_stop':p.metadata_json={**p.metadata_json,'execution_terminal':{'code':'quality_failed'}}
  if fault=='ancestor_changed':(await s.get(m.ExecutionBudgetModel,'frozen-old')).tokens_charged+=1
  if fault=='forged_receipt':
   row=await s.get(m.ExecutionOperationModel,x.payload['_execution_phase_handoff']);row.result_json={**row.result_json,'binding':{}}
  if fault=='missing_projection_receipt':p.metadata_json={**p.metadata_json,'execution_phase_handoff_receipts':[]}
  await s.commit()
 async with store.sessions() as s:
  with pytest.raises((ValueError,ExecutionBudgetError)):
   if fault in ('forged_receipt','missing_projection_receipt'):await h.validate_binding(s,task_id=x.task,payload=x.payload)
   else:await h.create(s,task_id=x.task,payload=x.payload)
  await s.rollback()
  hold=await s.get(m.ExecutionAttemptModel,'frozen-hold');assert hold.status=='unknown' and hold.actual_tokens is None

async def test_manager_commits_receipt_before_single_same_task_dispatch_without_relocking(store,tmp_path,monkeypatch):
 import asyncio,threading
 from bestseller.web import server
 x=await ready(store,tmp_path)
 manager=server.WebTaskManager(persist_path=tmp_path/'phase-task-test.json')
 task=server.WebTaskState(task_id=x.task,task_type='autowrite',status='failed',created_at='2026-10-21T01:00:00+00:00',updated_at='2026-10-21T02:00:00+00:00',project_slug=store.slug,payload=deepcopy(x.payload),error='execution_continuation_binding_invalid',result={'execution_terminal':{'code':'execution_continuation_binding_invalid'}})
 manager._tasks[x.task]=task
 rebuilt={k:v for k,v in x.payload.items() if not k.startswith('_')}
 async def rebuild(settings,slug):assert slug==store.slug;return rebuilt
 monkeypatch.setattr(server,'_rebuild_payload_from_db',rebuild)
 monkeypatch.setattr(server,'_merge_resume_payload_with_project_truth',lambda old,new:{**old,**new})
 starts=[]
 # Keep threading itself real so the caller can prove it does not deadlock;
 # replace only server's reference, rather than the global threading module.
 monkeypatch.setattr(server,'threading',SimpleNamespace(Thread=lambda **kw:SimpleNamespace(start=lambda:starts.append(kw)),Lock=threading.Lock))
 queued=await asyncio.wait_for(asyncio.to_thread(manager.resume_approved_project,x.task),timeout=3)
 assert queued['task_id']==x.task and queued['status']=='queued' and queued['error'] is None and queued['result'] is None
 assert len(starts)==1 and starts[0]['args'][0]==x.task
 assert queued['payload']['_phase_handoff_control_history'][-1]['previous_task']['error']=='execution_continuation_binding_invalid'
 async with store.sessions() as s:
  assert await h.validate_binding(s,task_id=x.task,payload=queued['payload'])==x.scope
 with pytest.raises(ValueError,match='stopped original task'):manager.resume_approved_project(x.task)
 assert len(starts)==1

@pytest.mark.parametrize('fault',[None,'task','scope','intent','status'])
async def test_preproject_quality_requires_completed_same_task_same_intent_workflow(store,tmp_path,fault):
 x=await ready(store,tmp_path)
 async with store.sessions() as s:
  run=await s.scalar(select(m.LlmRunModel).where(m.LlmRunModel.project_id==store.pid))
  snap=await s.get(m.PlanningArtifactVersionModel,x.sid)
  wf=m.WorkflowRunModel(workflow_type='conception_initial',status='completed',metadata_json={'execution_scope_id':x.scope,'creation_intent_hash':snap.content['creation_intent_hash']})
  s.add(wf);await s.flush()
  run.project_id=None;run.metadata_json={**run.metadata_json,'conception_task_id':x.task,'paid_call_origin':{'workflow_run_id':str(wf.id)}}
  if fault=='task':run.metadata_json={**run.metadata_json,'conception_task_id':'another'}
  if fault=='scope':wf.metadata_json={**wf.metadata_json,'execution_scope_id':'another'}
  if fault=='intent':wf.metadata_json={**wf.metadata_json,'creation_intent_hash':'another'}
  if fault=='status':wf.status='failed'
  await s.commit()
 async with store.sessions() as s:
  if fault:
   with pytest.raises(ValueError,match='actual_quality_receipt_required'):await h.create(s,task_id=x.task,payload=x.payload)
  else:
   receipt=await h.create(s,task_id=x.task,payload=x.payload)
   assert receipt['binding']['quality_origin_workflow_id']==str(wf.id)

async def test_page_phase_button_uses_explicit_route_and_never_blindly_uses_old_recovery():
 from pathlib import Path
 import subprocess,json
 html=Path('src/bestseller/web/novel_quickstart.html').read_text()
 start=html.index("    const continuationBox = $('#pvReviewedContinuation');")
 end=html.index('    // Stats',start)
 block=html[start:end]
 script="""
 const element=()=>({hidden:true,disabled:false,textContent:'',onclick:null});
 const nodes={};const $=x=>nodes[x]??(nodes[x]=element());
 const status='failed',taskId='original-task',data={error:'execution_continuation_binding_invalid',payload:{_run_conception:false}};
 const force=false,document={visibilityState:'visible'},window={__activePreviewTaskId:taskId};
 const reviewedContinuationBusy=new Set(),requests=[];let polls=0;
 const fetch=async(url,options)=>{requests.push([url,options?.method||'GET']);return {ok:true,json:async()=>({available:true})}};
 const startProgressPoll=id=>{if(id!==taskId)throw Error('wrong task');polls++};
 (async()=>{
 """+block+"""
 const label=$('#pvContinueReviewed').textContent,shown=!$('#pvReviewedContinuation').hidden;
 await Promise.all([$('#pvContinueReviewed').onclick(),$('#pvContinueReviewed').onclick()]);
 console.log(JSON.stringify({label,shown,requests,polls}));})();
 """
 out=subprocess.run(['/opt/homebrew/bin/node','-e',script],capture_output=True,text=True)
 assert out.returncode==0,out.stderr
 result=json.loads(out.stdout)
 assert result=={'label':'继续已批准项目','shown':True,'requests':[['/api/tasks/original-task/phase-handoff-authorization','GET'],['/api/tasks/original-task/continue-approved-project','POST']],'polls':1}
