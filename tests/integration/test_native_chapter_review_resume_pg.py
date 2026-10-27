"""Actual isolated PG locks/rollback; only synthetic project rows, no provider."""
import asyncio
from copy import deepcopy
from uuid import uuid4
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"unit"))

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.exc import DBAPIError

# Import actual native row fixtures; upstream context storage is synthetic,
# validators, fixed archive, renderer, history/ledger and SQL are real.
from test_native_chapter_review_resume import (  # noqa:F401
    native_scope, native_wait, completed, cohort, retained,
    snapshot,
)
from bestseller.infra.db import models as db
from bestseller.services import chapter_review_reconciliation as service
from bestseller.services import chapter_review_policy_history_8y24 as archive
from bestseller.services.chapter_review_prerequisite import WAITING

pytestmark=[pytest.mark.integration,pytest.mark.asyncio]


@pytest.fixture(autouse=True)
async def isolated_pg(postgresql_test_settings,monkeypatch):
    url=make_url(postgresql_test_settings.database.url)
    assert url.database=='bestseller_repair_test_20261015'
    schema='r09_native_test_'+uuid4().hex
    admin=create_async_engine(url)
    async with admin.begin() as conn:
        assert await conn.scalar(text('select current_database()'))=='bestseller_repair_test_20261015'
        await conn.execute(text('CREATE SCHEMA '+schema))
    import test_commercial_component_cohort as fixture_module
    def make_engine(_url):
        return create_async_engine(url,connect_args={'server_settings':{'search_path':schema,'lock_timeout':'3000'}})
    monkeypatch.setattr(fixture_module,'create_async_engine',make_engine)
    # The reused SQLite fixture creates only its own tables out of FK order;
    # these tests verify native lineage/locks, not FK DDL ordering.
    from sqlalchemy.schema import CreateTable
    monkeypatch.setattr(fixture_module,'CreateTable',lambda table:CreateTable(table,include_foreign_key_constraints=[]))
    try:yield
    finally:
        async with admin.begin() as conn:await conn.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))
        await admin.dispose()


async def test_pg_waiting_control_first_locks_and_rollback_visible_to_fresh_reader(native_wait):
    r=native_wait;factory=async_sessionmaker(r.engine,expire_on_commit=False)
    before=await snapshot(r);pid,cid=r.project.id,r.chapter.id
    order=[]
    def record(state):
        stmt=state.statement
        if getattr(stmt,'_for_update_arg',None) is not None:
            order.append(stmt.column_descriptions[0]['entity'])
    event.listen(r.session.sync_session,'do_orm_execute',record)
    try:result=await service.prepare_native_review_resume(r.session,r.settings,project_id=pid)
    finally:event.remove(r.session.sync_session,'do_orm_execute',record)
    assert order[:3]==[db.BookProductionControlModel,db.ProjectModel,db.ChapterModel]
    assert result['chapter_receipts'][0]['waiting_rearm']['status']=='rearmed'
    async with factory() as observer:
        stored=await observer.get(db.ChapterModel,cid)
        assert stored.metadata_json['chapter_critic_cycles'][r.cid]['state']==WAITING
        with pytest.raises(DBAPIError):
            await observer.scalar(select(db.BookProductionControlModel).where(
                db.BookProductionControlModel.project_id==pid).with_for_update(nowait=True))
        await observer.rollback()
    await r.session.rollback()
    assert await snapshot(r)==before
    await service.prepare_native_review_resume(r.session,r.settings,project_id=pid)
    await r.session.commit()
    async with factory() as reader:
        row=await reader.get(db.ChapterModel,cid)
        current=row.metadata_json['chapter_critic_cycles'][r.cid]
        assert current['state']=='prepared' and len(current['attempts'])==1
        assert current['attempts'][0]['infrastructure_history']==r.cycle['attempts'][0]['infrastructure_history']
        assert current['policy_source_hash']!=archive.OLD_POLICY


async def test_pg_source_mutation_while_waiting_for_chapter_lock_blocks_alias(native_wait):
    r=native_wait;factory=async_sessionmaker(r.engine,expire_on_commit=False)
    pid,cid=r.project.id,r.chapter.id
    await r.session.commit()
    async with factory() as writer:
        row=await writer.scalar(select(db.ChapterModel).where(db.ChapterModel.id==cid).with_for_update())
        row.chapter_goal='Concurrent genuinely changed authored goal'
        await writer.flush()
        reached=asyncio.Event()
        def record(state):
            stmt=state.statement
            if getattr(stmt,'_for_update_arg',None) is not None and stmt.column_descriptions[0]['entity'] is db.ChapterModel:
                reached.set()
        event.listen(r.session.sync_session,'do_orm_execute',record)
        task=asyncio.create_task(service.prepare_native_review_resume(r.session,r.settings,project_id=pid))
        try:
            await asyncio.wait_for(reached.wait(),5)
            assert not task.done()
            await writer.commit()
            with pytest.raises(ValueError,match='semantic_dependencies_changed'):
                await asyncio.wait_for(task,8)
        finally:
            event.remove(r.session.sync_session,'do_orm_execute',record)
            if not task.done():task.cancel()
    await r.session.rollback()
    async with factory() as reader:
        row=await reader.get(db.ChapterModel,cid)
        cycle=row.metadata_json['chapter_critic_cycles'][r.cid]
        assert cycle['policy_source_hash']==archive.OLD_POLICY and 'native_runtime_policy_alias' not in cycle
        assert cycle['state']==WAITING


async def test_pg_completed_native_paid_proof_retains_all_original_financial_rows(completed):
    r=completed;before=await snapshot(r)
    result=await service.prepare_native_review_resume(r.session,r.settings,project_id=r.project.id)
    assert result['chapter_receipts'][0]['policy_alias']['input_proof']['provider_calls']==0
    await r.session.commit()
    after=await snapshot(r)
    for table,rows in before.items():
        if table!='chapters':assert after[table]==rows
