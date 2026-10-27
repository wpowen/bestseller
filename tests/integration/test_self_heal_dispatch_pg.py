"""R06 final dispatch writer: actual isolated PG, synthetic source/queue only."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import asyncio
import sys

import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateTable

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'unit'))
from test_self_heal_repair_progress import hype_case, rig, collect, committed_hype  # noqa:F401,E402
from bestseller.infra.db import models as db
from bestseller.services import self_heal_repair_progress as observer
from bestseller.services.retry_ledger import project_execution_scope
from bestseller.services.budget import ExecutionBudgetError
from bestseller.worker import self_heal as worker

pytestmark=[pytest.mark.integration,pytest.mark.asyncio]


class QueueBoundary:
    """Only ARQ/Redis IO is simulated; no permissions/DB helper is stubbed."""
    def __init__(self):self.values={};self.outcome='accept';self.enqueues=[]
    async def get(self,key):return self.values.get(key)
    async def set(self,key,value,**kw):
        if kw.get('nx') and key in self.values:return False
        self.values[key]=value;return True
    async def delete(self,*keys):
        for key in keys:self.values.pop(key,None)
    async def exists(self,*keys):return sum(key in self.values for key in keys)
    async def zscore(self,*a):return None
    async def scan_iter(self,match):
        for key in list(self.values):
            if fnmatch(key,match):yield key
    async def eval(self,script,nkeys,*args):
        key,owner=args[:2]
        if self.values.get(key)==owner:
            self.values.pop(key,None);return 1
        return 0
    async def enqueue_job(self,*a,**kw):
        self.enqueues.append((a,kw))
        if self.outcome=='error':raise RuntimeError('synthetic ARQ queue refusal')
        if self.outcome=='dedup':return None
        return SimpleNamespace(job_id=kw.get('_job_id'))
    async def aclose(self):pass


@pytest.fixture
async def pg(hype_case,postgresql_test_settings,monkeypatch):  # noqa:F811
    r=hype_case
    # Full native source-plan recovery fixture, produced in isolated SQLite.
    # Clone its exact synthetic rows into PG, then use the REAL PG collector.
    baseline=observer.compare_repair_progress(None,await collect(r))['next_state']
    kept={'review_attempts':7,'reader_attraction':{'score':52,'status':'open'},
          'human_acceptance':'not_performed','execution_budget_audit':{'attempts':290,'unknown':0}}
    r.project.metadata_json={**r.project.metadata_json,**deepcopy(kept),observer.STATE_KEY:baseline,
        'self_heal_last_chapters_total':1,'self_heal_last_chapters_with_draft':1,'self_heal_no_progress_attempts':4}
    await r.session.commit()
    await committed_hype(r)
    url=make_url(postgresql_test_settings.database.url)
    assert url.database=='bestseller_repair_test_20261015'
    schema='r06_dispatch_test_'+uuid4().hex
    engine=create_async_engine(url,connect_args={'server_settings':{'search_path':schema,'lock_timeout':'3000'}})
    factory=async_sessionmaker(engine,expire_on_commit=False)
    models=(db.ProjectModel,db.PlanningArtifactVersionModel,db.WorkflowRunModel,db.ChapterModel,
        db.ChapterDraftVersionModel,db.SceneCardModel,db.BookProductionControlModel,db.ExecutionBudgetModel,
        db.ExecutionOperationModel,db.ExecutionAttemptModel,db.RewriteTaskModel)
    async with engine.begin() as conn:
        assert await conn.scalar(text('select current_database()'))=='bestseller_repair_test_20261015'
        await conn.execute(text('CREATE SCHEMA '+schema))
        for model in models:
            # Keep the actual control -> Project FK: its KEY SHARE check is
            # the native absent-control insertion fence under test below.
            constraints = list(model.__table__.foreign_key_constraints) if model is db.BookProductionControlModel else []
            ddl=str(CreateTable(model.__table__,include_foreign_key_constraints=constraints).compile(dialect=conn.dialect))
            if model is db.BookProductionControlModel:
                assert ddl.count('REFERENCES projects (id)')==1
                ddl=ddl.replace('REFERENCES projects (id)',f'REFERENCES "{schema}"."projects" (id)')
            await conn.exec_driver_sql(ddl)
    try:
        async with factory() as writer:
            for model in models[:6]:
                for row in await r.session.scalars(select(model)):
                    values={column.key:deepcopy(getattr(row,column.key)) for column in inspect(model).column_attrs}
                    for key,value in values.items():
                        if isinstance(value,datetime) and value.tzinfo is None:values[key]=value.replace(tzinfo=UTC)
                    writer.add(model(**values))
                # A mapped object without an ORM relationship is not an
                # insertion-order guarantee: persist Project before children.
                await writer.flush()
            writer.add(db.BookProductionControlModel(project_id=r.project.id,desired_state='run',command_serial=2))
            scope=project_execution_scope(r.project)
            writer.add(db.ExecutionBudgetModel(scope_id=scope,status='active',calls_used=7,tokens_charged=12345,
                max_calls=100,max_tokens=1000000,deadline_at=datetime.now(UTC)+timedelta(days=1),metadata_json={}))
            await writer.commit()
        pool=QueueBoundary()
        import arq.connections
        async def queue_pool(*a,**kw):return pool
        monkeypatch.setattr(arq.connections,'create_pool',queue_pool)
        monkeypatch.setattr(worker,'get_server_session',factory)
        stuck=worker.StuckProject(project_id=r.project.id,slug=r.project.slug,reason='outline_replan_required',
            stuck_at_chapter=None,chapters_total=1,chapters_with_draft=1,heal_kind='outline_replan')
        async def known_scanner(session):return [stuck]
        monkeypatch.setattr(worker,'find_stuck_projects',known_scanner)
        # Network/provider paths are tripwires, not synthetic semantic verdicts.
        from bestseller.services import llm,chapter_llm_quality_judge
        import httpx
        async def forbidden(*a,**kw):raise AssertionError('R06 PG prohibits HTTP/LLM/SDK sends')
        monkeypatch.setattr(llm,'complete_text',forbidden)
        monkeypatch.setattr(llm,'_call_litellm',forbidden)
        monkeypatch.setattr(llm,'_paid_transport_attempt',forbidden)
        monkeypatch.setattr(chapter_llm_quality_judge,'complete_text',forbidden)
        monkeypatch.setattr(httpx.AsyncClient,'send',forbidden)
        result=SimpleNamespace(factory=factory,engine=engine,pid=r.project.id,cid=r.chapter.id,
            scope=scope,settings=r.settings,stuck=stuck,pool=pool,preserved=kept,schema=schema)
        actual=await observer.collect_repair_progress(factory,r.settings,r.project.id)
        assert len(actual['effects'])==1 and actual['rejected']==[]
        result.snapshot=actual
        yield result
    finally:
        async with engine.begin() as conn:await conn.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))
        await engine.dispose()


async def readback(pg):
    async with pg.factory() as session:
        project=await session.get(db.ProjectModel,pg.pid)
        budget=await session.get(db.ExecutionBudgetModel,pg.scope)
        audits=list(await session.scalars(select(db.WorkflowRunModel).where(
            db.WorkflowRunModel.project_id==pg.pid,db.WorkflowRunModel.workflow_type==worker._REPAIR_OBSERVER_WORKFLOW)))
        return deepcopy(project.metadata_json), (budget.status,budget.calls_used,budget.tokens_charged), [deepcopy(a.metadata_json) for a in audits]


async def heal(pg):return await worker.heal_stuck_projects(pg.settings,redis=pg.pool,worker_id='r06-pg-'+str(pg.pid))


async def test_actual_pg_control_first_nowait_and_stop_seen_after_lock(pg):
    before=await readback(pg)
    async with pg.factory() as final,pg.factory() as stop,pg.factory() as probe:
        control=await stop.scalar(select(db.BookProductionControlModel).where(
            db.BookProductionControlModel.project_id==pg.pid).with_for_update())
        control.desired_state='stop';control.command_serial+=1
        await stop.flush()
        reached=asyncio.Event();locks=[]
        def sql(state):
            st=state.statement
            if getattr(st,'_for_update_arg',None) is not None:
                locks.append(st.column_descriptions[0]['entity']);reached.set()
        event.listen(final.sync_session,'do_orm_execute',sql)
        task=asyncio.create_task(worker._lock_heal_dispatch(final,pg.stuck,pg.pool))
        try:
            await asyncio.wait_for(reached.wait(),5)
            assert not task.done() and locks[0] is db.BookProductionControlModel
            with pytest.raises(DBAPIError):
                await probe.scalar(select(db.BookProductionControlModel).where(
                    db.BookProductionControlModel.project_id==pg.pid).with_for_update(nowait=True))
            await probe.rollback()
            # STOP follows the same Control -> Project order, so it can commit
            # while final admission is waiting on Control without a deadlock.
            await stop.scalar(select(db.ProjectModel).where(db.ProjectModel.id==pg.pid).with_for_update())
            await stop.commit()
            assert await asyncio.wait_for(task,5) is None
            assert locks==[db.BookProductionControlModel]
        finally:
            event.remove(final.sync_session,'do_orm_execute',sql)
            if not task.done():task.cancel()
    assert await heal(pg)==[] and not pg.pool.enqueues
    assert await readback(pg)==before


async def test_actual_pg_final_budget_refresh_refuses_exhausted_and_unknown(pg):
    original=await readback(pg)
    for status,code in [('exhausted','execution_budget_exhausted'),('unknown','execution_result_unknown')]:
        async with pg.factory() as final,pg.factory() as writer:
            cached=await final.get(db.ExecutionBudgetModel,pg.scope)
            cached_status=cached.status
            row=await writer.get(db.ExecutionBudgetModel,pg.scope)
            row.status=status;await writer.commit()
            assert cached.status==cached_status  # stale pre-scan identity
            with pytest.raises(ExecutionBudgetError,match=code):
                await worker._lock_heal_dispatch(final,pg.stuck,pg.pool)
            assert cached.status==status
        assert await heal(pg)==[] and not pg.pool.enqueues
        meta,budget,audits=await readback(pg)
        assert meta==original[0] and audits==[] and budget==(status,7,12345)


async def test_actual_pg_append_only_audit_blocks_replay_of_valid_old_metadata(pg):
    old,budget,_=await readback(pg)
    first=await heal(pg)
    assert len(first)==1
    meta,after_budget,audits=await readback(pg)
    assert meta['self_heal_no_progress_attempts']==0 and after_budget==budget
    assert len(audits)==1 and audits[0]['effect_key']==pg.snapshot['effects'][0]['effect_key']
    assert audits[0]['provider_calls']==audits[0]['new_attempts']==0
    assert all(audits[0][k] is False for k in observer.DENIALS)
    async with pg.factory() as writer:
        assert await worker._consumed_repair_effects(writer,pg.pid)==frozenset({audits[0]['effect_key']})
        project=await writer.get(db.ProjectModel,pg.pid)
        project.metadata_json=old  # exact older valid state/hash, same source
        await writer.commit()
    count=len(pg.pool.enqueues)
    assert await heal(pg)==[]
    replayed,latest_budget,history=await readback(pg)
    assert replayed['self_heal_no_progress_attempts']==5
    assert len(pg.pool.enqueues)==count and history==audits and latest_budget==budget
    assert {k:replayed[k] for k in pg.preserved}==pg.preserved


async def test_actual_pg_queue_dedup_and_failure_do_not_audit_or_consume(pg):
    before=await readback(pg)
    for outcome in ('dedup','error'):
        pg.pool.outcome=outcome
        assert await heal(pg)==[]
        assert await readback(pg)==before
        async with pg.factory() as session:
            assert not await worker._consumed_repair_effects(session,pg.pid)
    pg.pool.outcome='accept'
    assert len(await heal(pg))==1
    metadata,budget,audits=await readback(pg)
    assert metadata['self_heal_no_progress_attempts']==0 and len(audits)==1
    assert budget==before[1]


async def test_actual_pg_unknown_observer_history_refuses_final_writer(pg):
    before=await readback(pg)
    async with pg.factory() as writer:
        writer.add(db.WorkflowRunModel(project_id=pg.pid,workflow_type=worker._REPAIR_OBSERVER_WORKFLOW,
            scope_type='project',scope_id=pg.pid,status='failed',metadata_json={
                'effect_key':pg.snapshot['effects'][0]['effect_key']}))
        await writer.commit()
    count=len(pg.pool.enqueues)
    assert await heal(pg)==[] and len(pg.pool.enqueues)==count
    meta,budget,audits=await readback(pg)
    assert meta==before[0] and budget==before[1] and len(audits)==1
    async with pg.factory() as session:
        with pytest.raises(ValueError,match='repair_observer_history_unavailable'):
            await worker._consumed_repair_effects(session,pg.pid)


async def test_actual_pg_legacy_control_absence_admits_and_first_stop_insert_is_fenced(pg):
    # Legacy has no control row: native policy is RUN/serial0, with the same
    # finite budget. The native control FK must be present, not a mock fence.
    async with pg.factory() as writer:
        row=await writer.get(db.BookProductionControlModel,pg.pid)
        await writer.delete(row)
        await writer.commit()
        constraints=(await writer.execute(text("""
            SELECT n.nspname FROM pg_constraint c
            JOIN pg_class r ON r.oid=c.confrelid
            JOIN pg_namespace n ON n.oid=r.relnamespace
            WHERE c.conrelid='book_production_control'::regclass
              AND c.confrelid='projects'::regclass AND c.contype='f'
        """))).scalars().all()
        assert constraints==[pg.schema]
    before=await readback(pg)
    from bestseller.services.production_control import load_control_state
    async with pg.factory() as final,pg.factory() as stop:
        legacy=await load_control_state(final,pg.pid)
        assert not legacy.halted and legacy.command_serial==0
        project=await worker._lock_heal_dispatch(final,pg.stuck,pg.pool)
        assert project is not None and project.id==pg.pid
        assert await final.get(db.BookProductionControlModel,pg.pid) is None
        await stop.execute(text("SET LOCAL lock_timeout='150ms'"))
        stop.add(db.BookProductionControlModel(project_id=pg.pid,desired_state='stop',command_serial=1))
        with pytest.raises(DBAPIError) as blocked:
            await stop.flush()  # Actual FK KEY SHARE conflicts with Project FOR UPDATE.
        assert getattr(blocked.value.orig,'sqlstate',None)=='55P03'
        await stop.rollback()
        await final.rollback()
    assert await readback(pg)==before and not pg.pool.enqueues

    # A first STOP committed while final admission waits for Project must be
    # caught by the second, fresh Control FOR UPDATE (the first read was absent).
    async with pg.factory() as final,pg.factory() as stop:
        await stop.scalar(select(db.ProjectModel).where(db.ProjectModel.id==pg.pid).with_for_update())
        reached=asyncio.Event();locks=[]
        def sql(state):
            st=state.statement
            if getattr(st,'_for_update_arg',None) is not None:
                entity=st.column_descriptions[0]['entity'];locks.append(entity)
                if entity is db.ProjectModel:reached.set()
        event.listen(final.sync_session,'do_orm_execute',sql)
        task=asyncio.create_task(worker._lock_heal_dispatch(final,pg.stuck,pg.pool))
        try:
            await asyncio.wait_for(reached.wait(),5)
            assert not task.done() and locks==[db.BookProductionControlModel,db.ProjectModel]
            stop.add(db.BookProductionControlModel(project_id=pg.pid,desired_state='stop',command_serial=1))
            await stop.commit()
            assert await asyncio.wait_for(task,5) is None
            assert locks==[db.BookProductionControlModel,db.ProjectModel,db.BookProductionControlModel]
        finally:
            event.remove(final.sync_session,'do_orm_execute',sql)
            if not task.done():
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
    assert await heal(pg)==[] and not pg.pool.enqueues
    assert await readback(pg)==before
