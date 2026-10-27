"""Window completion uses adopted bodies and exact knowledge, never row counts."""
from uuid import UUID
from collections.abc import Callable, Sequence, Set
from typing import Any, TypedDict
from sqlalchemy.ext.asyncio import AsyncSession
from bestseller.settings import AppSettings, apply_runtime_llm_profile, load_settings
from bestseller.domain.pipeline import ProjectPipelineChapterSummary, ProjectPipelineResult
from bestseller.domain.planning import AutowriteResult

from sqlalchemy import select

from bestseller.infra.db.models import ChapterModel, ChapterStateSnapshotModel, ProjectModel, WorkflowRunModel
from bestseller.services.chapter_knowledge_state import adopted_bible_status, adopted_knowledge_status


class WindowProgress(TypedDict):
    window: dict[str, Any]
    whole_book_chapters: int
    adopted: list[int]
    knowledge_closed: list[int]
    pending: list[int]
    window_complete: bool
    whole_book_complete: bool
    manuscript_coverage_complete: bool
    narrative_closure_verified: bool
    sources: list[dict[str, Any]]
    review_debt: list[int]
    project_review_debt: bool


ProgressCallback = Callable[[str, dict[str, Any]], None]
from bestseller.services.creation_configuration import read_window


async def assert_window_runnable(session: AsyncSession, project: ProjectModel) -> None:
    from bestseller.services.gate_registry import project_blocks_all_prose_generation, project_resume_is_terminally_blocked
    from bestseller.services.production_control import load_control_state
    from bestseller.services.prefix_window_activation import verify_activated_prefix_window
    await verify_activated_prefix_window(session, apply_runtime_llm_profile(load_settings()), project)
    if project_blocks_all_prose_generation(project) or project_resume_is_terminally_blocked(project.metadata_json):
        raise ValueError("execution_window_structurally_blocked")
    control = await load_control_state(session, project.id)
    if control.halted or str(project.status) in {"paused", "cancelled", "archived"}:
        raise ValueError("execution_window_halted")


async def window_progress(session: AsyncSession, project: ProjectModel) -> WindowProgress | None:
    window = read_window(project)
    if window is None:
        return None
    from bestseller.services.exports import _load_adopted_export_draft
    from bestseller.services.feedback import validate_adopted_knowledge_source
    from bestseller.services.continuity import snapshot_evidence_statuses
    rows = list(await session.scalars(select(ChapterModel).where(ChapterModel.project_id == project.id,
        ChapterModel.chapter_number.between(window.start, window.end)).execution_options(populate_existing=True)))
    by_number = {row.chapter_number: row for row in rows}
    snapshots = {row.chapter_id: row for row in await session.scalars(
        select(ChapterStateSnapshotModel).where(
            ChapterStateSnapshotModel.project_id == project.id,
            ChapterStateSnapshotModel.chapter_id.in_([row.id for row in rows]),
        ).execution_options(populate_existing=True))}
    current_snapshots = await snapshot_evidence_statuses(
        session, project_id=project.id, chapter_numbers=by_number)
    adopted: list[int] = []
    closed: list[int] = []
    missing: list[int] = []
    sources: list[dict[str, Any]] = []
    review_debt: list[int] = []
    bible_sources: dict[UUID, dict[str, Any]] = {}
    for number in sorted(window.numbers()):
        chapter = by_number.get(number)
        if chapter is None:
            missing.append(number)
            continue
        metadata = chapter.metadata_json or {}
        if metadata.get("requires_human_review") or metadata.get("whole_book_quality_gate_blocked"):
            review_debt.append(number)
        draft = await _load_adopted_export_draft(session, chapter)
        if draft is None:
            missing.append(number)
            continue
        lineage, rejection = await validate_adopted_knowledge_source(session,
            project_id=project.id, chapter_id=chapter.id,
            source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md, lock=False)
        if rejection is not None:
            missing.append(number)
            continue
        adopted.append(number)
        knowledge = adopted_knowledge_status(chapter, draft, snapshots.get(chapter.id), lineage,
            require_bible=True)
        if adopted_bible_status(chapter, lineage) == 'closed':
            bible_sources[chapter.id] = lineage
        if knowledge['snapshot_complete']:
            knowledge['snapshot_complete'] = current_snapshots.get(number, False)
        knowledge['closed'] = (
            knowledge['feedback_complete'] and knowledge['snapshot_complete']
            and knowledge['bible_complete']
        )
        if knowledge['closed']:
            closed.append(number)
        else:
            missing.append(number)
        sources.append({"chapter_number": number, **lineage, **knowledge})
    if bible_sources:
        from bestseller.services.story_bible_projection import adopted_bible_projection_statuses

        projections = await adopted_bible_projection_statuses(
            session, project_id=project.id, source_lineages=bible_sources,
        )
        for source in sources:
            chapter_id = UUID(source['chapter_id'])
            if chapter_id in projections and projections[chapter_id] not in {'current', 'legacy_current'}:
                source['bible_complete'] = source['closed'] = False
        closed = [source['chapter_number'] for source in sources if source['closed']]
        missing = [number for number in sorted(window.numbers()) if number not in closed]
    return {"window": window.model_dump(mode="json"), "whole_book_chapters": project.target_chapters,
        "adopted": adopted, "knowledge_closed": closed, "pending": missing,
        "window_complete": not missing, "whole_book_complete": False,
        "manuscript_coverage_complete": len(closed) == project.target_chapters,
        "narrative_closure_verified": False, "sources": sources, "review_debt": review_debt,
        "project_review_debt": bool((project.metadata_json or {}).get("whole_book_quality_gate_blocked"))}


