from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

from arq.connections import ArqRedis, RedisSettings, create_pool
from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import select

from bestseller.api.deps import ApiKeyDep, SessionDep, SettingsDep
from bestseller.api.schemas.tasks import AutowriteRequest, PipelineRequest, TaskEnqueuedResponse
from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel
from bestseller.settings import AppSettings

router = APIRouter(tags=["pipelines"])

# Workflow types that count as "pipeline in progress" for concurrency guard
_PIPELINE_WORKFLOW_TYPES = frozenset({
    "autowrite_pipeline",
    "project_pipeline",
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
    _key: ApiKeyDep,
) -> TaskEnqueuedResponse:
    project = await _get_project_or_404(slug, session)
    await _assert_no_active_pipeline(session, project)
    return await _enqueue(
        settings,
        "run_autowrite_task",
        {"project_slug": slug, "premise": body.premise},
    )


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
    _key: ApiKeyDep,
) -> TaskEnqueuedResponse:
    project = await _get_project_or_404(slug, session)
    await _assert_no_active_pipeline(session, project)
    return await _enqueue(
        settings,
        "run_project_pipeline_task",
        {"project_slug": slug},
    )


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
