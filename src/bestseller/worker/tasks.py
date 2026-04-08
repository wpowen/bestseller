from __future__ import annotations

import logging
from typing import Any

from bestseller.infra.db.session import get_server_session
from bestseller.settings import get_settings
from bestseller.worker.progress import RedisProgressReporter, make_sync_callback

logger = logging.getLogger(__name__)


async def run_autowrite_task(ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Full end-to-end autowrite pipeline.

    Expects payload: {"project_slug": str, "premise": str | None}
    The project must already exist in the DB (created via POST /api/v1/projects).
    """
    from bestseller.services.pipelines import run_autowrite_pipeline  # noqa: PLC0415
    from bestseller.services.projects import get_project_by_slug  # noqa: PLC0415
    from bestseller.domain.project import ProjectCreate, ProjectType  # noqa: PLC0415

    settings = get_settings()
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)

    project_slug = payload["project_slug"]

    async with get_server_session() as session:
        # Load existing project to build ProjectCreate payload
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"Project '{project_slug}' not found")

        # Build ProjectCreate from the existing project record
        meta = project.metadata_json or {}
        project_payload = ProjectCreate(
            slug=project.slug,
            title=project.title,
            genre=project.genre,
            sub_genre=project.sub_genre,
            audience=project.audience,
            target_word_count=project.target_word_count,
            target_chapters=project.target_chapters,
            project_type=ProjectType(project.project_type),
            metadata=dict(meta),
        )

        premise = payload.get("premise") or str(meta.get("premise") or project.title)

        result = await run_autowrite_pipeline(
            session=session,
            settings=settings,
            project_payload=project_payload,
            premise=premise,
            progress=make_sync_callback(reporter),
        )

    await reporter.emit("completed", {"result": "autowrite_done"})
    return result.model_dump(mode="json")


async def run_project_pipeline_task(ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Project-level pipeline (draft all chapters)."""
    from bestseller.services.pipelines import run_project_pipeline  # noqa: PLC0415

    settings = get_settings()
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)

    async with get_server_session() as session:
        result = await run_project_pipeline(
            session=session,
            settings=settings,
            project_slug=payload["project_slug"],
            progress=make_sync_callback(reporter),
        )

    await reporter.emit("completed", {"result": "project_pipeline_done"})
    return result.model_dump(mode="json")


async def run_chapter_pipeline_task(ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Single chapter pipeline (no progress callback — pipeline doesn't support it)."""
    from bestseller.services.pipelines import run_chapter_pipeline  # noqa: PLC0415

    settings = get_settings()
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)

    await reporter.emit("started", {"chapter_number": payload["chapter_number"]})

    async with get_server_session() as session:
        result = await run_chapter_pipeline(
            session=session,
            settings=settings,
            project_slug=payload["project_slug"],
            chapter_number=payload["chapter_number"],
        )

    await reporter.emit("completed", {"result": "chapter_pipeline_done"})
    return result.model_dump(mode="json")
