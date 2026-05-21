# ruff: noqa: ANN401, RUF002
"""番茄短故事专用 autowrite pipeline。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, ProjectType
from bestseller.domain.fanqie_short import (
    DEFAULT_UNLOCK_LINE_RATIO,
    is_fanqie_short_project,
)
from bestseller.domain.planning import AutowriteResult
from bestseller.domain.project import ProjectCreate
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    RewriteTaskModel,
)
from bestseller.services.drafts import count_words, sanitize_novel_markdown_content
from bestseller.services.fanqie_short_export import (
    export_fanqie_short_markdown,
    export_fanqie_short_rejected_draft,
)
from bestseller.services.fanqie_short_finalizer import finalize_fanqie_short_for_upload
from bestseller.services.fanqie_short_gate_v2 import (
    build_fanqie_short_v2_rewrite_instructions,
    build_fanqie_short_v2_rewrite_routes,
)
from bestseller.services.fanqie_short_planner import (
    build_fanqie_segment_outline_batch,
    generate_fanqie_beat_sheet,
    persist_fanqie_chapter_outline,
)
from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story
from bestseller.services.fanqie_short_ranking_gate import (
    FanqieRankingFinding,
    FanqieRankingGateReport,
    build_fanqie_ranking_rewrite_instructions,
)
from bestseller.services.pipelines import (
    ProgressCallback,
    _checkpoint_commit,
    _emit_progress,
    _ensure_project_invariants,
    run_chapter_pipeline,
)
from bestseller.services.planner import generate_foundation_plan
from bestseller.services.projects import create_project, get_project_by_slug
from bestseller.services.workflows import (
    get_latest_planning_artifact,
    materialize_latest_chapter_outline_batch,
    materialize_latest_narrative_graph,
    materialize_latest_story_bible,
)
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


def _ranking_finding_chapter_numbers(
    finding: FanqieRankingFinding,
    *,
    segment_total: int,
    unlock_segment: int,
) -> list[int]:
    target = finding.target
    phase = finding.phase
    if phase == "opening" or target.startswith("opening"):
        return [1]
    if phase == "unlock" or target.startswith("unlock"):
        return list(range(1, max(1, min(unlock_segment, segment_total)) + 1))
    if phase == "closure" or target == "ending":
        return [segment_total]
    return list(range(1, segment_total + 1))


def _short_v2_finding_chapter_numbers(
    finding: FanqieRankingFinding,
    *,
    segment_total: int,
    unlock_segment: int,
) -> list[int]:
    """Map v2 whole-piece findings to concrete segment repair targets."""
    target = finding.target
    phase = finding.phase
    code = finding.code
    if (
        target == "title"
        or target.startswith("opening")
        or phase in {"title", "opening"}
        or code.startswith("first_screen")
    ):
        return [1]
    if phase == "unlock" or target.startswith("unlock"):
        return list(range(1, max(1, min(unlock_segment, segment_total)) + 1))
    if phase == "closure" or target == "ending":
        return [segment_total]
    if phase in {"payoff", "anti_longform"} or target == "whole_story":
        return list(range(1, segment_total + 1))
    return _ranking_finding_chapter_numbers(
        finding,
        segment_total=segment_total,
        unlock_segment=unlock_segment,
    )


def _short_v2_rewrite_strategy(code: str) -> str:
    safe_code = "".join(char if char.isalnum() or char == "_" else "_" for char in code)
    return f"fix_{safe_code}"[:64]


async def _chapter_by_number(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_number: int,
) -> ChapterModel | None:
    return await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project_id,
            ChapterModel.chapter_number == chapter_number,
        )
    )


async def _persist_fanqie_ranking_gate_failure(
    session: AsyncSession,
    project: ProjectModel,
    *,
    report: FanqieRankingGateReport,
    unlock_segment: int,
) -> int:
    """Persist ranking-gate failures as project metadata and rewrite tasks."""
    meta = dict(project.metadata_json or {})
    meta["fanqie_short_ranking_gate_report"] = report.to_dict()
    meta["fanqie_short_ready_for_upload"] = report.passed
    project.metadata_json = meta
    if report.passed:
        return 0

    critical_findings = [finding for finding in report.findings if finding.severity == "critical"]
    if not critical_findings:
        return 0

    created = 0
    segment_total = max(int(getattr(project, "target_chapters", 0) or 0), 1)
    for finding in critical_findings:
        for chapter_number in _ranking_finding_chapter_numbers(
            finding,
            segment_total=segment_total,
            unlock_segment=unlock_segment,
        ):
            chapter = await _chapter_by_number(
                session,
                project_id=project.id,
                chapter_number=chapter_number,
            )
            if chapter is None:
                continue
            task = RewriteTaskModel(
                project_id=project.id,
                parent_task_id=None,
                trigger_type="fanqie_ranking_gate",
                trigger_source_id=chapter.id,
                rewrite_strategy=f"fix_{finding.phase}",
                priority=1,
                status="pending",
                instructions=build_fanqie_ranking_rewrite_instructions(
                    FanqieRankingGateReport(
                        passed=False,
                        phase=finding.phase,
                        findings=(finding,),
                    )
                ),
                context_required=["chapter_draft", "fanqie_short_ranking_contract"],
                metadata_json={
                    "chapter_id": str(chapter.id),
                    "chapter_number": chapter.chapter_number,
                    "finding": finding.to_dict(),
                    "source": "fanqie_short_ranking_gate",
                },
            )
            session.add(task)
            chapter.status = "revision"
            chapter.production_state = "blocked"
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "blocked_by_fanqie_ranking_gate": True,
                "fanqie_ranking_gate_code": finding.code,
                "fanqie_ranking_gate_message": finding.message,
            }
            created += 1
    await session.flush()
    return created


async def _persist_fanqie_short_v2_gate_failure(
    session: AsyncSession,
    project: ProjectModel,
    *,
    report: FanqieRankingGateReport,
    unlock_segment: int,
) -> int:
    """Persist v2 short-story failures as worker-routed repair tasks."""
    routes = build_fanqie_short_v2_rewrite_routes(report)
    route_by_code = {route.finding_code: route for route in routes}
    meta = dict(project.metadata_json or {})
    meta["fanqie_short_v2_gate_report"] = report.to_dict()
    meta["fanqie_short_v2_rewrite_routes"] = [
        route.model_dump(mode="json") for route in routes
    ]
    if not report.passed:
        meta["fanqie_short_ready_for_upload"] = False
    project.metadata_json = meta
    if report.passed:
        return 0

    critical_findings = [finding for finding in report.findings if finding.severity == "critical"]
    if not critical_findings:
        return 0

    created = 0
    segment_total = max(int(getattr(project, "target_chapters", 0) or 0), 1)
    for finding in critical_findings:
        route = route_by_code.get(finding.code)
        route_payload = route.model_dump(mode="json") if route is not None else None
        instructions = build_fanqie_short_v2_rewrite_instructions(
            FanqieRankingGateReport(
                passed=False,
                phase=finding.phase,
                findings=(finding,),
            )
        )
        for chapter_number in _short_v2_finding_chapter_numbers(
            finding,
            segment_total=segment_total,
            unlock_segment=unlock_segment,
        ):
            chapter = await _chapter_by_number(
                session,
                project_id=project.id,
                chapter_number=chapter_number,
            )
            if chapter is None:
                continue
            task = RewriteTaskModel(
                project_id=project.id,
                parent_task_id=None,
                trigger_type="fanqie_short_v2_gate",
                trigger_source_id=chapter.id,
                rewrite_strategy=_short_v2_rewrite_strategy(finding.code),
                priority=route.priority if route is not None else 1,
                status="pending",
                instructions=instructions,
                context_required=[
                    "chapter_draft",
                    "fanqie_short_v2_contract",
                    "fanqie_short_worker_route",
                ],
                metadata_json={
                    "chapter_id": str(chapter.id),
                    "chapter_number": chapter.chapter_number,
                    "finding": finding.to_dict(),
                    "source": "fanqie_short_v2_gate",
                    "source_report_phase": report.phase,
                    "worker_route": route_payload,
                    "worker": route.worker if route is not None else "RankingGateWorker",
                },
            )
            session.add(task)
            chapter.status = "revision"
            chapter.production_state = "blocked"
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "blocked_by_fanqie_short_v2_gate": True,
                "fanqie_short_v2_gate_code": finding.code,
                "fanqie_short_v2_gate_message": finding.message,
                "fanqie_short_v2_worker": (
                    route.worker if route is not None else "RankingGateWorker"
                ),
            }
            created += 1
    await session.flush()
    return created


async def _load_chapter_drafts(
    session: AsyncSession,
    project_id: UUID,
) -> list[tuple[ChapterModel, ChapterDraftVersionModel]]:
    chapters = list(
        await session.scalars(
            select(ChapterModel)
            .where(ChapterModel.project_id == project_id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )
    payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]] = []
    for chapter in chapters:
        draft = await session.scalar(
            select(ChapterDraftVersionModel)
            .where(
                ChapterDraftVersionModel.chapter_id == chapter.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .order_by(ChapterDraftVersionModel.version_no.desc())
        )
        if draft is not None and (draft.content_md or "").strip():
            payloads.append((chapter, draft))
    return payloads


def _write_fanqie_source_artifacts(
    output_dir: Path,
    *,
    project: ProjectModel,
    premise: str,
    beat_sheet: Any,
    outline_payload: dict[str, Any],
    book_spec: dict[str, Any] | None = None,
    cast_spec: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Write planning/source artifacts so repair audit can operate autonomously."""
    output_dir.mkdir(parents=True, exist_ok=True)
    project_path = output_dir / "project.md"
    bible_path = output_dir / "story-bible.md"
    outline_path = output_dir / "outline.md"

    project_path.write_text(
        "\n".join(
            [
                f"# {project.title}",
                "",
                f"- slug: {project.slug}",
                f"- genre: {project.genre}",
                f"- sub_genre: {getattr(project, 'sub_genre', None) or ''}",
                f"- target_words: {project.target_word_count}",
                f"- segment_count: {project.target_chapters}",
                "",
                "## Premise",
                premise or "",
            ]
        ),
        encoding="utf-8",
    )
    bible_path.write_text(
        json.dumps(
            {
                "book_spec": book_spec or {},
                "cast_spec": cast_spec or {},
                "fanqie_beat_sheet": (
                    beat_sheet.model_dump(mode="json")
                    if hasattr(beat_sheet, "model_dump")
                    else beat_sheet
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    outline_path.write_text(
        json.dumps(outline_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "project_source_path": str(project_path.resolve()),
        "story_bible_source_path": str(bible_path.resolve()),
        "outline_source_path": str(outline_path.resolve()),
    }


def assemble_fanqie_whole_story(
    project: ProjectModel,
    chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]],
) -> str:
    """拼接为单篇正文（段间空行，不加连载章标题）。"""
    parts: list[str] = []
    for _chapter, draft in chapter_payloads:
        body = sanitize_novel_markdown_content(draft.content_md or "")
        body = body.strip()
        if body.startswith("#"):
            lines = body.splitlines()
            body = "\n".join(lines[1:]).strip()
        if body:
            parts.append(body)
    return "\n\n".join(parts).strip()


async def run_fanqie_short_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_payload: ProjectCreate,
    premise: str,
    requested_by: str = "system",
    export_markdown: bool = True,
    progress: ProgressCallback | None = None,
) -> AutowriteResult:
    if project_payload.project_type != ProjectType.FANQIE_SHORT:
        raise ValueError("run_fanqie_short_pipeline requires project_type=fanqie_short")

    slug = project_payload.slug
    _emit_progress(progress, "fanqie_short_pipeline_started", {"project_slug": slug})

    project = await get_project_by_slug(session, slug)
    if project is None:
        project = await create_project(session, project_payload, settings)
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "project_creation_completed",
            {"project_slug": slug, "project_type": ProjectType.FANQIE_SHORT.value},
        )
    else:
        if not is_fanqie_short_project(project):
            raise ValueError(f"Project {slug} is not a fanqie short story project")

    await _ensure_project_invariants(session, project, settings)

    meta = dict(project.metadata_json or {})
    unlock_ratio = float(meta.get("unlock_line_ratio") or DEFAULT_UNLOCK_LINE_RATIO)
    pov = str(meta.get("pov") or "first_person")
    protagonist_name = "我" if pov == "first_person" else None
    output_dir = (Path(settings.output.base_dir) / slug).resolve()

    _emit_progress(progress, "fanqie_foundation_plan_started", {"project_slug": slug})
    planning_result = await generate_foundation_plan(
        session,
        settings,
        slug,
        premise,
        requested_by=requested_by,
        progress=progress,
    )
    await _checkpoint_commit(session)
    _emit_progress(
        progress,
        "fanqie_foundation_plan_completed",
        {
            "project_slug": slug,
            "workflow_run_id": str(planning_result.workflow_run_id),
        },
    )

    book_artifact = await get_latest_planning_artifact(
        session, project_id=project.id, artifact_type=ArtifactType.BOOK_SPEC
    )
    cast_artifact = await get_latest_planning_artifact(
        session, project_id=project.id, artifact_type=ArtifactType.CAST_SPEC
    )
    book_spec = book_artifact.content if book_artifact else {}
    cast_spec = cast_artifact.content if cast_artifact else {}

    _emit_progress(progress, "fanqie_beat_sheet_started", {"project_slug": slug})
    beat_sheet = await generate_fanqie_beat_sheet(
        session,
        settings,
        slug,
        premise,
        book_spec=book_spec if isinstance(book_spec, dict) else None,
        cast_spec=cast_spec if isinstance(cast_spec, dict) else None,
        requested_by=requested_by,
    )
    outline_payload = build_fanqie_segment_outline_batch(
        project,
        beat_sheet,
        book_spec=book_spec if isinstance(book_spec, dict) else None,
        cast_spec=cast_spec if isinstance(cast_spec, dict) else None,
    )
    source_paths = _write_fanqie_source_artifacts(
        output_dir,
        project=project,
        premise=premise,
        beat_sheet=beat_sheet,
        outline_payload=outline_payload,
        book_spec=book_spec if isinstance(book_spec, dict) else None,
        cast_spec=cast_spec if isinstance(cast_spec, dict) else None,
    )
    await persist_fanqie_chapter_outline(session, slug, outline_payload)
    await _checkpoint_commit(session)
    _emit_progress(
        progress,
        "fanqie_beat_sheet_completed",
        {
            "project_slug": slug,
            "segment_count": len(beat_sheet.beats),
            "unlock_milestone_segment": beat_sheet.unlock_milestone_segment,
            "source_paths": source_paths,
        },
    )

    _emit_progress(progress, "fanqie_materialization_started", {"project_slug": slug})
    bible_result = await materialize_latest_story_bible(
        session, slug, requested_by=requested_by
    )
    outline_result = await materialize_latest_chapter_outline_batch(
        session, slug, requested_by=requested_by
    )
    _emit_progress(progress, "narrative_graph_materialization_started", {"project_slug": slug})
    narrative_graph_result = await materialize_latest_narrative_graph(
        session, slug, requested_by=requested_by
    )
    await _checkpoint_commit(session)
    _emit_progress(
        progress,
        "narrative_graph_materialization_completed",
        {
            "project_slug": slug,
            "workflow_run_id": str(narrative_graph_result.workflow_run_id),
            "plot_arc_count": narrative_graph_result.plot_arc_count,
            "clue_count": narrative_graph_result.clue_count,
        },
    )
    _emit_progress(
        progress,
        "fanqie_materialization_completed",
        {
            "project_slug": slug,
            "segments_created": outline_result.chapters_created,
            "chapters_created": outline_result.chapters_created,
            "scenes_created": outline_result.scenes_created,
        },
    )

    segment_count = project.target_chapters
    for chapter_no in range(1, segment_count + 1):
        _emit_progress(
            progress,
            "fanqie_segment_writing_started",
            {"project_slug": slug, "segment_number": chapter_no, "segment_total": segment_count},
        )
        await run_chapter_pipeline(
            session,
            settings,
            slug,
            chapter_no,
            requested_by=requested_by,
            export_markdown=False,
            progress=progress,
        )
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "fanqie_segment_writing_completed",
            {"project_slug": slug, "segment_number": chapter_no},
        )

    chapter_payloads = await _load_chapter_drafts(session, project.id)
    full_text = assemble_fanqie_whole_story(project, chapter_payloads)
    total_words = count_words(full_text)

    _emit_progress(progress, "fanqie_whole_review_started", {"project_slug": slug})
    whole_review = review_whole_fanqie_short_story(
        full_text,
        title=project.title,
        unlock_line_ratio=unlock_ratio,
        protagonist_name=protagonist_name,
    )
    _emit_progress(
        progress,
        "fanqie_whole_review_completed",
        {"project_slug": slug, "passed": whole_review.passed, **whole_review.to_dict()},
    )
    ranking_rewrite_task_count = 0
    if not whole_review.passed and whole_review.ranking_report is not None:
        try:
            ranking_rewrite_task_count = await _persist_fanqie_ranking_gate_failure(
                session,
                project,
                report=whole_review.ranking_report,
                unlock_segment=beat_sheet.unlock_milestone_segment,
            )
            await _checkpoint_commit(session)
            _emit_progress(
                progress,
                "fanqie_ranking_gate_rewrite_tasks_created",
                {
                    "project_slug": slug,
                    "rewrite_task_count": ranking_rewrite_task_count,
                    "ranking_gate_passed": whole_review.ranking_report.passed,
                    "critical_codes": [
                        finding.code
                        for finding in whole_review.ranking_report.findings
                        if finding.severity == "critical"
                    ],
                },
            )
        except Exception:
            logger.warning(
                "Persisting fanqie ranking gate failure failed for %s",
                slug,
                exc_info=True,
            )

    short_v2_rewrite_task_count = 0
    if not whole_review.short_v2_passed and whole_review.short_v2_report is not None:
        try:
            short_v2_rewrite_task_count = await _persist_fanqie_short_v2_gate_failure(
                session,
                project,
                report=whole_review.short_v2_report,
                unlock_segment=beat_sheet.unlock_milestone_segment,
            )
            await _checkpoint_commit(session)
            routes = build_fanqie_short_v2_rewrite_routes(whole_review.short_v2_report)
            _emit_progress(
                progress,
                "fanqie_short_v2_gate_rewrite_tasks_created",
                {
                    "project_slug": slug,
                    "rewrite_task_count": short_v2_rewrite_task_count,
                    "short_v2_gate_passed": whole_review.short_v2_report.passed,
                    "critical_codes": [
                        finding.code
                        for finding in whole_review.short_v2_report.findings
                        if finding.severity == "critical"
                    ],
                    "worker_routes": [
                        route.model_dump(mode="json") for route in routes
                    ],
                },
            )
        except Exception:
            logger.warning(
                "Persisting fanqie short v2 gate failure failed for %s",
                slug,
                exc_info=True,
            )

    export_paths: dict[str, str] = {}
    rejected_paths: dict[str, str] = {}
    if export_markdown and full_text.strip() and whole_review.passed:
        export_paths = export_fanqie_short_markdown(
            output_dir,
            title=project.title,
            genre=project.genre,
            full_text=full_text,
            unlock_line_ratio=unlock_ratio,
            protagonist_name=protagonist_name,
            target_word_count=project.target_word_count,
        )
        _emit_progress(
            progress,
            "fanqie_export_completed",
            {"project_slug": slug, **export_paths, "total_words": total_words},
        )
    elif export_markdown and full_text.strip():
        finalization = finalize_fanqie_short_for_upload(
            output_dir,
            title=project.title,
            genre=project.genre,
            full_text=full_text,
            unlock_line_ratio=unlock_ratio,
            protagonist_name=protagonist_name,
            target_word_count=project.target_word_count,
        )
        full_text = finalization.full_text
        total_words = count_words(full_text)
        whole_review = finalization.review
        if finalization.ready_for_upload:
            export_paths = finalization.export_paths
            _emit_progress(
                progress,
                "fanqie_finalizer_export_completed",
                {
                    "project_slug": slug,
                    **export_paths,
                    "total_words": total_words,
                    "final_title": finalization.title,
                    "finalization_actions": list(finalization.actions),
                    "finalization_report_path": finalization.report_path,
                },
            )
        else:
            rejected_paths = finalization.export_paths or export_fanqie_short_rejected_draft(
                output_dir,
                title=project.title,
                genre=project.genre,
                full_text=full_text,
                review_report=whole_review.to_dict(),
                unlock_line_ratio=unlock_ratio,
                protagonist_name=protagonist_name,
                target_word_count=project.target_word_count,
            )
            _emit_progress(
                progress,
                "fanqie_export_skipped",
                {
                    "project_slug": slug,
                    "reason": "whole_review_failed_after_finalizer",
                    "total_words": total_words,
                    "review_notes": whole_review.notes,
                    "rejected_paths": rejected_paths,
                    "finalization_actions": list(finalization.actions),
                    "finalization_report_path": finalization.report_path,
                },
            )

    _emit_progress(
        progress,
        "fanqie_short_pipeline_completed",
        {
            "project_slug": slug,
            "total_words": total_words,
            "whole_review_passed": whole_review.passed,
            "ranking_gate_passed": whole_review.ranking_passed,
            "short_v2_gate_passed": whole_review.short_v2_passed,
            "ranking_rewrite_task_count": ranking_rewrite_task_count,
            "short_v2_rewrite_task_count": short_v2_rewrite_task_count,
        },
    )

    return AutowriteResult(
        project_id=project.id,
        project_slug=slug,
        planning_workflow_run_id=planning_result.workflow_run_id,
        story_bible_workflow_run_id=bible_result.workflow_run_id,
        outline_workflow_run_id=outline_result.workflow_run_id,
        narrative_graph_workflow_run_id=narrative_graph_result.workflow_run_id,
        project_workflow_run_id=planning_result.workflow_run_id,
        chapter_count=len(chapter_payloads),
        export_status=(
            "exported"
            if export_paths
            else "rejected_quality_gate"
            if rejected_paths
            else "not_exported"
        ),
        output_path=export_paths.get("markdown_path"),
        output_dir=str(output_dir),
        output_files=(
            list(export_paths.values()) if export_paths else list(rejected_paths.values())
        ),
        final_verdict="pass" if whole_review.passed else "needs_attention",
        requires_human_review=not whole_review.passed,
    )
