from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.api.deps import ApiKeyDep, RedisDep, SessionDep
from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel
from bestseller.worker.progress import _PROGRESS_CHANNEL, _PROGRESS_LIST_KEY

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tasks"])
_ATTENTION_VERDICTS = {
    "attention",
    "needs_attention",
    "machine_blocked",
    "machine_repair_required",
    "requires_machine_repair",
    "requires_human_review",
    "exported_requires_machine_repair",
    "skipped_requires_machine_repair",
    "exported_requires_human_review",
    "skipped_requires_human_review",
}

# Terminal event types that close a task stream. These match what
# worker/tasks.py emits as a run's final event:
#   * clean success      -> "completed"
#   * hard failure       -> "failed" / "error"
#   * gate/repair blocks -> "machine_blocked" / "machine_repair_required" /
#                           "blocked_generation_gate"
#   * auto-continue to closure/repair job -> "repairable_auto_continue*"
# The repairable_auto_continue family was previously missing, so SSE hung for
# the full 25h timeout and /tasks/{id} reported "running" forever for books
# handed off to quality closure.
_TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "completed",
        "done",
        "finished",
        "failed",
        "error",
        "machine_blocked",
        "machine_repair_required",
        "blocked_generation_gate",
        "repairable_auto_continue",
        "repairable_auto_continue_pending",
        "repairable_auto_continue_deferred",
        "repairable_auto_continue_already_queued",
        "quality_closure_already_queued",
        "skipped_archived",
    }
)

# Event types whose status derivation maps to "incomplete" (attention needed).
_INCOMPLETE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "machine_blocked",
        "machine_repair_required",
        "blocked_generation_gate",
        "repairable_auto_continue",
        "repairable_auto_continue_pending",
        "repairable_auto_continue_deferred",
        "repairable_auto_continue_already_queued",
        "quality_closure_already_queued",
    }
)


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: list[dict[str, Any]] = []


def _event_payload_requires_attention(event: dict[str, Any]) -> bool:
    data = event.get("data")
    payload = data if isinstance(data, dict) else {}
    if payload.get("requires_machine_repair") is True or payload.get("requires_human_review") is True:
        return True
    verdict = (
        str(
            payload.get("final_verdict")
            or payload.get("verdict")
            or payload.get("status")
            or payload.get("export_status")
            or ""
        )
        .strip()
        .lower()
    )
    return verdict in _ATTENTION_VERDICTS


# Task metadata key written by api/routers/pipelines.py::_enqueue so a task can
# be correlated back to its project/DB workflow state before any progress event.
_TASK_META_KEY = "task:{task_id}:meta"

_DB_STATUS_TO_API = {
    WorkflowStatus.PENDING.value: "queued",
    WorkflowStatus.QUEUED.value: "queued",
    WorkflowStatus.RUNNING.value: "running",
    WorkflowStatus.COMPLETED.value: "completed",
    WorkflowStatus.FAILED.value: "failed",
    WorkflowStatus.MACHINE_BLOCKED.value: "incomplete",
    WorkflowStatus.CANCELLED.value: "incomplete",
}


# Which WorkflowRunModel.workflow_type a task class may have materialized, used
# to correlate the ARQ task to its DB rows instead of taking the project's
# latest row of any type (which can belong to an unrelated pipeline lane).
# Autowrite spans every pipeline type; a chapter task should not report a stale
# project/volume-plan row as its own status.
_TASK_NAME_TO_WORKFLOW_TYPES: dict[str, frozenset[str]] = {
    "run_chapter_pipeline_task": frozenset({"chapter_pipeline", "scene_pipeline"}),
    "run_project_pipeline_task": frozenset(
        {
            "generate_foundation_plan",
            "generate_volume_plan",
            "generate_novel_plan",
            "project_pipeline",
            "chapter_pipeline",
            "scene_pipeline",
            "project_repair",
        }
    ),
    "run_project_repair_task": frozenset({"project_repair"}),
    "run_outline_replan_task": frozenset({"generate_volume_plan", "generate_novel_plan"}),
    "run_book_quality_closure_task": frozenset({"project_repair", "chapter_pipeline"}),
}
_ALL_PIPELINE_TYPES: frozenset[str] = frozenset(
    {
        "generate_foundation_plan",
        "generate_volume_plan",
        "generate_novel_plan",
        "project_pipeline",
        "chapter_pipeline",
        "scene_pipeline",
        "project_repair",
        "materialize_story_bible",
        "materialize_narrative_graph",
        "materialize_narrative_tree",
    }
)


