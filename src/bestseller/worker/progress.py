from __future__ import annotations

from collections.abc import Callable
import json
import time
from typing import Any

from redis.asyncio import Redis

from bestseller.services.progress_context import TaskCancelledError

# Redis key patterns
_PROGRESS_LIST_KEY = "task:{task_id}:progress"
# Durable milestone axis — mirrors milestone-tier events so they survive the
# activity list's read window (lrange tail) on self-heal/resume reconnects.
_MILESTONE_LIST_KEY = "task:{task_id}:milestones"
_PROGRESS_CHANNEL = "task:{task_id}:events"
_PROGRESS_TTL = 86_400  # 24 h

# Payload key carrying the milestone/activity tier hint (mirrors
# services.progress_context.TIER_KEY without importing it here).
_TIER_KEY = "_tier"
_TIER_MILESTONE = "milestone"

# These events are emitted by the pipeline immediately after it observes the
# durable operator pause/stop row.  Raising from the synchronous progress
# callback is intentional: the pipeline's inner loop otherwise only breaks
# its current volume/chapter loop and can start the next paid batch.
_HALT_EVENTS = frozenset(
    {
        "autowrite_halted_by_operator",
        "chapter_loop_halted_by_operator",
    }
)


class RedisProgressReporter:
    """Thread-safe progress reporter backed by Redis pub/sub + list."""

    def __init__(self, redis: Redis, task_id: str, ttl: int = _PROGRESS_TTL) -> None:  # type: ignore[type-arg]
        self._redis = redis
        self._task_id = task_id
        self._ttl = ttl
        self._list_key = _PROGRESS_LIST_KEY.format(task_id=task_id)
        self._milestone_key = _MILESTONE_LIST_KEY.format(task_id=task_id)
        self._channel = _PROGRESS_CHANNEL.format(task_id=task_id)

    async def emit(
        self,
        message: str,
        data: dict[str, Any] | None = None,
        *,
        event_type: str = "progress",
    ) -> None:
        data = data or {}
        is_milestone = data.get(_TIER_KEY) == _TIER_MILESTONE
        event = json.dumps(
            {
                "ts": time.time(),
                "message": message,
                "data": data,
                "event_type": _TIER_MILESTONE if is_milestone else event_type,
            },
            ensure_ascii=False,
        )
        # Use pipeline to batch rpush + expire + publish in a single round-trip
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.rpush(self._list_key, event)  # type: ignore[misc]
            pipe.expire(self._list_key, self._ttl)
            if is_milestone:
                pipe.rpush(self._milestone_key, event)  # type: ignore[misc]
                pipe.expire(self._milestone_key, self._ttl)
            pipe.publish(self._channel, event)
            await pipe.execute()

    async def get_history(self) -> list[dict[str, Any]]:
        raw = await self._redis.lrange(self._list_key, 0, -1)  # type: ignore[misc]
        return [json.loads(item) for item in raw]


def make_sync_callback(
    reporter: RedisProgressReporter,
) -> Callable[[str, dict[str, Any] | None], None]:
    """Return a callback that emits progress and propagates durable halts.

    The pipeline calls progress synchronously.  A plain ``create_task`` would
    report a halt but let the caller continue into its next LLM batch.  Halt
    events are therefore raised synchronously after the event is scheduled;
    an already-running provider call is unaffected, while the next boundary
    cannot be crossed.
    """
    import asyncio

    def callback(message: str, data: dict[str, Any] | None = None) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No event loop running; silently drop the event
        task = loop.create_task(reporter.emit(message, data))
        task.add_done_callback(lambda completed: completed.exception())
        if message in _HALT_EVENTS:
            raise TaskCancelledError(f"pipeline halted by operator: {message}")

    return callback
