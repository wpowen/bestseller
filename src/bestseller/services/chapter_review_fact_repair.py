"""Current deny-only disposition of a retained review, without regrading history."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ChapterStatus
from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, RewriteTaskModel
from bestseller.services.chapter_verified_fact_disposition import (
    apply_verified_fact_disposition,
    decide_verified_fact_disposition,
)

SCHEMA = "retained-review-fact-repair.v1"


async def retained_fact_review_result(
    session: AsyncSession,
    *,
    settings,
    draft: ChapterDraftVersionModel,
    review_tuple: tuple,
) -> tuple:
    """Reuse paid criticism and create at most one ordinary budgeted repair task.

    The normal pipeline owns current policy/semantic/native-input admission and
    its existing source locks. Preflight/alias readers keep returning original
    receipts; only the normal completed-review consumer calls this producer.
    No report, quality row, paid attempt, namespace, budget grant or cycle is
    updated here. The caller commits the source-bound task and active counter.
    """
    from bestseller.services.reviews import chapter_review_body_fingerprint, rewrite_source_binding

    result, report, quality, original_task = review_tuple
    decision = decide_verified_fact_disposition(
        content_md=draft.content_md or "",
        evidence=result.evidence_summary,
    )
    if decision["status"] not in {"blocked", "unknown"}:
        return review_tuple
    effective = apply_verified_fact_disposition(result, content_md=draft.content_md or "")
    if decision["status"] == "unknown" or effective.verdict == "unknown":
        return effective, report, quality, None

    payload = report.structured_output or {}
    if (
        report.project_id != draft.project_id
        or report.target_type != "chapter"
        or report.target_id != draft.chapter_id
        or payload.get("draft_id") != str(draft.id)
        or payload.get("reviewed_body_fingerprint")
        != chapter_review_body_fingerprint(draft.content_md or "")
        or quality.review_report_id != report.id
        or quality.chapter_draft_version_id != draft.id
        or quality.project_id != draft.project_id
        or quality.target_id != draft.chapter_id
        or quality.target_type != "chapter"
    ):
        raise ValueError("retained_fact_review_binding_mismatch")
    for key in ("commercial_action_projection", "llm_commercial_judge"):
        if (payload.get("evidence_summary") or {}).get(key) != result.evidence_summary.get(key):
            raise ValueError("retained_fact_review_report_changed")
        if (quality.evidence_summary or {}).get(key) != result.evidence_summary.get(key):
            raise ValueError("retained_fact_review_quality_changed")

    chapter = await session.scalar(
        select(ChapterModel)
        .where(
            ChapterModel.id == draft.chapter_id,
            ChapterModel.project_id == draft.project_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if chapter is None:
        raise ValueError("retained_fact_review_chapter_missing")
    current = await session.scalar(
        select(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.chapter_id == draft.chapter_id,
            ChapterDraftVersionModel.project_id == draft.project_id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        current is None
        or current.id != draft.id
        or chapter_review_body_fingerprint(current.content_md or "")
        != payload["reviewed_body_fingerprint"]
    ):
        raise ValueError("retained_fact_review_source_changed")
    source_binding = rewrite_source_binding(current, chapter=chapter)
    if original_task is not None:
        metadata = original_task.metadata_json or {}
        if any(metadata.get(k) != v for k, v in source_binding.items()) or metadata.get(
            "review_report_id"
        ) != str(report.id):
            raise ValueError("retained_fact_review_task_changed")
        return (
            effective,
            report,
            quality,
            original_task if original_task.status in {"pending", "queued"} else None,
        )

    projection_hash = result.evidence_summary["commercial_action_projection"]["projection_sha256"]
    task_id = uuid5(
        NAMESPACE_URL, f"{SCHEMA}:{draft.project_id}:{draft.id}:{report.id}:{projection_hash}"
    )
    retained = await session.get(RewriteTaskModel, task_id)
    if retained is not None:
        metadata = retained.metadata_json or {}
        if (
            retained.project_id != draft.project_id
            or retained.trigger_source_id != chapter.id
            or metadata.get("derived_fact_repair_schema") != SCHEMA
            or metadata.get("projection_sha256") != projection_hash
            or metadata.get("review_report_id") != str(report.id)
            or any(metadata.get(k) != v for k, v in source_binding.items())
        ):
            raise ValueError("retained_fact_repair_identity_changed")
        return (
            effective,
            report,
            quality,
            retained if retained.status in {"pending", "queued"} else None,
        )

    metadata = dict(chapter.metadata_json or {})
    total = metadata.get("chapter_review_attempts_active", 0)
    active_cycle = metadata.get("chapter_review_active_cycle") or {}
    if not isinstance(active_cycle, Mapping):
        raise ValueError("retained_fact_repair_counter_invalid")
    base = active_cycle.get("attempts_base", 0)
    if type(total) is not int or total < 0 or type(base) is not int or base < 0:
        raise ValueError("retained_fact_repair_counter_invalid")
    used = max(0, total - base)
    budget = max(int(getattr(settings.pipeline, "chapter_auto_repair_max_attempts", 3) or 3), 1)
    if used >= budget:
        chapter.status = ChapterStatus.REVIEW.value
        chapter.production_state = "repair_exhausted"
        await session.flush()
        return effective, report, quality, None
    task = RewriteTaskModel(
        id=task_id,
        project_id=draft.project_id,
        trigger_type="chapter_review",
        trigger_source_id=chapter.id,
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        priority=4,
        status="pending",
        instructions=effective.rewrite_instructions,
        context_required=[
            "chapter_context",
            "current_chapter_draft",
            "scene_summaries",
            "review_findings",
        ],
        metadata_json={
            "chapter_id": str(chapter.id),
            **source_binding,
            "review_report_id": str(report.id),
            "editor_prompt_protocol": "3.1-lossless-source-time",
            "attempt_index": used + 1,
            "attempt_budget": budget,
            "derived_fact_repair_schema": SCHEMA,
            "projection_sha256": projection_hash,
            "original_review_verdict": result.verdict,
            "fact_disposition": decision,
            "original_paid_review_preserved": True,
        },
    )
    session.add(task)
    chapter.status = ChapterStatus.REVISION.value
    chapter.metadata_json = {**metadata, "chapter_review_attempts_active": total + 1}
    await session.flush()
    return effective, report, quality, task
