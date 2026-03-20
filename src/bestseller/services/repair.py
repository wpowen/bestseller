from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ProjectStatus, WorkflowStatus
from bestseller.domain.pipeline import ProjectRepairChapterSummary, ProjectRepairResult
from bestseller.infra.db.models import (
    ChapterModel,
    RewriteImpactModel,
    RewriteTaskModel,
    SceneCardModel,
)
from bestseller.services.consistency import review_project_consistency
from bestseller.services.exports import export_project_markdown
from bestseller.services.pipelines import run_chapter_pipeline
from bestseller.services.projects import get_project_by_slug
from bestseller.services.rewrite_impacts import refresh_rewrite_impacts
from bestseller.services.workflows import create_workflow_run, create_workflow_step_run
from bestseller.settings import AppSettings


WORKFLOW_TYPE_PROJECT_REPAIR = "project_repair"
ProgressCallback = Callable[[str, dict[str, Any] | None], None]


def _emit_progress(
    progress: ProgressCallback | None,
    stage: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if progress is None:
        return
    progress(stage, payload)


async def _load_pending_rewrite_tasks(
    session: AsyncSession,
    *,
    project_id: UUID,
) -> list[RewriteTaskModel]:
    return list(
        await session.scalars(
            select(RewriteTaskModel)
            .where(
                RewriteTaskModel.project_id == project_id,
                RewriteTaskModel.status.in_(["pending", "queued"]),
            )
            .order_by(
                RewriteTaskModel.priority.asc(),
                RewriteTaskModel.created_at.asc(),
            )
        )
    )


def _metadata_uuid(payload: dict[str, object] | None, key: str) -> UUID | None:
    if not payload:
        return None
    value = payload.get(key)
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


async def _chapter_number_from_scene_id(
    session: AsyncSession,
    scene_id: UUID | None,
) -> int | None:
    if scene_id is None:
        return None
    scene = await session.get(SceneCardModel, scene_id)
    if scene is None:
        return None
    chapter = await session.get(ChapterModel, scene.chapter_id)
    return chapter.chapter_number if chapter is not None else None


async def _chapter_number_from_chapter_id(
    session: AsyncSession,
    chapter_id: UUID | None,
) -> int | None:
    if chapter_id is None:
        return None
    chapter = await session.get(ChapterModel, chapter_id)
    return chapter.chapter_number if chapter is not None else None


async def _persisted_impacted_chapter_numbers(
    session: AsyncSession,
    *,
    rewrite_task_id: UUID,
) -> set[int]:
    chapter_numbers: set[int] = set()
    impacts = await session.scalars(
        select(RewriteImpactModel).where(RewriteImpactModel.rewrite_task_id == rewrite_task_id)
    )
    for impact in impacts:
        if impact.impacted_type == "chapter":
            chapter_number = await _chapter_number_from_chapter_id(session, impact.impacted_id)
        elif impact.impacted_type == "scene":
            chapter_number = await _chapter_number_from_scene_id(session, impact.impacted_id)
        else:
            chapter_number = None
        if chapter_number is not None:
            chapter_numbers.add(chapter_number)
    return chapter_numbers


async def _resolve_rewrite_task_chapter_numbers(
    session: AsyncSession,
    *,
    project_slug: str,
    task: RewriteTaskModel,
    refresh_impacts: bool,
) -> set[int]:
    metadata = task.metadata_json or {}
    chapter_numbers: set[int] = set()

    source_chapter_number = await _chapter_number_from_chapter_id(
        session,
        _metadata_uuid(metadata, "chapter_id") or task.trigger_source_id,
    )
    if task.trigger_type == "scene_review":
        source_chapter_number = await _chapter_number_from_chapter_id(
            session,
            _metadata_uuid(metadata, "chapter_id"),
        ) or await _chapter_number_from_scene_id(
            session,
            _metadata_uuid(metadata, "scene_id") or task.trigger_source_id,
        )

    if source_chapter_number is not None:
        chapter_numbers.add(source_chapter_number)

    if task.trigger_type == "scene_review":
        if refresh_impacts:
            analysis = await refresh_rewrite_impacts(
                session,
                project_slug,
                rewrite_task_id=task.id,
            )
            for impact in analysis.impacts:
                if impact.impacted_type == "chapter":
                    chapter_number = await _chapter_number_from_chapter_id(session, impact.impacted_id)
                elif impact.impacted_type == "scene":
                    chapter_number = await _chapter_number_from_scene_id(session, impact.impacted_id)
                else:
                    chapter_number = None
                if chapter_number is not None:
                    chapter_numbers.add(chapter_number)
        else:
            chapter_numbers.update(
                await _persisted_impacted_chapter_numbers(
                    session,
                    rewrite_task_id=task.id,
                )
            )

    return chapter_numbers


def _dedupe_sorted(values: Iterable[int]) -> list[int]:
    return sorted({value for value in values if value > 0})


async def run_project_repair(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    requested_by: str = "system",
    refresh_impacts: bool = True,
    export_markdown: bool = True,
    progress: ProgressCallback | None = None,
) -> ProjectRepairResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    pending_tasks = await _load_pending_rewrite_tasks(session, project_id=project.id)
    task_count = len(pending_tasks)
    _emit_progress(
        progress,
        "project_repair_started",
        {
            "project_slug": project_slug,
            "pending_rewrite_task_count": task_count,
        },
    )

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_PROJECT_REPAIR,
        status=WorkflowStatus.RUNNING,
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="collect_pending_rewrite_tasks",
        metadata={
            "project_slug": project_slug,
            "pending_rewrite_task_count": task_count,
            "refresh_impacts": refresh_impacts,
            "export_markdown": export_markdown,
        },
    )

    step_order = 1
    current_step_name = "collect_pending_rewrite_tasks"

    try:
        chapter_task_ids: dict[int, list[UUID]] = defaultdict(list)
        for task in pending_tasks:
            chapter_numbers = await _resolve_rewrite_task_chapter_numbers(
                session,
                project_slug=project_slug,
                task=task,
                refresh_impacts=refresh_impacts,
            )
            for chapter_number in chapter_numbers:
                chapter_task_ids[chapter_number].append(task.id)

        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "pending_rewrite_task_ids": [str(task.id) for task in pending_tasks],
                "target_chapter_numbers": _dedupe_sorted(chapter_task_ids.keys()),
            },
        )
        step_order += 1
        _emit_progress(
            progress,
            "project_repair_targets_collected",
            {
                "project_slug": project_slug,
                "pending_rewrite_task_count": task_count,
                "target_chapter_numbers": _dedupe_sorted(chapter_task_ids.keys()),
            },
        )

        superseded_task_count = 0
        if pending_tasks:
            current_step_name = "supersede_pending_rewrite_tasks"
            workflow_run.current_step = current_step_name
            for task in pending_tasks:
                task.status = "cancelled"
                task.error_log = None
                task.metadata_json = {
                    **(task.metadata_json or {}),
                    "superseded_by_workflow_run_id": str(workflow_run.id),
                    "superseded_reason": "project_repair",
                }
                superseded_task_count += 1
            await session.flush()
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "superseded_task_ids": [str(task.id) for task in pending_tasks],
                },
            )
            step_order += 1
            _emit_progress(
                progress,
                "project_repair_tasks_superseded",
                {
                    "project_slug": project_slug,
                    "superseded_task_count": superseded_task_count,
                },
            )

        processed_chapters: list[ProjectRepairChapterSummary] = []
        requires_human_review = False
        for chapter_number in _dedupe_sorted(chapter_task_ids.keys()):
            _emit_progress(
                progress,
                "project_repair_chapter_started",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                },
            )
            current_step_name = f"repair_chapter_{chapter_number}"
            workflow_run.current_step = current_step_name
            chapter_result = await run_chapter_pipeline(
                session,
                settings,
                project_slug,
                chapter_number,
                requested_by=requested_by,
                export_markdown=export_markdown,
            )
            processed_chapters.append(
                ProjectRepairChapterSummary(
                    chapter_number=chapter_number,
                    workflow_run_id=chapter_result.workflow_run_id,
                    source_task_ids=chapter_task_ids[chapter_number],
                    requires_human_review=chapter_result.requires_human_review,
                )
            )
            requires_human_review = requires_human_review or chapter_result.requires_human_review
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "chapter_number": chapter_number,
                    "chapter_workflow_run_id": str(chapter_result.workflow_run_id),
                    "source_task_ids": [str(task_id) for task_id in chapter_task_ids[chapter_number]],
                    "requires_human_review": chapter_result.requires_human_review,
                },
            )
            step_order += 1
            _emit_progress(
                progress,
                "project_repair_chapter_completed",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "workflow_run_id": str(chapter_result.workflow_run_id),
                    "requires_human_review": chapter_result.requires_human_review,
                },
            )

        export_artifact_id: UUID | None = None
        output_path: str | None = None
        if export_markdown:
            _emit_progress(
                progress,
                "project_repair_export_started",
                {"project_slug": project_slug},
            )
            current_step_name = "export_project_markdown"
            workflow_run.current_step = current_step_name
            artifact, artifact_path = await export_project_markdown(
                session,
                settings,
                project_slug,
                created_by_run_id=workflow_run.id,
            )
            export_artifact_id = artifact.id
            output_path = str(artifact_path.resolve())
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "export_artifact_id": str(artifact.id),
                    "output_path": output_path,
                },
            )
            step_order += 1
            _emit_progress(
                progress,
                "project_repair_export_completed",
                {
                    "project_slug": project_slug,
                    "export_artifact_id": str(artifact.id),
                    "output_path": output_path,
                },
            )

        current_step_name = "review_project_consistency"
        workflow_run.current_step = current_step_name
        review_result, report, quality = await review_project_consistency(
            session,
            settings,
            project_slug,
            workflow_run_id=workflow_run.id,
            expect_project_export=export_markdown,
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "review_report_id": str(report.id),
                "quality_score_id": str(quality.id),
                "verdict": review_result.verdict,
            },
        )
        step_order += 1
        _emit_progress(
            progress,
            "project_repair_review_completed",
            {
                "project_slug": project_slug,
                "review_report_id": str(report.id),
                "quality_score_id": str(quality.id),
                "verdict": review_result.verdict,
            },
        )

        remaining_pending_rewrite_count = int(
            await session.scalar(
                select(func.count())
                .select_from(RewriteTaskModel)
                .where(
                    RewriteTaskModel.project_id == project.id,
                    RewriteTaskModel.status.in_(["pending", "queued"]),
                )
            )
            or 0
        )
        requires_human_review = (
            requires_human_review
            or review_result.verdict != "pass"
            or remaining_pending_rewrite_count > 0
        )
        project.status = (
            ProjectStatus.REVISING.value if requires_human_review else ProjectStatus.WRITING.value
        )

        workflow_run.status = (
            WorkflowStatus.WAITING_HUMAN.value
            if requires_human_review
            else WorkflowStatus.COMPLETED.value
        )
        workflow_run.current_step = (
            "waiting_human_review" if requires_human_review else "completed"
        )
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "superseded_task_count": superseded_task_count,
            "processed_chapter_count": len(processed_chapters),
            "review_report_id": str(report.id),
            "quality_score_id": str(quality.id),
            "final_verdict": review_result.verdict,
            "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
            "remaining_pending_rewrite_count": remaining_pending_rewrite_count,
            "requires_human_review": requires_human_review,
        }
        await session.flush()
        _emit_progress(
            progress,
            "project_repair_completed",
            {
                "project_slug": project.slug,
                "workflow_run_id": str(workflow_run.id),
                "final_verdict": review_result.verdict,
                "remaining_pending_rewrite_count": remaining_pending_rewrite_count,
                "requires_human_review": requires_human_review,
                "output_path": output_path,
            },
        )

        return ProjectRepairResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            project_slug=project.slug,
            pending_rewrite_task_count=task_count,
            superseded_task_count=superseded_task_count,
            processed_chapters=processed_chapters,
            review_report_id=report.id,
            quality_score_id=quality.id,
            final_verdict=review_result.verdict,
            export_artifact_id=export_artifact_id,
            output_path=output_path,
            remaining_pending_rewrite_count=remaining_pending_rewrite_count,
            requires_human_review=requires_human_review,
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
        raise
