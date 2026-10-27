"""Explicit recovery of a durable alignment claim which never reached the gateway.

This does not retry paid, unknown, returned or adopted work. The old claim and
every financial row remain evidence; the original pending task keeps its identity.
"""

from copy import deepcopy
import hashlib
import json
import math
from datetime import datetime
from uuid import UUID

from sqlalchemy import Text, cast, inspect, or_, select

from bestseller.domain.context import ChapterSceneContext
from bestseller.domain.project import SceneStructureRead
from bestseller.infra.db import models as db
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services import rewrite_outline_dispatch as dispatch

SCHEMA = "outline-alignment-unstarted-recovery.v1"
HISTORY = "unstarted_alignment_reconciliations"
SETTLED_ATTEMPTS = {"completed", "rejected", "not_sent", "reconciled_charged"}
SETTLED_ROOTS = {"completed", "stopped"}
BUDGET_RECEIPTS = {"transport_result", "transport_failure_receipt",
                   "reconciliation_receipt", "unsent_reclaim_receipt"}


def _need(condition, code):
    if not condition:
        raise ValueError("alignment_unsent_recovery:" + code)


def _row(row):
    return json.loads(json.dumps({a.key: deepcopy(getattr(row, a.key))
        for a in inspect(type(row)).column_attrs}, default=str))


def _workflow_reference(value, workflow_id):
    if isinstance(value, dict):
        return value.get("workflow_run_id") == workflow_id or any(
            _workflow_reference(child, workflow_id) for child in value.values())
    return isinstance(value, list) and any(
        _workflow_reference(child, workflow_id) for child in value)


