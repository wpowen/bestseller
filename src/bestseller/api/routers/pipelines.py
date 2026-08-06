from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse
import uuid

from arq.connections import ArqRedis, RedisSettings, create_pool
from fastapi import APIRouter, Header, HTTPException, Path, status
from sqlalchemy import select

from bestseller.api.deps import ApiKeyDep, RedisDep, SessionDep, SettingsDep
from bestseller.api.schemas.tasks import AutowriteRequest, PipelineRequest, TaskEnqueuedResponse
from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pipelines"])

# A pipeline handler only enqueues an ARQ job; the WorkflowRunModel row is
# materialized later by the worker. That leaves a TOCTOU window where the DB
# guard (`_assert_no_active_pipeline`) has nothing to observe, so two near
# simultaneous requests both enqueue. A short-lived Redis NX marker bridges
# that gap: it serializes the *start* until the worker creates the row, after
# which the DB guard takes over. TTL keeps it self-healing if the job never
# starts (no permanent lock), and it never blocks legitimate re-runs once a
# pipeline has finished.
_PIPELINE_START_TTL_SECONDS = 120
# Keep the enqueue-time task metadata long enough for the caller to poll it out
# of a slow/heavy run (matches the worker's 24h job timeout + headroom).
_TASK_META_TTL_SECONDS = 7 * 86400


def _pipeline_start_key(project_id: Any) -> str:
    return f"pipeline:starting:{project_id}"


def _task_meta_key(task_id: str) -> str:
    return f"task:{task_id}:meta"


async def _reserve_pipeline_start(redis: Any, project_id: Any) -> str | None:
    """Reserve the per-project start slot.

    Returns a release token on success; raises 409 if a start is already in
    flight; returns None (degraded, proceed on the DB guard alone) if Redis is
    unavailable so an infra hiccup never blocks legitimate work.
    """
    token = str(uuid.uuid4())
    try:
        reserved = await redis.set(
            _pipeline_start_key(project_id),
            token,
            nx=True,
            ex=_PIPELINE_START_TTL_SECONDS,
        )
    except Exception:
        logger.warning(
            "pipeline start reservation skipped (redis unavailable)", exc_info=True
        )
        return None
    if not reserved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A pipeline is already starting for this project. "
                "Wait for it to begin, or retry shortly."
            ),
        )
    return token


async def _release_pipeline_start(redis: Any, project_id: Any, token: str | None) -> None:
    """Best-effort release of our own reservation (used on enqueue failure)."""
    if token is None:
        return
    key = _pipeline_start_key(project_id)
    try:
        current = await redis.get(key)
        if isinstance(current, bytes):
            current = current.decode()
        if current == token:
            await redis.delete(key)
    except Exception:
        logger.debug("pipeline start reservation release failed", exc_info=True)

# Workflow types that count as "pipeline in progress" for concurrency guard.
# Planning types included so double-click / heal-job cannot spawn zombie
# generate_volume_plan runs that thrash outline artifacts (2026-07-09).
# chapter_pipeline/scene_pipeline included so a chapter start is also guarded
# by the DB (a single chapter run is still a project pipeline in flight).
_PIPELINE_WORKFLOW_TYPES = frozenset({
    "autowrite_pipeline",
    "project_pipeline",
    "chapter_pipeline",
    "scene_pipeline",
    "generate_novel_plan",
    "generate_volume_plan",
    "generate_foundation_plan",
})
_ACTIVE_STATUSES = frozenset({
    WorkflowStatus.PENDING.value,
    WorkflowStatus.QUEUED.value,
    WorkflowStatus.RUNNING.value,
})

# Module-level cached ARQ pool — initialized lazily on first use
_arq_pool: ArqRedis | None = None


def _arq_redis_settings(settings: AppSettings) -> RedisSettings:
    parsed = urlparse(settings.redis.url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or "0"),
        password=parsed.password,
    )


async def _get_arq_pool(settings: AppSettings) -> ArqRedis:
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(_arq_redis_settings(settings))
    return _arq_pool


async def _get_project_or_404(slug: str, session: SessionDep) -> ProjectModel:
    result = await session.execute(select(ProjectModel).where(ProjectModel.slug == slug))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{slug}' not found")
    return project


