"""R07 native PG: a row refreshed before its lock cannot be reaped stale."""
import asyncio
from datetime import UTC,datetime,timedelta
from uuid import uuid4
import pytest
from sqlalchemy import event,select,text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from sqlalchemy.schema import CreateTable
from bestseller.infra.db.models import ProjectModel,WorkflowRunModel
from bestseller.worker import self_heal as heal

pytestmark=[pytest.mark.integration,pytest.mark.asyncio]


class EmptyKnownRedis:
    async def get(self,key):return None
    async def pttl(self,key):return -2
    async def scan_iter(self,match):
        if False:yield None


async def test_pg_reaper_rechecks_fresh_heartbeat_under_workflow_lock(postgresql_test_settings):
    url=make_url(postgresql_test_settings.database.url);assert url.database=='bestseller_repair_test_20261015'
    schema='r07_test_'+uuid4().hex
    engine=create_async_engine(url,connect_args={'server_settings':{'search_path':schema,'lock_timeout':'3000'}})
    factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with engine.begin() as c:
            assert await c.scalar(text('select current_database()'))=='bestseller_repair_test_20261015'
            await c.execute(text('CREATE SCHEMA '+schema))
            for model in (ProjectModel,WorkflowRunModel):await c.execute(CreateTable(model.__table__,include_foreign_key_constraints=[]))
        p=ProjectModel(id=uuid4(),slug='r07-test-'+uuid4().hex,title='synthetic',genre='test',target_chapters=4,target_word_count=8000,metadata_json={})
        old=datetime.now(UTC)-timedelta(hours=5)
        r=WorkflowRunModel(id=uuid4(),project_id=p.id,workflow_type='project_pipeline',status='running',created_at=old,updated_at=old,
            metadata_json={'execution_owner':uuid4().hex,'execution_scope_id':f'project:{p.id}','retained':'unchanged'})
        async with factory() as s:s.add_all([p,r]);await s.commit()
        async with factory() as reader,factory() as writer:
            # Retain an actual stale ORM identity before a separate writer commits.
            stale=await reader.get(WorkflowRunModel,r.id)
            owner=await writer.scalar(select(WorkflowRunModel).where(WorkflowRunModel.id==r.id).with_for_update())
            owner.updated_at=datetime.now(UTC);owner.metadata_json={**owner.metadata_json,'live_update':'preserved'}
            await writer.flush()
            # Reader's candidate query sees old committed age, but SKIP LOCKED
            # must not seize an active writer's workflow.
            assert await heal.reap_orphan_workflow_runs(reader,redis=EmptyKnownRedis())==0
            assert stale.status=='running'
            await writer.commit()
            # Force candidate inventory to retain the pre-lock stale snapshot;
            # actual locked SELECT must populate_existing before acting.
            from unittest.mock import patch
            async def candidates(*a,**k):return [stale]
            with patch.object(reader,'scalars',candidates):
                assert await heal.reap_orphan_workflow_runs(reader,redis=EmptyKnownRedis())==0
            assert stale.updated_at>old and stale.metadata_json['live_update']=='preserved'
            assert stale.status=='running'
            await reader.rollback()
        print({'database':url.database,'two_actual_transactions':True,'skip_locked_active_writer':True,'post_lock_heartbeat_refreshed':True})
    finally:
        async with engine.begin() as c:await c.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))
        await engine.dispose()
