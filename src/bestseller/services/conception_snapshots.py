"""Persistence primitives for versioned creation intent/conception snapshots.

This module intentionally stays separate from the legacy approved-artifact
writer.  Snapshot status is explicit and no implicit ``approved`` default is
allowed for the two creation-boundary artifact types.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel

SNAPSHOT_ARTIFACT_TYPES = frozenset({"creation_intent", "conception_snapshot"})
SNAPSHOT_STATUSES = frozenset(
    {
        "draft",
        "validated",
        "pending_user_approval",
        "candidate_v2",
        "reconciling",
        "blocked_hard_conflict",
        "canonical",
        "superseded",
        "failed",
    }
)

_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"validated", "failed"}),
    "validated": frozenset({"canonical", "candidate_v2", "pending_user_approval", "failed"}),
    "candidate_v2": frozenset({"reconciling", "blocked_hard_conflict", "failed"}),
    "reconciling": frozenset(
        {"canonical", "pending_user_approval", "blocked_hard_conflict", "failed"}
    ),
    "pending_user_approval": frozenset({"canonical", "failed"}),
    "canonical": frozenset({"superseded"}),
    "blocked_hard_conflict": frozenset({"reconciling", "failed"}),
    "superseded": frozenset(),
    "failed": frozenset(),
}


def snapshot_hash(content: object) -> str:
    payload = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_snapshot_args(
    *,
    artifact_type: str,
    status: str,
    scope_ref_id: UUID | None,
    idempotency_key: str | None,
) -> str:
    if artifact_type not in SNAPSHOT_ARTIFACT_TYPES:
        raise ValueError(f"snapshot writer does not support artifact_type={artifact_type!r}")
    if status not in SNAPSHOT_STATUSES:
        raise ValueError(f"invalid conception snapshot status: {status!r}")
    if scope_ref_id is not None:
        raise ValueError("creation-boundary snapshots must be project-level (scope_ref_id=None)")
    key = (idempotency_key or "").strip()
    if not key:
        raise ValueError("idempotency_key is required for conception snapshots")
    return key


async def create_conception_snapshot_artifact(
    session: AsyncSession,
    *,
    project_id: UUID,
    artifact_type: str,
    content: dict[str, Any],
    status: str,
    idempotency_key: str,
    schema_version: str = "1.0",
    source_run_id: UUID | None = None,
    notes: str | None = None,
    created_by: str = "system",
) -> PlanningArtifactVersionModel:
    """Create or reuse one project-level snapshot by deterministic key."""
    key = _validate_snapshot_args(
        artifact_type=artifact_type,
        status=status,
        scope_ref_id=None,
        idempotency_key=idempotency_key,
    )
    existing = await session.scalar(
        select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == artifact_type,
            PlanningArtifactVersionModel.scope_ref_id.is_(None),
            PlanningArtifactVersionModel.idempotency_key == key,
        )
    )
    if existing is not None:
        if snapshot_hash(existing.content) != snapshot_hash(content):
            raise ValueError(
                "idempotency_key already refers to different snapshot content"
            ) from None
        return existing

    version_no = int(
        (await session.scalar(
            select(func.coalesce(func.max(PlanningArtifactVersionModel.version_no), 0)).where(
                PlanningArtifactVersionModel.project_id == project_id,
                PlanningArtifactVersionModel.artifact_type == artifact_type,
                PlanningArtifactVersionModel.scope_ref_id.is_(None),
            )
        ))
        or 0
    ) + 1
    artifact = PlanningArtifactVersionModel(
        project_id=project_id,
        artifact_type=artifact_type,
        scope_ref_id=None,
        version_no=version_no,
        status=status,
        schema_version=schema_version,
        content=content,
        source_run_id=source_run_id,
        idempotency_key=key,
        notes=notes,
        created_by=created_by,
    )
    try:
        async with session.begin_nested():
            session.add(artifact)
            await session.flush()
    except IntegrityError:
        # The partial unique index is the concurrency arbiter. A savepoint
        # keeps the caller's outer transaction usable while we reuse the
        # winner of a concurrent retry.
        existing = await session.scalar(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.project_id == project_id,
                PlanningArtifactVersionModel.artifact_type == artifact_type,
                PlanningArtifactVersionModel.scope_ref_id.is_(None),
                PlanningArtifactVersionModel.idempotency_key == key,
            )
        )
        if existing is None:
            raise
        if snapshot_hash(existing.content) != snapshot_hash(content):
            raise ValueError("idempotency_key already refers to different snapshot content")
        return existing
    return artifact


async def transition_conception_snapshot(
    session: AsyncSession,
    snapshot_id: UUID,
    *,
    to_status: str,
    actor: str,
    reason: str,
    base_hash: str | None = None,
    result_hash: str | None = None,
) -> PlanningArtifactVersionModel:
    if to_status not in SNAPSHOT_STATUSES:
        raise ValueError(f"invalid conception snapshot status: {to_status!r}")
    artifact = await session.get(PlanningArtifactVersionModel, snapshot_id)
    if artifact is None or artifact.artifact_type not in SNAPSHOT_ARTIFACT_TYPES:
        raise ValueError("conception snapshot not found")
    allowed = _TRANSITIONS.get(artifact.status, frozenset())
    if to_status not in allowed:
        raise ValueError(f"invalid snapshot transition {artifact.status!r} -> {to_status!r}")
    audit = list((artifact.content or {}).get("_snapshot_audit", []))
    audit.append(
        {
            "actor": actor,
            "reason": reason,
            "from_state": artifact.status,
            "to_state": to_status,
            "at": datetime.now(UTC).isoformat(),
            "base_hash": base_hash,
            "result_hash": result_hash or snapshot_hash(artifact.content),
        }
    )
    artifact.content = {**(artifact.content or {}), "_snapshot_audit": audit}
    artifact.status = to_status
    await session.flush()
    return artifact


async def promote_conception_snapshot(
    session: AsyncSession,
    project_id: UUID,
    snapshot_id: UUID,
    *,
    expected_lock_version: int,
    actor: str,
    reason: str,
) -> ProjectModel:
    """Atomically promote one snapshot and supersede the prior canonical."""
    project = await session.scalar(
        select(ProjectModel).where(ProjectModel.id == project_id).with_for_update()
    )
    if project is None:
        raise ValueError("project not found")
    if project.lock_version != expected_lock_version:
        raise ValueError("project lock_version changed; refusing stale promotion")
    snapshot = await session.scalar(
        select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.id == snapshot_id,
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type.in_(SNAPSHOT_ARTIFACT_TYPES),
            PlanningArtifactVersionModel.scope_ref_id.is_(None),
        ).with_for_update()
    )
    if snapshot is None or snapshot.status not in {
        "validated",
        "candidate_v2",
        "reconciling",
        "pending_user_approval",
    }:
        raise ValueError("snapshot is not promotable")
    metadata = dict(project.metadata_json or {})
    old_id = metadata.get("creation_canonical_snapshot_id")
    if old_id and str(old_id) != str(snapshot_id):
        old = await session.get(
            PlanningArtifactVersionModel, UUID(str(old_id)), with_for_update=True
        )
        if (
            old is not None
            and old.artifact_type in SNAPSHOT_ARTIFACT_TYPES
            and old.status == "canonical"
        ):
            old_audit = list((old.content or {}).get("_snapshot_audit", []))
            old_audit.append(
                {
                    "actor": actor,
                    "reason": reason,
                    "from_state": "canonical",
                    "to_state": "superseded",
                    "at": datetime.now(UTC).isoformat(),
                    "base_hash": metadata.get("creation_canonical_snapshot_hash"),
                    "result_hash": snapshot_hash(old.content),
                }
            )
            old.content = {**(old.content or {}), "_snapshot_audit": old_audit}
            old.status = "superseded"
    previous_status = snapshot.status
    snapshot.status = "canonical"
    audit = list((snapshot.content or {}).get("_snapshot_audit", []))
    audit.append(
        {
            "actor": actor,
            "reason": reason,
            "from_state": previous_status,
            "to_state": "canonical",
            "at": datetime.now(UTC).isoformat(),
            "base_hash": metadata.get("creation_canonical_snapshot_hash"),
            "result_hash": snapshot_hash(snapshot.content),
        }
    )
    snapshot.content = {**(snapshot.content or {}), "_snapshot_audit": audit}
    metadata.update({
        "creation_canonical_snapshot_id": str(snapshot.id),
        "creation_canonical_snapshot_hash": snapshot_hash(snapshot.content),
    })
    project.metadata_json = metadata
    project.lock_version += 1
    await session.flush()
    return project
