"""Read-only provenance guard for an explicitly cancelled review-policy recovery.

This proof is not a review, grade, adoption, retry allowance or dispatch permit.
The caller owns the chapter lock, current-policy verification, durable recovery
identity and checkpoint, and must preserve the original cancelled workflow.
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
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.budget import completion_authorization, execution_scope_block_code

SCHEMA = "cancelled-review-policy-source.v1"


def _require(value, code):
    if not value:
        raise ValueError("chapter_cancelled_review_recovery_" + code)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _uuid(value):
    return isinstance(value, str) and str(UUID(value)) == value


async def validate_cancelled_review_policy_source(
    session, *, project, chapter, draft, previous, execution_scope_id, current_policy_hash,
) -> dict:
    """Validate ONE stored cancelled source, without flush/commit or model calls.

    current_policy_hash must be the caller's verified actual code hash, as in
    resume_chapter_review_after_policy_change. Missing/ambiguous adoption and
    unknown source evidence fail closed. An absent control row is NOT explicit RUN.
    """
    try:
        with session.no_autoflush:
            return await _validate(session, project, chapter, draft, previous,
                                   execution_scope_id, current_policy_hash)
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValueError("chapter_cancelled_review_recovery_invalid_source_shape") from None


async def _validate(session, project, chapter, draft, previous, scope, policy):
    from bestseller.services.pipelines import _chapter_critic_cycle_id, chapter_review_body_fingerprint

    _require(_sha(policy), "current_policy_hash")
    _require(isinstance(scope, str) and 0 < len(scope) <= 256
             and scope.startswith(("project:", "conception:", "workflow:")), "execution_scope")
    _require(chapter.project_id == draft.project_id == previous.project_id == project.id
             and draft.chapter_id == previous.scope_id == chapter.id
             and previous.scope_type == "chapter" and previous.workflow_type == "chapter_pipeline"
             and previous.status == "cancelled", "source_binding")
    relevant = {(type(row), row.id) for row in (project, chapter, draft, previous)}
    _require(not any((type(row), getattr(row, "id", None)) in relevant
        for row in [*session.new, *session.dirty, *session.deleted]), "uncommitted_source")
    project_meta = await session.scalar(select(ProjectModel.metadata_json).where(ProjectModel.id == project.id))
    _require(isinstance(project_meta, Mapping) and project_meta.get("execution_scope_id") == scope
             and (project.metadata_json or {}).get("execution_scope_id") == scope, "project_scope")
    chapter_row = (await session.execute(select(ChapterModel.project_id, ChapterModel.metadata_json).where(
        ChapterModel.id == chapter.id))).one_or_none()
    _require(chapter_row is not None and chapter_row.project_id == project.id
             and chapter_row.metadata_json == chapter.metadata_json, "stored_chapter")
    prior = (await session.execute(select(WorkflowRunModel.project_id, WorkflowRunModel.scope_type,
        WorkflowRunModel.scope_id, WorkflowRunModel.workflow_type, WorkflowRunModel.status,
        WorkflowRunModel.current_step, WorkflowRunModel.error_message, WorkflowRunModel.metadata_json).where(
        WorkflowRunModel.id == previous.id))).one_or_none()
    _require(prior is not None and all(getattr(prior, key) == getattr(previous, key) for key in (
        "project_id", "scope_type", "scope_id", "workflow_type", "status", "current_step",
        "error_message", "metadata_json")), "stored_cancelled_origin")
    current = (await session.execute(select(ChapterDraftVersionModel.id, ChapterDraftVersionModel.content_md,
        ChapterDraftVersionModel.promotion_state, ChapterDraftVersionModel.promoted_at).where(
        ChapterDraftVersionModel.project_id == project.id,
        ChapterDraftVersionModel.chapter_id == chapter.id,
        ChapterDraftVersionModel.is_current.is_(True)))).all()
    _require(len(current) == 1 and current[0].id == draft.id and draft.is_current
             and isinstance(draft.content_md, str) and draft.content_md.strip()
             and current[0].content_md == draft.content_md
             and current[0].promotion_state == draft.promotion_state
             and current[0].promoted_at is None and draft.promoted_at is None
             and draft.promotion_state in {"candidate", "under_review", "eligible", "rejected",
                                           "superseded", "quarantined"}, "current_unpromoted_body")
    fingerprint = chapter_review_body_fingerprint(draft.content_md)
    cycles = (chapter.metadata_json or {}).get("chapter_critic_cycles")
    _require(isinstance(cycles, Mapping), "stored_cycles_required")
    matching = []
    for key, cycle in cycles.items():
        _require(isinstance(cycle, Mapping) and cycle.get("cycle_id") == key, "stored_cycle_shape")
        if (cycle.get("source_draft_id") == str(draft.id)
                and cycle.get("source_body_hash") == fingerprint["digest"]
                and cycle.get("review_workflow_run_id") == str(previous.id)):
            matching.append(cycle)
    _require(len(matching) == 1, "unique_current_cancelled_cycle_required")
    cycle = matching[0]
    old_policy = cycle.get("policy_source_hash")
    _require(_sha(old_policy) and old_policy != policy, "actual_policy_change_required")
    _require(cycle.get("schema_version") == "chapter-critic-cycle.v1"
             and cycle.get("state") in {"prepared", "running", "completed"}
             and _sha(cycle.get("semantic_dependencies_sha256")), "cycle_protocol")
    _require(cycle["cycle_id"] in {
        _chapter_critic_cycle_id(project.id, chapter.id, fingerprint["digest"]),
        _chapter_critic_cycle_id(project.id, chapter.id, fingerprint["digest"], old_policy),
    }, "canonical_cycle_identity")
    attempts = cycle.get("attempts")
    _require(isinstance(attempts, list) and len(attempts) <= 2, "attempts")
    evidence = []
    for index, entry in enumerate(attempts, 1):
        _require(isinstance(entry, Mapping) and type(entry.get("attempt")) is int
                 and entry["attempt"] == index and entry.get("state") in {"prepared", "running", "completed"}
                 and entry.get("operation_namespace") == f"chapter-critic:{cycle['cycle_id']}:{index}", "attempt_binding")
        if entry["state"] == "completed":
            _require(entry.get("outcome") in {"unknown", "available"}
                     and entry.get("draft_id") == str(draft.id)
                     and entry.get("body_fingerprint") == fingerprint
                     and _uuid(entry.get("review_report_id")) and _uuid(entry.get("quality_score_id")), "attempt_result_binding")
        ids = {key: entry[key] for key in ("review_report_id", "received_report_id", "quality_score_id", "rewrite_task_id")
               if entry.get(key) is not None}
        _require(all(_uuid(value) for value in ids.values()), "evidence_ids")
        evidence.append({"attempt": index, "state": entry["state"], **ids})
        if index == 2:
            _require(attempts[0].get("state") == "completed" and attempts[0].get("outcome") == "unknown",
                     "attempt_sequence")
    _require(cycle["state"] != "completed" or (attempts and attempts[-1]["state"] == "completed"), "completed_cycle_attempts")
    control = (await session.execute(select(BookProductionControlModel.desired_state,
        BookProductionControlModel.command_serial).where(BookProductionControlModel.project_id == project.id))).one_or_none()
    _require(control is not None and control.desired_state == "run"
             and type(control.command_serial) is int and control.command_serial > 0,
             "explicit_project_run_required")
    budget = (await session.execute(select(ExecutionBudgetModel.metadata_json,
        ExecutionBudgetModel.status, ExecutionBudgetModel.deadline_at,
        ExecutionBudgetModel.calls_used, ExecutionBudgetModel.tokens_charged).where(
        ExecutionBudgetModel.scope_id == scope))).one_or_none()
    authorization = completion_authorization(budget) if budget is not None else None
    _require(budget is not None and budget.status in {"active", "exhausted"}
             and authorization is not None and authorization.get("status") == "active"
             and execution_scope_block_code(budget) is None, "active_completion_authorization_required")
    _require(await session.scalar(select(ExecutionOperationModel.key).where(
        ExecutionOperationModel.scope_id == scope,
        ExecutionOperationModel.status.in_(("pending", "unknown"))).limit(1)) is None,
        "settled_scope_required")
    _require(await session.scalar(select(ExecutionAttemptModel.id).where(
        ExecutionAttemptModel.scope_id == scope,
        ExecutionAttemptModel.status.in_(("reserved", "pending", "unknown"))).limit(1)) is None,
        "settled_attempts_required")
    adopted, numeric_receipt = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    _require(adopted is None or adopted.id != draft.id, "target_already_adopted")
    _require((chapter.metadata_json or {}).get("numeric_promotion_debt") is None
             or numeric_receipt is not None, "adoption_ambiguous")
    if adopted is None:
        promoted = await session.scalar(select(ChapterDraftVersionModel.id).where(
            ChapterDraftVersionModel.project_id == project.id,
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.promotion_state == "promoted").limit(1))
        _require(promoted is None and (chapter.metadata_json or {}).get("numeric_promotion_debt") is None,
                 "adoption_ambiguous")
    proof = {
        "schema": SCHEMA, "status": "verified_cancelled_origin",
        "project_id": str(project.id), "chapter_id": str(chapter.id), "draft_id": str(draft.id),
        "previous_workflow_run_id": str(previous.id), "previous_status": "cancelled",
        "previous_step": previous.current_step, "previous_error_sha256": _digest(previous.error_message),
        "previous_metadata_sha256": _digest(previous.metadata_json), "execution_scope_id": scope,
        "source_body_fingerprint": fingerprint,
        "raw_body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
        "source_cycle_id": cycle["cycle_id"], "source_cycle": deepcopy(dict(cycle)),
        "source_cycle_sha256": _digest(cycle), "evidence_ids": evidence,
        "previous_policy_source_hash": old_policy, "current_policy_source_hash": policy,
        "control_intent": "run", "control_command_serial": control.command_serial,
        "authorization_ref": authorization["authorization_ref"], "authorization_sha256": _digest(authorization),
        "budget_status": budget.status,
        "budget_usage": {"calls_used": budget.calls_used, "tokens_charged": budget.tokens_charged},
        "adopted_source_id": str(adopted.id) if adopted is not None else None,
        "origin_only": True, "grade_reuse_allowed": False, "adoption_allowed": False,
        "attempt_reset_allowed": False, "new_credit_allowed": False, "dispatch_allowed": False,
    }
    proof["proof_sha256"] = _digest(proof)
    return proof
