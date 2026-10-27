"""Synthetic file-SQLite invariants; never connects to PG, network or a model."""
from pathlib import Path
import socket
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable

from bestseller.services import conception_checkpoints as cp
from bestseller.infra.db.models import WorkflowRunModel

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
ROOT = Path(__file__).parent


@compiles(JSONB, 'sqlite')
def sqlite_jsonb(type_, compiler, **kwargs):
    return 'JSON'


def checkpoint():
    # Synthetic candidate data, not a historical snapshot, quality or paid receipt.
    return cp.build_conception_checkpoint({
        'premise': 'A surveyor investigates a failed observatory.',
        'title': 'Offline observatory fixture',
        'synopsis': 'The surveyor follows conflicting maintenance records.',
        'writing_profile': {}, 'story_spine': {}, 'concept_contract': {},
        'ctx': {'fixture_only': True}, 'tags': [], 'commercial_brief': {},
        'world_model_payload': {}, 'conception_log': [], 'llm_run_ids': [],
        'chapter_count': 4, 'genre_key': 'synthetic-observatory',
        'completed_candidates': [{'id': 'synthetic-candidate-1', 'text': 'retained fixture'}],
    }, input_hash='synthetic-input')


def replay():
    return cp.build_replay_context(
        {'avoid_names': ['synthetic-prior-name'], 'fixture_only': True},
        input_hash='synthetic-input', genre_key='synthetic-observatory', chapter_count=4,
    )


def observe(name, **values):
    # Runtime regression never rewrites the immutable original red evidence.
    return None


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('network forbidden in offline checkpoint audit')
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)


@pytest.fixture
async def workflow_db(tmp_path):
    path = tmp_path / 'checkpoint_resume_integrity_test.sqlite3'
    assert path.name.endswith('_test.sqlite3')
    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    async with engine.begin() as conn:
        identity = (await conn.execute(text('PRAGMA database_list'))).all()
        assert Path(next(row[2] for row in identity if row[1] == 'main')) == path
        await conn.exec_driver_sql(str(CreateTable(WorkflowRunModel.__table__).compile(
            dialect=conn.dialect)))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed(factory):
    wid = uuid4()
    async with factory() as session:
        session.add(WorkflowRunModel(id=wid, project_id=None, workflow_type='conception_initial',
                                    status='failed', metadata_json={'protected': 'keep'}))
        await session.commit()
    return wid


async def save_value(session, wid, kind, value):
    if kind == 'checkpoint':
        await cp.save_conception_checkpoint(session, workflow_run_id=wid, checkpoint=value)
    else:
        await cp.save_replay_context(session, workflow_run_id=wid, record=value)


async def load_value(session, wid, kind):
    if kind == 'checkpoint':
        return await cp.load_conception_checkpoint(session, workflow_run_id=wid,
                                                  input_hash='synthetic-input')
    return await cp.load_replay_context(session, workflow_run_id=wid,
                                        input_hash='synthetic-input')


async def test_checkpoint_save_must_preserve_concurrently_frozen_context(workflow_db):
    wid = await seed(workflow_db)
    frozen, candidate = replay(), checkpoint()
    async with workflow_db() as stale:
        held_row = await stale.get(WorkflowRunModel, wid)
        await stale.commit()  # end read transaction while keeping the identity-map row
        async with workflow_db() as other:
            await cp.save_replay_context(other, workflow_run_id=wid, record=frozen)
            await other.commit()
        before = await stale.scalar(select(WorkflowRunModel.metadata_json).where(
            WorkflowRunModel.id == wid))
        assert before[cp.REPLAY_CONTEXT_KEY] == frozen
        assert cp.REPLAY_CONTEXT_KEY not in held_row.metadata_json
        await cp.save_conception_checkpoint(stale, workflow_run_id=wid, checkpoint=candidate)
        await stale.commit()
    async with workflow_db() as fresh:
        row = await fresh.get(WorkflowRunModel, wid)
        assert row.metadata_json[cp.CHECKPOINT_KEY] == candidate
        assert row.metadata_json['protected'] == 'keep'
        observe('lost_frozen_context', before_keys=sorted(before),
                after_keys=sorted(row.metadata_json),
                frozen_context_present=cp.REPLAY_CONTEXT_KEY in row.metadata_json)
        assert row.metadata_json.get(cp.REPLAY_CONTEXT_KEY) == frozen


