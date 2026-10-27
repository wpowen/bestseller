"""Real SQLite, real policy and request construction; synthetic returns only."""
import asyncio
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, event, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bestseller.infra.db import session as db
from bestseller.infra.db.models import ExecutionBudgetModel
from bestseller.services import independent_acceptance as acceptance
from bestseller.services.budget import bind_execution_scope, ExecutionBudgetError, ExecutionOperationBusyError
from bestseller.services.commercial_source_evidence import PROMPT_VERSION
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings
from test_commercial_judge_recovery import BODY, report

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SCOPE = 'test:serial-admission-current'
OTHER = 'test:serial-admission-other'
SLOTS = [f'chapter-quality.independent-v2:{i}' for i in range(3)]
meta = MetaData()
receipts = Table('stage_sample_receipts', meta, Column('slot', String, primary_key=True),
                 Column('score', Integer), Column('request', String))


def valid_metadata(scope=SCOPE):
    # A fixture grant, never created through production authorization APIs.
    request = dict(scope=scope, counter_sha256=acceptance.CONTRACT_SHA256,
                   new_calls=128, new_tokens=2_000_000, authorization_ref='offline-fixture',
                   model=acceptance.MODEL, window_seconds=7200)
    return {acceptance.KEY: dict(schema=acceptance.SCHEMA, request=request,
                                request_sha256=acceptance.digest(request), submitted_at='fixture')}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    connect = socket.socket.connect
    def guarded(sock, *args, **kw):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            pytest.fail('Network connection forbidden in stage tests')
        return connect(sock, *args, **kw)
    def no_dns(*args, **kwargs):
        pytest.fail('Network name resolution forbidden in stage tests')
    monkeypatch.setattr(socket.socket, 'connect', guarded)
    monkeypatch.setattr(socket.socket, 'connect_ex', guarded)
    monkeypatch.setattr(socket, 'getaddrinfo', no_dns)
    monkeypatch.setenv('BESTSELLER_LLM_RUNTIME_PROFILE_PATH', str(tmp_path / 'absent.json'))
    from bestseller.services import llm
    async def forbidden(*args, **kwargs):
        pytest.fail('Provider/SDK call forbidden in stage tests')
    monkeypatch.setattr(llm, '_call_litellm', forbidden)
    monkeypatch.setattr(llm, 'complete_text', forbidden)




@pytest.fixture
def judge(monkeypatch):
    from bestseller.services import chapter_llm_quality_judge as module
    async def forbidden(*args, **kwargs):
        pytest.fail('Unstubbed completion forbidden')
    monkeypatch.setattr(module, 'complete_text', forbidden)
    return module

