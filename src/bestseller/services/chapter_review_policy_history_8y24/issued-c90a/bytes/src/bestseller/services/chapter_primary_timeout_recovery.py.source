"""One explicitly authorized primary timeout replacement; never dispatch or commit.

The operator supplies the receipt below. prepare is read-only; apply stores it
inside the original critic cycle. The gateway must call guard after resolving
its effective role, hash and semantic operation ID, before any paid/cache path.
All digests except wire input hashes use prompt_source_policy.content_hash.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
from uuid import UUID

from sqlalchemy import select

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services.budget import (
    ExecutionBudgetError,
    completion_authorization,
    current_execution_scope,
    execution_operation_id,
    execution_scope_block_code,
)
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = "chapter-primary-timeout-successor.v1"
AUTHORIZATION_FIELD = "primary_timeout_successor_authorization"
_FIELDS = set("schema authorization_ref actor project_id chapter_id draft_id body_sha256 cycle_id "
              "operation_namespace review_workflow_run_id semantic_dependencies_sha256 policy_source_hash "
              "scope_id provider_authorization_sha256 primary_operation_key primary_attempt_id "
              "primary_input_hash primary_prompt_version primary_sample_slot completed_operations".split())


def _need(value: object, code: str) -> None:
    if not value:
        raise ExecutionBudgetError("chapter_primary_timeout:" + code)


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _body(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode()).hexdigest()


def _key(scope: str, operation_id: str, slot: str | int) -> str:
    return hashlib.sha256(f"{scope}\n{operation_id}:sample:{slot}".encode()).hexdigest()


def _identity(receipt: dict) -> tuple[str, str, str]:
    original = execution_operation_id(receipt["operation_namespace"],
        subject=f"critic:chapter_review:{receipt['primary_prompt_version']}",
        revision=receipt["primary_input_hash"])
    successor = execution_operation_id(SCHEMA, subject=receipt["primary_operation_key"], revision="1")
    return original, successor, _key(receipt["scope_id"], successor, receipt["primary_sample_slot"])


async def _rows(session, model, *where) -> list[dict]:
    # Column reads bypass the identity map and do not refresh/mutate caller objects.
    columns = [getattr(model, field.key).label(field.key) for field in model.__mapper__.column_attrs]
    with session.no_autoflush:
        return [dict(row) for row in (await session.execute(select(*columns).where(*where))).mappings()]


def _shape(receipt: Mapping) -> dict:
    _need(isinstance(receipt, dict) and set(receipt) == _FIELDS, "receipt_shape")
    r = deepcopy(receipt)
    _need(r["schema"] == SCHEMA, "receipt_schema")
    for k in _FIELDS - {"completed_operations", "primary_sample_slot"}:
        _need(isinstance(r[k], str) and bool(r[k].strip()) and len(r[k]) <= 256, "receipt_field")
    for k in ("body_sha256", "cycle_id", "semantic_dependencies_sha256", "policy_source_hash",
              "provider_authorization_sha256", "primary_operation_key", "primary_input_hash"):
        _need(_sha(r[k]), "receipt_hash")
    for k in ("project_id", "chapter_id", "draft_id", "review_workflow_run_id"):
        try:
            _need(str(UUID(r[k])) == r[k], "receipt_uuid")
        except (ValueError, TypeError, AttributeError):
            _need(False, "receipt_uuid")
    _need(type(r["primary_sample_slot"]) in (str, int)
          and bool(str(r["primary_sample_slot"])) and len(str(r["primary_sample_slot"])) <= 128,
          "sample_slot")
    completed = r["completed_operations"]
    _need(isinstance(completed, list) and len(completed) == 4, "four_completed_required")
    for item in completed:
        _need(isinstance(item, dict) and set(item) == {"operation_key", "input_hash", "result_sha256"}
              and all(_sha(v) for v in item.values()), "completed_shape")
    _need(len({i["operation_key"] for i in completed} | {r["primary_operation_key"]}) == 5,
          "distinct_roots")
    original, _, successor_key = _identity(r)
    _need(_key(r["scope_id"], original, r["primary_sample_slot"]) == r["primary_operation_key"]
          and successor_key not in {i["operation_key"] for i in completed}, "original_identity")
    return r


async def _validate(session, *, chapter_id, receipt: dict) -> tuple[dict, dict]:
    r = _shape(receipt)
    _need(str(chapter_id) == r["chapter_id"], "chapter_binding")
    chapters = await _rows(session, ChapterModel, ChapterModel.id == UUID(r["chapter_id"]))
    _need(len(chapters) == 1, "chapter_missing")
    chapter = chapters[0]
    _need(str(chapter["project_id"]) == r["project_id"], "project_binding")
    cm = chapter["metadata_json"] or {}
    cycle = (cm.get("chapter_critic_cycles") or {}).get(r["cycle_id"])
    _need(isinstance(cycle, dict) and cycle.get("schema_version") == "chapter-critic-cycle.v1",
          "cycle_missing")
    _need(all(cycle.get(k) == r[v] for k, v in (
        ("cycle_id", "cycle_id"), ("source_draft_id", "draft_id"),
        ("source_body_hash", "body_sha256"), ("policy_source_hash", "policy_source_hash"),
        ("semantic_dependencies_sha256", "semantic_dependencies_sha256"),
        ("review_workflow_run_id", "review_workflow_run_id")))
        and not cycle.get("semantic_dependency_change_detected"), "cycle_binding")
    active = cm.get("chapter_review_active_cycle")
    canonical = hashlib.sha256(f"chapter-critic-cycle.v1\n{r['project_id']}\n{r['chapter_id']}\n{r['body_sha256']}\ninitial".encode()).hexdigest()
    _need((not active and canonical == r["cycle_id"])
          or (isinstance(active, dict) and active.get("cycle_id") == r["cycle_id"]), "active_cycle")
    attempts = cycle.get("attempts")
    _need(isinstance(attempts, list) and 1 <= len(attempts) <= 2, "cycle_attempts")
    for number, entry in enumerate(attempts, 1):
        _need(isinstance(entry, dict) and type(entry.get("attempt")) is int
              and entry["attempt"] == number
              and entry.get("operation_namespace") == f"chapter-critic:{r['cycle_id']}:{number}",
              "cycle_namespace")
    _need(attempts[-1]["operation_namespace"] == r["operation_namespace"]
          and attempts[-1].get("state") in {"running", "completed"}
          and cycle.get("state") in {"running", "completed"}, "original_round")
    existing = cycle.get(AUTHORIZATION_FIELD)
    _need(existing is None or existing == r, "authorization_conflict")
    drafts = await _rows(session, ChapterDraftVersionModel,
        ChapterDraftVersionModel.chapter_id == chapter["id"], ChapterDraftVersionModel.is_current.is_(True))
    _need(len(drafts) == 1 and str(drafts[0]["id"]) == r["draft_id"]
          and str(drafts[0]["project_id"]) == r["project_id"]
          and bool(drafts[0]["content_md"].strip()) and _body(drafts[0]["content_md"]) == r["body_sha256"]
          and drafts[0]["promoted_at"] is None and drafts[0]["promotion_state"] != "promoted", "current_body")
    projects = await _rows(session, ProjectModel, ProjectModel.id == chapter["project_id"])
    _need(len(projects) == 1 and (projects[0]["metadata_json"] or {}).get("execution_scope_id") == r["scope_id"],
          "original_scope")
    owners = await _rows(session, WorkflowRunModel, WorkflowRunModel.project_id == chapter["project_id"],
                        WorkflowRunModel.scope_id == chapter["id"], WorkflowRunModel.scope_type == "chapter")
    owner = next((w for w in owners if str(w["id"]) == r["review_workflow_run_id"]), None)
    wrappers = []
    for w in owners:
        if str(w["id"]) == r["review_workflow_run_id"] or w["status"] not in {"pending", "queued", "running"}:
            continue
        wm = w["metadata_json"] or {}
        _need(w["workflow_type"] == "chapter_pipeline" and wm.get("critic_cycle_id") == r["cycle_id"]
              and wm.get("execution_scope_id") == r["scope_id"]
              and wm.get("review_recovery_source") == {"draft_id": r["draft_id"], "body_hash": r["body_sha256"]},
              "foreign_owner")
        wrappers.append(w)
    _need(owner is not None and owner["workflow_type"] == "chapter_pipeline" and len(wrappers) <= 1,
          "foreign_owner")
    budgets = await _rows(session, ExecutionBudgetModel, ExecutionBudgetModel.scope_id == r["scope_id"])
    _need(len(budgets) == 1, "budget_missing")
    budget = ExecutionBudgetModel(**budgets[0])
    authorization = completion_authorization(budget)
    _need(budget.status == "active" and authorization is not None and authorization.get("status") == "active"
          and execution_scope_block_code(budget) is None
          and content_hash(authorization) == r["provider_authorization_sha256"], "active_original_authorization")
    operations = await _rows(session, ExecutionOperationModel, ExecutionOperationModel.scope_id == r["scope_id"])
    physical = await _rows(session, ExecutionAttemptModel, ExecutionAttemptModel.scope_id == r["scope_id"])
    _need(not any(o["status"] in {"pending", "unknown", "running", "reserved"} for o in operations)
          and not any(a["status"] in {"reserved", "unknown", "pending", "running"} for a in physical),
          "unresolved_or_owned")
    by_key = {o["key"]: o for o in operations}
    root = by_key.get(r["primary_operation_key"])
    old_attempts = [a for a in physical if a["operation_key"] == r["primary_operation_key"]]
    _need(root is not None and root["status"] == "stopped" and root["result_json"] is None
          and root["input_hash"] == r["primary_input_hash"] and len(old_attempts) == 1, "stopped_primary")
    old = old_attempts[0]
    _need(old["id"] == r["primary_attempt_id"] and old["status"] == "reconciled_charged"
          and old["input_hash"] == r["primary_input_hash"] and old["reserved_tokens"] > 0
          and old["actual_tokens"] is None, "unique_charged_timeout")
    transport_key = hashlib.sha256(f"transport-failure.v1\n{r['scope_id']}\n{old['id']}".encode()).hexdigest()
    transport = by_key.get(transport_key)
    _need(transport is not None and transport["status"] == "transport_failure_receipt"
          and transport["input_hash"] == r["primary_input_hash"], "durable_timeout")
    t = transport["result_json"] or {}
    _need(all(t.get(k) == v for k, v in {
        "schema": "transport-failure.v1", "attempt_id": old["id"], "operation_key": root["key"],
        "project_id": r["project_id"], "workflow_run_id": r["review_workflow_run_id"],
        "logical_role": "critic", "prompt_template": "chapter_review", "timeout_seconds": 180,
        "physical_sends": 1}.items()) and t.get("stream") is False
        and t.get("operator_interruption") is False and t.get("partial_stream_observed") is False
        and any(e.get("type", e.get("exception_type")) in {"TimeoutError", "ReadTimeout", "APITimeoutError"}
                for e in [t, *(t.get("error_chain") or [])] if isinstance(e, dict))
        and not t.get("native_deadline_policy"), "exact_timeout180_nonstream")
    reconciled = [o for o in operations if o["status"] == "reconciliation_receipt"
                  and ((o["result_json"] or {}).get("receipt") or {}).get("attempt_id") == old["id"]]
    _need(len(reconciled) == 1, "unique_durable_reconciliation")
    record = reconciled[0]["result_json"]
    stop = record.get("receipt") or {}
    reconciliation_id = record.get("reconciliation_id")
    _need(isinstance(reconciliation_id, str) and bool(reconciliation_id)
          and reconciled[0]["key"] == hashlib.sha256(f"reconciliation.v1\n{r['scope_id']}\n{reconciliation_id}".encode()).hexdigest()
          and reconciled[0]["input_hash"] == record.get("request_hash") and _sha(record.get("request_hash"))
          and record.get("evidence_kind") == "dispatcher_exit" and record.get("local_dispatcher_stopped") is True
          and record.get("remote_operation_closed") is False and bool(record.get("actor"))
          and _sha(record.get("evidence_sha256"))
          and all(stop.get(k) == v for k, v in {"scope_id": r["scope_id"],
              "reconciliation_id": reconciliation_id, "decision": "quarantine_charged", "remaining_unknown": 0,
              "remote_outcome": "unknown_quarantined"}.items())
          and all(stop.get(k) is False for k in ("credit_released", "automatic_resume", "operation_replay_allowed"))
          and type(stop.get("calls_used")) is int and 0 < stop["calls_used"] <= budget.calls_used
          and type(stop.get("tokens_charged")) is int
          and old["reserved_tokens"] <= stop["tokens_charged"] <= budget.tokens_charged,
          "full_charge_reconciliation")
    cache_templates = []
    for item in r["completed_operations"]:
        cached = by_key.get(item["operation_key"])
        _need(cached is not None and cached["status"] == "completed"
              and cached["input_hash"] == item["input_hash"] and isinstance(cached["result_json"], dict)
              and content_hash(cached["result_json"]) == item["result_sha256"], "completed_cache_changed")
        result = cached["result_json"]
        meta = result.get("metadata") or {}
        origin = meta.get("paid_call_origin") or {}
        _need(result.get("provider") not in {None, "mock", "fallback"} and not result.get("fallback_used")
              and origin.get("project_id") == r["project_id"]
              and origin.get("workflow_run_id") == r["review_workflow_run_id"]
              and origin.get("logical_role") == "critic"
              and origin.get("prompt_hash") == item["input_hash"]
              and (meta.get("effective_input_receipt") or {}).get("hash") == item["input_hash"],
              "completed_cache_origin")
        cache_templates.append(origin.get("prompt_template"))
    _need(cache_templates.count("chapter_commercial_quality_judge") == 3
          and cache_templates.count("chapter_window_quality_judge") == 1, "completed_cache_inventory")
    original, successor, successor_key = _identity(r)
    replacement = by_key.get(successor_key)
    _need(replacement is None or (replacement["status"] == "completed"
          and replacement["input_hash"] == r["primary_input_hash"] and isinstance(replacement["result_json"], dict)),
          "successor_already_failed_or_owned")
    _need(replacement is not None or not any(a["operation_key"] == successor_key for a in physical),
          "orphan_successor_attempt")
    _need(replacement is not None or (cycle["state"] == "running" and attempts[-1]["state"] == "running"),
          "completed_cycle_cannot_send")
    runs = await _rows(session, LlmRunModel, LlmRunModel.project_id == chapter["project_id"],
                       LlmRunModel.prompt_template == "chapter_review")
    raw_body = hashlib.sha256(drafts[0]["content_md"].encode()).hexdigest()
    for run in runs:
        meta = run["metadata_json"] or {}
        run_key = (meta.get("execution_receipt") or {}).get("operation_key")
        relevant = run["prompt_hash"] == r["primary_input_hash"] or meta.get("primary_review_body_sha256") == raw_body
        _need(not relevant or (replacement is not None and run_key == successor_key), "completed_primary_available")
    proof = {"schema": SCHEMA, "authorized": True, "cycle_id": r["cycle_id"],
        "operation_namespace": r["operation_namespace"], "primary_operation_key": root["key"],
        "successor_operation_id": successor, "successor_operation_key": successor_key,
        "receipt_sha256": content_hash(r), "budget_usage": {
            "calls_used": budget.calls_used, "tokens_charged": budget.tokens_charged},
        "original_operation_id": original, "primary_model": old["model_name"],
        "attempt_reset_allowed": False, "credit_released": False}
    return proof, chapter


async def prepare_authorization_receipt(session, *, chapter_id, receipt: dict) -> dict:
    """Validate an operator-authored proposed receipt, with SELECTs only."""
    with session.no_autoflush:
        proof, _ = await _validate(session, chapter_id=chapter_id, receipt=receipt)
    return proof


async def apply_authorization_receipt(session, *, chapter_id, receipt: dict) -> dict:
    """Revalidate and store in the SAME cycle; caller owns locking and commit."""
    chapter_id = UUID(str(chapter_id))
    # Serialize competing operator installs, then re-read every source.
    with session.no_autoflush:
        await session.execute(select(ChapterModel.id).where(ChapterModel.id == chapter_id).with_for_update())
        proof, stored = await _validate(session, chapter_id=chapter_id, receipt=receipt)
        chapter = await session.get(ChapterModel, chapter_id)
    _need(chapter not in session.dirty, "uncommitted_chapter")
    metadata = deepcopy(stored["metadata_json"])
    metadata["chapter_critic_cycles"][receipt["cycle_id"]][AUTHORIZATION_FIELD] = deepcopy(receipt)
    chapter.metadata_json = metadata
    return proof


async def guard_prepared_request(session, settings, request, prompt_hash, semantic_namespace,
                                 *, role_settings=None, effective_payload=None, scope_id=None):
    """No authorization => identical request. Authorized cycle => four caches or one root.

    Pass final role_settings/effective_payload from complete_text for the primary.
    scope_id defaults to the ambient execution scope; it must be the original scope.
    This method only reads rows and copies operation_id, never claims or sends.
    """
    namespace = semantic_namespace[0] if semantic_namespace else None
    if (request.project_id is None or request.logical_role != "critic"
        or not isinstance(namespace, str)
        or not namespace.startswith(("chapter-critic:", "commercial-component:"))):
        return request
    candidates = []
    for chapter in await _rows(session, ChapterModel, ChapterModel.project_id == request.project_id):
        for cycle in ((chapter["metadata_json"] or {}).get("chapter_critic_cycles") or {}).values():
            if not isinstance(cycle, dict) or AUTHORIZATION_FIELD not in cycle:
                continue
            outer = f"chapter-critic:{cycle.get('cycle_id')}:{len(cycle.get('attempts') or [])}"
            component = hashlib.sha256(json.dumps({"project_id": str(request.project_id),
                "chapter_id": str(chapter["id"]), "body_sha256": cycle.get("source_body_hash")},
                sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            component_call = namespace.startswith("commercial-component:") and (
                namespace.startswith(f"commercial-component:{component}:")
                or (request.prompt_template == "chapter_commercial_quality_judge"
                    and request.metadata.get("chapter_number") == chapter["chapter_number"]
                    and str(request.workflow_run_id) == cycle.get("review_workflow_run_id")))
            if namespace == outer or component_call:
                candidates.append((chapter, cycle[AUTHORIZATION_FIELD]))
    if not candidates:
        return request
    _need(len(candidates) == 1, "ambiguous_authorization")
    chapter, receipt = candidates[0]
    proof = await prepare_authorization_receipt(session, chapter_id=chapter["id"], receipt=receipt)
    _need((scope_id or current_execution_scope()) == receipt["scope_id"], "request_scope")
    _need(str(request.workflow_run_id) == receipt["review_workflow_run_id"], "request_owner")
    _need(_sha(prompt_hash) and request.operation_id is not None and request.sample_slot is not None,
          "prepared_identity_required")
    key = _key(receipt["scope_id"], request.operation_id, request.sample_slot)
    if key in {i["operation_key"] for i in receipt["completed_operations"]}:
        item = next(i for i in receipt["completed_operations"] if i["operation_key"] == key)
        _need(item["input_hash"] == prompt_hash, "cache_input_changed")
        return request
    _need(namespace == receipt["operation_namespace"] and request.logical_role == "critic"
          and request.prompt_template == "chapter_review" and request.prompt_version == receipt["primary_prompt_version"]
          and type(request.sample_slot) is type(receipt["primary_sample_slot"])
          and request.sample_slot == receipt["primary_sample_slot"] and prompt_hash == receipt["primary_input_hash"]
          and key in {receipt["primary_operation_key"], proof["successor_operation_key"]}, "unlisted_request")
    _need(role_settings is not None and effective_payload is not None, "final_role_and_payload_required")
    from bestseller.services.llm import _effective_input_hash, _effective_input_payload
    _need(role_settings.timeout_seconds == 180 and role_settings.model == proof["primary_model"]
          and effective_payload == _effective_input_payload(request, role_settings)
          and _effective_input_hash(request, role_settings) == prompt_hash
          and effective_payload.get("stream") is False
          and not request.metadata.get("native_deadline_policy"), "effective_primary_changed")
    drafts = await _rows(session, ChapterDraftVersionModel, ChapterDraftVersionModel.id == UUID(receipt["draft_id"]))
    _need(request.metadata.get("primary_review_body_sha256") == hashlib.sha256(drafts[0]["content_md"].encode()).hexdigest(),
          "request_body")
    return request.model_copy(update={"operation_id": proof["successor_operation_id"]})
