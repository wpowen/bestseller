"""Reuse an operator-quarantined commercial draw as unavailable, never a grade.

Only the cohort journal is projected by callers. Financial rows are read here;
ExecutionLedger.reconcile remains an explicit operator action.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    WorkflowRunModel,
)
from bestseller.services.budget import (
    ExecutionBudgetError,
    ExecutionReconciliation,
    current_execution_scope,
    execution_operation_id,
    execution_scope_block_code,
)
from bestseller.services.commercial_source_evidence import CommercialSources

SCHEMA = "commercial-slot-quarantine.v1"
# ruff: noqa: ANN401
SOURCE_KEYS = (
    "body_sha256",
    "semantic_source_sha256",
    "previous_sha256",
    "source_protocol_sha256",
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def requires_verified_quarantine_slots(
    result: LLMQualityJudgeResult | Mapping[str, Any],
) -> bool:
    """Detect exact decoded quarantine envelopes, never text inside paid wire data."""
    try:
        raw = result.get("raw_excerpt") if isinstance(result, Mapping) else result.raw_excerpt
        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            return False
        if envelope.get("schema") == SCHEMA:
            return True
        if envelope.get("schema_version") != "chapter-quality-aggregation.v1":
            return False
        samples = envelope.get("samples")
        if not isinstance(samples, list):
            return False
        for sample in samples:
            if not isinstance(sample, dict) or not isinstance(sample.get("report"), dict):
                continue
            try:
                child = json.loads(sample["report"].get("raw_excerpt", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(child, dict) and child.get("schema") == SCHEMA:
                return True
    except (ValueError, TypeError):
        pass
    return False


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("commercial_slot_quarantine_" + reason)


def _row(row: Any) -> dict[str, Any]:
    return json.loads(
        json.dumps(
            {column.name: getattr(row, column.name) for column in row.__table__.columns},
            default=str,
        )
    )


def _operation_key(scope: str, namespace: str, request: Any, input_hash: str, slot: str) -> str:
    operation_id = (
        execution_operation_id(
            namespace,
            subject=f"{request.logical_role}:{request.prompt_template}:{request.prompt_version}",
            revision=input_hash,
        )
        + f":sample:{slot}"
    )
    return hashlib.sha256(f"{scope}\n{operation_id}".encode()).hexdigest()


def _round2_input_binding(
    request: Any,
    prepared: Mapping[str, Any],
    binding: Mapping[str, Any],
    slot: str,
    sources: CommercialSources,
    role: Any,
    current_input_hash: str | None,
) -> dict[str, Any]:
    """Prove the base and feedback-bearing physical input without rewriting either."""
    from bestseller.services.commercial_judge_recovery import prepare_commercial_slot_retry
    from bestseller.services.commercial_review_transport import (
        _INSTRUCTIONS,
        _NOTICE,
        _load,
        prepare_commercial_review_transport,
        verify_commercial_review_transport,
    )
    from bestseller.services.llm import _effective_input_hash

    context = prepared["retry_context"]
    prior = LLMQualityJudgeResult.model_validate(prepared["previous_report"])
    prior_envelope = json.loads(prior.raw_excerpt)
    retry_slot = (context.get("slots") or {}).get(slot) or {}
    _need(
        type(context.get("attempt")) is int
        and context["attempt"] == 2
        and retry_slot.get("report") == prior.model_dump(mode="json")
        and type(retry_slot.get("spent_attempt")) is int
        and retry_slot.get("spent_attempt") == 1
        and retry_slot.get("evaluation_contract") == binding["evaluation_contract"]
        and retry_slot.get("source_binding") == binding["source_binding"]
        and retry_slot.get("raw_report") == prior_envelope.get("raw_report")
        and retry_slot.get("exact_source_audit") == prior_envelope.get("exact_source_audit")
        and retry_slot.get("prior_validation_errors") == prior_envelope.get("validation_errors")
        and prior_envelope.get("schema_version") == "commercial-slot-evidence.v1"
        and prior_envelope.get("sample_slot") == slot
        and type(prior_envelope.get("attempt")) is int
        and prior_envelope.get("attempt") == 1
        and prior_envelope.get("raw_report_sha256")
        == hashlib.sha256(retry_slot["raw_report"].encode()).hexdigest()
        and prior_envelope.get("source_binding") == binding["source_binding"]
        and prior_envelope.get("evaluation_contract") == binding["evaluation_contract"],
        "retry_context_changed",
    )
    reused, feedback, recovery_metadata = prepare_commercial_slot_retry(
        context,
        sample_slot=slot,
        sources=sources,
        evaluation_contract=binding["evaluation_contract"],
    )
    _need(
        reused is None
        and bool(feedback)
        and request.metadata.get("commercial_evidence_recovery") == recovery_metadata,
        "retry_feedback_changed",
    )
    logical = request.user_prompt
    transport = request.metadata.get("commercial_source_transport")
    if transport is not None:
        verify_commercial_review_transport(logical, sources=sources, receipt=transport)
        mode = transport["encoding"]
        instruction = "" if mode == "identity" else _INSTRUCTIONS[mode] + _NOTICE
        logical = (
            logical[: transport["packet_start"] - len(instruction)]
            + sources.text
            + logical[transport["packet_end"] :]
        )
    header, separator, expected_json = feedback.partition("{")
    _need(bool(separator), "retry_feedback_changed")
    base_logical, separator, retained_json = logical.rpartition(header)
    # JSONB can reorder the retained context's object keys. Authenticate all
    # typed feedback values, while preserving the physical request's bytes.
    _need(
        bool(separator) and _digest(_load(retained_json)) == _digest(_load("{" + expected_json)),
        "retry_feedback_changed",
    )
    base_wire, _ = prepare_commercial_review_transport(
        request.system_prompt,
        base_logical,
        sources=sources,
        tools=request.tools,
        output_tokens=request.max_tokens_override,
    )
    final_wire, final_transport = prepare_commercial_review_transport(
        request.system_prompt,
        logical,
        sources=sources,
        tools=request.tools,
        output_tokens=request.max_tokens_override,
    )
    expected_transport = final_transport if final_transport["encoding"] != "identity" else None
    base_hash = _effective_input_hash(request.model_copy(update={"user_prompt": base_wire}), role)
    _need(
        base_hash == current_input_hash
        and final_wire == request.user_prompt
        and transport == expected_transport,
        "input_hash_changed",
    )
    # Old round-2 producers omitted this metadata. Its absence is accepted only
    # after exact reconstruction; a present final-input receipt must be exact.
    final_hash = _effective_input_hash(request, role)
    receipt = request.metadata.get("prepared_effective_input")
    _need(
        "prepared_effective_input" not in request.metadata
        or receipt == {"schema": "effective-input.v1", "hash": final_hash},
        "input_hash_changed",
    )
    return {
        "base_input_hash": base_hash,
        "final_input_hash": final_hash,
        "retry_context_sha256": _digest(context),
        "sources": {
            "text": sources.text,
            "catalog": deepcopy(dict(sources.catalog)),
            "receipt": deepcopy(dict(sources.receipt)),
        },
    }


async def project_quarantined_slot(
    session: AsyncSession,
    *,
    journal: dict[str, Any],
    key: str,
    owner_id: UUID | str,
    slot: str,
    source_receipt: Mapping[str, Any] | None = None,
    effective_critic: Mapping[str, Any] | None = None,
    current_input_hash: str | None = None,
    sources: CommercialSources | None = None,
) -> tuple[LLMQualityJudgeResult, dict[str, Any]] | None:
    """Delegate prepared cache/claim paths; verify exact stopped quarantines.

    A quarantined entry is reconstructed from its retained prepared entry and
    live immutable proofs on every read, including aggregate binding.
    """
    from bestseller.services.llm import LLMCompletionRequest, _effective_input_hash
    from bestseller.settings import LLMRoleSettings

    entry = journal["slots"][slot]
    existing = entry.get("state") == "quarantined"
    if existing:
        history = entry.get("quarantine_history")
        _need(isinstance(history, list) and bool(history), "history_required")
        saved = history[-1]
        prepared, saved_proof = saved["prepared_entry"], saved["proof"]
        source_receipt = source_receipt or prepared["source_receipt"]
        effective_critic = effective_critic or saved_proof["effective_critic"]
        retry_proof = saved_proof.get("round2_input_binding")
        current_input_hash = current_input_hash or (
            retry_proof["base_input_hash"] if retry_proof else saved_proof["input_hash"]
        )
        if sources is None and retry_proof:
            sources = CommercialSources(**retry_proof["sources"])
    else:
        _need(entry.get("state") == "prepared", "prepared_required")
        prepared = entry
        if "request" not in prepared:
            return None
    binding = journal["binding"]
    scope = binding["scope_id"]
    _need(current_execution_scope() == scope, "scope_changed")
    budget = await session.get(ExecutionBudgetModel, scope, populate_existing=True)
    code = execution_scope_block_code(budget) if budget else "execution_scope_missing"
    if code and code not in {"execution_deadline_exhausted", "execution_budget_exhausted"}:
        raise ExecutionBudgetError(code, scope)
    _need(
        prepared.get("state") == "prepared"
        and type(prepared.get("used_attempts")) is int
        and prepared["used_attempts"] in (1, 2),
        "round_changed",
    )
    if prepared["used_attempts"] == 2:
        previous = LLMQualityJudgeResult.model_validate(prepared.get("previous_report"))
        _need(
            previous.infrastructure_failure
            and previous.llm_run_id is not None
            and (prepared.get("retry_context") or {}).get("attempt") == 2,
            "round_changed",
        )
    _need(
        isinstance(source_receipt, Mapping) and isinstance(effective_critic, Mapping),
        "input_context_required",
    )
    request = LLMCompletionRequest.model_validate(prepared["request"])
    metadata = request.metadata
    _need(
        prepared["used_attempts"] == 2
        or (
            metadata.get("commercial_evidence_recovery") is None
            and prepared.get("retry_context") is None
            and prepared.get("previous_report") is None
        ),
        "round_changed",
    )
    _need(
        request.logical_role == "critic"
        and request.prompt_template == "chapter_commercial_quality_judge"
        and request.prompt_version == prepared["source_receipt"]["prompt_version"]
        and str(request.project_id) == binding["project_id"]
        and str(request.workflow_run_id) == str(owner_id)
        and request.sample_slot == slot
        and request.operation_id is None,
        "request_changed",
    )
    namespace = (
        f"commercial-component:{key}:policy:{_digest(binding['evaluation_contract'])}"
        f":round:{prepared['used_attempts']}"
        if journal.get("protocol_successor_workflow_id")
        else f"commercial-component:{key}:round:{prepared['used_attempts']}"
    )
    input_hash = _effective_input_hash(request, LLMRoleSettings.model_validate(effective_critic))
    _need(
        binding["source_binding"] == {k: source_receipt[k] for k in SOURCE_KEYS}
        and binding["source_binding"] == {k: prepared["source_receipt"][k] for k in SOURCE_KEYS}
        and binding["effective_critic_sha256"] == _digest(effective_critic)
        and metadata.get("commercial_evaluation_contract") == binding["evaluation_contract"]
        and metadata.get("commercial_sample_slot") == slot
        and metadata.get("commercial_effective_critic_sha256") == _digest(effective_critic)
        and all(
            (metadata.get("commercial_source_input_receipt") or {}).get(k) == v
            for k, v in prepared["source_receipt"].items()
        ),
        "source_changed",
    )
    if prepared["used_attempts"] == 1:
        _need(
            metadata.get("prepared_effective_input")
            == {"schema": "effective-input.v1", "hash": input_hash},
            "input_hash_changed",
        )
    root_key = _operation_key(scope, namespace, request, input_hash, slot)
    root = await session.get(ExecutionOperationModel, root_key, populate_existing=True)
    if root is None and not existing:
        # A wholly unsent frozen request retains its original bytes even when
        # harmless current audit metadata changes. Paid/claimed roots below
        # require the exact fresh input before delegating cache/claim handling.
        candidates = list(
            await session.scalars(
                select(ExecutionOperationModel)
                .where(
                    ExecutionOperationModel.scope_id == scope,
                    ExecutionOperationModel.status.in_(
                        ("stopped", "unknown", "pending", "completed")
                    ),
                )
                .execution_options(populate_existing=True)
            )
        )
        matches = [
            row
            for row in candidates
            if row.key == _operation_key(scope, namespace, request, row.input_hash, slot)
        ]
        if not matches:
            return None
        _need(len(matches) == 1, "root_ambiguous")
        root = matches[0]
        root_key = root.key
    _need(
        prepared.get("source_receipt") == source_receipt
        and binding["source_binding"] == {k: source_receipt[k] for k in SOURCE_KEYS}
        and binding["effective_critic_sha256"] == _digest(effective_critic),
        "source_changed",
    )
    draft = await session.get(
        ChapterDraftVersionModel, UUID(binding["draft_id"]), populate_existing=True
    )
    _need(
        draft is not None
        and str(draft.project_id) == binding["project_id"]
        and str(draft.chapter_id) == binding["chapter_id"]
        and hashlib.sha256(draft.content_md.encode()).hexdigest() == source_receipt["body_sha256"],
        "body_changed",
    )
    expected_key = _digest(
        {
            "project_id": binding["project_id"],
            "chapter_id": binding["chapter_id"],
            "body_sha256": source_receipt["body_sha256"],
        }
    )
    _need(
        key == expected_key
        and slot in {f"chapter-quality.independent-v2:{i}" for i in range(binding["samples"])},
        "cohort_changed",
    )
    paid_source = metadata.get("commercial_source_input_receipt") or {}
    _need(
        request.logical_role == "critic"
        and request.prompt_template == "chapter_commercial_quality_judge"
        and request.prompt_version == source_receipt["prompt_version"]
        and str(request.project_id) == binding["project_id"]
        and str(request.workflow_run_id) == str(owner_id)
        and request.sample_slot == slot
        and request.operation_id is None
        and metadata.get("commercial_sample_slot") == slot
        and metadata.get("commercial_evaluation_contract") == binding["evaluation_contract"]
        and metadata.get("commercial_effective_critic_sha256") == _digest(effective_critic)
        and all(paid_source.get(k) == v for k, v in source_receipt.items()),
        "request_changed",
    )
    round2_binding = None
    if prepared["used_attempts"] == 2:
        _need(
            sources is not None and dict(sources.receipt) == source_receipt,
            "retry_sources_required",
        )
        round2_binding = _round2_input_binding(
            request,
            prepared,
            binding,
            slot,
            sources,
            LLMRoleSettings.model_validate(effective_critic),
            current_input_hash,
        )
    else:
        _need(
            input_hash == current_input_hash
            and metadata.get("prepared_effective_input")
            == {"schema": "effective-input.v1", "hash": input_hash},
            "input_hash_changed",
        )
    _need(
        root is not None
        and root.scope_id == scope
        and root.input_hash == input_hash
        and root.key == _operation_key(scope, namespace, request, input_hash, slot),
        "root_identity_changed",
    )
    if not existing and root.status != "stopped":
        return None  # Exact prepared identities retain the gateway cache/claim guards.
    _need(
        root is not None
        and root.scope_id == scope
        and root.input_hash == input_hash
        and root.status == "stopped"
        and root.result_json is None,
        "terminal_root_required",
    )
    attempts = list(
        await session.scalars(
            select(ExecutionAttemptModel)
            .where(
                ExecutionAttemptModel.operation_key == root_key,
            )
            .order_by(ExecutionAttemptModel.id)
            .execution_options(populate_existing=True)
        )
    )
    if not existing and not attempts:
        # The gateway commits reserve before any physical send. A stopped
        # claim without attempts is not a paid quarantine. Keep this exact
        # prepared identity and let claim perform its audited unsent reclaim.
        # Contradictory retained returns/failures must still fail closed.
        linked = await session.scalar(
            select(ExecutionOperationModel.key)
            .where(
                ExecutionOperationModel.status != "unsent_reclaim_receipt",
                or_(
                    ExecutionOperationModel.result_json["operation_key"].as_string() == root_key,
                    ExecutionOperationModel.result_json["receipt"]["operation_key"].as_string()
                    == root_key,
                ),
            )
            .limit(1)
        )
        returned = await session.scalar(
            select(LlmRunModel.id)
            .where(
                LlmRunModel.metadata_json["execution_receipt"]["operation_key"].as_string()
                == root_key,
            )
            .limit(1)
        )
        _need(linked is None and returned is None, "unsent_claim_conflicting_evidence")
        return None
    _need(
        bool(attempts)
        and all(
            a.scope_id == scope
            and a.input_hash == input_hash
            and a.model_name == effective_critic["model"]
            and a.status == "reconciled_charged"
            and type(a.reserved_tokens) is int
            and a.reserved_tokens > 0
            and a.actual_tokens is None
            for a in attempts
        ),
        "attempts_not_closed",
    )
    ids = {a.id for a in attempts}
    operations = list(
        await session.scalars(
            select(ExecutionOperationModel)
            .where(
                or_(
                    ExecutionOperationModel.scope_id == scope,
                    ExecutionOperationModel.result_json["operation_key"].as_string() == root_key,
                    ExecutionOperationModel.result_json["attempt_id"].as_string().in_(ids),
                    ExecutionOperationModel.result_json["receipt"]["attempt_id"]
                    .as_string()
                    .in_(ids),
                )
            )
            .execution_options(populate_existing=True)
        )
    )
    by_key = {row.key: row for row in operations}
    _need(
        not any(
            row.key != root_key
            and isinstance(row.result_json, dict)
            and (
                row.result_json.get("operation_key") == root_key
                or row.result_json.get("attempt_id") in ids
            )
            and row.status != "transport_failure_receipt"
            for row in operations
        ),
        "conflicting_return",
    )
    linked_failures = [
        row
        for row in operations
        if isinstance(row.result_json, dict)
        and row.result_json.get("operation_key") == root_key
        and row.status == "transport_failure_receipt"
    ]
    _need(len(linked_failures) == len(attempts), "physical_failures_changed")
    runs = list(
        await session.scalars(
            select(LlmRunModel)
            .where(
                LlmRunModel.metadata_json["execution_receipt"]["operation_key"].as_string()
                == root_key,
            )
            .execution_options(populate_existing=True)
        )
    )
    _need(not runs, "conflicting_run")
    failures, reconciliations = [], []
    for attempt in attempts:
        failure_key = hashlib.sha256(
            f"transport-failure.v1\n{scope}\n{attempt.id}".encode()
        ).hexdigest()
        failure = by_key.get(failure_key)
        diagnostic = failure.result_json if failure else {}
        _need(
            failure is not None
            and failure.scope_id == scope
            and failure.status == "transport_failure_receipt"
            and failure.input_hash == input_hash
            and failure.owner == root.owner
            and isinstance(diagnostic, dict)
            and diagnostic.get("schema") == "transport-failure.v1"
            and diagnostic.get("operation_key") == root_key
            and diagnostic.get("attempt_id") == attempt.id
            and type(diagnostic.get("physical_sends")) is int
            and diagnostic["physical_sends"] == 1
            and diagnostic.get("prompt_template") == request.prompt_template
            and diagnostic.get("logical_role") == "critic"
            and diagnostic.get("project_id") == str(request.project_id)
            and diagnostic.get("workflow_run_id") == str(request.workflow_run_id)
            and diagnostic.get("partial_stream_observed") is False,
            "physical_failure_required",
        )
        matching = [
            row
            for row in operations
            if row.status == "reconciliation_receipt"
            and isinstance(row.result_json, dict)
            and (row.result_json.get("receipt") or {}).get("attempt_id") == attempt.id
        ]
        _need(len(matching) == 1, "reconciliation_required")
        reconciliation = matching[0]
        record = reconciliation.result_json
        receipt = record["receipt"]
        _need(
            isinstance(receipt, dict)
            and set(receipt)
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
            },
            "reconciliation_receipt_shape",
        )
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
            and record.get("remote_operation_closed") is False
            and record.get("local_dispatcher_stopped") is True
            and record.get("evidence_kind") == "dispatcher_exit",
            "dispatcher_exit_required",
        )
        evidence = ExecutionReconciliation.model_validate(
            {
                **{
                    k: record[k]
                    for k in (
                        "reconciliation_id",
                        "actor",
                        "evidence_kind",
                        "evidence_sha256",
                        "remote_operation_closed",
                        "local_dispatcher_stopped",
                    )
                },
                "attempt_id": attempt.id,
                "expected_input_hash": input_hash,
                "decision": receipt.get("decision"),
                "result": None,
            }
        )
        expected_receipt_key = hashlib.sha256(
            f"reconciliation.v1\n{scope}\n{evidence.reconciliation_id}".encode()
        ).hexdigest()
        at = datetime.fromisoformat(record["at"])
        created = (
            attempt.created_at.replace(tzinfo=UTC)
            if attempt.created_at.tzinfo is None
            else attempt.created_at
        )
        _need(
            reconciliation.key == expected_receipt_key
            and reconciliation.scope_id == scope
            and reconciliation.owner == "manual-reconciliation"
            and reconciliation.input_hash
            == record.get("request_hash")
            == _digest(evidence.model_dump(mode="json"))
            and evidence.decision == "quarantine_charged"
            and all(
                record[k] == getattr(evidence, k)
                for k in ("reconciliation_id", "actor", "evidence_kind", "evidence_sha256")
            )
            and at.tzinfo is not None
            and at >= created
            and receipt.get("scope_id") == scope
            and receipt.get("reconciliation_id") == evidence.reconciliation_id
            and receipt.get("attempt_id") == attempt.id
            and receipt.get("remote_outcome") == "unknown_quarantined"
            and receipt.get("credit_released") is False
            and receipt.get("automatic_resume") is False
            and receipt.get("operation_replay_allowed") is False
            and receipt.get("status") in {"active", "unknown", "exhausted", "cancelled"}
            and type(receipt.get("remaining_unknown")) is int
            and receipt["remaining_unknown"] >= 0
            and type(receipt.get("calls_used")) is int
            and 1 <= receipt["calls_used"] <= budget.calls_used
            and type(receipt.get("tokens_charged")) is int
            and attempt.reserved_tokens <= receipt["tokens_charged"] <= budget.tokens_charged,
            "reconciliation_changed",
        )
        failures.append(_row(failure))
        reconciliations.append(_row(reconciliation))
    proof = {
        "schema": SCHEMA,
        "scope_id": scope,
        "cohort_key": key,
        "slot": slot,
        "round": prepared["used_attempts"],
        "namespace": namespace,
        "input_hash": input_hash,
        "request_sha256": _digest(prepared["request"]),
        "source_receipt_sha256": _digest(source_receipt),
        "effective_critic": deepcopy(dict(effective_critic)),
        "root": _row(root),
        "attempts": [_row(a) for a in attempts],
        "failures": failures,
        "reconciliations": reconciliations,
        **({"round2_input_binding": round2_binding} if round2_binding else {}),
    }
    result = LLMQualityJudgeResult.model_validate(
        {
            "scope": "chapter",
            "passed": False,
            "overall_score": 0,
            "dimension_scores": {},
            "llm_run_id": None,
            "blocking_issues": [
                {
                    "code": "CHAPTER_JUDGE_UNAVAILABLE",
                    "severity": "critical",
                    "evidence": "Paid slot quarantined with unknown remote outcome; no grade.",
                    "required_fix": (
                        "Retain the charged slot; no replay, replacement sample or prose rewrite."
                    ),
                }
            ],
            "rewrite_plan": {
                "scope": "judge",
                "instructions": "No retry or chapter rewrite is authorized.",
            },
            "raw_excerpt": json.dumps(
                {
                    "schema": SCHEMA,
                    "operation_key": root_key,
                    "sample_slot": slot,
                    "attempt": prepared["used_attempts"],
                    "proof_sha256": _digest(proof),
                },
                sort_keys=True,
            ),
        }
    )
    projected = {
        **deepcopy(prepared),
        "state": "quarantined",
        "quarantine_history": [
            *(prepared.get("quarantine_history") or []),
            {"prepared_entry": deepcopy(prepared), "proof": proof},
        ],
        "report": result.model_dump(mode="json"),
    }
    if existing:
        _need(entry == projected, "projection_changed")
    return result, projected


async def verify_quarantined_aggregate(
    session: AsyncSession,
    *,
    journal: dict[str, Any],
    key: str,
    owner_id: UUID | str,
    result: LLMQualityJudgeResult,
) -> None:
    """Retain original N/majority and exact child reports; never regrade them."""
    slots = journal["slots"]
    n = journal["binding"]["samples"]
    _need(
        set(slots) == {f"chapter-quality.independent-v2:{i}" for i in range(n)},
        "aggregate_slots_changed",
    )
    for slot, entry in slots.items():
        if entry["state"] == "quarantined":
            await project_quarantined_slot(
                session, journal=journal, key=key, owner_id=owner_id, slot=slot
            )
    if n == 1:
        _need(
            result.model_dump(mode="json") == next(iter(slots.values()))["report"],
            "aggregate_report_changed",
        )
        return
    receipt = json.loads(result.raw_excerpt)
    valid = sum(
        not LLMQualityJudgeResult.model_validate(e["report"]).infrastructure_failure
        for e in slots.values()
    )
    _need(
        receipt.get("schema_version") == "chapter-quality-aggregation.v1"
        and receipt.get("requested_samples") == n
        and receipt.get("required_valid_samples") == n // 2 + 1
        and receipt.get("valid_samples") == valid
        and len(receipt.get("samples", [])) == n,
        "aggregate_majority_changed",
    )
    for index, child in enumerate(receipt["samples"]):
        slot = f"chapter-quality.independent-v2:{index}"
        expected = slots[slot]["report"]
        _need(
            child
            == {
                "sample_slot": slot,
                "report": expected,
                "valid": not LLMQualityJudgeResult.model_validate(expected).infrastructure_failure,
            },
            "aggregate_report_changed",
        )
    _need(
        valid >= n // 2 + 1 or (result.infrastructure_failure and not result.passed),
        "aggregate_insufficient_majority",
    )


async def load_verified_quarantined_slot_proofs(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    content_md: str,
    result: LLMQualityJudgeResult,
    sources: CommercialSources,
) -> dict[str, dict[str, Any]]:
    """Read live quarantine custody for a pure action projection; never grant a grade.

    The exact retained aggregate, chapter pointer, owner and current source must
    agree before any per-slot action receipt is returned. No journal or ledger
    writes, reconciliation, sampling or report normalization occur here.
    """
    from bestseller.services.commercial_judge_recovery import COHORT_SCHEMA

    # A regular result without a retained commercial cohort needs no quarantine
    # receipts. A quarantine marker can never use this compatibility path.
    contains_quarantine = requires_verified_quarantine_slots(result)
    key = _digest(
        {
            "project_id": str(project_id),
            "chapter_id": str(chapter_id),
            "body_sha256": hashlib.sha256(content_md.encode()).hexdigest(),
        }
    )
    with session.no_autoflush:
        chapter = await session.get(ChapterModel, chapter_id, populate_existing=True)
        _need(chapter is not None and chapter.project_id == project_id, "action_chapter_changed")
        pointer = ((chapter.metadata_json or {}).get("commercial_component_cohorts") or {}).get(key)
        if pointer is None:
            _need(not contains_quarantine, "action_pointer_required")
            return {}
        owner = await session.get(
            WorkflowRunModel,
            UUID(pointer["workflow_run_id"]),
            populate_existing=True,
        )
        _need(
            owner is not None
            and owner.project_id == project_id
            and owner.scope_type == "chapter"
            and owner.scope_id == chapter_id,
            "action_owner_changed",
        )
        journal = ((owner.metadata_json or {}).get("commercial_component_cohorts") or {}).get(key)
        _need(
            isinstance(journal, dict) and journal.get("schema") == COHORT_SCHEMA,
            "action_journal_required",
        )
        binding = journal["binding"]
        _need(pointer.get("binding_sha256") == _digest(binding), "action_pointer_changed")
        slots = journal["slots"]
        quarantined = {s: e for s, e in slots.items() if e.get("state") == "quarantined"}
        if not quarantined:
            _need(not contains_quarantine, "action_quarantine_required")
            return {}
        _need(
            binding["project_id"] == str(project_id)
            and binding["chapter_id"] == str(chapter_id)
            and binding["scope_id"] == current_execution_scope()
            and binding["source_binding"] == {k: sources.receipt[k] for k in SOURCE_KEYS}
            and sources.receipt["body_sha256"] == hashlib.sha256(content_md.encode()).hexdigest(),
            "action_binding_changed",
        )
        draft = await session.get(
            ChapterDraftVersionModel,
            UUID(binding["draft_id"]),
            populate_existing=True,
        )
        _need(
            draft is not None
            and draft.project_id == project_id
            and draft.chapter_id == chapter_id
            and draft.content_md == content_md,
            "action_draft_changed",
        )
        aggregate = journal.get("aggregate") or {}
        _need(
            aggregate.get("slots_sha256") == _digest({s: e["report"] for s, e in slots.items()})
            and aggregate.get("report") == result.model_dump(mode="json")
            and all(e.get("state") in {"completed", "quarantined"} for e in slots.values()),
            "action_aggregate_changed",
        )
        await verify_quarantined_aggregate(
            session,
            journal=journal,
            key=key,
            owner_id=owner.id,
            result=result,
        )
        receipts = {}
        for slot, entry in quarantined.items():
            checked = await project_quarantined_slot(
                session,
                journal=journal,
                key=key,
                owner_id=owner.id,
                slot=slot,
            )
            _need(checked is not None, "action_quarantine_required")
            report, verified = checked
            proof = verified["quarantine_history"][-1]["proof"]
            envelope = json.loads(report.raw_excerpt)
            _need(envelope.get("proof_sha256") == _digest(proof), "action_proof_changed")
            receipts[slot] = {
                "schema": "verified-quarantined-slot-action.v1",
                "proof": deepcopy(proof),
                "source_binding": deepcopy(binding["source_binding"]),
                "evaluation_contract": deepcopy(binding["evaluation_contract"]),
                "original_report_sha256": _digest(entry["report"]),
            }
        return receipts


async def load_verified_historical_quarantine_roots(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    body_sha256: str,
    scope: str,
    workflows: list[WorkflowRunModel],
) -> dict[str, dict[str, Any]]:
    """Prove another body's retained commercial purpose, never spend or grade.

    Scan all project chapters independently of the caller's same-chapter
    journal checks. Only canonical, live-reverified quarantines can exclude a
    root from ambiguity; no receipt, request or historical row is rewritten.
    """
    from bestseller.services.commercial_judge_recovery import COHORT_SCHEMA

    candidates = []
    for workflow in workflows:
        if workflow.project_id != project_id:
            continue
        journals = (workflow.metadata_json or {}).get("commercial_component_cohorts") or {}
        if not isinstance(journals, Mapping):
            continue  # The caller's existing malformed-journal guard still runs.
        for key, journal in journals.items():
            if not isinstance(journal, Mapping):
                continue
            binding = journal.get("binding") or {}
            if not isinstance(binding, Mapping) or binding.get("scope_id") != scope:
                continue
            slots = journal.get("slots") or {}
            if isinstance(slots, Mapping):
                candidates.extend(
                    (workflow.id, key, slot)
                    for slot, entry in slots.items()
                    if isinstance(entry, Mapping) and entry.get("state") == "quarantined"
                )
    if not candidates:
        return {}
    _need(current_execution_scope() == scope, "historical_scope_changed")
    _need(
        isinstance(body_sha256, str)
        and len(body_sha256) == 64
        and all(c in "0123456789abcdef" for c in body_sha256),
        "historical_body_required",
    )
    with session.no_autoflush:
        current = await session.get(ChapterModel, chapter_id, populate_existing=True)
        _need(
            current is not None and current.project_id == project_id,
            "historical_current_chapter_changed",
        )
        proofs = {}
        for owner_id, key, slot in candidates:
            owner = await session.get(WorkflowRunModel, owner_id, populate_existing=True)
            _need(
                owner is not None
                and owner.project_id == project_id
                and owner.scope_type == "chapter",
                "historical_owner_changed",
            )
            journal = ((owner.metadata_json or {}).get("commercial_component_cohorts") or {}).get(
                key
            )
            _need(
                isinstance(journal, dict) and journal.get("schema") == COHORT_SCHEMA,
                "historical_journal_changed",
            )
            binding = journal["binding"]
            _need(
                binding["project_id"] == str(project_id)
                and binding["chapter_id"] == str(owner.scope_id)
                and binding["scope_id"] == scope,
                "historical_binding_changed",
            )
            prior_body = binding["source_binding"]["body_sha256"]
            if prior_body == body_sha256:
                # Same-body requests never gain credit through this purpose
                # path, even if another chapter owns the retained request.
                continue
            _need(
                key
                == _digest(
                    {
                        "project_id": str(project_id),
                        "chapter_id": binding["chapter_id"],
                        "body_sha256": prior_body,
                    }
                ),
                "historical_cohort_changed",
            )
            chapter = await session.get(ChapterModel, owner.scope_id, populate_existing=True)
            _need(
                chapter is not None and chapter.project_id == project_id,
                "historical_chapter_changed",
            )
            pointer = ((chapter.metadata_json or {}).get("commercial_component_cohorts") or {}).get(
                key
            )
            _need(
                isinstance(pointer, Mapping)
                and pointer.get("workflow_run_id") == str(owner.id)
                and pointer.get("binding_sha256") == _digest(binding),
                "historical_pointer_changed",
            )
            _need(
                (journal["slots"].get(slot) or {}).get("state") == "quarantined",
                "historical_slot_changed",
            )
            verified = await project_quarantined_slot(
                session,
                journal=journal,
                key=key,
                owner_id=owner.id,
                slot=slot,
            )
            _need(verified is not None, "historical_proof_required")
            report, entry = verified
            proof = entry["quarantine_history"][-1]["proof"]
            root_key = proof["root"]["key"]
            _need(root_key not in proofs, "historical_root_ambiguous")
            _need(
                json.loads(report.raw_excerpt)["proof_sha256"] == _digest(proof),
                "historical_proof_changed",
            )
            proofs[root_key] = {
                "schema": "verified-historical-quarantine-purpose.v1",
                "project_id": str(project_id),
                "scope_id": scope,
                "chapter_id": binding["chapter_id"],
                "draft_id": binding["draft_id"],
                "body_sha256": prior_body,
                "cohort_key": key,
                "owner_id": str(owner.id),
                "sample_slot": slot,
                "round": proof["round"],
                "proof_sha256": _digest(proof),
                "root_sha256": _digest(proof["root"]),
                "attempts_sha256": _digest(proof["attempts"]),
                "credit_released": False,
                "operation_replay_allowed": False,
                "grade_reused": False,
            }
        return proofs
