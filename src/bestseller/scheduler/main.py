from __future__ import annotations

"""APScheduler-based publishing scheduler service.

Loads all active PublishingSchedule records on startup and registers
each as a cron job. Listens to Redis pubsub for schedule changes
(create / pause / activate) to hot-reload without restart.
"""

import asyncio
import json
import logging
import os
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import select, text

from bestseller.infra.db.session import init_db, shutdown_db, get_server_session
from bestseller.infra.redis import init_redis, shutdown_redis, get_redis_client
from bestseller.infra.db.models import PublishingScheduleModel
from bestseller.scheduler.jobs import publish_next_chapter
from bestseller.settings import get_settings

logger = logging.getLogger(__name__)

_SCHEDULE_CHANNEL = "bestseller:schedule:events"


def _build_scheduler(db_url: str) -> AsyncIOScheduler:
    # Use synchronous psycopg (v3) for APScheduler job store (APScheduler doesn't support async)
    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    jobstores = {"default": SQLAlchemyJobStore(url=sync_url)}
    return AsyncIOScheduler(jobstores=jobstores)


async def _publish_schedule_job(schedule_id: UUID) -> None:
    """Module-level wrapper for a single publishing-schedule firing.

    MUST live at module-level (not nested inside `_register_schedule`).
    APScheduler's SQLAlchemyJobStore persists jobs via pickle; closures /
    nested functions can't be pickled, so a nested definition crashes
    `scheduler.start()` with "This Job cannot be serialized since the
    reference to its callable could not be determined".

    The schedule id is passed through `add_job(args=[...])` instead of
    captured as a closure variable, so the callable reference is the
    plain dotted path `bestseller.scheduler.main:_publish_schedule_job`.
    """
    async with get_server_session() as session:
        await publish_next_chapter(
            session=session,
            settings=get_settings(),
            schedule_id=schedule_id,
        )


async def _register_schedule(scheduler: AsyncIOScheduler, schedule: PublishingScheduleModel) -> None:
    job_id = f"publish_{schedule.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    if schedule.status != "active":
        return

    scheduler.add_job(
        _publish_schedule_job,
        trigger="cron",
        id=job_id,
        args=[schedule.id],
        replace_existing=True,
        **_parse_cron(schedule.cron_expression),
        timezone=schedule.timezone,
    )
    logger.info("Registered schedule %s (%s, %s)", schedule.id, schedule.cron_expression, schedule.timezone)


def _parse_cron(expr: str) -> dict[str, str]:
    parts = expr.strip().split()
    keys = ["minute", "hour", "day", "month", "day_of_week"]
    if len(parts) != len(keys):
        raise ValueError(
            f"Invalid cron expression '{expr}': expected {len(keys)} parts "
            f"(minute hour day month day_of_week), got {len(parts)}"
        )
    return dict(zip(keys, parts))


async def _listen_for_changes(scheduler: AsyncIOScheduler) -> None:
    """Listen to Redis pubsub for schedule changes with automatic reconnection.

    Uses ``get_message(timeout=...)`` in a loop instead of ``listen()`` because
    the shared Redis client is configured with ``socket_timeout=5s`` (sensible
    for regular commands) — but ``listen()`` blocks on the socket and every
    timeout tears the connection down, reconnecting ~360×/hour.  That spams
    tens of thousands of stack traces per day and churns connections.

    ``get_message`` returns ``None`` on timeout instead of raising, which lets
    us keep a single long-lived subscription and only reconnect on genuine
    connection errors.
    """
    # Poll interval: long enough to be cheap (1 tick/s), short enough to be
    # responsive to schedule changes in the UI.
    _POLL_TIMEOUT_SECONDS = 1.0

    pubsub = None
    while True:
        try:
            redis = get_redis_client()
            pubsub = redis.pubsub()
            await pubsub.subscribe(_SCHEDULE_CHANNEL)
            logger.info("Listening for schedule change events on %s", _SCHEDULE_CHANNEL)

            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=_POLL_TIMEOUT_SECONDS,
                )
                if message is None:
                    # Idle poll cycle — not an error, just no event yet.
                    continue
                if message.get("type") != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                    schedule_id = event.get("schedule_id")
                    if not schedule_id:
                        continue
                    async with get_server_session() as session:
                        result = await session.execute(
                            select(PublishingScheduleModel).where(
                                PublishingScheduleModel.id == schedule_id
                            )
                        )
                        schedule = result.scalar_one_or_none()
                        if schedule:
                            await _register_schedule(scheduler, schedule)
                except Exception:
                    logger.exception("Error processing schedule change event")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Redis pubsub connection lost, reconnecting in 5s…", exc_info=True)
            await asyncio.sleep(5)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(_SCHEDULE_CHANNEL)
                    await pubsub.aclose()
                except Exception:
                    pass
                pubsub = None


