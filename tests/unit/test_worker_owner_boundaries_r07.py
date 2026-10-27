"""R07 native owner fences. Fixtures are isolated SQL/Redis stand-ins, no send."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC,datetime,timedelta
from types import SimpleNamespace
from uuid import uuid4
import pickle

import pytest
from sqlalchemy import JSON,MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from sqlalchemy.schema import CreateTable
from bestseller.infra.db.models import ProjectModel,WorkflowRunModel
from bestseller.services.progress_context import TaskCancelledError,set_cancellation_checker
from bestseller.worker import tasks,self_heal as heal

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


class Redis:
    def __init__(self):self.values={};self.ttls={};self.fail=None
    async def get(self,key):
        if self.fail=='get':raise ConnectionError('synthetic')
        return self.values.get(key)
    async def pttl(self,key):
        if self.fail=='pttl':raise ConnectionError('synthetic')
        return self.ttls.get(key,-2)
    async def set(self,key,value,**kw):self.values[key]=value;self.ttls[key]=int(kw.get('ex',1))*1000;return True
    async def eval(self,script,numkeys,key,owner,*args):
        if self.fail=='eval':raise ConnectionError('synthetic')
        if self.values.get(key)!=owner:return 0
        if "'DEL'" in script:self.values.pop(key,None);self.ttls.pop(key,None);return 1
        self.ttls[key]=int(args[0])*1000;return 1
    async def scan_iter(self,match):
        import fnmatch
        if self.fail=='scan':raise ConnectionError('synthetic')
        for key in list(self.values):
            if fnmatch.fnmatch(key,match):yield key


@pytest.fixture
async def db(tmp_path):
    path=tmp_path/'owner_r07_test.sqlite3';engine=create_async_engine('sqlite+aiosqlite:///'+str(path))
    def ddl(c):
        for model in (ProjectModel,WorkflowRunModel):
            table=model.__table__.to_metadata(MetaData())
            for col in table.c:
                if isinstance(col.type,JSONB):col.type=JSON()
                if col.server_default and '::jsonb' in str(col.server_default.arg):col.server_default=None
            c.execute(CreateTable(table,include_foreign_key_constraints=[]))
    async with engine.begin() as c:await c.run_sync(ddl)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    p=ProjectModel(id=uuid4(),slug='owner-test-'+uuid4().hex,title='fixture',genre='test',target_chapters=4,target_word_count=8000,metadata_json={})
    old=datetime.now(UTC)-timedelta(hours=5)
    redis=Redis();token=uuid4().hex
    from bestseller.services.budget import bind_execution_scope
    from bestseller.services.workflows import create_workflow_run
    from bestseller.domain.enums import WorkflowStatus
    async with factory() as s:
        s.add(p);await s.commit()
        assert await tasks._acquire_pipeline_live(redis,p.slug,token)=='acquired'
        with bind_execution_scope(f'project:{p.id}'):
            async with tasks._pipeline_live_guard(redis,p.slug,token,'acquired'):
                r=await create_workflow_run(s,project_id=p.id,workflow_type='project_pipeline',status=WorkflowStatus.RUNNING,
                    scope_type='project',scope_id=p.id,requested_by='synthetic-native-test',metadata={'retained':{'calls':290}})
                await s.commit()
        r.created_at=old;r.updated_at=old;await s.commit()
        try:yield SimpleNamespace(session=s,project=p,run=r,redis=redis,factory=factory)
        finally:await s.rollback()
    await engine.dispose()


@pytest.mark.parametrize('kind',['project_pipeline','conception_initial','scene_pipeline'])
@pytest.mark.parametrize('protection',['fresh_heartbeat','live_lease','legacy_owner','probe_unknown'])
async def test_every_branch_protects_live_or_unknown_owner(db,kind,protection):
    r=db.run;r.workflow_type=kind
    if kind=='scene_pipeline':
        parent=WorkflowRunModel(id=uuid4(),project_id=db.project.id,workflow_type='project_pipeline',status='completed',metadata_json={})
        db.session.add(parent);r.metadata_json={**r.metadata_json,'parent_workflow_run_id':str(parent.id)}
    if protection=='fresh_heartbeat':r.updated_at=datetime.now(UTC)
    if protection=='legacy_owner':r.metadata_json={k:v for k,v in r.metadata_json.items() if k!='execution_owner'}
    if protection=='live_lease':await db.redis.set(tasks._pipeline_live_key(db.project.slug),'new-owner',ex=3)
    if protection=='probe_unknown':db.redis.fail='get'
    if protection!='fresh_heartbeat':r.updated_at=datetime.now(UTC)-timedelta(hours=5)
    await db.session.commit()
    assert await heal.reap_orphan_workflow_runs(db.session,startup_cutoff=datetime.now(UTC),redis=db.redis)==0
    assert r.status=='running' and r.metadata_json['retained']=={'calls':290}


@pytest.mark.parametrize('kind',['project_pipeline','conception_initial','scene_pipeline'])
async def test_expired_bound_owner_can_be_reaped_only_with_same_project_lineage(db,kind):
    db.run.workflow_type=kind
    if kind=='scene_pipeline':
        parent=WorkflowRunModel(id=uuid4(),project_id=db.project.id,workflow_type='project_pipeline',status='failed',metadata_json={})
        db.session.add(parent);db.run.metadata_json={**db.run.metadata_json,'parent_workflow_run_id':str(parent.id)}
    db.run.updated_at=datetime.now(UTC)-timedelta(hours=5)
    await db.session.commit()
    assert await heal.reap_orphan_workflow_runs(db.session,redis=db.redis)==1
    assert db.run.status=='failed' and db.run.metadata_json['retained']=={'calls':290}
    assert db.run.metadata_json['orphan_recovery']['observed']=='pipeline_lease_absent_arq_probe_complete'


async def test_foreign_child_parent_and_projectless_web_remain_unknown(db):
    parent=WorkflowRunModel(id=uuid4(),project_id=uuid4(),workflow_type='project_pipeline',status='failed',metadata_json={})
    db.session.add(parent);db.run.metadata_json={**db.run.metadata_json,'parent_workflow_run_id':str(parent.id)}
    db.run.workflow_type='scene_pipeline';db.run.updated_at=datetime.now(UTC)-timedelta(hours=5);await db.session.commit()
    assert await heal.reap_orphan_workflow_runs(db.session,redis=db.redis)==0
    db.run.project_id=None;db.run.workflow_type='conception_initial';db.run.updated_at=datetime.now(UTC)-timedelta(hours=5);await db.session.commit()
    assert await heal.reap_orphan_workflow_runs(db.session,redis=db.redis)==0
    assert db.run.status=='running'


async def test_created_before_boot_is_not_a_death_certificate(db):
    db.run.updated_at=datetime.now(UTC)-timedelta(minutes=10);await db.session.commit()
    assert await heal.reap_orphan_workflow_runs(db.session,startup_cutoff=datetime.now(UTC),redis=db.redis)==0
    assert db.run.status=='running'


@pytest.mark.parametrize('fail',['scan','get'])
async def test_arq_probe_failure_is_unknown_not_empty(fail):
    redis=Redis();redis.fail=fail
    if fail=='get':redis.values['arq:in-progress:fixture']=b'1'
    assert await heal._active_arq_project_slugs(redis) is None
    assert await heal._active_arq_project_slugs(None) is None


async def test_arq_unknown_payload_and_live_pipeline_are_conservative():
    redis=Redis();redis.values['arq:in-progress:fixture']=b'1'
    assert await heal._active_arq_project_slugs(redis) is None
    redis.values['arq:job:fixture']=pickle.dumps({'k':{'payload':{'project_slug':'fixture'}}})
    assert await heal._active_arq_project_slugs(redis)=={'fixture'}
    redis.values.clear();redis.values['bestseller:pipeline-live:long-owner']=b'owner'
    assert await heal._active_arq_project_slugs(redis)=={'long-owner'}


async def test_unknown_scan_prevents_recovery_dispatch(monkeypatch):
    redis=Redis();redis.fail='scan'
    @asynccontextmanager
    async def session():yield object()
    async def forbidden(*a,**k):raise AssertionError('UNKNOWN must not reach reap or dispatch')
    monkeypatch.setattr(heal,'get_server_session',session)
    monkeypatch.setattr(heal,'_try_acquire_heal_lock',lambda *a: async_true())
    monkeypatch.setattr(heal,'reap_orphan_workflow_runs',forbidden)
    monkeypatch.setattr(heal,'find_stuck_projects',forbidden)
    # Exercise probe + scanner before any recovery pool/provider can be made.
    assert await heal.heal_stuck_projects(SimpleNamespace(),redis=redis,worker_id='test-owner')==[]
    assert heal.SELF_HEAL_SCAN_DONE_KEY not in redis.values


async def async_true():return True


@pytest.mark.parametrize('fault',['replacement','eval_unavailable'])
async def test_paid_native_boundary_rejects_lost_owner_without_scope_stop(monkeypatch,fault):
    from bestseller.services import llm
    redis=Redis();key=tasks._pipeline_live_key('test-owner');await redis.set(key,'old',ex=3)
    async def forbidden(*a,**k):raise AssertionError('no project/scope write or admission allowed')
    monkeypatch.setattr(tasks,'get_server_session',forbidden)
    set_cancellation_checker(lambda:tasks._raise_if_project_halted('test-owner'))
    try:
        with pytest.raises(TaskCancelledError):
            async with tasks._pipeline_live_guard(redis,'test-owner','old','acquired'):
                if fault=='replacement':await redis.set(key,'new',ex=3)
                else:redis.fail='eval'
                await llm._execution_boundary()
        if fault=='replacement':assert redis.values[key]=='new'
    finally:set_cancellation_checker(None)


async def test_renewal_loss_cancels_only_former_executor(monkeypatch):
    redis=Redis();key=tasks._pipeline_live_key('test-owner');await redis.set(key,'old',ex=3)
    monkeypatch.setattr(tasks,'_PIPELINE_LIVE_RENEW_SECONDS',0.001)
    stopped=asyncio.Event()
    async def operation():
        try:
            async with tasks._pipeline_live_guard(redis,'test-owner','old','acquired'):
                await redis.set(key,'new',ex=3)
                await asyncio.Event().wait()
                raise AssertionError('lost owner continued')
        except TaskCancelledError:stopped.set()
    task=asyncio.create_task(operation());await asyncio.wait_for(stopped.wait(),1);await task
    assert redis.values[key]=='new'


async def test_degraded_ownership_never_enters_task_body():
    with pytest.raises(TaskCancelledError):
        async with tasks._pipeline_live_guard(Redis(),'test','old','degraded'):
            raise AssertionError('unowned body entered')


@pytest.mark.parametrize('field',['execution_owner','native_pipeline_owner'])
async def test_normal_producer_refuses_caller_supplied_owner_metadata(db,field):
    from bestseller.services.workflows import create_workflow_run
    from bestseller.domain.enums import WorkflowStatus
    from sqlalchemy import select,func
    before=await db.session.scalar(select(func.count()).select_from(WorkflowRunModel))
    with pytest.raises(ValueError,match='workflow_owner_metadata_not_accepted'):
        await create_workflow_run(db.session,project_id=db.project.id,workflow_type='chapter_pipeline',status=WorkflowStatus.RUNNING,
            scope_type='project',scope_id=db.project.id,requested_by='fixture',metadata={field:'caller-not-authority'})
    assert await db.session.scalar(select(func.count()).select_from(WorkflowRunModel))==before


async def test_normal_owner_is_native_and_legacy_uuid_alone_is_unknown(db):
    meta=db.run.metadata_json
    assert meta['native_pipeline_owner']['token']==meta['execution_owner']
    assert meta['native_pipeline_owner']['project_id']==str(db.project.id)
    db.run.metadata_json={k:v for k,v in meta.items() if k!='native_pipeline_owner'}
    db.run.updated_at=datetime.now(UTC)-timedelta(hours=5);await db.session.commit()
    assert await heal.reap_orphan_workflow_runs(db.session,redis=db.redis)==0


async def test_native_owner_context_cannot_bind_foreign_project(db):
    from bestseller.services.workflows import create_workflow_run
    from bestseller.services.budget import bind_execution_scope
    from bestseller.domain.enums import WorkflowStatus
    foreign=ProjectModel(id=uuid4(),slug='foreign-test',title='test',genre='test',target_chapters=1,target_word_count=8000,metadata_json={})
    db.session.add(foreign);await db.session.commit()
    token=uuid4().hex
    assert await tasks._acquire_pipeline_live(db.redis,db.project.slug,token)=='acquired'
    with bind_execution_scope(f'project:{db.project.id}'):
        async with tasks._pipeline_live_guard(db.redis,db.project.slug,token,'acquired'):
            with pytest.raises(ValueError,match='workflow_native_owner_binding_conflict'):
                await create_workflow_run(db.session,project_id=foreign.id,workflow_type='project_pipeline',status=WorkflowStatus.RUNNING,
                    scope_type='project',scope_id=foreign.id,requested_by='fixture')