@pytest.fixture
async def rig(tmp_path, monkeypatch, judge):
    path = tmp_path / 'serial_admission_test.sqlite3'
    assert 'test' in path.stem
    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    statements, events, sessions = [], [], []
    @event.listens_for(engine.sync_engine, 'before_cursor_execute')
    def audit(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters, context.compiled.statement if context.compiled else None))
    class OwnedSession(AsyncSession):
        async def commit(self):
            events.append(('commit-start', self.info['slot']))
            await super().commit()
            events.append(('commit', self.info['slot']))
        async def rollback(self):
            events.append(('rollback-start', self.info['slot']))
            await super().rollback()
            events.append(('rollback', self.info['slot']))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owned_factory = async_sessionmaker(engine, class_=OwnedSession, expire_on_commit=False)
    def make_owned():
        session = owned_factory(info={'slot': len(sessions)})
        sessions.append(session)
        return session
    monkeypatch.setattr(db, '_global_session_factory', make_owned)
    async with engine.begin() as conn:
        await conn.run_sync(ExecutionBudgetModel.__table__.create)
        await conn.run_sync(meta.create_all)
    async with factory() as session:
        session.add(ExecutionBudgetModel(scope_id=SCOPE, max_calls=128, max_tokens=2_000_000,
                    calls_used=11, tokens_charged=1234, status='active',
                    deadline_at=datetime.now(UTC)+timedelta(hours=1), metadata_json=valid_metadata()))
        await session.commit()
    settings = load_settings(env={})
    settings.llm.critic.model = acceptance.MODEL
    settings.llm.critic.model_override = None
    calls, requests, returned = [], [], []
    entered, release, all_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    state = SimpleNamespace(peak=0, active=0, hold=None, error=None, entered=entered,
                            release=release, all_entered=all_entered, events=events)
    original_sample = judge.judge_chapter_commercial_quality
    async def completion(session, cfg, request):
        requests.append(request.model_dump(mode='json'))
        # Acquiring the real SDK lock here proves the scheduler does not hold it.
        async with acceptance.serialize(SCOPE):
            raw = json.dumps(report(0.61 + int(str(request.sample_slot).rsplit(':', 1)[1]) / 10))
            return LLMCompletionResult(content=raw, provider='offline', model_name=cfg.llm.critic.model,
                tool_calls=[{'id': str(request.sample_slot), 'type': 'function',
                             'function': {'name': 'submit_chapter_review', 'arguments': raw}}])
    async def preparation(session, cfg, **kw):
        slot = int(kw['sample_slot'].rsplit(':', 1)[1])
        calls.append((session, cfg, deepcopy(kw)))
        events.append(('start', slot))
        state.active += 1
        state.peak = max(state.peak, state.active)
        if len(calls) == 3:
            all_entered.set()
        try:
            # Real preparation transaction, no budget lock or provider.
            await session.execute(select(ExecutionBudgetModel.scope_id).where(ExecutionBudgetModel.scope_id == SCOPE))
            if slot == state.hold:
                entered.set()
                await release.wait()
            # Allow all baseline preparation tasks to overlap deterministically.
            await asyncio.sleep(0)
            if state.error and slot == state.error[0]:
                await session.execute(insert(receipts).values(slot='failed-uncommitted', score=-1, request=''))
                raise state.error[1]
            result = await original_sample(session, cfg, **kw)
            await session.execute(insert(receipts).values(slot=kw['sample_slot'], score=slot,
                                  request=json.dumps(requests[-1], sort_keys=True)))
            returned.append(result)
            events.append(('return', slot))
            return result
        finally:
            state.active -= 1
    monkeypatch.setattr(judge, 'complete_text', completion)
    monkeypatch.setattr(judge, 'judge_chapter_commercial_quality', preparation)
    async with factory() as caller:
        args = dict(chapter_number=2, content_md=BODY, samples=3, project_id=uuid4(),
                    workflow_run_id=uuid4(), generation_input={'source_note':'same'}, language='zh')
        async def run(scope=SCOPE):
            with bind_execution_scope(scope) if scope else nullcontext():
                return await judge.judge_chapter_commercial_quality_stable(caller, settings, **args)
        async def saved():
            async with factory() as reader:
                return list((await reader.execute(select(receipts).order_by(receipts.c.slot))).mappings())
        async def set_metadata(value):
            async with factory() as writer:
                row = await writer.get(ExecutionBudgetModel, SCOPE)
                row.metadata_json = value
                await writer.commit()
        yield SimpleNamespace(**locals())
    await engine.dispose()


def assert_independent(r):
    assert [kw['sample_slot'] for _, _, kw in r.calls] == SLOTS
    assert len({id(s) for s, _, _ in r.calls}) == 3
    assert all(s is not r.caller for s, _, _ in r.calls)
    assert all(cfg is r.settings for _, cfg, _ in r.calls)
    parameters = [{k:v for k,v in kw.items() if k != 'sample_slot'} for _,_,kw in r.calls]
    assert parameters[0] == parameters[1] == parameters[2]
    assert parameters[0]['commercial_cohort_samples'] == 3
    assert all(req['prompt_version'] == PROMPT_VERSION for req in r.requests)
    request_bodies = [{k:v for k,v in req.items() if k not in {'sample_slot','metadata'}} for req in r.requests]
    assert request_bodies[0] == request_bodies[1] == request_bodies[2]
    request_meta = [{k:v for k,v in req['metadata'].items() if k != 'commercial_sample_slot'} for req in r.requests]
    assert request_meta[0] == request_meta[1] == request_meta[2]


