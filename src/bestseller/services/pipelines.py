from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.context import SceneWriterContextPacket
from bestseller.domain.enums import ChapterStatus, ArtifactType, ProjectStatus, SceneStatus, WorkflowStatus
from bestseller.domain.pipeline import ProjectPipelineChapterSummary, ProjectPipelineResult
from bestseller.domain.planning import AutowriteResult, PlanningArtifactCreate
from bestseller.domain.pipeline import (
    ChapterPipelineResult,
    ChapterPipelineSceneSummary,
    ScenePipelineResult,
)
from bestseller.domain.project import ProjectCreate
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ChapterStateSnapshotModel, ProjectModel, SceneCardModel, SceneDraftVersionModel
from bestseller.services.context import build_scene_writer_context_from_models
from bestseller.services.continuity import extract_chapter_state_snapshot, validate_fact_monotonicity
from bestseller.services.drafts import assemble_chapter_draft, generate_scene_draft
from bestseller.services.exports import export_chapter_markdown, export_project_markdown
from bestseller.services.consistency import detect_chapter_sequence_gaps, review_project_consistency
from bestseller.services.knowledge import propagate_scene_discoveries, refresh_scene_knowledge
from bestseller.services.planner import generate_foundation_plan, generate_novel_plan, generate_volume_plan
from bestseller.services.projects import create_project, get_project_by_slug, import_planning_artifact, load_json_file
from bestseller.services.reviews import (
    review_chapter_draft,
    review_scene_draft,
    rewrite_chapter_from_task,
    rewrite_scene_from_task,
)
from bestseller.services.workflows import (
    create_workflow_run,
    create_workflow_step_run,
    get_latest_planning_artifact,
    materialize_chapter_outline_batch,
    materialize_latest_chapter_outline_batch,
    materialize_latest_narrative_graph,
    materialize_latest_narrative_tree,
    materialize_latest_story_bible,
)
from bestseller.services.summarization import compress_knowledge_window
from bestseller.services.voice_drift import check_all_pov_voice_drift
from bestseller.services.world_expansion import sync_world_expansion_progress
from bestseller.settings import AppSettings


logger = logging.getLogger(__name__)


WORKFLOW_TYPE_SCENE_PIPELINE = "scene_pipeline"
WORKFLOW_TYPE_CHAPTER_PIPELINE = "chapter_pipeline"
WORKFLOW_TYPE_PROJECT_PIPELINE = "project_pipeline"
ProgressCallback = Callable[[str, dict[str, Any] | None], None]


def _collect_output_files(output_dir: Path) -> list[str]:
    if not output_dir.exists() or not output_dir.is_dir():
        return []
    return [
        str(path.resolve())
        for path in sorted(output_dir.iterdir(), key=lambda item: item.name)
        if path.is_file()
    ]


