"""Real SQLite checkpoint/readback and normal terminal-entry regressions."""
from copy import deepcopy
from uuid import uuid4,UUID
import pytest
from sqlalchemy import MetaData,JSON,ForeignKeyConstraint,select,func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from bestseller.infra.db.models import ProjectModel,WorkflowRunModel,ChapterModel,ChapterDraftVersionModel
from bestseller.services.autonomous_continuation import record_window_continuation,next_window_proposal,is_partial_window_result

pytestmark=pytest.mark.unit

@pytest.fixture
async def db(tmp_path):
 engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'autonomous_book_test.sqlite3'))
 meta=MetaData()
 for model in (ProjectModel,WorkflowRunModel,ChapterModel,ChapterDraftVersionModel):
  table=model.__table__.to_metadata(meta)
  for col in table.columns:
   if isinstance(col.type,JSONB):col.type=JSON();col.server_default=None
  for constraint in list(table.constraints):
   if isinstance(constraint,ForeignKeyConstraint):table.constraints.remove(constraint)
 async with engine.begin() as conn:
  for table in meta.tables.values():await conn.run_sync(table.create)
 yield async_sessionmaker(engine,expire_on_commit=False)
 await engine.dispose()

async def seed(session,*,end=4,target=20):
 p=ProjectModel(id=uuid4(),slug='unrelated-new-serial',title='河边旧站',genre='日常',language='zh-CN',target_chapters=target,target_word_count=target*2000,status='writing',project_type='linear',metadata_json={})
 w={'schema_version':'execution-window.v1','purpose':'opening_window','start':1,'end':end}
 from bestseller.services.creation_configuration import configuration_metadata
 p.metadata_json=configuration_metadata({}, {'configuration':{'book':{'total_chapters':target,'chapter_target':2000}},'execution_window':w})
 session.add(p);await session.flush()
 sources=[]
 from bestseller.services.feedback import validate_adopted_knowledge_source
 for number in range(1,end+1):
  ch=ChapterModel(id=uuid4(),project_id=p.id,volume_id=uuid4(),chapter_number=number,title='历史章'+str(number),chapter_goal='模拟已采用的历史事件',status='completed',metadata_json={})
  d=ChapterDraftVersionModel(id=uuid4(),project_id=p.id,chapter_id=ch.id,version_no=1,content_md='模拟已采用正文'+str(number),word_count=8,is_current=True,promotion_state='promoted',assembled_from_scene_draft_ids=[])
  session.add_all([ch,d]);await session.flush()
  lineage,error=await validate_adopted_knowledge_source(session,project_id=p.id,chapter_id=ch.id,source_chapter_draft_version_id=d.id,chapter_md=d.content_md,lock=False)
  assert error is None
  ch.metadata_json={'knowledge_feedback_receipts':[{'status':'empty','source_lineage':lineage}]}
  sources.append({'chapter_number':number,**lineage})
 receipt={'sources':sources,'window':w,'whole_book_chapters':target,'window_complete':True,'whole_book_complete':False,'review_debt':[],'project_review_debt':False,'adopted':list(range(1,end+1)),'knowledge_closed':list(range(1,end+1)),'pending':[]}
 upstream=WorkflowRunModel(id=uuid4(),project_id=p.id,workflow_type='project_pipeline',scope_type='project',scope_id=p.id,status='completed',current_step='execution_window_completed',metadata_json={'execution_window_receipt':receipt})
 session.add_all([p,upstream]);await session.commit()
 result={'project_id':str(p.id),'project_slug':p.slug,'project_workflow_run_id':str(upstream.id),'execution_window':w,'whole_book_complete':False,'final_verdict':'execution_window_completed','requires_human_review':False}
 return p,upstream,result

@pytest.mark.asyncio
async def test_real_checkpoint_reopens_idempotently_without_window_or_story_mutation(db):
 async with db() as session:
  p,up,result=await seed(session);pid=p.id;uid=up.id;before=deepcopy(up.metadata_json)
  first=await record_window_continuation(session,project_id=pid,result=result);await session.commit()
  assert first['next_window_proposal']=={'schema_version':'execution-window.v1','purpose':'continuation','start':5,'end':8}
  assert first['automatic_dispatch_started'] is False
 async with db() as reopened:
  same=await record_window_continuation(reopened,project_id=pid,result=result);await reopened.commit()
  assert same==first
  assert await reopened.scalar(select(func.count()).select_from(WorkflowRunModel).where(WorkflowRunModel.workflow_type=='autonomous_continuation'))==1
  original=await reopened.get(WorkflowRunModel,uid);project=await reopened.get(ProjectModel,pid)
  assert original.metadata_json==before and project.metadata_json['execution_window']==result['execution_window'] and project.target_chapters==20
  row=await reopened.get(WorkflowRunModel,UUID(first['continuation_workflow_run_id']))
  assert row.current_step=='continuation_planning_required' and row.metadata_json['provider_dispatch_authorized'] is False

