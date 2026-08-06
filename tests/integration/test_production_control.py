"""Operator intent must survive the pipeline rewriting projects.metadata.

The regression under test: a stop written by the web process used to live in
``projects.metadata``; the pipeline rewrote that whole JSONB block from its own
in-memory copy and erased the stop, so self-heal revived the book about a
minute later.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bestseller.infra.db.models import BookProductionControlModel, ProjectModel
from bestseller.services.production_control import (
    ProductionIntent,
    auto_recovery_is_permitted,
    claim_resume_pending,
    load_control_state,
    mark_resume_pending,
    request_pause,
    request_run,
    request_stop,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _test_database_url() -> str:
    url = os.environ.get("BESTSELLER__DATABASE__URL", "")
    if not url:
        pytest.skip("BESTSELLER__DATABASE__URL not set; see tests/conftest.py guard")
    return url


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_test_database_url(), future=True)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_conn: ProjectModel.__table__.create(sync_conn, checkfirst=True)
        )
        await connection.run_sync(
            lambda sync_conn: BookProductionControlModel.__table__.create(
                sync_conn, checkfirst=True
            )
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def project_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[UUID]:
    new_id = uuid4()
    async with session_factory() as session:
        session.add(
            ProjectModel(
                id=new_id,
                slug=f"test-control-{new_id.hex[:12]}",
                title="control fixture",
                genre="test",
                target_word_count=1,
                target_chapters=1,
                metadata_json={},
            )
        )
        await session.commit()
    yield new_id
    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM projects WHERE id = :pid"), {"pid": new_id}
        )
        await session.commit()


async def test_stop_survives_pipeline_rewriting_project_metadata(
    session_factory: async_sessionmaker[AsyncSession], project_id: UUID
) -> None:
    """The exact historical failure: whole-block metadata rewrite erases stop."""

    async with session_factory() as session:
        await request_stop(session, project_id, actor="web", reason="operator stop")
        await session.commit()

    # Pipeline worker checkpoint: rewrites metadata wholesale from its own copy,
    # exactly as services.pipelines does. This used to wipe the stop flag.
    async with session_factory() as session:
        project = await session.get(ProjectModel, project_id)
        assert project is not None
        project.metadata_json = {"planning_status": "ok", "chapter_cursor": 42}
        await session.commit()

    async with session_factory() as session:
        state = await load_control_state(session, project_id)

    assert state.intent is ProductionIntent.STOP
    assert not auto_recovery_is_permitted(state)


async def test_self_heal_cannot_arm_a_resume_for_a_stopped_book(
    session_factory: async_sessionmaker[AsyncSession], project_id: UUID
) -> None:
    async with session_factory() as session:
        await request_stop(session, project_id, actor="web", reason="operator stop")
        await session.commit()

    async with session_factory() as session:
        state = await mark_resume_pending(session, project_id, actor="self_heal")
        await session.commit()

    assert state.resume_pending is False
    async with session_factory() as session:
        assert await claim_resume_pending(session, project_id) is False


async def test_stop_landing_after_resume_is_armed_disarms_it(
    session_factory: async_sessionmaker[AsyncSession], project_id: UUID
) -> None:
    """The race that produced zombies: resume decided, stop arrives, resume fires."""

    async with session_factory() as session:
        await mark_resume_pending(session, project_id, actor="self_heal")
        await session.commit()

    async with session_factory() as session:
        await request_stop(session, project_id, actor="web", reason="operator stop")
        await session.commit()

    async with session_factory() as session:
        claimed = await claim_resume_pending(session, project_id)
        await session.commit()

    assert claimed is False
    async with session_factory() as session:
        state = await load_control_state(session, project_id)
    assert state.resume_pending is False
    assert state.intent is ProductionIntent.STOP


async def test_resume_intent_is_claimable_exactly_once(
    session_factory: async_sessionmaker[AsyncSession], project_id: UUID
) -> None:
    async with session_factory() as session:
        await mark_resume_pending(session, project_id, actor="self_heal")
        await session.commit()

    async with session_factory() as session:
        first = await claim_resume_pending(session, project_id)
        await session.commit()
    async with session_factory() as session:
        second = await claim_resume_pending(session, project_id)
        await session.commit()

    assert (first, second) == (True, False)


async def test_stale_claim_is_rejected_by_command_serial_cas(
    session_factory: async_sessionmaker[AsyncSession], project_id: UUID
) -> None:
    async with session_factory() as session:
        state = await mark_resume_pending(session, project_id, actor="self_heal")
        await session.commit()
    captured_serial = state.command_serial

    # An operator command lands after the recovery path captured its view.
    async with session_factory() as session:
        await request_pause(session, project_id, actor="web", reason="operator pause")
        await request_run(session, project_id, actor="web", reason="operator resume")
        await mark_resume_pending(session, project_id, actor="web")
        await session.commit()

    async with session_factory() as session:
        claimed = await claim_resume_pending(
            session, project_id, expected_command_serial=captured_serial
        )
        await session.commit()

    assert claimed is False


async def test_missing_control_row_defaults_to_runnable(
    session_factory: async_sessionmaker[AsyncSession], project_id: UUID
) -> None:
    """Books predating this table, and brand-new books, must still run."""

    async with session_factory() as session:
        state = await load_control_state(session, project_id)

    assert state.intent is ProductionIntent.RUN
    assert auto_recovery_is_permitted(state)
    assert state.command_serial == 0


async def test_pause_halts_auto_recovery_and_run_restores_it(
    session_factory: async_sessionmaker[AsyncSession], project_id: UUID
) -> None:
    async with session_factory() as session:
        await request_pause(session, project_id, actor="web", reason="inspect")
        await session.commit()
    async with session_factory() as session:
        paused = await load_control_state(session, project_id)
    assert not auto_recovery_is_permitted(paused)

    async with session_factory() as session:
        await request_run(session, project_id, actor="web", reason="looks fine")
        await session.commit()
    async with session_factory() as session:
        running = await load_control_state(session, project_id)

    assert auto_recovery_is_permitted(running)
    assert running.command_serial > paused.command_serial


async def test_unknown_desired_state_fails_closed(
    session_factory: async_sessionmaker[AsyncSession], project_id: UUID
) -> None:
    """A typo or a future enum value must not authorise auto-resume."""

    async with session_factory() as session:
        await request_run(session, project_id, actor="web")
        await session.commit()
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE book_production_control SET desired_state = 'quiesced' "
                "WHERE project_id = :pid"
            ),
            {"pid": project_id},
        )
        await session.commit()

    async with session_factory() as session:
        state = await load_control_state(session, project_id)

    assert state.intent is ProductionIntent.STOP
    assert not auto_recovery_is_permitted(state)
