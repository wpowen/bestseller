"""Service layer for ``book_generation_schedules``.

Lets users defer the start of an autowrite / quickstart pipeline to a
specific timestamp.  Rows in this table are claimed by the scheduler
service which registers a one-shot APScheduler ``trigger="date"`` job
per pending row; on fire the stored payload is materialised as a
``WebTaskState`` task exactly like a manual "Start" click.

Lifecycle: pending → fired → completed | failed | cancelled.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import BookGenerationScheduleModel


logger = logging.getLogger(__name__)


_VALID_TASK_TYPES: frozenset[str] = frozenset({"autowrite", "quickstart"})
_VALID_STATUSES: frozenset[str] = frozenset(
    {"pending", "fired", "completed", "failed", "cancelled"}
)


async def create_schedule(
    session: AsyncSession,
    *,
    task_type: str,
    scheduled_at: datetime,
    payload: dict[str, Any],
    project_slug: str | None = None,
    title: str | None = None,
    requested_by: str | None = None,
    schedule_timezone: str = "Asia/Shanghai",
) -> BookGenerationScheduleModel:
    """Create a new pending schedule row.

    The caller is responsible for committing the surrounding session.
    """
    if task_type not in _VALID_TASK_TYPES:
        raise ValueError(
            f"task_type must be one of {sorted(_VALID_TASK_TYPES)}, got {task_type!r}"
        )

    if scheduled_at.tzinfo is None:
        raise ValueError(
            "scheduled_at must be timezone-aware (got naive datetime)"
        )

    now = datetime.now(timezone.utc)
    if scheduled_at <= now:
        raise ValueError(
            f"scheduled_at must be in the future (got {scheduled_at.isoformat()}, now={now.isoformat()})"
        )

    row = BookGenerationScheduleModel(
        task_type=task_type,
        project_slug=project_slug,
        title=title,
        scheduled_at=scheduled_at,
        timezone=schedule_timezone,
        status="pending",
        payload=payload,
        requested_by=requested_by,
    )
    session.add(row)
    await session.flush()
    logger.info(
        "Created book generation schedule %s (%s, slug=%s, scheduled_at=%s)",
        row.id,
        task_type,
        project_slug,
        scheduled_at.isoformat(),
    )
    return row


async def list_schedules(
    session: AsyncSession,
    *,
    status_filter: str | None = None,
    project_slug: str | None = None,
    limit: int = 200,
) -> list[BookGenerationScheduleModel]:
    """List schedules ordered by scheduled time (most recent first).

    ``status_filter`` may be a single status string ("pending", "fired", ...) or
    ``None`` to return everything.
    """
    stmt = select(BookGenerationScheduleModel).order_by(
        BookGenerationScheduleModel.scheduled_at.desc()
    )
    if status_filter is not None:
        if status_filter not in _VALID_STATUSES:
            raise ValueError(
                f"status_filter must be one of {sorted(_VALID_STATUSES)}, got {status_filter!r}"
            )
        stmt = stmt.where(BookGenerationScheduleModel.status == status_filter)
    if project_slug is not None:
        stmt = stmt.where(BookGenerationScheduleModel.project_slug == project_slug)
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_schedule(
    session: AsyncSession, schedule_id: UUID
) -> BookGenerationScheduleModel | None:
    """Fetch a single schedule by id."""
    return await session.get(BookGenerationScheduleModel, schedule_id)


async def claim_pending_schedules(
    session: AsyncSession,
    *,
    horizon_seconds: int = 86400 * 365,
) -> list[BookGenerationScheduleModel]:
    """Return all pending schedules whose ``scheduled_at`` is within the horizon.

    The scheduler uses this on startup and on each poll cycle to find
    rows it should register as APScheduler date jobs.  ``horizon_seconds``
    defaults to one year — effectively "all pending rows" — but the
    parameter exists so callers can narrow the window when needed (e.g.
    a poll loop that only registers jobs for the next hour).
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(BookGenerationScheduleModel)
        .where(BookGenerationScheduleModel.status == "pending")
        .order_by(BookGenerationScheduleModel.scheduled_at.asc())
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    # Don't attempt to register far-future rows — APScheduler keeps date jobs
    # in memory until they fire, so very long horizons waste RAM.
    horizon_cutoff = now.timestamp() + horizon_seconds
    return [r for r in rows if r.scheduled_at.timestamp() <= horizon_cutoff]


async def cancel_schedule(
    session: AsyncSession, schedule_id: UUID
) -> BookGenerationScheduleModel | None:
    """Mark a pending schedule as cancelled.  Returns the updated row.

    Returns ``None`` if the row does not exist.  If the row is not in
    ``pending`` state, raises ``ValueError`` — already-fired jobs cannot
    be cancelled because the autowrite task may already be running.
    """
    row = await session.get(BookGenerationScheduleModel, schedule_id)
    if row is None:
        return None
    if row.status != "pending":
        raise ValueError(
            f"Schedule {schedule_id} is in status {row.status!r}, only pending schedules can be cancelled"
        )
    row.status = "cancelled"
    await session.flush()
    logger.info("Cancelled book generation schedule %s", schedule_id)
    return row


async def mark_fired(
    session: AsyncSession,
    schedule_id: UUID,
    *,
    task_id: str | None,
    error_message: str | None = None,
) -> None:
    """Transition a pending schedule to ``fired`` (or ``failed`` if firing failed).

    Idempotent: if the row is already in a terminal state, this is a no-op
    so the same APScheduler job firing twice (which can happen on
    misfire-recovery after a scheduler restart) does not corrupt state.
    """
    now = datetime.now(timezone.utc)
    new_status = "failed" if error_message else "fired"
    stmt = (
        update(BookGenerationScheduleModel)
        .where(
            BookGenerationScheduleModel.id == schedule_id,
            BookGenerationScheduleModel.status == "pending",
        )
        .values(
            status=new_status,
            task_id=task_id,
            fired_at=now,
            error_message=error_message,
        )
    )
    result = await session.execute(stmt)
    if result.rowcount == 0:
        logger.warning(
            "mark_fired no-op for schedule %s (already in terminal state)",
            schedule_id,
        )
        return
    logger.info(
        "Schedule %s transitioned pending → %s (task_id=%s)",
        schedule_id,
        new_status,
        task_id,
    )


async def mark_terminal(
    session: AsyncSession,
    schedule_id: UUID,
    *,
    status: str,
    error_message: str | None = None,
) -> None:
    """Transition a fired schedule to a terminal state (completed | failed).

    Used after the underlying autowrite task finishes so callers can see
    the eventual outcome of a scheduled run.
    """
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError(
            f"status must be one of completed/failed/cancelled, got {status!r}"
        )
    stmt = (
        update(BookGenerationScheduleModel)
        .where(BookGenerationScheduleModel.id == schedule_id)
        .values(status=status, error_message=error_message)
    )
    await session.execute(stmt)
    logger.info("Schedule %s transitioned to terminal state %s", schedule_id, status)
