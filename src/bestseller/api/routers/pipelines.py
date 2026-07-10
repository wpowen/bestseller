from __future__ import annotations

import logging
import uuid
from typing import Any
from urllib.parse import urlparse

from arq.connections import ArqRedis, RedisSettings, create_pool
from fastapi import APIRouter, HTTPException, Path, status
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


def _pipeline_start_key(project_id: Any) -> str:
    return f"pipeline:starting:{project_id}"


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
_PIPELINE_WORKFLOW_TYPES = frozenset({
    "autowrite_pipeline",
    "project_pipeline",
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
) -> TaskEnqueuedResponse:
    task_id = str(uuid.uuid4())
    pool = await _get_arq_pool(settings)
    await pool.enqueue_job(
        task_name,
        workflow_run_id=task_id,
        payload=payload,
        _job_id=task_id,
    )
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
) -> TaskEnqueuedResponse:
    project = await _get_project_or_404(slug, session)
    await _assert_no_active_pipeline(session, project)
    token = await _reserve_pipeline_start(redis, project.id)
    try:
        return await _enqueue(
            settings,
            "run_autowrite_task",
            {"project_slug": slug, "premise": body.premise},
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
) -> TaskEnqueuedResponse:
    project = await _get_project_or_404(slug, session)
    await _assert_no_active_pipeline(session, project)
    token = await _reserve_pipeline_start(redis, project.id)
    try:
        return await _enqueue(
            settings,
            "run_project_pipeline_task",
            {"project_slug": slug},
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
    _key: ApiKeyDep,
    body: PipelineRequest | None = None,
) -> TaskEnqueuedResponse:
    if chapter_number < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="chapter_number must be >= 1")
    await _get_project_or_404(slug, session)
    return await _enqueue(
        settings,
        "run_chapter_pipeline_task",
        {"project_slug": slug, "chapter_number": chapter_number},
    )