def _emit_progress(
    progress: ProgressCallback | None,
    stage: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if progress is None:
        return
    progress(stage, payload)


async def _checkpoint_commit(session: AsyncSession) -> None:
    """Commit the current transaction at a pipeline checkpoint.

    Splits the long-running autowrite/project/chapter pipelines into many short
    transactions instead of one mega-transaction. This prevents PostgreSQL
    snapshot bloat (idle-in-transaction blocking VACUUM, MVCC version chains
    growing across hours of work) and gives crash-recovery a meaningful
    granularity.

    Tests use FakeSession objects that may not implement ``commit``. Be tolerant
    of that — the production AsyncSession always implements it.
    """
    commit = getattr(session, "commit", None)
    if commit is None:
        return
    await commit()


async def _load_scene_identifiers(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
) -> tuple[ProjectModel, ChapterModel, SceneCardModel]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")

    scene = await session.scalar(
        select(SceneCardModel).where(
            SceneCardModel.chapter_id == chapter.id,
            SceneCardModel.scene_number == scene_number,
        )
    )
    if scene is None:
        raise ValueError(
            f"Scene {scene_number} was not found in chapter {chapter_number} for '{project_slug}'."
        )

    return project, chapter, scene


async def _load_current_scene_draft(
    session: AsyncSession,
    scene_id: UUID,
) -> SceneDraftVersionModel | None:
    return await session.scalar(
        select(SceneDraftVersionModel).where(
            SceneDraftVersionModel.scene_card_id == scene_id,
            SceneDraftVersionModel.is_current.is_(True),
        )
    )


async def run_scene_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    *,
    requested_by: str = "system",
    parent_workflow_run_id: UUID | None = None,
) -> ScenePipelineResult:
    project, chapter, scene = await _load_scene_identifiers(
        session,
        project_slug,
        chapter_number,
        scene_number,
    )

    # Resume: skip already-complete scenes to avoid re-drafting
    if settings.pipeline.resume_enabled and scene.status == SceneStatus.APPROVED.value:
        logger.info(
            "Scene %d.%d already complete — skipping (resume)",
            chapter_number, scene_number,
        )
        draft = await _load_current_scene_draft(session, scene.id)
        if draft is None:
            raise ValueError(
                f"Scene {chapter_number}.{scene_number} is marked COMPLETE but has no current draft."
            )
        return ScenePipelineResult(
            workflow_run_id=UUID(int=0),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter_number,
            scene_number=scene_number,
            current_draft_id=draft.id,
            current_draft_version_no=draft.version_no,
            final_verdict="pass",
            review_iterations=0,
            rewrite_iterations=0,
            canon_fact_count=0,
            timeline_event_count=0,
            requires_human_review=False,
        )

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_SCENE_PIPELINE,
        status=WorkflowStatus.RUNNING,
        scope_type="scene_card",
        scope_id=scene.id,
        requested_by=requested_by,
        current_step="load_context",
        metadata={
            "project_slug": project_slug,
            "chapter_number": chapter_number,
            "scene_number": scene_number,
            "parent_workflow_run_id": str(parent_workflow_run_id)
            if parent_workflow_run_id is not None
            else None,
        },
    )

    step_order = 1
    llm_run_ids: list[UUID] = []
    review_iterations = 0
    rewrite_iterations = 0
    canon_fact_count = 0
    timeline_event_count = 0
    current_step_name = "load_context"
    draft = await _load_current_scene_draft(session, scene.id)

    try:
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "project_id": str(project.id),
                "chapter_id": str(chapter.id),
                "scene_id": str(scene.id),
                "has_current_draft": draft is not None,
            },
        )
        step_order += 1

        # Opt-B: build the scene writer context exactly once per pipeline run and
        # share it between draft + review (and any rewrite re-review). The context
        # contains 10+ DB / retrieval queries; without sharing, each call rebuilds
        # the same packet. rewrite_scene_from_task does NOT consume context, so we
        # don't need to invalidate after rewrite. refresh_scene_knowledge runs last
        # and is allowed to invalidate the world — we never reuse shared_context
        # past it. Use the *_from_models variant since we already loaded
        # project/chapter/scene above.
        shared_context: SceneWriterContextPacket | None = None
        try:
            async with session.begin_nested():
                shared_context = await build_scene_writer_context_from_models(
                    session,
                    settings,
                    project,
                    chapter,
                    scene,
                    draft_mode=settings.quality.draft_mode,
                )
        except Exception:
            # Match the pre-Opt-B behavior in review_scene_draft: tolerate context
            # build failures (tests / mocks may not provide everything). Downstream
            # functions handle context_packet=None correctly. The SAVEPOINT above
            # ensures any failed query inside the context build does not poison the
            # outer transaction (asyncpg PendingRollbackError).
            logger.warning(
                "Context build failed for ch%d sc%d, proceeding without context",
                chapter.chapter_number,
                scene.scene_number,
                exc_info=True,
            )
            shared_context = None

        # ── Inject character identity constraints (Tier 0 — never dropped) ──
        if shared_context is not None:
            try:
                from bestseller.services.identity_guard import (
                    build_identity_constraint_block,
                    load_identity_registry,
                )
                _identity_registry = await load_identity_registry(session, project.id)
                if _identity_registry:
                    shared_context.identity_registry = _identity_registry
                    shared_context.identity_constraint_block = build_identity_constraint_block(
                        _identity_registry,
                        language=getattr(project, "language", None) or "zh-CN",
                        participant_names=list(scene.participants or []),
                    )
            except Exception:
                logger.warning(
                    "Identity guard load failed for ch%d sc%d (non-fatal)",
                    chapter_number, scene_number,
                    exc_info=True,
                )

        # ── Inject overused phrase avoidance + genre constraints ──
        if shared_context is not None:
            try:
                _phrase_block = (project.metadata_json or {}).get("_overused_phrase_block")
                if _phrase_block:
                    shared_context.overused_phrase_block = _phrase_block
            except Exception:
                logger.debug("Overused phrase injection failed (non-fatal)", exc_info=True)
            try:
                from bestseller.services.genre_consistency import (
                    get_genre_profile,
                    build_genre_constraint_block,
                )
                _genre = getattr(project, "genre", None) or settings.generation.genre
                _sub_genre = (project.metadata_json or {}).get("sub_genre")
                _gprofile = get_genre_profile(_genre, _sub_genre)
                if _gprofile:
                    # Build character states from latest snapshot
                    _latest_snap = await session.scalar(
                        select(ChapterStateSnapshotModel).where(
                            ChapterStateSnapshotModel.project_id == project.id,
                        ).order_by(ChapterStateSnapshotModel.chapter_number.desc())
                    )
                    _char_states: dict[str, dict] = {}
                    if _latest_snap and _latest_snap.facts:
                        for _f in _latest_snap.facts:
                            _fd = _f if isinstance(_f, dict) else _f.__dict__
                            _char = _fd.get("character", "")
                            if _char:
                                _char_states.setdefault(_char, {})
                                if _fd.get("kind") == "level":
                                    _char_states[_char]["cultivation_level"] = _fd.get("value", "")
                    if _char_states:
                        _lang = getattr(project, "language", None) or settings.generation.language
                        shared_context.genre_constraint_block = build_genre_constraint_block(
                            _gprofile, _char_states, language=_lang,
                        )
            except Exception:
                logger.debug("Genre constraint injection failed (non-fatal)", exc_info=True)

        # ── Pre-scene contradiction check (zero LLM cost) ──
        if settings.pipeline.enable_contradiction_checks and shared_context is not None:
            try:
                from bestseller.services.contradiction import run_pre_scene_contradiction_checks

                _contradiction_result = await run_pre_scene_contradiction_checks(
                    session,
                    project.id,
                    chapter_number,
                    scene_number,
                    scene_participants=list(scene.participants or []),
                    scene_information_release=getattr(
                        shared_context.scene_contract, "information_release", None
                    ) if shared_context.scene_contract else None,
                    settings=settings,
                    language=getattr(project, "language", None),
                )
                if _contradiction_result.violations or _contradiction_result.warnings:
                    shared_context.contradiction_warnings = [
                        v.message for v in _contradiction_result.violations
                    ] + [w.message for w in _contradiction_result.warnings]
            except Exception:
                logger.warning(
                    "Pre-scene contradiction check failed for ch%d sc%d (non-fatal)",
                    chapter_number,
                    scene_number,
                    exc_info=True,
                )
                workflow_run.metadata_json = {
                    **workflow_run.metadata_json,
                    "contradiction_check_failed": True,
                }

        # ── Inject pending consistency warnings from last rolling check ──
        _pending_cw: list[str] = []
        try:
            _pending_cw = (project.metadata_json or {}).get("_pending_consistency_warnings", [])
            if _pending_cw and shared_context is not None:
                shared_context.contradiction_warnings.extend(_pending_cw[:5])
            # Clear after first scene of a new chapter consumes them
            if scene_number == 1 and _pending_cw:
                project.metadata_json = {
                    **(project.metadata_json or {}),
                    "_pending_consistency_warnings": [],
                }
        except Exception:
            logger.debug("Failed to inject pending consistency warnings (non-fatal)", exc_info=True)

        if draft is None:
            current_step_name = "generate_scene_draft"
            workflow_run.current_step = current_step_name
            draft = await generate_scene_draft(
                session,
                project_slug,
                chapter_number,
                scene_number,
                settings=settings,
                workflow_run_id=workflow_run.id,
                context_packet=shared_context,
            )
            if draft.llm_run_id is not None:
                llm_run_ids.append(draft.llm_run_id)
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "draft_id": str(draft.id),
                    "draft_version_no": draft.version_no,
                    "llm_run_id": str(draft.llm_run_id) if draft.llm_run_id else None,
                },
            )
            step_order += 1

        # ── Post-draft identity validation (zero LLM cost) ──
        if draft is not None and draft.content_md:
            try:
                from bestseller.services.identity_guard import (
                    load_identity_registry,
                    validate_scene_text_identity,
                )
                _id_registry = await load_identity_registry(session, project.id)
                _id_violations = validate_scene_text_identity(
                    draft.content_md,
                    _id_registry,
                    language=getattr(project, "language", None) or "zh-CN",
                    participant_names=list(scene.participants or []),
                )
                if _id_violations:
                    logger.warning(
                        "Identity violations in ch%d sc%d: %s",
                        chapter_number, scene_number,
                        [(v.character_name, v.violation_type, v.expected, v.found) for v in _id_violations],
                    )
                    # Inject as contradiction warnings so the reviewer sees them
                    if shared_context is not None:
                        for v in _id_violations[:5]:
                            shared_context.contradiction_warnings.append(
                                f"[身份违规] {v.character_name}: {v.violation_type} "
                                f"(expected={v.expected}, found={v.found})"
                            )
            except Exception:
                logger.debug("Post-draft identity check failed (non-fatal)", exc_info=True)

        # ── Post-draft deduplication check (zero LLM cost) ──
        if draft is not None and draft.content_md:
            try:
                from bestseller.services.deduplication import check_scene_duplication

                _existing_drafts_q = await session.scalars(
                    select(SceneDraftVersionModel).join(
                        SceneCardModel,
                        SceneDraftVersionModel.scene_card_id == SceneCardModel.id,
                    ).join(
                        ChapterModel,
                        SceneCardModel.chapter_id == ChapterModel.id,
                    ).where(
                        ChapterModel.project_id == project.id,
                        SceneDraftVersionModel.is_current.is_(True),
                        SceneDraftVersionModel.id != draft.id,
                    )
                )
                _existing_texts: list[tuple[int, int, str]] = []
                for ed in _existing_drafts_q:
                    _sc = await session.get(SceneCardModel, ed.scene_card_id)
                    _ch = await session.get(ChapterModel, _sc.chapter_id) if _sc else None
                    if _ch and _sc and ed.content:
                        _existing_texts.append((_ch.chapter_number, _sc.scene_number, ed.content))

                _dedup_findings = check_scene_duplication(draft.content_md, _existing_texts)
                if _dedup_findings:
                    logger.warning(
                        "Deduplication findings in ch%d sc%d: %s",
                        chapter_number, scene_number,
                        [(f["chapter"], f["scene"], f["similarity"], f["severity"]) for f in _dedup_findings],
                    )
                    if shared_context is not None:
                        for f in _dedup_findings[:3]:
                            shared_context.contradiction_warnings.append(f["message"])
            except Exception:
                logger.debug("Post-draft deduplication check failed (non-fatal)", exc_info=True)

        # Draft mode: skip review/rewrite/knowledge refresh — rely on prompt
        # quality + mechanical sanitization (regex) for quality assurance.
        if settings.quality.draft_mode:
            scene.status = SceneStatus.APPROVED.value
            workflow_run.status = WorkflowStatus.COMPLETED.value
            workflow_run.current_step = "completed"
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "draft_mode": True,
                "final_verdict": "draft",
                "llm_run_ids": [str(rid) for rid in llm_run_ids],
            }
            await session.flush()
            return ScenePipelineResult(
                workflow_run_id=workflow_run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                scene_id=scene.id,
                chapter_number=chapter.chapter_number,
                scene_number=scene.scene_number,
                current_draft_id=draft.id,
                current_draft_version_no=draft.version_no,
                final_verdict="draft",
                review_report_id=None,
                quality_score_id=None,
                review_iterations=0,
                rewrite_iterations=0,
                llm_run_ids=llm_run_ids,
            )

        reached_revision_limit = False
        requires_human_review = False
        review_result = None
        report = None
        quality = None
        rewrite_task = None
        previous_scene_score: float | None = None
        previous_rewrite_instructions: str | None = None

        while True:
            review_iterations += 1
            current_step_name = f"review_scene_v{review_iterations}"
            workflow_run.current_step = current_step_name
            review_result, report, quality, rewrite_task = await review_scene_draft(
                session,
                settings,
                project_slug,
                chapter_number,
                scene_number,
                workflow_run_id=workflow_run.id,
                context_packet=shared_context,
            )
            if report.llm_run_id is not None:
                llm_run_ids.append(report.llm_run_id)
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "report_id": str(report.id),
                    "quality_score_id": str(quality.id),
                    "verdict": review_result.verdict,
                    "rewrite_task_id": str(rewrite_task.id) if rewrite_task is not None else None,
                    "llm_run_id": str(report.llm_run_id) if report.llm_run_id else None,
                },
            )
            step_order += 1
            current_scene_score = getattr(getattr(review_result, "scores", None), "overall", None)

            if review_result.verdict == "pass" or rewrite_task is None:
                break

            if (
                rewrite_iterations > 0
                and previous_scene_score is not None
                and current_scene_score is not None
            ):
                score_delta = current_scene_score - previous_scene_score
                same_rewrite_plan = (
                    getattr(review_result, "rewrite_instructions", None) or ""
                ) == (previous_rewrite_instructions or "")
                if (
                    same_rewrite_plan
                    and score_delta < settings.quality.min_scene_rewrite_improvement
                ):
                    reached_revision_limit = True
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "stalled_rewrite": True,
                        "stalled_rewrite_score_delta": round(score_delta, 4),
                        "stalled_rewrite_threshold": settings.quality.min_scene_rewrite_improvement,
                    }
                    if settings.pipeline.accept_on_stall:
                        logger.info(
                            "Scene %d.%d rewrite stalled (delta=%.4f) — accepting best draft",
                            chapter_number, scene_number, score_delta,
                        )
                    else:
                        requires_human_review = True
                        workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
                        workflow_run.current_step = "waiting_human_review"
                    break

            if rewrite_iterations >= settings.quality.max_scene_revisions:
                reached_revision_limit = True
                if settings.pipeline.accept_on_stall:
                    logger.info(
                        "Scene %d.%d reached max revisions (%d) — accepting best draft",
                        chapter_number, scene_number, rewrite_iterations,
                    )
                else:
                    requires_human_review = True
                    workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
                    workflow_run.current_step = "waiting_human_review"
                break

            previous_scene_score = current_scene_score
            previous_rewrite_instructions = getattr(review_result, "rewrite_instructions", None)

            rewrite_iterations += 1
            current_step_name = f"rewrite_scene_v{rewrite_iterations}"
            workflow_run.current_step = current_step_name
            draft, rewrite_task = await rewrite_scene_from_task(
                session,
                project_slug,
                chapter_number,
                scene_number,
                rewrite_task_id=rewrite_task.id,
                settings=settings,
                workflow_run_id=workflow_run.id,
            )
            if draft.llm_run_id is not None:
                llm_run_ids.append(draft.llm_run_id)
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "draft_id": str(draft.id),
                    "draft_version_no": draft.version_no,
                    "rewrite_task_id": str(rewrite_task.id),
                    "llm_run_id": str(draft.llm_run_id) if draft.llm_run_id else None,
                },
            )
            step_order += 1

        if draft is None or review_result is None or report is None or quality is None:
            raise RuntimeError("Scene pipeline did not produce a current draft and review result.")

        # When stall was accepted, promote scene/chapter status so downstream
        # logic (chapter assembly, resume) treats the scene as done.
        if reached_revision_limit and not requires_human_review:
            scene.status = SceneStatus.APPROVED.value

        if not requires_human_review:
            current_step_name = "refresh_scene_knowledge"
            workflow_run.current_step = current_step_name
            knowledge_result = await refresh_scene_knowledge(
                session,
                settings,
                project_slug,
                chapter_number,
                scene_number,
                workflow_run_id=workflow_run.id,
            )
            canon_fact_count = knowledge_result.canon_facts_created + knowledge_result.canon_facts_reused
            timeline_event_count = (
                knowledge_result.timeline_events_created + knowledge_result.timeline_events_reused
            )
            if knowledge_result.llm_run_id is not None:
                llm_run_ids.append(knowledge_result.llm_run_id)
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "canon_fact_ids": [str(fact_id) for fact_id in knowledge_result.canon_fact_ids],
                    "timeline_event_ids": [
                        str(event_id) for event_id in knowledge_result.timeline_event_ids
                    ],
                    "summary_text": knowledge_result.summary_text,
                    "llm_run_id": str(knowledge_result.llm_run_id)
                    if knowledge_result.llm_run_id
                    else None,
                },
            )
            step_order += 1

            # Bidirectional propagation: merge discoveries back into
            # CharacterModel/RelationshipModel (zero LLM cost).
            try:
                await propagate_scene_discoveries(
                    session,
                    project.id,
                    chapter.chapter_number,
                    scene.scene_number,
                    knowledge_result,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Scene %d:%d discovery propagation failed (non-fatal)",
                    chapter.chapter_number,
                    scene.scene_number,
                    exc_info=True,
                )

        if not requires_human_review:
            workflow_run.status = WorkflowStatus.COMPLETED.value
            workflow_run.current_step = "completed"
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "review_iterations": review_iterations,
            "rewrite_iterations": rewrite_iterations,
            "reached_revision_limit": reached_revision_limit,
            "requires_human_review": requires_human_review,
            "final_verdict": review_result.verdict,
            "canon_fact_count": canon_fact_count,
            "timeline_event_count": timeline_event_count,
            "llm_run_ids": [str(llm_run_id) for llm_run_id in llm_run_ids],
        }
        await session.flush()

        return ScenePipelineResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=draft.id,
            current_draft_version_no=draft.version_no,
            final_verdict=review_result.verdict,
            review_report_id=report.id,
            quality_score_id=quality.id,
            rewrite_task_id=rewrite_task.id if rewrite_task is not None else None,
            review_iterations=review_iterations,
            rewrite_iterations=rewrite_iterations,
            canon_fact_count=canon_fact_count,
            timeline_event_count=timeline_event_count,
            reached_revision_limit=reached_revision_limit,
            requires_human_review=requires_human_review,
            llm_run_ids=llm_run_ids,
        )
    except Exception as exc:
        # If the session is in PendingRollback state (e.g. from a lock-timeout
        # autoflush), skip the DB cleanup — any attempt to flush/query would
        # raise another PendingRollbackError, swallowing the original cause.
        if isinstance(exc, PendingRollbackError) or not session.is_active:
            await session.rollback()
            raise
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        try:
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.FAILED,
                error_message=str(exc),
            )
            await session.flush()
        except PendingRollbackError:
            await session.rollback()
        raise


