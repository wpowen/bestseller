"""Explicit migration of checkpoint shells bound to observed failed queue jobs."""
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services.prompt_source_policy import content_hash


async def reconcile_observed_failed_workflow(
    session: AsyncSession, *, project_id: UUID, workflow_id: UUID,
    evidence: Mapping[str, Any], actor: str,
) -> dict[str, Any]:
    """Never infer failure from age or rewrite a completed/foreign attempt.

    The operator supplies the retained queue result and progress correlation,
    after verifying no owner or provider operation is still active. This does
    not grant retry credit or mark any manuscript accepted.
    """
    if (evidence.get("schema") != "observed-failed-worker.v1"
        or evidence.get("job_success") is not False
        or evidence.get("owner_absent") is not True
        or evidence.get("pending_provider_operations") != 0
        or str(evidence.get("workflow_id")) != str(workflow_id)
        or str(evidence.get("project_id")) != str(project_id)
        or not evidence.get("job_id") or not evidence.get("failure_receipt_sha256")
        or not evidence.get("progress_sha256") or not actor):
        raise ValueError("verified_failed_workflow_evidence_required")
    row = await session.scalar(select(WorkflowRunModel).where(
        WorkflowRunModel.id == workflow_id).with_for_update())
    if row is None or row.project_id != project_id:
        raise ValueError("failed_workflow_identity_mismatch")
    key = content_hash(dict(evidence))
    metadata = dict(row.metadata_json or {})
    receipts = list(metadata.get("failure_reconciliation_receipts") or [])
    existing = next((r for r in receipts if r.get("evidence_hash") == key), None)
    if existing:
        return {"status": "already_reconciled", "receipt": existing}
    if row.status not in {"pending", "queued", "running"}:
        raise ValueError("workflow_no_longer_active")
    receipt = {"schema": "workflow-failure-reconciliation.v1", "evidence_hash": key,
        "evidence": dict(evidence), "actor": actor, "at": datetime.now(UTC).isoformat(),
        "previous_status": row.status, "previous_step": row.current_step,
        "previous_error": row.error_message, "code": "workflow_failed", "recoverable": True}
    receipts.append(receipt)
    row.metadata_json = {**metadata, "failure_reconciliation_receipts": receipts}
    row.status = "failed"
    row.error_message = "workflow_failed: observed worker failure reconciled"
    await session.flush()
    return {"status": "reconciled", "receipt": receipt}