def _budget_receipt_valid(op, attempts, operations):
    record = op.result_json
    if not isinstance(record, dict):
        return False
    if op.status == "unsent_reclaim_receipt":
        if type(record.get("operation_key")) is not str:
            return False
        root = operations.get(record.get("operation_key"))
        key = hashlib.sha256(f"unsent-reclaim.v1\n{record.get('operation_key')}\n{op.owner}".encode()).hexdigest()
        return bool(root and root.status in SETTLED_ROOTS and op.key == key
            and op.input_hash == root.input_hash and record.get("credit_released") is False
            and record.get("old_owner") == op.owner
            and isinstance(record.get("new_owner"), str) and record["new_owner"])
    if op.status == "reconciliation_receipt":
        receipt = record.get("receipt") or {}
        if not isinstance(receipt, dict) or type(receipt.get("attempt_id")) is not str:
            return False
        attempt = attempts.get(receipt.get("attempt_id"))
        key = hashlib.sha256(f"reconciliation.v1\n{op.scope_id}\n{record.get('reconciliation_id')}".encode()).hexdigest()
        if not (attempt and attempt.status == "reconciled_charged" and op.key == key
            and op.owner == "manual-reconciliation" and op.input_hash == record.get("request_hash")
            and receipt.get("scope_id") == op.scope_id and receipt.get("credit_released") is False):
            return False
        # The producer has no record.schema field. Validate its exact v1 shape
        # and reconstruct the ORIGINAL typed request/hash, without adding one.
        if set(record) != {"at", "actor", "receipt", "request_hash", "evidence_kind",
            "evidence_sha256", "reconciliation_id", "remote_operation_closed", "local_dispatcher_stopped"}:
            return False
        if (type(record["reconciliation_id"]) is not str
            or type(record["remote_operation_closed"]) is not bool
            or type(record["local_dispatcher_stopped"]) is not bool
            or type(record["at"]) is not str):
            return False
        expected_fields = {"scope_id", "reconciliation_id", "attempt_id", "decision", "status",
            "remaining_unknown", "calls_used", "tokens_charged", "automatic_resume", "credit_released"}
        if receipt.get("decision") == "quarantine_charged":
            expected_fields |= {"remote_outcome", "operation_replay_allowed"}
            if (receipt.get("remote_outcome") != "unknown_quarantined"
                or receipt.get("operation_replay_allowed") is not False):
                return False
        if (set(receipt) != expected_fields or receipt.get("automatic_resume") is not False
            or receipt.get("reconciliation_id") != record["reconciliation_id"]
            or receipt.get("status") not in {"active", "unknown", "exhausted", "cancelled"}
            or any(type(receipt.get(k)) is not int or receipt[k] < 0
                   for k in ("remaining_unknown", "calls_used", "tokens_charged"))
            or not 1 <= receipt["calls_used"] <= len(attempts)
            or receipt["tokens_charged"] < (attempt.actual_tokens if attempt.actual_tokens is not None
                                             else attempt.reserved_tokens)):
            return False
        try:
            from bestseller.services.budget import ExecutionReconciliation

            if datetime.fromisoformat(record["at"].replace("Z", "+00:00")).tzinfo is None:
                return False
            root = operations[attempt.operation_key]
            request = ExecutionReconciliation.model_validate({
                **{k: record[k] for k in ("reconciliation_id", "actor", "evidence_kind",
                    "evidence_sha256", "remote_operation_closed", "local_dispatcher_stopped")},
                "attempt_id": attempt.id, "expected_input_hash": attempt.input_hash,
                "decision": receipt.get("decision"),
                "result": root.result_json if receipt.get("decision") == "recover_completed" else None})
            return content_hash(request.model_dump(mode="json")) == record["request_hash"]
        except (ValueError, TypeError, KeyError):
            return False
    if type(record.get("attempt_id")) is not str:
        return False
    attempt = attempts.get(record.get("attempt_id"))
    if attempt is None or record.get("operation_key") != attempt.operation_key:
        return False
    if op.input_hash != attempt.input_hash or attempt.operation_key not in operations:
        return False
    schema = {"transport_result": "transport-result.v1",
              "transport_failure_receipt": "transport-failure.v1"}.get(op.status)
    key = hashlib.sha256(f"{schema}\n{op.scope_id}\n{attempt.id}".encode()).hexdigest()
    if op.key != key or record.get("schema") != schema:
        return False
    if op.status == "transport_result":
        return (record.get("input_hash") == attempt.input_hash
            and isinstance(record.get("result"), dict)
            and record["result"].get("model_name") == attempt.model_name)
    required = {"schema", "attempt_id", "operation_key", "exception_type", "physical_sends",
                "operator_interruption", "stream", "partial_stream_observed"}
    allowed = required | {"elapsed_ms", "project_id", "error_chain", "http_status", "logical_role",
                          "prompt_template", "timeout_seconds", "workflow_run_id", "native_deadline_policy"}
    if not required <= set(record) or set(record) - allowed:
        return False
    for name in ("project_id", "workflow_run_id"):
        value = record.get(name)
        if value is not None:
            try:
                if type(value) is not str or str(UUID(value)) != value:
                    return False
            except ValueError:
                return False
    if any(record.get(k) is not None and type(record[k]) is not str
           for k in ("logical_role", "prompt_template")):
        return False
    if record.get("error_chain") is not None and (
        not isinstance(record["error_chain"], list)
        or not all(isinstance(item, dict) for item in record["error_chain"])):
        return False
    sends = record.get("physical_sends")
    if (type(sends) is not int or sends not in {0, 1}
        or type(record.get("exception_type")) is not str or not record["exception_type"]
        or any(type(record.get(k)) is not bool for k in (
            "operator_interruption", "stream", "partial_stream_observed"))
        or (record["partial_stream_observed"] and (not record["stream"] or sends != 1))
        or attempt.status == "completed"
        or (attempt.status == "not_sent" and (sends != 0 or attempt.actual_tokens != 0))
        or (sends == 0 and attempt.actual_tokens not in (None, 0))):
        return False
    for name in ("elapsed_ms", "http_status"):
        value = record.get(name)
        if value is not None and (type(value) is not int or value < 0):
            return False
    if record.get("http_status") is not None and not 100 <= record["http_status"] <= 599:
        return False
    timeout = record.get("timeout_seconds")
    if timeout is not None and (type(timeout) not in (int, float)
        or not math.isfinite(timeout) or timeout <= 0):
        return False
    if record.get("native_deadline_policy") is not None:
        try:
            from bestseller.services.planning_native_deadline import validate_native_deadline_policy

            policy = validate_native_deadline_policy(record["native_deadline_policy"])
            if content_hash(policy["sdk_timeout_seconds"]) != content_hash(timeout):
                return False
        except (ValueError, TypeError, KeyError):
            return False
    return True


