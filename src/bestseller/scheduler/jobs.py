"""Publishing scheduler jobs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    PublishingHistoryModel,
    PublishingPlatformModel,
    PublishingScheduleModel,
)
from bestseller.services.exports import (
    collect_publication_blockers,
    load_publication_comparison_payloads,
)
from bestseller.services.publishing.base import ChapterPublishMeta
from bestseller.services.publishing.registry import get_adapter
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

# ── Retry / circuit-breaker constants ──────────────────────────
# Job is an APScheduler cron short task — seconds-level inline retry only;
# minute/hour-level retry is handled by the next cron tick.
_MAX_INLINE_RETRIES = 3
_INLINE_RETRY_BASE_SECONDS = 5
_CIRCUIT_BREAK_AFTER = 5  # consecutive failure ticks → pause schedule


def _consecutive_failures_from_metadata(metadata: object) -> int:
    if not isinstance(metadata, dict):
        return 0
    try:
        return max(0, int(metadata.get("consecutive_failures", 0)))
    except (TypeError, ValueError):
        return 0


async def publish_next_chapter(
    session: AsyncSession,
    settings: AppSettings,
    schedule_id: UUID,
) -> bool:
    """Publish the next unpublished chapter for a given schedule. Returns True on success."""
    # Load schedule
    sched_result = await session.execute(
        select(PublishingScheduleModel)
        .where(PublishingScheduleModel.id == schedule_id)
        .with_for_update()
    )
    schedule = sched_result.scalar_one_or_none()
    if schedule is None or schedule.status != "active":
        logger.warning("Schedule %s not found or not active", schedule_id)
        return False

    # Load platform + project
    plat_result = await session.execute(
        select(PublishingPlatformModel).where(PublishingPlatformModel.id == schedule.platform_id)
    )
    platform = plat_result.scalar_one_or_none()
    if platform is None:
        logger.error("Platform %s not found for schedule %s", schedule.platform_id, schedule_id)
        return False

    project_result = await session.execute(
        select(ProjectModel).where(ProjectModel.id == schedule.project_id)
    )
    project = project_result.scalar_one_or_none()
    if project is None:
        logger.error("Project %s not found for schedule %s", schedule.project_id, schedule_id)
        return False

    # Get adapter
    adapter = get_adapter(
        platform_type=platform.platform_type,
        credentials_encrypted=platform.credentials_enc,
        api_base_url=platform.api_base_url,
    )

    # ── Task 4.1: Cookie / credential pre-check ──
    try:
        auth_ok = await adapter.authenticate()
    except Exception as auth_exc:
        logger.error(
            "Auth check raised for platform %s (schedule %s): %s — retrying on next tick",
            platform.name, schedule_id, auth_exc,
        )
        metadata = dict(schedule.metadata_json or {})
        metadata["last_error"] = f"Authentication check failed: {auth_exc}"
        schedule.metadata_json = metadata
        await session.flush()
        return False

    if not auth_ok:
        logger.error(
            "Publishing paused: credentials invalid for platform %s (schedule %s)",
            platform.name, schedule_id,
        )
        schedule.status = "paused"
        # Store the reason in metadata_json (no new DB column needed)
        _meta = dict(schedule.metadata_json or {}) if schedule.metadata_json else {}
        _meta["last_error"] = f"Credentials invalid at {datetime.now(timezone.utc).isoformat()}"
        schedule.metadata_json = _meta
        await session.flush()
        return False

    # Publish `chapters_per_release` chapters in sequence
    chapters_to_publish = schedule.chapters_per_release or 1
    any_success = False
    consecutive_failures = _consecutive_failures_from_metadata(schedule.metadata_json)
    first_chapter_number = schedule.current_chapter + 1

    for offset in range(chapters_to_publish):
        next_chapter_number = first_chapter_number + offset

        # Load chapter
        chapter_result = await session.execute(
            select(ChapterModel).where(
                ChapterModel.project_id == schedule.project_id,
                ChapterModel.chapter_number == next_chapter_number,
            )
        )
        chapter = chapter_result.scalar_one_or_none()
        if chapter is None:
            logger.info("No chapter %d yet for schedule %s — stopping batch", next_chapter_number, schedule_id)
            break

        # Load approved draft
        draft_result = await session.execute(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.chapter_id == chapter.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
        draft = draft_result.scalar_one_or_none()
        if draft is None or not draft.content_md:
            logger.info("Chapter %d has no approved draft yet — stopping batch", next_chapter_number)
            break

        # Build meta and claim/reuse the deterministic local idempotency record.
        idempotency_key = f"{schedule.id}:{next_chapter_number}"
        meta = ChapterPublishMeta(
            chapter_number=next_chapter_number,
            title=getattr(chapter, "title", None),
            word_count=draft.word_count or 0,
            project_title=project.title,
            project_slug=project.slug,
            idempotency_key=idempotency_key,
        )

        # Local publication gates run before any remote-delivery claim exists.
        # A gate rejection is therefore safe to re-evaluate on the next tick
        # and can never be mistaken for an uncertain remote delivery.
        try:
            comparison_payloads = await load_publication_comparison_payloads(
                session,
                schedule.project_id,
                through_chapter_number=next_chapter_number,
            )
            publication_blockers = collect_publication_blockers(
                project,
                [(chapter, draft)],
                comparison_payloads=comparison_payloads,
            )
        except Exception:
            logger.exception(
                "Publication gate failed for chapter %d before remote delivery",
                next_chapter_number,
            )
            break
        if publication_blockers:
            logger.warning(
                "Publication gate blocked chapter %d for schedule %s: %s",
                next_chapter_number,
                schedule_id,
                "; ".join(publication_blockers[:10]),
            )
            break

        history_result = await session.execute(
            select(PublishingHistoryModel)
            .where(PublishingHistoryModel.idempotency_key == idempotency_key)
            .with_for_update()
        )
        history = history_result.scalar_one_or_none()
        if history is not None and history.status == "success":
            if not history.platform_chapter_id:
                schedule.status = "paused"
                history.status = "failed"
                history.error_message = "Successful history is missing a remote chapter ID"
                metadata = dict(schedule.metadata_json or {})
                metadata["last_error"] = history.error_message
                metadata["pause_reason"] = "delivery_unknown"
                schedule.metadata_json = metadata
                break
            schedule.current_chapter = next_chapter_number
            any_success = True
            continue
        delivery_state = (
            (history.platform_response_json or {}).get("delivery_state")
            if history is not None
            else None
        )
        safely_retryable_state = delivery_state in {"not_attempted", "known_failed"}
        if (
            history is not None
            and not safely_retryable_state
            and not getattr(adapter, "supports_idempotency", False)
        ):
            response = dict(history.platform_response_json or {})
            response["delivery_state"] = "reconcile_required"
            response["idempotency_key"] = idempotency_key
            history.platform_response_json = response
            history.error_message = (
                "Previous delivery outcome is unknown; remote reconciliation is required"
            )
            schedule.status = "paused"
            metadata = dict(schedule.metadata_json or {})
            metadata["last_error"] = history.error_message
            metadata["pause_reason"] = "reconcile_required"
            schedule.metadata_json = metadata
            logger.error(
                "Publishing paused for chapter %d because an existing delivery "
                "has no guaranteed remote idempotency",
                next_chapter_number,
            )
            break
        if history is None:
            history = PublishingHistoryModel(
                schedule_id=schedule.id,
                project_id=schedule.project_id,
                platform_id=platform.id,
                chapter_number=next_chapter_number,
                idempotency_key=idempotency_key,
                status="pending",
                platform_response_json={
                    "delivery_state": "uncertain",
                    "idempotency_key": idempotency_key,
                },
            )
            session.add(history)
            await session.flush()
        else:
            history.status = "retrying"
            history.error_message = None
            response = dict(history.platform_response_json or {})
            response["delivery_state"] = "uncertain"
            response["idempotency_key"] = idempotency_key
            history.platform_response_json = response

        try:
            # ── Task 4.2: Inline retry with circuit breaker ──
            result = None
            for attempt in range(_MAX_INLINE_RETRIES):
                try:
                    result = await adapter.publish_chapter(content=draft.content_md, meta=meta)
                    if result.success or not result.retryable:
                        break
                except Exception as publish_exc:
                    logger.warning(
                        "Unexpected publish error on attempt %d/%d for chapter %d: %s",
                        attempt + 1, _MAX_INLINE_RETRIES, next_chapter_number, publish_exc,
                    )
                    result = None
                    break
                if attempt < _MAX_INLINE_RETRIES - 1:
                    await asyncio.sleep(_INLINE_RETRY_BASE_SECONDS * (2**attempt))

            if result is not None and result.success and not result.platform_chapter_id:
                result.success = False
                result.error_message = "Platform reported success without a remote chapter ID"
                result.retryable = False
                result.error_kind = "delivery_unknown"

            if result is None or not result.success:
                # Publish failed after retries
                history.status = "failed"
                history.error_message = result.error_message if result else "All retry attempts failed"
                history.retry_count = (history.retry_count or 0) + 1

                error_kind = result.error_kind if result else "delivery_unknown"
                response = dict(result.platform_response or {}) if result else {}
                response["delivery_state"] = (
                    "uncertain" if error_kind == "delivery_unknown" else "known_failed"
                )
                response["idempotency_key"] = idempotency_key
                history.platform_response_json = response
                if error_kind == "content":
                    logger.warning(
                        "Content rejected for chapter %d; manual review required",
                        next_chapter_number,
                    )
                    break
                if error_kind in {"auth", "delivery_unknown"}:
                    schedule.status = "paused"
                    metadata = dict(schedule.metadata_json or {})
                    metadata["last_error"] = history.error_message
                    metadata["pause_reason"] = error_kind
                    schedule.metadata_json = metadata
                    logger.error(
                        "Publishing paused for chapter %d because error kind is %s",
                        next_chapter_number,
                        error_kind,
                    )
                    break

                consecutive_failures += 1

                # Circuit breaker
                if consecutive_failures >= _CIRCUIT_BREAK_AFTER:
                    schedule.status = "paused"
                    _meta = dict(schedule.metadata_json or {}) if schedule.metadata_json else {}
                    _meta["consecutive_failures"] = consecutive_failures
                    _meta["last_error"] = f"Circuit breaker triggered at {datetime.now(timezone.utc).isoformat()}"
                    schedule.metadata_json = _meta
                    logger.error(
                        "Publishing circuit breaker triggered for platform %s (schedule %s) after %d consecutive failures",
                        platform.name, schedule_id, consecutive_failures,
                    )
                else:
                    _meta = dict(schedule.metadata_json or {}) if schedule.metadata_json else {}
                    _meta["consecutive_failures"] = consecutive_failures
                    schedule.metadata_json = _meta

                logger.warning(
                    "Failed to publish chapter %d after %d attempts: %s — stopping batch",
                    next_chapter_number, _MAX_INLINE_RETRIES, history.error_message,
                )
                break  # Stop batch on first failure

            # Success
            history.published_at = datetime.now(timezone.utc)
            history.status = "success"
            history.platform_chapter_id = result.platform_chapter_id
            response = dict(result.platform_response or {})
            response["delivery_state"] = "success"
            response["idempotency_key"] = idempotency_key
            history.platform_response_json = response
            schedule.current_chapter = next_chapter_number
            any_success = True
            consecutive_failures = 0  # reset on success
            _meta = dict(schedule.metadata_json or {}) if schedule.metadata_json else {}
            _meta["consecutive_failures"] = 0
            _meta.pop("last_error", None)
            _meta.pop("pause_reason", None)
            schedule.metadata_json = _meta
            logger.info("Published chapter %d for schedule %s", next_chapter_number, schedule_id)

        except Exception as exc:
            history.status = "failed"
            history.error_message = str(exc)
            history.retry_count = (history.retry_count or 0) + 1
            response = dict(history.platform_response_json or {})
            response["delivery_state"] = "uncertain"
            response["idempotency_key"] = idempotency_key
            history.platform_response_json = response
            schedule.status = "paused"
            metadata = dict(schedule.metadata_json or {})
            metadata["last_error"] = history.error_message
            metadata["pause_reason"] = "reconcile_required"
            schedule.metadata_json = metadata
            logger.exception("Unexpected error publishing chapter %d — stopping batch", next_chapter_number)
            break

    return any_success


async def check_publish_review_status(
    session: AsyncSession,
    settings: AppSettings,
    history_id: UUID,
) -> None:
    """Task 4.3: Poll platform review status for a previously published chapter.

    Called by the recurring review poller for least-recently-checked records.
    Updates ``PublishingHistoryModel.status``
    to ``'success'`` (approved) or ``'failed'`` (rejected) based on the
    platform's review outcome.
    """
    history = await session.get(PublishingHistoryModel, history_id)
    if history is None or history.status != "success":
        return  # Already finalised or doesn't exist

    platform_result = await session.execute(
        select(PublishingPlatformModel).where(PublishingPlatformModel.id == history.platform_id)
    )
    platform = platform_result.scalar_one_or_none()
    if platform is None or not history.platform_chapter_id:
        return

    adapter = get_adapter(
        platform_type=platform.platform_type,
        credentials_encrypted=platform.credentials_enc,
        api_base_url=platform.api_base_url,
    )

    try:
        status_result = await adapter.check_publish_status(history.platform_chapter_id)
    except Exception as exc:
        logger.warning(
            "Review status check failed for history %s (non-fatal): %s",
            history_id, exc,
        )
        return

    response = dict(history.platform_response_json or {})
    response["review_status"] = status_result.status
    response["review_checked_at"] = datetime.now(timezone.utc).isoformat()
    if status_result.message:
        response["review_message"] = status_result.message
    history.platform_response_json = response

    if status_result.status == "rejected":
        history.status = "failed"
        history.error_message = status_result.message or "Chapter rejected by platform review"
        logger.warning(
            "Chapter %d rejected by platform %s: %s",
            history.chapter_number, platform.name, history.error_message,
        )
    elif status_result.status == "published":
        # Already approved — status stays "success", just log
        logger.info(
            "Chapter %d approved by platform %s",
            history.chapter_number, platform.name,
        )
    # "under_review" or "unknown" → leave as "success" and let next poll check again