@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['review','unproven','foreign','changed_extent'])
async def test_review_unknown_or_unbound_window_does_not_dispatch_or_close_book(db,bad):
 async with db() as session:
  p,up,result=await seed(session)
  if bad=='review':result['requires_human_review']=True
  if bad=='unproven':up.metadata_json={'execution_window_receipt':{}};await session.commit()
  if bad=='foreign':result['project_id']=str(uuid4())
  if bad=='changed_extent':result['execution_window']['end']=6
  if bad=='review':assert await record_window_continuation(session,project_id=p.id,result=result) is None
  else:
   with pytest.raises(ValueError):await record_window_continuation(session,project_id=p.id,result=result)
  assert await session.scalar(select(func.count()).select_from(WorkflowRunModel).where(WorkflowRunModel.workflow_type=='autonomous_continuation'))==0

@pytest.mark.asyncio
async def test_full_chapter_coverage_still_requires_book_review_not_fake_completion(db):
 async with db() as session:
  p,up,result=await seed(session,end=20)
  next_step=await record_window_continuation(session,project_id=p.id,result=result)
  assert next_step['next_window_proposal'] is None and next_step['next_stage']=='whole_book_review_required'
  assert next_step['whole_book_complete'] is False

@pytest.mark.parametrize('window,target',[({'start':1,'end':21},20),({'start':0,'end':4},20),({'start':True,'end':4},20)])
def test_invalid_extent_never_expands_book(window,target):
 with pytest.raises(ValueError):next_window_proposal(window=window,target_chapters=target)


def test_normal_web_terminal_entry_does_not_claim_partial_window_whole_book_done(tmp_path):
 from bestseller.web.server import WebTaskManager,WebTaskState
 path=tmp_path/'task_state.json';manager=WebTaskManager(persist_path=path)
 manager._tasks['fixture']=WebTaskState(task_id='fixture',task_type='autowrite',status='running',created_at='2026-10-20T00:00:00+00:00',updated_at='2026-10-20T00:00:00+00:00',project_slug='unrelated-new-serial',title='河边旧站',current_stage='writing')
 result={'execution_window':{'start':1,'end':4},'whole_book_complete':False,'final_verdict':'execution_window_completed','requires_human_review':False}
 manager._mark_completed('fixture',result)
 task=manager.get_task('fixture');assert task['status']=='incomplete' and task['current_stage']=='continuation_required' and task['error'] is None
 assert 'execution_window_completed' in path.read_text() and is_partial_window_result(result)

@pytest.mark.asyncio
async def test_normal_worker_partial_event_does_not_enqueue_full_book_benchmark(monkeypatch):
 from bestseller.worker import tasks
 events=[]
 class Reporter:
  async def emit(self,stage,payload,**kwargs):events.append((stage,payload,kwargs))
 async def forbidden(*args,**kwargs):raise AssertionError('Partial window is not a finished book')
 monkeypatch.setattr(tasks,'_enqueue_benchmark_regression_if_needed',forbidden)
 await tasks._emit_terminal_pipeline_event(Reporter(),{'execution_window':{'start':1,'end':4},'whole_book_complete':False,'requires_human_review':False},completed_result='done',attention_reason='attention')
 assert [e[0] for e in events]==['continuation_required']
 assert events[0][1]['whole_book_complete'] is False

class Queue:
 def __init__(self):self.jobs={};self.calls=0
 async def enqueue_job(self,name,**kwargs):
  self.calls+=1
  self.jobs.setdefault(kwargs['_job_id'],(name,kwargs))
 async def exists(self,*keys):
  return sum(key.removeprefix('arq:job:') in self.jobs for key in keys if key.startswith('arq:job:'))