async def test_serial_policy_includes_preparation_and_each_owned_commit(rig):
    r = rig
    before = deepcopy((await r.caller.get(ExecutionBudgetModel, SCOPE)).__dict__)
    result = await asyncio.wait_for(r.run(), 3)
    assert r.state.peak == 1
    assert_independent(r)
    assert [x['slot'] for x in await r.saved()] == SLOTS
    assert result.overall_score == pytest.approx(0.71)
    aggregation = json.loads(result.raw_excerpt)
    assert [s['sample_slot'] for s in aggregation['samples']] == SLOTS
    assert len(r.returned) == 3 and len({id(v) for v in r.returned}) == 3
    for slot in range(2):
        assert r.events.index(('commit',slot)) < r.events.index(('start',slot+1))
    assert not any(e[0].startswith('rollback') for e in r.events)
    async with r.factory() as reader:
        budget = await reader.get(ExecutionBudgetModel, SCOPE)
        assert (budget.calls_used,budget.tokens_charged,budget.metadata_json) == (11,1234,before['metadata_json'])


@pytest.mark.parametrize('kind',['missing_policy','different_scope','no_scope','missing_row'])
async def test_nonserialized_scopes_keep_peak_three(rig, kind):
    r = rig
    if kind == 'missing_policy':
        await r.set_metadata({})
    scope = {'different_scope':OTHER,'no_scope':None,'missing_row':'test:missing'}.get(kind,SCOPE)
    task = asyncio.create_task(r.run(scope))
    await asyncio.wait_for(r.all_entered.wait(), 3)
    result = await task
    assert r.state.peak == 3
    assert_independent(r)
    assert len(await r.saved()) == 3 and not result.infrastructure_failure


@pytest.mark.parametrize('error_cls',[ExecutionBudgetError, ExecutionOperationBusyError, RuntimeError])
@pytest.mark.parametrize('slot',[0,1])
async def test_error_stops_later_draws_and_preserves_completed_returns(rig,error_cls,slot):
    r = rig
    error = error_cls('fixture-native-stop')
    r.state.error = (slot,error)
    with pytest.raises(error_cls) as caught:
        await r.run()
    assert caught.value is error
    assert [kw['sample_slot'] for _,_,kw in r.calls] == SLOTS[:slot+1]
    assert [row['slot'] for row in await r.saved()] == SLOTS[:slot]
    assert len(r.returned) == slot
    assert ('rollback',slot) in r.events
    assert all(('commit',i) in r.events for i in range(slot))


@pytest.mark.parametrize('slot',[0,1])
async def test_cancel_drains_only_started_sample_and_commits_return(rig,slot):
    r = rig
    r.state.hold = slot
    task = asyncio.create_task(r.run())
    await asyncio.wait_for(r.entered.wait(), 3)
    for _ in range(2):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    r.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [kw['sample_slot'] for _,_,kw in r.calls] == SLOTS[:slot+1]
    assert [row['slot'] for row in await r.saved()] == SLOTS[:slot+1]
    assert len(r.returned) == slot+1
    assert not any(e[0].startswith('rollback') for e in r.events)


async def test_cancel_during_owned_commit_drains_it_before_propagation(rig,monkeypatch):
    r = rig
    entered, release = asyncio.Event(), asyncio.Event()
    original = r.OwnedSession.commit
    async def held_commit(session):
        if session.info['slot'] == 0 and not entered.is_set():
            entered.set()
            await release.wait()
        await original(session)
    monkeypatch.setattr(r.OwnedSession,'commit',held_commit)
    task = asyncio.create_task(r.run())
    await asyncio.wait_for(entered.wait(),3)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(r.calls)==1 and [v['slot'] for v in await r.saved()]==SLOTS[:1]
    assert ('commit',0) in r.events


async def test_fresh_select_ignores_stale_and_dirty_identity_without_flushing(rig):
    r = rig
    # Caller has a stale non-policy identity; DB is changed to valid policy.
    await r.set_metadata({})
    stale = await r.caller.get(ExecutionBudgetModel,SCOPE)
    await r.set_metadata(valid_metadata())
    stale.calls_used = 999
    stale.max_calls = 1
    stale.metadata_json = {'uncommitted':'keep this object'}
    dirty = stale.metadata_json
    r.statements.clear()
    await r.run()
    assert r.state.peak==1
    assert stale in r.caller.dirty and stale.metadata_json is dirty
    assert stale.calls_used==999 and stale.max_calls==1
    budget_sql = [(sql, stmt) for sql,_,stmt in r.statements if 'execution_budgets' in sql.lower()]
    assert budget_sql and all(sql.lstrip().upper().startswith('SELECT') for sql,_ in budget_sql)
    assert all(getattr(stmt,'_for_update_arg',None) is None for _,stmt in budget_sql)
    assert any('metadata_json' in sql and 'max_calls' in sql for sql,_ in budget_sql)
    async with r.factory() as reader:
        row=await reader.get(ExecutionBudgetModel,SCOPE)
        assert (row.calls_used,row.max_calls,row.metadata_json)==(11,128,valid_metadata())


