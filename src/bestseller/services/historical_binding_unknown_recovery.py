"""Explicit migration of a proven historical handoff misclassification.

Only the manual reconciliation adapter uses this proof. Financial receipts,
attempts, workflow evidence and the immutable phase binding remain untouched.
"""

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ExecutionAttemptModel as Attempt,
)
from bestseller.infra.db.models import (
    ExecutionBudgetModel as Budget,
)
from bestseller.infra.db.models import (
    ExecutionOperationModel as Operation,
)
from bestseller.infra.db.models import (
    ProjectModel,
)
from bestseller.infra.db.models import (
    WorkflowRunModel as Workflow,
)
from bestseller.services.budget import ExecutionReconciliation
from bestseller.services.execution_phase_handoff import validate_binding
from bestseller.services.retry_ledger import assert_execution_scope_open, execution_terminal_code

SCHEMA = "historical-binding-unknown-recovery.v1"
HISTORY = "historical_binding_unknown_recovery_history"
LEGACY_CODE = "execution_continuation_binding_invalid"
UNKNOWN_CODE = "execution_result_unknown"
_ACTIVE = ("running", "queued", "pending", "stopping")
_PROJECTION_FIELDS = (
    "execution_terminal",
    "production_paused",
    "production_pause_reason",
    "requires_machine_repair",
    "requires_human_review",
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _key(kind: str, scope: str, identifier: str) -> str:
    return hashlib.sha256(f"{kind}\n{scope}\n{identifier}".encode()).hexdigest()


def task_snapshot(task: object) -> dict:
    """Retain the complete result and stop controls, without copying event rings."""
    return deepcopy(
        {
            name: getattr(task, name, None)
            for name in (
                "task_id",
                "project_slug",
                "status",
                "current_stage",
                "error",
                "updated_at",
                "result",
                "cancel_requested",
                "payload",
            )
        }
    )


async def derive_reclassification(
    session: AsyncSession,
    *,
    project: ProjectModel,
    scope_id: str,
    task_id: str,
    task: dict,
    receipt: dict,
    evidence: ExecutionReconciliation,
) -> dict | None:
    """Return evidence only; unrelated or unproven stops are never projected."""
    metadata = project.metadata_json or {}
    terminal = metadata.get("execution_terminal") or {}
    result = task.get("result") or {}
    if (
        terminal.get("code") != LEGACY_CODE
        or terminal.get("terminal") is not True
        or execution_terminal_code(metadata) != LEGACY_CODE
        or execution_terminal_code(result) != LEGACY_CODE
        or task.get("task_id") != task_id
        or (result.get("execution_terminal") or {}).get("code") != LEGACY_CODE
        or (result.get("execution_terminal") or {}).get("terminal") is not True
        or result.get("code") not in (None, LEGACY_CODE)
        or task.get("status") != "failed"
        or task.get("current_stage") != LEGACY_CODE
        or task.get("error") != LEGACY_CODE
        or task.get("cancel_requested") is True
        or task.get("project_slug") != project.slug
        or project.status != "paused"
        or metadata.get("production_paused") is not True
        or metadata.get("production_pause_reason") != LEGACY_CODE
        or metadata.get("execution_scope_id") != scope_id
    ):
        return None
    # An independent stop must survive even when a legacy code shadows it.
    other = {
        k: v
        for k, v in metadata.items()
        if k not in {"execution_terminal", "production_pause_reason"}
    }
    other_result = {
        k: v for k, v in result.items() if k not in {"execution_terminal", "code", "terminal"}
    }
    if execution_terminal_code(other) or execution_terminal_code(other_result):
        return None
    payload = task.get("payload") or {}
    if payload.get("slug") != project.slug or payload.get("_active_continuation_scope") != scope_id:
        return None
    # Validate against the original projection. Never create a new handoff or
    # reinterpret changed author intent as the historical unknown outcome.
    if (
        await validate_binding(session, task_id=task_id, payload=payload, audit_only=True)
        != scope_id
    ):
        return None
    if (
        receipt.get("scope_id") != scope_id
        or receipt.get("decision") != "quarantine_charged"
        or receipt.get("status") != "active"
        or type(receipt.get("remaining_unknown")) is not int
        or receipt.get("remaining_unknown") != 0
        or receipt.get("automatic_resume") is not False
        or receipt.get("credit_released") is not False
        or receipt.get("operation_replay_allowed") is not False
        or receipt.get("remote_outcome") != "unknown_quarantined"
    ):
        return None
    request = evidence.model_dump(mode="json")
    aid = receipt.get("attempt_id")
    identifier = receipt.get("reconciliation_id")
    if (
        not isinstance(aid, str)
        or not isinstance(identifier, str)
        or request["attempt_id"] != aid
        or request["reconciliation_id"] != identifier
        or request["decision"] != "quarantine_charged"
        or request["result"] is not None
        or request["evidence_kind"] != "dispatcher_exit"
        or request["local_dispatcher_stopped"] is not True
        or request["remote_operation_closed"] is not False
    ):
        return None
    saved = await session.get(Operation, _key("reconciliation.v1", scope_id, identifier))
    record = saved.result_json or {} if saved else {}
    fingerprint = _digest(request)
    if (
        saved is None
        or saved.scope_id != scope_id
        or saved.status != "reconciliation_receipt"
        or saved.input_hash != fingerprint
        or record.get("request_hash") != fingerprint
        or record.get("reconciliation_id") != identifier
        or record.get("receipt") != receipt
        or record.get("local_dispatcher_stopped") is not True
        or record.get("remote_operation_closed") is not False
        or any(
            record.get(k) != request[k]
            for k in (
                "actor",
                "evidence_kind",
                "evidence_sha256",
                "remote_operation_closed",
                "local_dispatcher_stopped",
            )
        )
    ):
        return None
    budget = await session.get(Budget, scope_id, with_for_update=True, populate_existing=True)
    if (
        budget is None
        or budget.status != "active"
        or budget.calls_used != receipt.get("calls_used")
        or budget.tokens_charged != receipt.get("tokens_charged")
    ):
        return None
    await assert_execution_scope_open(session, scope_id)
    # Check actual pending identities as well as the current budget projection;
    # a replayed old receipt cannot clear a newly unknown operation.
    unresolved = await session.scalar(
        select(func.count())
        .select_from(Operation)
        .outerjoin(Attempt, Attempt.operation_key == Operation.key)
        .where(
            Operation.scope_id == scope_id,
            or_(Operation.status == "unknown", Attempt.status.in_(("reserved", "unknown"))),
        )
    )
    if unresolved or await session.scalar(
        select(Attempt.id)
        .where(Attempt.scope_id == scope_id, Attempt.status.in_(("reserved", "unknown")))
        .limit(1)
    ):
        return None
    attempt = await session.get(Attempt, aid)
    root = await session.get(Operation, attempt.operation_key) if attempt else None
    failure = await session.get(Operation, _key("transport-failure.v1", scope_id, aid))
    diagnostic = failure.result_json or {} if failure else {}
    if (
        attempt is None
        or root is None
        or failure is None
        or any(row.scope_id != scope_id for row in (attempt, root, failure))
        or attempt.status != "reconciled_charged"
        or root.status != "stopped"
        or root.result_json is not None
        or attempt.actual_tokens is not None
        or attempt.reserved_tokens <= 0
        or attempt.input_hash != request["expected_input_hash"]
        or root.input_hash != attempt.input_hash
        or failure.input_hash != attempt.input_hash
        or failure.owner != root.owner
        or failure.status != "transport_failure_receipt"
        or diagnostic.get("schema") != "transport-failure.v1"
        or diagnostic.get("attempt_id") != aid
        or diagnostic.get("operation_key") != root.key
        or diagnostic.get("project_id") != str(project.id)
        or type(diagnostic.get("physical_sends")) is not int
        or diagnostic["physical_sends"] != 1
        or diagnostic.get("exception_type") != "CancelledError"
        or diagnostic.get("operator_interruption") is not True
        or diagnostic.get("partial_stream_observed") is not False
        or diagnostic.get("stream") is not False
        or not isinstance(diagnostic.get("error_chain"), list)
        or not any(
            isinstance(item, dict) and item.get("type") == "CancelledError"
            for item in diagnostic.get("error_chain", [])
        )
    ):
        return None
    try:
        workflow_id = UUID(diagnostic["workflow_run_id"])
    except (KeyError, TypeError, ValueError):
        return None
    workflow = await session.get(Workflow, workflow_id)
    if (
        workflow is None
        or workflow.project_id != project.id
        or workflow.status != "failed"
        or (workflow.metadata_json or {}).get("execution_scope_id") != scope_id
    ):
        return None
    if await session.scalar(
        select(Workflow.id)
        .where(
            or_(
                Workflow.project_id == project.id,
                Workflow.metadata_json["execution_scope_id"].as_string() == scope_id,
            ),
            Workflow.status.in_(_ACTIVE),
        )
        .limit(1)
    ):
        return None
    if await session.scalar(
        select(Workflow.id)
        .where(
            Workflow.project_id == project.id,
            Workflow.id != workflow.id,
            Workflow.created_at >= workflow.created_at,
        )
        .limit(1)
    ):
        return None
    for kind in ("transport-result.v1", "provider-return.v1"):
        if await session.get(Operation, _key(kind, scope_id, aid)) is not None:
            return None
    history = metadata.get(HISTORY, [])
    task_history = result.get(HISTORY, [])
    if (
        not isinstance(history, list)
        or not isinstance(task_history, list)
        or len(history) >= 16
        or len(task_history) >= 16
    ):
        return None
    proof = {
        "schema": SCHEMA,
        "scope_id": scope_id,
        "project_id": str(project.id),
        "task_id": task_id,
        "from_code": LEGACY_CODE,
        "to_code": UNKNOWN_CODE,
        "phase_handoff_key": payload["_execution_phase_handoff"],
        "payload_sha256": _digest(payload),
        "receipt": deepcopy(receipt),
        "reconciliation_key": saved.key,
        "reconciliation_sha256": _digest(record),
        "dispatcher_exit_sha256": record["evidence_sha256"],
        "failure_key": failure.key,
        "failure_sha256": _digest(diagnostic),
        "attempt_id": aid,
        "operation_key": root.key,
        "input_hash": attempt.input_hash,
        "workflow_id": str(workflow.id),
        "previous_task": deepcopy(task),
        "previous_project": {
            "status": project.status,
            **{k: deepcopy(metadata[k]) for k in _PROJECTION_FIELDS if k in metadata},
        },
        "at": datetime.now(UTC).isoformat(),
        "automatic_resume": False,
        "credit_released": False,
        "operation_replay_allowed": False,
        "remote_outcome": "unknown_quarantined",
    }
    return {**proof, "proof_sha256": _digest(proof)}


def project_unknown(project: ProjectModel, proof: dict) -> None:
    """Append original controls before the existing worker clears the projection."""
    metadata = deepcopy(project.metadata_json or {})
    metadata[HISTORY] = [*metadata.get(HISTORY, []), deepcopy(proof)]
    metadata["execution_terminal"] = {
        **metadata["execution_terminal"],
        "code": UNKNOWN_CODE,
        "historical_reclassification_sha256": proof["proof_sha256"],
    }
    metadata["production_pause_reason"] = UNKNOWN_CODE
    project.metadata_json = metadata
