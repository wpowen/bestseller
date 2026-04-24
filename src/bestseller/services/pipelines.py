from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, PendingRollbackError
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
from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ChapterStateSnapshotModel, ProjectModel, SceneCardModel, SceneDraftVersionModel, VolumeModel
from bestseller.services.audit_loop import (
    build_phase1_audit,
    run_and_persist_audit,
)
from bestseller.services.context import build_scene_writer_context_from_models
from bestseller.services.continuity import extract_chapter_state_snapshot, validate_fact_monotonicity
from bestseller.services.drafts import assemble_chapter_draft, generate_scene_draft
from bestseller.services.exports import export_chapter_markdown, export_project_markdown
from bestseller.services.scorecard import compute_scorecard, save_scorecard
from bestseller.services.invariants import (
    InvariantSeedError,
    invariants_from_dict,
    invariants_to_dict,
    seed_invariants,
)
from bestseller.services.writing_presets import infer_genre_preset
from bestseller.services.consistency import (
    contiguous_prefix_max,
    detect_chapter_sequence_gaps,
    review_project_consistency,
)
from bestseller.services.knowledge import propagate_scene_discoveries, refresh_scene_knowledge
from bestseller.services.planner import generate_foundation_plan, generate_novel_plan, generate_volume_plan
from bestseller.services.projects import create_project, get_project_by_slug, import_planning_artifact, load_json_file
from bestseller.services.query_broker import run_scene_query_brief
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
from bestseller.services.truth_version import assert_truth_materializations_fresh
from bestseller.services.voice_drift import check_all_pov_voice_drift
from bestseller.services.write_safety_gate import (
    WriteSafetyBlockError,
    assert_no_write_safety_blocks,
    findings_from_contradiction_result,
    findings_from_identity_violations,
    serialize_write_safety_findings,
)
from bestseller.services.world_expansion import sync_world_expansion_progress
from bestseller.settings import AppSettings


logger = logging.getLogger(__name__)


WORKFLOW_TYPE_SCENE_PIPELINE = "scene_pipeline"
WORKFLOW_TYPE_CHAPTER_PIPELINE = "chapter_pipeline"
WORKFLOW_TYPE_PROJECT_PIPELINE = "project_pipeline"
ProgressCallback = Callable[[str, dict[str, Any] | None], None]

# Books above this chapter target require progressive planning: a single
# monolithic plan would take hours and cannot evolve cast/world with feedback
# from earlier volumes. Web UI enforces this threshold at submission; worker
# self-heal must mirror it so resumed pipelines take the same path as the
# original run. When the two diverge, large books stall at the outline frontier
# because the non-progressive path only processes existing outline entries
# without planning new volumes.
PROGRESSIVE_CHAPTER_THRESHOLD = 50


def _should_use_progressive_pipeline(
    settings: AppSettings,
    project_payload: ProjectCreate,
) -> bool:
    """Decide which autowrite pipeline a submission should use.

    Progressive planning is required whenever:
      * ``settings.pipeline.progressive_planning`` is explicitly enabled, or
      * ``project_payload.target_chapters`` exceeds the threshold.

    The target-based trigger keeps web-ui submissions and worker self-heal
    aligned on the same path — historically they diverged (web used the
    threshold, worker used the setting) which caused large books to stall at
    the current outline frontier during self-heal.
    """
    if settings.pipeline.progressive_planning:
        return True
    target_chapters = int(getattr(project_payload, "target_chapters", 0) or 0)
    return target_chapters > PROGRESSIVE_CHAPTER_THRESHOLD


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


def _merge_progressive_outline_batch(
    existing_chapters: list[Any],
    incoming_chapters: list[Any],
) -> list[dict[str, Any]]:
    """Merge the current-volume outline into the cumulative project outline.

    The current replan becomes authoritative for the entire unwritten tail
    starting at its first ``chapter_number``. Older outline entries at or
    beyond that boundary are stale and must be dropped before the incoming
    chapters are inserted.
    """
    incoming_numbers = sorted(
        n
        for n in (
            ch.get("chapter_number")
            for ch in incoming_chapters
            if isinstance(ch, dict)
        )
        if isinstance(n, int) and n > 0
    )
    replace_from = min(incoming_numbers) if incoming_numbers else None

    by_number: dict[int, dict[str, Any]] = {}
    for ch in existing_chapters:
        if not isinstance(ch, dict):
            continue
        n = ch.get("chapter_number")
        if not isinstance(n, int) or n <= 0:
            continue
        if replace_from is not None and n >= replace_from:
            continue
        by_number[n] = ch

    for ch in incoming_chapters:
        if not isinstance(ch, dict):
            continue
        n = ch.get("chapter_number")
        if not isinstance(n, int) or n <= 0:
            continue
        by_number[n] = ch
    return [by_number[k] for k in sorted(by_number)]


_WRITTEN_CHAPTER_STATUSES: tuple[str, ...] = (
    ChapterStatus.DRAFTING.value,
    ChapterStatus.REVIEW.value,
    ChapterStatus.REVISION.value,
    ChapterStatus.COMPLETE.value,
)


async def maybe_persist_opening_archetype(
    session: AsyncSession,
    *,
    chapter: ChapterModel | Any,
    assigned_opening: Any,
    chapter_number: int,
) -> bool:
    """Idempotently persist the L3-picked opening archetype onto the chapter.

    The L3 ``PromptConstructor`` picks one ``OpeningArchetype`` per chapter as
    part of its diversity budget; without this persistence the choice only
    lives in in-memory state. Writing it to the chapter row is what makes
    cross-project novelty audits and post-hoc archetype stats possible.

    Idempotent: if ``chapter.opening_archetype`` is already set the call is a
    no-op. The first scene of a chapter "wins" the archetype; every later
    scene of the same chapter re-derives the same pick and must not clobber
    the persisted value.

    Non-fatal: any exception is swallowed with a debug log so a transient DB
    hiccup cannot block the scene generation pipeline.

    Returns ``True`` iff a new value was flushed in this call.
    """
    try:
        if assigned_opening is None:
            return False
        if getattr(chapter, "opening_archetype", None):
            return False
        value = getattr(assigned_opening, "value", assigned_opening)
        chapter.opening_archetype = str(value)
        await session.flush()
        logger.info(
            "ch%d: opening_archetype persisted as '%s'",
            chapter_number,
            value,
        )
        return True
    except Exception:
        logger.debug(
            "opening_archetype persist failed for ch%d (non-fatal)",
            chapter_number,
            exc_info=True,
        )
        return False


