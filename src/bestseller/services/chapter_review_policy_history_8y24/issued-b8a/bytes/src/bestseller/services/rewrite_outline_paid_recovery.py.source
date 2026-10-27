"""Resume a consumed alignment claim from its exact completed paid request.

No claim, attempt, allowance or historical result is reset. Request preparation
uses the real executor in a rollback-only session before the claim is resumed.
"""
from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from uuid import UUID

from sqlalchemy import Text, and_, cast, or_, select

from bestseller.infra.db import models as db
from bestseller.services import rewrite_outline_dispatch as dispatch
from bestseller.services.budget import bind_execution_scope
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.rewrite_outline_dispatch_recovery import _row, _workflow_reference
from bestseller.services.rewrite_patch import text_hash

SCHEMA = "outline-alignment-paid-recovery.v1"
HISTORY = "paid_alignment_recoveries"
_ACTIVE = ContextVar("outline_alignment_paid_recovery", default=None)


def _need(value, code):
    if not value:
        raise ValueError("alignment_paid_recovery:" + code)


def active_paid_recovery(workflow_run_id):
    import asyncio
    state = _ACTIVE.get()
    if state and state["task"] is asyncio.current_task() and state["workflow_run_id"] == str(workflow_run_id):
        return state
    return None


async def paid_alignment_state(session, settings, *, workflow_run_id, expected_claim_id):
    """Read the current source and the complete original paid custody chain."""
    workflow = await session.get(db.WorkflowRunModel, workflow_run_id, populate_existing=True)
    _need(workflow is not None and workflow.workflow_type == dispatch._TYPE, "workflow")
    raw = (workflow.metadata_json or {}).get(dispatch._KEY) or {}
    scope = raw.get("original_execution_scope_id")
    task = await session.get(db.RewriteTaskModel, UUID(raw["task_id"]), populate_existing=True)
    _need(task is not None, "task")
    receipt = dispatch._receipt(workflow, task, chapter_id=workflow.scope_id, scope=scope)
    _need(workflow.status == "machine_blocked"
        and workflow.current_step == "alignment_rewrite_failed_requires_reconciliation"
        and receipt.get("claim_id") == expected_claim_id
        and str(UUID(expected_claim_id)) == expected_claim_id
        and receipt.get("output") is None, "consumed_claim")
    project, chapter, draft, marker = await dispatch._context(session, settings=settings,
        project_id=task.project_id, chapter_id=workflow.scope_id,
        marker=receipt["source_plan_binding"], scope=scope)
    from bestseller.services.reviews import _rewrite_task_source_identity
    _need(task.status in dispatch._PENDING and task.attempts == receipt["attempts_before"] == 0
        and dispatch._alignment(task) and dispatch._parent(task, draft, marker)
        and receipt["task_source_identity"] == _rewrite_task_source_identity(task)
        and receipt["input_draft_id"] == str(draft.id)
        and receipt["input_body_sha256"] == text_hash(draft.content_md), "parent_or_task")
    live = list(await session.scalars(select(db.WorkflowRunModel).where(
        db.WorkflowRunModel.project_id == project.id,
        db.WorkflowRunModel.status.in_(("running", "pending", "queued", "stopping")))))
    _need(not live, "live_workflow")
    reserved = await session.scalar(select(db.ExecutionAttemptModel.id).where(or_(
        db.ExecutionAttemptModel.status == "reserved",
        and_(db.ExecutionAttemptModel.scope_id == scope,
            db.ExecutionAttemptModel.status == "unknown"))).limit(1))
    _need(reserved is None, "unsettled_attempt")
    # Query candidate references in PG before loading large historical JSON.
    # Exact recursive validation below rejects incidental textual matches.
    global_operations = list(await session.scalars(select(db.ExecutionOperationModel).where(
        cast(db.ExecutionOperationModel.result_json, Text).contains(str(workflow.id)))))
    operations = [op for op in global_operations if op.scope_id == scope]
    roots = [op for op in operations if op.status == "completed" and
        ((op.result_json or {}).get("metadata") or {}).get("paid_call_origin", {}).get(
            "workflow_run_id") == str(workflow.id)]
    _need(len(roots) == 1, "paid_root_ambiguity")
    root = roots[0]
    result = root.result_json
    origin = result["metadata"].get("paid_call_origin") or {}
    effective = result["metadata"].get("effective_input_receipt") or {}
    _need(origin.get("schema") == "paid-call-origin.v1" and origin.get("status") == "recorded"
        and origin.get("project_id") == str(project.id)
        and origin.get("logical_role") == "editor"
        and origin.get("prompt_template") == "chapter_rewrite"
        and origin.get("step_run_id") is None
        and origin.get("prompt_hash") == root.input_hash
        and effective.get("schema") == "effective-input.v1"
        and effective.get("hash") == root.input_hash
        and effective.get("model") == result.get("model_name"), "paid_origin")
    _need(type(result.get("content")) is str and result["content"].strip()
        and result.get("finish_reason") == "stop" and result.get("fallback_used") is False
        and result.get("tool_calls") is None, "paid_body")
    attempts = list(await session.scalars(select(db.ExecutionAttemptModel).where(
        db.ExecutionAttemptModel.operation_key == root.key).order_by(db.ExecutionAttemptModel.id)))
    _need(len(attempts) == 1, "paid_attempt_ambiguity")
    attempt = attempts[0]
    _need(attempt.scope_id == scope and attempt.status == "completed"
        and attempt.input_hash == root.input_hash and attempt.model_name == result["model_name"]
        and type(attempt.actual_tokens) is int and attempt.actual_tokens >= 0
        and type(attempt.reserved_tokens) is int and attempt.reserved_tokens > 0
        and all(type(result.get(k)) is int and result[k] >= 0 for k in ("input_tokens", "output_tokens"))
        and result["input_tokens"] + result["output_tokens"] == attempt.actual_tokens, "paid_attempt")
    paid_receipt = result["metadata"].get("execution_receipt") or {}
    declared = paid_receipt.get("attempts")
    _need(paid_receipt.get("scope_id") == scope and paid_receipt.get("operation_key") == root.key
        and type(declared) is list and len(declared) == 1, "paid_receipt")
    declared = declared[0]
    _need(declared.get("attempt_id") == attempt.id and declared.get("status") == "completed"
        and declared.get("model") == attempt.model_name
        and declared.get("effective_input_hash") == root.input_hash
        and type(declared.get("reserved_tokens")) is int
        and declared["reserved_tokens"] == attempt.reserved_tokens
        and type(declared.get("wire_body_hash")) is str
        and len(declared["wire_body_hash"]) == 64
        and all(c in "0123456789abcdef" for c in declared["wire_body_hash"]), "paid_attempt_receipt")
    transports = list(await session.scalars(select(db.ExecutionOperationModel).where(
        db.ExecutionOperationModel.status == "transport_result",
        db.ExecutionOperationModel.result_json["attempt_id"].as_string() == attempt.id)))
    _need(len(transports) == 1, "physical_return_ambiguity")
    transport = transports[0]
    physical = transport.result_json
    _need(physical.get("schema") == "transport-result.v1"
        and transport.scope_id == scope
        and physical.get("operation_key") == root.key
        and physical.get("input_hash") == root.input_hash == transport.input_hash
        and physical.get("attempt_id") == attempt.id
        and isinstance(physical.get("result"), dict), "physical_return_binding")
    _need(content_hash((physical["result"].get("metadata") or {}).get("paid_call_origin"))
        == content_hash(origin), "physical_return_origin")
    fields = ("content", "model_name", "provider", "input_tokens", "output_tokens",
        "finish_reason", "fallback_used", "fallback_source", "tool_calls", "raw_message")
    _need(all(k in physical["result"] and k in result and
        content_hash(physical["result"][k]) == content_hash(result[k]) for k in fields), "physical_return")
    related_operations = [op for op in global_operations
        if _workflow_reference(op.result_json, str(workflow.id))]
    _need(all(op.key in {root.key, transport.key} for op in related_operations
        if _workflow_reference(op.result_json, str(workflow.id))), "other_workflow_payment")
    later_attempts = list(await session.scalars(select(db.ExecutionAttemptModel).where(
        db.ExecutionAttemptModel.scope_id == scope,
        db.ExecutionAttemptModel.created_at >= workflow.created_at)))
    _need({a.id for a in later_attempts} == {attempt.id}, "later_attempts")
    all_attempts = list(await session.scalars(select(db.ExecutionAttemptModel).where(
        db.ExecutionAttemptModel.scope_id == scope)))
    _need(all(a.status in {"completed", "rejected", "not_sent", "reconciled_charged"}
        and type(a.reserved_tokens) is int and a.reserved_tokens >= 0
        and (a.status == "reconciled_charged" or
            type(a.actual_tokens) is int and a.actual_tokens >= 0) for a in all_attempts), "financial_state")
    budget = await session.get(db.ExecutionBudgetModel, scope)
    _need(budget.calls_used == len(all_attempts) and budget.tokens_charged == sum(
        a.reserved_tokens if a.status == "reconciled_charged" else a.actual_tokens
        for a in all_attempts), "financial_accounting")
    namespace = "pipeline:run_chapter_pipeline:" + content_hash({
        "project_slug": project.slug, "chapter_number": chapter.chapter_number})
    from bestseller.services.retained_request_guard import retained_attempts_sha256
    binding = {"scope_id": scope, "project_id": str(project.id),
        "workflow_run_id": str(workflow.id), "logical_role": origin["logical_role"],
        "prompt_template": origin["prompt_template"], "prompt_version": origin["prompt_version"],
        "model": result["model_name"], "input_hash": root.input_hash, "operation_key": root.key,
        "result_sha256": content_hash(result),
        "attempts_sha256": retained_attempts_sha256([
            {column.key: getattr(a, column.key) for column in a.__mapper__.column_attrs}
            for a in attempts])}
    witness = {"workflow": _row(workflow), "task": _row(task), "draft": _row(draft),
        "source_plan_binding": marker, "paid_root": _row(root),
        "physical_return": _row(transport), "actual_attempts": [_row(a) for a in attempts],
        "budget": _row(budget), "namespace": namespace,
        "global_workflow_payment_refs": [_row(op) for op in sorted(related_operations, key=lambda o: o.key)]}
    return {"binding": binding, "witness": witness, "witness_sha256": content_hash(witness),
        "project_slug": project.slug, "chapter_number": chapter.chapter_number,
        "task_id": task.id, "namespace": namespace}