async def run_chapter_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    requested_by: str = "system",
    export_markdown: bool = False,
) -> ChapterPipelineResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")

    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )
    if not scenes:
        raise ValueError(f"Chapter {chapter_number} does not have any scene cards to process.")

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_CHAPTER_PIPELINE,
        status=WorkflowStatus.RUNNING,
        scope_type="chapter",
        scope_id=chapter.id,
        requested_by=requested_by,
        current_step="load_chapter_context",
        metadata={
            "project_slug": project_slug,
            "chapter_number": chapter_number,
            "scene_count": len(scenes),
            "export_markdown": export_markdown,
        },
    )

    step_order = 1
    current_step_name = "load_chapter_context"
    scene_results: list[ChapterPipelineSceneSummary] = []

    try:
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "chapter_id": str(chapter.id),
                "scene_numbers": [scene.scene_number for scene in scenes],
            },
        )
        step_order += 1

        scene_requires_human_review = False
        # Resume support: filter out already-completed scenes
        pending_scenes = [
            s for s in scenes
            if s.status != SceneStatus.APPROVED.value
        ] if settings.pipeline.resume_enabled else scenes
        skipped_scene_count = len(scenes) - len(pending_scenes)
        if skipped_scene_count > 0:
            logger.info(
                "Chapter %d resume: skipping %d completed scenes, %d pending",
                chapter_number, skipped_scene_count, len(pending_scenes),
            )
        for scene in pending_scenes:
            current_step_name = f"scene_pipeline_{scene.scene_number}"
            workflow_run.current_step = current_step_name
            scene_result = await run_scene_pipeline(
                session,
                settings,
                project_slug,
                chapter_number,
                scene.scene_number,
                requested_by=requested_by,
                parent_workflow_run_id=workflow_run.id,
            )
            scene_results.append(
                ChapterPipelineSceneSummary(
                    scene_number=scene.scene_number,
                    workflow_run_id=scene_result.workflow_run_id,
                    final_verdict=scene_result.final_verdict,
                    rewrite_iterations=scene_result.rewrite_iterations,
                    canon_fact_count=scene_result.canon_fact_count,
                    timeline_event_count=scene_result.timeline_event_count,
                    requires_human_review=scene_result.requires_human_review,
                    current_draft_version_no=scene_result.current_draft_version_no,
                )
            )
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "scene_number": scene.scene_number,
                    "scene_workflow_run_id": str(scene_result.workflow_run_id),
                    "final_verdict": scene_result.final_verdict,
                    "requires_human_review": scene_result.requires_human_review,
                },
            )
            step_order += 1

            if scene_result.requires_human_review:
                scene_requires_human_review = True

        # Resume optimisation: if every scene was already APPROVED (nothing
        # to process) and a chapter draft already exists, reuse it rather
        # than creating a redundant new version with identical content.
        current_step_name = "assemble_chapter_draft"
        workflow_run.current_step = current_step_name
        chapter_draft = None
        if settings.pipeline.resume_enabled and not pending_scenes:
            chapter_draft = await session.scalar(
                select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.chapter_id == chapter.id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
            )
            if chapter_draft is not None:
                logger.info(
                    "Chapter %d resume: reusing existing draft v%d",
                    chapter_number, chapter_draft.version_no,
                )
        if chapter_draft is None:
            chapter_draft = await assemble_chapter_draft(session, project_slug, chapter_number)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "chapter_draft_id": str(chapter_draft.id),
                "chapter_draft_version_no": chapter_draft.version_no,
            },
        )
        step_order += 1

        async def _export_current_chapter_markdown() -> tuple[UUID | None, str | None]:
            nonlocal current_step_name
            nonlocal step_order
            if not export_markdown:
                return None, None
            current_step_name = "export_chapter_markdown"
            workflow_run.current_step = current_step_name
            try:
                artifact, artifact_path = await export_chapter_markdown(
                    session,
                    settings,
                    project_slug,
                    chapter_number,
                    created_by_run_id=workflow_run.id,
                )
            except (ValueError, OSError) as exc:
                # Export blockers (hygiene checks, I/O errors) must not crash the
                # entire chapter pipeline — the draft is already persisted and can
                # be re-exported later once the issue is resolved.
                logger.warning(
                    "Chapter %d export blocked for %s, continuing pipeline: %s",
                    chapter_number,
                    project_slug,
                    exc,
                )
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name=current_step_name,
                    step_order=step_order,
                    status=WorkflowStatus.COMPLETED,
                    output_ref={"export_blocked": str(exc)},
                )
                step_order += 1
                return None, None
            artifact_id = artifact.id
            artifact_output_path = str(artifact_path.resolve())
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "export_artifact_id": str(artifact_id),
                    "output_path": artifact_output_path,
                },
            )
            step_order += 1
            return artifact_id, artifact_output_path

        if scene_requires_human_review:
            chapter.status = ChapterStatus.REVISION.value
            export_artifact_id, output_path = await _export_current_chapter_markdown()
            workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
            workflow_run.current_step = "waiting_human_review"
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "requires_human_review": True,
                "chapter_draft_id": str(chapter_draft.id),
                "chapter_draft_version_no": chapter_draft.version_no,
                "scene_requires_human_review": True,
                "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
            }
            await session.flush()
            return ChapterPipelineResult(
                workflow_run_id=workflow_run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                chapter_number=chapter.chapter_number,
                scene_results=scene_results,
                chapter_draft_id=chapter_draft.id,
                chapter_draft_version_no=chapter_draft.version_no,
                export_artifact_id=export_artifact_id,
                output_path=output_path,
                requires_human_review=True,
            )

        # Draft mode: skip chapter review/rewrite but keep state snapshot
        # for cross-chapter continuity, then export and return.
        if settings.quality.draft_mode:
            chapter.status = ChapterStatus.COMPLETE.value
            try:
                async with session.begin_nested():
                    snapshot = await extract_chapter_state_snapshot(
                        session,
                        settings,
                        project_id=project.id,
                        chapter=chapter,
                        chapter_md=chapter_draft.content_md,
                        workflow_run_id=workflow_run.id,
                    )
                    # Validate monotonic facts against previous chapter
                    if snapshot is not None and snapshot.facts:
                        from bestseller.domain.context import HardFactContext as _HFC
                        from bestseller.services.continuity import _extract_numeric  # noqa: F811

                        _prev_snapshot = None
                        if chapter.chapter_number > 1:
                            _prev_snap_model = await session.scalar(
                                select(ChapterStateSnapshotModel).where(
                                    ChapterStateSnapshotModel.project_id == project.id,
                                    ChapterStateSnapshotModel.chapter_number == chapter.chapter_number - 1,
                                ).order_by(ChapterStateSnapshotModel.created_at.desc())
                            )
                            if _prev_snap_model is not None and _prev_snap_model.facts:
                                _prev_facts = [
                                    _HFC(**f) if isinstance(f, dict) else f
                                    for f in (_prev_snap_model.facts or [])
                                ]
                                _cur_facts = [
                                    _HFC(**f) if isinstance(f, dict) else f
                                    for f in (snapshot.facts or [])
                                ]
                                _mono_warnings = validate_fact_monotonicity(_cur_facts, _prev_facts)
                                if _mono_warnings:
                                    logger.warning(
                                        "Chapter %d monotonicity violations: %s",
                                        chapter.chapter_number,
                                        _mono_warnings,
                                    )
                                    # Store warnings for next chapter's context
                                    project.metadata_json = {
                                        **(project.metadata_json or {}),
                                        "_pending_consistency_warnings": (
                                            (project.metadata_json or {}).get("_pending_consistency_warnings", [])
                                            + _mono_warnings[:5]
                                        ),
                                    }

                    # ── Genre-specific progression validation ──
                    try:
                        from bestseller.services.genre_consistency import (
                            get_genre_profile,
                            validate_xianxia_progression,
                            validate_litrpg_stats,
                        )
                        _genre = getattr(project, "genre", None) or settings.generation.genre
                        _sub_genre = (project.metadata_json or {}).get("sub_genre")
                        _gprofile = get_genre_profile(_genre, _sub_genre)
                        if _gprofile and snapshot.facts:
                            _genre_warnings: list[str] = []
                            if _gprofile.progression_system == "cultivation_tiers" and _prev_snap_model:
                                for f in (snapshot.facts or []):
                                    _fd = f if isinstance(f, dict) else f.__dict__
                                    if _fd.get("kind") == "level":
                                        _char = _fd.get("character", "")
                                        _cur_val = _fd.get("value", "")
                                        # Find matching previous fact
                                        for pf in (_prev_snap_model.facts or []):
                                            _pfd = pf if isinstance(pf, dict) else pf.__dict__
                                            if _pfd.get("kind") == "level" and _pfd.get("character") == _char:
                                                _genre_warnings.extend(
                                                    validate_xianxia_progression(
                                                        _char, _cur_val, _pfd.get("value", ""),
                                                        _gprofile.tier_names,
                                                    )
                                                )
                            if _genre_warnings:
                                logger.warning("Genre violations ch%d: %s", chapter.chapter_number, _genre_warnings)
                                project.metadata_json = {
                                    **(project.metadata_json or {}),
                                    "_pending_consistency_warnings": (
                                        (project.metadata_json or {}).get("_pending_consistency_warnings", [])
                                        + _genre_warnings[:3]
                                    ),
                                }
                    except Exception:
                        logger.debug("Genre consistency check failed (non-fatal)", exc_info=True)

                    # ── Book-level overused phrase tracking ──
                    try:
                        from bestseller.services.deduplication import (
                            extract_frequent_phrases,
                            build_overused_phrase_avoidance_block,
                        )
                        _all_scene_texts_q = await session.scalars(
                            select(SceneDraftVersionModel.content).join(
                                SceneCardModel,
                                SceneDraftVersionModel.scene_card_id == SceneCardModel.id,
                            ).join(
                                ChapterModel,
                                SceneCardModel.chapter_id == ChapterModel.id,
                            ).where(
                                ChapterModel.project_id == project.id,
                                SceneDraftVersionModel.is_current.is_(True),
                                SceneDraftVersionModel.content.isnot(None),
                            )
                        )
                        _all_scene_texts = [t for t in _all_scene_texts_q if t]
                        if len(_all_scene_texts) >= 3:
                            _lang = getattr(project, "language", None) or settings.generation.language
                            _phrases = extract_frequent_phrases(_all_scene_texts, language=_lang)
                            if _phrases:
                                _phrase_block = build_overused_phrase_avoidance_block(_phrases, language=_lang)
                                project.metadata_json = {
                                    **(project.metadata_json or {}),
                                    "_overused_phrase_block": _phrase_block,
                                }
                    except Exception:
                        logger.debug("Overused phrase tracking failed (non-fatal)", exc_info=True)

                    # ── Living Story Bible update ──
                    try:
                        from bestseller.services.story_bible import update_story_bible_from_chapter
                        _bible_counts = await update_story_bible_from_chapter(
                            session,
                            settings,
                            project=project,
                            chapter=chapter,
                            chapter_text=chapter_draft.content_md or "",
                            workflow_run_id=workflow_run.id,
                        )
                        logger.info("Bible update ch%d: %s", chapter.chapter_number, _bible_counts)
                    except Exception:
                        logger.debug("Living bible update failed (non-fatal)", exc_info=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Chapter %d hard-fact extraction failed (non-fatal): %s",
                    chapter.chapter_number,
                    exc,
                )
            export_artifact_id: UUID | None = None
            output_path: str | None = None
            if export_markdown:
                export_artifact_id, output_path = await _export_current_chapter_markdown()
            workflow_run.status = WorkflowStatus.COMPLETED.value
            workflow_run.current_step = "completed"
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "draft_mode": True,
                "chapter_draft_id": str(chapter_draft.id),
                "chapter_draft_version_no": chapter_draft.version_no,
                "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
            }
            await session.flush()
            return ChapterPipelineResult(
                workflow_run_id=workflow_run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                chapter_number=chapter.chapter_number,
                scene_results=scene_results,
                chapter_draft_id=chapter_draft.id,
                chapter_draft_version_no=chapter_draft.version_no,
                export_artifact_id=export_artifact_id,
                output_path=str(output_path) if output_path else None,
            )

        chapter_review_iterations = 0
        chapter_rewrite_iterations = 0
        chapter_review_result = None
        chapter_report = None
        chapter_quality = None
        chapter_rewrite_task = None
        reached_chapter_revision_limit = False
        requires_human_review = False

        while True:
            chapter_review_iterations += 1
            current_step_name = f"review_chapter_v{chapter_review_iterations}"
            workflow_run.current_step = current_step_name
            (
                chapter_review_result,
                chapter_report,
                chapter_quality,
                chapter_rewrite_task,
            ) = await review_chapter_draft(
                session,
                settings,
                project_slug,
                chapter_number,
                workflow_run_id=workflow_run.id,
            )
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "report_id": str(chapter_report.id),
                    "quality_score_id": str(chapter_quality.id),
                    "verdict": chapter_review_result.verdict,
                    "rewrite_task_id": (
                        str(chapter_rewrite_task.id) if chapter_rewrite_task is not None else None
                    ),
                },
            )
            step_order += 1

            if chapter_review_result.verdict == "pass" or chapter_rewrite_task is None:
                chapter.status = ChapterStatus.COMPLETE.value
                # Extract hard-fact snapshot for cross-chapter continuity.
                # Failures are logged and swallowed — continuity is a quality
                # enhancement, not a hard dependency for chapter completion.
                # Wrap in a SAVEPOINT so an internal DB error (e.g. missing
                # table, constraint violation) does not poison the outer
                # transaction shared across the rest of the chapter loop.
                try:
                    async with session.begin_nested():
                        await extract_chapter_state_snapshot(
                            session,
                            settings,
                            project_id=project.id,
                            chapter=chapter,
                            chapter_md=chapter_draft.content_md,
                            workflow_run_id=workflow_run.id,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Chapter %d hard-fact extraction failed (non-fatal): %s",
                        chapter.chapter_number,
                        exc,
                    )

                # ── Post-chapter feedback extraction (1 LLM call) ──
                if settings.pipeline.enable_chapter_feedback:
                    try:
                        from bestseller.services.feedback import extract_chapter_feedback

                        async with session.begin_nested():
                            await extract_chapter_feedback(
                                session,
                                settings,
                                project_id=project.id,
                                chapter=chapter,
                                chapter_md=chapter_draft.content_md,
                                workflow_run_id=workflow_run.id,
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Chapter %d feedback extraction failed (non-fatal): %s",
                            chapter.chapter_number,
                            exc,
                        )

                # ── Living Story Bible update (non-draft path) ──
                try:
                    from bestseller.services.story_bible import update_story_bible_from_chapter
                    async with session.begin_nested():
                        await update_story_bible_from_chapter(
                            session,
                            settings,
                            project=project,
                            chapter=chapter,
                            chapter_text=chapter_draft.content_md or "",
                            workflow_run_id=workflow_run.id,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Chapter %d bible update failed (non-fatal): %s",
                        chapter.chapter_number,
                        exc,
                    )
                break

            if chapter_rewrite_iterations >= settings.quality.max_chapter_revisions:
                reached_chapter_revision_limit = True
                requires_human_review = True
                workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
                workflow_run.current_step = "waiting_human_review"
                break

            chapter_rewrite_iterations += 1
            current_step_name = f"rewrite_chapter_v{chapter_rewrite_iterations}"
            workflow_run.current_step = current_step_name
            chapter_draft, chapter_rewrite_task = await rewrite_chapter_from_task(
                session,
                project_slug,
                chapter_number,
                rewrite_task_id=chapter_rewrite_task.id,
                settings=settings,
                workflow_run_id=workflow_run.id,
            )
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                    "rewrite_task_id": str(chapter_rewrite_task.id),
                },
            )
            step_order += 1

        if requires_human_review:
            export_artifact_id, output_path = await _export_current_chapter_markdown()
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "requires_human_review": True,
                "chapter_review_iterations": chapter_review_iterations,
                "chapter_rewrite_iterations": chapter_rewrite_iterations,
                "reached_chapter_revision_limit": reached_chapter_revision_limit,
                "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
            }
            await session.flush()
            return ChapterPipelineResult(
                workflow_run_id=workflow_run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                chapter_number=chapter.chapter_number,
                scene_results=scene_results,
                chapter_draft_id=chapter_draft.id,
                chapter_draft_version_no=chapter_draft.version_no,
                final_verdict=(
                    chapter_review_result.verdict if chapter_review_result is not None else None
                ),
                review_report_id=chapter_report.id if chapter_report is not None else None,
                quality_score_id=chapter_quality.id if chapter_quality is not None else None,
                rewrite_task_id=(
                    chapter_rewrite_task.id if chapter_rewrite_task is not None else None
                ),
                chapter_review_iterations=chapter_review_iterations,
                chapter_rewrite_iterations=chapter_rewrite_iterations,
                export_artifact_id=export_artifact_id,
                output_path=output_path,
                requires_human_review=True,
            )

        export_artifact_id: UUID | None = None
        output_path: str | None = None
        if export_markdown:
            export_artifact_id, output_path = await _export_current_chapter_markdown()

        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.current_step = "completed"
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "requires_human_review": False,
            "chapter_draft_id": str(chapter_draft.id),
            "chapter_draft_version_no": chapter_draft.version_no,
            "chapter_review_iterations": chapter_review_iterations,
            "chapter_rewrite_iterations": chapter_rewrite_iterations,
            "final_verdict": chapter_review_result.verdict if chapter_review_result is not None else None,
            "review_report_id": str(chapter_report.id) if chapter_report is not None else None,
            "quality_score_id": str(chapter_quality.id) if chapter_quality is not None else None,
            "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
        }
        await session.flush()

        return ChapterPipelineResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            scene_results=scene_results,
            chapter_draft_id=chapter_draft.id,
            chapter_draft_version_no=chapter_draft.version_no,
            final_verdict=chapter_review_result.verdict if chapter_review_result is not None else None,
            review_report_id=chapter_report.id if chapter_report is not None else None,
            quality_score_id=chapter_quality.id if chapter_quality is not None else None,
            rewrite_task_id=chapter_rewrite_task.id if chapter_rewrite_task is not None else None,
            chapter_review_iterations=chapter_review_iterations,
            chapter_rewrite_iterations=chapter_rewrite_iterations,
            export_artifact_id=export_artifact_id,
            output_path=output_path,
            requires_human_review=False,
        )
    except Exception as exc:
        if isinstance(exc, PendingRollbackError) or not session.is_active:
            await session.rollback()
            raise
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        try:
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.FAILED,
                error_message=str(exc),
            )
            await session.flush()
        except PendingRollbackError:
            await session.rollback()
        raise


