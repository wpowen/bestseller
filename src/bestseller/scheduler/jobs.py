from __future__ import annotations

"""Publishing scheduler jobs."""

import logging
from datetime import datetime, timezone
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
from bestseller.services.publishing.base import ChapterPublishMeta
from bestseller.services.publishing.registry import get_adapter
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


async def publish_next_chapter(
    session: AsyncSession,
    settings: AppSettings,
    schedule_id: UUID,
) -> bool:
    """Publish the next unpublished chapter for a given schedule. Returns True on success."""
    # Load schedule
    sched_result = await session.execute(
        select(PublishingScheduleModel).where(PublishingScheduleModel.id == schedule_id)
    )
    schedule = sched_result.scalar_one_or_none()
    if schedule is None or schedule.status != "active":
        logger.warning("Schedule %s not found or not active", schedule_id)
        return False

    # Load platform + project in parallel-ish queries
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

    # Get adapter once (reused across all chapters in this release)
    adapter = get_adapter(
        platform_type=platform.platform_type,
        credentials_encrypted=platform.credentials_enc,
        api_base_url=platform.api_base_url,
    )

    # Publish `chapters_per_release` chapters in sequence
    chapters_to_publish = schedule.chapters_per_release or 1
    any_success = False

    for offset in range(chapters_to_publish):
        next_chapter_number = schedule.current_chapter + 1 + offset

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

        # Build meta with real project info
        meta = ChapterPublishMeta(
            chapter_number=next_chapter_number,
            title=getattr(chapter, "title", None),
            word_count=draft.word_count or 0,
            project_title=project.title,
            project_slug=project.slug,
        )

        history = PublishingHistoryModel(
            schedule_id=schedule.id,
            project_id=schedule.project_id,
            platform_id=platform.id,
            chapter_number=next_chapter_number,
            status="pending",
        )
        session.add(history)
        await session.flush()

        try:
            result = await adapter.publish_chapter(content=draft.content_md, meta=meta)
            history.published_at = datetime.now(timezone.utc)
            history.status = "success" if result.success else "failed"
            history.platform_chapter_id = result.platform_chapter_id
            history.platform_response_json = result.platform_response or {}
            history.error_message = result.error_message

            if result.success:
                schedule.current_chapter = next_chapter_number
                any_success = True
                logger.info("Published chapter %d for schedule %s", next_chapter_number, schedule_id)
            else:
                history.retry_count += 1
                logger.warning(
                    "Failed to publish chapter %d: %s — stopping batch",
                    next_chapter_number,
                    result.error_message,
                )
                break  # Stop batch on first failure
        except Exception as exc:
            history.status = "failed"
            history.error_message = str(exc)
            history.retry_count += 1
            logger.exception("Unexpected error publishing chapter %d — stopping batch", next_chapter_number)
            break  # Stop batch on exception

    return any_success
