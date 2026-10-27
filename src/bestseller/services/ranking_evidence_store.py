"""Transactional writer for the ranking evidence ledger.

The store is deliberately small and boring: the database owns ordering and
the raw event table is never used as a mutable report cache.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.ranking_evidence import canonical_json_hash, canonical_timestamp
from bestseller.infra.db.models import RankingEvaluationRunModel, RankingRawEvidenceEventModel


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class RankingEvidenceStore:
    """Append and finalize evidence using one transaction per operation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_run(self, **values: Any) -> RankingEvaluationRunModel:  # noqa: ANN401
        run = RankingEvaluationRunModel(**values)
        self.session.add(run)
        await self.session.flush()
        return run

    async def append_event(
        self,
        run_id: UUID,
        *,
        evidence_layer: str,
        event_type: str,
        payload: dict[str, Any],
        candidate_revision: str,
        candidate_hash: str,
        source: str,
        ingest_idempotency_key: str,
        collector_identity: str = "unknown",
        collector_version: str = "unknown",
        occurred_at: datetime | None = None,
        session_hash: str | None = None,
        assignment_hash: str | None = None,
        cluster_hash: str | None = None,
        subject_hash: str | None = None,
        key_id: str | None = None,
        auth_receipt: str | None = None,
    ) -> RankingRawEvidenceEventModel:
        dialect = self.session.bind.dialect.name if self.session.bind is not None else "postgresql"
        if dialect == "postgresql":
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:run_id, 0))"),
                {"run_id": str(run_id)},
            )
        existing = await self.session.scalar(
            select(RankingRawEvidenceEventModel).where(
                RankingRawEvidenceEventModel.ingest_idempotency_key == ingest_idempotency_key
            )
        )
        if existing is not None:
            return existing
        stmt = (
            select(RankingEvaluationRunModel)
            .where(RankingEvaluationRunModel.id == run_id)
            .with_for_update()
        )
        run = await self.session.scalar(stmt)
        if run is None:
            raise ValueError(f"ranking evaluation run not found: {run_id}")
        if run.status == "finalized":
            raise ValueError("cannot append to a finalized ranking run")
        occurred = occurred_at or datetime.now(UTC)
        occurred_wire = canonical_timestamp(occurred)
        payload_hash = canonical_json_hash(payload)
        previous = run.last_event_hash
        sequence = run.next_event_sequence
        event_uuid = uuid4()
        envelope = {
            "run_id": str(run_id),
            "event_uuid": str(event_uuid),
            "sequence": sequence,
            "previous_event_hash": previous,
            "evidence_layer": evidence_layer,
            "event_type": event_type,
            "occurred_at": occurred_wire,
            "source": source,
            "session_hash": session_hash,
            "assignment_hash": assignment_hash,
            "cluster_hash": cluster_hash,
            "subject_hash": subject_hash,
            "candidate_revision": candidate_revision,
            "candidate_hash": candidate_hash,
            "collector_identity": collector_identity,
            "collector_version": collector_version,
            "key_id": key_id,
            "auth_receipt": auth_receipt,
            "ingest_idempotency_key": ingest_idempotency_key,
            "validation_status": "valid",
            "invalid_code": None,
        }
        from bestseller.services.ranking_evidence_verifier import event_hash

        event_hash_value = event_hash(
            {**envelope, "payload": payload, "payload_hash": payload_hash}
        )
        event = RankingRawEvidenceEventModel(
            run_id=run_id,
            event_uuid=event_uuid,
            sequence=sequence,
            evidence_layer=evidence_layer,
            event_type=event_type,
            occurred_at=occurred,
            source=source,
            session_hash=session_hash,
            assignment_hash=assignment_hash,
            cluster_hash=cluster_hash,
            subject_hash=subject_hash,
            candidate_revision=candidate_revision,
            candidate_hash=candidate_hash,
            payload_json=payload,
            payload_hash=payload_hash,
            previous_event_hash=previous,
            event_hash=event_hash_value,
            collector_identity=collector_identity,
            collector_version=collector_version,
            key_id=key_id,
            auth_receipt=auth_receipt,
            ingest_idempotency_key=ingest_idempotency_key,
        )
        self.session.add(event)
        run.next_event_sequence = sequence + 1
        run.last_event_hash = event_hash_value
        if run.status == "planned":
            run.status = "running"
        await self.session.flush()
        return event

    async def finalize_run(self, run_id: UUID, **kwargs: Any) -> RankingRawEvidenceEventModel:  # noqa: ANN401
        run = await self.session.scalar(
            select(RankingEvaluationRunModel)
            .where(RankingEvaluationRunModel.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise ValueError(f"ranking evaluation run not found: {run_id}")
        if run.status == "finalized":
            raise ValueError("ranking evaluation run already finalized")
        event = await self.append_event(
            run_id,
            evidence_layer="authority",
            event_type="RUN_FINALIZED",
            payload=kwargs,
            candidate_revision=run.candidate_revision,
            candidate_hash=run.candidate_hash,
            source="ranking_evidence_store",
            ingest_idempotency_key=f"{run_id}:RUN_FINALIZED",
            collector_identity="ranking_evidence_store",
            collector_version="v1",
        )
        run.status = "finalized"
        run.finalized_at = datetime.now(UTC)
        await self.session.flush()
        return event
