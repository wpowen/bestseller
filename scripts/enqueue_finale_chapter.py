"""Enqueue a single chapter pipeline job onto ARQ (the memory-safe async path).

Running `bestseller chapter pipeline` synchronously inside a worker container
competes with the resident ARQ worker for its 768MB cap and OOM-kills it. The
correct path is to enqueue the job so ONE worker runs it in its own process,
exactly like normal generation for every other chapter.

Usage (inside any worker container, which can reach redis):
    python /app/scripts/enqueue_finale_chapter.py 217
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from urllib.parse import urlparse

from arq.connections import RedisSettings, create_pool

from bestseller.settings import get_settings

SLUG = "xianxia-upgrade-1776137730"


async def main(chapter_number: int) -> None:
    settings = get_settings()
    parsed = urlparse(settings.redis.url)
    redis_settings = RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or "0"),
        password=parsed.password,
    )
    pool = await create_pool(redis_settings)
    task_id = str(uuid.uuid4())
    await pool.enqueue_job(
        "run_chapter_pipeline_task",
        workflow_run_id=task_id,
        payload={"project_slug": SLUG, "chapter_number": chapter_number},
        _job_id=task_id,
    )
    print(f"enqueued ch{chapter_number}: job_id={task_id}")


if __name__ == "__main__":
    ch = int(sys.argv[1]) if len(sys.argv) > 1 else 217
    asyncio.run(main(ch))
