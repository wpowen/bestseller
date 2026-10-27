"""R05: real isolated PostgreSQL lock/rollback evidence; no provider dispatch."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import session as db_session
from bestseller.infra.db.models import BookProductionControlModel, ProjectModel, WorkflowRunModel
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.execution_stage import attach_workflow_failure
from bestseller.worker.tasks import _persist_execution_terminal

pytestmark=[pytest.mark.integration,pytest.mark.asyncio]
DB='bestseller_repair_test_20261015'
CODE='execution_result_unknown'


@pytest.fixture
async def pg(postgresql_test_settings,monkeypatch):
    from sqlalchemy.engine import make_url
    url=make_url(postgresql_test_settings.database.url)
    assert url.database==DB
    schema='r05_test_'+uuid4().hex
    engine=create_async_engine(url,connect_args={'server_settings':{'search_path':schema,'lock_timeout':'4000','statement_timeout':'8000'}})
    models=(ProjectModel,WorkflowRunModel,BookProductionControlModel)
    async with engine.begin() as c:
        assert await c.scalar(text('SELECT current_database()'))==DB
        await c.execute(text('CREATE SCHEMA '+schema))
        for model in models:
            await c.execute(CreateTable(model.__table__,include_foreign_key_constraints=[]))
    factory=async_sessionmaker(engine,expire_on_commit=False)
    p=ProjectModel(id=uuid4(),slug='r05-test-'+uuid4().hex,title='original',genre='test',target_chapters=4,target_word_count=8000,
        status='drafting',metadata_json={'author':'kept','retry_chain':{'used':17}})
    r=WorkflowRunModel(id=uuid4(),project_id=p.id,workflow_type='chapter_pipeline',status='running',current_step='review',
        metadata_json={'execution_scope_id':f'project:{p.id}','paid_history':{'calls':290}})
    async with factory() as s:
        s.add_all([p,r,BookProductionControlModel(project_id=p.id,desired_state='pause',command_serial=7)])
        await s.commit()
    monkeypatch.setattr(db_session,'_global_session_factory',factory)
    try:yield SimpleNamespace(engine=engine,factory=factory,project=p,run=r,context=db_session.get_server_session)
    finally:
        async with engine.begin() as c:await c.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        await engine.dispose()


@pytest.mark.parametrize('writer_outcome',['commit','rollback'])
async def test_two_transactions_lock_order_refresh_status_and_no_lost_metadata(pg,writer_outcome):
    rig=pg
    seen=[];waiting=asyncio.Event();writer_pid=None
    def record(conn,cursor,statement,parameters,context,many):
        if 'FOR UPDATE' in statement:
            seen.append(statement)
            if 'book_production_control' in statement and len(seen)>2:waiting.set()
    event.listen(rig.engine.sync_engine,'before_cursor_execute',record)
    try:
        async with rig.factory() as writer:
            writer_pid=await writer.scalar(text('select pg_backend_pid()'))
            await writer.scalar(select(BookProductionControlModel).where(BookProductionControlModel.project_id==rig.project.id).with_for_update())
            p=await writer.scalar(select(ProjectModel).where(ProjectModel.id==rig.project.id).with_for_update())
            r=await writer.scalar(select(WorkflowRunModel).where(WorkflowRunModel.id==rig.run.id).with_for_update())
            p.metadata_json={**p.metadata_json,'concurrent_author_update':'committed-only'}
            r.status='completed';r.current_step='final_checkpoint'
            await writer.flush()
            waiter_pids=[]
            @asynccontextmanager
            async def stale_context():
                async with rig.factory() as s:
                    waiter_pids.append(await s.scalar(text('select pg_backend_pid()')))
                    # Force identity-map staleness BEFORE waiting for locks.
                    stale_p=await s.get(ProjectModel,rig.project.id)
                    stale_r=await s.get(WorkflowRunModel,rig.run.id)
                    assert stale_r.status=='running'
                    try:
                        yield s
                        await s.commit()
                    except BaseException:
                        await s.rollback();raise
                    assert stale_p is not None
            task=asyncio.create_task(_persist_execution_terminal(rig.project.slug,ExecutionBudgetError(CODE,f'project:{rig.project.id}'),
                str(rig.run.id),session_context=stale_context))
            try:
                await asyncio.wait_for(waiting.wait(),2)
                # Both real backend transactions exist; waiter cannot finish
                # while control is locked. No timing sleep stands in for SQL.
                async with asyncio.timeout(2):
                    while not await writer.scalar(text('SELECT :owner = ANY(pg_blocking_pids(:waiter))'),
                        {'owner':writer_pid,'waiter':waiter_pids[0]}):
                        await asyncio.sleep(0)
                assert not task.done()
                if writer_outcome=='commit':await writer.commit()
                else:await writer.rollback()
                await asyncio.wait_for(task,5)
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
        async with rig.factory() as s:
            assert await s.scalar(text('SELECT current_database()'))==DB
            p=await s.get(ProjectModel,rig.project.id);r=await s.get(WorkflowRunModel,rig.run.id)
            assert p.status=='paused' and p.metadata_json['author']=='kept'
            assert p.metadata_json['retry_chain']=={'used':17}
            assert p.metadata_json['execution_terminal']['workflow_id']==str(rig.run.id)
            assert p.metadata_json['execution_terminal']['code']==CODE
            assert r.metadata_json['paid_history']=={'calls':290}
            if writer_outcome=='commit':
                assert r.status=='completed' and r.current_step=='final_checkpoint'
                assert p.metadata_json['concurrent_author_update']=='committed-only'
            else:
                assert r.status=='failed' and 'concurrent_author_update' not in p.metadata_json
            control=await s.get(BookProductionControlModel,rig.project.id)
            assert control.desired_state=='pause' and control.command_serial==7
        terminal_locks=seen[3:]
        assert 'book_production_control' in terminal_locks[0]
        assert 'projects' in terminal_locks[1] and 'workflow_runs' in terminal_locks[2]
        assert all('execution_budgets' not in q for q in seen)
        print({'database':DB,'writer':writer_outcome,'two_backend_transactions':True,'blocked_on_control':True,
               'metadata_readback_preserved':True,'post_lock_workflow_status_refreshed':True})
    finally:event.remove(rig.engine.sync_engine,'before_cursor_execute',record)


async def test_actual_outer_rollback_receipt_survives_worker_terminal(pg):
    rig=pg;error=ExecutionBudgetError(CODE,f'project:{rig.project.id}')
    with pytest.raises(ExecutionBudgetError):
        async with rig.context() as s:
            p=await s.get(ProjectModel,rig.project.id);r=await s.get(WorkflowRunModel,rig.run.id)
            p.title='must roll back';attach_workflow_failure(error,r);r.status='failed'
            await s.flush();raise error
    async with rig.factory() as s:
        p=await s.get(ProjectModel,rig.project.id);r=await s.get(WorkflowRunModel,rig.run.id)
        assert p.title=='original' and r.status=='failed'
        before=deepcopy(r.metadata_json)
        assert p.metadata_json['execution_terminal']['workflow_id']==str(rig.run.id)
    await _persist_execution_terminal(rig.project.slug,error,'heal:queue-not-workflow',session_context=rig.context)
    async with rig.factory() as s:
        p=await s.get(ProjectModel,rig.project.id);r=await s.get(WorkflowRunModel,rig.run.id)
        assert r.metadata_json==before
        assert p.metadata_json['execution_terminal']['workflow_id']==str(rig.run.id)
        assert p.metadata_json['execution_terminal']['code']==CODE and p.metadata_json['retry_chain']=={'used':17}
        assert r.metadata_json['execution_failure_receipts'][-1]['workflow_id']==str(r.id)
    print({'database':DB,'real_flush_rollback_detached_receipt':True,'worker_binding_retained':True})
