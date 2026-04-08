from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from bestseller.api.deps import ApiKeyDep, RedisDep
from bestseller.worker.progress import _PROGRESS_CHANNEL, _PROGRESS_LIST_KEY

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tasks"])


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: list[dict] = []


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    redis: RedisDep,
    _key: ApiKeyDep,
) -> TaskStatusResponse:
    list_key = _PROGRESS_LIST_KEY.format(task_id=task_id)
    raw_events = await redis.lrange(list_key, 0, -1)  # type: ignore[misc]

    if not raw_events:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found")

    events = [json.loads(e) for e in raw_events]

    # Derive status from structured event_type (falls back to message text matching)
    task_status = "running"
    last = events[-1] if events else {}
    event_type = last.get("event_type", "")
    if event_type in ("completed", "done", "finished"):
        task_status = "completed"
    elif event_type in ("failed", "error"):
        task_status = "failed"
    elif event_type == "progress":
        task_status = "running"
    else:
        # Legacy fallback: match against message text
        msg = last.get("message", "")
        if any(k in msg.lower() for k in ("completed", "done", "finished")):
            task_status = "completed"
        elif any(k in msg.lower() for k in ("failed", "error")):
            task_status = "failed"

    return TaskStatusResponse(task_id=task_id, status=task_status, progress=events)


@router.get("/tasks/{task_id}/events")
async def stream_task_events(
    task_id: str,
    request: Request,
    redis: RedisDep,
    _key: ApiKeyDep,
) -> StreamingResponse:
    """Server-Sent Events stream for real-time task progress."""

    # Maximum SSE stream duration: 25 hours (matches worker job_timeout + 1 h buffer)
    _SSE_TIMEOUT_SECONDS = 25 * 3600

    async def event_generator() -> AsyncIterator[str]:
        # First replay existing progress history
        list_key = _PROGRESS_LIST_KEY.format(task_id=task_id)
        raw_history = await redis.lrange(list_key, 0, -1)  # type: ignore[misc]
        for raw in raw_history:
            parsed_hist = json.loads(raw)
            evt = parsed_hist.get("event_type", "progress")
            yield f"event: {evt}\ndata: {raw}\n\n"

        # Then subscribe to live events
        channel = _PROGRESS_CHANNEL.format(task_id=task_id)
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)

        deadline = asyncio.get_running_loop().time() + _SSE_TIMEOUT_SECONDS

        try:
            while True:
                if await request.is_disconnected():
                    break
                if asyncio.get_running_loop().time() > deadline:
                    yield 'event: error\ndata: {"message": "stream_timeout", "event_type": "error"}\n\n'
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "message":
                    data = msg["data"]
                    parsed = json.loads(data)
                    event_type = parsed.get("event_type", "progress")
                    yield f"event: {event_type}\ndata: {data}\n\n"
                    if event_type in ("completed", "done", "finished", "failed", "error"):
                        yield 'event: stream_end\ndata: {"message": "stream_end", "event_type": "stream_end"}\n\n'
                        break
                    # Legacy fallback
                    text_msg = parsed.get("message", "")
                    if any(k in text_msg.lower() for k in ("completed", "done", "failed", "error")):
                        yield 'event: stream_end\ndata: {"message": "stream_end", "event_type": "stream_end"}\n\n'
                        break
                else:
                    await asyncio.sleep(0.1)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
