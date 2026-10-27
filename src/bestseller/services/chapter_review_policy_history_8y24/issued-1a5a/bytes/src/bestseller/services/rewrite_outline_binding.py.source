"""Bind chapter rewrite instructions to an admitted ordinary outline revision.

Admission/grade verification and outline materialization belong to the caller.
This module never creates grades, adoption, budget credit, or transactions.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, RewriteTaskModel
from bestseller.services.rewrite_patch import text_hash

PLAN_MARKER = "outline_source_admission"
TASK_BINDING = "source_plan_binding"
_PLAN_KEYS = {"artifact_id", "workflow_run_id", "projection_hash", "source_content_sha256"}
_PENDING = {"pending", "queued"}
_ALIGNMENT_SCHEMA = "outline-revision-rewrite.v1"
_ALIGNMENT_INSTRUCTIONS = (
    "Align the complete current chapter with the current approved chapter outline "
    "and explicit author constraints, using the selected current sources. Preserve "
    "applicable established facts and author choices. Resolve obsolete outline "
    "requirements against those sources; do not reuse superseded task instructions "
    "or invent story facts. This is an UNGRADED source-alignment request. The result "
    "requires subsequent formal review and explicit adoption under the existing gates."
)


def validate_source_plan_binding(value: Any) -> dict[str, str]:
    """Require the exact technical marker; never infer or normalize provenance."""
    if not isinstance(value, dict) or set(value) != _PLAN_KEYS:
        raise ValueError("rewrite_outline_marker_invalid")
    for key in ("artifact_id", "workflow_run_id"):
        raw = value[key]
        try:
            valid = isinstance(raw, str) and str(UUID(raw)) == raw
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("rewrite_outline_marker_invalid")
    for key in ("projection_hash", "source_content_sha256"):
        raw = value[key]
        if (
            not isinstance(raw, str)
            or len(raw) != 64
            or any(c not in "0123456789abcdef" for c in raw)
        ):
            raise ValueError("rewrite_outline_marker_invalid")
    return deepcopy(value)


async def rewrite_outline_binding_rejection(
    session: AsyncSession,
    *,
    task: RewriteTaskModel,
    chapter_id: UUID,
    project_id: UUID,
    phase: Literal["entry", "commit"],
) -> dict[str, Any] | None:
    """Read columns afresh, returning a detached receipt on any lost binding.

    The commit caller holds the chapter row lock until its transaction ends.
    The private entry snapshot prevents rebinding old instructions during a call.
    Unmarked chapters with unbound tasks retain the previous body/report contract.
    """
    if phase not in {"entry", "commit"}:
        raise ValueError("rewrite_outline_phase_invalid")
    row = (
        await session.execute(
            select(ChapterModel.project_id, ChapterModel.metadata_json)
            .where(ChapterModel.id == chapter_id)
            .execution_options(autoflush=False)
        )
    ).one_or_none()
    task_metadata = task.metadata_json or {}
    binding_present = TASK_BINDING in task_metadata
    binding = deepcopy(task_metadata.get(TASK_BINDING))
    entry_present = hasattr(task, "_rewrite_plan_binding_before_execution")
    entry_binding = deepcopy(getattr(task, "_rewrite_plan_binding_before_execution", None))
    current = None
    reason = None
    if row is None or row.project_id != project_id or task.project_id != project_id:
        reason = "outline_source_scope_invalid"
    else:
        metadata = row.metadata_json
        guarded = (
            binding_present
            or entry_binding is not None
            or (isinstance(metadata, Mapping) and PLAN_MARKER in metadata)
        )
        if guarded and task.trigger_source_id != chapter_id:
            reason = "outline_source_scope_invalid"
        elif not isinstance(metadata, Mapping):
            reason = "outline_source_marker_invalid"
        elif PLAN_MARKER in metadata:
            current = deepcopy(metadata[PLAN_MARKER])
            try:
                validate_source_plan_binding(current)
            except (ValueError, TypeError, AttributeError):
                reason = "outline_source_marker_invalid"
            if reason is None and (not binding_present or binding != current):
                reason = "outline_source_binding_not_current"
        elif binding_present:
            reason = "outline_source_marker_missing"
    if phase == "commit" and reason is None:
        if (current is not None and not entry_present) or (
            entry_present and entry_binding != current
        ):
            reason = "outline_source_changed_before_commit"
    if reason is None:
        if phase == "entry":
            task._rewrite_plan_binding_before_execution = deepcopy(current)
        return None
    return {
        "reason": reason,
        "phase": phase,
        "current_plan_binding": deepcopy(current),
        "task_plan_binding": binding,
        "entry_plan_binding": entry_binding,
    }


def _exact_parent(task: RewriteTaskModel, draft_id: UUID, digest: str) -> bool:
    metadata = task.metadata_json
    return (
        isinstance(metadata, Mapping)
        and metadata.get("parent_draft_id") == str(draft_id)
        and metadata.get("parent_draft_hash") == digest
        and ("draft_id" not in metadata or metadata["draft_id"] == str(draft_id))
    )


async def stage_outline_revision_rewrite_tasks(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    draft_id: UUID,
    expected_body_sha256: str,
    expected_plan_binding: dict[str, str],
) -> dict[str, Any]:
    """Stage exact-parent retirement and one explicit UNGRADED alignment task.

    Caller first verifies an actual-grade ordinary outline admission, reconciles
    only unadopted chapters, and flushes that migration and any scoped edits.
    Here the marker, ownership, current body and absence of adoption are rechecked
    independently. No flush/commit/rollback is issued, including on repeated calls.
    Old task fields survive; status changes are recorded with original status and
    attempts. The newest affected task (or retained lineage leaf) is the singular
    parent; all affected IDs remain in receipts. Chapter/global counters and
    convergence are untouched. Source-invalidated tasks are never resurrected.
    """
    from bestseller.services.chapter_scene_contract_materializer import (
        chapter_contract_has_adopted_source,
    )

    expected = validate_source_plan_binding(expected_plan_binding)
    excluded: set[UUID] = set()
    local_terminal = []
    for obj in session.dirty:
        if isinstance(obj, ChapterModel) and obj.id == chapter_id:
            raise ValueError("rewrite_outline_caller_flush_required")
        if isinstance(obj, ChapterDraftVersionModel) and obj.chapter_id == chapter_id:
            raise ValueError("rewrite_outline_caller_flush_required")
        if (
            isinstance(obj, RewriteTaskModel)
            and obj.project_id == project_id
            and obj.trigger_source_id == chapter_id
        ):
            if obj.status in _PENDING:
                raise ValueError("rewrite_outline_caller_flush_required")
            excluded.add(obj.id)
            local_terminal.append(obj)
    with session.no_autoflush:
        chapter = await session.scalar(
            select(ChapterModel)
            .where(
                ChapterModel.id == chapter_id,
                ChapterModel.project_id == project_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if chapter is None:
            raise ValueError("rewrite_outline_project_chapter_mismatch")
        metadata = chapter.metadata_json
        current = validate_source_plan_binding(
            metadata.get(PLAN_MARKER) if isinstance(metadata, Mapping) else None
        )
        if current != expected:
            raise ValueError("rewrite_outline_marker_changed")
        draft = (
            await session.execute(
                select(ChapterDraftVersionModel.id, ChapterDraftVersionModel.content_md)
                .where(
                    ChapterDraftVersionModel.project_id == project_id,
                    ChapterDraftVersionModel.chapter_id == chapter_id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
                .with_for_update()
            )
        ).one_or_none()
        if (
            draft is None
            or draft.id != draft_id
            or not (draft.content_md or "").strip()
            or text_hash(draft.content_md) != expected_body_sha256
        ):
            raise ValueError("rewrite_outline_current_body_changed")
        if await chapter_contract_has_adopted_source(session, chapter=chapter) is not False:
            raise ValueError("rewrite_outline_adopted_source")
        query = (
            select(RewriteTaskModel)
            .where(
                RewriteTaskModel.project_id == project_id,
                RewriteTaskModel.trigger_source_id == chapter_id,
            )
            .order_by(RewriteTaskModel.created_at.desc(), RewriteTaskModel.id.desc())
        )
        if excluded:
            query = query.where(RewriteTaskModel.id.not_in(excluded))
        tasks = list(
            await session.scalars(query.with_for_update().execution_options(populate_existing=True))
        )
        # Include a prior unflushed invocation without triggering an autoflush.
        tasks = [
            *local_terminal,
            *(
                obj
                for obj in session.new
                if isinstance(obj, RewriteTaskModel)
                and obj.project_id == project_id
                and obj.trigger_source_id == chapter_id
            ),
            *tasks,
        ]
        existing = None
        affected = []
        same_parent = []
        for task in tasks:
            if not _exact_parent(task, draft_id, expected_body_sha256):
                continue
            same_parent.append(task)
            task_metadata = task.metadata_json
            if task_metadata.get(TASK_BINDING) == current:
                alignment = task_metadata.get("source_alignment")
                if (
                    existing is None
                    and task.status not in {"superseded", "stale"}
                    and task.trigger_type == "outline_revision"
                    and task.rewrite_strategy == "chapter_source_alignment_rewrite"
                    and task.instructions == _ALIGNMENT_INSTRUCTIONS
                    and isinstance(alignment, Mapping)
                    and alignment.get("schema") == _ALIGNMENT_SCHEMA
                    and alignment.get("evidence_status") == "UNGRADED"
                    and alignment.get("requires_formal_review") is True
                    and alignment.get("requires_formal_adoption") is True
                ):
                    existing = task
                continue
            if task.status not in _PENDING:
                continue
            history = task_metadata.get("supersession_receipts", [])
            if not isinstance(history, list):
                raise ValueError("rewrite_outline_supersession_history_invalid")
            affected.append(task)
        # A reverted outline needs a successor, never resurrection of an A task
        # invalidated during A -> B -> A. Keep a historical parent even when B
        # is already terminal and therefore cannot be retired by this helper.
        ancestor_ids = {task.parent_task_id for task in same_parent if task.parent_task_id}
        leaves = [task for task in same_parent if task.id not in ancestor_ids]
        lineage = affected or (leaves or same_parent)[:1]
        replacement = existing or RewriteTaskModel(
            id=uuid4(),
            project_id=project_id,
            parent_task_id=lineage[0].id if lineage else None,
            trigger_type="outline_revision",
            trigger_source_id=chapter_id,
            rewrite_strategy="chapter_source_alignment_rewrite",
            priority=min((task.priority for task in affected), default=5),
            status="pending",
            attempts=0,
            instructions=_ALIGNMENT_INSTRUCTIONS,
            context_required=[
                "current_approved_outline",
                "explicit_author_constraints",
                "current_complete_chapter",
                "verified_adopted_continuity",
            ],
            metadata_json={
                "chapter_id": str(chapter_id),
                "draft_id": str(draft_id),
                "parent_draft_id": str(draft_id),
                "parent_draft_hash": expected_body_sha256,
                TASK_BINDING: deepcopy(current),
                "source_alignment": {
                    "schema": _ALIGNMENT_SCHEMA,
                    "evidence_status": "UNGRADED",
                    "requires_formal_review": True,
                    "requires_formal_adoption": True,
                    "lineage_task_ids": [str(task.id) for task in lineage],
                    "prior_task_attempts": {str(task.id): task.attempts for task in lineage},
                },
            },
        )
        updates = []
        for task in affected:
            task_metadata = deepcopy(dict(task.metadata_json))
            receipt = {
                "schema": _ALIGNMENT_SCHEMA,
                "reason": "approved_outline_source_changed",
                "disposition": "superseded",
                "previous_status": task.status,
                "previous_attempts": task.attempts,
                "previous_parent_task_id": str(task.parent_task_id)
                if task.parent_task_id
                else None,
                "replacement_task_id": str(replacement.id),
                "project_id": str(project_id),
                "chapter_id": str(chapter_id),
                "draft_id": str(draft_id),
                "body_sha256": expected_body_sha256,
                "previous_plan_binding": deepcopy(task_metadata.get(TASK_BINDING)),
                "replacement_plan_binding": deepcopy(current),
                "evidence_status": "UNGRADED",
            }
            task_metadata["supersession_receipts"] = [
                *task_metadata.get("supersession_receipts", []),
                receipt,
            ]
            updates.append((task, task_metadata))
        # Complete validation before the first task mutation.
        if existing is None:
            session.add(replacement)
        for task, task_metadata in updates:
            task.metadata_json = task_metadata
            task.status = "superseded"
    return {
        "replacement_task_id": str(replacement.id),
        "created": existing is None,
        "superseded_task_ids": [str(task.id) for task in affected],
        "evidence_status": "UNGRADED",
    }