async def test_stale_valid_policy_cannot_enable_serialization_when_database_has_none(rig):
    r=rig
    stale=await r.caller.get(ExecutionBudgetModel,SCOPE)
    await r.set_metadata({})
    assert acceptance.policy(stale) is not None
    await r.run()
    assert r.state.peak==3


@pytest.mark.parametrize('change',['digest','scope','limits'])
async def test_invalid_policy_fails_closed_before_preparation(rig,change):
    r=rig
    value=valid_metadata()
    if change=='digest': value[acceptance.KEY]['request_sha256']='wrong'
    if change=='scope': value=valid_metadata(OTHER)
    if change=='limits':
        value[acceptance.KEY]['request']['new_calls']=127
        value[acceptance.KEY]['request_sha256']=acceptance.digest(value[acceptance.KEY]['request'])
    await r.set_metadata(value)
    with pytest.raises(ExecutionBudgetError,match='execution_acceptance_binding_invalid'):
        await r.run()
    assert not r.calls and not r.requests and not await r.saved()


@pytest.mark.parametrize('kind',['typed_error','cancel'])
async def test_nonserialized_error_or_cancel_still_drains_all_started_draws(rig,kind):
    r=rig
    await r.set_metadata({})
    r.state.hold=1
    if kind=='typed_error': r.state.error=(0,ExecutionBudgetError('fixture-stop'))
    task=asyncio.create_task(r.run())
    await asyncio.wait_for(r.all_entered.wait(),3)
    if kind=='cancel':
        task.cancel()
        await asyncio.sleep(0)
    assert not task.done()
    r.release.set()
    with pytest.raises(ExecutionBudgetError if kind=='typed_error' else asyncio.CancelledError):
        await task
    assert len(r.calls)==3 and r.state.peak==3
    assert [v['slot'] for v in await r.saved()]==(SLOTS[1:] if kind=='typed_error' else SLOTS)


async def test_cancel_during_rollback_drains_it_and_preserves_typed_stop(rig,monkeypatch):
    r=rig
    error=ExecutionBudgetError('fixture-native-typed-stop')
    r.state.error=(1,error)
    entered,release=asyncio.Event(),asyncio.Event()
    original=r.OwnedSession.rollback
    async def held_rollback(session):
        if session.info['slot']==1:
            entered.set()
            await release.wait()
        await original(session)
    monkeypatch.setattr(r.OwnedSession,'rollback',held_rollback)
    task=asyncio.create_task(r.run())
    await asyncio.wait_for(entered.wait(),3)
    for _ in range(2):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    release.set()
    with pytest.raises(ExecutionBudgetError) as caught:
        await task
    assert caught.value is error
    assert len(r.calls)==2 and [v['slot'] for v in await r.saved()]==SLOTS[:1]
    assert ('rollback',1) in r.events


async def test_unavailable_normal_return_commits_before_next_independent_sample(rig,monkeypatch):
    r=rig
    native=r.judge.complete_text
    async def unavailable(session,settings,request):
        if request.sample_slot==SLOTS[1]:
            r.requests.append(request.model_dump(mode='json'))
            return LLMCompletionResult(content='{"judge_unavailable":true}',provider='offline',model_name=acceptance.MODEL)
        return await native(session,settings,request)
    monkeypatch.setattr(r.judge,'complete_text',unavailable)
    result=await r.run()
    assert r.state.peak==1 and len(r.calls)==3
    assert len(r.returned)==3 and r.returned[1].infrastructure_failure
    assert [v['slot'] for v in await r.saved()]==SLOTS
    assert r.events.index(('commit',1))<r.events.index(('start',2))
    receipt=json.loads(result.raw_excerpt)
    assert receipt['valid_samples']==2
    assert receipt['samples'][1]['report']==r.returned[1].model_dump(mode='json')
