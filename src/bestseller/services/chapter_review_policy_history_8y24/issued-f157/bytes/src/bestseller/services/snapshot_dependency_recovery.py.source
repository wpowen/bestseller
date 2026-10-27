"""Derive legacy snapshot dependencies from retained, already paid physical evidence.

This service never sends, grades, adopts, settles an attempt, or grants credit.
The caller owns the transaction and must explicitly commit a successful execution.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    WorkflowRunModel,
)
from bestseller.services import continuity
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.feedback import validate_adopted_knowledge_source
from bestseller.settings import AppSettings, LLMRoleSettings

SCHEMA = "retained-snapshot-dependencies.v1"
_Row = TypeVar("_Row")
_RECEIPT = "snapshot_dependency_recovery"
_IMMUTABLE = (
    "raw_extraction", "extraction_model", "extraction_status", "time_anchor", "chapter_time_span",
)


class SnapshotDependencyRecoveryError(ValueError):
    """A safe rejection category; never includes retained text or credentials."""


def _require(condition: object, code: str) -> None:
    if not condition:
        raise SnapshotDependencyRecoveryError(code)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(value: object) -> str:
    return continuity._snapshot_digest(value)


def _projection(row: ChapterStateSnapshotModel) -> dict[str, Any]:
    return {
        "snapshot_id": str(row.id), "project_id": str(row.project_id),
        "chapter_id": str(row.chapter_id), "chapter_number": row.chapter_number,
        "facts": deepcopy(row.facts), **{key: getattr(row, key) for key in _IMMUTABLE},
    }


async def _read(session: AsyncSession, statement: Select[tuple[_Row]]) -> _Row | None:
    return await session.scalar(
        statement.execution_options(populate_existing=True, autoflush=False),
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "retained_json_duplicate_key")
        result[key] = value
    return result


def _payload(root: Path, reference: object, kind: str) -> tuple[dict[str, Any], str]:
    _require(isinstance(reference, str) and bool(reference), "retained_reference_missing")
    try:
        path = (root / reference).resolve(strict=True)
        _require(path.is_relative_to(root) and path.is_file(), "retained_reference_outside_store")
        raw = path.read_bytes()
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SnapshotDependencyRecoveryError("retained_file_unavailable") from None
    _require(isinstance(value, dict), "retained_payload_invalid")
    _require(value.get("schema_version") == "llm-payload.v1" and value.get("kind") == kind,
             "retained_payload_schema_mismatch")
    _require(value.get("replayable") is True and value.get("replayability_reasons") == [],
             "retained_payload_not_replayable")
    return value, hashlib.sha256(raw).hexdigest()


def _text(value: object) -> str:
    _require(isinstance(value, dict) and isinstance(value.get("text"), str),
             "retained_text_missing")
    text = value["text"]
    _require(value.get("truncated") is False and type(value.get("original_chars")) is int
             and value["original_chars"] == len(text), "retained_text_incomplete")
    return text


def _parameters(request: dict[str, Any]) -> str:
    """Read original parameters only; current role configuration supplies no defaults."""
    effective, requested = request.get("effective"), request.get("requested")
    _require(isinstance(effective, dict) and isinstance(requested, dict),
             "retained_parameters_missing")
    _require({"configured_model", "model", "provider", "temperature", "max_tokens",
              "timeout_seconds", "stream", "thinking_type", "reasoning_effort"} <= effective.keys()
             and {"model_catalog_key", "model_tier", "temperature_override", "max_tokens_override",
                  "cache_system"} <= requested.keys(), "retained_parameters_incomplete")
    _require(all(isinstance(effective[k], str) and effective[k]
                 for k in ("configured_model", "model", "provider")), "retained_parameters_invalid")
    _require(type(effective["max_tokens"]) is int and effective["max_tokens"] > 0
             and type(effective["stream"]) is bool, "retained_parameters_invalid")
    for key in ("temperature", "timeout_seconds"):
        _require(type(effective[key]) in (int, float) and math.isfinite(effective[key]),
                 "retained_parameters_invalid")
    return _digest({"requested": requested, "effective": effective})


def _bound_effective_input(settings: AppSettings, payload: dict[str, Any], run: LlmRunModel,
                           paid: ExecutionOperationModel) -> dict[str, Any]:
    """Candidate routes are witnesses only after reproducing the original hash.

    All model input parameters come from the retained request. Current config
    contributes endpoint candidates, never a default temperature/cap/protocol.
    The model's exact effective-input.v1 digest must match the paid operation,
    settled attempt and telemetry; no request is dispatched or claimed.
    """
    from bestseller.services import llm

    for metadata in (run.metadata_json or {}, (paid.result_json or {}).get('metadata') or {}):
        receipt = metadata.get('effective_input_receipt') or {}
        _require(receipt.get('schema') == 'effective-input.v1'
                 and receipt.get('hash') == run.prompt_hash
                 and receipt.get('model') == run.model_name, 'effective_input_receipt_mismatch')
    effective, requested = payload['effective'], payload['requested']
    _require(type(requested['cache_system']) is bool, 'retained_requested_parameters_invalid')
    for key in ('temperature_override', 'max_tokens_override'):
        value = requested[key]
        _require(value is None or (type(value) in (int, float) and math.isfinite(value)),
                 'retained_requested_parameters_invalid')
    _require(requested['max_tokens_override'] is None
             or (type(requested['max_tokens_override']) is int and requested['max_tokens_override'] > 0),
             'retained_requested_parameters_invalid')
    messages = payload['messages']
    request = llm.LLMCompletionRequest(
        logical_role='editor', system_prompt=_text(messages['system']),
        user_prompt=_text(messages['user']), fallback_response='{}',
        project_id=run.project_id, workflow_run_id=run.workflow_run_id,
        prompt_template=run.prompt_template, prompt_version=run.prompt_version,
        model_catalog_key=requested['model_catalog_key'], model_tier=requested['model_tier'],
        temperature_override=requested['temperature_override'],
        max_tokens_override=requested['max_tokens_override'], cache_system=requested['cache_system'],
        tools=requested.get('tools'), tool_choice=requested.get('tool_choice'),
    )
    endpoints = {None}
    config = getattr(settings, 'llm', None)
    for name in ('planner', 'writer', 'critic', 'editor', 'summarizer'):
        role = getattr(config, name, None)
        value = getattr(role, 'api_base', None)
        if value is None or isinstance(value, str):
            endpoints.add(value)
    matches = set()
    for endpoint in endpoints:
        role = LLMRoleSettings(
            model=run.model_name, api_base=endpoint, temperature=effective['temperature'],
            max_tokens=effective['max_tokens'], timeout_seconds=effective['timeout_seconds'],
            stream=effective['stream'], thinking_type=effective['thinking_type'],
            reasoning_effort=effective['reasoning_effort'], n_candidates=1,
        )
        actual = llm._effective_input_payload(request, role)
        # Requested overrides must not mask a contradictory effective field.
        if any(_digest(actual[key]) != _digest(effective[key]) for key in (
            'model', 'temperature', 'max_tokens', 'stream', 'thinking_type', 'reasoning_effort',
        )):
            continue
        if llm._effective_input_hash(request, role) == run.prompt_hash:
            matches.add(_sha(endpoint or ''))
    _require(len(matches) == 1, 'retained_effective_input_unprovable')
    return {'schema':'retained-exact-effective-input.v1', 'hash':run.prompt_hash,
            'endpoint_sha256':next(iter(matches)),
            'archived_effective_fields_verified':['model','temperature','max_tokens','stream',
                                                  'thinking_type','reasoning_effort'],
            'transport_only_fields_not_in_model_input':['timeout_seconds'],
            'dispatch_allowed':False, 'current_parameter_defaults_used':False}


async def _source(
    session: AsyncSession, chapter: ChapterModel, old: dict[str, Any],
) -> tuple[ChapterDraftVersionModel, dict[str, Any]]:
    contract = old["facts"].get("snapshot_contract")
    _require(isinstance(contract, dict) and contract.get("is_usable") is True,
             "snapshot_contract_unusable")
    adopted, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    _require(adopted is not None, "adopted_source_missing")
    lineage, rejection = await validate_adopted_knowledge_source(
        session, project_id=chapter.project_id, chapter_id=chapter.id,
        source_chapter_draft_version_id=adopted.id, chapter_md=adopted.content_md, lock=False,
    )
    _require(not rejection and continuity._snapshot_source_matches(contract, lineage),
             "snapshot_source_mismatch")
    _require(old["project_id"] == str(chapter.project_id)
             and old["chapter_id"] == str(chapter.id)
             and old["chapter_number"] == chapter.chapter_number
             and old["extraction_status"] == "ok_" + str(lineage.get("adoption_state")),
             "snapshot_owner_or_status_mismatch")
    return adopted, lineage


async def _proof(
    session: AsyncSession, settings: AppSettings, chapter: ChapterModel, old: dict[str, Any],
) -> dict[str, Any]:
    adopted, _ = await _source(session, chapter, old)
    previous = await continuity._current_snapshot_input(
        session, project_id=chapter.project_id, chapter_number=chapter.chapter_number - 1,
        include_source=True,
    )
    _require(chapter.chapter_number > 1 and previous.source is not None
             and previous.context is not None, "predecessor_snapshot_not_current")
    # This operator handles missing legacy dependencies only. Reconstruct the
    # paid input from the retained literal predecessor, while independently
    # requiring its current admitted view above. A new policy projection must
    # never be attributed to an old physical request.
    _require(previous.literal_context is not None, "predecessor_literal_input_missing")
    literal_previous = continuity._SnapshotInput(
        previous.chapter_id, previous.source, previous.literal_context,
    )
    dependencies = continuity._snapshot_dependencies(chapter.chapter_number, literal_previous)
    contract = old["facts"]["snapshot_contract"]
    operations = contract.get("llm_operations")
    _require(isinstance(operations, list) and len(operations) == 1
             and isinstance(operations[0], dict), "original_operation_ambiguous")
    operation = operations[0]
    for key in ("llm_run_id", "operation_id", "workflow_run_id", "prompt_template",
                "prompt_version", "prompt_sha256", "model", "provider", "fallback_used"):
        _require(key in contract and key in operation and contract[key] == operation[key],
                 "original_contract_receipt_mismatch")
    try:
        run_id = UUID(operation["llm_run_id"])
    except (ValueError, TypeError, AttributeError):
        raise SnapshotDependencyRecoveryError("original_run_missing") from None
    run = await _read(session, select(LlmRunModel).where(LlmRunModel.id == run_id))
    _require(run is not None and run.project_id == chapter.project_id
             and run.workflow_run_id is not None and run.logical_role == "editor"
             and run.prompt_template == "chapter_state_snapshot" and run.prompt_version == "2.0"
             and run.finish_reason == "stop", "original_run_identity_mismatch")
    workflow = await _read(session, select(WorkflowRunModel).where(
        WorkflowRunModel.id == run.workflow_run_id,
    ))
    _require(workflow is not None and workflow.project_id == chapter.project_id
             and operation["workflow_run_id"] == str(workflow.id), "original_workflow_mismatch")
    _require(operation["prompt_template"] == run.prompt_template
             and operation["prompt_version"] == run.prompt_version
             and operation["model"] == run.model_name == old["extraction_model"]
             and operation["provider"] == run.provider and operation["fallback_used"] is False,
             "original_run_receipt_mismatch")
    _require(settings.artifact_store.mode == "local", "retained_store_not_local")
    try:
        root = Path(settings.artifact_store.local_dir).expanduser().resolve(strict=True)
    except OSError:
        raise SnapshotDependencyRecoveryError("retained_store_unavailable") from None
    request, request_sha = _payload(root, run.request_payload_ref, "request")
    response, response_sha = _payload(root, run.response_payload_ref, "response")
    for payload in (request, response):
        _require(payload.get("llm_run_id") == str(run.id)
                 and payload.get("project_id") == str(chapter.project_id)
                 and payload.get("workflow_run_id") == str(workflow.id)
                 and payload.get("logical_role") == run.logical_role, "physical_owner_mismatch")
    _require(request.get("prompt_template") == run.prompt_template
             and request.get("prompt_version") == run.prompt_version, "physical_purpose_mismatch")
    messages = request.get("messages")
    _require(isinstance(messages, dict) and messages.get("source") == "system_user"
             and messages.get("replayable") is True, "physical_messages_unavailable")
    system, user = _text(messages.get("system")), _text(messages.get("user"))
    _require(_sha(system + "\n" + user) == contract["prompt_sha256"],
             "original_prompt_hash_mismatch")
    _require(system == continuity._EXTRACTION_SYSTEM_PROMPT
             and user == continuity._build_extraction_user_prompt(
                 chapter=chapter, chapter_md=adopted.content_md,
                 previous_snapshot=previous.literal_context,
             ), "physical_input_changed")
    raw = _text(response.get("content"))
    _require(isinstance(old["raw_extraction"], str) and raw == old["raw_extraction"],
             "physical_response_changed")
    parameters_sha = _parameters(request)
    _require(request["effective"]["model"] == response.get("model") == run.model_name
             and request["effective"]["provider"] == response.get("provider") == run.provider
             and response.get("fallback_used") is False and response.get("finish_reason") == "stop"
             and response.get("tool_call_count") == 0, "physical_result_mismatch")
    _require(all(type(getattr(run, k)) is int and getattr(run, k) >= 0
                 and type(response.get(k)) is int and response[k] == getattr(run, k)
                 for k in ("input_tokens", "output_tokens")), "physical_usage_mismatch")
    execution = (run.metadata_json or {}).get("execution_receipt")
    _require(isinstance(execution, dict) and execution.get("policy") == "execution-budget.v1",
             "paid_receipt_missing")
    scope, key = execution.get("scope_id"), execution.get("operation_key")
    _require(isinstance(scope, str) and bool(scope) and isinstance(operation["operation_id"], str)
             and key == _sha(scope + "\n" + operation["operation_id"]),
             "paid_operation_key_mismatch")
    paid = await _read(session, select(ExecutionOperationModel).where(
        ExecutionOperationModel.key == key,
    ))
    _require(paid is not None and paid.scope_id == scope and paid.status == "completed"
             and isinstance(paid.result_json, dict), "paid_operation_not_completed")
    _require(paid.input_hash == run.prompt_hash, "paid_input_hash_mismatch")
    result = paid.result_json
    _require(result.get("content") == raw and result.get("model_name") == run.model_name
             and result.get("provider") == run.provider and result.get("fallback_used") is False
             and result.get("finish_reason") == run.finish_reason
             and all(type(result.get(k)) is int and result[k] == getattr(run, k)
                     for k in ("input_tokens", "output_tokens")), "paid_result_mismatch")
    _require(result.get("tool_calls") is None or result.get("tool_calls") == [],
             "paid_snapshot_tool_calls_present")
    original_raw = result.get("raw_message")
    if original_raw is not None:
        _require(isinstance(original_raw, dict)
                 and original_raw.get("tool_calls") in (None, [])
                 and original_raw.get("function_call") is None,
                 "paid_snapshot_raw_tool_conflict")
    structured_fields = [key for key in ("tool_calls", "raw_message") if key in response]
    _require(all(_digest(response[key]) == _digest(result.get(key))
                 for key in structured_fields), "physical_structured_result_mismatch")
    cached = (result.get("metadata") or {}).get("execution_receipt")
    _require(isinstance(cached, dict) and cached.get("operation_key") == key
             and cached.get("scope_id") == scope, "paid_cached_receipt_mismatch")
    effective_binding = _bound_effective_input(settings, request, run, paid)
    attempts = list(await session.scalars(select(ExecutionAttemptModel).where(
        ExecutionAttemptModel.operation_key == key,
    ).order_by(ExecutionAttemptModel.id)
      .execution_options(populate_existing=True, autoflush=False)))
    recorded = execution.get("attempts")
    _require(isinstance(recorded, list) and recorded and all(isinstance(a, dict) for a in recorded)
             and {a.get("attempt_id") for a in recorded} == {a.id for a in attempts}
             and len(recorded) == len(attempts), "paid_attempts_incomplete")
    for attempt in attempts:
        _require(attempt.scope_id == scope and attempt.status in {"completed", "reconciled_charged"}
                 and type(attempt.actual_tokens) is int and attempt.actual_tokens >= 0,
                 "paid_attempt_unsettled")
        retained = next(a for a in recorded if a["attempt_id"] == attempt.id)
        _require(retained.get("model") == attempt.model_name
                 and retained.get("effective_input_hash") == attempt.input_hash,
                 "paid_attempt_identity_mismatch")
    _require(any(a.status == "completed" and a.model_name == run.model_name
                 and a.input_hash == run.prompt_hash
                 and a.actual_tokens == run.input_tokens + run.output_tokens for a in attempts),
             "paid_return_attempt_missing")
    return {
        "old_projection_sha256": _digest(old), "old_contract_sha256": _digest(contract),
        "request_sha256": request_sha, "response_sha256": response_sha,
        "physical_prompt_sha256": contract["prompt_sha256"],
        "dependency_sha256": _digest(dependencies), "snapshot_dependencies": dependencies,
        "input_context_basis": "retained_literal_predecessor",
        "current_predecessor_view_sha256": previous.context.epistemic_view_sha256,
        "current_predecessor_application_sha256": previous.context.epistemic_application_sha256,
        "paid_run_id": str(run.id), "workflow_run_id": str(workflow.id),
        "operation_key": key, "scope_id": scope, "operation_input_hash": paid.input_hash,
        "paid_result_sha256": _digest(result), "parameters_sha256": parameters_sha,
        "paid_run_metadata_sha256": _digest(run.metadata_json),
        "effective_input_binding": effective_binding,
        "physical_structured_fields": structured_fields,
        "physical_structured_fields_missing": [key for key in ("tool_calls", "raw_message")
                                                if key not in response],
        "attempts_sha256": _digest([{
            "id": a.id, "scope_id": a.scope_id, "status": a.status, "model": a.model_name,
            "input_hash": a.input_hash, "actual_tokens": a.actual_tokens,
            "reserved_tokens": a.reserved_tokens,
        } for a in attempts]),
    }


async def reconcile_retained_snapshot_dependencies(
    session: AsyncSession,
    settings: AppSettings,
    *,
    snapshot_id: UUID,
    actor: str,
    reason: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Prove exact original inputs; optionally derive provenance without regrading.

    Dry runs issue only unlocked reads with autoflush disabled. Execution locks
    chapter -> scoped drafts -> snapshot, rechecks the original projection, and
    rolls back its entire savepoint if any final evidence check fails.
    """
    _require(not (session.new or session.dirty or session.deleted), "pending_session_changes")
    _require(dry_run or (isinstance(actor, str) and actor.strip()
                        and isinstance(reason, str) and reason.strip()), "actor_reason_required")
    with session.no_autoflush:
        row = await _read(session, select(ChapterStateSnapshotModel).where(
            ChapterStateSnapshotModel.id == snapshot_id,
        ))
        _require(row is not None and isinstance(row.facts, dict), "snapshot_missing")
        old = _projection(row)
        chapter = await _read(session, select(ChapterModel).where(
            ChapterModel.id == row.chapter_id, ChapterModel.project_id == row.project_id,
        ))
        _require(chapter is not None, "snapshot_chapter_missing")
        _, lineage = await _source(session, chapter, old)
        contract = old["facts"]["snapshot_contract"]
        current = await continuity.snapshot_evidence_current(
            session, project_id=chapter.project_id, chapter_number=chapter.chapter_number,
            source_lineage=lineage,
        )
        base = {"snapshot_id": str(snapshot_id), "provider_calls": 0, "new_grade": 0,
                "new_adoption": 0, "credit_released": 0}
        if current and ("snapshot_dependencies" in contract or chapter.chapter_number == 1):
            return {**base, "status": "already_current"}
        _require("snapshot_dependencies" not in contract and _RECEIPT not in contract,
                 "prepared_snapshot_not_current")
        _require(isinstance(old["facts"].get("snapshot_history", []), list),
                 "snapshot_history_invalid")
        proof = await _proof(session, settings, chapter, old)
        if dry_run:
            return {**base, "status": "proof_only", "proof": proof}
        async with session.begin_nested():
            # The exact previous context can depend on every earlier chapter.
            # Freeze that prefix in a consistent order before locking drafts
            # and snapshots, then rerun the whole physical/source proof.
            prefix = list(await session.scalars(select(ChapterModel).where(
                ChapterModel.project_id == chapter.project_id,
                ChapterModel.chapter_number <= chapter.chapter_number,
            ).order_by(ChapterModel.chapter_number, ChapterModel.id).with_for_update()
              .execution_options(populate_existing=True, autoflush=False)))
            chapter = next((item for item in prefix if item.id == chapter.id), None)
            _require(chapter is not None, 'snapshot_chapter_missing')
            chapter_ids = [item.id for item in prefix]
            await session.scalars(select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.project_id == chapter.project_id,
                ChapterDraftVersionModel.chapter_id.in_(chapter_ids),
            ).order_by(ChapterDraftVersionModel.chapter_id, ChapterDraftVersionModel.id).with_for_update()
              .execution_options(populate_existing=True, autoflush=False))
            await session.scalars(select(ChapterStateSnapshotModel).where(
                ChapterStateSnapshotModel.project_id == chapter.project_id,
                ChapterStateSnapshotModel.chapter_id.in_(chapter_ids),
            ).order_by(ChapterStateSnapshotModel.chapter_number, ChapterStateSnapshotModel.id)
              .with_for_update().execution_options(populate_existing=True, autoflush=False))
            row = await _read(session, select(ChapterStateSnapshotModel).where(
                ChapterStateSnapshotModel.id == snapshot_id,
                ChapterStateSnapshotModel.project_id == chapter.project_id,
                ChapterStateSnapshotModel.chapter_id == chapter.id,
            ).with_for_update())
            _require(row is not None and _digest(_projection(row)) == _digest(old),
                     "snapshot_projection_changed")
            _require(await _proof(session, settings, chapter, old) == proof,
                     "retained_proof_changed")
            receipt = {"schema": SCHEMA, **proof, "actor": actor.strip(), "reason": reason.strip(),
                       "created_at": datetime.now(UTC).isoformat(), **base}
            new_contract = {
                **deepcopy(contract), "snapshot_dependencies": proof["snapshot_dependencies"],
                _RECEIPT: receipt,
            }
            expected_history = [*deepcopy(old["facts"].get("snapshot_history", [])), {
                "facts": {k: deepcopy(v) for k, v in old["facts"].items()
                          if k != "snapshot_history"},
                **{k: old[k] for k in _IMMUTABLE},
            }]
            # Normal producer persistence binds freshly paid inputs to the
            # current view. This explicit recovery already proved the original
            # physical input under prefix locks; append provenance directly so
            # the old extraction is never relabelled as a new-policy call.
            row.facts = {**deepcopy(old["facts"]), "snapshot_contract": new_contract,
                         "snapshot_history": expected_history}
            previous = await continuity._current_snapshot_input(
                session, project_id=chapter.project_id,
                chapter_number=chapter.chapter_number - 1, include_source=True,
            )
            adopted, _ = await _source(session, chapter, old)
            view = continuity.build_snapshot_epistemic_view(
                row, source_body=adopted.content_md, previous=previous,
            )
            continuity._publish_epistemic_view(
                row, view, actor=actor.strip(),
                reason="retained dependency recovery: " + reason.strip(),
                before_sha256=continuity.snapshot_epistemic_witness(row),
            )
            await session.flush()
            _require(await _proof(session, settings, chapter, old) == proof,
                     "retained_proof_changed")
            _require(all(getattr(row, k) == old[k] for k in _IMMUTABLE)
                     and await continuity.snapshot_evidence_current(
                         session, project_id=chapter.project_id,
                         chapter_number=chapter.chapter_number, source_lineage=lineage,
                     ), "derived_snapshot_not_current")
        return {**base, "status": "reconciled", "receipt": receipt}