@pytest.mark.asyncio
async def test_authorized_next_action_enqueued_once_and_restart_reads_same_stage(db):
 from bestseller.services.autonomous_dispatch import enqueue_checkpoint,execute_checkpoint,recover_pending_checkpoints
 queue=Queue()
 async with db() as session:
  p,up,result=await seed(session)
  c=await record_window_continuation(session,project_id=p.id,result=result,dispatch_authorized=True)
  await session.commit();cid=c['continuation_workflow_run_id']
  first=await enqueue_checkpoint(session,queue,cid)
  again=await enqueue_checkpoint(session,queue,cid)
  assert first['dispatched'] is True and again['dispatched'] is False
  assert len(queue.jobs)==1 and queue.calls==1
  row=await session.get(WorkflowRunModel,UUID(cid));row.status='running'
  row.metadata_json={**row.metadata_json,'execution_owner':'stopped-worker','stage_started':None,'stage_outputs':{'plan':{'simulated':True,'from_chapter':5,'through_chapter':8}}}
  await session.commit()
 async with db() as reopened:
  await recover_pending_checkpoints(reopened,queue)
  row=await reopened.get(WorkflowRunModel,UUID(cid))
  assert row.status=='pending' and row.metadata_json['stage_outputs']['plan']['simulated'] is True
  assert len(queue.jobs)==2 # new cursor, same persisted book/checkpoint
  called=[]
  def stage(name):
   async def run(session,project,row,outputs):
    called.append(name)
    if name=='activate':
     from bestseller.services.creation_configuration import configuration_metadata
     cfg=project.metadata_json['creation_configuration']
     project.metadata_json=configuration_metadata(project.metadata_json,{'configuration':cfg,'execution_window':{'schema_version':'execution-window.v1','purpose':'continuation','start':outputs['plan']['from_chapter'],'end':outputs['plan']['through_chapter']}})
    return {'simulated':True}
   return run
  async def permit(*args):return True,None
  receipts=await execute_checkpoint(reopened,cid,handlers={n:stage(n) for n in ['plan','admit','materialize','activate','write']},permit=permit,owner_id='new-worker',resume_after_restart=True)
  assert called==['admit','materialize','activate','write']
  assert receipts['whole_book_complete'] is False
 async with db() as reopened:
  called.clear();await execute_checkpoint(reopened,cid,handlers={},permit=permit)
  assert called==[]
  assert await reopened.scalar(select(func.count()).select_from(ProjectModel))==1

@pytest.mark.asyncio
async def test_denied_egress_never_runs_any_production_handler_or_queues_model_work(db):
 from bestseller.services.autonomous_dispatch import enqueue_checkpoint,execute_checkpoint
 queue=Queue()
 async with db() as session:
  p,up,result=await seed(session)
  c=await record_window_continuation(session,project_id=p.id,result=result)
  await session.commit()
  assert (await enqueue_checkpoint(session,queue,c['continuation_workflow_run_id']))['blocked_reason']=='external_authorization_required'
  async def deny(*args):return False,'external_authorization_required'
  async def forbidden(*args):raise AssertionError('No provider or handler may run')
  receipt=await execute_checkpoint(session,c['continuation_workflow_run_id'],handlers={'plan':forbidden},permit=deny)
  assert receipt['state']=='machine_blocked' and not queue.jobs

@pytest.mark.asyncio
async def test_true_hard_failure_stops_and_retains_prior_stage_without_auto_retry(db):
 from bestseller.services.autonomous_dispatch import execute_checkpoint
 async with db() as session:
  p,up,result=await seed(session)
  c=await record_window_continuation(session,project_id=p.id,result=result,dispatch_authorized=True);await session.commit()
  called=[]
  async def permit(*args):return True,None
  async def plan(*args):called.append('plan');return {'simulated':True}
  async def reject(*args):called.append('admit');return {'blocked_reason':'real_hard_source_contradiction'}
  receipt=await execute_checkpoint(session,c['continuation_workflow_run_id'],handlers={'plan':plan,'admit':reject},permit=permit)
  assert receipt['state']=='machine_blocked' and called==['plan','admit']
  again=await execute_checkpoint(session,c['continuation_workflow_run_id'],handlers={},permit=permit)
  assert again['state']=='machine_blocked' and called==['plan','admit']
  row=await session.get(WorkflowRunModel,UUID(c['continuation_workflow_run_id']))
  assert row.metadata_json['stage_outputs']['plan']=={'simulated':True}