async def _load_project_chapters(
    session: AsyncSession,
    project_id: UUID,
) -> list[ChapterModel]:
    return list(
        await session.scalars(
            select(ChapterModel)
            .where(ChapterModel.project_id == project_id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )


async def run_project_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    *,
    requested_by: str = "system",
    materialize_story_bible: bool = False,
    materialize_outline: bool = False,
    materialize_narrative_graph: bool = True,
    materialize_narrative_tree: bool = True,
    outline_file: Path | None = None,
    export_markdown: bool = True,
    progress: ProgressCallback | None = None,
    global_chapter_offset: int = 0,
    total_target_chapters: int = 0,
    current_volume_number: int | None = None,
    total_volumes: int | None = None,
) -> ProjectPipelineResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    story_bible_result = None
    narrative_graph_result = None
    narrative_tree_result = None
    if materialize_story_bible:
        _emit_progress(
            progress,
            "story_bible_materialization_started",
            {"project_slug": project_slug},
        )
        story_bible_result = await materialize_latest_story_bible(
            session,
            project_slug,
            requested_by=requested_by,
        )
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "story_bible_materialization_completed",
            {
                "project_slug": project_slug,
                "workflow_run_id": str(story_bible_result.workflow_run_id),
            },
        )

    chapters = await _load_project_chapters(session, project.id)
    should_materialize = materialize_outline or not chapters
    materialization_result = None
    if should_materialize:
        _emit_progress(
            progress,
            "outline_materialization_started",
            {"project_slug": project_slug},
        )
        if outline_file is not None:
            batch = ChapterOutlineBatchInput.model_validate(load_json_file(outline_file))
            materialization_result = await materialize_chapter_outline_batch(
                session,
                project_slug,
                batch,
                requested_by=requested_by,
            )
        else:
            artifact = await get_latest_planning_artifact(
                session,
                project_id=project.id,
                artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
            )
            if artifact is None:
                raise ValueError(
                    f"Project '{project_slug}' does not have a stored chapter outline batch artifact."
                )
            materialization_result = await materialize_latest_chapter_outline_batch(
                session,
                project_slug,
                requested_by=requested_by,
            )
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "outline_materialization_completed",
            {
                "project_slug": project_slug,
                "workflow_run_id": str(materialization_result.workflow_run_id),
            },
        )
        chapters = await _load_project_chapters(session, project.id)

    if not chapters:
        raise ValueError(f"Project '{project_slug}' does not have any chapters to process.")

    # Validate chapter sequence has no gaps before starting generation
    chapter_numbers = sorted(ch.chapter_number for ch in chapters)
    sequence_gaps = detect_chapter_sequence_gaps(chapter_numbers)
    if sequence_gaps:
        logger.error(
            "Chapter sequence has gaps for '%s': missing %s",
            project_slug,
            sequence_gaps,
        )
        raise ValueError(
            f"Chapter sequence has gaps: missing chapters {sequence_gaps}. "
            f"Fix the outline before running the pipeline."
        )

    # Resume support: filter out already-completed chapters.
    # When accept_on_stall is enabled, chapters stuck in REVISION (stalled
    # rewrites from a prior run) are also treated as complete on resume.
    _resume_done_statuses = {ChapterStatus.COMPLETE.value}
    if settings.pipeline.accept_on_stall:
        _resume_done_statuses.add(ChapterStatus.REVISION.value)
    pending_chapters = [
        ch for ch in chapters
        if ch.status not in _resume_done_statuses
    ] if settings.pipeline.resume_enabled else chapters
    skipped_count = len(chapters) - len(pending_chapters)
    if skipped_count > 0:
        _emit_progress(
            progress,
            "resume_skipped_chapters",
            {
                "project_slug": project_slug,
                "skipped_count": skipped_count,
                "pending_count": len(pending_chapters),
                "total_count": len(chapters),
            },
        )

    if materialize_narrative_graph:
        _emit_progress(
            progress,
            "narrative_graph_materialization_started",
            {"project_slug": project_slug},
        )
        narrative_graph_result = await materialize_latest_narrative_graph(
            session,
            project_slug,
            requested_by=requested_by,
        )
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "narrative_graph_materialization_completed",
            {
                "project_slug": project_slug,
                "workflow_run_id": str(narrative_graph_result.workflow_run_id),
                "plot_arc_count": narrative_graph_result.plot_arc_count,
                "clue_count": narrative_graph_result.clue_count,
            },
        )

    if materialize_narrative_tree:
        _emit_progress(
            progress,
            "narrative_tree_materialization_started",
            {"project_slug": project_slug},
        )
        narrative_tree_result = await materialize_latest_narrative_tree(
            session,
            project_slug,
            requested_by=requested_by,
        )
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "narrative_tree_materialization_completed",
            {
                "project_slug": project_slug,
                "workflow_run_id": str(narrative_tree_result.workflow_run_id),
                "node_count": narrative_tree_result.node_count,
            },
        )

    _emit_progress(
        progress,
        "project_pipeline_started",
        {
            "project_slug": project_slug,
            "chapter_count": len(chapters),
            # Multi-volume progress context — populated only when invoked
            # from run_progressive_autowrite_pipeline so the UI can render a
            # book-wide progress bar instead of a per-volume one.
            "volume_number": current_volume_number,
            "volume_count": total_volumes,
            "project_chapter_count": total_target_chapters or len(chapters),
            "global_chapter_offset": global_chapter_offset,
        },
    )

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_PROJECT_PIPELINE,
        status=WorkflowStatus.RUNNING,
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="load_project_context",
        metadata={
            "project_slug": project_slug,
            "chapter_count": len(chapters),
            "materialize_story_bible": materialize_story_bible,
            "materialize_outline": should_materialize,
            "materialize_narrative_graph": materialize_narrative_graph,
            "materialize_narrative_tree": materialize_narrative_tree,
            "outline_file": str(outline_file) if outline_file is not None else None,
            "export_markdown": export_markdown,
            "story_bible_workflow_run_id": str(story_bible_result.workflow_run_id)
            if story_bible_result is not None
            else None,
            "materialization_workflow_run_id": str(materialization_result.workflow_run_id)
            if materialization_result is not None
            else None,
            "narrative_graph_workflow_run_id": str(narrative_graph_result.workflow_run_id)
            if narrative_graph_result is not None
            else None,
            "narrative_tree_workflow_run_id": str(narrative_tree_result.workflow_run_id)
            if narrative_tree_result is not None
            else None,
        },
    )

    step_order = 1
    current_step_name = "load_project_context"
    chapter_results: list[ProjectPipelineChapterSummary] = []

    try:
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "project_id": str(project.id),
                "chapter_numbers": [chapter.chapter_number for chapter in chapters],
                "story_bible_workflow_run_id": str(story_bible_result.workflow_run_id)
                if story_bible_result is not None
                else None,
                "materialization_workflow_run_id": str(materialization_result.workflow_run_id)
                if materialization_result is not None
                else None,
                "narrative_graph_workflow_run_id": str(narrative_graph_result.workflow_run_id)
                if narrative_graph_result is not None
                else None,
                "narrative_tree_workflow_run_id": str(narrative_tree_result.workflow_run_id)
                if narrative_tree_result is not None
                else None,
            },
        )
        step_order += 1

        requires_human_review = False
        consistency_check_interval = settings.pipeline.consistency_check_interval
        rolling_summary_interval = settings.pipeline.rolling_summary_interval
        chapters_since_last_check = 0
        chapters_since_last_summary = 0

        # Compute arc boundaries from volume plan for arc summary triggers
        arc_boundaries: set[int] = set()
        arc_boundary_info: dict[int, dict[str, int]] = {}
        _volume_plan = (project.metadata_json or {}).get("volume_plan")
        if isinstance(_volume_plan, list):
            _global_arc_idx = 0
            for _vp_entry in _volume_plan:
                if not isinstance(_vp_entry, dict):
                    continue
                _arc_ranges = _vp_entry.get("arc_ranges")
                if isinstance(_arc_ranges, list):
                    for _arc_range in _arc_ranges:
                        if isinstance(_arc_range, list) and len(_arc_range) == 2:
                            _a_start, _a_end = _arc_range
                            arc_boundaries.add(_a_end)
                            arc_boundary_info[_a_end] = {
                                "arc_start": _a_start,
                                "arc_index": _global_arc_idx,
                            }
                            _global_arc_idx += 1

        for chapter in pending_chapters:
            local_done = len(chapter_results) + skipped_count + 1
            global_done = global_chapter_offset + local_done
            _total = total_target_chapters or len(chapters)
            _emit_progress(
                progress,
                "chapter_pipeline_started",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter.chapter_number,
                    "progress": f"{local_done}/{len(chapters)}",
                    "global_progress": f"{global_done}/{_total}",
                    "target_word_count": int(chapter.target_word_count or 0),
                },
            )
            current_step_name = f"chapter_pipeline_{chapter.chapter_number}"
            workflow_run.current_step = current_step_name
            chapter_result = await run_chapter_pipeline(
                session,
                settings,
                project_slug,
                chapter.chapter_number,
                requested_by=requested_by,
                export_markdown=export_markdown,
            )
            chapter_results.append(
                ProjectPipelineChapterSummary(
                    chapter_number=chapter.chapter_number,
                    workflow_run_id=chapter_result.workflow_run_id,
                    chapter_draft_version_no=chapter_result.chapter_draft_version_no,
                    export_artifact_id=chapter_result.export_artifact_id,
                    requires_human_review=chapter_result.requires_human_review,
                    approved_scene_count=len(chapter_result.scene_results),
                )
            )
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "chapter_number": chapter.chapter_number,
                    "chapter_workflow_run_id": str(chapter_result.workflow_run_id),
                    "requires_human_review": chapter_result.requires_human_review,
                    "chapter_draft_version_no": chapter_result.chapter_draft_version_no,
                },
            )
            step_order += 1
            if chapter_result.requires_human_review:
                requires_human_review = True
            _completed_local = len(chapter_results) + skipped_count
            _completed_global = global_chapter_offset + _completed_local
            _emit_progress(
                progress,
                "chapter_pipeline_completed",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter.chapter_number,
                    "progress": f"{_completed_local}/{len(chapters)}",
                    "global_progress": f"{_completed_global}/{_total}",
                    "workflow_run_id": str(chapter_result.workflow_run_id),
                    "requires_human_review": chapter_result.requires_human_review,
                    "chapter_draft_version_no": chapter_result.chapter_draft_version_no,
                    "chapter_title": chapter.title,
                    "word_count": int(chapter.current_word_count or 0),
                    "target_word_count": int(chapter.target_word_count or 0),
                },
            )
            project.current_chapter_number = max(
                int(project.current_chapter_number or 0),
                chapter.chapter_number,
            )
            await sync_world_expansion_progress(session, project=project)
            # Checkpoint after each chapter so completed chapters survive a
            # later failure.  Without this, a crash at chapter N rolls back
            # chapters 1..N-1 as well, making resume start from chapter 1.
            await _checkpoint_commit(session)

            # Periodic consistency check every N chapters
            chapters_since_last_check += 1
            if (
                consistency_check_interval > 0
                and chapters_since_last_check >= consistency_check_interval
                and chapter != pending_chapters[-1]  # Skip if last chapter (full check happens later)
            ):
                chapters_since_last_check = 0
                _emit_progress(
                    progress,
                    "periodic_consistency_check_started",
                    {
                        "project_slug": project_slug,
                        "after_chapter": chapter.chapter_number,
                    },
                )
                current_step_name = f"periodic_consistency_check_after_ch{chapter.chapter_number}"
                workflow_run.current_step = current_step_name
                try:
                    # SAVEPOINT: any DB error here rolls back only the periodic
                    # check work and leaves the outer chapter-loop transaction
                    # usable for the next chapter.
                    async with session.begin_nested():
                        interim_review, interim_report, interim_quality = await review_project_consistency(
                            session,
                            settings,
                            project_slug,
                            workflow_run_id=workflow_run.id,
                            expect_project_export=False,
                        )
                        await create_workflow_step_run(
                            session,
                            workflow_run_id=workflow_run.id,
                            step_name=current_step_name,
                            step_order=step_order,
                            status=WorkflowStatus.COMPLETED,
                            output_ref={
                                "review_report_id": str(interim_report.id),
                                "quality_score_id": str(interim_quality.id),
                                "verdict": interim_review.verdict,
                                "is_periodic": True,
                            },
                        )
                    step_order += 1
                    # Store findings for next chapter's scene pipeline to pick up
                    if interim_review.findings:
                        try:
                            _consistency_warnings = [f.message for f in interim_review.findings[:10]]
                            project.metadata_json = {
                                **(project.metadata_json or {}),
                                "_pending_consistency_warnings": _consistency_warnings,
                            }
                            await session.flush()
                        except Exception:
                            logger.debug("Failed to store consistency warnings in project metadata", exc_info=True)
                    _emit_progress(
                        progress,
                        "periodic_consistency_check_completed",
                        {
                            "project_slug": project_slug,
                            "after_chapter": chapter.chapter_number,
                            "verdict": interim_review.verdict,
                        },
                    )
                except Exception:
                    # Periodic check failures should not block the pipeline
                    _emit_progress(
                        progress,
                        "periodic_consistency_check_failed",
                        {
                            "project_slug": project_slug,
                            "after_chapter": chapter.chapter_number,
                            "error": traceback.format_exc(),
                        },
                    )
                    step_order += 1

            # ── Rolling summary compression + voice drift detection ────
            # Both use the same counter to stay synchronized, especially
            # during resume where absolute chapter numbers may skip ahead.
            chapters_since_last_summary += 1
            if (
                rolling_summary_interval > 0
                and chapters_since_last_summary >= rolling_summary_interval
            ):
                chapters_since_last_summary = 0

                # Rolling summary
                _emit_progress(
                    progress,
                    "rolling_summary_started",
                    {
                        "project_slug": project_slug,
                        "from_chapter": max(1, chapter.chapter_number - rolling_summary_interval + 1),
                        "to_chapter": chapter.chapter_number,
                    },
                )
                try:
                    # SAVEPOINT: rolling summary is best-effort. Isolate any
                    # DB error so the next chapter can still write.
                    async with session.begin_nested():
                        summary_result = await compress_knowledge_window(
                            session,
                            settings,
                            project.id,
                            from_chapter=max(1, chapter.chapter_number - rolling_summary_interval + 1),
                            to_chapter=chapter.chapter_number,
                            workflow_run_id=workflow_run.id,
                        )
                    _emit_progress(
                        progress,
                        "rolling_summary_completed",
                        {
                            "project_slug": project_slug,
                            "to_chapter": chapter.chapter_number,
                            "facts_compressed": summary_result.fact_count_before,
                            "summary_created": summary_result.summary_fact_created,
                        },
                    )
                except Exception:
                    _emit_progress(
                        progress,
                        "rolling_summary_failed",
                        {
                            "project_slug": project_slug,
                            "after_chapter": chapter.chapter_number,
                            "error": traceback.format_exc(),
                        },
                    )

                # Voice drift detection (triggered at same interval, after summary)
                if chapter.chapter_number >= 4:
                    _emit_progress(
                        progress,
                        "voice_drift_check_started",
                        {
                            "project_slug": project_slug,
                            "chapter_number": chapter.chapter_number,
                        },
                    )
                    try:
                        # SAVEPOINT: voice drift detection + correction writeback
                        # is best-effort. Wrap the whole block (drift check +
                        # metadata flush) so an asyncpg ERROR state is rolled
                        # back cleanly without poisoning the outer transaction.
                        async with session.begin_nested():
                            drift_results = await check_all_pov_voice_drift(
                                session,
                                settings,
                                project.id,
                                recent_chapter_start=max(1, chapter.chapter_number - 10),
                                recent_chapter_end=chapter.chapter_number,
                                workflow_run_id=workflow_run.id,
                            )
                            drifted = [r for r in drift_results if r.drift_detected]
                            if drifted:
                                # Merge corrections with existing ones (don't overwrite)
                                corrections = {
                                    r.character_name: r.correction_prompt
                                    for r in drifted
                                    if r.correction_prompt
                                }
                                if corrections:
                                    meta = dict(project.metadata_json or {})
                                    existing_corrections = dict(meta.get("voice_corrections", {}))
                                    existing_corrections.update(corrections)
                                    meta["voice_corrections"] = existing_corrections
                                    project.metadata_json = meta
                                    await session.flush()
                        _emit_progress(
                            progress,
                            "voice_drift_check_completed",
                            {
                                "project_slug": project_slug,
                                "chapter_number": chapter.chapter_number,
                                "characters_checked": len(drift_results),
                                "drift_detected_count": len(drifted),
                                "drifted_characters": [r.character_name for r in drifted],
                            },
                        )
                    except Exception:
                        _emit_progress(
                            progress,
                            "voice_drift_check_failed",
                            {
                                "project_slug": project_slug,
                                "after_chapter": chapter.chapter_number,
                                "error": traceback.format_exc(),
                            },
                        )

            # ── Arc summary + world snapshot at arc boundaries ────────────
            if settings.pipeline.arc_summary_enabled and chapter.chapter_number in arc_boundaries:
                try:
                    async with session.begin_nested():
                        from bestseller.services.linear_arc_summary import (
                            generate_linear_arc_summary,
                            generate_linear_world_snapshot,
                            load_arc_chapter_summaries,
                            store_linear_arc_summary,
                            store_linear_world_snapshot,
                        )

                        arc_info = arc_boundary_info.get(chapter.chapter_number, {})
                        arc_start = arc_info.get("arc_start", chapter.chapter_number)
                        arc_idx = arc_info.get("arc_index", 0)

                        _emit_progress(
                            progress,
                            "arc_summary_started",
                            {
                                "project_slug": project_slug,
                                "chapter_number": chapter.chapter_number,
                                "arc_index": arc_idx,
                            },
                        )
                        chapter_summaries = await load_arc_chapter_summaries(
                            session, project.id, arc_start, chapter.chapter_number,
                        )
                        arc_summary = await generate_linear_arc_summary(
                            session, settings, project, arc_start, chapter.chapter_number,
                            chapter_summaries=chapter_summaries,
                        )
                        await store_linear_arc_summary(
                            session, project, arc_idx, arc_summary, arc_start, chapter.chapter_number,
                        )
                        if settings.pipeline.world_snapshot_enabled:
                            snapshot = await generate_linear_world_snapshot(
                                session, settings, project, chapter.chapter_number, arc_summary,
                            )
                            await store_linear_world_snapshot(
                                session, project, chapter.chapter_number, snapshot,
                            )
                        _emit_progress(
                            progress,
                            "arc_summary_completed",
                            {
                                "project_slug": project_slug,
                                "chapter_number": chapter.chapter_number,
                                "arc_index": arc_idx,
                            },
                        )
                except Exception:
                    _emit_progress(
                        progress,
                        "arc_summary_failed",
                        {
                            "project_slug": project_slug,
                            "after_chapter": chapter.chapter_number,
                            "error": traceback.format_exc(),
                        },
                    )

            # ─── Per-chapter commit checkpoint ─────────────────────────────
            # Splits the project pipeline into one short transaction per
            # chapter. Without this, the entire multi-chapter run sits inside
            # a single PostgreSQL transaction that can grow to hours, blocking
            # autovacuum and bloating MVCC version chains.
            await _checkpoint_commit(session)

        export_artifact_id: UUID | None = None
        output_path: str | None = None
        if export_markdown:
            _emit_progress(
                progress,
                "project_export_started",
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
                    "export_artifact_id": str(export_artifact_id),
                    "output_path": output_path,
                },
            )
            step_order += 1
            _emit_progress(
                progress,
                "project_export_completed",
                {
                    "project_slug": project_slug,
                    "export_artifact_id": str(export_artifact_id),
                    "output_path": output_path,
                },
            )

        review_result = None
        report = None
        quality = None
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
        requires_human_review = requires_human_review or review_result.verdict != "pass"
        _emit_progress(
            progress,
            "project_consistency_review_completed",
            {
                "project_slug": project_slug,
                "verdict": review_result.verdict,
                "review_report_id": str(report.id),
                "quality_score_id": str(quality.id),
                "requires_human_review": requires_human_review,
            },
        )

        project.current_chapter_number = max(chapter.chapter_number for chapter in chapters)
        await sync_world_expansion_progress(session, project=project)
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
            "requires_human_review": requires_human_review,
            "processed_chapter_count": len(chapter_results),
            "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
            "review_report_id": str(report.id) if report is not None else None,
            "quality_score_id": str(quality.id) if quality is not None else None,
            "final_verdict": review_result.verdict if review_result is not None else None,
        }
        await session.flush()
        # Final commit so the project pipeline closes its transaction before
        # returning to the autowrite orchestrator (or worker context manager).
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "project_pipeline_completed",
            {
                "project_slug": project.slug,
                "workflow_run_id": str(workflow_run.id),
                "final_verdict": review_result.verdict if review_result is not None else None,
                "requires_human_review": requires_human_review,
                "output_path": output_path,
            },
        )

        return ProjectPipelineResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            project_slug=project.slug,
            chapter_results=chapter_results,
            story_bible_workflow_run_id=story_bible_result.workflow_run_id
            if story_bible_result is not None
            else None,
            materialization_workflow_run_id=materialization_result.workflow_run_id
            if materialization_result is not None
            else None,
            narrative_graph_workflow_run_id=narrative_graph_result.workflow_run_id
            if narrative_graph_result is not None
            else None,
            narrative_tree_workflow_run_id=narrative_tree_result.workflow_run_id
            if narrative_tree_result is not None
            else None,
            review_report_id=report.id if report is not None else None,
            quality_score_id=quality.id if quality is not None else None,
            final_verdict=review_result.verdict if review_result is not None else None,
            export_artifact_id=export_artifact_id,
            output_path=output_path,
            requires_human_review=requires_human_review,
        )
    except Exception as exc:
        if isinstance(exc, PendingRollbackError) or not session.is_active:
            await session.rollback()
            raise
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        try:
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.FAILED,
                error_message=str(exc),
            )
            await session.flush()
        except PendingRollbackError:
            await session.rollback()
        raise