async def prepare_paid_alignment_recovery(session_factory, settings, *, workflow_run_id,
    expected_claim_id, rewrite_executor=None):
    """Rebuild the actual next request; rollback everything before checkpoint."""
    from bestseller.services import reviews
    from bestseller.services.llm import bind_execution_operation
    from bestseller.services.retained_request_guard import bind_retained_request, RetainedRequestPrepared
    async with session_factory() as session:
        workflow = await session.get(db.WorkflowRunModel, workflow_run_id)
        scope = workflow.metadata_json[dispatch._KEY]["original_execution_scope_id"]
        with bind_execution_scope(scope):
            state = await paid_alignment_state(session, settings,
                workflow_run_id=workflow_run_id, expected_claim_id=expected_claim_id)
    executor = rewrite_executor or reviews.rewrite_chapter_from_task
    async with session_factory() as probe:
        try:
            with probe.no_autoflush, bind_execution_scope(scope), bind_execution_operation(state["namespace"]), \
                bind_retained_request(state["binding"], inspect_only=True):
                await executor(probe, state["project_slug"], state["chapter_number"],
                    rewrite_task_id=state["task_id"], settings=settings, workflow_run_id=workflow_run_id)
        except RetainedRequestPrepared as prepared:
            state["prepared_request"] = deepcopy(prepared.receipt)
        finally:
            await probe.rollback()
    _need("prepared_request" in state, "prepared_request_missing")
    return state


