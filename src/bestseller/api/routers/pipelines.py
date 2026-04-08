from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

from arq.connections import ArqRedis, RedisSettings, create_pool
from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import select

from bestseller.api.deps import ApiKeyDep, SessionDep, SettingsDep
from bestseller.api.schemas.tasks import AutowriteRequest, PipelineRequest, TaskEnqueuedResponse
from bestseller.infra.db.models import ProjectModel
from bestseller.settings import AppSettings

router = APIRouter(tags=["pipelines"])

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
    await _get_project_or_404(slug, session)
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
    await _get_project_or_404(slug, session)
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