@pytest.mark.asyncio
async def test_interrupted_unknown_stage_is_reconciled_before_any_resend(db):
 from bestseller.services.autonomous_dispatch import recover_pending_checkpoints
 queue=Queue()
 async with db() as session:
  p,up,result=await seed(session);c=await record_window_continuation(session,project_id=p.id,result=result,dispatch_authorized=True);await session.commit()
  row=await session.get(WorkflowRunModel,UUID(c['continuation_workflow_run_id']))
  row.status='running';row.metadata_json={**row.metadata_json,'execution_owner':'dead','stage_started':'admit'};await session.commit()
 async with db() as reopened:
  receipts=await recover_pending_checkpoints(reopened,queue)
  assert receipts[0]['blocked_reason']=='interrupted_stage_requires_reconciliation' and not queue.jobs

@pytest.mark.asyncio
async def test_last_window_runs_review_then_export_before_complete(db):
 from bestseller.services.autonomous_dispatch import execute_checkpoint
 async with db() as session:
  p,up,result=await seed(session,end=20);c=await record_window_continuation(session,project_id=p.id,result=result,dispatch_authorized=True);await session.commit()
  async def permit(*args):return True,None
  async def final(*args):return {'simulated':True,'whole_book_complete':True,'export_artifact_id':str(uuid4()),'output_path':'/fixture-only/book.md'}
  receipt=await execute_checkpoint(session,c['continuation_workflow_run_id'],handlers={'review_and_export':final},permit=permit)
  assert receipt['whole_book_complete'] is True and receipt['stage_outputs']['review_and_export']['simulated'] is True


@pytest.mark.asyncio
async def test_replayed_normal_entry_keeps_dispatched_checkpoint_cursor(db):
 async with db() as session:
  p,up,result=await seed(session)
  first=await record_window_continuation(session,project_id=p.id,result=result,dispatch_authorized=True)
  await session.commit()
  row=await session.get(WorkflowRunModel,UUID(first['continuation_workflow_run_id']))
  row.status='running';row.current_step='admit'
  row.metadata_json={**row.metadata_json,'queued_job_id':'existing-job','stage_started':'admit','stage_outputs':{'plan':{'from_chapter':5,'through_chapter':6}},'execution_owner':'original-worker'}
  await session.commit();before=deepcopy(row.metadata_json)
  again=await record_window_continuation(session,project_id=p.id,result=result,dispatch_authorized=True)
  assert again==first and row.status=='running' and row.current_step=='admit' and row.metadata_json==before
  assert await session.scalar(select(func.count()).select_from(WorkflowRunModel).where(WorkflowRunModel.workflow_type=='autonomous_continuation'))==1

@pytest.mark.asyncio
@pytest.mark.parametrize('change,reason',[
 ('receipt','continuation_upstream_receipt_changed'),
 ('body','continuation_prior_body_or_knowledge_changed'),
 ('knowledge','continuation_prior_body_or_knowledge_changed'),
 ('window','continuation_project_window_changed'),
])
async def test_durable_checkpoint_revalidates_upstream_before_any_handler(db,change,reason):
 from bestseller.services.autonomous_dispatch import execute_checkpoint
 async with db() as session:
  p,up,result=await seed(session)
  c=await record_window_continuation(session,project_id=p.id,result=result,dispatch_authorized=True)
  pid=p.id;uid=up.id;await session.commit()
 async with db() as changed:
  if change=='receipt':
   up=await changed.get(WorkflowRunModel,uid);meta=deepcopy(up.metadata_json);meta['execution_window_receipt']['pending']=[999];up.metadata_json=meta
  elif change=='window':
   from bestseller.services.creation_configuration import configuration_metadata
   p=await changed.get(ProjectModel,pid);meta=deepcopy(p.metadata_json);window={**meta['execution_window'],'end':3}
   p.metadata_json=configuration_metadata(meta,{'configuration':meta['creation_configuration'],'execution_window':window})
  else:
   ch=await changed.scalar(select(ChapterModel).where(ChapterModel.project_id==pid,ChapterModel.chapter_number==1))
   if change=='knowledge':ch.metadata_json={'knowledge_feedback_receipts':[]}
   else:
    d=await changed.scalar(select(ChapterDraftVersionModel).where(ChapterDraftVersionModel.chapter_id==ch.id));d.content_md+='真实变更'
  await changed.commit()
 async with db() as reopened:
  called=[]
  async def permit(*args):return True,None
  async def forbidden(*args):called.append('provider');raise AssertionError('stale source dispatched')
  receipt=await execute_checkpoint(reopened,c['continuation_workflow_run_id'],handlers={'plan':forbidden},permit=permit)
  assert receipt['state']=='machine_blocked' and receipt['blocked_reason']==reason and called==[]
  row=await reopened.get(WorkflowRunModel,UUID(c['continuation_workflow_run_id']))
  assert row.metadata_json.get('stage_outputs',{})=={} and row.metadata_json.get('stage_started') is None


