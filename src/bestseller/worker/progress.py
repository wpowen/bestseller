from __future__ import annotations

import json
import time
from typing import Any

from redis.asyncio import Redis


# Redis key patterns
_PROGRESS_LIST_KEY = "task:{task_id}:progress"
_PROGRESS_CHANNEL = "task:{task_id}:events"
_PROGRESS_TTL = 86_400  # 24 h


class RedisProgressReporter:
    """Thread-safe progress reporter backed by Redis pub/sub + list."""

    def __init__(self, redis: Redis, task_id: str, ttl: int = _PROGRESS_TTL) -> None:  # type: ignore[type-arg]
        self._redis = redis
        self._task_id = task_id
        self._ttl = ttl
        self._list_key = _PROGRESS_LIST_KEY.format(task_id=task_id)
        self._channel = _PROGRESS_CHANNEL.format(task_id=task_id)

    async def emit(self, message: str, data: dict[str, Any] | None = None, *, event_type: str = "progress") -> None:
        event = json.dumps(
            {"ts": time.time(), "message": message, "data": data or {}, "event_type": event_type},
            ensure_ascii=False,
        )
        # Use pipeline to batch rpush + expire + publish in a single round-trip
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.rpush(self._list_key, event)  # type: ignore[misc]
            pipe.expire(self._list_key, self._ttl)
            pipe.publish(self._channel, event)
            await pipe.execute()

    async def get_history(self) -> list[dict[str, Any]]:
        raw = await self._redis.lrange(self._list_key, 0, -1)  # type: ignore[misc]
        return [json.loads(item) for item in raw]


def make_sync_callback(reporter: RedisProgressReporter) -> Any:
    """Return a synchronous callback that schedules the async emit via asyncio."""
    import asyncio  # noqa: PLC0415

    def callback(message: str, data: dict[str, Any] | None = None) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No event loop running; silently drop the event
        loop.create_task(reporter.emit(message, data))

    return callback
