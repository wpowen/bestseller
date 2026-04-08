from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from bestseller.api.deps import ApiKeyDep, SessionDep, SettingsDep
from bestseller.api.schemas.publishing import (
    PlatformCreateRequest,
    PlatformResponse,
    PublishHistoryItem,
    PublishHistoryResponse,
    ScheduleCreateRequest,
    ScheduleResponse,
)
from bestseller.infra.db.models import (
    ProjectModel,
    PublishingHistoryModel,
    PublishingPlatformModel,
    PublishingScheduleModel,
)

router = APIRouter(tags=["publishing"])


async def _get_project_or_404(slug: str, session: SessionDep) -> ProjectModel:
    result = await session.execute(select(ProjectModel).where(ProjectModel.slug == slug))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{slug}' not found")
    return project


def _encrypt_credentials(creds: dict[str, str]) -> str:
    import json  # noqa: PLC0415
    import logging  # noqa: PLC0415

    _logger = logging.getLogger(__name__)

    enc_key = os.getenv("BESTSELLER_ENCRYPTION_KEY", "")
    if not enc_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="BESTSELLER_ENCRYPTION_KEY not configured — cannot store credentials securely",
        )
    from cryptography.fernet import Fernet  # noqa: PLC0415
    f = Fernet(enc_key.encode())
    return f.encrypt(json.dumps(creds).encode()).decode()


@router.post(
    "/projects/{slug}/publishing/platforms",
    response_model=PlatformResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_platform(
    slug: str,
    body: PlatformCreateRequest,
    session: SessionDep,
    _key: ApiKeyDep,
) -> PlatformResponse:
    project = await _get_project_or_404(slug, session)
    encrypted = _encrypt_credentials(body.credentials or {})
    platform = PublishingPlatformModel(
        project_id=project.id,
        name=body.name,
        platform_type=body.platform_type,
        api_base_url=body.api_base_url,
        credentials_enc=encrypted,
        rate_limit_rpm=body.rate_limit_rpm,
    )
    session.add(platform)
    await session.flush()
    await session.refresh(platform)
    return PlatformResponse.model_validate(platform)


@router.get("/projects/{slug}/publishing/platforms", response_model=list[PlatformResponse])
async def list_platforms(
    slug: str,
    session: SessionDep,
    _key: ApiKeyDep,
) -> list[PlatformResponse]:
    project = await _get_project_or_404(slug, session)
    result = await session.execute(
        select(PublishingPlatformModel).where(PublishingPlatformModel.project_id == project.id)
    )
    return [PlatformResponse.model_validate(p) for p in result.scalars()]


@router.post(
    "/projects/{slug}/publishing/schedule",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule(
    slug: str,
    body: ScheduleCreateRequest,
    session: SessionDep,
    _key: ApiKeyDep,
) -> ScheduleResponse:
    project = await _get_project_or_404(slug, session)

    # Verify platform belongs to this project
    plat_result = await session.execute(
        select(PublishingPlatformModel).where(
            PublishingPlatformModel.id == body.platform_id,
            PublishingPlatformModel.project_id == project.id,
        )
    )
    if plat_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Platform not found for this project")

    schedule = PublishingScheduleModel(
        project_id=project.id,
        platform_id=body.platform_id,
        cron_expression=body.cron_expression,
        timezone=body.timezone,
        start_chapter=body.start_chapter,
        current_chapter=body.start_chapter - 1,
        chapters_per_release=body.chapters_per_release,
        status="active",
    )
    session.add(schedule)
    await session.flush()
    await session.refresh(schedule)
    return ScheduleResponse.model_validate(schedule)


@router.get("/projects/{slug}/publishing/schedule", response_model=list[ScheduleResponse])
async def list_schedules(
    slug: str,
    session: SessionDep,
    _key: ApiKeyDep,
) -> list[ScheduleResponse]:
    project = await _get_project_or_404(slug, session)
    result = await session.execute(
        select(PublishingScheduleModel).where(PublishingScheduleModel.project_id == project.id)
    )
    return [ScheduleResponse.model_validate(s) for s in result.scalars()]


@router.get("/projects/{slug}/publishing/history", response_model=PublishHistoryResponse)
async def get_publishing_history(
    slug: str,
    session: SessionDep,
    _key: ApiKeyDep,
    offset: int = 0,
    limit: int = 50,
) -> PublishHistoryResponse:
    project = await _get_project_or_404(slug, session)

    total_result = await session.execute(
        select(func.count()).select_from(PublishingHistoryModel)
        .where(PublishingHistoryModel.project_id == project.id)
    )
    total = total_result.scalar_one()

    result = await session.execute(
        select(PublishingHistoryModel)
        .where(PublishingHistoryModel.project_id == project.id)
        .order_by(PublishingHistoryModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items = [PublishHistoryItem.model_validate(h) for h in result.scalars()]
    return PublishHistoryResponse(items=items, total=total)


@router.post(
    "/projects/{slug}/publishing/publish-now",
    status_code=status.HTTP_202_ACCEPTED,
)
async def publish_now(
    slug: str,
    schedule_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    _key: ApiKeyDep,
) -> dict:
    """Immediately publish the next unpublished chapter for a schedule."""
    from bestseller.services.publishing.registry import get_adapter  # noqa: PLC0415
    from bestseller.scheduler.jobs import publish_next_chapter  # noqa: PLC0415

    result = await publish_next_chapter(
        session=session,
        settings=settings,
        schedule_id=schedule_id,
    )
    return {"published": result}