def test_narrowed_rolling_window_preserves_original_continuation_batch_size():
 assert next_window_proposal(window={'start':5,'end':6},target_chapters=20,batch_size=4)['end']==10
 with pytest.raises(ValueError,match='batch_size_invalid'):
  next_window_proposal(window={'start':5,'end':6},target_chapters=20,batch_size=0)

@pytest.mark.asyncio
async def test_two_workers_cannot_execute_same_checkpoint_handlers(db):
 import asyncio
 from bestseller.services.autonomous_dispatch import execute_checkpoint
 async with db() as session:
  p,up,result=await seed(session)
  c=await record_window_continuation(session,project_id=p.id,result=result,dispatch_authorized=True)
  await session.commit()
 entered=asyncio.Event();release=asyncio.Event();calls=[]
 async def permit(*args):return True,None
 async def paused_plan(*args):
  calls.append('plan');entered.set();await release.wait()
  return {'blocked_reason':'fixture_stop_after_single_owner_proof'}
 async def owner():
  async with db() as session:
   return await execute_checkpoint(session,c['continuation_workflow_run_id'],handlers={'plan':paused_plan},permit=permit,owner_id='worker-a')
 running=asyncio.create_task(owner())
 try:
  await asyncio.wait_for(entered.wait(),timeout=5)
  async with db() as other:
   result=await execute_checkpoint(other,c['continuation_workflow_run_id'],handlers={'plan':paused_plan},permit=permit,owner_id='worker-b')
   assert result['state']=='already_running' and calls==['plan']
 finally:
  release.set();await asyncio.wait_for(running,timeout=5)
 assert calls==['plan']


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['goal_changed','missing_handler'])
async def test_invalid_checkpoint_cannot_become_infinite_restart_loop(db,fault):
 from bestseller.services.autonomous_dispatch import execute_checkpoint,recover_pending_checkpoints
 async with db() as session:
  p,up,result=await seed(session)
  c=await record_window_continuation(session,project_id=p.id,result=result,dispatch_authorized=True)
  await session.commit()
  if fault=='goal_changed':p.target_chapters=21;await session.commit()
  async def permit(*args):return True,None
  stopped=await execute_checkpoint(session,c['continuation_workflow_run_id'],handlers={},permit=permit)
  assert stopped['state']=='machine_blocked'
  assert stopped['blocked_reason']==('continuation_goal_changed' if fault=='goal_changed' else 'continuation_handler_missing:plan')
 async with db() as reopened:
  queue=Queue();assert await recover_pending_checkpoints(reopened,queue)==[] and not queue.jobs
  again=await execute_checkpoint(reopened,c['continuation_workflow_run_id'],handlers={},permit=permit)
  assert again==stopped

@pytest.mark.asyncio
@pytest.mark.parametrize('has_arq',[True,False])
async def test_normal_worker_restart_preserves_queue_capable_client_and_drains_outbox(monkeypatch,has_arq):
 from contextlib import asynccontextmanager
 from bestseller.worker import main
 from bestseller.infra.db import session as db_session
 from bestseller.services import autonomous_dispatch
 import arq.connections
 from bestseller.settings import load_settings
 settings=load_settings(env={});settings.pipeline.enable_material_library=False
 monkeypatch.setenv('WORKER_SELF_HEAL','0')
 monkeypatch.setattr(main,'get_settings',lambda:settings)
 async def noop(*args,**kwargs):pass
 for name in ('init_db','init_redis','shutdown_db','shutdown_redis'):monkeypatch.setattr(main,name,noop)
 @asynccontextmanager
 async def scope(*args):yield object()
 monkeypatch.setattr(db_session,'session_scope',scope)
 class Arq:
  def __init__(self):self.closed=False
  async def enqueue_job(self,*args,**kwargs):pass
  async def aclose(self):self.closed=True
 pool=Arq();seen=[];created=[]
 async def create_pool(*args):created.append(True);return pool
 async def drain(session,redis):seen.append(redis);return []
 monkeypatch.setattr(arq.connections,'create_pool',create_pool)
 monkeypatch.setattr(autonomous_dispatch,'recover_pending_checkpoints',drain)
 ctx={'redis':pool if has_arq else object()}
 await main.startup(ctx)
 assert ctx['redis'] is pool and seen==[pool] and len(created)==(0 if has_arq else 1)
 await main.shutdown(ctx)
 assert pool.closed is (not has_arq)
