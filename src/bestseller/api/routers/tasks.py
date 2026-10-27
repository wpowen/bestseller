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
from bestseller.services.retry_ledger import execution_terminal_code
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
        "skipped_halted",
        "cancelled",
        "canceled",
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
    payload = _task_event_payload(event)
    if payload.get("adopted") is False:
        return True
    if (payload.get("requires_machine_repair") is True
            or payload.get("requires_human_review") is True):
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


def _task_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data", event.get("payload"))
    return data if isinstance(data, dict) else {}


_ROOT_ATTEMPT_STARTS = frozenset({
    "started", "task_started", "attempt_started", "autowrite_started",
    "progressive_autowrite_started", "project_pipeline_started", "chapter_pipeline_started",
    "project_repair_started", "outline_replan_started", "book_quality_closure_started",
})
_ROOT_COMPLETIONS = frozenset({
    "completed", "done", "finished", "skipped_archived", "autowrite_completed",
    "progressive_autowrite_completed", "project_pipeline_completed",
    "chapter_pipeline_completed", "project_repair_completed", "outline_replan_completed",
    "book_quality_closure_completed",
})


def _current_task_attempt_events(
    events: list[dict[str, Any]], *, task_id: str | None = None,
) -> list[dict[str, Any]]:
    """Select one correlated attempt without mutating its retained history.

    Only an explicit root start with a different attempt/workflow identity can
    retire the prior attempt's events. Child starts and late foreign receipts
    cannot revive or terminate the current task. Unidentified legacy events
    remain in the same attempt; they cannot manufacture a retry identity.
    """
    selected: list[dict[str, Any]] = []
    correlation = {"job_id": task_id} if task_id else {}
    attempt: dict[str, str] = {}
    retired: set[tuple[str, str]] = set()
    root_stage: str | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        payload = _task_event_payload(event)
        fields = {**event, **payload}
        identity = {
            "job_id": fields.get("job_id") or fields.get("task_id"),
            "project_id": fields.get("project_id"),
            "project_slug": fields.get("project_slug"),
            "scope_id": fields.get("execution_scope_id") or fields.get("scope_id"),
        }
        identity = {key: str(value) for key, value in identity.items() if value is not None}
        if any(key in correlation and correlation[key] != value for key, value in identity.items()):
            continue
        markers = {}
        for key in ("attempt_id", "task_attempt_id", "workflow_run_id"):
            value = fields.get(key)
            # Worker start events sometimes name the ARQ job in this field;
            # a child DB workflow is not a different ARQ attempt in that case.
            if value is not None and not (key == "workflow_run_id" and str(value) == task_id):
                markers.setdefault("attempt_id" if key == "task_attempt_id" else key, str(value))
        stage = str(event.get("message") or event.get("stage") or "").lower()
        root_start = event.get("event_type") == "started" or (
            stage in _ROOT_ATTEMPT_STARTS
            and (root_stage is None or stage == root_stage
                 or stage in {"started", "task_started", "attempt_started"})
        )
        primary = next(iter(markers.items()), None)
        if primary in retired and attempt.get(primary[0]) != primary[1]:
            continue
        matches = any(attempt.get(key) == value for key, value in markers.items())
        same_attempt_id = (
            "attempt_id" in markers and attempt.get("attempt_id") == markers["attempt_id"]
        )
        conflicts = any(key in attempt and attempt[key] != value for key, value in markers.items())
        if markers and attempt and (not matches or (conflicts and not same_attempt_id)):
            if not root_start:
                continue
            retired.update(attempt.items())
            selected = []
            root_stage = stage
            attempt = {}
        for key, value in markers.items():
            attempt.setdefault(key, value)
        correlation.update(identity)
        if root_start and root_stage is None:
            root_stage = stage
        selected.append(event)
    return selected


