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

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import select

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


async def _register_schedule(scheduler: AsyncIOScheduler, schedule: PublishingScheduleModel) -> None:
    job_id = f"publish_{schedule.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    if schedule.status != "active":
        return

    async def job() -> None:
        async with get_server_session() as session:
            await publish_next_chapter(session=session, settings=get_settings(), schedule_id=schedule.id)

    scheduler.add_job(
        job,
        trigger="cron",
        id=job_id,
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
    """Listen to Redis pubsub for schedule changes with automatic reconnection."""
    pubsub = None
    while True:
        try:
            redis = get_redis_client()
            pubsub = redis.pubsub()
            await pubsub.subscribe(_SCHEDULE_CHANNEL)
            logger.info("Listening for schedule change events on %s", _SCHEDULE_CHANNEL)

            async for message in pubsub.listen():
                if message["type"] != "message":
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
