"""Read-only evidence for a manual, pre-project budget-stop resume.

This proves a new native M3.1 grant, or an exact native unpause of that grant,
against the stopped task. Unpause applies only to its own paused terminal.
It never clears a task/ledger fence, rearms an operation, grants credit or sends.
The normal Web owner must compare the whole task again before journaling and
removing only its execution_terminal. Existing ledger admission remains final.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import UUID

from sqlalchemy import and_, or_, select

from bestseller.infra.db.models import (
    ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel,
    ProjectModel, WorkflowRunModel,
)
from bestseller.services.budget import completion_authorization, current_execution_scope, execution_scope_block_code, execution_snapshot
from bestseller.services.conception_checkpoints import stable_conception_input_hash
from bestseller.services.creation_intent_contract import ConceptionAttemptInput, contract_from_payload
from bestseller.services.model_catalog import get_model_catalog_entry
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retry_ledger import execution_terminal_code

MODEL = "openai/MiniMax-M3.1-Flash"
ENDPOINT = "api.minimaxi.com"
PAUSED_CODE = "execution_authorization_paused"
ALLOWED_CODES = frozenset({"execution_budget_exhausted", "execution_deadline_exhausted", "execution_downstream_budget_reserved", PAUSED_CODE})
_ACTIVE = ("running", "pending", "queued", "stopping")


class ExecutionBudgetResumeRejected(ValueError):
    pass


def _need(value, reason):
    if not value:
        raise ExecutionBudgetResumeRejected("execution_budget_resume:" + reason)


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2048


def _date(value, *, ledger=False):
    if ledger and isinstance(value, datetime):
        parsed = value
    else:
        _need(isinstance(value, str), "timestamp_type")
        try:
            parsed = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            raise ExecutionBudgetResumeRejected("execution_budget_resume:timestamp_value") from None
    # Native authorize_provider_completion serializes SQLite's naive DateTime.
    if ledger and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    _need(parsed.tzinfo is not None and parsed.utcoffset() is not None, "timestamp_timezone")
    return parsed.astimezone(timezone.utc)


def _json(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError):
        raise ExecutionBudgetResumeRejected("execution_budget_resume:task_not_json") from None


def _row(row):
    def scalar(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, (UUID, Decimal)):
            return str(value)
        return copy.deepcopy(value)
    return {field.key: scalar(getattr(row, field.key)) for field in row.__mapper__.column_attrs}


def _pause_resume_pair(row, authorized_at, stopped_at, now):
    history = row.metadata_json.get("provider_pause_history")
    _need(type(history) is list and len(history) >= 2 and len(history) % 2 == 0, "pause_history_shape")
    previous_at = None
    times = []
    for index, entry in enumerate(history):
        _need(type(entry) is dict and set(entry) == {"actor", "reason", "status", "at"}, "pause_history_entry")
        _need(_text(entry["actor"]) and _text(entry["reason"]), "pause_operator_identity")
        _need(entry["status"] == ("paused" if index % 2 == 0 else "active"), "pause_status_order")
        at = _date(entry["at"])
        _need((previous_at is None or previous_at < at) and at <= now, "pause_timestamp_order")
        previous_at = at
        times.append(at)
    _need(authorized_at < times[-2] <= stopped_at < times[-1] <= now, "pause_pair_not_covering_stop")
    return {"paused": copy.deepcopy(history[-2]), "active": copy.deepcopy(history[-1]),
            "history_sha256": content_hash(history)}


def _authorization(row, stopped_at, terminal_code):
    _need(type(row.metadata_json) is dict, "budget_metadata")
    _need(row.status == "active" and execution_scope_block_code(row) is None, "scope_not_open")
    policy = completion_authorization(row)
    expected_keys = {"mode", "provider", "status", "models", "endpoint_hosts", "objective", "authorization_ref",
                     "aggregate_max_calls", "aggregate_max_tokens", "aggregate_deadline_at"}
    _need(type(policy) is dict and set(policy) == expected_keys, "completion_policy_shape")
    _need(policy["mode"] == "completion_bound" and policy["provider"] == "minimax" and policy["status"] == "active"
          and policy["models"] == [MODEL] and policy["endpoint_hosts"] == [ENDPOINT]
          and all(policy[key] is None for key in ("aggregate_max_calls", "aggregate_max_tokens", "aggregate_deadline_at"))
          and _text(policy["authorization_ref"]) and _text(policy["objective"]), "completion_route_or_limits")
    records = (row.metadata_json or {}).get("provider_authorizations")
    _need(type(records) is dict and all(_text(k) and type(v) is dict for k, v in records.items()), "authorization_history")
    matches = [(key, receipt) for key, receipt in records.items() if receipt.get("authorization") == policy]
    _need(len(matches) == 1, "unique_current_authorization")
    key, receipt = matches[0]
    _need(set(receipt) == {"policy", "request", "before", "authorization", "authorized_at"}
          and receipt["policy"] == "operator-provider-completion.v1", "operator_receipt_shape")
    request, before = receipt["request"], receipt["before"]
    _need(type(request) is dict and set(request) == {"actor", "authorization_ref", "objective", "expected_max_calls", "expected_max_tokens", "model"}, "operator_request_shape")
    _need(_text(request["actor"]) and request["authorization_ref"] == policy["authorization_ref"]
          and request["objective"] == policy["objective"] and request["model"] == MODEL, "operator_request_identity")
    _need(type(before) is dict and set(before) == {"max_calls", "max_tokens", "calls_used", "tokens_charged", "deadline_at", "status", "previous_provider_authorization"}, "operator_before_shape")
    _need(type(request["expected_max_calls"]) is int and type(request["expected_max_tokens"]) is int
          and (request["expected_max_calls"], request["expected_max_tokens"]) == (row.max_calls, row.max_tokens) == (64, 1_000_000), "original_caps_changed")
    _need(type(before["max_calls"]) is int and type(before["max_tokens"]) is int
          and (before["max_calls"], before["max_tokens"]) == (row.max_calls, row.max_tokens)
          and _date(before["deadline_at"], ledger=True) == _date(row.deadline_at, ledger=True)
          and isinstance(before["status"], str) and before["status"] in {"active", "exhausted"}, "original_limits_changed")
    _need(all(type(before[k]) is int and type(getattr(row, k)) is int and 0 <= before[k] <= getattr(row, k)
              for k in ("calls_used", "tokens_charged")), "original_usage_regressed")
    previous = before["previous_provider_authorization"]
    _need(previous is None or (type(previous) is dict and previous.get("status") == "completed"
          and previous.get("authorization_ref") != policy["authorization_ref"]), "previous_policy_not_completed")
    at = _date(receipt["authorized_at"])
    now = datetime.now(timezone.utc)
    pause_pair = None
    if terminal_code == PAUSED_CODE:
        pause_pair = _pause_resume_pair(row, at, stopped_at, now)
    else:
        _need(stopped_at < at <= now, "authorization_not_newer_than_stop")
    return key, copy.deepcopy(receipt), pause_pair


async def prepare_authorized_budget_resume(session, *, task_id, task_snapshot) -> dict:
    """Return a fresh proof; all task/DB mutations belong to the normal owner."""
    _need(not session.new and not session.dirty and not session.deleted, "clean_session_required")
    snapshot = _json(task_snapshot)
    _need(type(snapshot) is dict and snapshot.get("task_id") == task_id, "task_identity")
    try:
        identity = UUID(task_id)
    except (ValueError, TypeError, AttributeError):
        raise ExecutionBudgetResumeRejected("execution_budget_resume:task_uuid") from None
    _need(str(identity) == task_id and snapshot.get("task_type") == "autowrite"
          and snapshot.get("status") == "failed" and snapshot.get("cancel_requested") is False, "task_not_stopped_creation")
    result = snapshot.get("result")
    terminal = result.get("execution_terminal") if type(result) is dict else None
    _need(type(terminal) is dict and terminal.get("terminal") is True, "structured_terminal_required")
    code = terminal.get("code")
    _need(isinstance(code, str) and code in ALLOWED_CODES and execution_terminal_code(result) == code
          and snapshot.get("current_stage") == code and snapshot.get("error") == code, "terminal_not_exact_budget_stop")
    remaining = copy.deepcopy(result)
    remaining.pop("execution_terminal")
    _need(execution_terminal_code(remaining) is None, "another_execution_terminal")
    stopped_at = _date(snapshot.get("updated_at"))
    payload = snapshot.get("payload")
    _need(type(payload) is dict and payload.get("_run_conception") is True, "creation_payload_required")
    scope = "conception:" + task_id
    _need(current_execution_scope() in (None, scope)
          and payload.get("_active_continuation_scope") in (None, "", scope), "foreign_scope")
    slug = snapshot.get("project_slug")
    _need(_text(slug) and payload.get("slug") == slug, "task_slug")
    try:
        source_id = UUID(payload.get("_conception_workflow_run_id"))
        _need(str(source_id) == payload["_conception_workflow_run_id"], "source_uuid")
        contract = contract_from_payload(payload)
        route_key = payload.get("llm_model_id")
        _need(_text(route_key) and route_key == contract.llm_model_id, "creation_model_route")
        route = get_model_catalog_entry(route_key)
        _need(route is not None and route.model == MODEL, "creation_model_catalog")
        attempt = ConceptionAttemptInput(
            contract=contract, attempt_id="budget-resume-proof", idempotency_key="budget-resume-proof",
            input_payload={"premise": payload.get("premise"), "user_hints": payload.get("user_hints") or {},
                           "genre": payload.get("genre"), "sub_genre": payload.get("sub_genre"),
                           "target_chapters": payload.get("target_chapters")},
        )
        stable = stable_conception_input_hash(attempt)
    except ExecutionBudgetResumeRejected:
        raise
    except (ValueError, TypeError, AttributeError):
        raise ExecutionBudgetResumeRejected("execution_budget_resume:creation_source_input") from None
    with session.no_autoflush:
        _need(await session.scalar(select(ProjectModel.id).where(ProjectModel.slug == slug).limit(1)) is None, "project_already_exists")
        source = await session.scalar(select(WorkflowRunModel).where(WorkflowRunModel.id == source_id).execution_options(populate_existing=True))
        _need(source is not None and source.project_id is None and source.workflow_type == "conception_initial"
              and source.status == "failed", "source_workflow")
        metadata = source.metadata_json
        _need(type(metadata) is dict and metadata.get("execution_scope_id") == scope
              and metadata.get("creation_intent_hash") == contract.contract_hash()
              and metadata.get("conception_checkpoint_input_hash") == stable, "source_binding")
        row = await session.scalar(select(ExecutionBudgetModel).where(ExecutionBudgetModel.scope_id == scope).execution_options(populate_existing=True))
        _need(row is not None and row.scope_id == scope, "budget_missing")
        key, authorization, pause_pair = _authorization(row, stopped_at, code)
        active = await session.scalar(select(WorkflowRunModel.id).where(
            WorkflowRunModel.status.in_(_ACTIVE),
            or_(WorkflowRunModel.metadata_json["execution_scope_id"].as_string() == scope,
                and_(WorkflowRunModel.scope_type == "creation", WorkflowRunModel.scope_id == identity)),
        ).limit(1))
        _need(active is None, "active_scope_workflow")
        operations = list(await session.scalars(select(ExecutionOperationModel).where(ExecutionOperationModel.scope_id == scope).execution_options(populate_existing=True)))
        attempts = list(await session.scalars(select(ExecutionAttemptModel).where(ExecutionAttemptModel.scope_id == scope).execution_options(populate_existing=True)))
        _need(not any(op.status in {"pending", "unknown"} for op in operations), "unsettled_operation")
        _need(not any(a.status in {"reserved", "unknown"} for a in attempts), "unsettled_attempt")
        operation_rows = sorted((_row(op) for op in operations), key=lambda value: value["key"])
        attempt_rows = sorted((_row(a) for a in attempts), key=lambda value: value["id"])
        used_keys = {a.operation_key for a in attempts}
        stopped = [{"row": _row(op), "row_sha256": content_hash(_row(op))} for op in operations
                   if op.status == "stopped" and op.key not in used_keys]
        terminal_snapshot = {field: copy.deepcopy(snapshot.get(field)) for field in
                             ("status", "result", "error", "current_stage", "updated_at", "cancel_requested")}
        return _json({
            "schema": "execution-budget-resume.v1", "status": "authorized_budget_resume_proven",
            "task_id": task_id, "task_snapshot_sha256": content_hash(snapshot),
            "terminal_code": code, "previous_terminal_snapshot": terminal_snapshot,
            "previous_terminal_snapshot_sha256": content_hash(terminal_snapshot),
            "scope_id": scope, "source_workflow_run_id": str(source_id),
            "source_workflow_sha256": content_hash(_row(source)),
            "creation_intent_hash": contract.contract_hash(), "stable_input_hash": stable,
            "authorization_key": key, "authorization_ref": authorization["request"]["authorization_ref"],
            "actual_authorization_receipt": authorization,
            "actual_authorization_receipt_sha256": content_hash(authorization),
            "native_pause_resume_pair": pause_pair,
            "fresh_scope": execution_snapshot(row), "budget_row_sha256": content_hash(_row(row)),
            "operations_sha256": content_hash(operation_rows), "operation_count": len(operation_rows),
            "attempts_sha256": content_hash(attempt_rows), "attempt_count": len(attempt_rows),
            "no_attempt_stopped_operations": stopped,
            "provider_calls": 0, "new_credit_allowed": False, "automatic_resume": False,
        })