@pytest.mark.parametrize('kind', ['checkpoint', 'replay'])
async def test_loader_must_observe_committed_candidate_or_context(workflow_db, kind):
    wid = await seed(workflow_db)
    value = checkpoint() if kind == 'checkpoint' else replay()
    async with workflow_db() as stale:
        held_row = await stale.get(WorkflowRunModel, wid)
        await stale.commit()
        async with workflow_db() as other:
            await save_value(other, wid, kind, value)
            await other.commit()
        assert held_row.metadata_json == {'protected': 'keep'}
        observed = await load_value(stale, wid, kind)
        async with workflow_db() as fresh:
            current = await load_value(fresh, wid, kind)
        assert current == value
        observe(f'missed_{kind}', stale_loader=observed, fresh_loader=current,
                fresh_has_completed_candidate=bool(
                    kind == 'checkpoint' and current['state']['completed_candidates']))
        assert observed == current


async def test_checkpoint_save_must_recheck_newly_linked_historical_owner(workflow_db):
    wid = await seed(workflow_db)
    pid = uuid4()
    async with workflow_db() as stale:
        held_row = await stale.get(WorkflowRunModel, wid)
        await stale.commit()
        async with workflow_db() as other:
            await other.execute(update(WorkflowRunModel).where(WorkflowRunModel.id == wid).values(
                project_id=pid, status='completed'))
            await other.commit()
        assert held_row.project_id is None
        rejected = False
        try:
            await cp.save_conception_checkpoint(stale, workflow_run_id=wid,
                                                checkpoint=checkpoint())
        except ValueError as error:
            assert 'project-less' in str(error)
            rejected = True
        await stale.commit()
    async with workflow_db() as fresh:
        row = await fresh.get(WorkflowRunModel, wid)
        assert row.project_id == pid and row.status == 'completed'
        observe('historical_owner_bypass', rejected=rejected, linked=True,
                checkpoint_written=cp.CHECKPOINT_KEY in row.metadata_json)
        assert rejected


async def test_fresh_checkpoint_save_preserves_frozen_context_control(workflow_db):
    wid = await seed(workflow_db)
    frozen = replay()
    async with workflow_db() as first:
        await cp.save_replay_context(first, workflow_run_id=wid, record=frozen)
        await first.commit()
    async with workflow_db() as second:
        await cp.save_conception_checkpoint(second, workflow_run_id=wid, checkpoint=checkpoint())
        await second.commit()
    async with workflow_db() as fresh:
        row = await fresh.get(WorkflowRunModel, wid)
        assert row.metadata_json[cp.REPLAY_CONTEXT_KEY] == frozen


async def test_fresh_checkpoint_save_rejects_linked_owner_control(workflow_db):
    wid = await seed(workflow_db)
    async with workflow_db() as other:
        await other.execute(update(WorkflowRunModel).where(WorkflowRunModel.id == wid).values(
            project_id=uuid4(), status='completed'))
        await other.commit()
    async with workflow_db() as fresh:
        with pytest.raises(ValueError, match='project-less'):
            await cp.save_conception_checkpoint(fresh, workflow_run_id=wid, checkpoint=checkpoint())


async def test_replay_save_refreshes_and_preserves_concurrent_checkpoint_control(workflow_db):
    wid = await seed(workflow_db)
    candidate = checkpoint()
    async with workflow_db() as stale:
        held_row = await stale.get(WorkflowRunModel, wid)
        await stale.commit()
        async with workflow_db() as other:
            await cp.save_conception_checkpoint(other, workflow_run_id=wid, checkpoint=candidate)
            await other.commit()
        assert cp.CHECKPOINT_KEY not in held_row.metadata_json
        await cp.save_replay_context(stale, workflow_run_id=wid, record=replay())
        await stale.commit()
    async with workflow_db() as fresh:
        row = await fresh.get(WorkflowRunModel, wid)
        assert row.metadata_json[cp.CHECKPOINT_KEY] == candidate
        assert row.metadata_json[cp.REPLAY_CONTEXT_KEY] == replay()
