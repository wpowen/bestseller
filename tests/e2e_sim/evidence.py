"""Read-only exact joins for synthetic sends; never creates native receipts.

API: await read_native_correlations(session, project_id, events), or the pure
correlate_native_rows(project_id, events, operations, attempts, llm_runs).
Both return correlations, correlated_send_count, errors and ok. Each error has
an event_index and a fixed reason; callers must propagate errors as diagnostics.
An empty list is no positive evidence. Records describe fixture/synthetic
correlation only, never billing, authorization, acceptance or production proof.

The native physical input is ExecutionAttemptModel.input_hash and receipt
attempt.effective_input_hash. ExecutionOperationModel.input_hash is the initial
claim hash and may differ after retry/fallback. LlmRunModel.prompt_hash and
paid_call_origin.prompt_hash must identify the actual send. The native metadata
key is execution_receipt (policy execution-budget.v1), not execution_ledger.
Timeouts without an LlmRun remain explicitly unmatched; no receipt is invented.
"""

from __future__ import annotations

import re
from uuid import UUID

_MARKER = "v3-synthetic-http.v1"


def _uuid(value):
    if isinstance(value, UUID):
        return str(value)
    if type(value) is str:
        try:
            if str(UUID(value)) == value:
                return value
        except ValueError:
            pass
    return None


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _attempt_id(value):
    # Native reserve() uses uuid4().hex, not a UUID with hyphens or tape ordinal.
    return type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _wire_error(project, event):
    if event.get("schema") != _MARKER or event.get("simulation_only") is not True:
        return "invalid_synthetic_send"
    count = event.get("observed_native_send_count")
    if type(count) is not int or count != 1:
        return "native_send_count_not_one"
    if _uuid(event.get("project_id")) != project:
        return "wire_project_mismatch"
    if _uuid(event.get("workflow_run_id")) is None:
        return "wire_workflow_missing_or_invalid"
    if not _hash(event.get("native_operation_key")):
        return "wire_operation_missing_or_invalid"
    if not _hash(event.get("native_input_hash")):
        return "wire_input_hash_missing_or_invalid"
    if not _attempt_id(event.get("native_attempt_id")):
        return "wire_attempt_missing_or_invalid"
    return None


def _llm_matches(run, event, operation, attempt):
    if (
        _uuid(getattr(run, "id", None)) is None
        or _uuid(getattr(run, "project_id", None)) != event["project_id"]
        or _uuid(getattr(run, "workflow_run_id", None)) != event["workflow_run_id"]
        or getattr(run, "prompt_hash", None) != event["native_input_hash"]
        or getattr(run, "model_name", None) != attempt.model_name
    ):
        return False
    metadata = getattr(run, "metadata_json", None)
    if type(metadata) is not dict:
        return False
    origin = metadata.get("paid_call_origin")
    receipt = metadata.get("execution_receipt")
    if type(origin) is not dict or type(receipt) is not dict:
        return False
    if (
        origin.get("schema") != "paid-call-origin.v1"
        or origin.get("status") != "recorded"
        or origin.get("project_id") != event["project_id"]
        or origin.get("workflow_run_id") != event["workflow_run_id"]
        or origin.get("prompt_hash") != event["native_input_hash"]
        or receipt.get("policy") != "execution-budget.v1"
        or receipt.get("scope_id") != operation.scope_id
        or receipt.get("operation_key") != operation.key
    ):
        return False
    # Optional redundant metadata may never contradict the core exact join.
    if "effective_input_receipt" in metadata:
        effective = metadata["effective_input_receipt"]
        if (
            type(effective) is not dict
            or effective.get("schema") != "effective-input.v1"
            or effective.get("hash") != event["native_input_hash"]
        ):
            return False
    attempts = receipt.get("attempts")
    if type(attempts) is not list:
        return False
    matches = [
        item for item in attempts if type(item) is dict and item.get("attempt_id") == attempt.id
    ]
    return (
        len(matches) == 1
        and matches[0].get("effective_input_hash") == attempt.input_hash
        and matches[0].get("model") == attempt.model_name
        and matches[0].get("status") == attempt.status
    )


