from __future__ import annotations

import logging
import os
from pathlib import Path
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

_MCP_CONFIG_PATH = Path(
    os.environ.get("BESTSELLER_MCP_CONFIG_PATH", "config/mcp_servers.yaml")
)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    logger.info("Worker startup: initializing DB and Redis…")
    await init_db(settings)
    await init_redis(settings)
    ctx["redis"] = get_redis_client()

    # ── MCP connection pool (Batch 1, optional) ────────────────────────
    # Only spins up when the material-library flag is on; otherwise the
    # slow subprocess startup is pure overhead.  Pool stash lives in
    # ``ctx['mcp_pool']`` so tasks can reach it via the ARQ context.
    # Individual server failures are already swallowed inside the pool,
    # so a bad config cannot block worker startup.
    if settings.pipeline.enable_material_library:
        try:
            from bestseller.services.mcp_bridge import build_mcp_pool  # noqa: PLC0415

            if _MCP_CONFIG_PATH.exists():
                pool = await build_mcp_pool(_MCP_CONFIG_PATH, env=os.environ)
                ctx["mcp_pool"] = pool
                logger.info(
                    "Worker startup: MCP pool ready with servers=%s",
                    pool.server_names(),
                )
            else:
                logger.info(
                    "Worker startup: MCP config not found at %s; skipping pool",
                    _MCP_CONFIG_PATH,
                )
        except Exception:  # noqa: BLE001 — MCP is optional, never block worker
            logger.exception("Worker startup: MCP pool init failed — continuing without MCP")
    else:
        logger.info(
            "Worker startup: material library flag off; MCP pool not initialised"
        )

    # Self-heal: scan for stuck generation pipelines (e.g. chapters halfway
    # written when the previous container died) and re-queue an autowrite
    # task for each. Any active WorkflowRunModel row written before *this*
    # worker booted is by definition a ghost from a prior container —
    # pass ``startup_cutoff`` so those are reaped immediately instead of
    # waiting for the heartbeat timeout. Failures must not block startup.
    if os.getenv("WORKER_SELF_HEAL", "1") != "0":
        import datetime as _dt  # noqa: PLC0415
        import socket  # noqa: PLC0415

        startup_cutoff = _dt.datetime.now(_dt.UTC) - _dt.timedelta(seconds=60)
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        try:
            from bestseller.worker.self_heal import heal_stuck_projects  # noqa: PLC0415

            dispatched = await heal_stuck_projects(
                settings,
                startup_cutoff=startup_cutoff,
                redis=ctx["redis"],
                worker_id=worker_id,
            )
            if dispatched:
                logger.info(
                    "Worker startup self-heal: re-queued %d stuck project(s): %s",
                    len(dispatched),
                    [d["slug"] for d in dispatched],
                )
        except Exception:  # noqa: BLE001
            logger.exception("Worker startup self-heal failed — continuing")


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("Worker shutdown: closing connections…")
    pool = ctx.pop("mcp_pool", None)
    if pool is not None:
        try:
            await pool.stop()
        except Exception:  # noqa: BLE001
            logger.exception("Worker shutdown: MCP pool stop failed")
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
    max_jobs = int(os.getenv("WORKER_MAX_JOBS", "1"))
    job_timeout = int(os.getenv("WORKER_JOB_TIMEOUT", "86400"))  # 24 h (supports 200+ chapter novels)


def main() -> None:
    from arq import run_worker  # noqa: PLC0415

    logging.basicConfig(level=logging.INFO)
    run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
