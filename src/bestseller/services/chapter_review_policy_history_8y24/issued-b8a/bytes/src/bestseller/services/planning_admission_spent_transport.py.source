"""Read-only admission of one charged, quarantined initial v5 timeout.

The parent alone reconciles the ledger and appends the journal item. Its exact
keys are ``status``, ``wire_version``, ``wire_contract``, ``transport_quarantine``.
No completion, native body, raw grade or semantic verdict belongs in this item.

After reconciliation, read fresh columns using ``_Reader`` and call
``build_transport_quarantine_receipt(scope_id=..., effective_input=...,
attempt=..., root=..., failure=..., reconciliation=...)``. The four rows are the
post-quarantine attempt, root operation, transport failure and durable
reconciliation operation. ``effective_input`` is the original credential-free
``llm._effective_input_payload(request, role)``; the original full prepared
literal request contract and wire version remain at the item's top level.
The builder only copies/hashes evidence; verification supplies its authority.

Receipt keys are closed: schema, scope_id, operation_key, attempt_ids (one ID),
reconciliation_id, effective_input and row_hashes (attempt/root/failure/
reconciliation). Hashes use content_hash over ALL mapped columns, including
otherwise unused metadata and timestamps. Never construct these from cached
ORM objects. This module neither reconciles nor grants replay, credit or grade.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import inspect, or_
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db import models as db
from bestseller.services.budget import ExecutionReconciliation
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader
from bestseller.services.retry_ledger import project_execution_scope
from bestseller.settings import AppSettings

SCHEMA = "planning-admission-spent-transport.v1"
_ITEM_KEYS = {"status", "wire_version", "wire_contract", "transport_quarantine"}
_PROOF_KEYS = {
    "schema",
    "scope_id",
    "operation_key",
    "attempt_ids",
    "reconciliation_id",
    "effective_input",
    "row_hashes",
}
_ROW_MODELS = {
    "attempt": db.ExecutionAttemptModel,
    "root": db.ExecutionOperationModel,
    "failure": db.ExecutionOperationModel,
    "reconciliation": db.ExecutionOperationModel,
}
_FAILURE_KEYS = {
    "attempt_id",
    "operation_key",
    "schema",
    "physical_sends",
    "exception_type",
    "http_status",
    "prompt_template",
    "logical_role",
    "project_id",
    "workflow_run_id",
    "operator_interruption",
    "elapsed_ms",
    "timeout_seconds",
    "stream",
    "partial_stream_observed",
    "error_chain",
    "native_deadline_policy",
}


def _need(condition: object, code: str) -> None:
    if not condition:
        # Fixed diagnostic codes only: never include request data or secrets.
        raise ValueError("planning_admission_spent_transport:" + code)


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _same(left: object, right: object) -> bool:
    # In particular, False and 0 must never be interchangeable witnesses.
    return _json(left) == _json(right)


def _key(domain: str, scope: str, identifier: str) -> str:
    return sha256(f"{domain}\n{scope}\n{identifier}".encode()).hexdigest()


def _row_hash(row: object, model: type) -> str:
    return content_hash({a.key: getattr(row, a.key) for a in inspect(model).column_attrs})


def build_transport_quarantine_receipt(
    *,
    scope_id: str,
    effective_input: dict,
    attempt: object,
    root: object,
    failure: object,
    reconciliation: object,
) -> dict:
    """Pure receipt construction from four fresh POST-reconciliation rows."""
    rows = {"attempt": attempt, "root": root, "failure": failure, "reconciliation": reconciliation}
    return {
        "schema": SCHEMA,
        "scope_id": scope_id,
        "operation_key": root.key,
        "attempt_ids": [attempt.id],
        "reconciliation_id": reconciliation.result_json["reconciliation_id"],
        "effective_input": deepcopy(effective_input),
        "row_hashes": {name: _row_hash(row, _ROW_MODELS[name]) for name, row in rows.items()},
    }


async def verify_quarantined_transport_slot(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: db.ProjectModel | SimpleNamespace,
    workflow_run_id: UUID,
    supplied: dict,
    item: dict,
    journal: list[dict],
    index: int,
    schema: str,
) -> str:
    """Verify fresh immutable charged-timeout evidence; return only its scope.

    Only an initial v5 literal slot with exactly one physical timeout is
    supported. The parent owns subsequent slot identity and feedback. This
    function never calls a provider, flushes, commits or refreshes ORM objects.
    """
    # Admission imports this verifier from its dispatcher; keep imports lazy.
    from bestseller.services import llm
    from bestseller.services import planning_draft_admission as admission
    from bestseller.services.planning_admission_id_protocol import POLICY
    from bestseller.services.planning_admission_id_protocol import SCHEMA as V5
    from bestseller.services.planning_output_budget import LITERAL_ID_SOURCE_VERSION
    from bestseller.services.planning_surgical_budget import resolve_request_role

    _need(type(index) is int and index == 0 and type(journal) is list, "initial_slot_required")
    _need(
        type(item) is dict
        and set(item) == _ITEM_KEYS
        and item.get("status") == "transport_quarantined",
        "journal_item",
    )
    _need(
        schema == V5
        and type(supplied) is dict
        and supplied.get("schema") == V5
        and supplied.get("hard_admission_policy") == POLICY
        and supplied.get("project_id") == str(project.id)
        and supplied.get("workflow_run_id") == str(workflow_run_id)
        and item.get("wire_version") == LITERAL_ID_SOURCE_VERSION,
        "original_v5_identity",
    )
    proof = item.get("transport_quarantine")
    _need(
        type(proof) is dict and set(proof) == _PROOF_KEYS and proof.get("schema") == SCHEMA,
        "receipt_shape",
    )
    scope = project_execution_scope(project)
    _need(proof.get("scope_id") == scope, "scope")
    ids, reconciliation_id = proof.get("attempt_ids"), proof.get("reconciliation_id")
    _need(
        type(ids) is list
        and len(ids) == 1
        and type(ids[0]) is str
        and bool(ids[0])
        and type(reconciliation_id) is str
        and bool(reconciliation_id),
        "single_attempt_required",
    )

    expected, contract = admission.build_literal_admission_request(
        settings,
        project=project,
        workflow_run_id=workflow_run_id,
        supplied=supplied,
        journal=journal[:index],
        index=index,
        verify_retention=False,
    )
    role = resolve_request_role(settings, project, expected)
    payload = llm._effective_input_payload(expected, role)
    effective_hash = llm._effective_input_hash(expected, role)
    _need(
        _same(item.get("wire_contract"), contract)
        and _same(proof.get("effective_input"), payload)
        and contract["effective_input_sha256"] == effective_hash
        and contract["model"] == role.model
        and contract["graded_input_hash"] == content_hash(supplied)
        and contract["candidate_hash"] == content_hash(supplied["candidate_payload"])
        and contract["hard_admission_policy"] == POLICY,
        "original_request_changed",
    )
    operation_id = admission._protocol_operation(
        project,
        workflow_run_id,
        content_hash(supplied),
        journal[:index],
        index,
    )
    root_key = sha256(f"{scope}\n{operation_id}".encode()).hexdigest()
    _need(proof.get("operation_key") == root_key, "root_key")

    reader = _Reader(session)
    # Do not filter by scope or status: an extra/mis-scoped attempt is a conflict.
    attempts = await reader.rows(
        db.ExecutionAttemptModel, db.ExecutionAttemptModel.operation_key == root_key
    )
    _need(len(attempts) == 1 and [a.id for a in attempts] == ids, "actual_attempt_set")
    attempt = attempts[0]
    _need(
        attempt.scope_id == scope
        and attempt.input_hash == effective_hash
        and attempt.model_name == role.model
        and attempt.status == "reconciled_charged"
        and attempt.actual_tokens is None
        and type(attempt.reserved_tokens) is int
        and attempt.reserved_tokens > 0,
        "charged_attempt",
    )
    root = await reader.get(db.ExecutionOperationModel, root_key)
    failure_key = _key("transport-failure.v1", scope, attempt.id)
    reconciliation_key = _key("reconciliation.v1", scope, reconciliation_id)
    failure = await reader.get(db.ExecutionOperationModel, failure_key)
    reconciliation = await reader.get(db.ExecutionOperationModel, reconciliation_key)
    _need(
        root is not None
        and root.scope_id == scope
        and root.input_hash == effective_hash
        and root.status == "stopped"
        and root.result_json is None,
        "stopped_root",
    )
    _need(
        failure is not None
        and failure.scope_id == scope
        and failure.input_hash == effective_hash
        and failure.status == "transport_failure_receipt"
        and failure.owner == root.owner
        and type(failure.result_json) is dict,
        "failure_row",
    )
    diagnostic = failure.result_json
    chain = diagnostic.get("error_chain")
    _need(
        set(diagnostic) <= _FAILURE_KEYS
        and diagnostic.get("schema") == "transport-failure.v1"
        and diagnostic.get("attempt_id") == attempt.id
        and diagnostic.get("operation_key") == root_key
        and type(diagnostic.get("physical_sends")) is int
        and diagnostic["physical_sends"] == 1
        and diagnostic.get("exception_type")
        in {"Timeout", "TimeoutError", "APITimeoutError", "ReadTimeout"}
        and (
            diagnostic.get("http_status") is None
            or (type(diagnostic["http_status"]) is int and diagnostic["http_status"] == 408)
        )
        and diagnostic.get("operator_interruption") is False
        and diagnostic.get("partial_stream_observed") is False
        and diagnostic.get("project_id") == str(project.id)
        and diagnostic.get("workflow_run_id") == str(workflow_run_id)
        and diagnostic.get("prompt_template") == admission.TEMPLATE
        and diagnostic.get("logical_role") == "critic"
        and type(chain) is list
        and all(type(e) is dict for e in chain)
        and (
            diagnostic["exception_type"] == "ReadTimeout"
            or any(e.get("type") == "ReadTimeout" for e in chain)
        ),
        "single_timeout_required",
    )
    _need(
        reconciliation is not None
        and reconciliation.scope_id == scope
        and reconciliation.status == "reconciliation_receipt"
        and type(reconciliation.result_json) is dict,
        "reconciliation_row",
    )
    record = reconciliation.result_json
    _need(
        set(record)
        == {
            "reconciliation_id",
            "request_hash",
            "actor",
            "evidence_kind",
            "evidence_sha256",
            "remote_operation_closed",
            "local_dispatcher_stopped",
            "at",
            "receipt",
        }
        and record.get("reconciliation_id") == reconciliation_id
        and record.get("evidence_kind") == "dispatcher_exit"
        and record.get("remote_operation_closed") is False
        and record.get("local_dispatcher_stopped") is True
        and type(record.get("receipt")) is dict,
        "dispatcher_exit_required",
    )
    receipt = record["receipt"]
    _need(
        set(receipt)
        == {
            "scope_id",
            "reconciliation_id",
            "attempt_id",
            "decision",
            "status",
            "remaining_unknown",
            "calls_used",
            "tokens_charged",
            "automatic_resume",
            "credit_released",
            "remote_outcome",
            "operation_replay_allowed",
        }
        and receipt.get("scope_id") == scope
        and receipt.get("reconciliation_id") == reconciliation_id
        and receipt.get("attempt_id") == attempt.id
        and receipt.get("decision") == "quarantine_charged"
        and receipt.get("remote_outcome") == "unknown_quarantined"
        and receipt.get("operation_replay_allowed") is False
        and receipt.get("credit_released") is False
        and receipt.get("automatic_resume") is False
        and type(receipt.get("calls_used")) is int
        and receipt["calls_used"] >= 1
        and type(receipt.get("tokens_charged")) is int
        and receipt["tokens_charged"] >= attempt.reserved_tokens,
        "quarantine_receipt",
    )
    evidence = ExecutionReconciliation(
        reconciliation_id=reconciliation_id,
        attempt_id=attempt.id,
        expected_input_hash=effective_hash,
        decision="quarantine_charged",
        evidence_kind=record["evidence_kind"],
        evidence_sha256=record["evidence_sha256"],
        actor=record["actor"],
        remote_operation_closed=False,
        local_dispatcher_stopped=True,
    )
    _need(
        content_hash(evidence.model_dump(mode="json"))
        == record["request_hash"]
        == reconciliation.input_hash,
        "reconciliation_fingerprint",
    )
    fresh = build_transport_quarantine_receipt(
        scope_id=scope,
        effective_input=payload,
        attempt=attempt,
        root=root,
        failure=failure,
        reconciliation=reconciliation,
    )
    _need(_same(proof, fresh), "post_quarantine_rows_changed")

    # Known domain keys plus actual row links prevent hidden/late native returns,
    # duplicate failures and extra reconciliations, even if receipt IDs are forged.
    return_keys = [
        _key(domain, scope, attempt.id) for domain in ("transport-result.v1", "provider-return.v1")
    ]
    operations = await reader.rows(
        db.ExecutionOperationModel,
        or_(
            db.ExecutionOperationModel.key.in_(return_keys),
            db.ExecutionOperationModel.result_json["operation_key"].as_string() == root_key,
            db.ExecutionOperationModel.result_json["attempt_id"].as_string() == attempt.id,
            db.ExecutionOperationModel.result_json["receipt"]["attempt_id"].as_string()
            == attempt.id,
        ),
    )
    _need(
        {r.key for r in operations} == {failure_key, reconciliation_key}, "unexpected_slot_record"
    )
    runs = await reader.rows(
        db.LlmRunModel,
        or_(
            db.LlmRunModel.prompt_hash == effective_hash,
            db.LlmRunModel.metadata_json["execution_receipt"]["operation_key"].as_string()
            == root_key,
            db.LlmRunModel.metadata_json["execution_receipt"]["attempt_id"].as_string()
            == attempt.id,
        ),
    )
    _need(not runs, "old_slot_llm_run")
    budget = await reader.get(db.ExecutionBudgetModel, scope)
    charged_attempts = await reader.rows(
        db.ExecutionAttemptModel, db.ExecutionAttemptModel.scope_id == scope
    )
    amounts = [
        a.actual_tokens if a.actual_tokens is not None else a.reserved_tokens
        for a in charged_attempts
    ]
    _need(
        budget is not None
        and all(type(n) is int and n >= 0 for n in amounts)
        and budget.calls_used == len(charged_attempts) >= receipt["calls_used"]
        and budget.tokens_charged == sum(amounts) >= receipt["tokens_charged"],
        "retained_charges",
    )
    return scope