async def run_autowrite_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_payload: ProjectCreate,
    premise: str,
    requested_by: str = "system",
    export_markdown: bool = True,
    auto_repair_on_attention: bool = True,
    progress: ProgressCallback | None = None,
) -> AutowriteResult:
    # ── Route to progressive pipeline if enabled ──
    if settings.pipeline.progressive_planning:
        return await run_progressive_autowrite_pipeline(
            session, settings,
            project_payload=project_payload,
            premise=premise,
            requested_by=requested_by,
            export_markdown=export_markdown,
            auto_repair_on_attention=auto_repair_on_attention,
            progress=progress,
        )

    project = await get_project_by_slug(session, project_payload.slug)
    if project is None:
        _emit_progress(
            progress,
            "project_creation_started",
            {"project_slug": project_payload.slug},
        )
        project = await create_project(session, project_payload, settings)
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "project_creation_completed",
            {
                "project_slug": project.slug,
                "project_id": str(project.id),
            },
        )

    # Resume: check if planning artifact already exists
    existing_plan_artifact = await get_latest_planning_artifact(
        session,
        project_id=project.id,
        artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
    )
    if existing_plan_artifact is not None and settings.pipeline.resume_enabled:
        _emit_progress(
            progress,
            "planning_skipped_resume",
            {"project_slug": project.slug, "reason": "planning artifacts already exist"},
        )
        # Create a minimal planning result placeholder for downstream references
        from bestseller.domain.planning import NovelPlanningResult  # noqa: PLC0415

        planning_result = NovelPlanningResult(
            workflow_run_id=existing_plan_artifact.source_run_id or UUID(int=0),
            project_id=project.id,
            premise=premise,
            volume_count=0,
            chapter_count=0,
        )
    else:
        _emit_progress(
            progress,
            "planning_started",
            {"project_slug": project.slug},
        )
        planning_result = await generate_novel_plan(
            session,
            settings,
            project.slug,
            premise,
            requested_by=requested_by,
        )
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "planning_completed",
            {
                "project_slug": project.slug,
                "workflow_run_id": str(planning_result.workflow_run_id),
                "volume_count": planning_result.volume_count,
                "chapter_count": planning_result.chapter_count,
            },
        )

    _emit_progress(
        progress,
        "story_bible_materialization_started",
        {"project_slug": project.slug},
    )
    story_bible_result = await materialize_latest_story_bible(
        session,
        project.slug,
        requested_by=requested_by,
    )
    await _checkpoint_commit(session)
    _emit_progress(
        progress,
        "story_bible_materialization_completed",
        {
            "project_slug": project.slug,
            "workflow_run_id": str(story_bible_result.workflow_run_id),
        },
    )
    _emit_progress(
        progress,
        "outline_materialization_started",
        {"project_slug": project.slug},
    )
    outline_result = await materialize_latest_chapter_outline_batch(
        session,
        project.slug,
        requested_by=requested_by,
    )
    await _checkpoint_commit(session)
    _emit_progress(
        progress,
        "outline_materialization_completed",
        {
            "project_slug": project.slug,
            "workflow_run_id": str(outline_result.workflow_run_id),
        },
    )
    _emit_progress(
        progress,
        "narrative_graph_materialization_started",
        {"project_slug": project.slug},
    )
    narrative_graph_result = await materialize_latest_narrative_graph(
        session,
        project.slug,
        requested_by=requested_by,
    )
    await _checkpoint_commit(session)
    _emit_progress(
        progress,
        "narrative_graph_materialization_completed",
        {
            "project_slug": project.slug,
            "workflow_run_id": str(narrative_graph_result.workflow_run_id),
            "plot_arc_count": narrative_graph_result.plot_arc_count,
            "clue_count": narrative_graph_result.clue_count,
        },
    )
    _emit_progress(
        progress,
        "narrative_tree_materialization_started",
        {"project_slug": project.slug},
    )
    narrative_tree_result = await materialize_latest_narrative_tree(
        session,
        project.slug,
        requested_by=requested_by,
    )
    await _checkpoint_commit(session)
    _emit_progress(
        progress,
        "narrative_tree_materialization_completed",
        {
            "project_slug": project.slug,
            "workflow_run_id": str(narrative_tree_result.workflow_run_id),
            "node_count": narrative_tree_result.node_count,
        },
    )
    project_result = await run_project_pipeline(
        session,
        settings,
        project.slug,
        requested_by=requested_by,
        materialize_story_bible=False,
        materialize_outline=False,
        materialize_narrative_graph=False,
        materialize_narrative_tree=False,
        export_markdown=export_markdown,
        progress=progress,
    )
    repair_result = None
    if project_result.requires_human_review and auto_repair_on_attention:
        _emit_progress(
            progress,
            "auto_repair_started",
            {
                "project_slug": project.slug,
                "project_workflow_run_id": str(project_result.workflow_run_id),
                "final_verdict": project_result.final_verdict,
            },
        )
        from bestseller.services.repair import run_project_repair

        repair_result = await run_project_repair(
            session,
            settings,
            project.slug,
            requested_by=requested_by,
            export_markdown=export_markdown,
            progress=progress,
        )
        _emit_progress(
            progress,
            "auto_repair_completed",
            {
                "project_slug": project.slug,
                "workflow_run_id": str(repair_result.workflow_run_id),
                "final_verdict": repair_result.final_verdict,
                "requires_human_review": repair_result.requires_human_review,
            },
        )

    final_review_report_id = (
        repair_result.review_report_id if repair_result is not None else project_result.review_report_id
    )
    final_quality_score_id = (
        repair_result.quality_score_id if repair_result is not None else project_result.quality_score_id
    )
    final_export_artifact_id = (
        repair_result.export_artifact_id
        if repair_result is not None and repair_result.export_artifact_id is not None
        else project_result.export_artifact_id
    )
    final_output_path = (
        repair_result.output_path
        if repair_result is not None and repair_result.output_path is not None
        else project_result.output_path
    )
    final_verdict = repair_result.final_verdict if repair_result is not None else project_result.final_verdict
    final_requires_human_review = (
        repair_result.requires_human_review
        if repair_result is not None
        else project_result.requires_human_review
    )
    output_dir = (Path(settings.output.base_dir) / project.slug).resolve()
    output_files = _collect_output_files(output_dir)
    export_status = (
        "exported_requires_human_review"
        if final_export_artifact_id is not None and final_requires_human_review
        else "exported"
        if final_export_artifact_id is not None
        else "skipped_requires_human_review"
        if final_requires_human_review
        else "not_exported"
    )
    _emit_progress(
        progress,
        "autowrite_completed",
        {
            "project_slug": project.slug,
            "export_status": export_status,
            "output_dir": str(output_dir),
            "output_files": output_files,
            "final_verdict": final_verdict,
            "requires_human_review": final_requires_human_review,
        },
    )
    return AutowriteResult(
        project_id=project.id,
        project_slug=project.slug,
        planning_workflow_run_id=planning_result.workflow_run_id,
        story_bible_workflow_run_id=story_bible_result.workflow_run_id,
        outline_workflow_run_id=outline_result.workflow_run_id,
        narrative_graph_workflow_run_id=narrative_graph_result.workflow_run_id,
        narrative_tree_workflow_run_id=narrative_tree_result.workflow_run_id,
        project_workflow_run_id=project_result.workflow_run_id,
        repair_workflow_run_id=repair_result.workflow_run_id if repair_result is not None else None,
        repair_attempted=repair_result is not None,
        review_report_id=final_review_report_id,
        quality_score_id=final_quality_score_id,
        export_artifact_id=final_export_artifact_id,
        output_path=final_output_path,
        output_dir=str(output_dir),
        output_files=output_files,
        export_status=export_status,
        chapter_count=len(project_result.chapter_results),
        final_verdict=final_verdict,
        requires_human_review=final_requires_human_review,
    )


