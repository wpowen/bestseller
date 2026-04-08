from __future__ import annotations

import logging
import os
from typing import Any

from arq.connections import RedisSettings

from bestseller.infra.db.session import init_db, shutdown_db
from bestseller.infra.redis import get_redis_client, init_redis, shutdown_redis
from bestseller.settings import get_settings
from bestseller.worker.tasks import (
    run_autowrite_task,
    run_chapter_pipeline_task,
    run_project_pipeline_task,
)

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    logger.info("Worker startup: initializing DB and Redis…")
    await init_db(settings)
    await init_redis(settings)
    ctx["redis"] = get_redis_client()


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("Worker shutdown: closing connections…")
    await shutdown_redis()
    await shutdown_db()


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    url = settings.redis.url
    # Parse redis://[:password@]host[:port][/db]
    from urllib.parse import urlparse  # noqa: PLC0415
    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or "0"),
        password=parsed.password,
    )


class WorkerSettings:
    functions = [
        run_autowrite_task,
        run_project_pipeline_task,
        run_chapter_pipeline_task,
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()
    max_jobs = int(os.getenv("WORKER_MAX_JOBS", "4"))
    job_timeout = int(os.getenv("WORKER_JOB_TIMEOUT", "86400"))  # 24 h (supports 200+ chapter novels)


def main() -> None:
    from arq import run_worker  # noqa: PLC0415

    logging.basicConfig(level=logging.INFO)
    run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
