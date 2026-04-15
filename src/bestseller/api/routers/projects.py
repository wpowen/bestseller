from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from bestseller.api.deps import ApiKeyDep, SessionDep, SettingsDep  # noqa: F401
from bestseller.api.schemas.projects import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
)
from bestseller.infra.db.models import ProjectModel

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(
    session: SessionDep,
    _key: ApiKeyDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=200),
) -> ProjectListResponse:
    total_result = await session.execute(select(func.count()).select_from(ProjectModel))
    total = total_result.scalar_one()

    result = await session.execute(
        select(ProjectModel)
        .order_by(ProjectModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items = [ProjectResponse.model_validate(p) for p in result.scalars()]
    return ProjectListResponse(items=items, total=total)


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateRequest,
    session: SessionDep,
    settings: SettingsDep,
    _key: ApiKeyDep,
) -> ProjectResponse:
    from bestseller.services.projects import create_project as svc_create  # noqa: PLC0415
    from bestseller.domain.project import ProjectCreate  # noqa: PLC0415

    project_create = ProjectCreate(
        slug=body.slug,
        title=body.title,
        genre=body.genre,
        target_word_count=body.target_word_count,
        target_chapters=body.target_chapters,
        audience=body.audience,
        metadata={"premise": body.premise, "writing_preset": body.writing_preset},
    )
    project = await svc_create(session=session, settings=settings, payload=project_create)
    return ProjectResponse.model_validate(project)


@router.get("/projects/{slug}", response_model=ProjectResponse)
async def get_project(
    slug: str,
    session: SessionDep,
    _key: ApiKeyDep,
) -> ProjectResponse:
    result = await session.execute(select(ProjectModel).where(ProjectModel.slug == slug))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{slug}' not found")
    return ProjectResponse.model_validate(project)


@router.get("/projects/{slug}/structure")
async def get_project_structure(
    slug: str,
    session: SessionDep,
    _key: ApiKeyDep,
) -> dict:
    from bestseller.services.inspection import build_project_structure  # noqa: PLC0415

    structure = await build_project_structure(session=session, project_slug=slug)
    return structure.model_dump(mode="json")
