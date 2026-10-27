"""Read-only proof of an original, unusable native planning return.

This admits a spent protocol slot, never a grade, credit or SEND permission.
The old v3 request has a 6000 output cap and no output-budget metadata.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db import models as db
from bestseller.services import llm
from bestseller.services import planning_draft_admission as admission
from bestseller.services.budget import ExecutionReconciliation
from bestseller.services.model_context_capacity import bind_selected_model_capacity
from bestseller.services.planning_admission_submission import planning_admission_tool
from bestseller.services.planning_review_transport import prepare_planning_review_transport
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader
from bestseller.services.retained_review_input_proof import _fresh_catalog, _fresh_role
from bestseller.services.retry_ledger import project_execution_scope
from bestseller.settings import AppSettings, LLMRoleSettings

SCHEMA = "known-terminal-planning-attempt.v1"
REQUEST_SCHEMA = "planning-draft-admission.v3"
OUTPUT_CAP = 6000


class KnownTerminalPlanningAttemptError(ValueError):
    pass


def _need(condition: object, reason: str) -> None:
    if not condition:
        raise KnownTerminalPlanningAttemptError("known_terminal_planning_attempt:" + reason)


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _hash(value: object) -> str:
    return sha256(_json(value).encode()).hexdigest()


def _row(row: SimpleNamespace) -> dict:
    return json.loads(_json(vars(row)))


def _key(domain: str, scope: str, identifier: str) -> str:
    return sha256(f"{domain}\n{scope}\n{identifier}".encode()).hexdigest()


def _same(left: object, right: object) -> bool:
    # JSON booleans are not integer witnesses.
    return _json(left) == _json(right)


def _request(
    settings: AppSettings,
    project: db.ProjectModel | SimpleNamespace,
    workflow_id: UUID,
    supplied: dict,
) -> tuple[llm.LLMCompletionRequest, LLMRoleSettings, str]:
    _need(
        supplied.get("schema") == REQUEST_SCHEMA
        and supplied.get("project_id") == str(project.id)
        and supplied.get("workflow_run_id") == str(workflow_id),
        "input_owner",
    )
    candidate = supplied.get("candidate_payload")
    _need(
        isinstance(candidate, dict) and "chapters" in candidate and "volumes" not in candidate,
        "chapter_request_required",
    )
    digest = content_hash(supplied)
    tools = [
        planning_admission_tool(
            admission._Grade.model_json_schema(),
            supplied["projection_audit_targets"],
            projection_kind="chapters",
        )
    ]
    user = admission._admission_prompt(supplied, digest, [])
    with bind_selected_model_capacity(project.metadata_json or {}):
        user, transport = prepare_planning_review_transport(
            admission.SYSTEM,
            user,
            data=json.loads(user),
            data_block=user,
            tools=tools,
            output_tokens=OUTPUT_CAP,
        )
        capacity = admission._input_capacity_receipt(
            user, tools, system=admission.SYSTEM, output_tokens=OUTPUT_CAP
        )
    _need(capacity["budget_passed"], "original_request_capacity")
    request = llm.LLMCompletionRequest(
        logical_role="critic",
        project_id=project.id,
        workflow_run_id=workflow_id,
        operation_id=admission._protocol_operation(project, workflow_id, digest, [], 0),
        system_prompt=admission.SYSTEM,
        user_prompt=user,
        prompt_template=admission.TEMPLATE,
        prompt_version=REQUEST_SCHEMA,
        max_tokens_override=OUTPUT_CAP,
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "submit_planning_admission"}},
        metadata={"planning_admission_input_receipt": capacity, "source_transport": transport},
        fallback_response="{}",
    )
    catalog, _ = _fresh_catalog()
    role = _fresh_role(settings, project, request, catalog)
    return request, role, digest


def _completion(record: dict, model: str) -> dict:
    raw = record.get("response")
    _need(isinstance(raw, dict), "provider_response_missing")
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False)
    _need(
        sha256(encoded.encode()).hexdigest() == record.get("response_sha256"),
        "provider_response_hash",
    )
    _need(
        raw.get("object") == "chat.completion"
        and bool(raw.get("id"))
        and raw.get("model") == model.removeprefix("openai/"),
        "provider_response_identity",
    )
    choices = raw.get("choices")
    _need(isinstance(choices, list) and len(choices) == 1, "single_choice_required")
    choice = choices[0]
    _need(
        type(choice.get("index")) is int
        and choice["index"] == 0
        and choice.get("finish_reason") == "length",
        "terminal_length_required",
    )
    message = choice.get("message")
    _need(
        isinstance(message, dict)
        and message.get("role") == "assistant"
        and message.get("content") == ""
        and message.get("tool_calls") in (None, [])
        and "function_call" in message
        and message["function_call"] is None
        and isinstance(message.get("reasoning_content"), str)
        and bool(message["reasoning_content"].strip()),
        "reasoning_only_native_return_required",
    )
    usage = raw.get("usage")
    _need(isinstance(usage, dict), "usage_required")
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    counts = [usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")]
    _need(
        all(type(n) is int and n >= 0 for n in [*counts, reasoning])
        and counts[1] == reasoning == OUTPUT_CAP
        and counts[2] == counts[0] + counts[1],
        "terminal_usage_invalid",
    )
    return {
        "content": "",
        "llm_run_id": None,
        "finish_reason": "length",
        "model_name": model,
        "provider": llm._provider_from_model(model),
        "input_tokens": counts[0],
        "output_tokens": counts[1],
        "tool_calls": deepcopy(message.get("tool_calls")),
        "raw_message": deepcopy(message),
        "metadata": {"paid_call_origin": None},
    }


async def _inspect(
    session: AsyncSession,
    settings: AppSettings,
    project: db.ProjectModel | SimpleNamespace,
    workflow_id: UUID,
    supplied: dict,
    attempt_id: str,
    *,
    reconciled: bool,
) -> dict:
    reader = _Reader(session)
    fresh = await reader.get(db.ProjectModel, project.id)
    workflow = await reader.get(db.WorkflowRunModel, workflow_id)
    _need(
        fresh is not None and workflow is not None and workflow.project_id == fresh.id,
        "workflow_owner",
    )
    request, role, digest = _request(settings, fresh, workflow_id, supplied)
    scope = project_execution_scope(fresh)
    root_key = sha256(f"{scope}\n{request.operation_id}".encode()).hexdigest()
    attempt = await reader.get(db.ExecutionAttemptModel, attempt_id)
    root = await reader.get(db.ExecutionOperationModel, root_key)
    budget = await reader.get(db.ExecutionBudgetModel, scope)
    _need(attempt is not None and root is not None and budget is not None, "ledger_missing")
    effective = llm._effective_input_hash(request, role)
    _need(
        attempt.operation_key == root_key
        and attempt.scope_id == root.scope_id == scope
        and attempt.input_hash == root.input_hash == effective
        and attempt.model_name == role.model,
        "request_identity",
    )
    attempts = await reader.rows(
        db.ExecutionAttemptModel, db.ExecutionAttemptModel.operation_key == root_key
    )
    _need(len(attempts) == 1 and attempts[0].id == attempt_id, "single_attempt_required")
    _need(
        attempt.status == ("reconciled_charged" if reconciled else "unknown")
        and root.status == ("stopped" if reconciled else "unknown")
        and root.result_json is None
        and attempt.actual_tokens is None
        and type(attempt.reserved_tokens) is int
        and attempt.reserved_tokens > 0,
        "original_full_hold_required",
    )
    _need(reconciled or budget.status == "unknown", "original_scope_unknown_required")
    witnesses = {}
    for domain, status in (
        ("provider-return.v1", "unusable_provider_return"),
        ("transport-failure.v1", "transport_failure_receipt"),
    ):
        row = await reader.get(db.ExecutionOperationModel, _key(domain, scope, attempt_id))
        _need(
            row is not None
            and row.status == status
            and row.scope_id == scope
            and row.input_hash == effective
            and row.owner == root.owner
            and row.lease_until == root.lease_until,
            "physical_receipt_owner",
        )
        payload = row.result_json
        _need(
            isinstance(payload, dict)
            and payload.get("schema") == domain
            and payload.get("attempt_id") == attempt_id
            and payload.get("operation_key") == root_key,
            "physical_receipt_identity",
        )
        witnesses[domain] = _row(row)
    _need(
        await reader.get(db.ExecutionOperationModel, _key("transport-result.v1", scope, attempt_id))
        is None,
        "usable_transport_conflict",
    )
    returned = witnesses["provider-return.v1"]["result_json"]
    _need(
        returned.get("input_hash") == effective
        and returned.get("model_name") == role.model
        and all(
            returned.get(k) is False for k in ("usable", "adoption_authorized", "credit_released")
        )
        and isinstance(returned.get("wire_body_hash"), str)
        and re.fullmatch(r"[0-9a-f]{64}", returned["wire_body_hash"]) is not None,
        "provider_receipt_invalid",
    )
    failure = witnesses["transport-failure.v1"]["result_json"]
    _need(
        type(failure.get("physical_sends")) is int
        and failure["physical_sends"] == 1
        and failure.get("exception_type") == "UnusableProviderResponse"
        and failure.get("http_status") is None
        and all(
            failure.get(k) is False
            for k in ("stream", "operator_interruption", "partial_stream_observed")
        )
        and failure.get("logical_role") == "critic"
        and failure.get("prompt_template") == admission.TEMPLATE
        and failure.get("project_id") == str(fresh.id)
        and failure.get("workflow_run_id") == str(workflow_id),
        "single_complete_send_required",
    )
    chain = failure.get("error_chain")
    _need(
        isinstance(chain, list)
        and any(
            isinstance(e, dict)
            and e.get("type") == "ValueError"
            and any(
                f.get("function") == "_parse_complete_provider_response"
                for f in e.get("frames", [])
                if isinstance(f, dict)
            )
            for e in chain
        ),
        "parser_failure_required",
    )
    completion = _completion(returned, role.model)
    error = admission._protocol_error(
        completion, supplied["candidate_payload"], supplied, schema=REQUEST_SCHEMA
    )
    _need(error and error.get("detail") == "admission_incomplete_completion", "incomplete_required")
    return {
        "schema": SCHEMA,
        "slot": 0,
        "scope_id": scope,
        "project_id": str(fresh.id),
        "workflow_run_id": str(workflow_id),
        "attempt_id": attempt_id,
        "graded_input_hash": digest,
        "effective_input_hash": effective,
        "output_cap": OUTPUT_CAP,
        "wire_body_hash": returned["wire_body_hash"],
        "wire_reconstructed": False,
        "witnesses": witnesses,
        "attempt_before": _row(attempt),
        "operation_before": _row(root),
        "budget_before": _row(budget),
        "completion": completion,
        "status": "protocol_invalid",
        "error": error,
    }


async def inspect_terminal_planning_return(
    session: AsyncSession,
    settings: AppSettings,
    project: db.ProjectModel | SimpleNamespace,
    workflow_id: UUID,
    supplied: dict,
    attempt_id: str,
) -> dict:
    """Build a sealed journal item before explicit operator reconciliation."""
    with session.no_autoflush:
        proof = await _inspect(
            session, settings, project, workflow_id, supplied, attempt_id, reconciled=False
        )
    proof["evidence_sha256"] = _hash(proof)
    return proof


def _without(mapping: dict, *keys: str) -> dict:
    return {k: v for k, v in mapping.items() if k not in keys}


async def verify_terminal_planning_slot(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: db.ProjectModel | SimpleNamespace,
    workflow_id: UUID,
    supplied: dict,
    item: dict,
    index: int = 0,
) -> str:
    """Verify full-charge reconciliation and return scope of the spent slot only.

    Add only reconciliation_id to the inspected item. The evidence hash is the
    exact one supplied to ExecutionLedger.reconcile; old unknown holds may remain.
    """
    _need(
        type(index) is int
        and index == 0
        and isinstance(item, dict)
        and type(item.get("slot")) is int
        and item["slot"] == 0,
        "original_slot_only",
    )
    sealed = _without(item, "evidence_sha256", "reconciliation_id")
    _need(_hash(sealed) == item.get("evidence_sha256"), "journal_seal")
    with session.no_autoflush:
        current = await _inspect(
            session, settings, project, workflow_id, supplied, item["attempt_id"], reconciled=True
        )
        snapshots = ("attempt_before", "operation_before", "budget_before")
        _need(
            _same(_without(sealed, *snapshots), _without(current, *snapshots)),
            "original_witness_changed",
        )
        for name in snapshots[:2]:
            before, after = item[name], current[name]
            _need(
                before.get("status") == "unknown"
                and _same(_without(before, "status"), _without(after, "status")),
                "original_ledger_changed",
            )
        before, after = item["budget_before"], current["budget_before"]
        _need(
            before.get("status") == "unknown"
            and after.get("status") in {"active", "unknown", "exhausted"}
            and _same(
                _without(before, "status", "metadata_json", "calls_used", "tokens_charged"),
                _without(after, "status", "metadata_json", "calls_used", "tokens_charged"),
            )
            and all(
                type(after.get(k)) is int and type(before.get(k)) is int and after[k] >= before[k]
                for k in ("calls_used", "tokens_charged")
            )
            and _same(
                _without(before["metadata_json"], "reconciliations"),
                _without(after["metadata_json"], "reconciliations"),
            ),
            "financial_snapshot_changed",
        )
        rid = item.get("reconciliation_id")
        _need(isinstance(rid, str) and bool(rid), "reconciliation_required")
        row = await _Reader(session).get(
            db.ExecutionOperationModel, _key("reconciliation.v1", item["scope_id"], rid)
        )
        _need(
            row is not None
            and row.scope_id == item["scope_id"]
            and row.status == "reconciliation_receipt"
            and row.owner == "manual-reconciliation",
            "durable_reconciliation_required",
        )
        record = row.result_json
        receipt = record.get("receipt", {}) if isinstance(record, dict) else {}
        _need(
            record
            and record.get("reconciliation_id") == rid
            and record.get("evidence_kind") == "provider_receipt"
            and record.get("remote_operation_closed") is True
            and record.get("local_dispatcher_stopped") is False
            and record.get("evidence_sha256") == item["evidence_sha256"]
            and receipt.get("decision") == "abandon_charged"
            and receipt.get("attempt_id") == item["attempt_id"]
            and receipt.get("scope_id") == item["scope_id"]
            and receipt.get("reconciliation_id") == rid
            and receipt.get("automatic_resume") is False
            and receipt.get("credit_released") is False,
            "reconciliation_binding",
        )
        evidence = ExecutionReconciliation(
            reconciliation_id=rid,
            attempt_id=item["attempt_id"],
            expected_input_hash=item["effective_input_hash"],
            decision="abandon_charged",
            evidence_kind="provider_receipt",
            evidence_sha256=item["evidence_sha256"],
            actor=record.get("actor"),
            remote_operation_closed=True,
        )
        _need(
            row.input_hash == record.get("request_hash") == _hash(evidence.model_dump(mode="json")),
            "reconciliation_request_hash",
        )
        _need(
            type(receipt.get("remaining_unknown")) is int
            and receipt["remaining_unknown"] >= 0
            and receipt.get("status") in {"active", "unknown", "exhausted"}
            and all(
                type(receipt.get(k)) is int and receipt[k] == before[k]
                for k in ("calls_used", "tokens_charged")
            ),
            "reconciliation_financial_receipt",
        )
    return item["scope_id"]