async def _count_written_chapters_in_volume(
    session: AsyncSession,
    project_id: UUID,
    volume_number: int,
) -> int:
    """Count chapters in a given volume that already have real content.

    Used by the Phase B loop to decide whether to skip ``generate_volume_plan``
    for a volume whose chapters are already drafted. Prevents the re-plan path
    from globally re-numbering chapters and re-inserting them after the writer
    has advanced (the root cause of the 200-chapter gap incident).
    """
    stmt = (
        select(func.count(ChapterModel.id))
        .join(VolumeModel, ChapterModel.volume_id == VolumeModel.id)
        .where(
            ChapterModel.project_id == project_id,
            VolumeModel.volume_number == volume_number,
            ChapterModel.status.in_(_WRITTEN_CHAPTER_STATUSES),
        )
    )
    result = await session.scalar(stmt)
    return int(result or 0)


async def _volume_fully_written(
    session: AsyncSession,
    project_id: UUID,
    volume_number: int,
) -> tuple[bool, int, int]:
    """Return (is_fully_written, written_count, total_count) for a volume.

    Evidence is drawn only from the DB — never from VOLUME_PLAN targets that
    may have drifted during replanning. The skip decision must not depend on
    plan metadata that the drift itself could have corrupted.
    """
    total_stmt = (
        select(func.count(ChapterModel.id))
        .join(VolumeModel, ChapterModel.volume_id == VolumeModel.id)
        .where(
            ChapterModel.project_id == project_id,
            VolumeModel.volume_number == volume_number,
        )
    )
    total = int(await session.scalar(total_stmt) or 0)
    if total <= 0:
        return (False, 0, 0)
    written = await _count_written_chapters_in_volume(session, project_id, volume_number)
    return (written >= total, written, total)


async def _ensure_project_invariants(
    session: AsyncSession,
    project: ProjectModel,
    settings: AppSettings,
) -> None:
    """Seed or reload ``ProjectInvariants`` onto the given project row.

    The invariants contract (L1) is stored as ``projects.invariants_json``.
    Seeding happens at most once per project; subsequent pipeline runs read
    the persisted payload instead of regenerating. We intentionally fail
    loud on invalid payloads — a drifted contract is worse than a fresh one
    because downstream stages will happily generate off a broken promise.
    """

    if project.invariants_json:
        try:
            invariants_from_dict(project.invariants_json)
        except InvariantSeedError:
            logger.warning(
                "project %s has invalid invariants payload; reseeding", project.slug
            )
        else:
            return

    # Eagerly load style_guide within the current async context. The relationship
    # is lazy-loaded by default, and accessing it via getattr outside a greenlet
    # triggers MissingGreenlet. refresh() performs the load through the async
    # session machinery, avoiding the lazy-load trap.
    try:
        await session.refresh(project, ["style_guide"])
    except Exception:
        logger.debug("failed to refresh style_guide for project %s", project.slug, exc_info=True)
    style_guide = getattr(project, "style_guide", None)
    pov = getattr(style_guide, "pov_type", None) or settings.generation.pov or "close_third"
    tense = getattr(style_guide, "tense", None) or "past"

    # Pull the genre preset's raw ``writing_profile_overrides`` so the Hype
    # Engine can pick up the preset-declared ``hype`` namespace (recipe_deck,
    # comedic_beat_density_target, etc.) plus the ``market`` fields
    # (reader_promise, selling_points, hook_keywords, chapter_hook_strategy)
    # without going through ``sanitize_genre_story_overrides`` — the latter
    # intentionally strips story content on the story-framework path.
    preset_overrides: dict[str, Any] = {}
    genre_preset = infer_genre_preset(project.genre, project.sub_genre)
    if genre_preset is not None:
        preset_overrides = dict(genre_preset.writing_profile_overrides)

    try:
        invariants = seed_invariants(
            project_id=project.id,
            language=project.language,
            words_per_chapter=settings.generation.words_per_chapter,
            pov=pov,
            tense=tense,
            overrides={"preset_overrides": preset_overrides},
        )
    except Exception as exc:  # pragma: no cover - defensive
        raise InvariantSeedError(
            f"seed_invariants failed for project {project.slug}: {exc}"
        ) from exc

    project.invariants_json = invariants_to_dict(invariants)
    await _checkpoint_commit(session)
    logger.info("seeded invariants for project %s", project.slug)


