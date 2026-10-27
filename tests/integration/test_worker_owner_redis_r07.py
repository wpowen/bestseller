"""Real Redis, unique prefixed keys and short TTLs; no production config/keys."""
import asyncio,os
from uuid import uuid4
import pytest
from redis.asyncio import Redis
from bestseller.infra.redis_locks import compare_owner_expire,compare_owner_delete
from bestseller.worker import tasks
from bestseller.services.progress_context import TaskCancelledError

pytestmark=[pytest.mark.integration,pytest.mark.asyncio]


class NamespacedRedis:
    def __init__(self,client,prefix):self.client=client;self.prefix=prefix;self.keys=set()
    def key(self,key):
        k=self.prefix+key;self.keys.add(k);return k
    async def set(self,key,value,**kw):return await self.client.set(self.key(key),value,**kw)
    async def get(self,key):return await self.client.get(self.key(key))
    async def pttl(self,key):return await self.client.pttl(self.key(key))
    async def scan_iter(self,match):
        # Scan only the isolated prefix; expose logical keys to native reader.
        async for key in self.client.scan_iter(match=self.prefix+match):
            assert key.startswith(self.prefix)
            yield key[len(self.prefix):]
    async def eval(self,script,numkeys,key,*args):
        assert numkeys==1
        return await self.client.eval(script,1,self.key(key),*args)


@pytest.fixture
async def redis():
    port=os.environ.get('BESTSELLER_TEST_LOCK_REDIS_PORT')
    if not port:pytest.skip('explicit isolated Redis test port required')
    assert 1024<int(port)<65536
    client=Redis(host='127.0.0.1',port=int(port),decode_responses=True)
    prefix='r07:test:'+uuid4().hex+':'
    wrapper=NamespacedRedis(client,prefix)
    try:
        assert await client.ping()
        yield wrapper
    finally:
        for key in wrapper.keys:
            assert key.startswith(prefix)
            await client.delete(key)
        await client.aclose()


async def test_real_expiry_replacement_old_token_cannot_renew_or_delete(redis):
    key=tasks._pipeline_live_key('fixture')
    assert await redis.set(key,'old',nx=True,px=80)
    async with asyncio.timeout(2):
        while await redis.get(key) is not None:await asyncio.sleep(.01)
    assert await redis.set(key,'new',nx=True,px=2000)
    assert await compare_owner_expire(redis,key,'old',10) is False
    assert await compare_owner_delete(redis,key,'old') is False
    assert await tasks._renew_pipeline_live_once(redis,'fixture','old') is False
    await tasks._release_pipeline_live(redis,'fixture','old')
    assert await redis.get(key)=='new' and 0<await redis.pttl(key)<=2000
    assert await compare_owner_expire(redis,key,'new',2) is True
    assert await compare_owner_delete(redis,key,'new') is True
    print({'real_redis':True,'actual_expiry_then_replacement':True,'old_token_mutations_refused':True,'keys':'unique r07:test prefix only'})


async def test_real_guard_loss_stops_old_body_and_preserves_successor(redis,monkeypatch):
    key=tasks._pipeline_live_key('fixture');await redis.set(key,'old',px=2000)
    monkeypatch.setattr(tasks,'_PIPELINE_LIVE_RENEW_SECONDS',.01)
    finished=asyncio.Event()
    async def body():
        try:
            async with tasks._pipeline_live_guard(redis,'fixture','old','acquired'):
                await redis.set(key,'new',px=2000)
                await asyncio.Event().wait()
                raise AssertionError('old owner performed later action')
        except TaskCancelledError:finished.set()
    worker=asyncio.create_task(body())
    try:
        await asyncio.wait_for(finished.wait(),2);await worker
        assert await redis.get(key)=='new'
        assert 0<await redis.pttl(key)<=2000
    finally:
        if not worker.done():worker.cancel()
        await asyncio.gather(worker,return_exceptions=True)
    print({'real_redis':True,'local_executor_stopped':True,'successor_lease_preserved':True,'scope_stop_written':False})


async def test_actual_acquire_normal_producer_expiry_and_pg_reap(redis,postgresql_test_settings):
    """No owner metadata injected: actual normal producer must write the binding."""
    from datetime import UTC,datetime,timedelta
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from sqlalchemy.schema import CreateTable
    from bestseller.infra.db.models import ProjectModel,WorkflowRunModel
    from bestseller.services.budget import bind_execution_scope
    from bestseller.services.workflows import create_workflow_run
    from bestseller.domain.enums import WorkflowStatus
    from bestseller.worker import self_heal
    url=make_url(postgresql_test_settings.database.url);assert url.database=='bestseller_repair_test_20261015'
    schema='r07_producer_test_'+uuid4().hex
    engine=create_async_engine(url,connect_args={'server_settings':{'search_path':schema}})
    try:
        async with engine.begin() as c:
            assert await c.scalar(text('select current_database()'))==url.database
            await c.execute(text('CREATE SCHEMA '+schema))
            for model in (ProjectModel,WorkflowRunModel):await c.execute(CreateTable(model.__table__,include_foreign_key_constraints=[]))
        factory=async_sessionmaker(engine,expire_on_commit=False)
        async with factory() as s:
            p=ProjectModel(id=uuid4(),slug='real-producer-test-'+uuid4().hex,title='synthetic',genre='test',target_chapters=4,
                target_word_count=8000,metadata_json={})
            s.add(p);await s.commit()
            token=uuid4().hex;key=tasks._pipeline_live_key(p.slug)
            assert await tasks._acquire_pipeline_live(redis,p.slug,token)=='acquired'
            with bind_execution_scope(f'project:{p.id}'):
                with pytest.raises(TaskCancelledError):
                    async with tasks._pipeline_live_guard(redis,p.slug,token,'acquired'):
                        run=await create_workflow_run(s,project_id=p.id,workflow_type='project_pipeline',status=WorkflowStatus.RUNNING,
                            scope_type='project',scope_id=p.id,requested_by='synthetic-native-test',metadata={'retained':'unchanged'})
                        await s.commit()
                        assert run.metadata_json['execution_owner']==token
                        assert run.metadata_json['native_pipeline_owner']['project_id']==str(p.id)
                        assert await redis.client.pexpire(redis.key(key),80)
                        async with asyncio.timeout(2):
                            while await redis.get(key) is not None:await asyncio.sleep(.01)
                        # Guard's exit recheck observes the actual lost lease.
            run.created_at=datetime.now(UTC)-timedelta(hours=5)
            run.updated_at=run.created_at;await s.commit()
            assert await redis.get(key) is None
            assert await self_heal.reap_orphan_workflow_runs(s,redis=redis)==1
            await s.commit()
            rid=run.id
        async with factory() as check:
            run=await check.get(WorkflowRunModel,rid)
            assert run.status=='failed' and run.metadata_json['execution_owner']==token
            assert run.metadata_json['retained']=='unchanged'
            assert run.metadata_json['orphan_recovery']['execution_owner']==token
        print({'real_redis_and_pg':True,'normal_workflow_producer':True,'actual_acquired_owner_written':True,
               'actual_lease_expired':True,'stale_native_owner_reaped':True,'external_owner_metadata_used':False})
    finally:
        async with engine.begin() as c:await c.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))
        await engine.dispose()