async def _db_workflow_status(
    session: AsyncSession,
    project_slug: str,
    since_epoch: float | None,
    task_name: str = "",
) -> str | None:
    """Return the API status for the latest project workflow run at/after enqueue.

    Correlation is narrowed to the workflow types the task class produces so a
    task does not adopt an unrelated lane's status. Falls back to any type when
    the narrower filter finds nothing (e.g. resume/heal jobs with no task_name).
    Returns None when there is no project or no matching workflow run yet.
    """
    project = await session.scalar(
        select(ProjectModel).where(ProjectModel.slug == project_slug).limit(1)
    )
    if project is None:
        return None
    relevant = _TASK_NAME_TO_WORKFLOW_TYPES.get(task_name, _ALL_PIPELINE_TYPES)
    base_where = [WorkflowRunModel.project_id == project.id]
    if since_epoch is not None:
        from datetime import datetime

        base_where.append(
            WorkflowRunModel.created_at >= datetime.fromtimestamp(since_epoch, tz=UTC)
        )
    run = await session.scalar(
        select(WorkflowRunModel)
        .where(*base_where, WorkflowRunModel.workflow_type.in_(relevant))
        .order_by(WorkflowRunModel.created_at.desc())
        .limit(1)
    )
    if run is None and since_epoch is not None and relevant is not _ALL_PIPELINE_TYPES:
        # Narrow correlation found nothing (e.g. the row type is not in our
        # table). Fall back to any post-enqueue row so a genuinely running
        # pipeline still reports "running" instead of a wrong "queued".
        run = await session.scalar(
            select(WorkflowRunModel)
            .where(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.created_at >= datetime.fromtimestamp(since_epoch, tz=UTC),
            )
            .order_by(WorkflowRunModel.created_at.desc())
            .limit(1)
        )
    if run is None:
        return None
    return _DB_STATUS_TO_API.get(run.status)


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    redis: RedisDep,
    session: SessionDep,
    _key: ApiKeyDep,
) -> TaskStatusResponse:
    list_key = _PROGRESS_LIST_KEY.format(task_id=task_id)
    raw_events = await redis.lrange(list_key, 0, -1)  # type: ignore[misc]

    if raw_events:
        events = [json.loads(e) for e in raw_events]
        return TaskStatusResponse(
            task_id=task_id,
            status=_derive_status_from_events(events),
            progress=events,
        )

    # No progress events yet. Fall back to the enqueue-time mapping, then to DB
    # workflow state, so a task that fails before its first emit still reports a
    # meaningful status instead of 404 ("queued" was previously unobservable).
    try:
        meta = await redis.hgetall(_TASK_META_KEY.format(task_id=task_id))  # type: ignore[misc]
    except Exception:
        meta = {}
    if not meta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found",
        )

    raw_slug = meta.get(b"project_slug")
    if not isinstance(raw_slug, bytes):
        raw_slug = meta.get("project_slug")
    project_slug = str(raw_slug or "")
    raw_task_name = meta.get(b"task_name")
    if not isinstance(raw_task_name, bytes):
        raw_task_name = meta.get("task_name")
    task_name = str(raw_task_name or "")
    raw_created = meta.get(b"created_at")
    if not isinstance(raw_created, bytes):
        raw_created = meta.get("created_at")
    since_epoch = None
    try:
        since_epoch = float(raw_created) if raw_created else None
    except (TypeError, ValueError):
        since_epoch = None
    db_status = (
        await _db_workflow_status(session, project_slug, since_epoch, task_name)
        if project_slug
        else None
    )
    task_status = db_status or "queued"
    return TaskStatusResponse(task_id=task_id, status=task_status, progress=[])


def _derive_status_from_events(events: list[dict[str, Any]]) -> str:
    """Derive the API status from the last structured event (with text fallback)."""
    task_status = "running"
    last = events[-1] if events else {}
    event_type = last.get("event_type", "")
    if _event_payload_requires_attention(last):
        task_status = "incomplete"
    elif event_type in ("completed", "done", "finished"):
        task_status = "completed"
    elif event_type in ("failed", "error"):
        task_status = "failed"
    elif event_type == "skipped_archived":
        task_status = "completed"
    elif event_type in _INCOMPLETE_EVENT_TYPES:
        task_status = "incomplete"
    elif event_type == "progress":
        task_status = "running"
    else:
        # Legacy fallback: match against message text
        msg = last.get("message", "")
        if _event_payload_requires_attention(last):
            task_status = "incomplete"
        elif any(k in msg.lower() for k in ("completed", "done", "finished")):
            task_status = "completed"
        elif any(k in msg.lower() for k in ("failed", "error")):
            task_status = "failed"
    return task_status


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
            if evt in _TERMINAL_EVENT_TYPES:
                # The run already ended before this consumer attached; do not
                # keep the stream alive for up to 25h waiting on a channel that
                # will never produce a terminal event again.
                yield 'event: stream_end\ndata: {"message": "stream_end", "event_type": "stream_end"}\n\n'
                return

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
                    if event_type in _TERMINAL_EVENT_TYPES:
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