async def _enforce_truth_version_guard(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
) -> None:
    if not getattr(settings.pipeline, "enable_truth_version_guard", True):
        return
    await assert_truth_materializations_fresh(session, project)


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
    await _enforce_truth_version_guard(session, settings, project)

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
        # Nested draft/review work may roll back the shared session; persist
        # the scene workflow shell before entering the expensive path.
        await _checkpoint_commit(session)

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

        # ── Inject chapter auto-repair hint (C6) ──
        # When the chapter was blocked in a previous assembly and the auto-
        # repair loop has just reset this scene to NEEDS_REWRITE, the hint
        # stored on ``scene.metadata_json["auto_repair_hint"]`` tells the
        # writer *why* the rewrite is happening (e.g. "chapter too short,
        # expand this scene").  Surfacing it via ``contradiction_warnings``
        # reuses the existing "continuity constraints" rendering path so no
        # schema change is required.
        if shared_context is not None:
            try:
                _scene_meta = (
                    getattr(scene, "metadata_json", None) or {}
                )
                _repair_hint = str(
                    _scene_meta.get("auto_repair_hint") or ""
                ).strip()
                if _repair_hint:
                    _repair_codes = _scene_meta.get(
                        "auto_repair_block_codes"
                    ) or ()
                    _prefix = (
                        f"[章节自动修复 {','.join(_repair_codes)}] "
                        if _repair_codes
                        else "[章节自动修复] "
                    )
                    # Prepend so the writer sees the repair reason before
                    # any subsequent non-critical warnings.
                    shared_context.contradiction_warnings.insert(
                        0, _prefix + _repair_hint
                    )
            except Exception:
                logger.debug(
                    "auto_repair_hint injection failed for ch%d sc%d (non-fatal)",
                    chapter_number,
                    scene_number,
                    exc_info=True,
                )

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

        # ── Inject opening diversity block (only for scene 1 — chapter opener) ──
        # Show the LLM the last 5 chapter openings so it avoids repeating the
        # same sentence structure or setting description.
        if shared_context is not None and scene_number == 1:
            try:
                from bestseller.infra.db.models import ChapterDraftVersionModel
                from bestseller.services.deduplication import build_opening_diversity_block

                _recent_drafts = await session.execute(
                    select(
                        ChapterModel.chapter_number,
                        ChapterDraftVersionModel.content_md,
                    )
                    .join(
                        ChapterDraftVersionModel,
                        ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                    )
                    .where(
                        ChapterModel.project_id == project.id,
                        ChapterModel.chapter_number < chapter_number,
                        ChapterDraftVersionModel.is_current.is_(True),
                    )
                    .order_by(ChapterModel.chapter_number.desc())
                    .limit(5)
                )
                _recent_openings: list[tuple[int, str]] = []
                for _ch_num, _content in _recent_drafts.fetchall():
                    _lines = [
                        l.strip() for l in (_content or "").split("\n")
                        if l.strip() and not l.strip().startswith("#")
                    ]
                    if _lines:
                        _recent_openings.append((_ch_num, _lines[0]))
                if _recent_openings:
                    _lang = getattr(project, "language", None) or settings.generation.language
                    shared_context.opening_diversity_block = build_opening_diversity_block(
                        _recent_openings, language=_lang,
                    )
            except Exception:
                logger.debug("Opening diversity block injection failed (non-fatal)", exc_info=True)

        # ── Stage A + B: inject conflict / scene-purpose / env diversity blocks ──
        # Runs for ALL scenes (not just scene 1) — this is the main lever against
        # plot-template and setting reuse in long novels.
        if shared_context is not None:
            try:
                from bestseller.services.context import (
                    compute_conflict_history,
                    compute_env_history,
                    compute_scene_purpose_history,
                )
                from bestseller.services.deduplication import (
                    build_conflict_diversity_block,
                    build_env_diversity_block,
                    build_scene_purpose_diversity_block,
                )

                _lang = getattr(project, "language", None) or settings.generation.language
                _genre_pool_key = (project.metadata_json or {}).get("conflict_pool_key")
                if not _genre_pool_key:
                    # Heuristic: for female-lead no-CP novels flagged by genre/sub_genre
                    _genre = (getattr(project, "genre", None) or "").lower()
                    _sub_genre = ((project.metadata_json or {}).get("sub_genre") or "").lower()
                    if "female" in _genre or "female" in _sub_genre or "no_cp" in _sub_genre:
                        _genre_pool_key = "female_lead_no_cp"

                _conflicts = await compute_conflict_history(
                    session, project.id,
                    current_chapter=chapter_number,
                    current_scene=scene_number,
                    window=10,
                )
                _last_emerging_ch = (project.metadata_json or {}).get("_last_emerging_conflict_chapter")
                from bestseller.services.conflict_taxonomy import should_inject_emerging
                _inject_emerging = should_inject_emerging(
                    chapter_number,
                    int(_last_emerging_ch) if _last_emerging_ch else None,
                )
                shared_context.conflict_diversity_block = build_conflict_diversity_block(
                    _conflicts,
                    genre_pool_key=_genre_pool_key,
                    inject_emerging=_inject_emerging,
                    language=_lang,
                )

                _purposes = await compute_scene_purpose_history(
                    session, project.id,
                    current_chapter=chapter_number,
                    current_scene=scene_number,
                    window=5,
                )
                shared_context.scene_purpose_diversity_block = build_scene_purpose_diversity_block(
                    _purposes, language=_lang,
                )

                _envs = await compute_env_history(
                    session, project.id,
                    current_chapter=chapter_number,
                    current_scene=scene_number,
                    window=3,
                )
                shared_context.env_diversity_block = build_env_diversity_block(
                    _envs, language=_lang,
                )
            except Exception:
                logger.debug("Stage A/B diversity block injection failed (non-fatal)", exc_info=True)

        # ── Stage C + D: arc beat / five-layer / cliffhanger / tension / location ──
        # These blocks require knowing the project's target chapter count + POV.
        # They gracefully degrade to generic prompts when metadata is missing.
        if shared_context is not None:
            try:
                from bestseller.services.context import (
                    compute_arc_structure_for_pov,
                    compute_location_history,
                    compute_recent_hook_types,
                    compute_recent_tension_scores,
                )
                from bestseller.services.deduplication import (
                    build_arc_beat_block,
                    build_cliffhanger_diversity_block,
                    build_five_layer_thinking_block,
                    build_location_ledger_block,
                    build_tension_target_block,
                )

                _lang = getattr(project, "language", None) or settings.generation.language
                _total_chapters = (
                    getattr(project, "target_chapters", None)
                    or (project.metadata_json or {}).get("target_chapter_count")
                    or 100
                )

                # POV character lookup — prefer first participant, fall back to any.
                _participants = list(scene.participants or [])
                _pov_name = _participants[0] if _participants else None
                _inner_struct, _pov_display = await compute_arc_structure_for_pov(
                    session, project.id, pov_character_name=_pov_name,
                )
                shared_context.arc_beat_block = build_arc_beat_block(
                    _inner_struct,
                    chapter_number=chapter_number,
                    total_chapters=int(_total_chapters),
                    pov_name=_pov_display,
                    language=_lang,
                )
                shared_context.five_layer_block = build_five_layer_thinking_block(
                    language=_lang,
                )

                _hook_types = await compute_recent_hook_types(
                    session, project.id,
                    current_chapter=chapter_number,
                    window=5,
                )
                shared_context.cliffhanger_diversity_block = build_cliffhanger_diversity_block(
                    _hook_types,
                    chapter_number=chapter_number,
                    total_chapters=int(_total_chapters),
                    language=_lang,
                )

                _tensions = await compute_recent_tension_scores(
                    session, project.id,
                    current_chapter=chapter_number,
                    window=10,
                )
                shared_context.tension_target_block = build_tension_target_block(
                    chapter_number,
                    int(_total_chapters),
                    recent_tension_scores=_tensions,
                    language=_lang,
                )

                _locations = await compute_location_history(
                    session, project.id,
                    current_chapter=chapter_number,
                    current_scene=scene_number,
                    window=8,
                )
                # Best-effort current-location lookup from scene metadata.
                _current_loc: str | None = None
                try:
                    _scene_meta = getattr(scene, "metadata_json", None) or {}
                    _current_loc = (
                        _scene_meta.get("location_id")
                        or _scene_meta.get("location")
                        or getattr(scene, "location", None)
                    )
                except Exception:
                    _current_loc = None
                shared_context.location_ledger_block = build_location_ledger_block(
                    _current_loc,
                    _locations,
                    language=_lang,
                )
            except Exception:
                logger.debug("Stage C/D block injection failed (non-fatal)", exc_info=True)

        # ── L3 — DiversityBudget-sourced block (hot vocab + structured rotation) ──
        # Complements the deduplication.py heuristic blocks above: those use raw
        # text from prior scenes; this block surfaces the project-level typed
        # rotation state (OpeningArchetype, CliffhangerType enums + hot_vocab
        # counter) that the L5 gate enforces. Cheap lookup — one row join.
        if shared_context is not None:
            try:
                from bestseller.services.diversity_budget import (
                    load_diversity_budget,
                    render_budget_diversity_block,
                )
                from bestseller.infra.db.models import SceneCardModel as _SCM_for_closer

                _budget = await load_diversity_budget(session, project.id)
                _max_scene_row = await session.execute(
                    select(func.max(_SCM_for_closer.scene_number)).where(
                        _SCM_for_closer.chapter_id == chapter.id,
                    )
                )
                _max_scene = _max_scene_row.scalar_one_or_none() or scene_number
                _is_closer = int(scene_number) >= int(_max_scene)
                _bd_lang = getattr(project, "language", None) or settings.generation.language
                _budget_block = render_budget_diversity_block(
                    _budget,
                    language=_bd_lang,
                    is_chapter_opener=scene_number == 1,
                    is_chapter_closer=_is_closer,
                )
                if _budget_block:
                    shared_context.budget_diversity_block = _budget_block
            except Exception:
                logger.debug(
                    "DiversityBudget block injection failed (non-fatal)",
                    exc_info=True,
                )

        # ── Reader Hype Engine — per-chapter picker shared across scenes ──
        # Pulls hype_scheme from invariants, reuses the DiversityBudget above
        # for LRU state, derives the golden-finger ladder from the preset's
        # growth_curve when no explicit ladder is declared, and stamps the
        # shared_context with:
        #   - reader_contract_block (per-chapter cadence)
        #   - hype_constraints_block (per-chapter)
        #   - assigned_hype_{type,recipe_key,intensity} (persisted after draft)
        # Legacy projects (empty HypeScheme) → no-op.
        if shared_context is not None:
            try:
                from bestseller.services.hype_engine import (
                    extract_ladder_from_growth_curve,
                    GoldenFingerLadder,
                )
                from bestseller.services.prompt_constructor import (
                    build_chapter_hype_blocks,
                )

                _invariants_for_hype = None
                if project.invariants_json:
                    _invariants_for_hype = invariants_from_dict(project.invariants_json)
                _budget_for_hype = _budget if "_budget" in locals() else None
                if _budget_for_hype is None:
                    from bestseller.services.diversity_budget import (
                        load_diversity_budget as _load_budget,
                    )
                    _budget_for_hype = await _load_budget(session, project.id)
                if (
                    _invariants_for_hype is not None
                    and not _invariants_for_hype.hype_scheme.is_empty
                ):
                    _total_for_hype = (
                        getattr(project, "target_chapters", None)
                        or (project.metadata_json or {}).get("target_chapter_count")
                        or 100
                    )
                    _growth_curve = (
                        (project.metadata_json or {}).get("growth_curve")
                        or ""
                    )
                    _ladder: GoldenFingerLadder | None = None
                    if _growth_curve:
                        _ladder = extract_ladder_from_growth_curve(
                            _growth_curve, int(_total_for_hype)
                        )
                        if _ladder.is_empty:
                            _ladder = None
                    _hype_blocks = build_chapter_hype_blocks(
                        _invariants_for_hype,
                        _budget_for_hype,
                        chapter_no=chapter_number,
                        total_chapters=int(_total_for_hype),
                        pacing_profile=getattr(
                            settings.generation, "pacing_profile", "medium"
                        ) or "medium",
                        golden_finger_ladder=_ladder,
                    )
                    shared_context.reader_contract_block = (
                        _hype_blocks.reader_contract_block or None
                    )
                    shared_context.hype_constraints_block = (
                        _hype_blocks.hype_constraints_block or None
                    )
                    if _hype_blocks.assigned_hype_type is not None:
                        shared_context.assigned_hype_type = (
                            _hype_blocks.assigned_hype_type.value
                        )
                    if _hype_blocks.assigned_hype_recipe is not None:
                        shared_context.assigned_hype_recipe_key = (
                            _hype_blocks.assigned_hype_recipe.key
                        )
                    if _hype_blocks.assigned_hype_intensity is not None:
                        shared_context.assigned_hype_intensity = (
                            _hype_blocks.assigned_hype_intensity
                        )

                # L3 PromptConstructor: emit the diversity + methodology
                # + anti-slop block once per chapter and attach to the
                # shared packet. Legacy projects (invariants_json empty)
                # already fall through because ``_invariants_for_hype``
                # is None. When L3 is disabled in config we skip the call.
                try:
                    from bestseller.services.quality_gates_config import (
                        get_quality_gates_config,
                    )
                    _l3_cfg = get_quality_gates_config().l3
                    if (
                        _l3_cfg.enabled
                        and _invariants_for_hype is not None
                    ):
                        from bestseller.services.prompt_constructor import (
                            build_chapter_l3_blocks,
                        )
                        _l3_blocks = build_chapter_l3_blocks(
                            _invariants_for_hype,
                            _budget_for_hype,
                            chapter_no=chapter_number,
                            hot_vocab_window=_l3_cfg.hot_vocab_window_chapters,
                            hot_vocab_top_n=_l3_cfg.hot_vocab_top_n,
                            hot_vocab_min_count=_l3_cfg.hot_vocab_min_count,
                            no_repeat_within_openings=_l3_cfg.no_repeat_within_openings,
                        )
                        if not _l3_blocks.is_empty:
                            shared_context.l3_prompt_block = (
                                _l3_blocks.as_prompt_block() or None
                            )
                        # Persist the chosen opening archetype onto the
                        # chapter row the first time we see it. See
                        # ``maybe_persist_opening_archetype`` for the
                        # idempotency + non-fatal semantics.
                        await maybe_persist_opening_archetype(
                            session,
                            chapter=chapter,
                            assigned_opening=_l3_blocks.assigned_opening,
                            chapter_number=chapter_number,
                        )
                except Exception:
                    logger.debug(
                        "L3 prompt block injection failed for ch%d sc%d (non-fatal)",
                        chapter_number,
                        scene_number,
                        exc_info=True,
                    )
            except Exception:
                logger.debug(
                    "Hype block injection failed for ch%d sc%d (non-fatal)",
                    chapter_number,
                    scene_number,
                    exc_info=True,
                )

        # ── Pre-scene contradiction check (zero LLM cost) ──
        if settings.pipeline.enable_contradiction_checks and shared_context is not None:
            try:
                current_step_name = "pre_scene_contradiction_check"
                workflow_run.current_step = current_step_name
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
                _safety_findings = findings_from_contradiction_result(
                    _contradiction_result,
                    block_on_violation=getattr(
                        settings.pipeline,
                        "contradiction_block_on_violation",
                        True,
                    ),
                )
                if _safety_findings:
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "blocked_by_write_safety_gate": True,
                        "write_safety_gate_source": "contradiction",
                        "write_safety_findings": serialize_write_safety_findings(
                            _safety_findings
                        ),
                    }
                    assert_no_write_safety_blocks(
                        _safety_findings,
                        project_slug=project_slug,
                        chapter_number=chapter_number,
                        scene_number=scene_number,
                    )
            except WriteSafetyBlockError:
                raise
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

        # ── Plan-richness gate (zero LLM cost, pre-draft) ──
        # Validates that the scene card has concrete, specific purpose / state
        # fields before we spend tokens on the writer LLM. Thin cards force
        # the model into safe short-dialogue loops (see ch181 "浮标封锁").
        if (
            draft is None
            and getattr(settings.pipeline, "enable_scene_plan_richness_gate", True)
        ):
            try:
                from bestseller.services.scene_plan_richness import validate_scene_model

                _lang = getattr(project, "language", None) or settings.generation.language
                _richness = validate_scene_model(scene, language=_lang)
                if _richness.issues:
                    _codes = [i.code for i in _richness.issues]
                    logger.warning(
                        "Scene %d.%d richness %s — issues=%s",
                        chapter_number, scene_number, _richness.severity, _codes,
                    )
                    _block = _richness.to_prompt_block(language=_lang)
                    if shared_context is not None and _block:
                        shared_context.plan_richness_block = _block
                        # Also inject critical issues into contradiction_warnings
                        # so the writer sees them in the Tier-0 warnings section.
                        for i in _richness.critical_issues[:3]:
                            shared_context.contradiction_warnings.append(
                                f"[场景卡稠密度] {i.field_path}: {i.message}"
                            )
                    # Persist the findings on the scene metadata so the planner
                    # can pick them up on the next re-plan cycle.
                    try:
                        _meta = dict(getattr(scene, "metadata_json", {}) or {})
                        _meta["plan_richness"] = {
                            "severity": _richness.severity,
                            "issue_codes": _codes,
                            "checked_at_chapter": chapter_number,
                            "checked_at_scene": scene_number,
                        }
                        scene.metadata_json = _meta
                    except Exception:
                        logger.debug(
                            "Failed to persist richness findings on scene metadata (non-fatal)",
                            exc_info=True,
                        )
                    # Optionally block: raise so caller triggers re-plan path.
                    if (
                        _richness.severity == "critical"
                        and getattr(settings.pipeline, "scene_richness_block_on_critical", False)
                    ):
                        workflow_run.metadata_json = {
                            **workflow_run.metadata_json,
                            "blocked_by_richness_gate": True,
                            "richness_issue_codes": _codes,
                        }
                        raise ValueError(
                            f"Scene {chapter_number}.{scene_number} blocked by plan-richness "
                            f"gate: {_codes}. Re-plan required (card too thin)."
                        )
            except ValueError:
                raise
            except Exception:
                logger.debug("Plan-richness gate failed (non-fatal)", exc_info=True)

        if (
            draft is None
            and shared_context is not None
            and getattr(settings.pipeline, "enable_story_query_brief", False)
        ):
            try:
                query_brief = await run_scene_query_brief(
                    session,
                    settings,
                    project=project,
                    chapter_number=chapter_number,
                    scene_number=scene_number,
                    scene_title=scene.title,
                    scene_type=scene.scene_type,
                    participants=list(scene.participants or []),
                    story_purpose=str(scene.purpose.get("story", "") or ""),
                    emotion_purpose=str(scene.purpose.get("emotion", "") or ""),
                    context_packet=shared_context,
                )
                shared_context.query_brief = query_brief.get("brief")
                shared_context.query_trace = list(query_brief.get("trace") or [])
                workflow_run.metadata_json = {
                    **workflow_run.metadata_json,
                    "query_brief_rounds": query_brief.get("rounds"),
                    "query_brief_exit_reason": query_brief.get("exit_reason"),
                    "query_tool_call_count": len(shared_context.query_trace),
                }
            except Exception:
                logger.warning(
                    "Scene query brief failed for ch%d sc%d (non-fatal)",
                    chapter_number,
                    scene_number,
                    exc_info=True,
                )

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
                current_step_name = "post_draft_identity_check"
                workflow_run.current_step = current_step_name
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
                    _safety_findings = findings_from_identity_violations(
                        _id_violations,
                        block_on_violation=getattr(
                            settings.pipeline,
                            "identity_block_on_violation",
                            True,
                        ),
                        blocked_severities=getattr(
                            settings.pipeline,
                            "identity_block_severities",
                            ["critical", "major"],
                        ),
                    )
                    if _safety_findings:
                        scene.status = SceneStatus.NEEDS_REWRITE.value
                        workflow_run.metadata_json = {
                            **workflow_run.metadata_json,
                            "blocked_by_write_safety_gate": True,
                            "write_safety_gate_source": "identity",
                            "write_safety_findings": serialize_write_safety_findings(
                                _safety_findings
                            ),
                        }
                        assert_no_write_safety_blocks(
                            _safety_findings,
                            project_slug=project_slug,
                            chapter_number=chapter_number,
                            scene_number=scene_number,
                        )
            except WriteSafetyBlockError:
                raise
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
                    if _ch and _sc and ed.content_md:
                        _existing_texts.append((_ch.chapter_number, _sc.scene_number, ed.content_md))

                _dedup_findings = check_scene_duplication(draft.content_md, _existing_texts)
                if _dedup_findings:
                    logger.warning(
                        "Deduplication findings in ch%d sc%d: %s",
                        chapter_number, scene_number,
                        [(f["chapter"], f["scene"], f["similarity"], f["severity"]) for f in _dedup_findings],
                    )
                    if shared_context is not None:
                        # Forward to reviewer so duplication_score reflects broad-scope matches.
                        # (Cast to the expected schema; check_scene_duplication already uses it.)
                        shared_context.pipeline_duplication_findings = list(_dedup_findings)
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
        # Any SQLAlchemy DB-level failure (LockNotAvailableError wrapped in
        # DBAPIError, PendingRollbackError, connection errors) leaves the
        # session unusable. Attempting further writes triggers autoflush →
        # connection checkout → pool_pre_ping → ``MissingGreenlet`` which
        # masks the real error. Rollback first and re-raise so the reaper
        # can pick up the workflow_run row instead.
        if (
            isinstance(exc, (PendingRollbackError, DBAPIError))
            or not session.is_active
        ):
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
        except (PendingRollbackError, DBAPIError):
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
    await _enforce_truth_version_guard(session, settings, project)

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
        # Child scene pipelines can roll back the shared session on hard DB
        # errors. Persist the chapter workflow shell before descending.
        await _checkpoint_commit(session)

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
            chapter_draft = await assemble_chapter_draft(session, project_slug, chapter_number, settings=settings)

        # ── Chapter auto-repair loop (C6) ──
        # When the assembled chapter trips a repairable block code (default:
        # BLOCK_LOW / BLOCK_HIGH from the length-stability gate), reset every
        # scene to NEEDS_REWRITE with targeted hints, re-run the scene
        # pipeline for that chapter, and re-assemble.  Capped by
        # ``chapter_auto_repair_max_attempts`` so we fail closed on
        # pathological drafts instead of spinning.  Deterministic blocks
        # (L4/L5 naming / POV / dialog) fall through to the legacy blocked
        # path — those need human or planner attention, not more rewriting.
        auto_repair_attempts = 0
        auto_repair_cap = int(
            getattr(
                settings.pipeline,
                "chapter_auto_repair_max_attempts",
                0,
            )
            or 0
        )
        auto_repair_enabled = bool(
            getattr(settings.pipeline, "enable_chapter_auto_repair", False)
        )
        auto_repair_codes = tuple(
            str(c) for c in getattr(
                settings.pipeline,
                "chapter_auto_repair_repairable_codes",
                (),
            )
            or ()
            if c
        )
        while (
            auto_repair_enabled
            and auto_repair_cap > 0
            and auto_repair_attempts < auto_repair_cap
            and getattr(chapter, "production_state", None) == "blocked"
        ):
            try:
                from bestseller.services.drafts import (
                    maybe_prepare_chapter_auto_repair,
                )
                repair_triggered, block_codes = await maybe_prepare_chapter_auto_repair(
                    session,
                    project=project,
                    chapter=chapter,
                    repairable_codes=auto_repair_codes,
                )
            except Exception:
                logger.warning(
                    "Chapter %d auto-repair prepare failed (non-fatal)",
                    chapter_number,
                    exc_info=True,
                )
                break

            if not repair_triggered:
                logger.info(
                    "Chapter %d: block codes %s not auto-repairable — leaving "
                    "chapter in blocked state",
                    chapter_number,
                    list(block_codes) if block_codes else [],
                )
                break

            auto_repair_attempts += 1
            current_step_name = f"chapter_auto_repair_attempt_{auto_repair_attempts}"
            workflow_run.current_step = current_step_name
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "chapter_auto_repair_attempts": auto_repair_attempts,
                "chapter_auto_repair_last_block_codes": list(block_codes)
                if block_codes
                else [],
            }
            logger.warning(
                "Chapter %d: auto-repair attempt %d/%d triggered for blocks %s",
                chapter_number,
                auto_repair_attempts,
                auto_repair_cap,
                list(block_codes) if block_codes else [],
            )

            # Re-run scene pipelines — every scene was reset to NEEDS_REWRITE
            # by ``maybe_prepare_chapter_auto_repair``.  Iterate ALL scenes
            # this time, not just ``pending_scenes`` from the initial pass,
            # so the chapter reassembly has fresh content for every slot.
            repair_scenes = list(
                await session.scalars(
                    select(SceneCardModel)
                    .where(SceneCardModel.chapter_id == chapter.id)
                    .order_by(SceneCardModel.scene_number.asc())
                )
            )
            for _repair_scene in repair_scenes:
                _repair_result = await run_scene_pipeline(
                    session,
                    settings,
                    project_slug,
                    chapter_number,
                    _repair_scene.scene_number,
                    requested_by=requested_by,
                    parent_workflow_run_id=workflow_run.id,
                )
                if _repair_result.requires_human_review:
                    scene_requires_human_review = True
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name=f"{current_step_name}_scene_{_repair_scene.scene_number}",
                    step_order=step_order,
                    status=WorkflowStatus.COMPLETED,
                    output_ref={
                        "scene_number": _repair_scene.scene_number,
                        "scene_workflow_run_id": str(_repair_result.workflow_run_id),
                        "final_verdict": _repair_result.final_verdict,
                        "requires_human_review": _repair_result.requires_human_review,
                    },
                )
                step_order += 1

            # Re-assemble with the repaired scenes so the next gate pass sees
            # a fresh chapter_draft + the length-stability helper re-scores.
            current_step_name = f"chapter_auto_repair_reassemble_{auto_repair_attempts}"
            workflow_run.current_step = current_step_name
            chapter_draft = await assemble_chapter_draft(
                session, project_slug, chapter_number, settings=settings
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
                    "auto_repair_attempt": auto_repair_attempts,
                },
            )
            step_order += 1

        if auto_repair_attempts > 0 and getattr(chapter, "production_state", None) == "blocked":
            logger.warning(
                "Chapter %d: auto-repair exhausted %d attempt(s), still blocked — "
                "marking chapter for human review",
                chapter_number,
                auto_repair_attempts,
            )
            scene_requires_human_review = True

        # L2 per-chapter bible validation: detect stance flips lacking
        # a turning-point arc beat and deceased speakers; log findings on
        # the step output so the regen_loop can consume them on the next
        # scene pass.
        bible_findings: dict[str, int] | None = None
        try:
            from bestseller.services.quality_gates_config import (
                get_quality_gates_config,
            )
            _gates_cfg = get_quality_gates_config()
            if _gates_cfg.l2.enabled:
                from bestseller.services.bible_gate import (
                    validate_chapter_against_bible,
                )
                _bible_result = await validate_chapter_against_bible(
                    session,
                    project_id=chapter.project_id,
                    chapter_number=chapter_number,
                    only_enforce_from_chapter=_gates_cfg.l2.only_enforce_from_chapter,
                )
                bible_findings = {
                    "violations": len(_bible_result.violations),
                    "warnings": len(_bible_result.warnings),
                }
                if _bible_result.violations:
                    logger.warning(
                        "L2 bible_gate chapter %d: %d violation(s), %d warning(s)",
                        chapter_number,
                        len(_bible_result.violations),
                        len(_bible_result.warnings),
                    )
        except Exception:
            logger.debug(
                "L2 bible_gate per-chapter validation failed (non-fatal)",
                exc_info=True,
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
                **({"bible_findings": bible_findings} if bible_findings else {}),
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

            at_chapter_rewrite_limit = (
                chapter_rewrite_iterations >= settings.quality.max_chapter_revisions
            )
            accept_chapter_on_stall = (
                at_chapter_rewrite_limit and settings.pipeline.accept_on_stall
            )
            if (
                chapter_review_result.verdict == "pass"
                or chapter_rewrite_task is None
                or accept_chapter_on_stall
            ):
                if accept_chapter_on_stall:
                    reached_chapter_revision_limit = True
                    logger.info(
                        "Chapter %d reached max revisions (%d) — accepting best draft",
                        chapter_number,
                        chapter_rewrite_iterations,
                    )
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

                # ── L7 per-chapter audit (lightweight) ──
                # Runs PleasureDistributionAudit + SetupPayoffTrackerAudit
                # filtered to findings on the current chapter, then promotes
                # PLEASURE_SETUP_PAYOFF_DEBT to a pending RewriteTask so the
                # review loop compensates in a later chapter rather than
                # waiting for the book-end audit. Failures non-fatal.
                try:
                    from bestseller.services.quality_gates_config import (
                        get_quality_gates_config,
                    )
                    _l7_cfg = get_quality_gates_config().l7
                    if _l7_cfg.enabled:
                        from bestseller.services.audit_loop import (
                            build_per_chapter_audit,
                            run_and_persist_audit,
                            spawn_rewrite_tasks_from_findings,
                        )
                        async with session.begin_nested():
                            _audit_report = await run_and_persist_audit(
                                session,
                                project_id=project.id,
                                audit=build_per_chapter_audit(),
                                chapter_number=chapter.chapter_number,
                            )
                            _rewrites_created = await spawn_rewrite_tasks_from_findings(
                                session, _audit_report
                            )
                            if _rewrites_created:
                                logger.info(
                                    "Chapter %d L7 audit spawned %d rewrite task(s)",
                                    chapter.chapter_number,
                                    _rewrites_created,
                                )
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "Chapter %d L7 per-chapter audit failed (non-fatal)",
                        chapter.chapter_number,
                        exc_info=True,
                    )

                # ── L8 per-chapter scorecard refresh ──
                # Upserts NovelScorecardModel so dashboards see post-chapter
                # quality scores without waiting for book-end Stage 11.
                # Idempotent; failures non-fatal.
                try:
                    from bestseller.services.quality_gates_config import (
                        get_quality_gates_config,
                    )
                    _l8_cfg = get_quality_gates_config().l8
                    if _l8_cfg.enabled:
                        from bestseller.services.scorecard import (
                            update_scorecard_incrementally,
                        )
                        async with session.begin_nested():
                            await update_scorecard_incrementally(
                                session,
                                project_id=project.id,
                                chapter_number=chapter.chapter_number,
                                expected_chapter_count=project.target_chapters,
                            )
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "Chapter %d L8 per-chapter scorecard failed (non-fatal)",
                        chapter.chapter_number,
                        exc_info=True,
                    )
                break

            if at_chapter_rewrite_limit:
                # accept_on_stall=True case was handled above.  Only reached
                # when accept_on_stall=False → pause for human review.
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
        # Mirror the guard in ``run_scene_pipeline`` — any DB-level error
        # leaves the session unusable and follow-up writes explode with
        # ``MissingGreenlet``. Rollback first and let the reaper clean up.
        if (
            isinstance(exc, (PendingRollbackError, DBAPIError))
            or not session.is_active
        ):
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
        except (PendingRollbackError, DBAPIError):
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