async def finish_project_window(
    session: AsyncSession, settings: AppSettings, project: ProjectModel,
    workflow: WorkflowRunModel, chapter_results: Sequence[ProjectPipelineChapterSummary], *,
    export_markdown: bool = False, progress: ProgressCallback | None = None,
    scope_numbers: Set[int] | None = None,
) -> ProjectPipelineResult:
    from bestseller.domain.pipeline import ProjectPipelineResult
    from bestseller.services.exports import export_execution_window_markdown
    state = await window_progress(session, project)
    if state is None:
        raise ValueError("execution_window_configuration_required")
    complete = state["window_complete"]
    review_debt = bool(state["review_debt"] or state["project_review_debt"]) or bool((workflow.metadata_json or {}).get("requires_human_review")) or any(
        getattr(row, "requires_human_review", False) for row in chapter_results)
    halted = bool((workflow.metadata_json or {}).get("execution_window_halted"))
    window = read_window(project)
    if window is None:
        raise ValueError("execution_window_configuration_required")
    blocked = bool(set(state["pending"]) & (set(scope_numbers) if scope_numbers is not None else window.numbers()))
    verdict = "execution_window_halted" if halted else "execution_window_requires_review" if review_debt else "execution_window_completed" if complete else "execution_window_incomplete" if blocked else "execution_window_progress"
    workflow.current_step = verdict
    workflow.status = "cancelled" if halted else "machine_blocked" if blocked or review_debt else "completed"
    workflow.metadata_json = {**(workflow.metadata_json or {}), "execution_window_receipt": state}
    # A full manuscript still requires its ending/content acceptance. A window
    # can never set a thousand-chapter project to completed.
    project.status = "paused" if halted else "revising" if blocked or review_debt else "writing"
    artifact, path = None, None
    if export_markdown and complete and not review_debt and not halted:
        artifact, path = await export_execution_window_markdown(session, settings, project.slug,
            created_by_run_id=workflow.id)
    await session.flush()
    await session.commit()
    if progress:
        progress(verdict, {"project_slug": project.slug, **state})
    return ProjectPipelineResult(workflow_run_id=workflow.id, project_id=project.id,
        project_slug=project.slug, chapter_results=list(chapter_results), final_verdict=verdict,
        requires_human_review=blocked or review_debt or halted, export_artifact_id=artifact.id if artifact else None,
        output_path=str(path) if path else None)