async def _assert_no_active_pipeline(
    session: SessionDep,
    project: ProjectModel,
) -> None:
    """Raise 409 Conflict if a pipeline is already running for this project."""
    # Planning rows (generate_novel_plan / generate_volume_plan /
    # generate_foundation_plan) only get swept when a *new* planning run
    # starts. If a book never re-plans again, a dead planning row from a
    # crashed worker would otherwise dangle in `_ACTIVE_STATUSES` forever and
    # permanently 409 "start writing" below. Sweep stale ones first
    # (best-effort — a genuinely fresh planning row is left alone and still
    # correctly 409s via the active_run check).
    try:
        from bestseller.services.planning_concurrency import cancel_stale_planning_workflows

        await cancel_stale_planning_workflows(
            session,
            project.id,
            reason="stale planning row swept before writing start",
        )
    except Exception:
        logger.debug(
            "stale planning sweep before writing-start skipped", exc_info=True
        )

    active_run = await session.scalar(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.project_id == project.id,
            WorkflowRunModel.workflow_type.in_(_PIPELINE_WORKFLOW_TYPES),
            WorkflowRunModel.status.in_(_ACTIVE_STATUSES),
        )
        .limit(1)
    )
    if active_run is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Project '{project.slug}' already has an active pipeline "
                f"(workflow_run={active_run.id}, status={active_run.status}). "
                "Wait for it to finish or cancel it first."
            ),
        )


async def _enqueue(
    settings: AppSettings,
    task_name: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> TaskEnqueuedResponse:
    # Request-level idempotency (P09): a caller-supplied Idempotency-Key header
    # becomes the ARQ job id, which ARQ dedups — re-sending the same key does
    # not enqueue a duplicate job. Absent the header we fall back to a fresh
    # UUID (legacy behavior).
    task_id = idempotency_key or str(uuid.uuid4())
    pool = await _get_arq_pool(settings)
    await pool.enqueue_job(
        task_name,
        workflow_run_id=task_id,
        payload=payload,
        _job_id=task_id,
    )
    # Best-effort: record the enqueue-time mapping so /tasks/{id} can fall back
    # to DB workflow state before the first Redis progress event exists. Without
    # it a task that fails before its first emit (worker not started / project
    # missing) returns 404 forever and "queued" is unobservable (P04).
    try:
        await pool.hset(  # type: ignore[misc]
            _task_meta_key(task_id),
            mapping={
                "task_name": task_name,
                "project_slug": str(payload.get("project_slug") or ""),
                "created_at": str(time.time()),
            },
        )
        await pool.expire(_task_meta_key(task_id), _TASK_META_TTL_SECONDS)
    except Exception:
        logger.debug("task meta write skipped (redis unavailable)", exc_info=True)
    return TaskEnqueuedResponse(task_id=task_id)


@router.post(
    "/projects/{slug}/autowrite",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_autowrite(
    slug: str,
    body: AutowriteRequest,
    session: SessionDep,
    settings: SettingsDep,
    redis: RedisDep,
    _key: ApiKeyDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskEnqueuedResponse:
    project = await _get_project_or_404(slug, session)
    if not body.force:
        await _assert_no_active_pipeline(session, project)
    token = await _reserve_pipeline_start(redis, project.id)
    try:
        return await _enqueue(
            settings,
            "run_autowrite_task",
            {"project_slug": slug, "premise": body.premise},
            idempotency_key=idempotency_key,
        )
    except Exception:
        await _release_pipeline_start(redis, project.id, token)
        raise


@router.post(
    "/projects/{slug}/pipeline",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_project_pipeline(
    slug: str,
    body: PipelineRequest,
    session: SessionDep,
    settings: SettingsDep,
    redis: RedisDep,
    _key: ApiKeyDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskEnqueuedResponse:
    project = await _get_project_or_404(slug, session)
    if not body.force:
        await _assert_no_active_pipeline(session, project)
    token = await _reserve_pipeline_start(redis, project.id)
    try:
        return await _enqueue(
            settings,
            "run_project_pipeline_task",
            {
                "project_slug": slug,
                "chapter_first": body.chapter_first,
                "stop_on_chapter_failure": body.stop_on_chapter_failure,
            },
            idempotency_key=idempotency_key,
        )
    except Exception:
        await _release_pipeline_start(redis, project.id, token)
        raise


@router.post(
    "/projects/{slug}/chapters/{chapter_number}/pipeline",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_chapter_pipeline(
    slug: str,
    chapter_number: int,
    session: SessionDep,
    settings: SettingsDep,
    redis: RedisDep,
    _key: ApiKeyDep,
    body: PipelineRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskEnqueuedResponse:
    if chapter_number < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="chapter_number must be >= 1")
    project = await _get_project_or_404(slug, session)
    if not (body is not None and body.force):
        await _assert_no_active_pipeline(session, project)
    # Same per-project start reservation as autowrite/project pipelines: a
    # double-click or near-simultaneous retry used to enqueue duplicate chapter
    # runs (each call used a fresh job id, and this endpoint had no guard). The
    # reservation covers the enqueue→row window; the DB active-run guard above
    # covers the rest of the run.
    token = await _reserve_pipeline_start(redis, project.id)
    try:
        return await _enqueue(
            settings,
            "run_chapter_pipeline_task",
            {
                "project_slug": slug,
                "chapter_number": chapter_number,
                "chapter_first": body.chapter_first if body is not None else None,
            },
            idempotency_key=idempotency_key,
        )
    except Exception:
        await _release_pipeline_start(redis, project.id, token)
        raise
