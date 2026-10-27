"""Missing optional snapshots are not failed source reads; no provider or PG."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable

from bestseller.domain.context import ChapterStateSnapshotContext
from bestseller.infra.db.models import ChapterStateSnapshotModel
from bestseller.services import context, drafts, reviews
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@compiles(JSONB, 'sqlite')
def _jsonb(_type, _compiler, **_kwargs):
    return 'JSON'


@pytest.fixture
async def sqlite_session(tmp_path):
    path = tmp_path / 'context_snapshot_source_test.sqlite3'
    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    async with engine.begin() as conn:
        ddl = str(CreateTable(ChapterStateSnapshotModel.__table__).compile(dialect=conn.dialect))
        await conn.exec_driver_sql(ddl.replace('::jsonb', ''))
        await conn.exec_driver_sql('CREATE TABLE caller_work (id INTEGER PRIMARY KEY, value TEXT)')
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.mark.parametrize('chapter_number', [1, 2, 9])
async def test_real_absent_snapshot_returns_none_without_inventing_facts(sqlite_session, chapter_number):
    assert await context._safe_load_previous_snapshot(sqlite_session,
        project_id=uuid4(), current_chapter_number=chapter_number) is None
    assert not sqlite_session.in_nested_transaction()


async def test_first_chapter_performs_no_snapshot_read(sqlite_session):
    statements = []
    connection = await sqlite_session.connection()
    listener = lambda _c, _cur, statement, *_: statements.append(statement)
    event.listen(connection.sync_connection, 'before_cursor_execute', listener)
    try:
        assert await context._safe_load_previous_snapshot(sqlite_session,
            project_id=uuid4(), current_chapter_number=1) is None
    finally:
        event.remove(connection.sync_connection, 'before_cursor_execute', listener)
    assert not statements


async def test_successful_snapshot_is_returned_exactly(sqlite_session, monkeypatch):
    snapshot = ChapterStateSnapshotContext(chapter_number=1, facts=[])
    loader = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(context, 'load_previous_chapter_snapshot', loader)
    result = await context._safe_load_previous_snapshot(sqlite_session,
        project_id=uuid4(), current_chapter_number=2)
    assert result is snapshot and loader.await_count == 1


async def test_sqlite_read_failure_rolls_back_nested_work_and_preserves_outer_transaction(sqlite_session, monkeypatch):
    session = sqlite_session
    await session.execute(text("INSERT INTO caller_work VALUES (1, 'outer caller work')"))

    async def failed_read(s, **_kwargs):
        assert s.in_nested_transaction()
        await s.execute(text("UPDATE caller_work SET value='uncommitted nested change' WHERE id=1"))
        return await s.scalar(text('SELECT * FROM nonexistent_snapshot_read_source'))

    monkeypatch.setattr(context, 'load_previous_chapter_snapshot', failed_read)
    with pytest.raises(RuntimeError, match='source_context_unavailable: previous_chapter_snapshot') as caught:
        await context._safe_load_previous_snapshot(session, project_id=uuid4(), current_chapter_number=2)
    assert type(caught.value.__cause__).__name__ == 'OperationalError'
    assert not isinstance(caught.value, ValueError)
    assert session.in_transaction() and not session.in_nested_transaction()
    assert await session.scalar(text('SELECT value FROM caller_work WHERE id=1')) == 'outer caller work'
    await session.rollback()
    assert await session.scalar(text('SELECT count(*) FROM caller_work')) == 0


async def test_savepoint_refusal_is_unknown_not_an_absent_source():
    class RefusedReadAdapter:
        def begin_nested(self):
            raise RuntimeError('adapter refused savepoint')

    with pytest.raises(RuntimeError, match='source_context_unavailable') as caught:
        await context._safe_load_previous_snapshot(RefusedReadAdapter(),
            project_id=uuid4(), current_chapter_number=2)
    assert str(caught.value.__cause__) == 'adapter refused savepoint'


async def test_cancellation_is_not_converted_to_missing_snapshot(sqlite_session, monkeypatch):
    async def cancelled_read(_session, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(context, 'load_previous_chapter_snapshot', cancelled_read)
    with pytest.raises(asyncio.CancelledError):
        await context._safe_load_previous_snapshot(sqlite_session, project_id=uuid4(), current_chapter_number=2)
    assert not sqlite_session.in_nested_transaction()
    assert await sqlite_session.scalar(text('SELECT count(*) FROM caller_work')) == 0


@pytest.mark.parametrize('entrypoint', ['chapter_writer', 'chapter_review', 'scene_review'])
async def test_real_entrypoint_cannot_pay_after_snapshot_source_read_failure(sqlite_session, monkeypatch, entrypoint):
    """Only upstream context assembly is abbreviated; the failed read is real SQLite.

    Exercise the actual writer/reviewer entry and its optional ValueError catch,
    rather than testing a caller invented solely for the regression.
    """
    project = SimpleNamespace(id=uuid4(), slug='unrelated-ocean-survey')
    chapter = SimpleNamespace(id=uuid4(), project_id=project.id, chapter_number=2,
                              status='planned', metadata_json={})
    scene = SimpleNamespace(id=uuid4())
    settings = load_settings(env={})
    provider = AsyncMock(side_effect=AssertionError('provider must never be reached'))
    monkeypatch.setattr(drafts, 'complete_text', provider)
    monkeypatch.setattr(reviews, 'complete_text', provider)

    async def failed_read(s, **_kwargs):
        return await s.scalar(text('SELECT * FROM nonexistent_snapshot_read_source'))

    async def build_until_snapshot(*_args, **_kwargs):
        return await context._safe_load_previous_snapshot(sqlite_session,
            project_id=project.id, current_chapter_number=2)

    monkeypatch.setattr(context, 'load_previous_chapter_snapshot', failed_read)
    if entrypoint == 'chapter_writer':
        caller = SimpleNamespace(scalar=AsyncMock(return_value=chapter),
            scalars=AsyncMock(return_value=[scene]), get=AsyncMock(return_value=None))
        monkeypatch.setattr(drafts, 'get_project_by_slug', AsyncMock(return_value=project))
        monkeypatch.setattr(drafts, 'build_chapter_writer_context', build_until_snapshot)
        action = drafts.generate_chapter_draft_once(caller, project.slug, 2, settings=settings)
    elif entrypoint == 'chapter_review':
        monkeypatch.setattr(reviews, '_load_chapter_context',
            AsyncMock(return_value=(project, chapter, None, [scene], SimpleNamespace())))
        monkeypatch.setattr(reviews, 'build_chapter_writer_context', build_until_snapshot)
        action = reviews.review_chapter_draft(sqlite_session, settings, project.slug, 2)
    else:
        monkeypatch.setattr(reviews, '_load_scene_context',
            AsyncMock(return_value=(project, chapter, scene, None, SimpleNamespace())))
        monkeypatch.setattr(reviews, 'build_scene_writer_context', build_until_snapshot)
        action = reviews.review_scene_draft(sqlite_session, settings, project.slug, 2, 1)
    with pytest.raises(RuntimeError, match='source_context_unavailable'):
        await action
    provider.assert_not_awaited()
    assert not sqlite_session.in_nested_transaction()
    assert await sqlite_session.scalar(text('SELECT count(*) FROM caller_work')) == 0