# ── DB maintenance job ────────────────────────────────────────────────────
# Runs once per day at 03:00 UTC.  Removes large analytical tables that
# grow without bound and are never queried after a short retention window:
#
#   rewrite_impacts  — per-scene impact scores for every rewrite task.
#                      101 K rows / 84 MB after a few weeks of use.
#                      CASCADE-delete via rewrite_tasks (completed, >30d).
#   workflow_step_runs — per-step execution log.  Only used for debugging.
#                        Records older than 30 days are not actionable.
#
# Retention window is configurable via BESTSELLER_DB_RETENTION_DAYS.
#
# IMPORTANT: this function MUST live at module-level (not nested inside
# main()).  APScheduler's SQLAlchemyJobStore persists jobs via pickle, and
# pickle cannot serialize closures / nested functions — doing so crashes
# scheduler.start() with "This Job cannot be serialized since the reference
# to its callable could not be determined".  A module-level function is
# pickled by its dotted path (bestseller.scheduler.main:_db_maintenance_job),
# which APScheduler can resolve on reload.
async def _db_maintenance_job() -> None:
    retention_days = int(os.environ.get("BESTSELLER_DB_RETENTION_DAYS", "30"))
    logger.info("DB maintenance: purging records older than %d days", retention_days)
    try:
        async with get_server_session() as session:
            # 1. Delete completed/failed rewrite_tasks older than retention window.
            #    rewrite_impacts rows are removed automatically via ON DELETE CASCADE.
            #    Note: INTERVAL doesn't support bind parameters in PostgreSQL so we
            #    multiply the bound :days against a literal INTERVAL '1 day'.
            rt_result = await session.execute(
                text(
                    """
                    DELETE FROM rewrite_tasks
                    WHERE status IN ('completed', 'failed', 'cancelled')
                      AND created_at < NOW() - (:days * INTERVAL '1 day')
                    """
                ),
                {"days": retention_days},
            )
            rt_deleted = rt_result.rowcount

            # 2. Delete old workflow_step_runs (verbose execution log, large table).
            ws_result = await session.execute(
                text(
                    """
                    DELETE FROM workflow_step_runs
                    WHERE created_at < NOW() - (:days * INTERVAL '1 day')
                    """
                ),
                {"days": retention_days},
            )
            ws_deleted = ws_result.rowcount

            await session.commit()

        logger.info(
            "DB maintenance done: removed %d rewrite_task(s) (+ cascaded impacts), "
            "%d workflow_step_run(s)",
            rt_deleted,
            ws_deleted,
        )
    except Exception:
        logger.exception("DB maintenance job failed")


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)

    logger.info("Initializing DB and Redis for scheduler…")
    await init_db(settings)
    await init_redis(settings)

    scheduler = _build_scheduler(settings.database.url)

    # Load all active schedules from DB
    async with get_server_session() as session:
        result = await session.execute(
            select(PublishingScheduleModel).where(PublishingScheduleModel.status == "active")
        )
        schedules = result.scalars().all()

    for schedule in schedules:
        await _register_schedule(scheduler, schedule)

    retention_days = int(os.environ.get("BESTSELLER_DB_RETENTION_DAYS", "30"))
    scheduler.add_job(
        _db_maintenance_job,
        trigger="cron",
        id="db_maintenance",
        replace_existing=True,
        hour=3,
        minute=0,
        timezone="UTC",
    )
    logger.info("Registered daily DB maintenance job (retention=%d days)", retention_days)

    # ── Library Curator weekly audit (Batch 1) ─────────────────────────
    # Even when ``enable_material_library`` is False we still register
    # the job — the entry point is flag-aware and short-circuits at
    # execution time, so the job stays cheap until the flag flips on.
    # This lets us ship the plumbing without having to redeploy the
    # scheduler at flag-flip time.
    try:
        from bestseller.services.library_curator import scheduled_weekly_audit  # noqa: PLC0415

        scheduler.add_job(
            scheduled_weekly_audit,
            trigger="cron",
            id="library_curator_weekly",
            replace_existing=True,
            hour=int(os.environ.get("BESTSELLER_CURATOR_CRON_HOUR", str(
                settings.pipeline.curator_weekly_cron_hour
            ))),
            minute=0,
            day_of_week=os.environ.get(
                "BESTSELLER_CURATOR_CRON_DOW",
                settings.pipeline.curator_weekly_cron_day_of_week,
            ),
            timezone="UTC",
        )
        logger.info(
            "Registered library_curator weekly audit job (flag=%s)",
            settings.pipeline.enable_material_library,
        )
    except Exception:  # noqa: BLE001 — never block scheduler startup on optional jobs
        logger.exception("Failed to register library_curator weekly audit job — skipping")

    scheduler.start()
    logger.info("Scheduler started with %d active jobs", len(scheduler.get_jobs()))

    try:
        await _listen_for_changes(scheduler)
    except asyncio.CancelledError:
        pass
    finally:
        scheduler.shutdown()
        await shutdown_redis()
        await shutdown_db()
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    asyncio.run(main())