# ---------------------------------------------------------------------------
# Progressive Autowrite Pipeline (Phase 3)
# ---------------------------------------------------------------------------


async def run_progressive_autowrite_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_payload: ProjectCreate,
    premise: str,
    requested_by: str = "system",
    export_markdown: bool = True,
    auto_repair_on_attention: bool = True,
    progress: ProgressCallback | None = None,
) -> AutowriteResult:
    """Progressive planning pipeline: Foundation → per-volume (plan → write → feedback) loop.

    Characters and world evolve with the story — each volume's planning is
    informed by feedback from the previous volume's actual writing output.
    """
    from bestseller.services.planning_context import (  # noqa: PLC0415
        collect_volume_writing_feedback,
        summarize_volume_feedback,
    )

    project = await get_project_by_slug(session, project_payload.slug)
    if project is None:
        _emit_progress(progress, "project_creation_started", {"project_slug": project_payload.slug})
        project = await create_project(session, project_payload, settings)
        await _checkpoint_commit(session)
        _emit_progress(progress, "project_creation_completed", {"project_slug": project.slug, "project_id": str(project.id)})

    # ── Phase A: Foundation Plan ──
    existing_volume_plan = await get_latest_planning_artifact(
        session, project_id=project.id, artifact_type=ArtifactType.VOLUME_PLAN,
    )
    if existing_volume_plan is not None and settings.pipeline.resume_enabled:
        _emit_progress(progress, "foundation_planning_skipped_resume", {"project_slug": project.slug})
        from bestseller.domain.planning import NovelPlanningResult  # noqa: PLC0415
        planning_result = NovelPlanningResult(
            workflow_run_id=existing_volume_plan.source_run_id or UUID(int=0),
            project_id=project.id, premise=premise, volume_count=0, chapter_count=0,
        )
    else:
        _emit_progress(progress, "foundation_planning_started", {"project_slug": project.slug})
        planning_result = await generate_foundation_plan(
            session, settings, project.slug, premise, requested_by=requested_by,
        )
        await _checkpoint_commit(session)
        _emit_progress(progress, "foundation_planning_completed", {
            "project_slug": project.slug,
            "workflow_run_id": str(planning_result.workflow_run_id),
            "volume_count": planning_result.volume_count,
        })

    # ── Materialize story bible from foundation ──
    _emit_progress(progress, "story_bible_materialization_started", {"project_slug": project.slug})
    story_bible_result = await materialize_latest_story_bible(session, project.slug, requested_by=requested_by)
    await _checkpoint_commit(session)
    _emit_progress(progress, "story_bible_materialization_completed", {"project_slug": project.slug, "workflow_run_id": str(story_bible_result.workflow_run_id)})

    # ── Load planning artifacts for volume loop ──
    book_spec_art = await get_latest_planning_artifact(session, project_id=project.id, artifact_type=ArtifactType.BOOK_SPEC)
    world_spec_art = await get_latest_planning_artifact(session, project_id=project.id, artifact_type=ArtifactType.WORLD_SPEC)
    cast_spec_art = await get_latest_planning_artifact(session, project_id=project.id, artifact_type=ArtifactType.CAST_SPEC)
    volume_plan_art = await get_latest_planning_artifact(session, project_id=project.id, artifact_type=ArtifactType.VOLUME_PLAN)

    book_spec_payload = book_spec_art.content if book_spec_art else {}
    world_spec_payload = world_spec_art.content if world_spec_art else {}
    cast_spec_payload = cast_spec_art.content if cast_spec_art else {}
    volume_plan_payload = volume_plan_art.content if volume_plan_art else []

    # Normalize volume plan
    if isinstance(volume_plan_payload, dict):
        volume_plan_list = volume_plan_payload.get("volumes", [])
    elif isinstance(volume_plan_payload, list):
        volume_plan_list = volume_plan_payload
    else:
        volume_plan_list = []

    prior_feedback_summary: str | None = None
    prior_world_snapshot: str | None = None
    all_chapter_results: list[Any] = []
    total_volumes = len(volume_plan_list)
    # Initialize variables used after the loop to avoid UnboundLocalError
    outline_result = None
    narrative_graph_result = None
    narrative_tree_result = None
    vol_project_result = None

    # Book-wide totals so the web UI can render progress across the entire
    # multi-volume run, not just the current volume.
    _emit_progress(progress, "progressive_autowrite_started", {
        "project_slug": project.slug,
        "volume_count": total_volumes,
        "project_chapter_count": project.target_chapters or 0,
    })

    # ── Phase B: Per-volume loop ──
    for vol_idx, vol_entry in enumerate(volume_plan_list, start=1):
        vol_num = int(vol_entry.get("volume_number", 0)) or vol_idx

        _emit_progress(progress, "volume_planning_started", {
            "project_slug": project.slug, "volume_number": vol_num, "total_volumes": total_volumes,
        })

        # Plan this volume (cast expansion + world disclosure + outline)
        vol_plan_result = await generate_volume_plan(
            session, settings, project.slug, vol_num,
            book_spec=book_spec_payload,
            world_spec=world_spec_payload,
            cast_spec=cast_spec_payload,
            volume_plan=volume_plan_list,
            prior_feedback_summary=prior_feedback_summary,
            prior_world_snapshot=prior_world_snapshot,
            requested_by=requested_by,
        )
        await _checkpoint_commit(session)

        _emit_progress(progress, "volume_planning_completed", {
            "project_slug": project.slug, "volume_number": vol_num,
            "chapter_count": vol_plan_result.chapter_count,
            "new_characters": vol_plan_result.new_characters_introduced,
        })

        # Refresh canonical world/cast specs materialized by generate_volume_plan
        # so this volume's writing and the next volume's planning both see the
        # latest canon instead of the foundation snapshot.
        _emit_progress(progress, "story_bible_refresh_started", {
            "project_slug": project.slug, "volume_number": vol_num,
        })
        story_bible_result = await materialize_latest_story_bible(
            session,
            project.slug,
            requested_by=requested_by,
        )
        await _checkpoint_commit(session)
        _emit_progress(progress, "story_bible_refresh_completed", {
            "project_slug": project.slug,
            "volume_number": vol_num,
            "workflow_run_id": str(story_bible_result.workflow_run_id),
        })

        latest_world_spec = await get_latest_planning_artifact(
            session,
            project_id=project.id,
            artifact_type=ArtifactType.WORLD_SPEC,
        )
        latest_cast_spec = await get_latest_planning_artifact(
            session,
            project_id=project.id,
            artifact_type=ArtifactType.CAST_SPEC,
        )
        if latest_world_spec and isinstance(latest_world_spec.content, dict):
            world_spec_payload = latest_world_spec.content
        if latest_cast_spec and isinstance(latest_cast_spec.content, dict):
            cast_spec_payload = latest_cast_spec.content

        # Materialize the per-volume outline into the combined CHAPTER_OUTLINE_BATCH
        # so the existing chapter writing pipeline can pick it up
        vol_outline_art = await get_latest_planning_artifact(
            session, project_id=project.id, artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE,
        )
        if vol_outline_art and vol_outline_art.content:
            # Merge volume outline into cumulative CHAPTER_OUTLINE_BATCH
            existing_batch_art = await get_latest_planning_artifact(
                session, project_id=project.id, artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
            )
            existing_chapters: list[Any] = []
            if existing_batch_art and isinstance(existing_batch_art.content, dict):
                existing_chapters = existing_batch_art.content.get("chapters", [])
            elif existing_batch_art and isinstance(existing_batch_art.content, list):
                existing_chapters = existing_batch_art.content
            vol_chapters = (
                vol_outline_art.content.get("chapters", [])
                if isinstance(vol_outline_art.content, dict)
                else vol_outline_art.content if isinstance(vol_outline_art.content, list) else []
            )
            merged = {"batch_name": "progressive-merged-outline", "chapters": existing_chapters + vol_chapters}
            await import_planning_artifact(session, project.slug, PlanningArtifactCreate(
                artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH, content=merged,
            ))
            await _checkpoint_commit(session)

        # Materialize outline + narrative structures for this volume's chapters
        _emit_progress(progress, "outline_materialization_started", {"project_slug": project.slug})
        outline_result = await materialize_latest_chapter_outline_batch(session, project.slug, requested_by=requested_by)
        await _checkpoint_commit(session)
        _emit_progress(progress, "outline_materialization_completed", {"project_slug": project.slug, "workflow_run_id": str(outline_result.workflow_run_id)})

        _emit_progress(progress, "narrative_graph_materialization_started", {"project_slug": project.slug})
        narrative_graph_result = await materialize_latest_narrative_graph(session, project.slug, requested_by=requested_by)
        await _checkpoint_commit(session)
        _emit_progress(progress, "narrative_graph_materialization_completed", {"project_slug": project.slug, "workflow_run_id": str(narrative_graph_result.workflow_run_id)})

        _emit_progress(progress, "narrative_tree_materialization_started", {"project_slug": project.slug})
        narrative_tree_result = await materialize_latest_narrative_tree(session, project.slug, requested_by=requested_by)
        await _checkpoint_commit(session)
        _emit_progress(progress, "narrative_tree_materialization_completed", {"project_slug": project.slug, "workflow_run_id": str(narrative_tree_result.workflow_run_id)})

        # Write this volume's chapters via the existing project pipeline.
        # In multi-volume mode we deliberately skip the per-volume full-book
        # markdown export:
        #   1. The preflight hygiene check scans the full project, so a single
        #      natural-prose false positive anywhere would abort every volume.
        #   2. The per-chapter markdown files are still written incrementally
        #      by assemble_chapter_draft, and a final best-effort project
        #      export runs once after the whole loop completes.
        _emit_progress(progress, "volume_writing_started", {
            "project_slug": project.slug, "volume_number": vol_num,
            "total_volumes": total_volumes,
        })
        vol_project_result = await run_project_pipeline(
            session, settings, project.slug,
            requested_by=requested_by,
            materialize_story_bible=False,
            materialize_outline=False,
            materialize_narrative_graph=False,
            materialize_narrative_tree=False,
            export_markdown=False,
            progress=progress,
            global_chapter_offset=len(all_chapter_results),
            total_target_chapters=project.target_chapters or 0,
            current_volume_number=vol_num,
            total_volumes=total_volumes,
        )
        await _checkpoint_commit(session)
        all_chapter_results.extend(vol_project_result.chapter_results)
        _emit_progress(progress, "volume_writing_completed", {
            "project_slug": project.slug, "volume_number": vol_num,
            "chapters_written": len(vol_project_result.chapter_results),
        })

        # ── Collect feedback (反哺) for next volume ──
        _emit_progress(progress, "volume_feedback_collection_started", {
            "project_slug": project.slug, "volume_number": vol_num,
        })
        feedback = await collect_volume_writing_feedback(session, project.id, vol_num)
        prior_feedback_summary = summarize_volume_feedback(feedback, language=project.language)
        # Extract world snapshot for next volume's world disclosure
        world_snap = feedback.get("world_snapshot")
        if world_snap and isinstance(world_snap, dict):
            prior_world_snapshot = world_snap.get("summary", "")
        _emit_progress(progress, "volume_feedback_collected", {
            "project_slug": project.slug, "volume_number": vol_num,
            "character_evolutions": len(feedback.get("character_states", [])),
            "unresolved_threads": len(feedback.get("arc_summary", {}).get("unresolved_threads", [])),
        })

    # ── Final export + review ──
    # Best-effort project export: surface preflight failures as a warning
    # event but never let them mask a successful multi-volume write. The
    # per-chapter markdown files are still available even when the combined
    # project export is blocked by the hygiene check.
    exported_artifact = None
    exported_output_path: str | None = None
    if export_markdown:
        try:
            exported_artifact, exported_path = await export_project_markdown(session, settings, project.slug)
            exported_output_path = str(exported_path)
        except ValueError as export_err:
            _emit_progress(progress, "project_export_skipped", {
                "project_slug": project.slug,
                "reason": str(export_err),
            })
            logger.warning(
                "Final project export blocked for %s: %s (continuing; "
                "per-chapter markdown files remain available).",
                project.slug,
                export_err,
            )

    project_result = vol_project_result if vol_project_result is not None else ProjectPipelineResult(
        workflow_run_id=UUID(int=0), project_id=project.id, project_slug=project.slug,
        chapter_results=[], review_report_id=None, quality_score_id=None,
        export_artifact_id=None, output_path=None,
        final_verdict=None, requires_human_review=False,
    )

    repair_result = None
    if project_result.requires_human_review and auto_repair_on_attention:
        _emit_progress(progress, "auto_repair_started", {
            "project_slug": project.slug, "final_verdict": project_result.final_verdict,
        })
        from bestseller.services.repair import run_project_repair
        repair_result = await run_project_repair(
            session, settings, project.slug,
            requested_by=requested_by, export_markdown=export_markdown, progress=progress,
        )
        _emit_progress(progress, "auto_repair_completed", {
            "project_slug": project.slug, "workflow_run_id": str(repair_result.workflow_run_id),
        })

    final_review_report_id = repair_result.review_report_id if repair_result else project_result.review_report_id
    final_quality_score_id = repair_result.quality_score_id if repair_result else project_result.quality_score_id
    final_export_artifact_id = (
        repair_result.export_artifact_id if repair_result and repair_result.export_artifact_id
        else project_result.export_artifact_id or (exported_artifact.id if exported_artifact else None)
    )
    final_output_path = (
        repair_result.output_path if repair_result and repair_result.output_path
        else project_result.output_path or exported_output_path
    )
    final_verdict = repair_result.final_verdict if repair_result else project_result.final_verdict
    final_requires_human_review = repair_result.requires_human_review if repair_result else project_result.requires_human_review
    output_dir = (Path(settings.output.base_dir) / project.slug).resolve()
    output_files = _collect_output_files(output_dir)
    export_status = (
        "exported_requires_human_review" if final_export_artifact_id and final_requires_human_review
        else "exported" if final_export_artifact_id
        else "skipped_requires_human_review" if final_requires_human_review
        else "not_exported"
    )
    _emit_progress(progress, "autowrite_completed", {
        "project_slug": project.slug, "export_status": export_status,
        "output_dir": str(output_dir), "final_verdict": final_verdict,
    })
    return AutowriteResult(
        project_id=project.id,
        project_slug=project.slug,
        planning_workflow_run_id=planning_result.workflow_run_id,
        story_bible_workflow_run_id=story_bible_result.workflow_run_id,
        outline_workflow_run_id=outline_result.workflow_run_id if outline_result is not None else UUID(int=0),
        narrative_graph_workflow_run_id=narrative_graph_result.workflow_run_id if narrative_graph_result is not None else UUID(int=0),
        narrative_tree_workflow_run_id=narrative_tree_result.workflow_run_id if narrative_tree_result is not None else UUID(int=0),
        project_workflow_run_id=project_result.workflow_run_id,
        repair_workflow_run_id=repair_result.workflow_run_id if repair_result else None,
        repair_attempted=repair_result is not None,
        review_report_id=final_review_report_id,
        quality_score_id=final_quality_score_id,
        export_artifact_id=final_export_artifact_id,
        output_path=final_output_path,
        output_dir=str(output_dir),
        output_files=output_files,
        export_status=export_status,
        chapter_count=len(all_chapter_results),
        final_verdict=final_verdict,
        requires_human_review=final_requires_human_review,
    )