async def resume_paid_alignment_recovery(session_factory, settings, *, workflow_run_id,
    expected_claim_id, expected_witness_sha256, actor, authorization_ref, repair_ref,
    allow_followup_after_reuse=False, rewrite_executor=None):
    """Keep the same consumed claim and reuse its first request before new work."""
    import asyncio
    from bestseller.services.llm import bind_execution_operation
    from bestseller.services.retained_request_guard import bind_retained_request
    _need(all(type(v) is str and v.strip() for v in (actor, authorization_ref, repair_ref)), "authorization")
    state = await prepare_paid_alignment_recovery(session_factory, settings,
        workflow_run_id=workflow_run_id, expected_claim_id=expected_claim_id,
        rewrite_executor=rewrite_executor)
    _need(state["witness_sha256"] == expected_witness_sha256, "witness_changed")
    active = {**state, "workflow_run_id": str(workflow_run_id), "expected_claim_id": expected_claim_id,
        "actor": actor, "authorization_ref": authorization_ref, "repair_ref": repair_ref,
        "task": asyncio.current_task()}
    token = _ACTIVE.set(active)
    try:
        with bind_execution_scope(state["binding"]["scope_id"]), bind_execution_operation(state["namespace"]), \
            bind_retained_request(state["binding"], allow_followup_after_reuse=allow_followup_after_reuse):
            return await dispatch.execute_outline_alignment_dispatch(session_factory, settings,
                workflow_run_id=workflow_run_id, rewrite_executor=rewrite_executor)
    finally:
        _ACTIVE.reset(token)