def as_autowrite_window_result(
    project: ProjectModel, project_result: ProjectPipelineResult, *,
    planning_workflow_run_id: UUID | None = None,
) -> AutowriteResult:
    from bestseller.domain.planning import AutowriteResult
    window = read_window(project)
    if window is None:
        raise ValueError("execution_window_configuration_required")
    return AutowriteResult(project_id=project.id, project_slug=project.slug,
        planning_workflow_run_id=planning_workflow_run_id or UUID(int=0),
        project_workflow_run_id=project_result.workflow_run_id,
        chapter_count=len(project_result.chapter_results), final_verdict=project_result.final_verdict,
        requires_human_review=project_result.requires_human_review,
        export_artifact_id=project_result.export_artifact_id, output_path=project_result.output_path,
        export_status="window_exported" if project_result.export_artifact_id else "window_not_exported",
        repair_attempted=False, execution_window=window.model_dump(mode="json"), whole_book_complete=False)


async def completed_window_resume(
    session: AsyncSession, settings: AppSettings, project: ProjectModel, *,
    export_markdown: bool = False, progress: ProgressCallback | None = None,
) -> ProjectPipelineResult | None:
    if read_window(project) is None:
        return None
    await assert_window_runnable(session, project)
    state = await window_progress(session, project)
    if state is None:
        raise ValueError("execution_window_configuration_required")
    if set(state["adopted"]) - set(state["knowledge_closed"]):
        await resume_window_knowledge(session, settings, project, state)
        state = await window_progress(session, project)
    if state is None:
        raise ValueError("execution_window_configuration_required")
    if not state["window_complete"]:
        return None
    from bestseller.infra.db.models import WorkflowRunModel
    workflow = WorkflowRunModel(project_id=project.id, workflow_type="execution_window_resume",
        scope_type="project", scope_id=project.id, status="completed", metadata_json={})
    session.add(workflow)
    await session.flush()
    return await finish_project_window(session, settings, project, workflow, [],
        export_markdown=export_markdown, progress=progress)


async def resume_window_knowledge(
    session: AsyncSession, settings: AppSettings, project: ProjectModel, state: WindowProgress,
) -> None:
    """Resume only missing exact adopted-body knowledge; never rewrite prose."""
    from bestseller.services.adopted_knowledge_resume import resume_adopted_knowledge
    from bestseller.services.exports import _load_adopted_export_draft
    if not settings.pipeline.enable_chapter_feedback:
        raise ValueError("execution_window_requires_knowledge_feedback")
    pending = (set(state["adopted"]) - set(state["knowledge_closed"])) | {
        source['chapter_number'] for source in state['sources']
        if source.get('bible_complete') is False
    }
    for number in sorted(pending):
        await assert_window_runnable(session, project)
        chapter = await session.scalar(select(ChapterModel).where(
            ChapterModel.project_id == project.id, ChapterModel.chapter_number == number,
        ).execution_options(populate_existing=True))
        if chapter is None:
            raise ValueError("execution_window_knowledge_source_changed")
        draft = await _load_adopted_export_draft(session, chapter)
        if draft is None:
            raise ValueError("execution_window_knowledge_source_changed")
        expected = next((source for source in state['sources']
                         if source['chapter_number'] == number), {})

        async def runnable() -> None:
            await assert_window_runnable(session, project)

        await resume_adopted_knowledge(
            session, settings, project=project, chapter=chapter, draft=draft,
            expected_source=expected, assert_runnable=runnable,
            workflow_type="execution_window_knowledge_resume",
            require_bible=True,
        )
    current = await window_progress(session, project)
    if current is None:
        raise ValueError("execution_window_configuration_required")
    if set(current["adopted"]) - set(current["knowledge_closed"]) or any(
        source.get('bible_complete') is False for source in current['sources']
    ):
        raise ValueError("execution_window_knowledge_resume_incomplete")
