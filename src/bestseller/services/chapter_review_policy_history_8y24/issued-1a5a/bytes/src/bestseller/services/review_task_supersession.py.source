"""Retire review tasks without replacing their instructions or historical data."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterDraftVersionModel, ReviewReportModel, RewriteTaskModel


async def supersede_review_tasks_preserving_history(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    report_id: UUID,
    draft_id: UUID,
    except_task_id: UUID | None = None,
    automatic_only: bool = False,
    disposition: Literal["superseded", "cancelled"] = "superseded",
    reason: str = "current_chapter_review_passed",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage changes only; the caller owns all flush/commit/rollback decisions.

    The caller must flush its new report/draft and any edits to existing pending
    tasks first. FOR UPDATE plus populate_existing reads current task history;
    dirty scoped rows are refused rather than overwriting caller changes.
    Automatic classification recognizes the actual chapter-review producer's
    trigger/strategy and original report/draft linkage, not prose or an auto flag.
    This helper does not decide whether a review grade authorizes retirement.
    """
    if (
        disposition not in {"superseded", "cancelled"}
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValueError("review_task_retirement_disposition_or_reason_invalid")
    if details is not None and not isinstance(details, dict):
        raise ValueError("review_task_retirement_details_invalid")
    extra = deepcopy(details) if details is not None else {}
    json.dumps(extra, allow_nan=False)
    pending = {"pending", "queued"}
    # Exclude in-memory terminal changes as well: a second invocation before
    # caller flush must not reload the still-pending DB image and undo them.
    excluded = {except_task_id} if except_task_id is not None else set()
    for obj in session.dirty:
        if not isinstance(obj, RewriteTaskModel):
            continue
        if obj.status not in pending:
            excluded.add(obj.id)
        elif (
            obj.id not in excluded
            and obj.project_id == project_id
            and obj.trigger_source_id == chapter_id
        ):
            raise ValueError("review_task_retirement_caller_flush_required")

    with session.no_autoflush:
        report = (
            await session.execute(
                select(
                    ReviewReportModel.project_id,
                    ReviewReportModel.target_type,
                    ReviewReportModel.target_id,
                    ReviewReportModel.structured_output,
                ).where(ReviewReportModel.id == report_id)
            )
        ).one_or_none()
        draft = (
            await session.execute(
                select(
                    ChapterDraftVersionModel.project_id,
                    ChapterDraftVersionModel.chapter_id,
                    ChapterDraftVersionModel.content_md,
                ).where(ChapterDraftVersionModel.id == draft_id)
            )
        ).one_or_none()
        if (
            report is None
            or draft is None
            or report.project_id != project_id
            or draft.project_id != project_id
            or report.target_type != "chapter"
            or report.target_id != chapter_id
            or draft.chapter_id != chapter_id
            or not isinstance(report.structured_output, Mapping)
            or report.structured_output.get("draft_id") != str(draft_id)
        ):
            raise ValueError("review_task_retirement_review_draft_binding_invalid")
        body = draft.content_md or ""
        receipt = {
            "schema": "review-task-retirement.v1",
            "project_id": str(project_id),
            "chapter_id": str(chapter_id),
            "report_id": str(report_id),
            "draft_id": str(draft_id),
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "disposition": disposition,
            "reason": reason,
            "details": extra,
        }
        fingerprint = report.structured_output.get("reviewed_body_fingerprint")
        if fingerprint is not None:
            normalized = body.replace("\r\n", "\n").replace("\r", "\n")
            expected = {
                "schema": "chapter_review_body_v1",
                "algorithm": "sha256",
                "normalization": "newlines_lf_only",
                "digest": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            }
            if fingerprint != expected:
                raise ValueError("review_task_retirement_review_body_changed")
        statement = (
            select(RewriteTaskModel)
            .where(
                RewriteTaskModel.project_id == project_id,
                RewriteTaskModel.trigger_source_id == chapter_id,
                RewriteTaskModel.status.in_(pending),
            )
            .order_by(RewriteTaskModel.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if excluded:
            statement = statement.where(RewriteTaskModel.id.not_in(excluded))
        tasks = list((await session.scalars(statement)).all())
        updates: list[tuple[RewriteTaskModel, dict[str, Any]]] = []
        skipped: list[str] = []
        for task in tasks:
            original = task.metadata_json
            if not isinstance(original, Mapping):
                raise ValueError("review_task_retirement_metadata_not_mapping")
            metadata = deepcopy(dict(original))
            if automatic_only:
                source_id = metadata.get("review_report_id")
                source_draft = metadata.get("draft_id")
                try:
                    source_uuid = UUID(str(source_id))
                except (ValueError, TypeError, AttributeError):
                    source_uuid = None
                automatic = (
                    task.trigger_type == "chapter_review"
                    and task.rewrite_strategy == "chapter_coherence_bridge_rewrite"
                    and metadata.get("chapter_id") == str(chapter_id)
                    and source_uuid is not None
                    and source_draft
                    and metadata.get("parent_draft_id") == source_draft
                    and isinstance(metadata.get("parent_draft_hash"), str)
                    and len(metadata["parent_draft_hash"]) == 64
                )
                source = None
                if automatic:
                    source = (
                        await session.execute(
                            select(
                                ReviewReportModel.structured_output,
                            ).where(
                                ReviewReportModel.id == source_uuid,
                                ReviewReportModel.project_id == project_id,
                                ReviewReportModel.target_type == "chapter",
                                ReviewReportModel.target_id == chapter_id,
                                ReviewReportModel.verdict == "rewrite",
                            )
                        )
                    ).one_or_none()
                if (
                    source is None
                    or not isinstance(source.structured_output, Mapping)
                    or source.structured_output.get("draft_id") != source_draft
                ):
                    skipped.append(str(task.id))
                    continue
            history = metadata.get("supersession_receipts", [])
            if not isinstance(history, list):
                raise ValueError("review_task_retirement_receipt_history_not_list")
            if receipt not in history:
                metadata["supersession_receipts"] = [*history, deepcopy(receipt)]
            updates.append((task, metadata))
        # Validate all selected rows before staging any mutation.
        for task, metadata in updates:
            task.metadata_json = metadata
            task.status = disposition
    ids = [str(task.id) for task, _ in updates]
    return {
        "changed_count": len(ids),
        "changed_task_ids": ids,
        "superseded_task_ids": ids if disposition == "superseded" else [],
        "cancelled_task_ids": ids if disposition == "cancelled" else [],
        "skipped_task_ids": skipped,
        "receipt": receipt,
    }
