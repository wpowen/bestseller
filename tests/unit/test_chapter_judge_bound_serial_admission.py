"""Use the existing commercial receipt fixture with real owned SQLite sessions."""
import asyncio
from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bestseller.infra.db import session as db
from bestseller.infra.db.models import ExecutionAttemptModel, LlmRunModel
from bestseller.services import chapter_llm_quality_judge as shared_judge
from bestseller.services.budget import bind_execution_scope, ExecutionBudgetError
from test_commercial_component_cohort import cohort, SCOPE, _jsonb  # noqa: F401
from test_chapter_judge_serial_admission import valid_metadata, SLOTS, offline  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
OWNED_SESSIONS = db.get_server_session


@pytest.fixture
def judge():
    return shared_judge


@pytest.fixture
async def bound(cohort, judge, monkeypatch):
    r=cohort
    r.budget.max_calls=128
    r.budget.max_tokens=2_000_000
    r.budget.metadata_json=valid_metadata(SCOPE)
    await r.session.commit()
    r.outcomes.update({0:[True],1:[True],2:[True]})
    r.settings.llm.critic.model=valid_metadata(SCOPE)['independent_acceptance']['request']['model']
    monkeypatch.setattr(judge,'complete_text',shared_judge.complete_text)
    monkeypatch.setattr(db,'get_server_session',OWNED_SESSIONS)
    factory=async_sessionmaker(r.engine,expire_on_commit=False)
    monkeypatch.setattr(db,'_global_session_factory',factory)
    async def run():
        with bind_execution_scope(SCOPE):
            return await judge.judge_chapter_commercial_quality_stable(
                r.session,r.settings,chapter_number=2,content_md=r.draft.content_md,
                samples=3,project_id=r.project.id,chapter_id=r.chapter.id,
                draft_id=r.draft.id,workflow_run_id=r.owner.id)
    return r,judge,run,factory


async def test_real_bound_preparation_slots_attempts_and_retained_reuse(bound):
    r,judge,run,factory=bound
    result=await asyncio.wait_for(run(),5)
    assert not result.infrastructure_failure
    assert [request.sample_slot for request in r.calls]==SLOTS
    assert len({request.prompt_version for request in r.calls})==1
    async with factory() as reader:
        attempts=list(await reader.scalars(select(ExecutionAttemptModel)))
        runs=list(await reader.scalars(select(LlmRunModel)))
        assert len(attempts)==len(runs)==3
        assert len({a.id for a in attempts})==3
        assert all(a.model_name==r.settings.llm.critic.model for a in attempts)
        assert all(a.actual_tokens==7 and a.status=='completed' for a in attempts)
    await r.session.refresh(r.owner)
    journal=deepcopy(r.owner.metadata_json['commercial_component_cohorts'])
    saved=next(iter(journal.values()))
    assert len(saved['slots'])==3
    assert all(slot['report'] for slot in saved['slots'].values())
    again=await asyncio.wait_for(run(),5)
    assert again.model_dump()==result.model_dump()
    assert len(r.calls)==3
    await r.session.refresh(r.owner)
    assert r.owner.metadata_json['commercial_component_cohorts']==journal


async def test_real_bound_partial_return_survives_typed_stop_without_replay(bound,monkeypatch):
    r,judge,run,factory=bound
    original=judge.judge_chapter_commercial_quality
    error=ExecutionBudgetError('fixture-preclaim-stop')
    entered=[]
    async def one(session,settings,**kw):
        entered.append(kw['sample_slot'])
        if kw['sample_slot']==SLOTS[1]: raise error
        return await original(session,settings,**kw)
    monkeypatch.setattr(judge,'judge_chapter_commercial_quality',one)
    with pytest.raises(ExecutionBudgetError) as caught:
        await run()
    assert caught.value is error and entered==SLOTS[:2]
    assert len(r.calls)==1 and r.calls[0].sample_slot==SLOTS[0]
    async with factory() as reader:
        assert len(list(await reader.scalars(select(ExecutionAttemptModel))))==1
        owner=await reader.get(type(r.owner),r.owner.id)
        journal=next(iter(owner.metadata_json['commercial_component_cohorts'].values()))
        assert len(journal['slots'])==1
        assert next(iter(journal['slots'].values()))['report']


async def test_real_bound_cancel_retains_both_completed_reports_and_attempts(bound,monkeypatch):
    r,judge,run,factory=bound
    native=judge.complete_text
    entered,release=asyncio.Event(),asyncio.Event()
    async def held_completion(session,settings,request):
        value=await native(session,settings,request)
        if request.sample_slot==SLOTS[1]:
            entered.set()
            await release.wait()
        return value
    monkeypatch.setattr(judge,'complete_text',held_completion)
    task=asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(),5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert [request.sample_slot for request in r.calls]==SLOTS[:2]
    async with factory() as reader:
        assert len(list(await reader.scalars(select(ExecutionAttemptModel))))==2
        owner=await reader.get(type(r.owner),r.owner.id)
        journal=next(iter(owner.metadata_json['commercial_component_cohorts'].values()))
        assert len(journal['slots'])==2
        assert all(slot['report'] for slot in journal['slots'].values())