def correlate_native_rows(project_id, events, operations, attempts, llm_runs):
    """Join actual row-shaped objects; emit safe errors for every unmatched send.

    SimpleNamespace fixtures suffice for offline tests. Inputs are never
    mutated. Row multiplicity is rejected, and repeated attempt observations
    cannot inflate the correlated count. Operation state grants no authority.
    """
    project = _uuid(project_id)
    if project is None:
        raise ValueError("invalid_project_id")
    operations, attempts, llm_runs = list(operations), list(attempts), list(llm_runs)
    correlations, errors, seen = [], [], set()
    for index, event in enumerate(events):
        if type(event) is not dict:
            errors.append({"event_index": index, "reason": "invalid_audit_event"})
            continue
        if event.get("event") != "synthetic_http_send":
            continue
        reason = _wire_error(project, event)
        if reason:
            errors.append({"event_index": index, "reason": reason})
            continue
        attempt_id = event["native_attempt_id"]
        if attempt_id in seen:
            errors.append({"event_index": index, "reason": "duplicate_native_attempt"})
            continue
        seen.add(attempt_id)
        ops = [op for op in operations if getattr(op, "key", None) == event["native_operation_key"]]
        tries = [row for row in attempts if getattr(row, "id", None) == attempt_id]
        if len(ops) != 1 or len(tries) != 1:
            errors.append({"event_index": index, "reason": "native_rows_not_unique"})
            continue
        operation, attempt = ops[0], tries[0]
        if (
            not _hash(getattr(operation, "input_hash", None))
            or type(getattr(operation, "scope_id", None)) is not str
            or not operation.scope_id
            or getattr(attempt, "scope_id", None) != operation.scope_id
            or getattr(attempt, "operation_key", None) != operation.key
            or getattr(attempt, "input_hash", None) != event["native_input_hash"]
            or getattr(attempt, "status", None) != "completed"
            or type(getattr(attempt, "model_name", None)) is not str
            or not attempt.model_name
        ):
            errors.append({"event_index": index, "reason": "native_attempt_mismatch"})
            continue
        runs = [run for run in llm_runs if _llm_matches(run, event, operation, attempt)]
        if len(runs) != 1:
            errors.append({"event_index": index, "reason": "llm_correlation_not_unique"})
            continue
        correlations.append(
            {
                "event_index": index,
                "project_id": project,
                "workflow_run_id": event["workflow_run_id"],
                "operation_key": operation.key,
                "operation_input_hash": operation.input_hash,
                "input_hash": attempt.input_hash,
                "attempt_id": attempt.id,
                "attempt_status": attempt.status,
                "llm_run_id": str(runs[0].id),
            }
        )
    return {
        "schema": "synthetic-native-correlation.v1",
        "simulation_only": True,
        "correlations": correlations,
        "correlated_send_count": len(correlations),
        "errors": errors,
        "ok": not errors,
    }


async def read_native_correlations(session, project_id, events):
    """SELECT only exact operation/attempt IDs and project/workflow LlmRun rows.

    Uses the supplied AsyncSession under no_autoflush; no engine creation,
    writes, commit, ledger APIs, receipt repair or project aggregate counters.
    Database/import failures propagate; callers must not report them as proof.
    """
    events = list(events)
    # Validate before imports or any database read. Invalid events still reach
    # the pure helper so their original audit positions produce diagnostics.
    validation = correlate_native_rows(project_id, events, [], [], [])
    project = _uuid(project_id)
    valid = [
        event
        for event in events
        if type(event) is dict
        and event.get("event") == "synthetic_http_send"
        and _wire_error(project, event) is None
    ]
    if not valid:
        return validation
    from sqlalchemy import select

    from bestseller.infra.db.models import (
        ExecutionAttemptModel,
        ExecutionOperationModel,
        LlmRunModel,
    )

    with session.no_autoflush:
        operations = (
            await session.scalars(
                select(ExecutionOperationModel).where(
                    ExecutionOperationModel.key.in_(
                        {event["native_operation_key"] for event in valid}
                    )
                )
            )
        ).all()
        attempts = (
            await session.scalars(
                select(ExecutionAttemptModel).where(
                    ExecutionAttemptModel.id.in_({event["native_attempt_id"] for event in valid})
                )
            )
        ).all()
        runs = (
            await session.scalars(
                select(LlmRunModel).where(
                    LlmRunModel.project_id == UUID(project),
                    LlmRunModel.workflow_run_id.in_(
                        {UUID(event["workflow_run_id"]) for event in valid}
                    ),
                    LlmRunModel.prompt_hash.in_({event["native_input_hash"] for event in valid}),
                )
            )
        ).all()
    return correlate_native_rows(project, events, operations, attempts, runs)
