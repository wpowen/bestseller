from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from bestseller.api.deps import ApiKeyDep, SessionDep, SettingsDep
from bestseller.infra.db.models import ExportArtifactModel, ProjectModel

router = APIRouter(tags=["exports"])


class ExportResponse(BaseModel):
    project_slug: str
    format: str
    file_path: str
    word_count: int | None = None


@router.post("/projects/{slug}/export/{fmt}", response_model=ExportResponse)
async def export_novel(
    slug: str,
    fmt: str,
    session: SessionDep,
    settings: SettingsDep,
    _key: ApiKeyDep,
) -> ExportResponse:
    if fmt not in {"markdown", "docx", "epub", "pdf"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported format: {fmt}")

    result = await session.execute(select(ProjectModel).where(ProjectModel.slug == slug))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{slug}' not found")

    from bestseller.services import exports as export_svc  # noqa: PLC0415

    if fmt == "markdown":
        export_result = await export_svc.export_project_markdown(
            session=session, settings=settings, project_slug=slug
        )
    elif fmt == "docx":
        export_result = await export_svc.export_project_docx(
            session=session, settings=settings, project_slug=slug
        )
    elif fmt == "epub":
        export_result = await export_svc.export_project_epub(
            session=session, settings=settings, project_slug=slug
        )
    else:
        export_result = await export_svc.export_project_pdf(
            session=session, settings=settings, project_slug=slug
        )

    return ExportResponse(
        project_slug=slug,
        format=fmt,
        file_path=str(export_result.file_path),
        word_count=getattr(export_result, "word_count", None),
    )


@router.get("/projects/{slug}/exports/{artifact_id}/download")
async def download_export(
    slug: str,
    artifact_id: str,
    session: SessionDep,
    settings: SettingsDep,
    _key: ApiKeyDep,
) -> FileResponse:
    try:
        uid = UUID(artifact_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact ID") from exc

    # Verify the project exists
    proj_result = await session.execute(select(ProjectModel).where(ProjectModel.slug == slug))
    project = proj_result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{slug}' not found")

    # Look up artifact scoped to this project
    result = await session.execute(
        select(ExportArtifactModel).where(
            ExportArtifactModel.id == uid,
            ExportArtifactModel.project_id == project.id,
        )
    )
    artifact = result.scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export artifact not found")

    file_path = Path(artifact.storage_uri)

    # Path traversal protection: ensure file is under the configured output directory
    allowed_root = Path(settings.output.base_dir).resolve()
    resolved = file_path.resolve()
    if not str(resolved).startswith(str(allowed_root)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if not resolved.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export file not found on disk")

    return FileResponse(path=str(resolved), filename=resolved.name)
