"""Actual isolated Web helper Redis/PG paths. No provider, production key or data."""
import asyncio,os
from datetime import UTC,datetime,timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.schema import CreateTable
from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db.models import ProjectModel,WorkflowRunModel
from bestseller.services.budget import bind_execution_scope
from bestseller.services.progress_context import _CANCELLATION_CHECKER,TaskCancelledError
from bestseller.services.web_pipeline_owner import web_pipeline_owner,WebPipelineOwnerUnavailable
from bestseller.services.workflows import create_workflow_run
from bestseller.worker import tasks,self_heal

pytestmark=[pytest.mark.integration,pytest.mark.asyncio]


@pytest.fixture
async def native(postgresql_test_settings):
    port=os.environ.get('BESTSELLER_TEST_LOCK_REDIS_PORT')
    if not port:pytest.fail('Explicit isolated Redis test port required')
    assert 1024<int(port)<65536
    url=make_url(postgresql_test_settings.database.url);assert url.database=='bestseller_repair_test_20261015'
    schema='web_owner_test_'+uuid4().hex
    engine=create_async_engine(url,connect_args={'server_settings':{'search_path':schema}})
    monitor=Redis(host='127.0.0.1',port=int(port),decode_responses=True)
    slug='web-owner-test-'+uuid4().hex
    key=tasks._pipeline_live_key(slug)
    assert ':web-owner-test-' in key
    settings=SimpleNamespace(redis=SimpleNamespace(url=f'redis://127.0.0.1:{port}/0',socket_timeout_seconds=1,
        socket_connect_timeout_seconds=1,pool_max_connections=2))
    try:
        assert await monitor.ping()
        async with engine.begin() as c:
            assert await c.scalar(text('SELECT current_database()'))=='bestseller_repair_test_20261015'
            await c.execute(text('CREATE SCHEMA '+schema))
            for m in (ProjectModel,WorkflowRunModel):await c.execute(CreateTable(m.__table__,include_foreign_key_constraints=[]))
        factory=async_sessionmaker(engine,expire_on_commit=False)
        p=ProjectModel(id=uuid4(),slug=slug,title='test',genre='test',target_chapters=4,target_word_count=8000,metadata_json={})
        async with factory() as s:s.add(p);await s.commit()
        yield SimpleNamespace(factory=factory,project=p,settings=settings,monitor=monitor,key=key)
    finally:
        assert ':web-owner-test-' in key
        await monitor.delete(key)  # Only this test's unique key; no FLUSH or production key.
        await monitor.aclose()
        async with engine.begin() as c:await c.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))
        await engine.dispose()


async def test_real_web_acquire_normal_workflow_expiry_and_native_reap(native):
    r=native;before=len(await r.monitor.client_list());prior=_CANCELLATION_CHECKER.get()
    with bind_execution_scope(f'project:{r.project.id}'):
        with pytest.raises(TaskCancelledError):
            async with web_pipeline_owner(r.settings,r.project.slug):
                token=await r.monitor.get(r.key)
                assert token and len(token)==32
                async with r.factory() as s:
                    wf=await create_workflow_run(s,project_id=r.project.id,workflow_type='project_pipeline',status=WorkflowStatus.RUNNING,
                        scope_type='project',scope_id=r.project.id,requested_by='web-native-fixture',metadata={'original':'retained'})
                    await s.commit();wid=wf.id
                    assert wf.metadata_json['execution_owner']==token
                    assert wf.metadata_json['native_pipeline_owner']['project_slug']==r.project.slug
                assert await r.monitor.pexpire(r.key,80)
                async with asyncio.timeout(2):
                    while await r.monitor.get(r.key) is not None:await asyncio.sleep(.01)
    assert _CANCELLATION_CHECKER.get() is prior
    assert len(await r.monitor.client_list())==before  # Helper's fresh pool closed.
    async with r.factory() as s:
        wf=await s.get(WorkflowRunModel,wid)
        wf.created_at=datetime.now(UTC)-timedelta(hours=5);wf.updated_at=wf.created_at
        await s.commit()
        assert await self_heal.reap_orphan_workflow_runs(s,redis=r.monitor)==1
        await s.commit()
    async with r.factory() as s:
        wf=await s.get(WorkflowRunModel,wid)
        assert wf.status=='failed' and wf.metadata_json['execution_owner']==token
        assert wf.metadata_json['original']=='retained'
    print({'actual_web_helper':True,'fresh_client_closed':True,'normal_producer_owner_written':True,
           'actual_redis_expiry':True,'native_pg_reap':True,'external_owner_flag':False,'provider_calls':0})


async def test_real_replacement_fails_at_actual_paid_boundary_preserves_prior_checker(native):
    from bestseller.services.llm import _execution_boundary
    r=native;calls=[]
    async def operator():calls.append('original-web-cancellation')
    binding=_CANCELLATION_CHECKER.set(operator)
    try:
        with pytest.raises(TaskCancelledError):
            async with web_pipeline_owner(r.settings,r.project.slug):
                await r.monitor.set(r.key,'successor',px=2000)
                await _execution_boundary()
                raise AssertionError('replaced Web owner passed paid boundary')
        assert calls==['original-web-cancellation'] and _CANCELLATION_CHECKER.get() is operator
        assert await r.monitor.get(r.key)=='successor'
        assert 0<await r.monitor.pttl(r.key)<=2000
    finally:_CANCELLATION_CHECKER.reset(binding)
    print({'real_redis_replacement':True,'native_paid_boundary_refused':True,'original_checker_preserved':True,'successor_preserved':True,'provider_calls':0})


async def test_real_held_owner_never_enters_or_deletes_successor(native):
    r=native;await r.monitor.set(r.key,'successor',px=2000)
    with pytest.raises(WebPipelineOwnerUnavailable) as caught:
        async with web_pipeline_owner(r.settings,r.project.slug):raise AssertionError('duplicate entered')
    assert caught.value.reason=='held_by_other' and await r.monitor.get(r.key)=='successor'


async def test_actual_unreachable_loopback_is_unknown_and_body_never_runs():
    import socket
    # Reserve an unlistening local port: cannot accidentally reach another service.
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1]
        settings=SimpleNamespace(redis=SimpleNamespace(url=f'redis://127.0.0.1:{port}/0',socket_timeout_seconds=.2,
            socket_connect_timeout_seconds=.2,pool_max_connections=1))
        with pytest.raises(WebPipelineOwnerUnavailable) as caught:
            async with web_pipeline_owner(settings,'web-owner-test-'+uuid4().hex):
                raise AssertionError('UNKNOWN owner was admitted')
        assert caught.value.reason=='degraded'
    print({'actual_loopback_connection_failure':True,'unknown_not_admitted':True,'provider_calls':0})
