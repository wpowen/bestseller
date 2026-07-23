from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.infra.db.models import PlanningArtifactVersionModel
from bestseller.services.conception_snapshots import (
    SNAPSHOT_STATUSES,
    _validate_snapshot_args,
    create_conception_snapshot_artifact,
    snapshot_hash,
    transition_conception_snapshot,
)


def test_snapshot_writer_contract_is_fail_closed() -> None:
    key = _validate_snapshot_args(
        artifact_type="conception_snapshot",
        status="validated",
        scope_ref_id=None,
        idempotency_key=" attempt-1 ",
    )
    assert key == "attempt-1"
    assert SNAPSHOT_STATUSES >= {"draft", "validated", "canonical", "failed"}
    with pytest.raises(ValueError, match="status"):
        _validate_snapshot_args(
            artifact_type="conception_snapshot",
            status="approved",
            scope_ref_id=None,
            idempotency_key="attempt-1",
        )
    with pytest.raises(ValueError, match="idempotency_key"):
        _validate_snapshot_args(
            artifact_type="creation_intent",
            status="draft",
            scope_ref_id=None,
            idempotency_key="",
        )


def test_snapshot_hash_is_canonical() -> None:
    assert snapshot_hash({"b": 2, "a": 1}) == snapshot_hash({"a": 1, "b": 2})
    assert snapshot_hash({"a": 1}) != snapshot_hash({"a": 2})


@pytest.mark.asyncio
async def test_same_idempotency_key_reuses_identical_snapshot() -> None:
    project_id = uuid4()
    existing = PlanningArtifactVersionModel(
        project_id=project_id,
        artifact_type="conception_snapshot",
        version_no=1,
        status="validated",
        schema_version="1.0",
        content={"title": "same"},
        idempotency_key="attempt-1",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=[existing])
    result = await create_conception_snapshot_artifact(
        session,
        project_id=project_id,
        artifact_type="conception_snapshot",
        content={"title": "same"},
        status="validated",
        idempotency_key="attempt-1",
    )
    assert result is existing
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_transition_records_audit_and_rejects_legacy_approved() -> None:
    artifact = PlanningArtifactVersionModel(
        project_id=uuid4(),
        artifact_type="conception_snapshot",
        version_no=1,
        status="draft",
        schema_version="1.0",
        content={"title": "same"},
        idempotency_key="attempt-1",
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=artifact)
    transitioned = await transition_conception_snapshot(
        session,
        artifact.id,
        to_status="validated",
        actor="system",
        reason="schema gates passed",
    )
    assert transitioned.status == "validated"
    assert transitioned.content["_snapshot_audit"][0]["from_state"] == "draft"
    with pytest.raises(ValueError, match="invalid conception snapshot status"):
        await transition_conception_snapshot(
            session,
            artifact.id,
            to_status="approved",
            actor="system",
            reason="legacy status must not be accepted",
        )