async def _select_pending_chapters_for_resume(
    session: AsyncSession,
    chapters: list[ChapterModel],
    *,
    resume_enabled: bool,
    accept_on_stall: bool,
) -> tuple[list[ChapterModel], list[int]]:
    """Filter chapters for a resumed run, safely handling stalled REVISION.

    Returns ``(pending_chapters, draftless_revision_chapter_numbers)``.

    - When ``resume_enabled`` is False, every chapter is pending.
    - ``COMPLETE`` chapters are always skipped on resume.
    - ``REVISION`` chapters are skipped only when ``accept_on_stall`` is
      True AND they already have at least one ``ChapterDraftVersionModel``
      row (i.e. a chapter draft was assembled at least once).  A
      ``REVISION`` chapter with zero drafts means the writer crashed
      mid-chapter before assembling a draft; skipping would leave a
      permanent hole in the book (see prod incident on 2026-04-17:
      superhero-fiction-1776147970 ch 154, 186, 188).
    """
    if not resume_enabled:
        return list(chapters), []

    revision_ids = [
        ch.id for ch in chapters if ch.status == ChapterStatus.REVISION.value
    ]
    drafted_ids: set[UUID] = set()
    if accept_on_stall and revision_ids:
        drafted_rows = await session.scalars(
            select(func.distinct(ChapterDraftVersionModel.chapter_id)).where(
                ChapterDraftVersionModel.chapter_id.in_(revision_ids)
            )
        )
        drafted_ids = {row for row in drafted_rows}

    def _is_resume_done(ch: ChapterModel) -> bool:
        if ch.status == ChapterStatus.COMPLETE.value:
            return True
        if (
            accept_on_stall
            and ch.status == ChapterStatus.REVISION.value
            and ch.id in drafted_ids
        ):
            return True
        return False

    pending = [ch for ch in chapters if not _is_resume_done(ch)]
    draftless_revisions = [
        ch.chapter_number
        for ch in chapters
        if ch.status == ChapterStatus.REVISION.value
        and ch.id not in drafted_ids
    ]
    return pending, draftless_revisions


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
    chapter_numbers: set[int] | None = None,
) -> ProjectPipelineResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    # L1 ProjectInvariants — seed once, re-use across all downstream stages.
    # Seeding must happen before any LLM call so prompt construction and
    # output validation see a coherent contract from chapter 1 onward.
    await _ensure_project_invariants(session, project, settings)

    # ── Batch 2: Material Forge ────────────────────────────────────────────
    # When ``enable_forge_pipeline`` is on, run all 5 Forges before the
    # Planner so that project_materials exist for reference-style prompting.
    # Runs only on the first pass (when no project_materials exist yet) to
    # avoid re-forging on every resume.  Failures are logged but do NOT
    # abort the pipeline — the old non-reference path is the safe fallback.
    if settings.pipeline.enable_forge_pipeline:
        try:
            from bestseller.services.material_forge import forge_all_materials  # noqa: PLC0415
            from bestseller.infra.db.models import ProjectMaterialModel  # noqa: PLC0415
            from sqlalchemy import select, func  # noqa: PLC0415

            existing_count_result = await session.execute(
                select(func.count()).where(
                    ProjectMaterialModel.project_id == project.id
                )
            )
            existing_count = existing_count_result.scalar_one()
            if existing_count == 0:
                _emit_progress(
                    progress,
                    "material_forge_started",
                    {"project_slug": project_slug},
                )
                genre = (project.metadata_json or {}).get("genre", "")
                sub_genre = (project.metadata_json or {}).get("sub_genre")
                forge_results = await forge_all_materials(
                    session,
                    project_id=project.id,
                    genre=genre,
                    settings=settings,
                    sub_genre=sub_genre,
                )
                await _checkpoint_commit(session)
                total_forged = sum(r.emitted_count for r in forge_results)
                _emit_progress(
                    progress,
                    "material_forge_completed",
                    {"project_slug": project_slug, "total_forged": total_forged},
                )
        except Exception:
            logger.exception(
                "run_project_pipeline: material forge failed — continuing with legacy path"
            )

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

    if chapter_numbers is not None:
        chapters = [ch for ch in chapters if ch.chapter_number in chapter_numbers]
        if not chapters:
            raise ValueError(
                f"Project '{project_slug}' does not have any chapters matching the requested outline slice."
            )

    # Validate chapter sequence has no gaps before starting generation.
    #
    # On resume, stuck projects often have a discontiguous set of
    # ChapterModel rows (e.g. 1..50 + 101..150 — some prior outline
    # regen widened the numbering). Failing hard here would make
    # self-heal impossible: the pipeline could never even start.
    # Instead, when resume is enabled we trim to the contiguous 1..N
    # prefix and defer the remainder — the completed prefix still lets
    # downstream passes (outline repair, narrative rebuild) run and
    # eventually close the gap.
    chapter_numbers = sorted(ch.chapter_number for ch in chapters)
    sequence_gaps = detect_chapter_sequence_gaps(chapter_numbers)
    if sequence_gaps:
        prefix_max = contiguous_prefix_max(chapter_numbers)
        if settings.pipeline.resume_enabled and prefix_max is not None:
            logger.warning(
                "Chapter sequence has gaps for '%s': keeping contiguous 1..%d, "
                "deferring %d discontiguous chapter(s) %s",
                project_slug,
                prefix_max,
                len(sequence_gaps),
                sequence_gaps[:10] + (["..."] if len(sequence_gaps) > 10 else []),
            )
            chapters = [
                ch for ch in chapters
                if ch.chapter_number <= prefix_max
            ]
        else:
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
    # A REVISION chapter with no assembled ChapterDraftVersionModel must
    # NOT be skipped — that path leaves permanent holes in the book
    # (prod incident on 2026-04-17, multiple projects).  See
    # ``_select_pending_chapters_for_resume`` for full rationale.
    pending_chapters, draftless_revisions = await _select_pending_chapters_for_resume(
        session,
        chapters,
        resume_enabled=settings.pipeline.resume_enabled,
        accept_on_stall=settings.pipeline.accept_on_stall,
    )
    if draftless_revisions:
        logger.warning(
            "Found %d REVISION chapter(s) with no assembled chapter draft "
            "(%s) — re-queuing to prevent silent skip on resume.",
            len(draftless_revisions),
            draftless_revisions[:20] + (["..."] if len(draftless_revisions) > 20 else []),
        )
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

    await _enforce_truth_version_guard(session, settings, project)

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
        # Child chapter pipelines can roll back the shared session. Persist
        # the project workflow shell before entering the chapter loop.
        await _checkpoint_commit(session)

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
        project_review_not_pass = review_result.verdict != "pass"
        if project_review_not_pass:
            if settings.pipeline.accept_on_stall:
                logger.info(
                    "Project %s consistency verdict=%s — accepting per accept_on_stall; "
                    "skipping human-review pause.",
                    project_slug,
                    review_result.verdict,
                )
            else:
                requires_human_review = True
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

        # Stage 10 — Continuous Audit.
        # ---------------------------------------------------------------
        # Replay gap + L4 content checks over the finished project. Findings
        # are persisted so the Scorecard (Stage 11) and CLI ``audit`` command
        # see the same snapshot. Failures here are telemetry only — never
        # fail the pipeline because the novel itself already wrote.
        audit_finding_count = 0
        try:
            audit_report = await run_and_persist_audit(
                session, project.id, build_phase1_audit()
            )
            audit_finding_count = len(audit_report.findings)
            _emit_progress(
                progress,
                "continuous_audit_completed",
                {
                    "project_slug": project.slug,
                    "finding_count": audit_finding_count,
                    "critical": audit_report.has_critical,
                },
            )
        except Exception as audit_exc:  # pragma: no cover - telemetry guard
            logger.warning(
                "Stage 10 continuous audit failed for project %s: %s",
                project.slug,
                audit_exc,
            )

        # Stage 11 — Scorecard.
        # ---------------------------------------------------------------
        # Aggregate all evidence (chapter lengths, quality reports, audit
        # findings, diversity budget) into the single NovelScorecard row.
        # Dashboards read this; humans use ``bestseller scorecard`` to
        # triage.
        scorecard_quality_score: float | None = None
        try:
            scorecard = await compute_scorecard(
                session,
                project.id,
                expected_chapter_count=project.target_chapters,
            )
            await save_scorecard(session, scorecard)
            scorecard_quality_score = scorecard.quality_score
            _emit_progress(
                progress,
                "scorecard_computed",
                {
                    "project_slug": project.slug,
                    "quality_score": scorecard.quality_score,
                    "total_chapters": scorecard.total_chapters,
                    "missing_chapters": scorecard.missing_chapters,
                    "chapters_blocked": scorecard.chapters_blocked,
                },
            )
        except Exception as scorecard_exc:  # pragma: no cover - telemetry guard
            logger.warning(
                "Stage 11 scorecard failed for project %s: %s",
                project.slug,
                scorecard_exc,
            )

        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "audit_finding_count": audit_finding_count,
            "scorecard_quality_score": scorecard_quality_score,
        }

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
                "audit_finding_count": audit_finding_count,
                "scorecard_quality_score": scorecard_quality_score,
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
        # Same guard as the scene/chapter pipelines — DB-level failures must
        # rollback-and-raise so follow-up writes don't trigger
        # ``MissingGreenlet`` during connection checkout.
        if (
            isinstance(exc, (PendingRollbackError, DBAPIError))
            or not session.is_active
        ):
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
        except (PendingRollbackError, DBAPIError):
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
    # ── Route to progressive pipeline if enabled or target warrants it ──
    if _should_use_progressive_pipeline(settings, project_payload):
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

        # Skip replanning if this volume is already fully written. Re-running
        # generate_volume_plan against a drifted volume_plan is what produced
        # the 200-chapter gap on xianxia-upgrade-1776137730: the fallback
        # re-seeded chapter_number globally across all volumes and reinserted
        # chapters past the writer frontier. Evidence is DB-only — the skip
        # decision must not depend on plan targets that the drift could have
        # corrupted.
        if settings.pipeline.resume_enabled:
            fully_written, written_count, total_count = await _volume_fully_written(
                session, project.id, vol_num,
            )
            if fully_written:
                logger.info(
                    "Volume %d already fully written (%d/%d chapters) — skipping replanning.",
                    vol_num, written_count, total_count,
                )
                _emit_progress(progress, "volume_planning_skipped_resume", {
                    "project_slug": project.slug,
                    "volume_number": vol_num,
                    "written": written_count,
                    "total": total_count,
                })
                continue

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
        vol_chapters: list[Any] = []
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
            merged_chapters = _merge_progressive_outline_batch(
                existing_chapters,
                vol_chapters,
            )
            merged = {
                "batch_name": "progressive-merged-outline",
                "chapters": merged_chapters,
            }
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
        current_volume_chapter_numbers = {
            ch.get("chapter_number")
            for ch in vol_chapters
            if isinstance(ch, dict) and isinstance(ch.get("chapter_number"), int)
        }
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
            chapter_numbers=current_volume_chapter_numbers,
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

        # ── Volume audit (质量反哺) — best-effort; never fails the pipeline ──
        try:
            from bestseller.services.volume_audit import run_volume_audit  # noqa: PLC0415
            _audit_output_root = Path(settings.output.base_dir)
            audit_digest = await run_volume_audit(
                session,
                project.slug,
                vol_num,
                output_root=_audit_output_root,
            )
            if audit_digest and prior_feedback_summary:
                prior_feedback_summary = audit_digest + "\n\n" + prior_feedback_summary
            elif audit_digest:
                prior_feedback_summary = audit_digest
            _emit_progress(progress, "volume_audit_completed", {
                "project_slug": project.slug, "volume_number": vol_num,
                "digest": audit_digest[:120] if audit_digest else "",
            })
        except Exception as _audit_exc:
            logger.warning(
                "volume audit skipped for %s v%s: %s",
                project.slug, vol_num, _audit_exc,
            )

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
