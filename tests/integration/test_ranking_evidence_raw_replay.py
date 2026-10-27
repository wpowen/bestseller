"""Raw-ledger export and independent verifier replay."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bestseller.domain.ranking_evidence import canonical_json_hash
from bestseller.infra.db.models import (
    ProjectModel,
    RankingEvaluationRunModel,
    RankingRawEvidenceEventModel,
)
from bestseller.infra.db.session import create_engine, create_session_factory
from bestseller.services.ranking_evidence_store import RankingEvidenceStore
from bestseller.services.ranking_evidence_verifier import verify_raw_ledger

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def replay_db() -> AsyncIterator[tuple[async_sessionmaker, UUID]]:
    engine: AsyncEngine = create_engine()
    if engine.dialect.name != "postgresql":
        await engine.dispose()
        pytest.skip("ranking replay tests require PostgreSQL")
    project_id = uuid4()
    factory = create_session_factory(engine=engine)
    async with factory() as session:
        session.add(
            ProjectModel(
                id=project_id,
                slug=f"replay-{project_id.hex}",
                title="Replay",
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


@pytest.mark.asyncio
async def test_exported_raw_events_replay_to_same_root(replay_db) -> None:
    factory, project_id = replay_db
    async with factory() as session:
        run = RankingEvaluationRunModel(
            project_id=project_id,
            candidate_revision="rev-1",
            candidate_hash="a" * 64,
            preregistration_json={},
            preregistration_hash="b" * 64,
            analysis_plan_json={},
            analysis_plan_hash="c" * 64,
            required_cell_manifest_json={"cells": ["g0"]},
            required_cell_manifest_hash="d" * 64,
            roster_json={"members": ["x"]},
            roster_hash="e" * 64,
            sealed_candidate_manifest_hash="f" * 64,
        )
        session.add(run)
        await session.flush()
        store = RankingEvidenceStore(session)
        await store.append_event(
            run.id,
            evidence_layer="structure",
            event_type="sample",
            payload={"value": "中文"},
            candidate_revision="rev-1",
            candidate_hash="a" * 64,
            source="test",
            ingest_idempotency_key=f"one-{run.id}",
        )
        await store.append_event(
            run.id,
            evidence_layer="structure",
            event_type="sample",
            payload={"value": 2},
            candidate_revision="rev-1",
            candidate_hash="a" * 64,
            source="test",
            ingest_idempotency_key=f"two-{run.id}",
        )
        await session.commit()
        rows = (
            (
                await session.execute(
                    select(RankingRawEvidenceEventModel)
                    .where(RankingRawEvidenceEventModel.run_id == run.id)
                    .order_by(RankingRawEvidenceEventModel.sequence)
                )
            )
            .scalars()
            .all()
        )
    exported = [
        {
            "run_id": str(row.run_id),
            "event_uuid": str(row.event_uuid),
            "sequence": row.sequence,
            "evidence_layer": row.evidence_layer,
            "event_type": row.event_type,
            "occurred_at": row.occurred_at.isoformat().replace("+00:00", "Z"),
            "source": row.source,
            "session_hash": row.session_hash,
            "assignment_hash": row.assignment_hash,
            "cluster_hash": row.cluster_hash,
            "subject_hash": row.subject_hash,
            "candidate_revision": row.candidate_revision,
            "candidate_hash": row.candidate_hash,
            "payload": row.payload_json,
            "payload_hash": row.payload_hash,
            "previous_event_hash": row.previous_event_hash,
            "event_hash": row.event_hash,
            "collector_identity": row.collector_identity,
            "collector_version": row.collector_version,
            "key_id": row.key_id,
            "auth_receipt": row.auth_receipt,
            "ingest_idempotency_key": row.ingest_idempotency_key,
            "validation_status": row.validation_status,
            "invalid_code": row.invalid_code,
        }
        for row in rows
    ]
    result = verify_raw_ledger(exported, run_id=str(run.id))
    assert result.valid and result.event_count == 2 and result.ledger_root == rows[-1].event_hash
    assert rows[0].payload_hash == canonical_json_hash({"value": "中文"})


@pytest.mark.asyncio
async def test_replay_detects_middle_event_tamper(replay_db) -> None:
    factory, project_id = replay_db
    async with factory() as session:
        run = RankingEvaluationRunModel(
            project_id=project_id,
            candidate_revision="rev-1",
            candidate_hash="a" * 64,
            preregistration_json={},
            preregistration_hash="b" * 64,
            analysis_plan_json={},
            analysis_plan_hash="c" * 64,
            required_cell_manifest_json={"cells": ["g0"]},
            required_cell_manifest_hash="d" * 64,
            roster_json={"members": ["x"]},
            roster_hash="e" * 64,
            sealed_candidate_manifest_hash="f" * 64,
        )
        session.add(run)
        await session.flush()
        store = RankingEvidenceStore(session)
        for index in range(3):
            await store.append_event(
                run.id,
                evidence_layer="structure",
                event_type="sample",
                payload={"value": index},
                candidate_revision="rev-1",
                candidate_hash="a" * 64,
                source="test",
                ingest_idempotency_key=f"event-{index}-{run.id}",
            )
        await session.commit()
        rows = (
            (
                await session.execute(
                    select(RankingRawEvidenceEventModel)
                    .where(RankingRawEvidenceEventModel.run_id == run.id)
                    .order_by(RankingRawEvidenceEventModel.sequence)
                )
            )
            .scalars()
            .all()
        )
    exported = [
        {
            "run_id": str(row.run_id),
            "event_uuid": str(row.event_uuid),
            "sequence": row.sequence,
            "evidence_layer": row.evidence_layer,
            "event_type": row.event_type,
            "occurred_at": row.occurred_at.isoformat().replace("+00:00", "Z"),
            "source": row.source,
            "candidate_revision": row.candidate_revision,
            "candidate_hash": row.candidate_hash,
            "payload": row.payload_json,
            "payload_hash": row.payload_hash,
            "previous_event_hash": row.previous_event_hash,
            "event_hash": row.event_hash,
            "collector_identity": row.collector_identity,
            "collector_version": row.collector_version,
            "ingest_idempotency_key": row.ingest_idempotency_key,
            "validation_status": row.validation_status,
        }
        for row in rows
    ]
    exported[1]["payload"] = {"value": "tampered"}
    result = verify_raw_ledger(exported, run_id=str(rows[0].run_id))
    assert not result.valid and result.code == "EVENT_HASH_MISMATCH"