async def _cancelled_admission_proof(session, root, *, current_scope):
    """Isolate an old cancelled zero-attempt admission, without guessing purpose.

    No credit, ownership, grade or replay permission is derived from this row.
    Unknown charged attempts elsewhere in its cancelled scope remain unknown.
    """
    _need(root.scope_id != current_scope and root.status == "stopped"
        and root.result_json is None, "global_orphan_chargeable_root")
    budget = await session.get(db.ExecutionBudgetModel, root.scope_id, populate_existing=True)
    _need(budget is not None and budget.status == "cancelled", "global_orphan_chargeable_root")
    attempts = list(await session.scalars(select(db.ExecutionAttemptModel).where(
        db.ExecutionAttemptModel.scope_id == root.scope_id).order_by(db.ExecutionAttemptModel.id)))
    operations = list(await session.scalars(select(db.ExecutionOperationModel).where(
        db.ExecutionOperationModel.scope_id == root.scope_id).order_by(db.ExecutionOperationModel.key)))
    by_key = {o.key: o for o in operations}
    _need(not any(a.operation_key == root.key for a in attempts)
        and budget.calls_used == len(attempts)
        and budget.tokens_charged == sum(a.actual_tokens if a.actual_tokens is not None
                                        else a.reserved_tokens for a in attempts)
        and all(a.status in SETTLED_ATTEMPTS | {"unknown"}
            and a.operation_key in by_key
            and by_key[a.operation_key].input_hash == a.input_hash
            and by_key[a.operation_key].status in SETTLED_ROOTS | {"unknown"}
            for a in attempts), "global_orphan_chargeable_root")
    return {"schema": "isolated-cancelled-admission.v1", "scope_id": root.scope_id,
        "operation_key": root.key, "root_sha256": content_hash(_row(root)),
        "financial_inventory_sha256": content_hash({"budget": _row(budget),
            "attempts": [_row(a) for a in attempts], "operations": [_row(o) for o in operations]}),
        "actual_root_attempts": [], "purpose": "unknown_preserved",
        "scope_status": "cancelled", "operation_replay_allowed": False,
        "credit_released": False, "financial_rows_changed": False}


