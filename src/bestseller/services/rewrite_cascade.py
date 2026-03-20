from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.rewrite import (
    RewriteCascadeChapterResult,
    RewriteCascadeResult,
)
from bestseller.infra.db.models import ChapterModel, SceneCardModel
from bestseller.services.pipelines import run_chapter_pipeline
from bestseller.services.projects import get_project_by_slug
from bestseller.services.rewrite_impacts import list_rewrite_impacts, refresh_rewrite_impacts
from bestseller.settings import AppSettings


async def run_rewrite_cascade(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    rewrite_task_id: UUID | None = None,
    chapter_number: int | None = None,
    scene_number: int | None = None,
    requested_by: str = "system",
    refresh: bool = True,
    export_markdown: bool = False,
) -> RewriteCascadeResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    if refresh or rewrite_task_id is None:
        analysis = await refresh_rewrite_impacts(
            session,
            project_slug,
            rewrite_task_id=rewrite_task_id,
            chapter_number=chapter_number,
            scene_number=scene_number,
        )
        active_rewrite_task_id = analysis.rewrite_task_id
        impacts = analysis.impacts
    else:
        if rewrite_task_id is None:
            raise ValueError("rewrite_task_id is required when refresh is disabled.")
        active_rewrite_task_id = rewrite_task_id
        persisted = await list_rewrite_impacts(session, project_slug, rewrite_task_id=rewrite_task_id)
        impacts = [
            type(
                "ImpactStub",
                (),
                {
                    "impacted_type": item.impacted_type,
                    "impacted_id": item.impacted_id,
                },
            )()
            for item in persisted
        ]

    impacted_chapters: set[int] = set()
    for impact in impacts:
        if impact.impacted_type == "chapter":
            chapter = await session.get(ChapterModel, impact.impacted_id)
            if chapter is not None:
                impacted_chapters.add(chapter.chapter_number)
        elif impact.impacted_type == "scene":
            scene = await session.get(SceneCardModel, impact.impacted_id)
            if scene is None:
                continue
            chapter = await session.get(ChapterModel, scene.chapter_id)
            if chapter is not None:
                impacted_chapters.add(chapter.chapter_number)

    processed_chapters: list[RewriteCascadeChapterResult] = []
    for impacted_chapter_number in sorted(impacted_chapters):
        result = await run_chapter_pipeline(
            session,
            settings,
            project_slug,
            impacted_chapter_number,
            requested_by=requested_by,
            export_markdown=export_markdown,
        )
        processed_chapters.append(
            RewriteCascadeChapterResult(
                chapter_number=impacted_chapter_number,
                workflow_run_id=result.workflow_run_id,
                requires_human_review=result.requires_human_review,
            )
        )

    return RewriteCascadeResult(
        rewrite_task_id=active_rewrite_task_id,
        project_id=project.id,
        processed_chapters=processed_chapters,
        impact_count=len(impacts),
        refreshed=refresh,
    )
