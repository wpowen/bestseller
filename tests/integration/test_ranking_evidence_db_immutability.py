"""PostgreSQL negative tests for the evidence ledger authority boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bestseller.infra.db.models import ProjectModel, RankingEvaluationRunModel
from bestseller.infra.db.session import create_engine, create_session_factory
from bestseller.services.ranking_evidence_store import RankingEvidenceStore

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def ledger_db() -> AsyncIterator[tuple[async_sessionmaker, UUID]]:
    engine: AsyncEngine = create_engine()
    if engine.dialect.name != "postgresql":
        await engine.dispose()
        pytest.skip("ranking authority tests require PostgreSQL")
    project_id = uuid4()
    factory = create_session_factory(engine=engine)
    async with factory() as session:
        session.add(
            ProjectModel(
                id=project_id,
                slug=f"ledger-{project_id.hex}",
                title="Ledger",
                genre="test",
                target_word_count=100,
                target_chapters=1,
                metadata_json={},
            )
        )
        await session.commit()
    try:
        yield factory, project_id
    finally:
        async with factory() as session:
            await session.execute(delete(ProjectModel).where(ProjectModel.id == project_id))
            await session.commit()
        await engine.dispose()


async def _run(factory: async_sessionmaker, project_id: UUID) -> RankingEvaluationRunModel:
    async with factory() as session:
        run = await RankingEvidenceStore(session).create_run(
            project_id=project_id,
            candidate_revision="rev-1",
            candidate_hash="a" * 64,
            preregistration_json={},
            preregistration_hash="b" * 64,
            analysis_plan_json={},
            analysis_plan_hash="c" * 64,
            required_cell_manifest_json={"cells": ["g0"]},
            required_cell_manifest_hash="d" * 64,
            roster_json={"members": ["candidate"]},
            roster_hash="e" * 64,
            sealed_candidate_manifest_hash="f" * 64,
        )
        await session.commit()
        return run


async def _append(factory: async_sessionmaker, run_id: UUID, key: str) -> None:
    async with factory() as session:
        await RankingEvidenceStore(session).append_event(
            run_id,
            evidence_layer="structure",
            event_type="sample",
            payload={"key": key},
            candidate_revision="rev-1",
            candidate_hash="a" * 64,
            source="test",
            ingest_idempotency_key=key,
        )
        await session.commit()


@pytest.mark.asyncio
async def test_direct_update_delete_middle_tail_and_truncate_are_rejected(ledger_db) -> None:
    factory, project_id = ledger_db
    run = await _run(factory, project_id)
    await _append(factory, run.id, f"a-{run.id}")
    await _append(factory, run.id, f"b-{run.id}")
    checks = [
        "UPDATE ranking_raw_evidence_events SET source='tampered' WHERE run_id=:id AND sequence=1",
        "DELETE FROM ranking_raw_evidence_events WHERE run_id=:id AND sequence=1",
        "DELETE FROM ranking_raw_evidence_events WHERE run_id=:id AND sequence=2",
        "TRUNCATE ranking_raw_evidence_events",
    ]
    for statement in checks:
        async with factory() as session:
            with pytest.raises(DBAPIError):
                await session.execute(text(statement), {"id": run.id})
                await session.commit()
            await session.rollback()


@pytest.mark.asyncio
async def test_idempotency_and_rollback_reuse_sequence(ledger_db) -> None:
    factory, project_id = ledger_db
    run = await _run(factory, project_id)
    async with factory() as session:
        store = RankingEvidenceStore(session)
        first = await store.append_event(
            run.id,
            evidence_layer="structure",
            event_type="x",
            payload={"n": 1},
            candidate_revision="rev-1",
            candidate_hash="a" * 64,
            source="test",
            ingest_idempotency_key=f"same-{run.id}",
        )
        await session.commit()
    async with factory() as session:
        replay = await RankingEvidenceStore(session).append_event(
            run.id,
            evidence_layer="structure",
            event_type="x",
            payload={"different": True},
            candidate_revision="rev-1",
            candidate_hash="a" * 64,
            source="test",
            ingest_idempotency_key=f"same-{run.id}",
        )
        assert replay.id == first.id and replay.sequence == 1
        await session.commit()
    async with factory() as session:
        await RankingEvidenceStore(session).append_event(
            run.id,
            evidence_layer="structure",
            event_type="rolled_back",
            payload={},
            candidate_revision="rev-1",
            candidate_hash="a" * 64,
            source="test",
            ingest_idempotency_key=f"rollback-{run.id}",
        )
        await session.rollback()
    await _append(factory, run.id, f"after-rollback-{run.id}")
    async with factory() as session:
        row = await session.scalar(
            text(
                "SELECT sequence FROM ranking_raw_evidence_events "
                "WHERE run_id=:id AND ingest_idempotency_key=:key"
            ),
            {"id": run.id, "key": f"after-rollback-{run.id}"},
        )
        assert row == 2


@pytest.mark.asyncio
async def test_same_run_concurrent_appends_are_contiguous(ledger_db) -> None:
    factory, project_id = ledger_db
    run = await _run(factory, project_id)
    await asyncio.gather(
        *[_append(factory, run.id, f"concurrent-{index}-{run.id}") for index in range(8)]
    )
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT sequence FROM ranking_raw_evidence_events "
                        "WHERE run_id=:id ORDER BY sequence"
                    ),
                    {"id": run.id},
                )
            )
            .scalars()
            .all()
        )
        assert rows == list(range(1, 9))


@pytest.mark.asyncio
async def test_finalized_run_rejects_insert(ledger_db) -> None:
    factory, project_id = ledger_db
    run = await _run(factory, project_id)
    async with factory() as session:
        await RankingEvidenceStore(session).finalize_run(run.id, verdict="INCONCLUSIVE")
        await session.commit()
    async with factory() as session:
        with pytest.raises((DBAPIError, ValueError)):
            await RankingEvidenceStore(session).append_event(
                run.id,
                evidence_layer="structure",
                event_type="late",
                payload={},
                candidate_revision="rev-1",
                candidate_hash="a" * 64,
                source="test",
                ingest_idempotency_key=f"late-{run.id}",
            )