async def reconcile_unstarted_outline_alignment(
    session, settings, *, workflow_run_id: UUID, expected_claim_id: str,
    actor: str, authorization_ref: str, repair_ref: str,
    explicit_user_approval: bool = False, execute: bool = False,
    expected_witness_sha256: str | None = None,
) -> dict:
    """Read-only plan, then an exact CAS-style retirement with zero send credit.

    Any attempt in any scope since this workflow was prepared blocks recovery.
    The conservative interval check also covers unattributed/cross-scope requests.
    The caller owns commit and dispatcher-exit verification, and must not run a
    provider concurrently. No task attempt, budget or execution operation changes.
    """
    _need(explicit_user_approval is True and all(type(v) is str and v.strip()
        for v in (actor, authorization_ref, repair_ref, expected_claim_id)), "authorization")
    _need(not session.new and not session.dirty and not session.deleted, "clean_session_required")
    workflow = await session.get(db.WorkflowRunModel, workflow_run_id, populate_existing=True)
    _need(workflow is not None, "workflow_missing")
    # A repeat reads the old retirement only; it cannot rearm a later claim.
    history = deepcopy((workflow.metadata_json or {}).get(HISTORY) or [])
    for item in history:
        if item.get("retired_claim_id") == expected_claim_id:
            _need(item.get("repair_ref") == repair_ref, "retirement_repair_ref_changed")
            return {"status": "already_reconciled", "receipt": item,
                    "workflow_current_status": workflow.status, "database_writes": 0}
    raw = deepcopy((workflow.metadata_json or {}).get(dispatch._KEY) or {})
    _need(type(raw.get("task_id")) is str, "task_binding_missing")
    task = await session.get(db.RewriteTaskModel, UUID(raw["task_id"]), populate_existing=True)
    _need(task is not None, "task_missing")
    scope = raw.get("original_execution_scope_id")
    project, chapter, draft, marker = await dispatch._context(
        session, project_id=task.project_id, chapter_id=workflow.scope_id,
        marker=raw.get("source_plan_binding"), scope=scope, settings=settings, lock=execute)
    if execute:
        # Match normal dispatch's chapter/draft -> task -> workflow order.
        task = await session.get(db.RewriteTaskModel, task.id, with_for_update=True,
                                 populate_existing=True)
        workflow = await session.get(db.WorkflowRunModel, workflow_run_id,
                                     with_for_update=True, populate_existing=True)
    receipt = dispatch._receipt(workflow, task, chapter_id=chapter.id, scope=scope)
    _need(workflow.status == "machine_blocked" and workflow.current_step
        == "alignment_rewrite_failed_requires_reconciliation", "not_unstarted_failure")
    _need(receipt.get("claim_id") == expected_claim_id and receipt.get("output") is None,
          "claim_or_output_changed")
    from bestseller.services.reviews import _rewrite_task_source_identity

    _need(dispatch._alignment(task) and task.status in dispatch._PENDING
        and task.trigger_source_id == chapter.id
        and int(task.attempts or 0) == 0 and dispatch._parent(task, draft, marker)
        and receipt.get("task_source_identity") == _rewrite_task_source_identity(task),
        "task_or_parent_changed")
    matched_attempt = select(db.ExecutionAttemptModel.id).where(
        db.ExecutionAttemptModel.operation_key == db.ExecutionOperationModel.key,
        db.ExecutionAttemptModel.scope_id == db.ExecutionOperationModel.scope_id).exists()
    orphan_query = select(db.ExecutionOperationModel).where(
        db.ExecutionOperationModel.status.in_(["completed", "stopped", "pending", "unknown"]),
        ~matched_attempt).order_by(db.ExecutionOperationModel.key)
    orphans = list(await session.scalars(orphan_query))
    locked_scopes = {scope} | {o.scope_id for o in orphans}
    budgets = {}
    # Scope locks are ordered, including immutable cancelled admission evidence.
    for key in sorted(locked_scopes):
        budgets[key] = await session.get(db.ExecutionBudgetModel, key,
            with_for_update=execute, populate_existing=True)
    budget = budgets[scope]
    _need(budget is not None and workflow.created_at is not None, "financial_origin_missing")
    attempts = list(await session.scalars(select(db.ExecutionAttemptModel).where(
        db.ExecutionAttemptModel.scope_id == scope).order_by(db.ExecutionAttemptModel.id)))
    # Compare timestamps in SQL: historical databases and SQLite differ in TZ representation.
    later = list(await session.scalars(select(db.ExecutionAttemptModel.id).where(
        db.ExecutionAttemptModel.created_at >= workflow.created_at)))
    _need(not later, "attempt_after_preparation")
    _need(all(a.status not in {"reserved", "unknown"} for a in attempts), "scope_not_settled")
    _need(all(a.status in SETTLED_ATTEMPTS for a in attempts), "attempt_status_unsupported")
    operations = list(await session.scalars(select(db.ExecutionOperationModel).where(
        db.ExecutionOperationModel.scope_id == scope).order_by(db.ExecutionOperationModel.key)))
    _need(budget.calls_used == len(attempts)
        and budget.tokens_charged == sum(a.actual_tokens if a.actual_tokens is not None
                                        else a.reserved_tokens for a in attempts),
        "financial_inventory_incomplete")
    roots = {o.key: o for o in operations}
    _need(all(a.operation_key in roots and roots[a.operation_key].input_hash == a.input_hash
              for a in attempts), "attempt_root_missing")
    _need(all(o.status not in {"pending", "unknown", "running"} for o in operations),
          "scope_operation_inflight")
    from bestseller.services.execution_control_purpose import STATUSES, verify_non_dispatch_control

    supported = SETTLED_ROOTS | BUDGET_RECEIPTS | STATUSES
    _need(all(o.status in supported for o in operations), "operation_status_unsupported")
    _need(all(roots[a.operation_key].status in SETTLED_ROOTS for a in attempts),
          "attempt_parent_not_chargeable")
    by_root = {}
    for attempt in attempts:
        by_root.setdefault(attempt.operation_key, []).append(attempt)
    by_attempt = {a.id: a for a in attempts}
    for op in operations:
        if op.status in SETTLED_ROOTS:
            _need(bool(by_root.get(op.key)), "orphan_chargeable_root")
        elif op.status in BUDGET_RECEIPTS:
            _need(_budget_receipt_valid(op, by_attempt, roots), "technical_receipt_invalid")
        else:
            try:
                proof = await verify_non_dispatch_control(session,
                    operation=op, operations=operations, attempts=attempts)
            except (ValueError, TypeError, KeyError) as error:
                raise ValueError("alignment_unsent_recovery:technical_receipt_invalid") from error
            _need(isinstance(proof, dict), "technical_receipt_invalid")
    # Search every scope, then interpret only canonical structured references.
    # A text filter keeps full unrelated provider bodies outside this process.
    referenced = list(await session.scalars(select(db.ExecutionOperationModel).where(
        cast(db.ExecutionOperationModel.result_json, Text).contains(str(workflow.id)))))
    _need(not any(_workflow_reference(o.result_json, str(workflow.id)) for o in referenced),
          "workflow_has_operation_evidence")
    isolated_admissions = []
    for root in await session.scalars(orphan_query.execution_options(populate_existing=True)):
        _need(not execute or root.scope_id in locked_scopes, "global_orphan_chargeable_root")
        isolated_admissions.append(await _cancelled_admission_proof(session, root,
            current_scope=scope))
    runs = list(await session.scalars(select(db.LlmRunModel).where(
        or_(db.LlmRunModel.workflow_run_id == workflow.id,
            (db.LlmRunModel.project_id == project.id)
            & or_(db.LlmRunModel.workflow_run_id.is_(None),
                   db.LlmRunModel.created_at >= workflow.created_at)))))
    unbound_proofs = []
    for run in runs:
        _need(run.workflow_run_id is None and run.project_id == project.id,
              "workflow_has_provider_result")
        try:
            from bestseller.services.retained_review_input_proof import _physical_return

            metadata = run.metadata_json or {}
            execution = metadata.get("execution_receipt") or {}
            origin = metadata.get("paid_call_origin") or {}
            root = roots.get(execution.get("operation_key"))
            declared = execution.get("attempts")
            actual = by_root.get(root.key, []) if root is not None else []
            _need(execution.get("policy") == "execution-budget.v1"
                and origin.get("schema") == "paid-call-origin.v1"
                and origin.get("status") == "recorded" and origin.get("workflow_run_id") is None
                and root is not None and isinstance(declared, list) and actual
                and all(isinstance(item, dict) and type(item.get("attempt_id")) is str
                        for item in declared)
                and len(declared) == len({item["attempt_id"] for item in declared})
                and {item["attempt_id"] for item in declared} == {a.id for a in actual},
                "workflow_has_provider_result")
            indexed = {a.id: a for a in actual}
            _need(all(item.get("model") == indexed[item["attempt_id"]].model_name
                and item.get("effective_input_hash") == indexed[item["attempt_id"]].input_hash
                and content_hash(item.get("reserved_tokens"))
                    == content_hash(indexed[item["attempt_id"]].reserved_tokens)
                for item in declared), "workflow_has_provider_result")
            paid, key = await _physical_return(session, run, scope=scope,
                expected_hash=run.prompt_hash, expected_model=run.model_name,
                allow_unbound_workflow=True)
            _need(key == root.key and content_hash(metadata.get("paid_call_origin"))
                == content_hash((paid.metadata or {}).get("paid_call_origin"))
                and all(a.input_hash == run.prompt_hash and a.model_name == run.model_name
                        for a in actual), "workflow_has_provider_result")
            for source in (metadata, paid.metadata):
                effective = source.get("effective_input_receipt") or {}
                _need(effective.get("schema") == "effective-input.v1"
                    and effective.get("hash") == run.prompt_hash
                    and effective.get("model") == run.model_name, "workflow_has_provider_result")
            # The earlier GLOBAL interval query already excluded every actual
            # gateway attempt since this claim's workflow was created. Run-row
            # timestamps (transaction-start defaults) do not prove send time.
            unbound_proofs.append({"llm_run_id": str(run.id),
                "row_sha256": content_hash(_row(run)), "original_workflow_run_id": None,
                "operation_key": key, "actual_attempt_ids": sorted(a.id for a in actual),
                "operation_sha256": content_hash(_row(root)),
                "purpose": "preexisting_paid_request_only_no_grade_or_credit"})
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("alignment_unsent_recovery:workflow_has_provider_result") from error
    live = list(await session.scalars(select(db.WorkflowRunModel.id).where(
        db.WorkflowRunModel.project_id == project.id,
        db.WorkflowRunModel.status.in_(["pending", "queued", "running", "stopping"]))))
    _need(not live, "project_dispatcher_inflight")
    scenes = list(await session.scalars(select(db.SceneCardModel).where(
        db.SceneCardModel.project_id == project.id, db.SceneCardModel.chapter_id == chapter.id
    ).order_by(db.SceneCardModel.scene_number)))
    _need(bool(scenes), "scene_inventory_missing")
    shapes = []
    for scene in scenes:
        context = ChapterSceneContext(scene_number=scene.scene_number, title=scene.title,
            scene_type=scene.scene_type, status=scene.status,
            participants=list(scene.participants),
            story_purpose=str(scene.purpose["story"]) if scene.purpose.get("story") else None,
            emotion_purpose=str(scene.purpose["emotion"]) if scene.purpose.get("emotion") else None)
        structure = SceneStructureRead(id=scene.id, scene_number=scene.scene_number,
            title=scene.title, scene_type=scene.scene_type, status=scene.status,
            participants=list(scene.participants), target_word_count=scene.target_word_count)
        shapes.append({"row_sha256": content_hash(_row(scene)),
                       "context": context.model_dump(mode="json"),
                       "structure": structure.model_dump(mode="json")})
    witness = {"schema": SCHEMA, "workflow_sha256": content_hash(_row(workflow)),
        "task_sha256": content_hash(_row(task)), "project_sha256": content_hash(_row(project)),
        "chapter_sha256": content_hash(_row(chapter)), "draft_sha256": content_hash(_row(draft)),
        "scene_shapes_sha256": content_hash(shapes),
        "scene_schema_sha256": content_hash([ChapterSceneContext.model_json_schema(),
                                               SceneStructureRead.model_json_schema()]),
        "financial_sha256": content_hash({"budget": _row(budget),
            "attempts": [_row(a) for a in attempts], "operations": [_row(o) for o in operations]}),
        "attempt_count": len(attempts), "operation_count": len(operations),
        "attempt_interval_scope": "all_scopes",
        "attempts_since_preparation": [], "workflow_provider_runs": [],
        "verified_preexisting_unbound_runs": unbound_proofs,
        "isolated_cancelled_admissions": isolated_admissions,
        "actor": actor, "authorization_ref": authorization_ref, "repair_ref": repair_ref}
    digest = content_hash(witness)
    out = {"status": "ready", "workflow_run_id": str(workflow.id), "task_id": str(task.id),
        "retired_claim_id": expected_claim_id, "witness": witness,
        "witness_sha256": digest, "provider_dispatches": 0, "database_writes": 0}
    if not execute:
        return out
    _need(expected_witness_sha256 == digest, "witness_changed")
    retired = {"schema": SCHEMA, "retired_claim_id": expected_claim_id,
        "repair_ref": repair_ref, "witness": witness, "witness_sha256": digest,
        "prior_workflow": _row(workflow), "new_grade_or_adoption": False,
        "new_spend_allowance": False, "financial_rows_changed": False}
    history.append(retired)
    receipt["claim_id"] = None
    receipt.pop("blocked_reason", None)
    workflow.metadata_json = {**workflow.metadata_json, HISTORY: history, dispatch._KEY: receipt}
    workflow.status = "pending"
    workflow.current_step = "outline_alignment_prepared"
    workflow.error_message = None
    await session.flush()
    return {**out, "status": "reconciled", "receipt": retired, "database_writes": 1}
