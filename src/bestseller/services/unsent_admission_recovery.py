"""Prove and journal a pre-project UI admission misclassification.

No dispatcher, budget amendment, provider replay or financial settlement lives
here. The task projection may be repaired only from existing physical evidence.
"""
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, WorkflowRunModel,
)
from bestseller.services.budget import execution_snapshot


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def reconcile_confirmed_unsent_admission(session: AsyncSession, *, task_id: str,
        expected_attempt_id: str, workflow_id: str, previous_projection: dict,
        dry_run: bool = True) -> dict:
    """Append proof; caller owns task-file CAS and the surrounding transaction."""
    scope = f"conception:{task_id}"
    if previous_projection.get("terminal") != {"terminal": True, "code": "execution_ledger_unavailable"}:
        raise ValueError("unsent recovery requires the original ledger-unavailable projection")
    row = await session.scalar(select(ExecutionBudgetModel).where(
        ExecutionBudgetModel.scope_id == scope).with_for_update())
    if row is None or row.status not in {"active", "exhausted"}:
        raise ValueError("unsent recovery cannot supersede an unknown or cancelled scope")
    attempts = list((await session.scalars(select(ExecutionAttemptModel).where(
        ExecutionAttemptModel.scope_id == scope).order_by(
        ExecutionAttemptModel.created_at, ExecutionAttemptModel.id))).all())
    if (not attempts or attempts[-1].id != expected_attempt_id
        or any(a.status not in {"completed", "reconciled_charged", "not_sent", "rejected"} for a in attempts)):
        raise ValueError("unsent recovery requires the latest closed attempt inventory")
    attempt = attempts[-1]
    if attempt.status != "not_sent" or attempt.actual_tokens != 0:
        raise ValueError("latest attempt is not a definite zero-send result")
    operation = await session.get(ExecutionOperationModel, attempt.operation_key)
    if (operation is None or operation.scope_id != scope or operation.status != "stopped"
        or operation.input_hash != attempt.input_hash or operation.result_json is not None):
        raise ValueError("unsent operation binding is not closed")
    unresolved = await session.scalar(select(ExecutionOperationModel.key).where(
        ExecutionOperationModel.scope_id == scope,
        ExecutionOperationModel.status.in_(["pending", "unknown"])).limit(1))
    if unresolved:
        raise ValueError("another operation remains unresolved")
    failure_key = hashlib.sha256(f"transport-failure.v1\n{scope}\n{attempt.id}".encode()).hexdigest()
    failure = await session.get(ExecutionOperationModel, failure_key)
    diagnostic = failure.result_json if failure is not None else None
    if (failure is None or failure.scope_id != scope or failure.status != "transport_failure_receipt"
        or failure.input_hash != attempt.input_hash or not isinstance(diagnostic, dict)
        or diagnostic.get("schema") != "transport-failure.v1"
        or type(diagnostic.get("physical_sends")) is not int or diagnostic["physical_sends"] != 0
        or diagnostic.get("attempt_id") != attempt.id
        or diagnostic.get("operation_key") != operation.key
        or diagnostic.get("workflow_run_id") != workflow_id
        or diagnostic.get("exception_type") != "ExecutionBudgetError"
        or diagnostic.get("operator_interruption") is not False):
        raise ValueError("exact original zero-send diagnostic is required")
    workflow = await session.get(WorkflowRunModel, UUID(workflow_id))
    if (workflow is None or workflow.project_id is not None
        or workflow.workflow_type not in {"conception_initial", "conception_revision"}
        or workflow.status != "failed" or workflow.error_message != "execution_provider_not_sent"):
        raise ValueError("original pre-project workflow does not prove the unsent stop")
    key = hashlib.sha256(f"unsent-admission-recovery.v1\n{scope}\n{attempt.id}".encode()).hexdigest()
    existing = await session.get(ExecutionOperationModel, key)
    proof = {"schema": "unsent-admission-recovery.v1", "task_id": task_id, "scope_id": scope,
        "attempt_id": attempt.id, "operation_key": operation.key,
        "input_hash": attempt.input_hash, "workflow_id": workflow_id,
        "diagnostic_sha256": _sha(diagnostic), "previous_projection": previous_projection,
        "physical_sends": 0, "actual_tokens": 0, "credit_released": False,
        "automatic_resume": False, "provider_replay": False}
    if existing is not None:
        saved = existing.result_json or {}
        if (existing.scope_id != scope or existing.status != "unsent_admission_recovery"
            or existing.input_hash != attempt.input_hash
            or any(saved.get(k) != v for k, v in proof.items())):
            raise ValueError("unsent recovery receipt conflicts with original evidence")
        proof = saved
    elif not dry_run:
        proof = {**proof, "recorded_at": datetime.now(timezone.utc).isoformat(),
            "budget_at_recovery": execution_snapshot(row)}
        session.add(ExecutionOperationModel(key=key, scope_id=scope,
            input_hash=attempt.input_hash, status="unsent_admission_recovery",
            owner=operation.owner, lease_until=operation.lease_until, result_json=proof))
        await session.flush()
    current = execution_snapshot(row)
    renewal = None
    original = proof.get("budget_at_recovery") or {}
    if existing is not None and original.get("block_code") == "execution_deadline_exhausted" and current["execution_open"]:
        # A repaired expiry remains a stop until a trusted operator has
        # explicitly renewed time. A raw deadline edit is not authorization.
        matches = []
        for authorization_key, receipt in ((row.metadata_json or {}).get("operator_authorizations") or {}).items():
            request, before, after = (receipt.get(k) or {} for k in ("request", "before", "after"))
            if (receipt.get("policy") == "operator-phase-authorization.v1"
                and request.get("additional_calls") == 0 and request.get("additional_tokens") == 0
                and type(request.get("additional_seconds")) is int and request["additional_seconds"] > 0
                and request.get("actor") and request.get("authorization_ref")
                and before.get("deadline_at") == original.get("deadline_at")
                and after.get("deadline_at") == current["deadline_at"]
                and all(before.get(k) == after.get(k) == current[k]
                    for k in ("max_calls", "max_tokens", "calls_used", "tokens_charged"))):
                matches.append((authorization_key, receipt))
        if len(matches) != 1:
            raise ValueError("expiry requires one exact audited time-only authorization")
        authorization_key, authorization = matches[0]
        renewal = {"schema": "unsent-admission-time-renewal.v1", "task_id": task_id,
            "scope_id": scope, "attempt_id": attempt.id, "migration_receipt_key": key,
            "authorization_key": authorization_key, "authorization_sha256": _sha(authorization),
            "previous_block_code": "execution_deadline_exhausted",
            "deadline_at": current["deadline_at"], "automatic_resume": False, "credit_released": False}
        renewal_key = hashlib.sha256(f"unsent-admission-time-renewal.v1\n{scope}\n{attempt.id}\n{authorization_key}".encode()).hexdigest()
        stored = await session.get(ExecutionOperationModel, renewal_key)
        if stored is not None and (stored.status != "unsent_admission_time_renewal" or stored.result_json != renewal):
            raise ValueError("time renewal receipt conflicts with original authorization")
        if stored is None and not dry_run:
            session.add(ExecutionOperationModel(key=renewal_key, scope_id=scope,
                input_hash=attempt.input_hash, status="unsent_admission_time_renewal",
                owner=operation.owner, lease_until=operation.lease_until, result_json=renewal))
            await session.flush()
    return {"receipt": proof, "receipt_key": key, "dry_run": dry_run,
        "time_renewal_receipt": renewal, "current_budget": current, "automatic_resume": False}
