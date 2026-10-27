"""One durable outline-alignment rewrite, then a strict new-body review handoff.

Parent owns fresh source admission and pipeline recursion. No latest-source lookup,
task creation, budget grant/reset, style replay, review, adoption, or export occurs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionBudgetModel,
    ProjectModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services.budget import current_execution_scope, execution_scope_block_code
from bestseller.services.reviews import chapter_review_body_fingerprint
from bestseller.services.rewrite_outline_binding import validate_source_plan_binding
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import AppSettings, load_settings

_SCHEMA = "outline-alignment-dispatch.v1"
_TYPE = "chapter_outline_alignment_rewrite"
_KEY = "source_alignment_dispatch"
_PENDING = {"pending", "queued"}


@dataclass(frozen=True)
class OutlineAlignmentDispatch:
    state: Literal["not_applicable", "prepared", "inflight", "blocked", "review_ready"]
    reason: str | None = None
    workflow_run_id: UUID | None = None
    task_id: UUID | None = None
    review_source_binding: dict[str, str] | None = None


def _result(state: Any, reason: str | None = None, *, workflow=None, task=None, binding=None):
    return OutlineAlignmentDispatch(
        state,
        reason,
        workflow.id if workflow else None,
        task.id if task else None,
        deepcopy(binding),
    )


def _workflow_id(task_id: UUID) -> UUID:
    return uuid5(task_id, _SCHEMA)


def _alignment(task: RewriteTaskModel) -> bool:
    metadata = task.metadata_json
    alignment = metadata.get("source_alignment") if isinstance(metadata, Mapping) else None
    return (
        task.trigger_type == "outline_revision"
        and task.rewrite_strategy == "chapter_source_alignment_rewrite"
        and isinstance(alignment, Mapping)
        and alignment.get("schema") == "outline-revision-rewrite.v1"
        and alignment.get("evidence_status") == "UNGRADED"
        and alignment.get("requires_formal_review") is True
        and alignment.get("requires_formal_adoption") is True
        and metadata.get("review_report_id") is None
    )


def _parent(task: RewriteTaskModel, draft: ChapterDraftVersionModel, marker: dict) -> bool:
    metadata = task.metadata_json or {}
    return (
        metadata.get("draft_id") == str(draft.id)
        and metadata.get("parent_draft_id") == str(draft.id)
        and metadata.get("parent_draft_hash") == text_hash(draft.content_md or "")
        and metadata.get("source_plan_binding") == marker
    )


async def _context(
    session, *, project_id, chapter_id, marker, scope, settings, lock=False, admit=True
):
    """Selected source-entry marker only; the parent already ran source admission."""
    marker = validate_source_plan_binding(marker)
    project = await session.scalar(
        select(ProjectModel)
        .where(ProjectModel.id == project_id)
        .execution_options(populate_existing=True)
    )
    chapter_query = (
        select(ChapterModel)
        .where(
            ChapterModel.id == chapter_id,
            ChapterModel.project_id == project_id,
        )
        .execution_options(populate_existing=True)
    )
    chapter = await session.scalar(chapter_query.with_for_update() if lock else chapter_query)
    if project is None or chapter is None:
        raise ValueError("alignment_dispatch_project_chapter_mismatch")
    # Existing query-only project/status/control/terminal/budget guard. Never
    # acquire project/control locks across the independent paid gateway writes.
    from bestseller.services.outline_revision_lifecycle import _runnable

    if admit:
        await _runnable(session, project)
    if (chapter.metadata_json or {}).get("outline_source_admission") != marker:
        raise ValueError("alignment_dispatch_selected_marker_changed")
    if (
        not isinstance(scope, str)
        or not scope
        or current_execution_scope() != scope
        or (project.metadata_json or {}).get("execution_scope_id") != scope
    ):
        raise ValueError("alignment_dispatch_original_scope_mismatch")
    admission = await session.scalar(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.id == UUID(marker["workflow_run_id"]),
            WorkflowRunModel.project_id == project_id,
        )
        .execution_options(populate_existing=True)
    )
    if admission is None or (admission.metadata_json or {}).get("execution_scope_id") != scope:
        raise ValueError("alignment_dispatch_admission_scope_mismatch")
    budget = await session.scalar(
        select(ExecutionBudgetModel)
        .where(
            ExecutionBudgetModel.scope_id == scope,
        )
        .execution_options(populate_existing=True)
    )
    if budget is None:
        raise ValueError("alignment_dispatch_original_budget_missing")
    if admit and execution_scope_block_code(budget) is not None:
        raise ValueError("alignment_dispatch_original_scope_closed")
    draft_query = (
        select(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.project_id == project_id,
            ChapterDraftVersionModel.chapter_id == chapter_id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    draft = (
        await session.execute(draft_query.with_for_update() if lock else draft_query)
    ).scalar_one_or_none()
    if draft is None or not (draft.content_md or "").strip():
        raise ValueError("alignment_dispatch_current_body_missing")
    # Re-run ACTUAL proof against the marker-selected artifact, canonical and
    # foundation sources, actual-grade admission and materialized projection.
    # The caller's earlier source guard does not authorize a later isolated send.
    from bestseller.services.outline_source_entry import require_current_outline_source_admission

    if admit:
        proof = await require_current_outline_source_admission(
            session,
            settings,
            project=project,
            chapter=chapter,
        )
        if (
            not isinstance(proof, Mapping)
            or proof.get("workflow_run_id") != marker["workflow_run_id"]
            or proof.get("source_content_sha256") != marker["source_content_sha256"]
            or proof.get("source_plan_projection") != marker["projection_hash"]
        ):
            raise ValueError("alignment_dispatch_current_hard_proof_required")
    return project, chapter, draft, marker


@contextmanager
def _bind_control_guard(session_factory, project_id):
    from bestseller.services.outline_revision_lifecycle import _runnable
    from bestseller.services.progress_context import _CANCELLATION_CHECKER

    previous = _CANCELLATION_CHECKER.get()

    async def check():
        if previous is not None:
            await previous()
        async with session_factory() as control_session:
            project = await control_session.get(ProjectModel, project_id)
            if project is None:
                raise ValueError("alignment_dispatch_project_missing")
            await _runnable(control_session, project)

    token = _CANCELLATION_CHECKER.set(check)
    try:
        yield
    finally:
        _CANCELLATION_CHECKER.reset(token)


def _receipt(workflow: WorkflowRunModel, task: RewriteTaskModel, *, chapter_id, scope):
    metadata = workflow.metadata_json or {}
    receipt = metadata.get(_KEY)
    if (
        workflow.id != _workflow_id(task.id)
        or workflow.workflow_type != _TYPE
        or workflow.project_id != task.project_id
        or workflow.scope_type != "chapter"
        or workflow.scope_id != chapter_id
        or workflow.idempotency_key != f"{_SCHEMA}:{task.id}"
        or metadata.get("execution_scope_id") != scope
        or not isinstance(receipt, dict)
        or receipt.get("schema") != _SCHEMA
        or receipt.get("task_id") != str(task.id)
        or receipt.get("chapter_id") != str(chapter_id)
        or receipt.get("project_id") != str(task.project_id)
        or receipt.get("original_execution_scope_id") != scope
        or receipt.get("input_draft_id") != (task.metadata_json or {}).get("parent_draft_id")
        or receipt.get("input_body_sha256") != (task.metadata_json or {}).get("parent_draft_hash")
        or receipt.get("source_plan_binding")
        != (task.metadata_json or {}).get("source_plan_binding")
        or receipt.get("attempts_before") != 0
    ):
        raise ValueError("alignment_dispatch_workflow_identity_mismatch")
    return deepcopy(receipt)


def _handoff(workflow, task, draft, marker, receipt):
    output = receipt.get("output")
    if (
        workflow.status != "completed"
        or task.status != "completed"
        or not receipt.get("claim_id")
        or int(task.attempts or 0) != 1
        or not isinstance(output, dict)
        or output.get("evidence_status") != "UNGRADED"
        or receipt.get("source_plan_binding") != marker
        or output.get("draft_id") != str(draft.id)
        or output.get("body_sha256") != text_hash(draft.content_md or "")
        or str(draft.id) == receipt.get("input_draft_id")
        or output.get("body_sha256") == receipt.get("input_body_sha256")
        or (task.metadata_json or {}).get("rewritten_chapter_draft_id") != str(draft.id)
    ):
        return _result(
            "blocked", "alignment_dispatch_output_not_current", workflow=workflow, task=task
        )
    binding = {
        "draft_id": str(draft.id),
        "body_hash": chapter_review_body_fingerprint(draft.content_md or "")["digest"],
    }
    return _result("review_ready", workflow=workflow, task=task, binding=binding)


async def _completed_route(session, settings, workflow, task, chapter, draft, marker, receipt):
    result = _handoff(workflow, task, draft, marker, receipt)
    if result.state != "review_ready":
        return result
    from bestseller.services.chapter_review_preflight import (
        validate_retained_review_before_processing,
    )

    proof = await validate_retained_review_before_processing(session, settings, chapter)
    if proof is not None:
        if (
            proof.get("project_id") != str(chapter.project_id)
            or proof.get("chapter_id") != str(chapter.id)
            or proof.get("draft_id") != str(draft.id)
            or proof.get("body_sha256") != result.review_source_binding["body_hash"]
        ):
            raise ValueError("alignment_dispatch_current_review_proof_mismatch")
        # Ordinary entry MUST still execute its usual preflight. This preserves
        # pending/available/unknown/waiting gates and normal targeted rewrites.
        return _result(
            "not_applicable",
            "alignment_dispatch_current_review_available",
            workflow=workflow,
            task=task,
        )
    return result


async def prepare_outline_alignment_dispatch(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    expected_draft_id: UUID | None,
    expected_body_sha256: str | None,
    expected_plan_binding: dict[str, str] | None,
    original_execution_scope_id: str | None,
    requested_by: str = "system",
    settings: AppSettings | None = None,
) -> OutlineAlignmentDispatch:
    """Stage a stable workflow only. Caller flushes/checkpoints before execution."""
    from bestseller.services.reviews import _rewrite_task_source_identity

    settings = settings or load_settings()

    for obj in session.dirty:
        if (
            (isinstance(obj, ProjectModel) and obj.id == project_id)
            or (isinstance(obj, ChapterModel) and obj.id == chapter_id)
            or (isinstance(obj, ChapterDraftVersionModel) and obj.chapter_id == chapter_id)
            or (
                isinstance(obj, RewriteTaskModel)
                and obj.project_id == project_id
                and obj.trigger_source_id == chapter_id
            )
        ):
            raise ValueError("alignment_dispatch_caller_flush_required")
    with session.no_autoflush:
        # An ordinary new chapter with no alignment work must keep normal entry,
        # including when it has no retained body or ordinary admission marker yet.
        detected = list(
            await session.scalars(
                select(RewriteTaskModel).where(
                    RewriteTaskModel.project_id == project_id,
                    RewriteTaskModel.trigger_source_id == chapter_id,
                    RewriteTaskModel.trigger_type == "outline_revision",
                    RewriteTaskModel.rewrite_strategy == "chapter_source_alignment_rewrite",
                )
            )
        )
        if not detected:
            return _result("not_applicable")
        project, chapter, draft, marker = await _context(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            marker=expected_plan_binding,
            scope=original_execution_scope_id,
            settings=settings,
            lock=True,
        )
        if draft.id != expected_draft_id or text_hash(draft.content_md) != expected_body_sha256:
            raise ValueError("alignment_dispatch_current_body_changed")
        tasks = list(
            await session.scalars(
                select(RewriteTaskModel)
                .where(
                    RewriteTaskModel.project_id == project_id,
                    RewriteTaskModel.trigger_source_id == chapter_id,
                    RewriteTaskModel.trigger_type == "outline_revision",
                    RewriteTaskModel.rewrite_strategy == "chapter_source_alignment_rewrite",
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        invalid = [
            task
            for task in tasks
            if not _alignment(task)
            and (task.metadata_json or {}).get("parent_draft_id") == str(draft.id)
        ]
        if invalid:
            return _result(
                "blocked", "alignment_dispatch_ungraded_contract_invalid", task=invalid[0]
            )
        all_alignment_tasks = tasks
        tasks = [task for task in tasks if _alignment(task)]
        exact = [task for task in tasks if _parent(task, draft, marker)]
        pending = [task for task in exact if task.status in _PENDING]
        if len(pending) > 1:
            return _result("blocked", "alignment_dispatch_ambiguous_tasks")
        relevant_pending = [
            task
            for task in all_alignment_tasks
            if task.status in _PENDING
            and (task.metadata_json or {}).get("parent_draft_id") == str(draft.id)
        ]
        if relevant_pending and not pending:
            return _result(
                "blocked", "alignment_dispatch_pending_source_not_current", task=relevant_pending[0]
            )
        # Exact current pending work takes priority. Historical output/marker A
        # cannot intercept a newly admitted pending task for marker B.
        for task in [] if pending else tasks:
            if (task.metadata_json or {}).get("source_plan_binding") != marker:
                continue
            workflow = await session.get(
                WorkflowRunModel, _workflow_id(task.id), populate_existing=True
            )
            if workflow is None:
                continue
            receipt = _receipt(
                workflow, task, chapter_id=chapter_id, scope=original_execution_scope_id
            )
            output = receipt.get("output") or {}
            if output.get("draft_id") == str(draft.id):
                return await _completed_route(
                    session, settings, workflow, task, chapter, draft, marker, receipt
                )
        if not pending:
            retained = [
                task
                for task in all_alignment_tasks
                if (task.metadata_json or {}).get("parent_draft_id") == str(draft.id)
            ]
            return (
                _result("blocked", "alignment_dispatch_terminal_or_stale_task", task=retained[0])
                if retained
                else _result("not_applicable")
            )
        task = pending[0]
        if int(task.attempts or 0) != 0:
            return _result("blocked", "alignment_dispatch_attempt_already_consumed", task=task)
        workflow_id = _workflow_id(task.id)
        workflow = next(
            (
                row
                for row in session.new
                if isinstance(row, WorkflowRunModel) and row.id == workflow_id
            ),
            None,
        )
        workflow = workflow or await session.get(
            WorkflowRunModel, workflow_id, populate_existing=True
        )
        if workflow is not None:
            receipt = _receipt(
                workflow, task, chapter_id=chapter_id, scope=original_execution_scope_id
            )
            if workflow.status == "running":
                return _result(
                    "inflight", "alignment_dispatch_already_claimed", workflow=workflow, task=task
                )
            if (
                workflow.status != "pending"
                or receipt.get("claim_id") is not None
                or receipt.get("task_source_identity") != _rewrite_task_source_identity(task)
            ):
                return _result(
                    "blocked", "alignment_dispatch_workflow_consumed", workflow=workflow, task=task
                )
            return _result("prepared", workflow=workflow, task=task)
        workflow = WorkflowRunModel(
            id=workflow_id,
            project_id=project_id,
            workflow_type=_TYPE,
            status="pending",
            scope_type="chapter",
            scope_id=chapter_id,
            requested_by=requested_by,
            current_step="outline_alignment_prepared",
            idempotency_key=f"{_SCHEMA}:{task.id}",
            metadata_json={
                "execution_scope_id": original_execution_scope_id,
                _KEY: {
                    "schema": _SCHEMA,
                    "task_id": str(task.id),
                    "project_id": str(project_id),
                    "chapter_id": str(chapter_id),
                    "input_draft_id": str(draft.id),
                    "input_body_sha256": text_hash(draft.content_md),
                    "source_plan_binding": deepcopy(marker),
                    "task_source_identity": deepcopy(_rewrite_task_source_identity(task)),
                    "original_execution_scope_id": original_execution_scope_id,
                    "attempts_before": int(task.attempts or 0),
                    "claim_id": None,
                    "output": None,
                },
            },
        )
        session.add(workflow)
        return _result("prepared", workflow=workflow, task=task)


async def execute_outline_alignment_dispatch(
    session_factory: async_sessionmaker[AsyncSession],
    settings: AppSettings,
    *,
    workflow_run_id: UUID,
    rewrite_executor: Callable[..., Awaitable[tuple[Any, Any]]] | None = None,
) -> OutlineAlignmentDispatch:
    """Own isolated claim/rewrite/recovery transactions. Never resend a claim."""
    from bestseller.services.reviews import (
        RewriteSourceRejected,
        _rewrite_task_source_identity,
        persist_rewrite_rejection,
        rewrite_chapter_from_task,
    )
    from bestseller.services.retained_request_guard import RetainedRequestRejected

    executor = rewrite_executor or rewrite_chapter_from_task
    # Claim is committed separately so rollback/crash cannot grant another send.
    async with session_factory() as claim_session:
        async with claim_session.begin():
            workflow = await claim_session.scalar(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.id == workflow_run_id,
                )
            )
            if workflow is None:
                return _result("blocked", "alignment_dispatch_workflow_not_durable")
            raw = (workflow.metadata_json or {}).get(_KEY) or {}
            task = await claim_session.get(RewriteTaskModel, UUID(raw["task_id"]))
            if task is None:
                return _result("blocked", "alignment_dispatch_task_missing", workflow=workflow)
            scope = raw.get("original_execution_scope_id")
            receipt = _receipt(workflow, task, chapter_id=workflow.scope_id, scope=scope)
            project, chapter, draft, marker = await _context(
                claim_session,
                project_id=task.project_id,
                chapter_id=workflow.scope_id,
                marker=receipt["source_plan_binding"],
                scope=scope,
                settings=settings,
                lock=True,
            )
            # Match preparation order: chapter/draft -> task -> workflow.
            task = await claim_session.get(
                RewriteTaskModel, task.id, with_for_update=True, populate_existing=True
            )
            workflow = await claim_session.get(
                WorkflowRunModel, workflow_run_id, with_for_update=True, populate_existing=True
            )
            receipt = _receipt(workflow, task, chapter_id=chapter.id, scope=scope)
            from bestseller.services.rewrite_outline_paid_recovery import active_paid_recovery
            retained = active_paid_recovery(workflow_run_id)
            if workflow.status == "completed":
                return await _completed_route(
                    claim_session, settings, workflow, task, chapter, draft, marker, receipt
                )
            if workflow.status == "running":
                return _result(
                    "inflight", "alignment_dispatch_already_claimed", workflow=workflow, task=task
                )
            if retained is not None:
                from bestseller.services.rewrite_outline_paid_recovery import (
                    HISTORY, SCHEMA, paid_alignment_state,
                )
                # Only the verified recovery context can reuse a consumed
                # claim. Its original failed row is append-only evidence.
                fresh = await paid_alignment_state(claim_session, settings,
                    workflow_run_id=workflow_run_id,
                    expected_claim_id=retained["expected_claim_id"])
                if fresh["witness_sha256"] != retained["witness_sha256"]:
                    raise ValueError("alignment_paid_recovery:witness_changed_before_claim")
                from bestseller.services.rewrite_outline_dispatch_recovery import _row
                history = list((workflow.metadata_json or {}).get(HISTORY) or [])
                history.append({"schema": SCHEMA, "prior_workflow": _row(workflow),
                    "original_claim_id": receipt["claim_id"],
                    "witness_sha256": fresh["witness_sha256"],
                    "paid_operation_key": fresh["binding"]["operation_key"],
                    "prepared_request": deepcopy(retained["prepared_request"]),
                    "actor": retained["actor"], "authorization_ref": retained["authorization_ref"],
                    "repair_ref": retained["repair_ref"], "credit_released": False,
                    "formal_adoption": False})
                workflow.metadata_json = {**workflow.metadata_json, HISTORY: history}
                claim_id = receipt["claim_id"]
            elif (
                workflow.status != "pending"
                or receipt.get("claim_id") is not None
                or task.status not in _PENDING
                or not _alignment(task)
                or int(task.attempts or 0) != receipt["attempts_before"]
                or int(task.attempts or 0) != 0
                or not _parent(task, draft, marker)
                or receipt["task_source_identity"] != _rewrite_task_source_identity(task)
            ):
                return _result(
                    "blocked", "alignment_dispatch_not_executable", workflow=workflow, task=task
                )
            else:
                claim_id = str(uuid4())
            receipt["claim_id"] = claim_id
            workflow.status = "running"
            workflow.current_step = "outline_alignment_rewrite"
            workflow.metadata_json = {**workflow.metadata_json, _KEY: receipt}
    # From here on every exception leaves a durable consumed identity.
    try:
        async with session_factory() as rewrite_session:
            # complete_text commits at its pre-provider checkpoint. Let the
            # session autobegin after each checkpoint; an enclosing begin()
            # context would stay closed when the paid result returns.
            workflow = await rewrite_session.get(WorkflowRunModel, workflow_run_id)
            task = await rewrite_session.get(RewriteTaskModel, UUID(receipt["task_id"]))
            current = _receipt(workflow, task, chapter_id=workflow.scope_id, scope=scope)
            if workflow.status != "running" or current.get("claim_id") != claim_id:
                raise ValueError("alignment_dispatch_claim_changed")
            project, chapter, draft, marker = await _context(
                rewrite_session,
                project_id=task.project_id,
                chapter_id=workflow.scope_id,
                marker=current["source_plan_binding"],
                scope=scope,
                settings=settings,
                lock=False,
            )
            if (
                not _parent(task, draft, marker)
                or task.status not in _PENDING
                or _rewrite_task_source_identity(task) != current["task_source_identity"]
            ):
                raise ValueError("alignment_dispatch_source_changed_before_rewrite")
            with _bind_control_guard(session_factory, project.id):
                rewritten, returned_task = await executor(
                    rewrite_session,
                    project.slug,
                    chapter.chapter_number,
                    rewrite_task_id=task.id,
                    settings=settings,
                    workflow_run_id=workflow.id,
                )
            await rewrite_session.flush()
            # Read DB columns/rows after actual rewrite; do not trust callback claims.
            project, chapter, selected, marker = await _context(
                rewrite_session,
                project_id=task.project_id,
                chapter_id=workflow.scope_id,
                marker=current["source_plan_binding"],
                scope=scope,
                settings=settings,
                lock=True,
                admit=False,
            )
            # Only post-provider commit takes WF/parent locks. A pre-send
            # WF FOR UPDATE would block the independent LlmRun FK KEY SHARE.
            workflow = await rewrite_session.get(
                WorkflowRunModel, workflow_run_id, with_for_update=True, populate_existing=True
            )
            saved = _receipt(workflow, task, chapter_id=workflow.scope_id, scope=scope)
            if workflow.status != "running" or saved.get("claim_id") != claim_id:
                raise ValueError("alignment_dispatch_claim_changed_before_commit")
            # Closing SEND permission must not erase a returned failed task,
            # candidate evidence or actual attempt. Persist results, block
            # further work, and leave the original ledger untouched.
            from bestseller.services.budget import ExecutionBudgetError
            from bestseller.services.outline_revision_lifecycle import _runnable

            stopped = None
            try:
                await _runnable(rewrite_session, project)
            except (ValueError, ExecutionBudgetError) as stop:
                stopped = getattr(stop, "code", None) or str(stop)
            valid = (
                returned_task.id == task.id
                and rewritten.id == selected.id
                and task.status == "completed"
                and selected.id != draft.id
                and int(task.attempts or 0) == 1
                and text_hash(selected.content_md) != current["input_body_sha256"]
                and (task.metadata_json or {}).get("rewritten_chapter_draft_id")
                == str(selected.id)
                and stopped is None
            )
            workflow.status = "completed" if valid else "machine_blocked"
            workflow.current_step = (
                "strict_new_body_review_required" if valid else "alignment_rewrite_not_promoted"
            )
            if valid:
                current["output"] = {
                    "draft_id": str(selected.id),
                    "body_sha256": text_hash(selected.content_md),
                    "evidence_status": "UNGRADED",
                }
            else:
                current["blocked_reason"] = stopped or "alignment_dispatch_rewrite_not_promoted"
            workflow.metadata_json = {**workflow.metadata_json, _KEY: current}
            outcome = (
                _handoff(workflow, task, selected, marker, current)
                if valid
                else _result("blocked", current["blocked_reason"], workflow=workflow, task=task)
            )
            await rewrite_session.commit()
            return outcome
    except (Exception, RetainedRequestRejected) as error:
        async with session_factory() as recovery:
            async with recovery.begin():
                saved = await recovery.get(WorkflowRunModel, workflow_run_id, with_for_update=True)
                saved_receipt = deepcopy((saved.metadata_json or {}).get(_KEY) or {})
                if saved.status == "running" and saved_receipt.get("claim_id") == claim_id:
                    saved.status = "machine_blocked"
                    saved.current_step = "alignment_rewrite_failed_requires_reconciliation"
                    saved_receipt["blocked_reason"] = type(error).__name__
                    saved.metadata_json = {**saved.metadata_json, _KEY: saved_receipt}
                    if isinstance(error, RewriteSourceRejected):
                        await persist_rewrite_rejection(recovery, error)
        raise