def _project_task_events(
    events: list[dict[str, Any]], *, task_id: str | None = None,
) -> dict[str, Any]:
    """Project invocation outcome, never chapter adoption or book delivery."""
    selected = _current_task_attempt_events(events, task_id=task_id)
    root_stage = next((str(event.get("message") or event.get("stage") or "").lower()
                       for event in selected
                       if str(event.get("message") or event.get("stage") or "").lower()
                       in _ROOT_ATTEMPT_STARTS), None)
    settled: dict[str, Any] | None = None
    rank = {"completed": 0, "incomplete": 1, "cancelled": 2, "failed": 3}
    for event in selected:
        payload = _task_event_payload(event)
        stage = str(event.get("message") or event.get("stage") or "").lower()
        event_type = str(event.get("event_type") or "").lower()
        payload_status = str(payload.get("status") or "").lower()
        code = execution_terminal_code(payload)
        status = None
        if code:
            status = "cancelled" if code == "execution_cancelled" else "failed"
        elif {stage, event_type, payload_status} & {
            "cancelled", "canceled", "skipped_halted", "halted", "paused",
            "autowrite_halted_by_operator", "chapter_loop_halted_by_operator",
        }:
            status = "cancelled"
        elif {stage, event_type, payload_status} & {"failed", "error"}:
            status = "failed"
        elif payload.get("terminal") is True:
            status = "incomplete"  # unclassified terminal evidence cannot certify success
        elif event_type in _INCOMPLETE_EVENT_TYPES or stage in _INCOMPLETE_EVENT_TYPES:
            status = "incomplete"
        elif event_type in {"completed", "done", "finished", "skipped_archived"} or (
            stage in _ROOT_COMPLETIONS and (
                root_stage is None or stage in {"completed", "done", "finished", "skipped_archived"}
                or stage == root_stage.removesuffix("_started") + "_completed"
            )
        ):
            status = "incomplete" if _event_payload_requires_attention(event) else "completed"
        elif event_type != "progress":
            if any(word in stage for word in ("failed", "error")):
                status = "failed"
        if status and (settled is None or rank[status] >= rank[settled["status"]]) and not (
            settled and settled.get("code") and not code
        ):
            settled = {"status": status, "event": event, "terminal": True, "code": code}
    if settled is not None:
        return {**settled, "events": selected}
    latest = selected[-1] if selected else {}
    return {"status": "incomplete" if _event_payload_requires_attention(latest) else "running",
            "event": latest, "terminal": False, "code": None, "events": selected}


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
    "run_autonomous_continuation_task": frozenset({"autonomous_continuation"}),
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
            status=_derive_status_from_events(events, task_id=task_id),
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


def _derive_status_from_events(
    events: list[dict[str, Any]], *, task_id: str | None = None,
) -> str:
    return _project_task_events(events, task_id=task_id)["status"]


@router.get("/tasks/{task_id}/events")
async def stream_task_events(
    task_id: str,
    request: Request,
    redis: RedisDep,
    _key: ApiKeyDep,
) -> StreamingResponse:
    """Server-Sent Events stream for real-time task progress."""

    # Maximum SSE stream duration: 25 hours (matches worker job_timeout + 1 h buffer)
    sse_timeout_seconds = 25 * 3600
    stream_end = (
        'event: stream_end\ndata: {"message": "stream_end", "event_type": "stream_end"}\n\n'
    )

    async def event_generator() -> AsyncIterator[str]:
        # First replay existing progress history
        list_key = _PROGRESS_LIST_KEY.format(task_id=task_id)
        raw_history = await redis.lrange(list_key, 0, -1)  # type: ignore[misc]
        events = []
        for raw in raw_history:
            parsed_hist = json.loads(raw)
            events.append(parsed_hist)
            evt = parsed_hist.get("event_type", "progress")
            yield f"event: {evt}\ndata: {raw}\n\n"
        # Replay every receipt before projecting the current attempt. An older
        # attempt's terminal event must not truncate a newer attempt's history.
        if _project_task_events(events, task_id=task_id)["terminal"]:
            yield stream_end
            return

        # Then subscribe to live events
        channel = _PROGRESS_CHANNEL.format(task_id=task_id)
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)

        deadline = asyncio.get_running_loop().time() + sse_timeout_seconds

        try:
            while True:
                if await request.is_disconnected():
                    break
                if asyncio.get_running_loop().time() > deadline:
                    yield (
                        'event: error\ndata: '
                        '{"message": "stream_timeout", "event_type": "error"}\n\n'
                    )
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "message":
                    data = msg["data"]
                    parsed = json.loads(data)
                    events.append(parsed)
                    event_type = parsed.get("event_type", "progress")
                    yield f"event: {event_type}\ndata: {data}\n\n"
                    if _project_task_events(events, task_id=task_id)["terminal"]:
                        yield stream_end
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
