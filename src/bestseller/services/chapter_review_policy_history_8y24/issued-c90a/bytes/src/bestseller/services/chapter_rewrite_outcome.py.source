"""Retain a rejected editor result without granting another execution attempt."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ChapterStatus, WorkflowStatus
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services.rewrite_patch import text_hash


async def retain_failed_chapter_rewrite_outcome(
    session: AsyncSession,
    *,
    workflow: WorkflowRunModel,
    chapter: ChapterModel,
    current_draft: ChapterDraftVersionModel,
    rewrite_task: RewriteTaskModel,
) -> dict[str, Any] | None:
    """Mark a returned hard rejection as terminal for this pipeline invocation.

    Call after the executor returns and its outcome step is recorded. A completed
    task is a no-op. On rejection, checkpoint in the caller, then break the loop
    with requires_human_review=True. This function never commits, creates tasks,
    changes attempts, promotes drafts, or authorizes a retry. The caller retains
    ownership of source/concurrency guards and the surrounding transaction.
    Missing historical candidate hashes remain observations, never historical
    generation proof; present but conflicting bindings are rejected.
    """
    if rewrite_task.status == "completed":
        return None
    if rewrite_task.status != "failed":
        raise ValueError("chapter_rewrite_outcome_not_terminal")
    metadata = rewrite_task.metadata_json or {}
    if (
        workflow.project_id != chapter.project_id
        or workflow.scope_type != "chapter"
        or workflow.scope_id != chapter.id
        or rewrite_task.project_id != chapter.project_id
        or rewrite_task.trigger_source_id != chapter.id
        or current_draft.project_id != chapter.project_id
        or current_draft.chapter_id != chapter.id
        or not current_draft.is_current
        or str(metadata.get("parent_draft_id") or "") != str(current_draft.id)
        or ("draft_id" in metadata and str(metadata["draft_id"]) != str(current_draft.id))
        or metadata.get("parent_draft_hash") != text_hash(current_draft.content_md or "")
        or (
            "preserved_current_chapter_draft_version_no" in metadata
            and metadata["preserved_current_chapter_draft_version_no"] != current_draft.version_no
        )
        or str(metadata.get("preserved_current_chapter_draft_id") or "") != str(current_draft.id)
    ):
        raise ValueError("chapter_rewrite_outcome_source_mismatch")
    try:
        candidate_id = UUID(str(metadata["candidate_chapter_draft_id"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("chapter_rewrite_outcome_candidate_missing") from error
    # Reading a result must not flush unrelated caller work. The executor has
    # already flushed its candidate; the caller alone decides when to commit.
    with session.no_autoflush:
        candidate = await session.get(ChapterDraftVersionModel, candidate_id)
    if (
        candidate is None
        or candidate.project_id != chapter.project_id
        or candidate.chapter_id != chapter.id
        or candidate.id == current_draft.id
        or candidate.is_current
        or candidate.version_no != metadata.get("candidate_chapter_draft_version_no")
    ):
        raise ValueError("chapter_rewrite_outcome_candidate_mismatch")
    candidate_hash = text_hash(candidate.content_md or "")
    candidate_llm_id = str(candidate.llm_run_id) if candidate.llm_run_id else None
    if (
        "candidate_body_sha256" in metadata and metadata["candidate_body_sha256"] != candidate_hash
    ) or (
        "candidate_llm_run_id" in metadata and metadata["candidate_llm_run_id"] != candidate_llm_id
    ):
        raise ValueError("chapter_rewrite_outcome_candidate_binding_mismatch")
    receipt = {
        "schema": "chapter-rewrite-outcome.v1",
        "outcome": "rejected_current_preserved",
        "workflow_run_id": str(workflow.id),
        "project_id": str(chapter.project_id),
        "chapter_id": str(chapter.id),
        "rewrite_task_id": str(rewrite_task.id),
        "parent_task_id": str(rewrite_task.parent_task_id) if rewrite_task.parent_task_id else None,
        "review_report_id": metadata.get("review_report_id"),
        "current_draft_id": str(current_draft.id),
        "current_body_sha256": text_hash(current_draft.content_md or ""),
        "candidate_draft_id": str(candidate.id),
        "candidate_version_no": candidate.version_no,
        "candidate_body_sha256": candidate_hash,
        "candidate_llm_run_id": candidate_llm_id,
        "candidate_body_proof": (
            "retained_hash_matched"
            if "candidate_body_sha256" in metadata
            else "checkpoint_observation_only"
        ),
        "candidate_llm_reference_matched": (
            "candidate_llm_run_id" in metadata and candidate_llm_id is not None
        ),
        "task_attempts": int(rewrite_task.attempts or 0),
        "automatic_retry_authorized": False,
    }
    workflow_metadata = deepcopy(workflow.metadata_json or {})
    outcomes = dict(workflow_metadata.get("chapter_rewrite_outcomes") or {})
    prior = outcomes.get(str(rewrite_task.id))
    if prior is not None and prior != receipt:
        raise ValueError("chapter_rewrite_outcome_receipt_changed")
    if workflow.status in {WorkflowStatus.COMPLETED.value, WorkflowStatus.CANCELLED.value}:
        raise ValueError("chapter_rewrite_outcome_workflow_terminal")
    outcomes[str(rewrite_task.id)] = receipt
    workflow.metadata_json = {
        **workflow_metadata,
        "chapter_rewrite_outcomes": outcomes,
        "blocked_after_chapter_rewrite_quality_gate": True,
        "requires_human_review": True,
        "chapter_draft_id": str(current_draft.id),
        "chapter_draft_version_no": current_draft.version_no,
        "rewrite_task_id": str(rewrite_task.id),
    }
    workflow.status = WorkflowStatus.MACHINE_BLOCKED.value
    workflow.current_step = "machine_repair_required"
    chapter.status = ChapterStatus.REVISION.value
    chapter.production_state = "blocked"
    return deepcopy(receipt)
