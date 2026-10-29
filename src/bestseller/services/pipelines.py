from __future__ import annotations

from bestseller.services.chapter_regeneration_feedback import build_regeneration_feedback
from bestseller.services.planning_capability_selection import PlanningCapabilitySelectionError

from bestseller.services.execution_stage import pipeline_operation_stage, attach_workflow_failure
from bestseller.services.production_prompt_audit import (
    bind_resolved_project_prompt_audit,
    project_prompt_audit_scope,
)
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.anti_meta_gate import AntiMetaActionConfigError

from bestseller.services.invariants import project_invariants_from_row

from collections.abc import Callable, Iterable, Mapping, Sequence
from asyncio import CancelledError
from copy import deepcopy
from dataclasses import dataclass, field
import datetime as _dt
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from bestseller.services.reviews import rewrite_source_binding
import re
import traceback
from typing import TYPE_CHECKING, Any, NamedTuple, cast
from uuid import UUID

if TYPE_CHECKING:  # pragma: no cover — import only for type hints
    from bestseller.services.book_runtime_guard import DriftReport

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import DBAPIError, MissingGreenlet, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bestseller.domain.context import SceneWriterContextPacket
from bestseller.domain.enums import (
    ArtifactType,
    ChapterStatus,
    DraftPromotionState,
    ProjectStatus,
    SceneStatus,
    WorkflowStatus,
)
from bestseller.domain.pipeline import (
    ChapterPipelineResult,
    ChapterPipelineSceneSummary,
    ProjectPipelineChapterSummary,
    ProjectPipelineResult,
    ScenePipelineResult,
)
from bestseller.domain.planning import AutowriteResult, PlanningArtifactCreate
from bestseller.domain.project import ProjectCreate
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import (
    ChapterContractModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterQualityReportModel,
    ChapterStateSnapshotModel,
    CharacterModel,
    ChaseDebtModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneDraftVersionModel,
    VolumeModel,
    WorkflowRunModel,
    WorldRuleModel,
)
from bestseller.services.audit_loop import (
    build_phase1_audit,
    run_and_persist_audit,
)
from bestseller.services.book_closure import (
    SETTLED_PRODUCTION_STATES,
    settle_project_status_on_closure,
)
from bestseller.services.book_design import assert_current_world_snapshot
from bestseller.services.chapter_generation_input_builder import (
    build_chapter_generation_input_bundle,
)
from bestseller.services.chapter_length_gate import (
    DEFAULT_HARD_FLOOR_ZH_CHARS,
    DEFAULT_HARD_MAX_ZH_CHARS,
    DEFAULT_SOFT_WARNING_ZH_CHARS,
    count_zh_chars,
)
from bestseller.services.chapter_outline_readiness_gate import (
    chapter_scene_budget_sum_thresholds,
    evaluate_chapter_outline_readiness,
)
from bestseller.services.chapter_predraft_quality_gate import (
    evaluate_chapter_predraft_quality,
)
from bestseller.services.chapter_scene_contract_materializer import (
    chapter_contract_has_adopted_source,
    materialize_chapter_contract_from_chapter,
    materialize_chapter_scene_contracts,
)
from bestseller.services.chase_debt_ledger import accrue_debt_rows
from bestseller.services.commercial_planning_readiness import (
    ChapterPlanProbe,
    ScenePlanProbe,
    commercial_planning_readiness_report_to_dict,
    evaluate_commercial_planning_readiness,
)
from bestseller.services.consistency import (
    contiguous_prefix_max,
    detect_chapter_sequence_gaps,
    review_project_consistency,
)
from bestseller.services.context import (
    build_chapter_writer_context,
    build_scene_writer_context_from_models,
)
from bestseller.services.continuity import (
    check_countdown_arithmetic,
    check_time_regression,
    extract_chapter_state_snapshot,
    load_previous_chapter_snapshot,
    validate_fact_monotonicity,
)
from bestseller.services.draft_promotion import (
    DraftQualityIneligibleError,
    draft_supersession_codes,
    numeric_promotion_debt_receipt,
    mark_candidate_under_review,
    mark_draft_eligible,
    promote_chapter_draft,
    promote_scene_draft,
    quarantine_draft,
)
from bestseller.services.drafts import (
    _chapter_length_contract_band,
    _front10_forbidden_signal_terms,
    _prompt_safe_forbidden_actions,
    _redact_front10_prompt_leaks,
    assemble_chapter_draft,
    audit_persisted_story_effect_realization,
    authoritative_word_count_for_language,
    generate_chapter_draft_once,
    generate_scene_draft,
    render_hype_preservation_block,
    resync_draft_word_count,
)
from bestseller.services.emotion_kernel_backfill import ensure_project_emotion_driven_kernel
from bestseller.services.entry_system_backfill import ensure_project_entry_system_compat
from bestseller.services.exports import (
    export_chapter_markdown,
    export_project_closure_draft_markdown,
    export_project_markdown,
    write_commercial_package_sidecars,
)
from bestseller.services.fanqie_market_repository import (
    evaluate_and_persist_fanqie_long_readiness,
    load_current_chapter_texts_for_fanqie_gate,
)
from bestseller.services.gate_registry import (
    chapter_local_debt_allows_continuation,
    core_block_metadata_keys,
    pause_reason_is_structural,
)
from bestseller.services.generation_policy import generation_unit_preference_from_metadata
from bestseller.services.invariants import (
    InvariantSeedError,
)
from bestseller.services.knowledge import propagate_scene_discoveries, refresh_scene_knowledge
from bestseller.services.narrative_line_tracker import (
    classify_chapter as classify_chapter_lines,
)
from bestseller.services.narrative_line_tracker import (
    persist_history as persist_line_history,
)
from bestseller.services.planner import (
    PlannerFallbackError,
    _outline_judge_project_brief,
    generate_foundation_plan,
    generate_novel_plan,
    generate_volume_plan,
    project_uses_signing_quality_gate,
)
from bestseller.services.premium_genre_engine import build_premium_genre_engine_blocks
from bestseller.services.production_control import load_control_state
from bestseller.services.projects import (
    create_project,
    get_project_by_slug,
    import_planning_artifact,
    load_json_file,
)
from bestseller.services.public_emotion_backfill import ensure_project_public_emotion_kernels
from bestseller.services.qimao_opening_gate import (
    evaluate_qimao_opening_gate,
    qimao_opening_gate_report_to_dict,
)
from bestseller.services.qimao_planning_gate import (
    evaluate_qimao_planning_gate,
    qimao_planning_gate_report_to_dict,
)
from bestseller.services.quality_gates_config import get_quality_gates_config
from bestseller.services.query_broker import run_scene_query_brief
from bestseller.services.reviews import (
    build_qimao_opening_rewrite_instructions,
    chapter_review_body_fingerprint,
    qimao_opening_rewrite_strategy_for_findings,
    review_chapter_draft,
    review_scene_draft,
    rewrite_chapter_from_task,
    rewrite_scene_from_task,
)
from bestseller.services.scorecard import compute_scorecard, save_scorecard
from bestseller.services.story_engine import (
    resolve_story_engine_rollout_decision_from_db,
)
from bestseller.services.story_engine_review import (
    StoryEngineReceiptRejected,
    StoryEngineReceiptVerdict,
    extract_story_engine_receipt_observation,
    promote_chapter_draft_with_story_engine_receipt,
    review_story_engine_transition,
)
from bestseller.services.summarization import compress_knowledge_window
from bestseller.services.truth_version import (
    TruthVersionStaleError,
    assert_truth_materializations_fresh,
)
from bestseller.services.voice_drift import check_all_pov_voice_drift
from bestseller.services.whole_book_quality_gate import (
    build_whole_book_quality_rewrite_instructions,
    evaluate_whole_book_quality,
    whole_book_quality_report_to_dict,
    whole_book_quality_strategy_for_findings,
)
from bestseller.services.workflows import (
    WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE,
    WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH,
    WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_TREE,
    WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
    create_workflow_run,
    create_workflow_step_run,
    ensure_project_identity_manifest,
    get_latest_completed_workflow_run,
    get_latest_approved_planning_artifact,
    materialize_chapter_outline_batch,
    materialize_latest_chapter_outline_batch,
    materialize_latest_narrative_graph,
    materialize_latest_narrative_tree,
    materialize_latest_story_bible,
)
from bestseller.services.world_expansion import sync_world_expansion_progress
from bestseller.services.write_safety_gate import (
    WriteSafetyBlockError,
    assert_no_write_safety_blocks,
    findings_from_contradiction_result,
    findings_from_identity_violations,
    serialize_write_safety_findings,
)
from bestseller.services.writing_profile import is_english_language
from bestseller.settings import AppSettings
from bestseller.services import full_flow_length

logger = logging.getLogger(__name__)


_DESLOP_REVIEW_METADATA_KEYS = (
    "reader_judge",
    "retention_gate_last_findings",
    "continuity_latest",
    "chapter_continuity_latest",
    "deterministic_audit_latest",
    # Historical aliases are included so an old deployment cannot leak a
    # body-bound verdict onto newly selected prose.
    "reader_judge_latest",
    "retention_latest",
)


async def _select_deslop_candidate(
    session: AsyncSession,
    *,
    chapter: ChapterModel,
    candidate: ChapterDraftVersionModel,
    language: str = "zh-CN",
    selected_reason: str,
    selection_pool: Sequence[ChapterDraftVersionModel] = (),
    selection_origin: str | None = None,
    settings: AppSettings | None = None,
) -> ChapterDraftVersionModel:
    """Make one already-persisted candidate current without promoting it.

    Current-body selection, word-count synchronization, supersession lineage,
    and body-bound review invalidation are deliberately atomic at this call
    site.  The chapter's required/block state is left untouched: removing a
    stale report from the new body's live namespace is not evidence of a pass.
    """

    candidate_metadata = candidate.promotion_metadata or {}
    if "book_length_control" in candidate_metadata:
        from bestseller.settings import load_settings
        length_settings = settings or load_settings()
        length_request = full_flow_length.require_feasible_request(
            candidate_metadata["book_length_control"])
        preview = full_flow_length.evaluate_candidate(length_request, candidate.content_md)
        if not preview["length_passed"]:
            raise full_flow_length.LengthControlError(preview["issue_code"])
        project = await session.scalar(select(ProjectModel).where(ProjectModel.id == chapter.project_id))
        if project is None:
            raise full_flow_length.LengthControlError("LENGTH_CONTROL_PROJECT_UNKNOWN")
        recheck = await full_flow_length.revalidate_request(
            session, length_settings, project=project, chapter=chapter, receipt=length_request)
        if not recheck["valid"]:
            raise full_flow_length.LengthControlError(recheck["issue_code"])
    information = candidate_metadata.get("information_preservation")
    if isinstance(information, Mapping) and information.get("automatic_selection_allowed") is False:
        raise ValueError("deslop_candidate_information_change_requires_review")

    previous_current = await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    candidate_metadata = dict(candidate.promotion_metadata or {})
    candidate_metadata.pop("unselected_reason", None)
    candidate_metadata.update(
        {
            "auto_promoted": False,
            "candidate_status": "selected",
            "selected_reason": selected_reason,
        }
    )
    if previous_current is not None and previous_current.id == candidate.id:
        candidate.promotion_metadata = candidate_metadata
        chapter.current_word_count = candidate.word_count
        return candidate

    await session.execute(
        update(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .values(is_current=False)
    )
    await session.flush()

    candidate.is_current = True
    candidate.promotion_state = DraftPromotionState.CANDIDATE.value
    candidate.promotion_reason_codes = draft_supersession_codes(
        origin=selection_origin or f"ai_flavor_{(candidate.promotion_metadata or {}).get('transformation') or 'deslop_rewrite'}",
        took_current=True,
        supersedes_version=(
            int(previous_current.version_no)
            if previous_current is not None
            else int((candidate.promotion_metadata or {}).get("parent_version_no") or 0)
        ),
        hold_reason=None,
    )
    candidate.promotion_metadata = candidate_metadata

    for other in selection_pool:
        if other.id == candidate.id:
            continue
        other.promotion_metadata = {
            **(other.promotion_metadata or {}),
            "candidate_status": "unselected",
            "unselected_reason": f"not_selected:{selected_reason}",
        }

    metadata = deepcopy(dict(getattr(chapter, "metadata_json", None) or {}))
    previous_fingerprint = None
    if previous_current is not None:
        previous_fingerprint = chapter_review_body_fingerprint(previous_current.content_md)
        review_reports = {
            key: deepcopy(metadata[key])
            for key in _DESLOP_REVIEW_METADATA_KEYS
            if key in metadata
        }
        if review_reports or metadata.get("current_body_review_status") is not None:
            historical_review_snapshot = {
                "body_fingerprint": previous_fingerprint,
                "review_status": metadata.get("current_body_review_status"),
                "reports": review_reports,
            }
            review_history = list(metadata.get("superseded_body_review_history") or [])
            review_history.append(
                {
                    "draft_id": str(previous_current.id),
                    "body_fingerprint": previous_fingerprint,
                    "review_status": metadata.get("current_body_review_status"),
                    "snapshot_metadata_key": "historical_review_snapshot",
                }
            )
            metadata["superseded_body_review_history"] = review_history[-20:]
        previous_current.promotion_metadata = {
            **(previous_current.promotion_metadata or {}),
            "superseded_by_version_id": str(candidate.id),
            "superseded_by_version_no": int(candidate.version_no),
            "current_review_status": "historical_unverified",
            "superseded_body_fingerprint": previous_fingerprint,
            **(
                {"historical_review_snapshot": historical_review_snapshot}
                if review_reports or metadata.get("current_body_review_status") is not None
                else {}
            ),
        }
        if (previous_current.promotion_metadata or {}).get("origin") == "ai_flavor_deslop":
            previous_current.promotion_metadata = {
                **previous_current.promotion_metadata,
                "candidate_status": "superseded_current",
                "unselected_reason": f"superseded:{selected_reason}",
            }

    for key in _DESLOP_REVIEW_METADATA_KEYS:
        metadata.pop(key, None)
    metadata["current_body_review_status"] = "pending"
    metadata["current_body_draft_id"] = str(candidate.id)
    metadata["current_body_fingerprint"] = chapter_review_body_fingerprint(candidate.content_md)
    chapter.metadata_json = metadata
    chapter.current_word_count = authoritative_word_count_for_language(
        candidate.content_md,
        language=language,
    )
    candidate.word_count = chapter.current_word_count
    await session.flush()
    return candidate


async def _persist_deslop_candidate(
    session: AsyncSession,
    *,
    chapter: ChapterModel,
    parent_draft: ChapterDraftVersionModel,
    content: str,
    llm_run_id: UUID | None,
    round_no: int,
    language: str = "zh-CN",
    transformation: str = "deslop_rewrite",
    make_current: bool = False,
    information_preservation: dict | None = None,
    length_request: Mapping[str, Any] | None = None,
    settings: AppSettings | None = None,
) -> ChapterDraftVersionModel:
    """Persist a deslop result as a traceable candidate, without promoting it."""
    if length_request is None and "book_length_control" in (getattr(parent_draft, "promotion_metadata", None) or {}):
        raise full_flow_length.LengthControlError("LENGTH_CONTROL_REQUEST_REQUIRED")
    length_preview = (full_flow_length.evaluate_candidate(length_request, content)
                      if length_request is not None else None)
    next_version = int(
        await session.scalar(
            select(func.coalesce(func.max(ChapterDraftVersionModel.version_no), 0)).where(
                ChapterDraftVersionModel.chapter_id == chapter.id
            )
        )
        or 0
    ) + 1
    candidate = ChapterDraftVersionModel(
        project_id=parent_draft.project_id,
        chapter_id=chapter.id,
        version_no=next_version,
        content_md=content,
        word_count=authoritative_word_count_for_language(content, language=language),
        assembled_from_scene_draft_ids=list(parent_draft.assembled_from_scene_draft_ids or []),
        is_current=False,
        promotion_state=DraftPromotionState.CANDIDATE.value,
        promotion_reason_codes=draft_supersession_codes(
            origin=f"ai_flavor_{transformation}",
            took_current=False,
            supersedes_version=int(parent_draft.version_no),
            hold_reason="deslop_candidate_not_selected",
        ),
        promotion_metadata={
            **({"book_length_control": deepcopy(length_request),
                "book_length_control_candidate": length_preview} if length_request is not None else {}),
            "origin": "ai_flavor_deslop",
            "parent_draft_id": str(parent_draft.id),
            "parent_version_no": int(parent_draft.version_no),
            "deslop_round": round_no,
            "auto_promoted": False,
            "candidate_status": "unselected",
            "unselected_reason": "awaiting_existing_deslop_quality_selection",
            "source_body_fingerprint": chapter_review_body_fingerprint(parent_draft.content_md),
            "transformation": transformation,
            **({"information_preservation": deepcopy(information_preservation)}
               if information_preservation is not None else {}),
        },
        llm_run_id=llm_run_id,
    )
    session.add(candidate)
    await session.flush()
    if make_current and (length_preview is None or length_preview["length_passed"]):
        return await _select_deslop_candidate(
            session,
            chapter=chapter,
            candidate=candidate,
            language=language,
            selected_reason=f"persisted_{transformation}_selected",
            settings=settings,
        )
    return candidate


async def _get_current_world_spec_artifact(
    session: AsyncSession, *, project: ProjectModel, settings: AppSettings | None = None,
) -> PlanningArtifactVersionModel | None:
    """Load the latest WORLD_SPEC and enforce snapshot lineage before use."""

    artifact = await get_latest_approved_planning_artifact(
        session,
        project_id=project.id,
        artifact_type=ArtifactType.WORLD_SPEC,
        **({"settings": settings} if settings is not None else {}),
    )
    return cast(
        PlanningArtifactVersionModel | None,
        assert_current_world_snapshot(project, artifact),
    )


async def _world_snapshot_requires_foundation_repair(
    session: AsyncSession, *, project: ProjectModel, settings: AppSettings | None = None,
) -> bool:
    """Detect a stale or unfinished foundation during explicit structural repair."""

    artifact = await get_latest_approved_planning_artifact(
        session, project_id=project.id, artifact_type=ArtifactType.WORLD_SPEC,
        **({"settings": settings} if settings is not None else {}),
    )
    if artifact is None:
        return False
    try:
        assert_current_world_snapshot(project, artifact)
    except ValueError as exc:
        if str(exc) != "WORLD_SPEC_SNAPSHOT_MISMATCH":
            raise
        from bestseller.services.book_design import ensure_project_book_design_snapshot

        # Validate the locked source, never manufacture lineage for old output.
        ensure_project_book_design_snapshot(project)
        draft = await session.scalar(
            select(ChapterDraftVersionModel.id)
            .where(ChapterDraftVersionModel.project_id == project.id)
            .limit(1)
        )
        if draft is not None:
            raise
        return True
    # Older interrupted rebuilds can have a new matching WorldSpec but leave
    # the prior run's successful readiness marker. The latest failed run takes
    # precedence over that stale success when there is no prose to preserve.
    latest_run = await session.scalar(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.project_id == project.id,
            WorkflowRunModel.workflow_type == "generate_foundation_plan",
        )
        .order_by(WorkflowRunModel.created_at.desc())
        .limit(1)
    )
    if getattr(latest_run, "status", None) == "failed":
        # The approved getter above revalidates the exact pinned cohort,
        # including physical source/admission evidence. A later completed
        # selection may consume a failed generation without rewriting it.
        # Never let that superseded failure force a new paid foundation.
        pin = (project.metadata_json or {}).get("active_foundation_selection")
        if (isinstance(pin, Mapping)
            and pin.get("workflow_id") == str(artifact.source_run_id)
            and (pin.get("artifact_ids") or {}).get("world_spec") == str(artifact.id)):
            selected_run = await session.get(WorkflowRunModel, artifact.source_run_id)
            if (selected_run is not None and selected_run.project_id == project.id
                and selected_run.scope_type == "project" and selected_run.scope_id == project.id
                and selected_run.status == WorkflowStatus.COMPLETED.value
                and selected_run.workflow_type == "complete_foundation_revision"
                and selected_run.id != latest_run.id
                and selected_run.created_at is not None and latest_run.created_at is not None
                and selected_run.created_at > latest_run.created_at
                and selected_run.created_at > (latest_run.updated_at or latest_run.created_at)):
                return False
        draft = await session.scalar(
            select(ChapterDraftVersionModel.id)
            .where(ChapterDraftVersionModel.project_id == project.id)
            .limit(1)
        )
        if draft is not None:
            raise ValueError("FOUNDATION_REPAIR_AFTER_PROSE_FORBIDDEN")
        return True
    return False


@dataclass(slots=True)
class FinalQualityGateResult:
    """Fail-closed result for the exact bytes that are about to be exported."""

    passed: bool
    issues: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    patched_text: str | None = None


def run_final_quality_gates(
    *,
    chapter_number: int,
    content_md: str,
    project,
    settings=None,
    chapter_metadata: dict | None = None,
) -> FinalQualityGateResult:
    """Run the single publication gate after the last text mutation.

    This helper intentionally treats evaluator failures as hard failures.  The
    caller may still retain the chapter as a draft, but formal publication must
    never proceed on an unverified result.

    ``chapter_metadata`` optionally carries a prior ``reader_judge`` blob so
    export can fail-closed on ai_taste/human_voice without a second LLM call.
    """

    issues: list[str] = []
    errors: list[str] = []
    patched_text: str | None = None
    if not content_md:
        return FinalQualityGateResult(False, issues=["empty_content"])
    try:
        cfg = get_quality_gates_config()
        if cfg.ai_flavor.enabled:
            from bestseller.services.ai_flavor_gate import run_ai_flavor_gate

            output_dir = None
            if settings is not None:
                output_dir = (Path(settings.output.base_dir) / project.slug).resolve()
            outcome = run_ai_flavor_gate(
                chapter_number=chapter_number,
                content_md=content_md,
                language=getattr(project, "language", None) or "zh-CN",
                config=cfg.ai_flavor,
                project_output_dir=output_dir,
            )
            if outcome.patched_text is not None:
                patched_text = outcome.patched_text
            if outcome.decision == "block":
                issues.append(f"ai_flavor:{getattr(outcome, 'after_score', 0):.2f}")

        prose_cfg = cfg.prose_quality
        text = patched_text or content_md
        if prose_cfg.anti_meta_enabled or prose_cfg.in_scene_ending_enabled:
            from bestseller.services.anti_meta_gate import check_anti_meta_gate, effective_anti_meta_blockers

            report = check_anti_meta_gate(text, chapter_position=chapter_number)
            issues.extend(
                f"anti_meta:{issue.id}"
                for issue in effective_anti_meta_blockers(report, prose_cfg)[:6]
            )
        if prose_cfg.show_dont_tell_enabled:
            from bestseller.services.show_dont_tell_gate import check_show_dont_tell_gate

            report = check_show_dont_tell_gate(text, chapter_position=chapter_number)
            if (
                not report.passed
                and getattr(prose_cfg, "show_dont_tell_severity", "warn") == "block"
            ):
                issues.extend(f"show_dont_tell:{finding.code}" for finding in report.findings[:6])
        if chapter_number <= 3:
            from bestseller.services.opening_golden_chapter_gate import (
                check_opening_golden_chapter_gate,
            )

            report = check_opening_golden_chapter_gate(
                text,
                chapter_position=chapter_number,
                protagonist_name=_fanqie_gate_protagonist_name(project),
            )
            # Golden-chapter findings remain advisory; retain them for callers
            # that want diagnostics without weakening the formal gate.
            golden_issues = report.to_checker_report().issues
            if golden_issues:
                issues.extend(f"golden:{finding.id}" for finding in golden_issues[:3])

        # Phase C / B1: fail-closed on stored reader-judge voice axes when
        # explicitly enforced. Missing dims only hard-fail under enforce.
        rq = getattr(cfg, "reader_quality", None)
        if rq is not None and getattr(rq, "enforce_reader_judge_voice_axes", False):
            from bestseller.services.reader_judge import (
                extract_reader_judge_dimensions,
                voice_axis_failures,
            )

            meta = chapter_metadata if isinstance(chapter_metadata, dict) else {}
            voice_fails = voice_axis_failures(
                extract_reader_judge_dimensions(meta),
                min_ai_taste=float(getattr(rq, "min_ai_taste", 0.55)),
                min_human_voice=float(getattr(rq, "min_human_voice", 0.55)),
                enforce=True,
            )
            # Plateau stop (C3): after N voice rewrites, closure records debt
            # and does not keep hard-blocking the same chapter forever.
            if voice_fails and meta.get("reader_judge_voice_debt"):
                issues.extend(f"voice_debt:{item}" for item in voice_fails)
            else:
                issues.extend(voice_fails)
    except Exception as exc:
        errors.append(f"evaluator_error:{type(exc).__name__}:{exc}")

    hard_issues = [
        item
        for item in issues
        if not item.startswith("golden:") and not item.startswith("voice_debt:")
    ]
    return FinalQualityGateResult(
        passed=not errors and not hard_issues,
        issues=issues,
        errors=errors,
        patched_text=patched_text,
    )


def _retention_chapter_length_kwargs(project: ProjectModel) -> dict[str, int]:
    """Return retention-gate thresholds without bypassing the zh hard wall."""

    target = int(
        getattr(project, "default_target_chapter_words", 0)
        or getattr(project, "target_chapter_words", 0)
        or 0
    )
    if target <= 0:
        return {}
    if is_english_language(getattr(project, "language", None)):
        return {
            "chapter_length_hard_floor": max(1500, int(target * 0.7)),
            "chapter_length_soft_warning": max(2000, int(target * 0.85)),
            "chapter_length_hard_max": max(3000, int(target * 1.2)),
        }

    hard_floor = min(
        DEFAULT_SOFT_WARNING_ZH_CHARS,
        max(DEFAULT_HARD_FLOOR_ZH_CHARS, int(target * 0.7)),
    )
    soft_warning = min(
        DEFAULT_HARD_MAX_ZH_CHARS,
        max(hard_floor, DEFAULT_SOFT_WARNING_ZH_CHARS, int(target * 0.85)),
    )
    return {
        "chapter_length_hard_floor": hard_floor,
        "chapter_length_soft_warning": soft_warning,
        "chapter_length_hard_max": DEFAULT_HARD_MAX_ZH_CHARS,
    }


def _existing_chapter_draft_needs_length_recheck(
    chapter_draft: ChapterDraftVersionModel | None,
    *,
    language: str,
    hard_min: int,
    hard_max: int,
) -> tuple[bool, int]:
    """Check resume eligibility from body truth, never stale stored counts."""

    if chapter_draft is None:
        return False, 0
    actual = authoritative_word_count_for_language(
        chapter_draft.content_md or "",
        language=language,
    )
    return actual < hard_min or actual > hard_max, actual


def _bundle_hook_domain_tokens(project) -> tuple[str, ...]:
    """Book-derived hook vocabulary for the quality bundle's hook-echo check.

    Same source as the production-side injection (imagery anchors) so the
    duty block and validation always extract the same token set. Fails to
    () — the generic extraction layers carry the gate without it.
    """

    try:
        from bestseller.services.imagery_system_design import (
            imagery_anchor_phrases,
        )

        return imagery_anchor_phrases(project)
    except Exception:
        return ()


WORKFLOW_TYPE_SCENE_PIPELINE = "scene_pipeline"


async def _load_prev_chapter_draft_text(
    session: AsyncSession,
    project: ProjectModel,
    chapter_number: int,
) -> str | None:
    if chapter_number < 2:
        return None
    result = await session.execute(
        select(ChapterDraftVersionModel.content_md)
        .join(ChapterModel, ChapterDraftVersionModel.chapter_id == ChapterModel.id)
        .where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number - 1,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .limit(1)
    )
    if result is None:
        return None
    if hasattr(result, "scalar_one_or_none"):
        return result.scalar_one_or_none()
    if hasattr(result, "scalar"):
        return result.scalar()
    return None


class PersonaGateMerge(NamedTuple):
    """Result of merging persona findings into the retention report.

    ``corroborated_persona_codes`` / ``corroborating_base_codes`` exist so the
    receipt names **which** independent failure corroborated **which** persona
    item. Recording the whole base-code list instead made an unrelated failure
    look like evidence for every synthetic preference (2026-09-11, W5 审查 P1).
    """

    findings: list
    repair_codes: list
    authority: str
    corroborated_persona_codes: tuple[str, ...] = ()
    corroborating_base_codes: tuple[str, ...] = ()


def _merge_persona_gate_findings(
    *,
    base_findings: Any,
    base_repair_codes: Any,
    persona_findings: Any,
    persona_repair_codes: Any,
    require_corroboration: bool,
) -> PersonaGateMerge:
    """把合成 reader persona 的失败并入 retention 报告，但**不无条件授权**。

    2026-09-10 (W4-2)。此前 persona 失败的三条 finding 以 ``severity="critical"``
    原样并入 retention 报告，其 ``auto_repair_codes`` 被直接追加——于是**一个确定性
    计数公式**（hook/payoff/emotion 过 sigmoid）可以**独立**把章节判为不合格并路由到
    修复/重写。计划 §3 W4-2：纯合成偏好不应自动拥有换故事或整章改写权。

    2026-09-11 (W5 审查 P1) 修正逐项判定。W4-2 的判据是
    ``corroborated = bool(base_repair_codes)``，等于把**共现**当**互证**：任意一个
    独立失败都会让**全部** persona 失败升级并追加**全部** persona 修复码。
    "开头钩子缺失"证明不了"爽点密度不足"，更无权替一整套模拟偏好背书。

    现在的规则：

      * ``require_corroboration is False`` → 显式回滚开关，恢复旧行为，但其
        authority 记为 ``"override_unrestricted"``，**不得**冒充 ``"corroborated"``；
      * 否则**逐项**判定：某个 persona 项只有在
        :data:`PERSONA_CORROBORATION_LINKS` 为它登记了关系、且其中至少一个独立
        修复码确实存在时才算互证；此时**只有该项**升为 ``critical`` 并追加
        **它自己**的修复码；
      * 未互证的项 → ``severity="advisory"`` 记入 findings、**不追加修复码**，
        不影响 ``RetentionGateReport.passed`` / ``has_critical``。

    **独立（base）失败永远原样保留**，不受本函数影响：关掉合成偏好不是关掉事实检查。

    本函数是**纯函数**：不改入参、不读配置、无副作用。

    ``persona_repair_codes`` 保留在签名里仅为调用方兼容：自 2026-09-11 起它
    **不再**作为整份清单被授予，修复义务按 ``findings`` 逐项判定。
    """

    from bestseller.services.persona_quality_gate import corroborating_base_codes
    from bestseller.services.retention_safety_gate import RetentionGateFinding

    merged_findings = list(base_findings)
    merged_codes = list(base_repair_codes)
    # 显式回滚开关与"确实互证"是两回事，不能共用同一个 authority 名字。
    override_unrestricted = require_corroboration is False

    corroborated_persona: list[str] = []
    corroborating_codes: set[str] = set()

    for item in persona_findings:
        if override_unrestricted:
            item_corroborated = True
            item_codes: frozenset[str] = frozenset()
        else:
            item_codes = corroborating_base_codes(item.code, merged_codes)
            item_corroborated = bool(item_codes)
        corroborating_codes |= item_codes
        severity = "critical" if item_corroborated else "advisory"
        merged_findings.append(
            RetentionGateFinding(code=item.code, severity=severity, detail=item.detail)
        )
        if item_corroborated:
            corroborated_persona.append(item.code)
            # 只追加**这一项**自己的修复码，而不是整份 persona 清单。
            if item.code not in merged_codes:
                merged_codes.append(item.code)

    if override_unrestricted:
        authority = "override_unrestricted"
    elif corroborated_persona:
        authority = "corroborated"
    else:
        authority = "advisory"

    return PersonaGateMerge(
        findings=merged_findings,
        repair_codes=merged_codes,
        authority=authority,
        corroborated_persona_codes=tuple(corroborated_persona),
        corroborating_base_codes=tuple(sorted(corroborating_codes)),
    )


async def _evaluate_retention_safety_after_assembly(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_draft: ChapterDraftVersionModel,
    chapter_number: int,
    output_base_dir: str | Path | None = None,
) -> bool:
    from bestseller.services.canon_guardrails import load_canon_guardrails_for_project
    from bestseller.services.character_role_gate import load_character_profiles
    from bestseller.services.retention_safety_gate import (
        evaluate_retention_safety,
        stamp_retention_block_codes,
    )
    from bestseller.services.timeline_consistency_gate import load_timeline_canon

    prev_text = await _load_prev_chapter_draft_text(
        session,
        project,
        chapter_number,
    )

    # ──────────────────────────────────────────────────────────────────
    # CRITICAL BUG FIX (2026-05-23):
    # TimelineConsistencyGate and CharacterRoleGate were being silently
    # skipped in production because this function did not load nor pass
    # the required ``timeline_canon`` and ``character_profiles`` args.
    # The retention gate's internal guards (line 283 / 359) treat those
    # as opt-in features: when None/empty, the check is skipped.
    # That's how ch1 shipped with 5 critical timeline violations.
    # ──────────────────────────────────────────────────────────────────
    bible_root = _project_story_bible_root(project, output_base_dir)
    timeline_canon = None
    character_profiles: tuple = ()
    if bible_root is not None:
        try:
            timeline_canon = load_timeline_canon(bible_root / "timeline-canon.md")
        except Exception:
            logger.debug(
                "timeline-canon load failed for ch%d (non-fatal)",
                chapter_number,
                exc_info=True,
            )
        try:
            character_profiles = load_character_profiles(bible_root / "cast-and-promises.md")
        except Exception:
            logger.debug(
                "cast-and-promises load failed for ch%d (non-fatal)",
                chapter_number,
                exc_info=True,
            )

    # Derive chapter-length thresholds. Use project's target_chapter_words
    # if defined; else fall back to the gate defaults.
    _length_kwargs: dict[str, int | bool] = _retention_chapter_length_kwargs(project)
    # Honor the per-gate disable flag (default True; tests can opt out).
    try:
        from bestseller.services.quality_gates_config import (
            get_quality_gates_config,
        )

        _length_enabled = bool(
            getattr(
                get_quality_gates_config().originality_engine,
                "chapter_length_gate_enabled",
                True,
            )
        )
    except Exception:
        _length_enabled = True
    if not _length_enabled:
        _length_kwargs["skip_chapter_length"] = True

    from bestseller.services.chapter_word_count_truth import (
        authoritative_zh_word_count,
    )
    from bestseller.services.quality_gates_config import get_quality_gates_config

    _rq_cfg = get_quality_gates_config().reader_quality
    _language = str(getattr(project, "language", None) or "zh-CN")
    _body = chapter_draft.content_md or ""
    _actual_wc = authoritative_zh_word_count(_body, language=_language)
    chapter.current_word_count = _actual_wc
    chapter_draft.word_count = _actual_wc

    # Same book-derived hook vocabulary as the production side
    # (prepare_chapter_context) so duty-block tokens and validation tokens
    # never diverge.
    try:
        from bestseller.services.imagery_system_design import (
            imagery_anchor_phrases as _imagery_anchor_phrases,
        )

        _hook_domain_tokens: tuple[str, ...] = _imagery_anchor_phrases(project)
    except Exception:
        _hook_domain_tokens = ()

    report = evaluate_retention_safety(
        chapter_position=chapter_number,
        chapter_text=_body,
        prev_chapter_text=prev_text,
        prev_chapter_position=chapter_number - 1 if chapter_number > 1 else None,
        hook_domain_tokens=_hook_domain_tokens,
        total_chapters=int(getattr(project, "target_chapters", 0) or 500),
        guardrails=load_canon_guardrails_for_project(
            project,
            output_base_dir=output_base_dir,
        ),
        timeline_canon=timeline_canon,
        character_profiles=character_profiles or None,
        skip_signature=not _signature_plan_file_exists(
            project,
            output_base_dir=output_base_dir,
        ),
        block_below_target=_rq_cfg.block_below_target_length,
        payoff_block=_rq_cfg.block_payoff_ledger,
        skip_word_count_truth=not _rq_cfg.block_word_count_metadata_mismatch,
        skip_duplicate_check=not _rq_cfg.block_chapter_duplicates,
        skip_payoff_ledger=False,
        opening_similarity_threshold=_rq_cfg.opening_similarity_threshold,
        body_similarity_threshold=_rq_cfg.body_similarity_threshold,
        min_payoff_density=_rq_cfg.min_payoff_density,
        **_length_kwargs,
    )

    # Initialised before the guard so the metadata write below is safe whether
    # the persona gate ran, was disabled, or raised. A NameError here would take
    # down the whole chapter pipeline over a diagnostics field.
    _persona_evidence: dict[str, Any] | None = None
    if _rq_cfg.enabled and _rq_cfg.block_on_persona_failure and output_base_dir:
        try:
            from bestseller.domain.reader_persona import PersonaSimulationResult
            from bestseller.services.persona_feedback_repository import (
                resolve_persona_feedback_path,
            )
            from bestseller.services.persona_quality_gate import (
                evaluate_persona_quality,
            )
            from bestseller.services.retention_safety_gate import (
                RetentionGateReport,
            )

            _mode_b = bool((getattr(project, "metadata_json", None) or {}).get("mode_b"))
            _feedback_path = resolve_persona_feedback_path(
                project.slug,
                chapter_number,
                output_base_dir=output_base_dir,
                mode_b=_mode_b,
            )
            if _feedback_path.is_file():
                _payload = json.loads(_feedback_path.read_text(encoding="utf-8"))
                _persona_result = PersonaSimulationResult.model_validate(_payload)
                _target_chapters = int(getattr(project, "target_chapters", 0) or 0)
                _target_words = int(getattr(chapter, "target_word_count", 0) or 0)
                _block_on_payoff = not (
                    _target_chapters
                    and _target_chapters <= 12
                    and chapter_number == 1
                    and 0 < _target_words <= 2500
                )
                _pq = evaluate_persona_quality(
                    _persona_result,
                    min_weighted_score=_rq_cfg.min_weighted_score,
                    max_abandon_rate=_rq_cfg.max_abandon_rate,
                    min_payoff_density=_rq_cfg.min_payoff_density,
                    block_on_payoff=_block_on_payoff,
                )
                # Persist what the personas actually said, not only the score
                # they produced. A verdict of "weighted_score=0.53 < 0.62" is
                # unactionable on its own: it cannot distinguish prose that is
                # genuinely weak from a ruler that does not fit this book, and
                # the simulation already computed the concerns and the at-risk
                # personas before discarding them (2026-08-06 — five chapters
                # scored 0.49–0.61 with no recorded reason anywhere).
                _persona_evidence = {
                    "weighted_score": _persona_result.weighted_score,
                    "abandon_rate": _persona_result.abandon_rate,
                    "high_risk_personas": list(_persona_result.high_risk_personas),
                    "concerns": list(_persona_result.aggregated_concerns)[:8],
                    "next_chapter_directives": list(_persona_result.next_chapter_directives)[:5],
                    "thresholds": {
                        "min_weighted_score": _rq_cfg.min_weighted_score,
                        "max_abandon_rate": _rq_cfg.max_abandon_rate,
                        "min_payoff_density": _rq_cfg.min_payoff_density,
                    },
                    # 2026-09-10 (W4-2)：这一章 persona 失败的**实际权限**。
                    # none=没失败；advisory=失败但只留痕（合成偏好不得独立阻断）；
                    # corroborated=**有登记的证据关系**的那几项参与阻断；
                    # override_unrestricted=互证要求被显式关闭（回滚开关，非互证）。
                    "authority": "none",
                    # 2026-09-10 (W4-3)：哪些通道由默认常数代替、**没有实测**。
                    # 让"测出来的低分"与"默认值带来的分"可分辨。
                    "unmeasured_channels": list(
                        getattr(_persona_result, "unmeasured_channels", []) or []
                    ),
                }
                if not _pq.passed:
                    # 2026-09-10 (W4-2)：合成 persona 失败**不独立授权**阻断/重写。
                    # 见 _merge_persona_gate_findings 的契约。
                    _base_repair_codes = list(report.auto_repair_codes)
                    _merge = _merge_persona_gate_findings(
                        base_findings=report.findings,
                        base_repair_codes=_base_repair_codes,
                        persona_findings=_pq.findings,
                        persona_repair_codes=_pq.auto_repair_codes,
                        require_corroboration=bool(
                            getattr(
                                _rq_cfg,
                                "persona_block_requires_corroboration",
                                True,
                            )
                        ),
                    )
                    report = RetentionGateReport(
                        chapter_position=chapter_number,
                        findings=tuple(_merge.findings),
                        auto_repair_codes=tuple(_merge.repair_codes),
                    )
                    _persona_authority = _merge.authority
                    _persona_evidence["authority"] = _persona_authority
                    _persona_evidence["persona_codes"] = list(_pq.auto_repair_codes)
                    _persona_evidence["would_have_blocked"] = True
                    # 2026-09-11 (W5 审查 P1)：回执必须写明**哪一条**独立失败互证了
                    # **哪一条** persona 失败。此前写整份 base 清单，等于让一个无关的
                    # 真实失败看起来像全部合成偏好的证据。
                    _persona_evidence["corroborated_persona_codes"] = list(
                        _merge.corroborated_persona_codes
                    )
                    _persona_evidence["corroborating_base_codes"] = list(
                        _merge.corroborating_base_codes
                    )
                    if _persona_authority == "advisory":
                        _persona_evidence["advisory_reason"] = (
                            "synthetic_persona_alone_cannot_block"
                        )
                    elif _persona_authority == "override_unrestricted":
                        _persona_evidence["override_note"] = (
                            "persona_block_requires_corroboration disabled: legacy "
                            "grant restored, NOT corroborated"
                        )
                    else:
                        _persona_evidence["corroborating_codes"] = list(
                            _merge.corroborating_base_codes
                        )
        except Exception:
            logger.debug(
                "persona quality gate failed for ch%d (non-fatal)",
                chapter_number,
                exc_info=True,
            )
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    if _persona_evidence is not None:
        metadata["persona_gate_evidence"] = _persona_evidence
    metadata["retention_gate_last_findings"] = [
        {
            "code": finding.code,
            "severity": finding.severity,
            "detail": finding.detail,
            **({"coverage": finding.coverage} if finding.coverage is not None else {}),
            **(
                {"exposition_ratio": finding.exposition_ratio}
                if finding.exposition_ratio is not None
                else {}
            ),
            **({"evidence": finding.evidence} if finding.evidence else {}),
        }
        for finding in report.findings
    ]
    metadata["retention_gate_passed"] = report.passed
    chapter.metadata_json = metadata
    blocked = stamp_retention_block_codes(chapter, report)
    await session.flush()
    logger.info(
        "retention_safety_gate evaluated: ch%d passed=%s codes=%s findings=%d",
        chapter_number,
        report.passed,
        list(report.auto_repair_codes),
        len(report.findings),
    )
    return blocked


async def _maybe_apply_deterministic_length_trim_before_export(
    session: AsyncSession,
    *,
    settings: Any,
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_draft: ChapterDraftVersionModel,
    chapter_number: int,
) -> bool:
    """Trim an over-max chapter only when length is its sole quality block."""

    text = chapter_draft.content_md or ""
    if not text:
        return False

    from bestseller.services.chapter_length_gate import trim_chapter_to_hard_max
    from bestseller.services.chapter_quality_bundle import (
        ChapterQualityBundleContext,
        run_chapter_quality_bundle,
    )

    target_words = int(
        getattr(chapter, "target_word_count", 0)
        or getattr(project, "default_target_chapter_words", 0)
        or getattr(project, "target_chapter_words", 0)
        or getattr(settings.generation.words_per_chapter, "target", 0)
        or 0
    )
    previous_text = await _load_prev_chapter_draft_text(session, project, chapter_number)
    _prior_chapter_texts: tuple[tuple[int, str], ...] = ()
    try:
        from bestseller.services.drafts import _collect_previous_current_chapter_texts

        _prior_chapter_texts = await _collect_previous_current_chapter_texts(
            session, project=project, chapter_number=chapter_number
        )
    except Exception:
        logger.debug(
            "chapter %s: prior chapter texts for cross-chapter gate unavailable",
            chapter_number,
            exc_info=True,
        )
        _prior_chapter_texts = (
            ((chapter_number - 1, previous_text),) if previous_text and chapter_number > 1 else ()
        )
    context = ChapterQualityBundleContext(
        chapter_number=chapter_number,
        previous_chapter_text=previous_text,
        previous_chapter_position=chapter_number - 1 if previous_text else None,
        # 全部前序章，不是只比紧邻的上一章（2026-08-24）：reviews/drafts 两条
        # 路径用的是 _collect_previous_current_chapter_texts（取全量在架稿），
        # 只有这条塞一个上一章。而这条恰好是**短书唯一走到的那条**——
        # reviews 的 bundle 挂在 target_chapters >= 50 后面，12 章的书整块跳过。
        # 同一个检查两套输入口径，弱的那套服务的是覆盖最薄的书。
        previous_chapter_texts=_prior_chapter_texts,
        total_chapters=int(getattr(project, "target_chapters", 0) or 500),
        language=str(getattr(project, "language", None) or "zh-CN"),
        target_chapter_words=target_words or None,
        commercial_strict=True,
        hook_domain_tokens=_bundle_hook_domain_tokens(project),
    )
    report = run_chapter_quality_bundle(text, context)
    codes = tuple(finding.code for finding in report.blocking_findings)
    if codes != ("CHAPTER_LENGTH_BLOCK_HIGH",):
        return False

    fallback_hard_max = max(3000, int(target_words * 1.2)) if target_words else 3000
    hard_max = int(report.blocking_findings[0].evidence.get("hard_max") or fallback_hard_max)
    if not is_english_language(getattr(project, "language", None)):
        hard_max = min(hard_max, DEFAULT_HARD_MAX_ZH_CHARS)
    trimmed_text, trimmed = trim_chapter_to_hard_max(text, hard_max)
    if not trimmed:
        return False
    post_report = run_chapter_quality_bundle(trimmed_text, context)
    if post_report.blocking_findings:
        return False

    original_word_count = int(getattr(chapter_draft, "word_count", 0) or 0)
    from bestseller.services.chapter_word_count_truth import (
        authoritative_zh_word_count,
    )

    trimmed_word_count = authoritative_zh_word_count(
        trimmed_text,
        language=str(getattr(project, "language", None) or "zh-CN"),
    )
    chapter_draft.content_md = trimmed_text
    chapter_draft.word_count = trimmed_word_count
    chapter.current_word_count = trimmed_word_count
    chapter.status = ChapterStatus.REVISION.value
    chapter.production_state = "ok"

    metadata = dict(chapter.metadata_json or {})
    for key in (
        "quality_bundle_blocking_codes",
        "quality_gate_block_codes",
        "production_block_code",
        "quality_gate_block_code",
        "quality_gate_block_source",
        "quality_gate_block_hint",
        "blocked_by_write_safety_gate",
        "write_safety_block_code",
        "write_safety_hint",
        "export_blocked_reason",
        "export_blocked_by_run_id",
        "auto_repair_exhausted",
        "retention_auto_repair_exhausted",
        "chapter_review_attempts_active",
    ):
        metadata.pop(key, None)
    metadata["quality_bundle"] = post_report.to_dict()
    metadata["deterministic_length_trim"] = {
        "from_block_code": "CHAPTER_LENGTH_BLOCK_HIGH",
        "hard_max": hard_max,
        "original_word_count": original_word_count,
        "trimmed_word_count": trimmed_word_count,
    }
    chapter.metadata_json = metadata
    await session.flush()
    return True


def _insert_paragraph_after_chapter_heading(text: str, paragraph: str) -> str:
    if not text.strip() or not paragraph.strip():
        return text
    blocks = text.split("\n\n")
    if blocks and blocks[0].lstrip().startswith("#"):
        return "\n\n".join([blocks[0], paragraph.strip(), *blocks[1:]]).rstrip()
    return (paragraph.strip() + "\n\n" + text.lstrip()).rstrip()


def _render_deterministic_hook_echo_bridge(tokens: list[str]) -> str:
    # Compatibility surface only: detectors must never manufacture story prose.
    return ""


async def _maybe_apply_deterministic_hook_echo_bridge_before_review(
    session: AsyncSession,
    *,
    settings: Any,
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_draft: ChapterDraftVersionModel,
    chapter_number: int,
) -> bool:
    """Detection is read-only; missing continuity is repaired through versioned candidates."""
    return False





def _signature_plan_file_exists(
    project: ProjectModel,
    *,
    output_base_dir: str | Path | None,
) -> bool:
    if output_base_dir is None:
        return True
    bible_root = _project_story_bible_root(project, output_base_dir)
    return bool(bible_root and (bible_root / "signature-scene-plan.json").exists())


def _project_story_bible_root(
    project: ProjectModel,
    output_base_dir: str | Path | None,
) -> Path | None:
    """Return the story-bible root for classic and Mode B output layouts."""

    if output_base_dir is None:
        return None

    base = Path(output_base_dir)
    is_mode_b = bool((getattr(project, "metadata_json", None) or {}).get("mode_b"))
    classic_root = base / project.slug / "story-bible"
    mode_b_root = base / "ai-generated" / project.slug / "story-bible"
    candidates = (mode_b_root, classic_root) if is_mode_b else (classic_root, mode_b_root)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _current_auto_repair_block_codes(chapter: ChapterModel) -> tuple[str, ...]:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    raw_codes = (
        metadata.get("auto_repair_last_block_codes")
        or metadata.get("quality_gate_block_codes")
        or (
            [metadata.get("production_block_code")] if metadata.get("production_block_code") else []
        )
    )
    return _filter_inactive_audit_cache_codes(tuple(
        dict.fromkeys(
            (
                *(str(code) for code in raw_codes if code),
                *_active_quality_block_codes_from_metadata(metadata),
            )
        )
    ), metadata)


_CHAPTER_FIRST_FULL_REGEN_STRUCTURAL_CODES: frozenset[str] = frozenset(
    {
        "OPENING_SCENE_DRIFT",
        "FRONT10_FORBIDDEN_SIGNAL",
        "FRONT10_SCENE_FORBIDDEN_ACTION",
        "FRONT10_RULE_LECTURE_DENSITY",
        "REPEATED_EVENT_BEAT",
    }
)

_CHAPTER_FIRST_FULL_REGEN_IMMEDIATE_FRONT10_CODES: frozenset[str] = frozenset(
    {
        "OPENING_SCENE_DRIFT",
        "FRONT10_FORBIDDEN_SIGNAL",
        "FRONT10_SCENE_FORBIDDEN_ACTION",
    }
)

_CHAPTER_FIRST_FULL_REGEN_LENGTH_LOW_CODES: frozenset[str] = frozenset(
    {
        "BLOCK_LOW",
        "LENGTH_UNDER",
        "CHAPTER_TOO_SHORT",
        "CHAPTER_LENGTH_BLOCK_LOW",
    }
)

_CHAPTER_FIRST_FULL_REGEN_LENGTH_HIGH_CODES: frozenset[str] = frozenset(
    {
        "BLOCK_HIGH",
        "LENGTH_OVER",
        "CHAPTER_TOO_LONG",
        "CHAPTER_LENGTH_BLOCK_HIGH",
    }
)


def _normalize_chapter_first_block_code(code: str) -> str:
    text = str(code or "").strip().upper()
    if text in {"LENGTH_UNDER", "CHAPTER_TOO_SHORT", "CHAPTER_LENGTH_BLOCK_LOW"}:
        return "BLOCK_LOW"
    if text in {"LENGTH_OVER", "CHAPTER_TOO_LONG", "CHAPTER_LENGTH_BLOCK_HIGH"}:
        return "BLOCK_HIGH"
    return text


def _chapter_first_full_regeneration_reason(
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_draft: ChapterDraftVersionModel | None,
    block_codes: tuple[str, ...],
    *,
    attempt_number: int,
) -> str | None:
    """Choose full regeneration when a chapter-first draft is too poor to patch.

    Patch-first is appropriate for localized continuity, tail-hook, timeline,
    or dialogue problems.  It is the wrong tool when the whole draft is too
    short to be a chapter, when the opening/scene contract is polluted, or
    when a prior repair already failed and is now amplifying defects.
    """

    project_metadata = getattr(project, "metadata_json", None)
    project_metadata = project_metadata if isinstance(project_metadata, Mapping) else {}
    chapter_metadata = getattr(chapter, "metadata_json", None)
    chapter_metadata = chapter_metadata if isinstance(chapter_metadata, Mapping) else {}
    full_regen_limit = int(
        project_metadata.get("chapter_first_full_regeneration_max_attempts") or 1
    )
    full_regen_used = int(chapter_metadata.get("chapter_first_full_regeneration_count") or 0)
    if full_regen_limit <= 0 or full_regen_used >= full_regen_limit:
        return None

    normalized_codes = {_normalize_chapter_first_block_code(code) for code in block_codes if code}
    original_codes = {str(code or "").strip().upper() for code in block_codes if code}
    story_engine_hits = {
        code for code in original_codes if code.startswith("STORY_ENGINE_RECEIPT_")
    }
    if story_engine_hits:
        return "story_engine_receipt_block:" + ",".join(sorted(story_engine_hits))
    word_count = 0
    if chapter_draft is not None:
        try:
            word_count = int(getattr(chapter_draft, "word_count", None) or 0)
        except (TypeError, ValueError):
            word_count = 0
        if word_count <= 0:
            word_count = authoritative_word_count_for_language(
                getattr(chapter_draft, "content_md", "") or "",
                language=str(getattr(project, "language", None) or "zh-CN"),
            )
    if word_count <= 0:
        try:
            word_count = int(getattr(chapter, "current_word_count", None) or 0)
        except (TypeError, ValueError):
            word_count = 0
    hard_min, target_words, hard_max = _chapter_length_contract_band(
        project,
        int(getattr(chapter, "target_word_count", 0) or 0),
    )
    if "LENGTH_OUT_OF_BAND" in original_codes:
        if word_count > 0 and word_count < hard_min:
            normalized_codes.add("BLOCK_LOW")
        elif word_count > hard_max:
            normalized_codes.add("BLOCK_HIGH")
    if (
        normalized_codes & _CHAPTER_FIRST_FULL_REGEN_LENGTH_LOW_CODES
        and word_count > 0
        and (word_count < int(hard_min * 0.85) or word_count < int(target_words * 0.75))
    ):
        return (
            f"severe_under_length:word_count={word_count},hard_min={hard_min},target={target_words}"
        )
    if normalized_codes & _CHAPTER_FIRST_FULL_REGEN_LENGTH_HIGH_CODES and word_count > int(
        hard_max * 1.2
    ):
        return f"severe_over_length:word_count={word_count},hard_max={hard_max}"
    structural_hits = original_codes & _CHAPTER_FIRST_FULL_REGEN_STRUCTURAL_CODES
    if int(getattr(chapter, "chapter_number", 0) or 0) <= 10 and structural_hits:
        immediate_hits = structural_hits & _CHAPTER_FIRST_FULL_REGEN_IMMEDIATE_FRONT10_CODES
        if immediate_hits:
            return "front10_hard_contract_polluted:" + ",".join(sorted(immediate_hits))
        if len(structural_hits) >= 2:
            return "multiple_front10_structural_blocks:" + ",".join(sorted(structural_hits))
        if attempt_number >= 2:
            return "repeated_front10_structural_block:" + ",".join(sorted(structural_hits))
    if attempt_number >= 2 and normalized_codes & _CHAPTER_FIRST_FULL_REGEN_LENGTH_LOW_CODES:
        return (
            "repeated_under_length_after_repair:"
            f"word_count={word_count},hard_min={hard_min},target={target_words}"
        )
    return None


def _chapter_review_report_block_codes(report: Any) -> tuple[str, ...]:
    payload = getattr(report, "report_json", None)
    if not isinstance(payload, Mapping):
        return ()
    codes: list[str] = []
    for key in ("blocking_codes", "block_codes"):
        values = payload.get(key)
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            codes.extend(str(value).strip() for value in values if str(value).strip())
    for key in ("violations", "findings", "blocking_issues"):
        values = payload.get(key)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        for item in values:
            if not isinstance(item, Mapping):
                continue
            blocks_write = bool(
                item.get("blocks_write")
                or item.get("blocking")
                or str(item.get("severity") or "").lower() in {"critical", "block", "blocker"}
            )
            code = str(item.get("code") or item.get("issue_code") or "").strip()
            if blocks_write and code:
                codes.append(code)
    return tuple(dict.fromkeys(codes))


def _chapter_review_body_binding_code(
    report: Any,
    draft: ChapterDraftVersionModel,
) -> str | None:
    """Return a promotion block when the review is not bound to this body."""

    payload = getattr(report, "structured_output", None)
    if not isinstance(payload, Mapping):
        return "REVIEW_BODY_UNVERIFIED"
    if str(payload.get("draft_id") or "") != str(draft.id):
        return "REVIEW_BODY_STALE"
    if not isinstance(payload.get("reviewed_body_fingerprint"), Mapping):
        return "REVIEW_BODY_UNVERIFIED"
    expected = chapter_review_body_fingerprint(draft.content_md or "")
    if payload.get("reviewed_body_fingerprint") != expected:
        return "REVIEW_BODY_STALE"
    return None


def _chapter_review_full_regeneration_reason(
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_draft: ChapterDraftVersionModel | None,
    report: Any,
    quality: Any,
    *,
    rewrite_iterations: int,
) -> str | None:
    block_codes = _chapter_review_report_block_codes(report)
    structural_reason = _chapter_first_full_regeneration_reason(
        project,
        chapter,
        chapter_draft,
        block_codes,
        attempt_number=rewrite_iterations + 1,
    )
    if structural_reason:
        return structural_reason

    score_value: float | None = None
    try:
        raw_score = getattr(quality, "score_overall", None)
        if raw_score is not None:
            score_value = float(raw_score)
    except (TypeError, ValueError):
        score_value = None
    if score_value is None:
        return None

    # Prefer targeted chapter rewrite over whole-draft regeneration.
    # Full regen on M3 often degrades (0.88→0.72); only force it for
    # catastrophic scores or after two failed targeted patches.
    if score_value <= 0.50:
        return f"very_low_review_score:{score_value:.2f}"
    if rewrite_iterations >= 2 and score_value < 0.65:
        return f"repeated_low_review_score:{score_value:.2f}"
    return None


# Runtime blocking predicate uses ONLY ``core`` tier block keys.
# ``advanced`` tier gates (ai_flavor, show_dont_tell, signature_audit) are
# prose polish — a single weak-model style regression should never loop
# the chapter through machine_repair_required. They still surface
# through project review reports and overview schemas via the full
# ``registered_block_metadata_keys()`` set.
# ``phase_d_time_gate`` and ``material_advancement_gate`` deliberately
# stay in ``core`` — they enforce timeline arithmetic and story-contract
# delivery, which are correctness concerns, not polish.
_NON_QUALITY_BLOCK_METADATA_KEYS: tuple[str, ...] = core_block_metadata_keys()


def _latest_quality_report_is_clean(report: Any) -> bool:
    if report is None:
        return False
    if bool(getattr(report, "blocks_write", False)):
        return False
    payload = getattr(report, "report_json", None)
    if not isinstance(payload, dict):
        return True
    blocking_codes = payload.get("blocking_codes")
    blocking_code_values = (
        {str(code).strip() for code in blocking_codes if str(code).strip()}
        if isinstance(blocking_codes, (list, tuple, set))
        else set()
    )
    if blocking_code_values:
        return False
    for key in ("violations", "findings", "blocking_issues"):
        values = payload.get(key)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        for item in values:
            if not isinstance(item, Mapping):
                continue
            severity = str(item.get("severity") or "").strip().lower()
            if (
                item.get("blocks_write")
                or item.get("blocking")
                or severity in {"critical", "block", "blocker"}
            ):
                return False
    return True


def _chapter_has_non_quality_block_metadata(chapter: ChapterModel) -> bool:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    return any(bool(metadata.get(key)) for key in _NON_QUALITY_BLOCK_METADATA_KEYS)


_ACTIVE_EXTERNAL_QUALITY_SEVERITIES: frozenset[str] = frozenset(
    {"critical", "high", "block", "blocker"}
)


def _deterministic_audit_block_codes_from_metadata(
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return active deterministic post-write audit findings.

    The deterministic audit writes a structured report to chapter metadata,
    while the rewrite loop historically only consumed quality-report
    ``blocking_codes``.  Keeping this as a view function lets the pipeline
    converge on one repair surface without changing the persisted schema.
    """

    report = metadata.get("deterministic_audit_latest")
    if not isinstance(report, Mapping):
        return ()
    findings = report.get("findings")
    if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
        return ()
    from bestseller.services.deterministic_audit_actions import effective_deterministic_audit_blockers
    codes = [str(finding.get("code") or "").strip()
             for finding in effective_deterministic_audit_blockers(
                 report, settings=get_quality_gates_config(), chapter_number=report.get("chapter_number"),
             ) if isinstance(finding, Mapping) and finding.get("code")]
    return tuple(dict.fromkeys(codes))


def _filter_inactive_audit_cache_codes(codes, metadata):
    from bestseller.services.deterministic_audit_actions import filter_inactive_deterministic_audit_codes
    from bestseller.services.anti_meta_gate import filter_inactive_anti_meta_block_codes
    quality_config = get_quality_gates_config()
    report = metadata.get("deterministic_audit_latest")
    active = filter_inactive_deterministic_audit_codes(
        codes, report if isinstance(report, Mapping) else None,
        settings=quality_config,
        chapter_number=report.get("chapter_number") if isinstance(report, Mapping) else None,
    )
    return filter_inactive_anti_meta_block_codes(active, quality_config.prose_quality)


def _retention_quality_block_codes_from_metadata(
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    if metadata.get("retention_gate_passed") is not False:
        return ()
    findings = metadata.get("retention_gate_last_findings")
    if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
        return ()
    codes: list[str] = []
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        severity = str(finding.get("severity") or "").strip().lower()
        if severity not in _ACTIVE_EXTERNAL_QUALITY_SEVERITIES:
            continue
        code = str(finding.get("code") or "").strip()
        if code:
            codes.append(code)
    return tuple(dict.fromkeys(codes))


def _active_quality_block_codes_from_metadata(
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    raw_codes: list[str] = []
    for key in ("quality_gate_block_codes", "quality_bundle_blocking_codes"):
        values = metadata.get(key)
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            raw_codes.extend(str(code).strip() for code in values if str(code).strip())
    production_block_code = str(metadata.get("production_block_code") or "").strip()
    if production_block_code:
        raw_codes.append(production_block_code)
    raw_codes.extend(_deterministic_audit_block_codes_from_metadata(metadata))
    if metadata.get("effect_realization_blocked") is True:
        effect_codes = metadata.get("story_effect_realization_block_codes")
        if isinstance(effect_codes, Sequence) and not isinstance(effect_codes, (str, bytes)):
            raw_codes.extend(str(code).strip() for code in effect_codes if str(code).strip())
        else:
            raw_codes.append("STORY_EFFECT_REALIZATION_UNREALIZED")
    return _filter_inactive_audit_cache_codes(tuple(dict.fromkeys(raw_codes)), metadata)


def _chapter_has_unresolved_external_quality_findings(chapter: ChapterModel) -> bool:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    return bool(
        _deterministic_audit_block_codes_from_metadata(metadata)
        or _retention_quality_block_codes_from_metadata(metadata)
    )


async def _release_stale_auto_repair_block_if_latest_quality_clean(
    session: AsyncSession,
    chapter: ChapterModel,
) -> bool:
    """Release stale auto-repair blocks after a clean final quality report.

    Auto-repair reassembles the chapter repeatedly. A previous blocked state can
    survive on the in-memory chapter row even after the latest persisted chapter
    quality report is clean, which prevents export. Only release that stale
    state when no other hard gate left explicit block metadata.
    """

    if str(getattr(chapter, "production_state", "") or "").lower() != "blocked":
        return False
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    if _chapter_has_non_quality_block_metadata(chapter):
        return False
    latest_report = await session.scalar(
        select(ChapterQualityReportModel)
        .where(ChapterQualityReportModel.chapter_id == chapter.id)
        .order_by(ChapterQualityReportModel.created_at.desc())
    )
    if not _latest_quality_report_is_clean(latest_report):
        return False
    metadata.pop("auto_repair_in_progress", None)
    metadata.pop("auto_repair_exhausted", None)
    metadata["auto_repair_resolved_by_clean_quality_report"] = True
    chapter.metadata_json = metadata
    chapter.production_state = "ok"
    await _clear_scene_auto_repair_residue_for_clean_chapter(session, chapter)
    return True


async def _clear_scene_auto_repair_residue_for_clean_chapter(
    session: AsyncSession,
    chapter: ChapterModel,
) -> int:
    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )
    if not scenes:
        return 0
    from bestseller.services.drafts import (
        _clear_scene_auto_repair_residue_after_clean_assembly,
    )

    return _clear_scene_auto_repair_residue_after_clean_assembly(scenes)


def _readiness_blocked_only_by_stale_auto_repair_residue(
    report: Any,
) -> bool:
    blocking_issues = tuple(getattr(report, "blocking_issues", ()) or ())
    return bool(blocking_issues) and all(
        getattr(issue, "code", None) == "OUTLINE_STALE_AUTO_REPAIR_RESIDUE"
        for issue in blocking_issues
    )


def _clear_stale_scene_auto_repair_residue_for_outline_retry(
    scenes: Sequence[SceneCardModel],
) -> int:
    from bestseller.services.drafts import (
        _clear_scene_auto_repair_residue_after_clean_assembly,
    )

    return _clear_scene_auto_repair_residue_after_clean_assembly(list(scenes))


_CHAPTER_OUTLINE_READINESS_BLOCK_KEYS = (
    "blocked_by_chapter_outline_readiness_gate",
    "chapter_outline_readiness_block_codes",
    "chapter_outline_readiness_hint",
    "chapter_outline_readiness_report",
)


def _clear_chapter_outline_readiness_block_metadata(
    chapter: ChapterModel,
    *,
    recovered_by: str,
) -> bool:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    removed = [key for key in _CHAPTER_OUTLINE_READINESS_BLOCK_KEYS if key in metadata]
    if not removed:
        return False
    for key in removed:
        metadata.pop(key, None)
    metadata["chapter_outline_readiness_block_cleared_by"] = recovered_by
    metadata["chapter_outline_readiness_block_cleared_keys"] = removed
    chapter.metadata_json = metadata
    return True


async def _stop_auto_repair_if_latest_quality_clean(
    session: AsyncSession,
    chapter: ChapterModel,
) -> bool:
    """Stop a stale repair loop as soon as the latest chapter report is clean."""

    latest_report = await session.scalar(
        select(ChapterQualityReportModel)
        .where(ChapterQualityReportModel.chapter_id == chapter.id)
        .order_by(ChapterQualityReportModel.created_at.desc())
    )
    if not _latest_quality_report_is_clean(latest_report):
        return False
    if _chapter_has_unresolved_external_quality_findings(chapter):
        return False
    if _chapter_has_non_quality_block_metadata(chapter):
        return False

    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    previous_repair_codes = metadata.pop("auto_repair_last_block_codes", None)
    if previous_repair_codes:
        metadata["auto_repair_last_resolved_block_codes"] = previous_repair_codes
    for key in (
        "auto_repair_in_progress",
        "auto_repair_exhausted",
        "quality_bundle_blocking_codes",
        "quality_gate_block_codes",
        "production_block_code",
    ):
        metadata.pop(key, None)
    metadata["auto_repair_stopped_by_clean_quality_report"] = True
    chapter.metadata_json = metadata
    if str(getattr(chapter, "production_state", "") or "").lower() == "blocked":
        chapter.production_state = "ok"
    await _clear_scene_auto_repair_residue_for_clean_chapter(session, chapter)
    return True


def _apply_retention_retry_budget(
    chapter: ChapterModel,
    block_codes: tuple[str, ...],
    originality_config: Any,
) -> bool:
    """Increment retention-specific retry state.

    Returns True when the retention retry budget is exhausted and the caller
    should stop automatic repair, leaving the chapter for human review.
    """

    try:
        from bestseller.services.retention_safety_gate import (
            AUTO_REPAIR_RETENTION_CODES,
        )
    except Exception:
        return False

    retention_set = set(AUTO_REPAIR_RETENTION_CODES)
    retention_codes = tuple(code for code in block_codes if code in retention_set)
    if not retention_codes:
        return False

    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    try:
        retry_count = int(metadata.get("retention_retry_count") or 0) + 1
    except (TypeError, ValueError):
        retry_count = 1

    max_retries = max(
        1,
        int(
            metadata.get("retention_repair_max_retries")
            or getattr(originality_config, "retention_max_retries", 5)
            or 5
        ),
    )
    escalate_after = max(1, int(getattr(originality_config, "retention_escalate_after", 3) or 3))

    metadata["retention_retry_count"] = retry_count
    metadata["retention_retry_last_block_codes"] = list(retention_codes)
    if retry_count >= escalate_after:
        metadata["retention_retry_strict_prompt"] = (
            f"【留存自修复第 {retry_count} 次】本章连续触发留存门禁 "
            f"{', '.join(retention_codes)}。本次重写必须在前1000字内显式修复这些问题；"
            "若仍未通过，将转入机器深度修复。不要开新支线，不要扩写设定，优先兑现上一章钩子、"
            "招牌场景、铺垫节制与 cast/正典约束。"
        )
    else:
        metadata.pop("retention_retry_strict_prompt", None)

    exhausted = retry_count > max_retries
    if exhausted:
        metadata["retention_auto_repair_exhausted"] = True
        metadata["retention_machine_repair_required"] = True
        metadata["requires_machine_repair"] = True
        metadata["requires_human_review"] = False
        chapter.status = ChapterStatus.REVISION.value
        chapter.production_state = "blocked"

    chapter.metadata_json = metadata
    return exhausted


def _apply_rewrite_escalation(
    chapter: ChapterModel,
    block_codes: tuple[str, ...],
    originality_config: Any,
) -> bool:
    """Apply general rewrite escalation and keep legacy retention budgeting."""

    retention_exhausted = _apply_retention_retry_budget(
        chapter,
        block_codes,
        originality_config,
    )
    if not block_codes:
        return retention_exhausted
    try:
        from bestseller.services.rewrite_escalation import (
            EscalationLevel,
            decide_escalation,
        )
    except Exception:
        return retention_exhausted

    target_word_count = int(getattr(chapter, "target_word_count", 0) or 2200)
    try:
        _hard_min, _hard_target, hard_max = _chapter_length_contract_band(
            None,
            target_word_count,
        )
        target_word_count = int(_hard_target)
    except Exception:
        hard_max = max(target_word_count, int(target_word_count * 1.2))
    current_word_count = int(getattr(chapter, "current_word_count", 0) or target_word_count)
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    latest_audit = metadata.get("deterministic_audit_latest")
    forbidden_terms_hit = tuple(
        str(item.get("matched_text") or "")
        for item in (latest_audit.get("findings", []) if isinstance(latest_audit, dict) else [])
        if isinstance(item, dict)
        and str(item.get("code") or "") in {"FORBIDDEN_TERM_HIT", "DEPRECATED_ENTITY_HIT"}
    )
    decision = decide_escalation(
        chapter=chapter,
        block_codes=block_codes,
        current_word_count=current_word_count,
        target_word_count=target_word_count,
        hard_max_word_count=int(hard_max),
        forbidden_terms_hit=forbidden_terms_hit,
    )
    attempts_by_kind = dict(metadata.get("rewrite_attempts_by_kind") or {})
    attempts_by_kind[decision.block_kind] = decision.attempt_count
    metadata["rewrite_attempts_by_kind"] = attempts_by_kind
    metadata["rewrite_escalation"] = {
        "level": decision.level.value,
        "block_kind": decision.block_kind,
        "attempt_count": decision.attempt_count,
        "strict_directive": decision.strict_directive,
        "post_process_action": decision.post_process_action,
        "block_codes": list(block_codes),
    }
    if decision.level == EscalationLevel.MACHINE_REPAIR:
        metadata["requires_machine_repair"] = True
        metadata["requires_human_review"] = False
        metadata["auto_repair_machine_escalated"] = True
        chapter.status = ChapterStatus.REVISION.value
        chapter.production_state = "blocked"
    chapter.metadata_json = metadata
    return retention_exhausted or decision.level == EscalationLevel.MACHINE_REPAIR


def _is_volume_outline_auto_repairable(exc: Exception) -> bool:
    message = str(exc)
    count_contract = (
        "failed chapter-outline repair loop" in message
        and "returned" in message
        and "chapters" in message
    )
    aggregate_contract = any(
        marker in message
        for marker in (
            "has degenerate outline fields",
            "failed semantic promotion",
            "OUTLINE_INFORMATION_CONTRACT_GAP",
            "OUTLINE_CAUSAL_CONTRACT_DEGENERATE",
            "OUTLINE_CONTRADICTORY_TRANSFER",
        )
    )
    return count_contract or aggregate_contract


def _volume_outline_auto_repair_reason(exc: Exception) -> str:
    """Return the actual failed contract instead of labelling every retry count drift."""

    message = str(exc)
    if any(
        marker in message
        for marker in (
            "has degenerate outline fields",
            "failed semantic promotion",
            "OUTLINE_INFORMATION_CONTRACT_GAP",
            "OUTLINE_CAUSAL_CONTRACT_DEGENERATE",
            "OUTLINE_CONTRADICTORY_TRANSFER",
        )
    ):
        return "aggregate_semantic_contract"
    return "chapter_outline_count_contract"


def _volume_outline_auto_repair_constraints(
    *,
    language: str | None,
    volume_number: int,
    expected_count: int,
    error_message: str,
) -> list[str]:
    is_en = is_english_language(language)
    if expected_count <= 0:
        expected_count = 1
    excerpt = error_message[:1200]
    aggregate_repair = any(
        marker in error_message
        for marker in (
            "has degenerate outline fields",
            "failed semantic promotion",
            "OUTLINE_INFORMATION_CONTRACT_GAP",
            "OUTLINE_CAUSAL_CONTRACT_DEGENERATE",
            "OUTLINE_CONTRADICTORY_TRANSFER",
        )
    )
    if is_en:
        if aggregate_repair:
            return [
                (
                    f"Automatic aggregate-contract repair for volume {volume_number}. "
                    "Regenerate the outline and fix every chapter/field named in the "
                    "diagnostic. chapter_goal, opening_situation, and main_conflict must "
                    "carry distinct meanings; revealed/withheld information and causal "
                    "state transitions must remain explicit and non-contradictory. Preserve "
                    f"the exact {expected_count}-chapter budget and all unaffected contracts."
                ),
                f"Previous aggregate diagnostic: {excerpt}",
            ]
        return [
            (
                "Automatic volume-outline repair after a count-contract failure. "
                f"Regenerate volume {volume_number} from scratch and return exactly "
                f"{expected_count} chapter objects in chapters. Count the array "
                "before final output; do not summarize, stop early, pad, trim, "
                "merge, split, or move future-volume material."
            ),
            f"Previous failure diagnostic: {excerpt}",
        ]
    if aggregate_repair:
        return [
            (
                f"第{volume_number}卷汇总硬合同自动修复：请重新生成章纲，并逐项修复诊断中点名的"
                "章节与字段。chapter_goal、opening_situation、main_conflict 必须各自承担不同"
                "语义；information_revealed / information_withheld 与因果状态转移必须明确、"
                f"不矛盾。必须保持恰好 {expected_count} 章，并原样保留未被点名的合同。"
            ),
            f"上一轮汇总诊断：{excerpt}",
        ]
    return [
        (
            "卷章纲自动修复：上一轮违反章数合同。"
            f"请从头重写第{volume_number}卷，chapters 数组必须恰好包含 "
            f"{expected_count} 个章节对象。输出前必须自检数组长度；不得概括、提前停止、"
            "补白、裁剪、合并、拆分，也不得把后续卷内容挪入本卷。"
        ),
        f"上一轮失败诊断：{excerpt}",
    ]


WORKFLOW_TYPE_CHAPTER_PIPELINE = "chapter_pipeline"
WORKFLOW_TYPE_PROJECT_PIPELINE = "project_pipeline"
ProgressCallback = Callable[[str, dict[str, Any] | None], None]


class ProjectRepairPauseError(RuntimeError):
    """Raised when normal writing is blocked by a project-level pause."""


TEMPORARY_PLANNING_THROTTLE_REASON = "temporary_planning_throttle_for_new_books"


def _project_blocked_for_structural_repair(project: ProjectModel) -> bool:
    """Whether a pause should block *forward* writing.

    Explicit structural markers (``structural_repair_required``,
    ``generation_resume_blocked_until_repair_audit``) always block. A generic
    ``production_paused`` only blocks when its reason maps to a *structural*
    gate — a pause caused by a *local* quality gate (opening tension, length,
    style) is confined to one chapter's prose and must not stall new-chapter
    writing. See ``services.repair_impact`` and the 青囊不语问阴阳 regression
    (looped ch1 opening repair forever while later chapters waited).
    """

    metadata = getattr(project, "metadata_json", None) or {}
    focus_pause = metadata.get("focus_pause")
    focus_reason = str(metadata.get("production_pause_reason") or "").strip()
    if isinstance(focus_pause, dict):
        focus_reason = str(focus_pause.get("reason") or focus_reason).strip()
    if focus_reason.startswith("focus_"):
        return True
    if metadata.get("structural_repair_required") or metadata.get(
        "generation_resume_blocked_until_repair_audit"
    ):
        return True
    if metadata.get("production_paused"):
        reason = metadata.get("production_pause_reason") or metadata.get(
            "last_generation_gate_reason"
        )
        return pause_reason_is_structural(str(reason) if reason is not None else None)
    return False


def _fanqie_finding_applies_to_chapter(finding: Mapping[str, Any], chapter_number: int) -> bool:
    """Keep earlier chapters' local findings out of the current chapter veto."""
    target = str(finding.get("target") or "")
    if re.fullmatch(r"opening\.first_\d+", target):
        return chapter_number == 1
    match = re.fullmatch(r"chapters\.(\d+)", target)
    if match:
        # A future/missing chapter is not silently treated as resolved.
        return int(match.group(1)) >= chapter_number
    return True


async def _run_fanqie_long_gate_for_chapter(
    session: AsyncSession,
    *,
    project: ProjectModel,
    project_slug: str,
    chapter_number: int,
    chapter_draft: ChapterDraftVersionModel,
    block_on_failure: bool,
    chapter: ChapterModel | None = None,
    block_attempt_cap: int = 3,
    settings: AppSettings | None = None,
    workflow_run_id: UUID | None = None,
) -> dict[str, Any]:
    """Preserve keyword findings and verify their veto against scoped prose.

    Production callers supply settings for one auditable semantic review.
    The attempt budget remains only for legacy callers without model settings;
    repeated failure never overrides an uncertain production review.
    """

    chapter_texts = await load_current_chapter_texts_for_fanqie_gate(
        session,
        project_slug=project_slug,
        through_chapter=chapter_number,
    )
    chapter_texts[chapter_number] = chapter_draft.content_md or ""
    artifact = await evaluate_and_persist_fanqie_long_readiness(
        session,
        project_slug=project_slug,
        chapter_texts=chapter_texts,
        protagonist_name=_fanqie_gate_protagonist_name(project),
    )
    report = getattr(artifact, "content", None) or {}
    passed = bool(report.get("passed"))
    findings = report.get("findings", [])
    finding_payloads = findings if isinstance(findings, list) else []
    critical_count = sum(
        1
        for finding in finding_payloads
        if isinstance(finding, dict) and finding.get("severity") == "critical"
    )

    # Per-chapter block-attempt budget. Persisted in chapter.metadata so
    # the count survives across pipeline runs (intra-run counters reset
    # at the start of every project_repair / chapter_pipeline call).
    block_attempts = 0
    budget_demoted = False
    if chapter is not None and settings is None:
        chapter_meta = dict(getattr(chapter, "metadata_json", None) or {})
        prior_attempts = int(chapter_meta.get("fanqie_long_ranking_block_attempts") or 0)
        if passed:
            # Clean signal: this draft cleared the gate. Reset the counter
            # so a future regression starts the budget fresh.
            if "fanqie_long_ranking_block_attempts" in chapter_meta:
                chapter_meta.pop("fanqie_long_ranking_block_attempts", None)
                chapter_meta.pop("fanqie_long_ranking_block_attempts_demoted", None)
                chapter.metadata_json = chapter_meta
            block_attempts = 0
        else:
            block_attempts = prior_attempts + 1
            chapter_meta["fanqie_long_ranking_block_attempts"] = block_attempts
            if block_attempts >= max(int(block_attempt_cap), 1):
                budget_demoted = True
                chapter_meta["fanqie_long_ranking_block_attempts_demoted"] = True
                logger.warning(
                    "fanqie_long_ranking_gate demoted to audit-only for "
                    "project=%s chapter=%d (block_attempts=%d >= cap=%d); "
                    "downstream LLM judges remain authoritative.",
                    project_slug,
                    chapter_number,
                    block_attempts,
                    int(block_attempt_cap),
                )
            chapter.metadata_json = chapter_meta

    blocks_write = bool(block_on_failure and not passed and not budget_demoted)
    semantic_review = None
    applicable_critical_count = critical_count
    if settings is not None and block_on_failure and not passed:
        from bestseller.services.fanqie_long_semantic_review import (
            review_fanqie_long_semantic_findings,
        )

        metadata = getattr(project, "metadata_json", None) or {}
        applicable_findings = [
            finding for finding in finding_payloads
            if isinstance(finding, dict)
            and _fanqie_finding_applies_to_chapter(finding, chapter_number)
        ]
        applicable_critical_count = sum(
            finding.get("severity") == "critical" for finding in applicable_findings
        )
        review = await review_fanqie_long_semantic_findings(
            session,
            settings,
            project=project,
            genre=str(metadata.get("genre") or ""),
            profile=metadata.get("genre_intent_contract") or {},
            chapter_texts=chapter_texts,
            findings=applicable_findings,
            workflow_run_id=workflow_run_id,
        )
        semantic_review = review.model_dump(mode="json")
        # Missing critical evidence is not a semantic clearance.
        blocks_write = bool(
            review.blocking_findings
            or (not critical_count and not applicable_critical_count)
        )

    return {
        "artifact_id": str(artifact.id) if artifact.id else None,
        "passed": passed,
        "critical_count": critical_count,
        "current_chapter_critical_count": applicable_critical_count,
        "evaluated_chapter_number": chapter_number,
        "finding_count": len(finding_payloads),
        "findings": finding_payloads[:20],
        "repair_hints": [
            {
                "code": str(finding.get("code") or ""),
                "target": str(finding.get("target") or ""),
                "severity": str(finding.get("severity") or ""),
                "repair_hint": str(finding.get("repair_hint") or ""),
            }
            for finding in finding_payloads[:20]
            if isinstance(finding, dict)
        ],
        "metrics": report.get("metrics", {}),
        "block_attempts": block_attempts,
        "block_attempt_cap": int(block_attempt_cap),
        "block_attempts_demoted": budget_demoted,
        "blocks_write": blocks_write,
        "semantic_review": semantic_review,
        "chapter_draft_id": str(chapter_draft.id),
    }


def _fanqie_gate_protagonist_name(project: ProjectModel) -> str | None:
    metadata = getattr(project, "metadata_json", None) or {}
    cast_spec = metadata.get("cast_spec")
    if isinstance(cast_spec, dict):
        protagonist = cast_spec.get("protagonist")
        if isinstance(protagonist, dict):
            name = str(protagonist.get("name") or "").strip()
            if name:
                return name
    protagonist = metadata.get("protagonist")
    if isinstance(protagonist, dict):
        name = str(protagonist.get("name") or "").strip()
        if name:
            return name
    return None


def _voice_dna_excluded_names(project: ProjectModel) -> list[str]:
    """Character names to suppress from self-extracted Voice DNA.

    Without these, high-frequency cast names surface as "catchphrases"
    the writer is then instructed to keep repeating.
    """

    names: list[str] = []
    seen: set[str] = set()

    def _add(value: object) -> None:
        name = str(value or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    _add(_fanqie_gate_protagonist_name(project))
    metadata = getattr(project, "metadata_json", None) or {}
    cast_spec = metadata.get("cast_spec")
    if isinstance(cast_spec, dict):
        for value in cast_spec.values():
            entries = value if isinstance(value, list) else [value]
            for entry in entries:
                if isinstance(entry, dict):
                    _add(entry.get("name"))
    return names


async def _refresh_overused_phrase_block(
    session: AsyncSession,
    project: ProjectModel,
    settings: AppSettings,
) -> None:
    """Recompute the book-level overused-phrase avoidance block.

    Persists into ``project.metadata_json["_overused_phrase_block"]``
    which the scene pre-write path injects into the writer context.
    Must run on BOTH the draft-mode and full-quality post-chapter paths
    — it originally lived only inside the draft branch, leaving the
    block permanently absent for production (full-quality) runs.
    """

    from bestseller.services.deduplication import (
        build_overused_phrase_avoidance_block,
        extract_frequent_phrases,
    )

    _all_scene_texts_q = await session.scalars(
        select(SceneDraftVersionModel.content)
        .join(
            SceneCardModel,
            SceneDraftVersionModel.scene_card_id == SceneCardModel.id,
        )
        .join(
            ChapterModel,
            SceneCardModel.chapter_id == ChapterModel.id,
        )
        .where(
            ChapterModel.project_id == project.id,
            SceneDraftVersionModel.is_current.is_(True),
            SceneDraftVersionModel.content.isnot(None),
        )
    )
    _all_scene_texts = [t for t in _all_scene_texts_q if t]
    if len(_all_scene_texts) < 3:
        return
    _lang = getattr(project, "language", None) or settings.generation.language
    _phrases = extract_frequent_phrases(_all_scene_texts, language=_lang)
    if _phrases:
        _phrase_block = build_overused_phrase_avoidance_block(_phrases, language=_lang)
        project.metadata_json = {
            **(project.metadata_json or {}),
            "_overused_phrase_block": _phrase_block,
        }


async def _ensure_emotion_kernel_backfill_for_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    *,
    requested_by: str,
    progress: ProgressCallback | None = None,
) -> None:
    from bestseller.services.planning_capability_selection import planning_capability_selected

    if not planning_capability_selected(project, "emotion_driven_kernel",
            available=getattr(settings.pipeline, "enable_emotion_driven_kernel", True)):
        return
    if not getattr(settings.pipeline, "enable_emotion_kernel_backfill", True):
        return
    try:
        result = await ensure_project_emotion_driven_kernel(
            session,
            project,
            requested_by=requested_by,
            persist_artifact=False,
        )
    except PlanningCapabilitySelectionError:
        raise
    except Exception:
        logger.warning(
            "EmotionDrivenKernel legacy backfill failed for project %s; continuing without it",
            project.slug,
            exc_info=True,
        )
        project.metadata_json = {
            **(getattr(project, "metadata_json", None) or {}),
            "emotion_driven_kernel_backfill_failed": True,
        }
        return
    if result.changed:
        _emit_progress(
            progress,
            "emotion_kernel_backfilled",
            {
                "project_slug": project.slug,
                "status": result.status,
                "source": result.source,
            },
        )


async def _ensure_public_emotion_kernel_backfill_for_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    *,
    requested_by: str,
    progress: ProgressCallback | None = None,
) -> None:
    if not getattr(settings.pipeline, "enable_public_emotion_kernel_backfill", True):
        return
    try:
        result = await ensure_project_public_emotion_kernels(
            session,
            project,
            requested_by=requested_by,
            persist_artifact=False,
        )
    except PlanningCapabilitySelectionError:
        raise
    except Exception:
        logger.warning(
            "PublicEmotionKernel legacy backfill failed for project %s; continuing without it",
            project.slug,
            exc_info=True,
        )
        project.metadata_json = {
            **(getattr(project, "metadata_json", None) or {}),
            "public_emotion_kernel_backfill_failed": True,
        }
        return
    if result.changed:
        _emit_progress(
            progress,
            "public_emotion_kernel_backfilled",
            {
                "project_slug": project.slug,
                "status": result.status,
                "source": result.source,
            },
        )


async def _ensure_entry_system_backfill_for_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    *,
    requested_by: str,
    progress: ProgressCallback | None = None,
) -> None:
    from bestseller.services.planning_capability_selection import planning_capability_selected

    if not planning_capability_selected(project, "entry_system_kernel",
            available=getattr(settings.pipeline, "enable_entry_system_kernel", True)):
        return
    if not getattr(settings.pipeline, "enable_entry_system_backfill", True):
        return
    try:
        result = await ensure_project_entry_system_compat(
            session,
            project,
            requested_by=requested_by,
            persist_artifact=False,
        )
    except PlanningCapabilitySelectionError:
        raise
    except Exception:
        logger.warning(
            "Entry system legacy backfill failed for project %s; continuing without it",
            project.slug,
            exc_info=True,
        )
        project.metadata_json = {
            **(getattr(project, "metadata_json", None) or {}),
            "entry_system_backfill_failed": True,
        }
        return
    if result.changed:
        _emit_progress(
            progress,
            "entry_system_backfilled",
            {
                "project_slug": project.slug,
                "status": result.status,
                "source": result.source,
                "registry_entry_count": len((result.registry or {}).get("entries") or []),
            },
        )


def _assert_project_not_blocked_for_structural_repair(
    project: ProjectModel,
    *,
    project_slug: str,
    operation: str,
    allow_structural_repair: bool = False,
) -> None:
    if allow_structural_repair or not _project_blocked_for_structural_repair(project):
        return
    metadata = getattr(project, "metadata_json", None) or {}
    reason = metadata.get("production_pause_reason") or "structural repair is required"
    raise ProjectRepairPauseError(
        f"Project '{project_slug}' is paused for structural repair and cannot run "
        f"{operation}. reason={reason!r}. Run the repair workflow or clear "
        "generation_resume_blocked_until_repair_audit after the repair audit passes."
    )


async def _enforce_book_design_consistency(
    session: AsyncSession,
    project: ProjectModel,
) -> None:
    """Persist a recoverable replan state when creation authority has drifted."""

    from bestseller.services.book_design import validate_project_book_design

    try:
        report = validate_project_book_design(project)
    except (TypeError, ValueError) as exc:
        metadata = dict(getattr(project, "metadata_json", None) or {})
        metadata.update(
            {
                "book_design_consistency_status": "needs_replan",
                "book_design_consistency_report": {
                    "passed": False,
                    "issues": [
                        {
                            "code": "book_design_snapshot_invalid",
                            "asset": "book_design_snapshot",
                            "expected": "valid locked creation snapshot",
                            "actual": str(exc),
                        }
                    ],
                },
                "planning_status": "needs_replan",
                "production_paused": True,
                "production_pause_reason": "book_design_snapshot_invalid",
                "generation_resume_blocked_until_repair_audit": True,
            }
        )
        project.metadata_json = metadata
        project.status = ProjectStatus.NEEDS_REPLAN.value
        await _checkpoint_commit(session)
        raise ProjectRepairPauseError(
            f"Book design snapshot is missing or invalid; replan is required. reason={exc}"
        ) from exc
    metadata = dict(getattr(project, "metadata_json", None) or {})
    metadata["book_design_consistency_report"] = report.to_dict()
    # Advisory-only drift (e.g. two auto-invented protagonist names) is recorded
    # for repair but must not pause a finished conception — see
    # BookDesignValidationReport.blocking_issues for the 2026-07-25 evidence.
    if not report.blocks_production:
        metadata["book_design_consistency_status"] = (
            "approved" if report.passed else "approved_with_advisories"
        )
        # 立锁的人负责解锁（2026-08-14 真机死锁）：本函数在 needs_replan 时
        # 设 production_paused + generation_resume_blocked_until_repair_audit，
        # 复检转绿时却只改 status，封锁标记留在原地——书卡成
        # 「一致性 approved 但不许写作」的永久僵局，自愈的解锁条件又不认这一族。
        # 同一个事实不能住在两个地方，谁设谁清。
        for key in (
            "production_paused",
            "production_pause_reason",
            "generation_resume_blocked_until_repair_audit",
        ):
            metadata.pop(key, None)
        project.metadata_json = metadata
        await session.flush()
        return
    metadata.update(
        {
            "book_design_consistency_status": "needs_replan",
            "planning_status": "needs_replan",
            "production_paused": True,
            "production_pause_reason": "book_design_consistency_failed",
            "generation_resume_blocked_until_repair_audit": True,
        }
    )
    project.metadata_json = metadata
    project.status = ProjectStatus.NEEDS_REPLAN.value
    await _checkpoint_commit(session)
    issue_codes = ", ".join(issue.code for issue in report.blocking_issues)
    raise ProjectRepairPauseError(
        "Book design consistency failed; outline and prose promotion are blocked "
        f"until replan. issues={issue_codes or 'unknown'}"
    )


async def _checkpoint_book_runtime_guard(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    *,
    progress: ProgressCallback | None = None,
) -> DriftReport | None:
    """Freeze the book's contract on first sight, verify it on every later one.

    Returns the drift report, or ``None`` when the guard is disabled or could
    not run. Swallows its own errors deliberately: a guard that can abort a
    book is a worse bug than the drift it looks for.
    """

    from bestseller.services.book_contract_snapshot import collect_book_contract
    from bestseller.services.book_runtime_guard import (
        GuardMode,
        build_guard,
        guard_mode,
        load_guard,
        store_guard,
        verify_guard,
    )

    mode = guard_mode()
    if mode is GuardMode.OFF:
        return None

    try:
        contract = collect_book_contract(settings, project)
        metadata = dict(getattr(project, "metadata_json", None) or {})

        if load_guard(metadata) is None:
            project.metadata_json = store_guard(
                metadata,
                build_guard(contract, frozen_by="autowrite_pipeline"),
            )
            await session.flush()
            _emit_progress(
                progress,
                "book_runtime_guard_frozen",
                {
                "project_slug": project.slug,
                "contract_parts": sorted(contract.keys()),
                },
            )
            return None

        report = verify_guard(metadata, contract, mode=mode)
        if not report.has_drift:
            return report

        logger.warning(
            "config drift detected for project=%s: %s (mode=%s)",
            project.slug,
            report.describe(),
            mode.value,
        )
        metadata["book_runtime_guard_drift"] = {
            **report.to_payload(),
            "detected_at": _dt.datetime.now(_dt.UTC).isoformat(),
        }
        if report.blocks_production:
            metadata["production_paused"] = True
            metadata["production_pause_reason"] = "book_runtime_config_drift"
        project.metadata_json = metadata
        await session.flush()
        _emit_progress(
            progress,
            "book_runtime_guard_drift",
            {
            "project_slug": project.slug,
            **report.to_payload(),
            },
        )
        return report
    except Exception:
        logger.warning(
            "book runtime guard checkpoint failed for %s; continuing",
            getattr(project, "slug", "?"),
            exc_info=True,
        )
        return None


async def _clear_auto_resumable_project_pause(
    session: AsyncSession,
    project: ProjectModel,
) -> bool:
    metadata = dict(getattr(project, "metadata_json", None) or {})
    reason = str(
        metadata.get("production_pause_reason") or metadata.get("last_generation_gate_reason") or ""
    ).strip()
    if reason != TEMPORARY_PLANNING_THROTTLE_REASON:
        return False
    if not (
        metadata.get("production_paused")
        or metadata.get("generation_resume_blocked_until_repair_audit")
        or (getattr(project, "status", None) or "").lower() == ProjectStatus.PAUSED.value
    ):
        return False

    metadata["last_project_pause_auto_resumed_reason"] = reason
    for key in (
        "generation_resume_blocked_until_repair_audit",
        "production_paused",
        "production_pause_reason",
        "paused_at",
    ):
        metadata.pop(key, None)
    project.metadata_json = metadata
    if (getattr(project, "status", None) or "").lower() == ProjectStatus.PAUSED.value:
        project.status = ProjectStatus.REVISING.value
    await session.flush()
    return True


def project_awaits_concept_approval(project: ProjectModel) -> bool:
    """这本书还在等用户批准创意（``stop_after_conception`` 建的）。

    2026-08-24 真机：这条判据此前只以**字面形式**住在下面那个函数里，自愈
    那边根本不知道它存在，于是把一本 conception_only 的书按
    ``under_target_chapters`` 捞起来开写了整本；走的还是正常 autowrite 入口，
    因此下面那个函数顺手写下 conception_approved=True —— 框架替用户按了
    「同意」，并抹掉了自己曾在等批准的证据。做成一份，两边引同一个。
    """

    metadata = getattr(project, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        return False
    return bool(
        metadata.get("conception_only")
        or metadata.get("planning_status") == "awaiting_concept_approval"
    )


def _mark_project_autowrite_started(project: ProjectModel) -> bool:
    """Clear conception-only lifecycle residue once full writing really starts."""

    metadata = dict(getattr(project, "metadata_json", None) or {})
    if not project_awaits_concept_approval(project):
        return False
    metadata.pop("conception_only", None)
    metadata["planning_status"] = "writing"
    metadata["conception_approved"] = True
    metadata["conception_only_cleared_by"] = "autowrite_pipeline"
    project.metadata_json = metadata
    return True


def _chapter_by_number(chapters: list[ChapterModel], number: int) -> ChapterModel | None:
    for chapter in chapters:
        if chapter.chapter_number == number:
            return chapter
    return None


def _chapter_text(*values: Any, default: str = "") -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _project_protagonist_name(project: ProjectModel) -> str:
    metadata = getattr(project, "metadata_json", None) or {}
    for value in (
        metadata.get("protagonist_name"),
        metadata.get("main_character_name"),
        (metadata.get("protagonist") or {}).get("name")
        if isinstance(metadata.get("protagonist"), dict)
        else None,
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "主角"


def _build_qimao_opening_contract_from_outline(
    project: ProjectModel,
    chapters: list[ChapterModel],
) -> dict[str, Any]:
    """Backfill a concrete Qimao opening contract for legacy projects with outlines."""

    if not chapters:
        return {}

    first = _chapter_by_number(chapters, 1) or chapters[0]
    second = _chapter_by_number(chapters, 2)
    third = _chapter_by_number(chapters, 3)
    protagonist_name = _project_protagonist_name(project)
    first_title = _chapter_text(first.title, default=f"第{first.chapter_number}章")
    first_goal = _chapter_text(
        first.chapter_goal,
        first.main_conflict,
        first.hook_description,
        default=f"{protagonist_name}处理第{first.chapter_number}章现场危机",
    )
    first_conflict = _chapter_text(
        first.main_conflict,
        first.chapter_goal,
        default=first_goal,
    )
    first_hook = _chapter_text(first.hook_description, first_conflict, default=first_conflict)
    # 2026-06-25 去通用性污染：原合同把【侦探取证开局】(证据被抹/保住现场线索/逼出谁
    # 在掩盖/抓证据漏洞反制/拿可验证证据) 焊死成每本书的签约质量合同 → 修仙/言情/科幻
    # 书都被强塞侦探开局。改为题材中性的"开场即冲突+三章节奏+钩子"骨架，用本书自己的
    # 目标/冲突/钩子填充，不预设证据/线索/反制的侦探词。
    contract: dict[str, Any] = {
        "platform_target": "qimao",
        "source": "outline_backfill_qimao_planning_gate",
        "protagonist_name": protagonist_name,
        "opening_incident": (
            f"《{first_title}》开场：{protagonist_name}当场被「{first_goal}」逼到必须立刻行动的位置，"
            "拖延或退让都会让局势立刻恶化。"
        ),
        "first_page_conflict": (
            f"{protagonist_name}在《{first_title}》当场面对「{first_conflict}」；"
            "必须立刻应对，否则会被对手当场夺走主动权、付出第一轮代价。"
        ),
        "protagonist_immediate_goal": (
            f"在被逼到墙角前，先在《{first_title}》现场稳住局面、看清第一处要害，并当场决定下一步怎么走。"
        ),
        "visible_loss_if_fail": (
            f"失败会让{protagonist_name}在《{first_title}》当场被夺走主动权、付出第一轮代价。"
        ),
        "protagonist_edge": (
            f"{protagonist_name}能在高压现场做出别人做不到的那一步关键判断或行动，打开局面。"
        ),
        "edge_limit": "优势只能解决第一轮压力，不能直接跳过主线代价。",
        "chapter_1_small_turn": (
            f"{protagonist_name}用自己的优势稳住局面、夺回主动、扭转误判，并把钩子引到「{first_hook}」。"
        ),
        "chapter_2_reveal": "第二章放出会改变局势判断的新信息、误会扩大或隐藏规则。",
        "chapter_3_payoff": (
            f"{protagonist_name}在第三章拿到第一口实打实的回报，并打开下一轮危险。"
        ),
        "first_10000_loop": (
            "触发冲突 -> 主角当场行动 -> 拿到第一份进展同时承受代价 -> 章尾把钩子引向更深的悬念"
        ),
        "forbidden_opening_modes": [
            "background_exposition",
            "normal_day",
            "scenery_first",
            "worldbuilding_first",
            "slow_relationship_setup",
        ],
    }

    if second is not None:
        second_title = _chapter_text(second.title, default=f"第{second.chapter_number}章")
        second_reveal = _chapter_text(
            second.main_conflict,
            second.hook_description,
            second.chapter_goal,
            default="第二章放出改变局势判断的新信息。",
        )
        contract["chapter_2_reveal"] = f"《{second_title}》揭示：{second_reveal}"

    if third is not None:
        third_title = _chapter_text(third.title, default=f"第{third.chapter_number}章")
        third_payoff = _chapter_text(
            third.hook_description,
            third.main_conflict,
            third.chapter_goal,
            default="主角拿到第一份实打实的回报。",
        )
        contract["chapter_3_payoff"] = (
            f"{protagonist_name}在《{third_title}》拿到第一口实打实的回报：{third_payoff}"
        )

    return contract


def _repair_qimao_opening_contract_from_outline(
    contract: dict[str, Any],
    chapters: list[ChapterModel],
) -> dict[str, Any]:
    """Turn abstract opening-contract slogans into chapter-1 executable beats."""

    if not contract or not chapters:
        return contract

    first = _chapter_by_number(chapters, 1) or chapters[0]
    second = _chapter_by_number(chapters, 2)
    third = _chapter_by_number(chapters, 3)
    protagonist_name = _chapter_text(contract.get("protagonist_name"), default="主角")
    first_title = _chapter_text(first.title, default=f"第{first.chapter_number}章")
    first_goal = _chapter_text(
        first.chapter_goal,
        first.main_conflict,
        first.hook_description,
        default=f"{protagonist_name}处理第{first.chapter_number}章现场危机",
    )
    first_conflict = _chapter_text(
        first.main_conflict,
        first.chapter_goal,
        default=first_goal,
    )
    first_hook = _chapter_text(first.hook_description, first_conflict, default=first_conflict)

    repaired = dict(contract)
    repaired["opening_incident"] = (
        f"《{first_title}》开场：{protagonist_name}当场处理「{first_goal}」，"
        f"随即撞上「{first_conflict}」。"
    )
    repaired["first_page_conflict"] = (
        f"{protagonist_name}在《{first_title}》当场面对「{first_conflict}」；"
        "必须立刻应对，否则会被对手当场夺走主动权、付出第一轮代价。"
    )
    repaired["protagonist_immediate_goal"] = (
        f"在被逼到墙角前，先在《{first_title}》现场稳住局面、看清第一处要害，并当场决定下一步怎么走。"
    )
    repaired["visible_loss_if_fail"] = (
        f"失败会让{protagonist_name}在《{first_title}》当场被夺走主动权、付出第一轮代价。"
    )
    repaired["chapter_1_small_turn"] = (
        f"{protagonist_name}用自己的优势稳住局面、夺回主动、扭转误判，并把钩子引到「{first_hook}」。"
    )

    if second is not None:
        second_title = _chapter_text(second.title, default=f"第{second.chapter_number}章")
        second_reveal = _chapter_text(
            second.main_conflict,
            second.hook_description,
            second.chapter_goal,
            default="第二章放出改变局势判断的新信息。",
        )
        repaired["chapter_2_reveal"] = f"《{second_title}》揭示：{second_reveal}"

    if third is not None:
        third_title = _chapter_text(third.title, default=f"第{third.chapter_number}章")
        third_payoff = _chapter_text(
            third.hook_description,
            third.main_conflict,
            third.chapter_goal,
            default="主角拿到第一份实打实的回报。",
        )
        repaired["chapter_3_payoff"] = (
            f"{protagonist_name}在《{third_title}》拿到第一口实打实的回报：{third_payoff}"
        )

    repaired["first_10000_loop"] = (
        "触发冲突 -> 主角当场行动 -> 拿到第一份进展同时承受代价 -> 章尾把钩子引向更深的悬念"
    )
    return repaired


def _record_qimao_planning_gate(
    project: ProjectModel,
    *,
    chapters: list[ChapterModel] | None = None,
) -> dict[str, Any] | None:
    if not project_uses_signing_quality_gate(project):
        return None
    metadata = getattr(project, "metadata_json", None) or {}
    contract = metadata.get("opening_quality_contract") or metadata.get("qimao_opening_contract")
    payload_to_check = {"qimao_opening_contract": contract} if contract else metadata
    report = evaluate_qimao_planning_gate(
        payload_to_check,
        target_chapters=getattr(project, "target_chapters", None),
    )
    if not contract and not report.passed and chapters:
        backfilled_contract = _build_qimao_opening_contract_from_outline(project, chapters)
        backfilled_report = evaluate_qimao_planning_gate(
            {"qimao_opening_contract": backfilled_contract},
            target_chapters=getattr(project, "target_chapters", None),
        )
        if backfilled_report.passed:
            contract = backfilled_contract
            report = backfilled_report
    if contract and not report.passed and chapters:
        repaired_contract = _repair_qimao_opening_contract_from_outline(
            dict(contract),
            chapters,
        )
        repaired_report = evaluate_qimao_planning_gate(
            {"qimao_opening_contract": repaired_contract},
            target_chapters=getattr(project, "target_chapters", None),
        )
        if repaired_report.passed:
            contract = repaired_contract
            report = repaired_report
    payload = qimao_planning_gate_report_to_dict(report)
    updated_metadata = {
        **metadata,
        "opening_quality_planning_gate_report": payload,
        "qimao_planning_gate_report": payload,
    }
    if contract:
        updated_metadata["opening_quality_contract"] = contract
        updated_metadata["qimao_opening_contract"] = contract
        if report.passed:
            updated_metadata["opening_quality_contract_status"] = "planned_gate_passed"
            updated_metadata["qimao_opening_contract_status"] = "planned_gate_passed"
    project.metadata_json = updated_metadata
    return payload


def _qimao_planning_gate_error_message(report_payload: dict[str, Any]) -> str:
    findings = report_payload.get("findings")
    codes: list[str] = []
    if isinstance(findings, list):
        codes = [
            str(item.get("code"))
            for item in findings
            if isinstance(item, dict) and item.get("severity") == "critical"
        ]
    suffix = ", ".join(codes) if codes else "unknown"
    return f"Qimao planning gate failed: {suffix}"


def _scene_probe_from_model(scene: SceneCardModel) -> ScenePlanProbe:
    metadata = dict(getattr(scene, "metadata_json", None) or {})
    return ScenePlanProbe(
        scene_number=int(getattr(scene, "scene_number", 0) or 0),
        scene_type=str(getattr(scene, "scene_type", "") or ""),
        title=str(getattr(scene, "title", "") or ""),
        participants=tuple(
            str(item).strip()
            for item in (getattr(scene, "participants", None) or [])
            if str(item).strip()
        ),
        purpose=str(getattr(scene, "purpose", None) or ""),
        entry_state=str(getattr(scene, "entry_state", None) or ""),
        exit_state=str(getattr(scene, "exit_state", None) or ""),
        hook_requirement=str(getattr(scene, "hook_requirement", "") or ""),
        contract_context=json.dumps(
            {
                key: metadata.get(key)
                for key in (
                    "methodology_contract",
                    "action_sequence",
                    "concrete_goal",
                    "protagonist_state",
                    "cut_point",
                )
                if metadata.get(key)
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def _chapter_probe_from_model(chapter: ChapterModel) -> ChapterPlanProbe:
    try:
        raw_scenes = list(getattr(chapter, "scenes", []) or [])
    except Exception:
        raw_scenes = []
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    return ChapterPlanProbe(
        chapter_number=int(getattr(chapter, "chapter_number", 0) or 0),
        title=str(getattr(chapter, "title", "") or ""),
        chapter_goal=str(getattr(chapter, "chapter_goal", "") or ""),
        opening_situation=str(getattr(chapter, "opening_situation", "") or ""),
        main_conflict=str(getattr(chapter, "main_conflict", "") or ""),
        hook_description=str(getattr(chapter, "hook_description", "") or ""),
        hype_type=str(getattr(chapter, "hype_type", "") or ""),
        hype_intensity=(
            float(getattr(chapter, "hype_intensity"))
            if getattr(chapter, "hype_intensity", None) is not None
            else None
        ),
        contract_context=json.dumps(
            {
                key: metadata.get(key)
                for key in ("causal_contract", "methodology_contract")
                if metadata.get(key)
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        scenes=tuple(_scene_probe_from_model(scene) for scene in raw_scenes),
    )


def _project_outline_semantic_payload(
    project: ProjectModel,
    chapters: Sequence[ChapterModel],
    settings: AppSettings | None = None,
) -> dict[str, Any]:
    """Translate persisted runtime models into the whole-book gate contract."""

    from bestseller.services.book_design import (
        ensure_project_book_design_snapshot,
        extract_creation_protagonist_name,
    )

    metadata = dict(getattr(project, "metadata_json", None) or {})
    snapshot = ensure_project_book_design_snapshot(project)
    story_spine = dict(metadata.get("story_spine") or {})
    story_name = extract_creation_protagonist_name(metadata) or snapshot.protagonist.name
    intent = metadata.get("genre_intent_contract")
    tone_preference = (
        str(intent.get("tone_preference") or "").strip() if isinstance(intent, Mapping) else ""
    )
    story_spine.update(
        {
            "title": project.title,
            "protagonist": story_name,
            "genre": project.genre,
            "tone": tone_preference or snapshot.tone,
        }
    )

    writing_profile = metadata.get("writing_profile")
    profile = writing_profile if isinstance(writing_profile, Mapping) else {}
    style = profile.get("style") if isinstance(profile.get("style"), Mapping) else {}
    tone_keywords = style.get("tone_keywords") or profile.get("tone_keywords") or ()
    if isinstance(tone_keywords, str):
        writing_tone = tone_keywords
    elif isinstance(tone_keywords, Sequence):
        writing_tone = "、".join(str(item) for item in tone_keywords if str(item).strip())
    else:
        writing_tone = ""

    identity_rows = metadata.get("identity_manifest")
    manifest_rows = (
        list(identity_rows)
        if isinstance(identity_rows, Sequence) and not isinstance(identity_rows, (str, bytes))
        else []
    )
    chapter_count = max(1, int(getattr(project, "target_chapters", 0) or len(chapters) or 1))
    total_words = max(0, int(getattr(project, "target_word_count", 0) or 0))
    if settings is not None and total_words > 0:
        from bestseller.services.word_targets import authoritative_book_word_targets

        exact_targets = authoritative_book_word_targets(project, settings)
    elif total_words > 0:
        base, remainder = divmod(total_words, chapter_count)
        exact_targets = tuple(
            base + (1 if index < remainder else 0) for index in range(chapter_count)
        )
    else:
        exact_targets = tuple(2600 for _ in range(chapter_count))
    semantic_scope = "whole_book"
    raw_rolling_plan = metadata.get("rolling_outline_plan")
    if isinstance(raw_rolling_plan, Mapping) and len(chapters) < chapter_count and chapters:
        chapter_numbers = [int(getattr(chapter, "chapter_number", 0) or 0) for chapter in chapters]
        if sorted(chapter_numbers) != list(range(1, len(chapter_numbers) + 1)):
            raise ValueError("rolling semantic gate requires a contiguous materialized prefix")
        if any(number < 1 or number > len(exact_targets) for number in chapter_numbers):
            raise ValueError("rolling semantic gate chapter is outside the book budget")
        exact_targets = tuple(exact_targets[number - 1] for number in chapter_numbers)
        chapter_count = len(chapters)
        total_words = sum(exact_targets)
        semantic_scope = "rolling_materialized_prefix"
    minimum_target = min(exact_targets)
    maximum_target = max(exact_targets)
    average_target = round(sum(exact_targets) / len(exact_targets))
    return {
        "target_word_count": total_words,
        "target_chapters": chapter_count,
        "semantic_scope": semantic_scope,
        "word_budget": {
            "minimum": minimum_target,
            "target": average_target,
            "maximum": maximum_target,
        },
        "story_spine": story_spine,
        "commercial_brief": {
            "title": project.title,
            "protagonist": story_name,
            "genre": project.genre,
            "tone": writing_tone or tone_preference or snapshot.tone,
        },
        "identity_manifest": {
            "title": project.title,
            "genre": project.genre,
            "entities": manifest_rows,
        },
        "chapters": [
            {
                "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
                "chapter_title": str(getattr(chapter, "title", "") or ""),
                "chapter_goal": str(getattr(chapter, "chapter_goal", "") or ""),
                "opening_situation": str(getattr(chapter, "opening_situation", "") or ""),
                "main_conflict": str(getattr(chapter, "main_conflict", "") or ""),
                "hook_description": str(getattr(chapter, "hook_description", "") or ""),
                "hook_type": str(getattr(chapter, "hook_type", "") or ""),
                "information_revealed": list(getattr(chapter, "information_revealed", None) or []),
                "chapter_emotion_arc": str(getattr(chapter, "chapter_emotion_arc", "") or ""),
                "target_word_count": int(getattr(chapter, "target_word_count", 0) or 0),
                "metadata": dict(getattr(chapter, "metadata_json", None) or {}),
            }
            for chapter in chapters
        ],
    }


def _record_outline_semantic_gate(
    project: ProjectModel,
    chapters: Sequence[ChapterModel],
    settings: AppSettings | None = None,
) -> dict[str, Any]:
    from bestseller.services.outline_semantic_gate import (
        evaluate_outline_semantic_gate,
        hard_contract_findings,
        llm_adjudication_candidates,
    )

    try:
        report = evaluate_outline_semantic_gate(
            _project_outline_semantic_payload(project, chapters, settings)
        )
        payload = report.to_dict()
        hard_findings = hard_contract_findings(report)
        candidates = llm_adjudication_candidates(report)
        # A finding about a chapter whose prose is already committed cannot be
        # acted on: you do not replan the outline of a chapter the book has
        # already shipped, and blocking on it starves every chapter that has not
        # been written yet. 2026-08-03, xianxia-upgrade-1785697772: with 16
        # chapters written and 23 planned, this gate blocked the whole book on
        # one OUTLINE_STATE_REGRESSION at chapter 13 plus seven
        # OUTLINE_REUSED_PAYLOAD_ANCHOR at chapter 1 — all of them behind the
        # written frontier. The project then had no owner at all: the prose lane
        # refuses a ``needs_replan`` project and the replan lane refuses one that
        # already has drafts.
        #
        # The frontier is ``current_chapter_number`` — the same pointer the
        # rolling window advances on — so the two cannot disagree. Findings
        # behind it are kept in the report as advisory.
        _written_frontier = int(getattr(project, "current_chapter_number", 0) or 0)
        _settled_findings = tuple(
            finding
            for finding in hard_findings
            if finding.chapter is not None and int(finding.chapter) <= _written_frontier
        )
        if _settled_findings:
            hard_findings = tuple(
                finding for finding in hard_findings if finding not in _settled_findings
            )
            logger.warning(
                "outline semantic gate for %s: %d finding(s) concern chapters at or "
                "behind the written frontier (chapter %d) and are advisory: %s",
                getattr(project, "slug", "?"),
                len(_settled_findings),
                _written_frontier,
                sorted({finding.code for finding in _settled_findings}),
            )
        payload.update(
            {
                "raw_promotion_allowed": report.promotion_allowed,
                "promotion_allowed": not hard_findings,
                "settled_chapter_findings": [finding.to_dict() for finding in _settled_findings],
                "written_frontier": _written_frontier,
                "effective_blocking_findings": [finding.to_dict() for finding in hard_findings],
                "llm_adjudication_candidates": [finding.to_dict() for finding in candidates],
                "adjudication_policy": "hard_contract_then_llm_contextual",
            }
        )
    except (TypeError, ValueError) as exc:
        payload = {
            "passed": False,
            "promotion_allowed": False,
            "score": 0.0,
            "findings": [
                {
                    "code": "OUTLINE_SEMANTIC_INPUT_INVALID",
                    "severity": "critical",
                    "message": str(exc),
                    "path": "chapters",
                }
            ],
        }
    metadata = dict(getattr(project, "metadata_json", None) or {})
    metadata.update(
        {
            "outline_semantic_gate_report": payload,
            "outline_semantic_gate_status": (
                "approved" if payload.get("promotion_allowed") else "needs_replan"
            ),
        }
    )
    if not payload.get("promotion_allowed"):
        metadata.update(
            {
                "planning_status": "needs_replan",
                "production_paused": True,
                "production_pause_reason": "outline_semantic_gate_failed",
                "generation_resume_blocked_until_repair_audit": True,
            }
        )
        project.status = ProjectStatus.NEEDS_REPLAN.value
    project.metadata_json = metadata
    return payload


def _release_approved_outline_replan_gate(
    project: ProjectModel,
    outline_artifact: object | None,
) -> bool:
    """Hand an approved replan back to the ordinary prose recovery lane.

    The dedicated replan owner must stop blocking prose as soon as a genuinely
    newer outline has passed promotion.  Keeping ``outline_replan_in_progress``
    set while chapters are written makes a worker restart unrecoverable: the
    prose scanner sees a structural block, while the replan scanner refuses to
    replace an outline after drafts exist.
    """

    metadata = dict(getattr(project, "metadata_json", None) or {})
    if not metadata.get("outline_replan_in_progress"):
        return False

    try:
        prior_version = int(metadata.get("outline_replan_prior_outline_version") or 0)
        current_version = int(getattr(outline_artifact, "version_no", 0) or 0)
    except (TypeError, ValueError):
        return False
    semantic_report = metadata.get("outline_semantic_gate_report")
    semantic_adjudication = (
        semantic_report.get("llm_adjudication")
        if isinstance(semantic_report, dict)
        and isinstance(semantic_report.get("llm_adjudication"), dict)
        else {}
    )
    adjudicated_promotion = bool(
        isinstance(semantic_report, dict)
        and (
            semantic_report.get("llm_adjudicated_all_volumes") is True
            or semantic_adjudication.get("restored_declared_gate_pass") is True
        )
    )
    if not (
        isinstance(semantic_report, dict)
        and semantic_report.get("promotion_allowed") is True
        and (current_version > prior_version or adjudicated_promotion)
    ):
        return False

    for key in (
        "outline_replan_in_progress",
        "outline_replan_prior_outline_version",
        "generation_resume_blocked_until_repair_audit",
        "production_paused",
        "production_pause_reason",
    ):
        metadata.pop(key, None)
    metadata.update(
        {
            "outline_replan_completed_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "planning_status": "writing",
            "outline_semantic_gate_status": "approved",
        }
    )
    project.metadata_json = metadata
    if project.status in {
        ProjectStatus.PLANNING.value,
        ProjectStatus.NEEDS_REPLAN.value,
        ProjectStatus.PAUSED.value,
    }:
        project.status = ProjectStatus.WRITING.value
    return True


async def _select_rolling_outline_window(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapters: Sequence[ChapterModel],
) -> list[ChapterModel]:
    """Promote one bounded chapter window and keep future detail out of prose."""

    from bestseller.services.book_design import ensure_project_book_design_snapshot
    from bestseller.services.rolling_outline import (
        build_rolling_outline_plan,
        promote_rolling_outline,
        rolling_window_schedule_hash,
    )

    if not getattr(settings.pipeline, "enable_rolling_outline", True):
        return list(chapters)
    metadata = dict(getattr(project, "metadata_json", None) or {})
    raw_macro = metadata.get("macro_outline_plan")
    raw_plan = metadata.get("rolling_outline_plan")
    if not isinstance(raw_macro, Mapping) or not isinstance(raw_plan, Mapping):
        if not getattr(settings.pipeline, "rolling_outline_block_when_missing", True):
            return list(chapters)
        metadata.update(
            {
                "rolling_outline_status": "needs_replan",
                "planning_status": "needs_replan",
                "production_paused": True,
                "production_pause_reason": "rolling_outline_missing",
                "generation_resume_blocked_until_repair_audit": True,
            }
        )
        project.metadata_json = metadata
        project.status = ProjectStatus.NEEDS_REPLAN.value
        await _checkpoint_commit(session)
        raise ProjectRepairPauseError(
            "Rolling outline plans are missing; prose is blocked until replan."
        )
    try:
        snapshot = ensure_project_book_design_snapshot(project)
        from bestseller.services.chapter_outline_readiness import load_rolling_plan_for_prefix_consumption
        macro_plan, persisted_plan, prefix_scope = await load_rolling_plan_for_prefix_consumption(
            session, settings, project=project, raw_macro=raw_macro, raw_plan=raw_plan,
            source_snapshot_hash=snapshot.source_hash)
    except (TypeError, ValueError) as exc:
        metadata.update(
            {
                "rolling_outline_status": "needs_replan",
                "rolling_outline_integrity_error": str(exc),
                "planning_status": "needs_replan",
                "production_paused": True,
                "production_pause_reason": "rolling_outline_invalid",
                "generation_resume_blocked_until_repair_audit": True,
            }
        )
        project.metadata_json = metadata
        project.status = ProjectStatus.NEEDS_REPLAN.value
        await _checkpoint_commit(session)
        raise ProjectRepairPauseError(
            f"Rolling outline integrity check failed; replan is required. reason={exc}"
        ) from exc

    if prefix_scope is not None:
        return [chapter for chapter in chapters if chapter.chapter_number in prefix_scope]

    window_start = persisted_plan.window_start
    window_end = persisted_plan.window_end
    raw_schedule = metadata.get("rolling_outline_windows")
    if raw_schedule is None and macro_plan.total_chapters <= 10:
        schedule = [{"window_start": window_start, "window_end": window_end}]
    elif (
        isinstance(raw_schedule, Sequence)
        and not isinstance(raw_schedule, (str, bytes))
        and all(isinstance(item, Mapping) for item in raw_schedule)
        ):
        schedule = [dict(item) for item in raw_schedule]
        if metadata.get("rolling_outline_windows_hash") != rolling_window_schedule_hash(schedule):
            schedule = []
    else:
        schedule = []
    expected_start = 1
    schedule_valid = bool(schedule)
    for index, item in enumerate(schedule):
        start = int(item.get("window_start") or 0)
        end = int(item.get("window_end") or 0)
        size = end - start + 1
        is_final_partial = end == macro_plan.total_chapters and 1 <= size < 6
        if start != expected_start or end < start or (not 6 <= size <= 10 and not is_final_partial):
            schedule_valid = False
            break
        expected_start = end + 1
    if expected_start != macro_plan.total_chapters + 1:
        schedule_valid = False
    if not schedule_valid:
        metadata.update(
            {
                "rolling_outline_status": "needs_replan",
                "planning_status": "needs_replan",
                "production_paused": True,
                "production_pause_reason": "rolling_window_schedule_invalid",
                "generation_resume_blocked_until_repair_audit": True,
            }
        )
        project.metadata_json = metadata
        project.status = ProjectStatus.NEEDS_REPLAN.value
        await _checkpoint_commit(session)
        raise ProjectRepairPauseError(
            "Rolling outline execution schedule is invalid; replan is required."
        )
    current_chapter = max(0, int(getattr(project, "current_chapter_number", 0) or 0))
    if current_chapter >= window_end and window_end < macro_plan.total_chapters:
        next_start = current_chapter + 1
        next_window = next(
            (item for item in schedule if int(item.get("window_start") or 0) == next_start),
            None,
        )
        if next_window is None:
            metadata.update(
                {
                    "rolling_outline_status": "needs_replan",
                    "planning_status": "needs_replan",
                    "production_paused": True,
                    "production_pause_reason": "rolling_window_schedule_discontinuity",
                    "generation_resume_blocked_until_repair_audit": True,
                }
            )
            project.metadata_json = metadata
            project.status = ProjectStatus.NEEDS_REPLAN.value
            await _checkpoint_commit(session)
            raise ProjectRepairPauseError(
                "Rolling outline execution schedule does not continue from current state."
            )
        window_size = int(next_window["window_end"]) - next_start + 1
        state_model = await session.scalar(
            select(ChapterStateSnapshotModel)
            .where(
                ChapterStateSnapshotModel.project_id == project.id,
                ChapterStateSnapshotModel.chapter_number <= current_chapter,
            )
            .order_by(ChapterStateSnapshotModel.chapter_number.desc())
            .limit(1)
        )
        state_snapshot = {
            "current_chapter": current_chapter,
            "facts": dict(getattr(state_model, "facts", None) or {}),
        }
        next_plan = promote_rolling_outline(
            build_rolling_outline_plan(
                macro_plan,
                current_state_snapshot=state_snapshot,
                next_macro_anchor=(
                    macro_plan.slots[next_start + window_size - 1].to_dict()
                    if next_start + window_size - 1 < macro_plan.total_chapters
                    else "book_complete"
                ),
                source_snapshot_hash=snapshot.source_hash,
                window_start=next_start,
                window_size=window_size,
                batch_size=int(getattr(settings.pipeline, "rolling_outline_batch_size", 4) or 4),
                confirmed_chapters=tuple(range(1, current_chapter + 1)),
                previous_state_snapshot=raw_plan.get("current_state_snapshot")
                if isinstance(raw_plan.get("current_state_snapshot"), Mapping)
                else {},
            ),
            "approved",
        )
        raw_plan = next_plan.to_dict()
        metadata["rolling_outline_plan"] = raw_plan
        metadata["rolling_outline_status"] = "approved"
        project.metadata_json = metadata
        window_start, window_end = next_plan.window_start, next_plan.window_end

    selected: list[ChapterModel] = []
    for chapter in chapters:
        chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
        chapter_metadata = dict(getattr(chapter, "metadata_json", None) or {})
        if window_start <= chapter_number <= window_end:
            chapter_metadata["rolling_outline_status"] = "approved"
            selected.append(chapter)
        elif chapter_number > window_end:
            chapter_metadata["rolling_outline_status"] = "macro_only"
        chapter.metadata_json = chapter_metadata
    selected_numbers = {int(getattr(chapter, "chapter_number", 0) or 0) for chapter in selected}
    expected_numbers = set(range(window_start, window_end + 1))
    if selected_numbers != expected_numbers:
        missing = expected_numbers - selected_numbers
        materialized_frontier = max(
            (int(getattr(chapter, "chapter_number", 0) or 0) for chapter in chapters),
            default=0,
        )
        metadata = dict(getattr(project, "metadata_json", None) or {})
        # Advancing off the end of what has been planned is *progress*, not a
        # broken architecture. The window promoted above moves to the next
        # range as soon as the previous one is written, so on every multi-window
        # book there is a moment where the new window exists and its chapters do
        # not yet. Marking that ``needs_replan`` poisoned the project: the
        # planner lane in self-heal only runs while zero drafts are committed,
        # and the prose lane refuses any project whose architecture is rejected,
        # so a book that had just written 8 good chapters became permanently
        # inert with nothing but a once-a-minute skip log
        # (2026-08-03, xianxia-upgrade-1785697772).
        #
        # A hole *below* the frontier is the real defect this branch was written
        # for, and it still takes the replan path.
        if missing == expected_numbers and min(missing) > materialized_frontier:
            metadata["rolling_window_pending_materialization"] = sorted(missing)
            metadata.pop("rolling_window_missing_chapters", None)
            project.metadata_json = metadata
            await _checkpoint_commit(session)
            raise ProjectRepairPauseError(
                "Rolling outline advanced to chapters "
                f"{min(missing)}-{max(missing)}, which are not planned yet; "
                "the next window must be planned before writing continues."
            )
        metadata.update(
            {
                "rolling_outline_status": "needs_replan",
                "planning_status": "needs_replan",
                "production_paused": True,
                "production_pause_reason": "rolling_window_not_materialized",
                "generation_resume_blocked_until_repair_audit": True,
                "rolling_window_missing_chapters": sorted(missing),
            }
        )
        project.metadata_json = metadata
        project.status = ProjectStatus.NEEDS_REPLAN.value
        await _checkpoint_commit(session)
        raise ProjectRepairPauseError(
            "Approved rolling outline window is not fully materialized; replan is required."
        )
    return selected


def _strengthen_golden_three_hype_assignments(
    chapters: list[ChapterModel],
    *,
    min_intensity: float = 8.0,
) -> int:
    """Compatibility no-op: quality gates must never invent story content.

    Weak hype assignments are planning defects.  Replacing them with a generic
    ``reversal/8.0`` value only makes the schema look complete and prevents the
    planner from seeing that the underlying event still needs to be rewritten.
    """

    del chapters, min_intensity
    return 0


def _backfill_golden_three_visible_losses(
    chapters: list[ChapterModel], *, low_pressure: bool = False
) -> int:
    """Compatibility no-op: a gate reports missing stakes instead of fabricating them."""

    del chapters, low_pressure
    return 0


def _record_commercial_planning_readiness_gate(
    project: ProjectModel,
    *,
    chapters: list[ChapterModel],
    package_root: Path | None = None,
    long_serial_min_chapters: int = 50,
) -> dict[str, Any] | None:
    if not project_uses_signing_quality_gate(project):
        return None
    if int(getattr(project, "target_chapters", 0) or 0) < long_serial_min_chapters:
        return None
    if package_root is not None:
        write_commercial_package_sidecars(project, [], package_root)
    hype_repair_count = 0
    visible_loss_repair_count = 0
    report = evaluate_commercial_planning_readiness(
        [_chapter_probe_from_model(chapter) for chapter in chapters],
        target_chapters=int(getattr(project, "target_chapters", 0) or 0),
        package_root=package_root,
        long_serial_min_chapters=long_serial_min_chapters,
        genre=getattr(project, "genre", None),
        sub_genre=getattr(project, "sub_genre", None),
    )
    payload = commercial_planning_readiness_report_to_dict(report)
    project.metadata_json = {
        **(getattr(project, "metadata_json", None) or {}),
        "commercial_planning_readiness_report": payload,
        "commercial_planning_readiness_status": (
            "planned_gate_passed" if report.passed else "planned_gate_failed"
        ),
        "commercial_planning_hype_repair_count": hype_repair_count,
        "commercial_planning_visible_loss_repair_count": visible_loss_repair_count,
    }
    return payload


def _commercial_planning_issue_codes_from_payload(
    payload: Mapping[str, Any] | None,
    *,
    key: str,
    critical_only: bool,
) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    issues = payload.get(key)
    if not isinstance(issues, list):
        return []
    codes: list[str] = []
    for item in issues:
        if not isinstance(item, Mapping):
            continue
        severity = str(item.get("severity") or "").strip().lower()
        if critical_only and severity not in {
            "critical",
            "block",
            "blocking",
            "blocker",
        }:
            continue
        code = str(item.get("code") or item.get("issue_code") or "").strip()
        if code:
            codes.append(code)
    return codes


# (2026-08-02) Only structural gaps remain actionable blockers. The retention
# opinions — "the opening lacks live pressure", "the golden finger is not
# visible in the first three chapters", "the scene chain is solo" — are editing
# judgements, and they were deterministic: the repair rounds re-rolled against
# the same verdict and the book then died before writing a word. They stay in
# the report as advisory findings and ride the quality-debt record instead.
_ACTIONABLE_COMMERCIAL_PLANNING_BLOCK_CODES = {
    "missing_opening_situation",
}


def _commercial_planning_has_actionable_blockers(
    report_payload: Mapping[str, Any] | None,
) -> bool:
    codes = set(
        _commercial_planning_issue_codes_from_payload(
            report_payload,
            key="findings",
            critical_only=False,
        )
    )
    return bool(codes & _ACTIONABLE_COMMERCIAL_PLANNING_BLOCK_CODES)


def _commercial_planning_llm_judge_should_block(judge_result: Any) -> bool:
    if bool(getattr(judge_result, "passed", False)):
        return False
    blocking_issues = getattr(judge_result, "blocking_issues", ()) or ()
    return bool(blocking_issues)


def _commercial_planning_readiness_error_message(
    report_payload: dict[str, Any],
    *,
    llm_judge_payload: Mapping[str, Any] | None = None,
) -> str:
    codes = _commercial_planning_issue_codes_from_payload(
        report_payload,
        key="findings",
        critical_only=True,
    )
    llm_codes = _commercial_planning_issue_codes_from_payload(
        llm_judge_payload,
        key="blocking_issues",
        critical_only=False,
    )
    codes.extend(f"llm:{code}" for code in llm_codes)
    suffix = ", ".join(codes) if codes else "unknown"
    # Carry evidence/required_fix inside the exception text: the metadata write
    # that also holds this payload is rolled back by this very raise, so the
    # task error message is the only forensic trail that survives (learned
    # 2026-07-16 when a killed book left zero auditable evidence).
    detail_lines: list[str] = []
    for issue in (llm_judge_payload or {}).get("blocking_issues", ())[:4]:
        if not isinstance(issue, Mapping):
            continue
        _ev = str(issue.get("evidence") or "").strip()[:160]
        _fx = str(issue.get("required_fix") or "").strip()[:160]
        detail_lines.append(f"- {issue.get('code')}: {_ev}" + (f" → 整改: {_fx}" if _fx else ""))
    detail = ("\n" + "\n".join(detail_lines)) if detail_lines else ""
    return f"Commercial planning readiness gate failed: {suffix}{detail}"


async def _load_chapter_draft_for_pipeline_result(
    session: AsyncSession,
    chapter_result: ChapterPipelineResult,
) -> ChapterDraftVersionModel | None:
    if chapter_result.chapter_draft_id is not None:
        draft = await session.get(ChapterDraftVersionModel, chapter_result.chapter_draft_id)
        if draft is not None:
            return draft
    return await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id == chapter_result.chapter_id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )


def _qimao_opening_gate_error_message(report_payload: dict[str, Any]) -> str:
    findings = report_payload.get("findings")
    codes: list[str] = []
    if isinstance(findings, list):
        codes = [
            str(item.get("code"))
            for item in findings
            if isinstance(item, dict) and item.get("severity") == "critical"
        ]
    suffix = ", ".join(codes) if codes else "unknown"
    return f"Qimao opening gate failed: {suffix}"


def _clear_gate_state(metadata: Mapping[str, Any] | None, *keys: str) -> dict[str, Any]:
    """Drop stale terminal flags after a later evaluation of that gate passes."""

    cleaned = dict(metadata or {})
    for key in keys:
        cleaned.pop(key, None)
    return cleaned


def _project_uses_whole_book_quality_gate(project: ProjectModel) -> bool:
    metadata = getattr(project, "metadata_json", None) or {}
    return metadata.get("whole_book_quality_gate_disabled") is not True


def _chapter_review_blocks_for_project(
    project: ProjectModel,
    settings: AppSettings,
) -> bool:
    """Effective chapter-review hard-block, with a per-project escape hatch.

    By default chapter-review failures hard-block (``chapter_review_block_on_failure``)
    so a rejected chapter never auto-completes via accept-on-stall. A project may
    opt out by setting metadata ``chapter_review_warn_only: true`` — mirroring the
    existing ``whole_book_quality_gate_warn_only`` per-project override. This lets a
    compressed / finale project finalize the best *safe* draft (review + rewrites
    still run up to the limit; only the terminal hard-block is relaxed) instead of
    stalling in REVISION when the strict chapter critic will not converge.
    """
    default = bool(getattr(settings.pipeline, "chapter_review_block_on_failure", True))
    metadata = getattr(project, "metadata_json", None)
    if isinstance(metadata, Mapping) and metadata.get("chapter_review_warn_only") is True:
        return False
    return default


def _retention_gate_blocks_for_project(
    project: ProjectModel,
    settings: AppSettings,
) -> bool:
    """Effective reader-retention / persona-gate hard-block, with an escape hatch.

    When the retention auto-repair budget is exhausted on a chapter the writer
    model structurally cannot clear (e.g. ``PERSONA_WEIGHTED_SCORE_LOW`` — the gate's
    0.62 weighted-score bar sits above the model's ~0.51 ceiling per
    ``reader_persona_calibration``), the legacy behaviour hard-routes the chapter to
    machine-repair and pauses the whole book. Soft by default
    (``retention_safety_gate_block_on_failure=False``): accept the best draft on-stall,
    flag it, and ADVANCE — mirroring ``_chapter_review_blocks_for_project``. A project
    may force either mode via metadata ``retention_safety_gate_warn_only: true``.
    """
    default = bool(getattr(settings.pipeline, "retention_safety_gate_block_on_failure", False))
    metadata = getattr(project, "metadata_json", None)
    if isinstance(metadata, Mapping) and metadata.get("retention_safety_gate_warn_only") is True:
        return False
    return default


def _block_codes_are_retention_only(codes: tuple[str, ...]) -> bool:
    """True when every remaining block code is a reader-retention code.

    Used by the post-repair soft fuse: structural/text-integrity codes
    (splice contradictions, duplicate paragraphs, …) must keep hard-blocking,
    but retention-quality codes the writer model has already failed to clear
    across the full repair budget follow the soft retention gate instead of
    dead-ending the book in machine repair.
    """

    if not codes:
        return False
    try:
        from bestseller.services.retention_safety_gate import (
            AUTO_REPAIR_RETENTION_CODES,
            RETENTION_AUDIT_SOFT_CODES,
        )
    except Exception:
        return False
    retention_set = set(AUTO_REPAIR_RETENTION_CODES) | RETENTION_AUDIT_SOFT_CODES
    return all(code in retention_set for code in codes)


_WHOLE_BOOK_QUALITY_GATE_AUTO_WARN_ONLY_CODES = frozenset(
    {
        "chapter_hook_missing",
        "volume_momentum_drop",
    }
)


def _whole_book_quality_gate_finding_codes(report_payload: dict[str, Any]) -> list[str]:
    findings = report_payload.get("findings")
    if not isinstance(findings, list):
        return []
    codes: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity") or "").lower()
        if severity not in {"high", "critical"}:
            continue
        code = str(finding.get("code") or "").strip()
        if code:
            codes.append(code)
    return sorted(set(codes))


def _whole_book_quality_gate_can_warn_only(report_payload: dict[str, Any]) -> bool:
    codes = _whole_book_quality_gate_finding_codes(report_payload)
    if not codes:
        return False
    return all(code in _WHOLE_BOOK_QUALITY_GATE_AUTO_WARN_ONLY_CODES for code in codes)


def _whole_book_quality_gate_error_message(report_payload: dict[str, Any]) -> str:
    findings = report_payload.get("findings")
    codes: list[str] = []
    if isinstance(findings, list):
        codes = [
            str(item.get("code"))
            for item in findings
            if isinstance(item, dict) and item.get("severity") in {"critical", "high"}
        ]
    suffix = ", ".join(codes) if codes else "unknown"
    return f"Whole-book quality gate failed: {suffix}"


async def _enforce_qimao_opening_gate_after_chapter(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_result: ChapterPipelineResult,
    opening_texts: dict[int, str],
    workflow_run: WorkflowRunModel,
    settings: AppSettings,
    progress: ProgressCallback | None,
) -> None:
    if not project_uses_signing_quality_gate(project):
        project.metadata_json = _clear_gate_state(
            project.metadata_json,
            "opening_quality_gate_blocked",
            "qimao_opening_gate_blocked",
            "qimao_opening_gate_exhausted",
            "last_qimao_opening_gate_error",
            "qimao_opening_gate_error",
            "production_pause_reason",
            "last_generation_gate_reason",
        )
        workflow_run.metadata_json = _clear_gate_state(
            workflow_run.metadata_json,
            "qimao_opening_gate_blocked",
            "qimao_opening_gate_exhausted",
            "qimao_opening_gate_error",
        )
        return
    if chapter.chapter_number > 3:
        return
    metadata = getattr(project, "metadata_json", None) or {}
    opening_contract = metadata.get("opening_quality_contract") or metadata.get(
        "qimao_opening_contract"
    )
    if not isinstance(opening_contract, dict) or not opening_contract:
        return

    chapter_draft = await _load_chapter_draft_for_pipeline_result(session, chapter_result)
    if chapter_draft is None or not getattr(chapter_draft, "content_md", None):
        return
    opening_texts[chapter.chapter_number] = chapter_draft.content_md or ""
    if chapter.chapter_number not in {1, 3}:
        return
    if chapter.chapter_number == 1:
        gate_texts = {1: opening_texts[1]}
    else:
        if not all(number in opening_texts for number in (1, 2, 3)):
            return
        gate_texts = {number: opening_texts[number] for number in (1, 2, 3)}

    protagonist_name = opening_contract.get("protagonist_name")
    report = evaluate_qimao_opening_gate(
        gate_texts,
        opening_contract=opening_contract,
        protagonist_name=str(protagonist_name) if protagonist_name else None,
    )
    report_payload = qimao_opening_gate_report_to_dict(report)
    project.metadata_json = {
        **(getattr(project, "metadata_json", None) or {}),
        "opening_quality_gate_report": report_payload,
        "opening_quality_gate_reports": [
            *(
                (getattr(project, "metadata_json", None) or {}).get("opening_quality_gate_reports")
                or []
            ),
            {"chapter_number": chapter.chapter_number, **report_payload},
        ],
        "qimao_opening_gate_report": report_payload,
        "qimao_opening_gate_reports": [
            *(
                (getattr(project, "metadata_json", None) or {}).get("qimao_opening_gate_reports")
                or []
            ),
            {"chapter_number": chapter.chapter_number, **report_payload},
        ],
    }
    workflow_run.metadata_json = {
        **(workflow_run.metadata_json or {}),
        "opening_quality_gate_report": report_payload,
        "qimao_opening_gate_report": report_payload,
    }

    if report.passed:
        project.metadata_json = _clear_gate_state(
            project.metadata_json,
            "opening_quality_gate_blocked",
            "qimao_opening_gate_blocked",
            "qimao_opening_gate_exhausted",
            "last_qimao_opening_gate_error",
            "qimao_opening_gate_error",
            "production_pause_reason",
            "last_generation_gate_reason",
        )
        workflow_run.metadata_json = _clear_gate_state(
            workflow_run.metadata_json,
            "qimao_opening_gate_blocked",
            "qimao_opening_gate_exhausted",
            "qimao_opening_gate_error",
        )
        _emit_progress(
            progress,
            "qimao_opening_gate_passed",
            {"project_slug": project.slug, "chapter_number": chapter.chapter_number},
        )
        return

    # ── 就地有界重写闭环（正向流消费者）──────────────────────────────────────
    # 软失败时下方会 queue 一个 RewriteTaskModel，但自主正向写作流里没有消费者，
    # 于是被判失败的开篇会原样带跑全书（真机 ch1）。这里仿 deslop 闭环，先做一轮
    # 有界就地重写 + 复检：若重写让门禁通过，直接落库、当作通过返回，不再 queue 死
    # 任务、不阻断。重写永不抛/永不返回更短稿；失败则照旧走下方 queue 兜底。
    if getattr(settings.pipeline, "qimao_opening_inline_revise_enabled", True):
        try:
            from bestseller.services.opening_revise import revise_opening_qimao

            _instructions = build_qimao_opening_rewrite_instructions(
                report.findings,
                chapter_number=chapter.chapter_number,
                opening_contract=opening_contract,
                rejection_reasons=None,
            )
            _revised = await revise_opening_qimao(
                session,
                settings,
                content=chapter_draft.content_md or "",
                instructions=_instructions,
                project_id=project.id,
                workflow_run_id=workflow_run.id,
            )
            if _revised and _revised != (chapter_draft.content_md or ""):
                _recheck_texts = dict(gate_texts)
                _recheck_texts[chapter.chapter_number] = _revised
                _recheck = evaluate_qimao_opening_gate(
                    _recheck_texts,
                    opening_contract=opening_contract,
                    protagonist_name=str(protagonist_name) if protagonist_name else None,
                )
                if _recheck.passed:
                    chapter_draft.content_md = _revised
                    resync_draft_word_count(chapter_draft, language=project.language or "zh-CN")
                    opening_texts[chapter.chapter_number] = _revised
                    await session.flush()
                    _emit_progress(
                        progress,
                        "qimao_opening_gate_inline_revise_passed",
                        {
                            "project_slug": project.slug,
                            "chapter_number": chapter.chapter_number,
                        },
                    )
                    return
        except Exception:
            logger.warning(
                "qimao_opening_gate ch%d: inline revise failed; falling back to queue",
                chapter.chapter_number,
                exc_info=True,
            )

    max_attempts = max(
        1,
        int(getattr(settings.pipeline, "qimao_opening_max_attempts", 3) or 3),
    )
    current_metadata = dict(getattr(project, "metadata_json", None) or {})
    attempts_by_chapter = dict(current_metadata.get("qimao_opening_gate_attempts_by_chapter") or {})
    attempt_key = str(chapter.chapter_number)
    attempt_count = int(attempts_by_chapter.get(attempt_key) or 0) + 1
    attempts_by_chapter[attempt_key] = attempt_count
    project.metadata_json = {
        **current_metadata,
        "opening_quality_gate_blocked": True,
        "qimao_opening_gate_blocked": True,
        "qimao_opening_gate_attempts_by_chapter": attempts_by_chapter,
        "qimao_opening_gate_attempt_count": attempt_count,
        "qimao_opening_max_attempts": max_attempts,
    }
    workflow_run.metadata_json = {
        **(workflow_run.metadata_json or {}),
        "qimao_opening_gate_blocked": True,
        "qimao_opening_gate_attempt_count": attempt_count,
        "qimao_opening_max_attempts": max_attempts,
    }

    if attempt_count >= max_attempts:
        error_message = _qimao_opening_gate_error_message(report_payload)
        _qimao_hard_block = bool(
            getattr(settings.pipeline, "qimao_opening_gate_block_on_failure", False)
        )
        # Exhaustion is a chapter-level human-review flag in the default soft
        # mode, not a project-wide structural pause.  The old code stamped
        # ``production_paused`` unconditionally and then claimed it would
        # continue; worker startup interpreted that stale flag as a repair
        # gate and repeatedly re-queued the same self-heal job for hours.
        project.status = (
            ProjectStatus.PAUSED.value if _qimao_hard_block else ProjectStatus.WRITING.value
        )
        chapter.status = ChapterStatus.REVISION.value
        chapter.production_state = "needs_human_review"
        _qimao_metadata = {
            **(project.metadata_json or {}),
            "qimao_opening_gate_exhausted": True,
            "last_qimao_opening_gate_error": error_message,
        }
        if _qimao_hard_block:
            _qimao_metadata.update(
                {
                    "production_paused": True,
                    "production_pause_reason": "qimao_opening_gate_exhausted",
                    "last_generation_gate_reason": "qimao_opening_gate_exhausted",
                    "last_generation_gate_error": error_message,
                }
            )
        else:
            for _key in (
                "production_paused",
                "production_pause_reason",
                "last_generation_gate_reason",
                "last_generation_gate_error",
                "generation_resume_blocked_until_repair_audit",
            ):
                _qimao_metadata.pop(_key, None)
        project.metadata_json = _qimao_metadata
        workflow_run.metadata_json = {
            **(workflow_run.metadata_json or {}),
            "requires_human_review": True,
            "qimao_opening_gate_exhausted": True,
            "qimao_opening_gate_error": error_message,
        }
        _emit_progress(
            progress,
            "qimao_opening_gate_exhausted",
            {
                "project_slug": project.slug,
                "chapter_number": chapter.chapter_number,
                "attempt_count": attempt_count,
                "max_attempts": max_attempts,
                "findings": report_payload.get("findings", []),
            },
        )
        await session.flush()
        # Soft by default: the opening is flagged for human review and the
        # chapter marked needs_human_review, but the autonomous run continues to
        # the next chapter instead of dying. Only the legacy worker-retry mode
        # (qimao_opening_gate_block_on_failure=True) hard-aborts here.
        if _qimao_hard_block:
            raise ValueError(error_message)
        return

    rejection_reasons = (
        metadata.get("editor_rejection_reasons")
        or metadata.get("rejection_reasons")
        or metadata.get("rejection_reason")
    )
    strategy = qimao_opening_rewrite_strategy_for_findings(report.findings)
    rewrite_task = RewriteTaskModel(
        project_id=project.id,
        trigger_type="qimao_opening_gate",
        trigger_source_id=chapter.id,
        rewrite_strategy=strategy,
        priority=1,
        status="pending",
        instructions=build_qimao_opening_rewrite_instructions(
            report.findings,
            chapter_number=chapter.chapter_number,
            opening_contract=opening_contract,
            rejection_reasons=str(rejection_reasons) if rejection_reasons else None,
        ),
        context_required=[
            "opening_quality_contract",
            "current_chapter_draft",
            "qimao_opening_gate_findings",
        ],
        metadata_json={
            **rewrite_source_binding(chapter_draft, chapter=chapter),
            "chapter_id": str(chapter.id),
            "chapter_number": chapter.chapter_number,
            "chapter_draft_id": str(chapter_draft.id),
            "opening_quality_gate_report": report_payload,
            "opening_quality_contract": opening_contract,
            "qimao_opening_gate_report": report_payload,
            "qimao_opening_contract": opening_contract,
        },
    )
    session.add(rewrite_task)
    workflow_run.metadata_json = {
        **(workflow_run.metadata_json or {}),
        "qimao_opening_gate_blocked": True,
        "qimao_opening_rewrite_strategy": strategy,
    }
    _emit_progress(
        progress,
        "qimao_opening_gate_failed",
        {
            "project_slug": project.slug,
            "chapter_number": chapter.chapter_number,
            "findings": report_payload.get("findings", []),
            "rewrite_strategy": strategy,
        },
    )
    # Soft by default: the rewrite task is queued above for the worker/human to
    # pick up, but the autonomous run continues instead of dying on a weak hook.
    # The hard raise only suits the worker self-heal retry loop and is opt-in via
    # qimao_opening_gate_block_on_failure (framework self-harm fix).
    if getattr(settings.pipeline, "qimao_opening_gate_block_on_failure", False):
        raise ValueError(_qimao_opening_gate_error_message(report_payload))


async def _enforce_whole_book_quality_gate_after_chapter(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_result: ChapterPipelineResult,
    chapter_texts: dict[int, str],
    workflow_run: WorkflowRunModel,
    progress: ProgressCallback | None,
    settings: AppSettings | None = None,
) -> None:
    if not _project_uses_whole_book_quality_gate(project):
        return

    chapter_draft = await _load_chapter_draft_for_pipeline_result(session, chapter_result)
    if chapter_draft is None or not getattr(chapter_draft, "content_md", None):
        return

    from bestseller.services.story_enhancers import resolve_cost_style
    from bestseller.services.planning_capability_selection import planning_capability_selected

    chapter_texts[chapter.chapter_number] = chapter_draft.content_md or ""
    metadata = getattr(project, "metadata_json", None) or {}
    report = evaluate_whole_book_quality(
        chapter_texts,
        volume_plan=metadata.get("volume_plan"),
        emotion_driven_kernel=metadata.get("emotion_driven_kernel")
            if planning_capability_selected(metadata, "emotion_driven_kernel") else None,
        # Without this the gate flags a 爽文无代价 book for the very thing the
        # user asked for — cost-free wins.
        cost_style=resolve_cost_style(metadata),
    )
    report_payload = whole_book_quality_report_to_dict(report)
    project.metadata_json = {
        **(getattr(project, "metadata_json", None) or {}),
        "whole_book_quality_report": report_payload,
        "whole_book_engagement_ledger": report_payload.get("ledger", []),
    }
    workflow_run.metadata_json = {
        **(workflow_run.metadata_json or {}),
        "whole_book_quality_report": report_payload,
    }
    if report.passed:
        project.metadata_json = _clear_gate_state(
            project.metadata_json,
            "whole_book_quality_gate_blocked",
            "whole_book_quality_gate_block_codes",
            "whole_book_quality_gate_codes",
            "whole_book_quality_gate_strategy",
            "whole_book_quality_gate_warning",
            "whole_book_quality_gate_warning_codes",
            "whole_book_quality_gate_warning_scope",
        )
        workflow_run.metadata_json = _clear_gate_state(
            workflow_run.metadata_json,
            "whole_book_quality_gate_blocked",
            "whole_book_quality_gate_codes",
            "whole_book_quality_rewrite_strategy",
            "whole_book_quality_gate_warning",
        )
        await session.execute(
            update(RewriteTaskModel)
            .where(
                RewriteTaskModel.project_id == project.id,
                RewriteTaskModel.trigger_type == "whole_book_quality_gate",
                RewriteTaskModel.status.in_(("pending", "queued")),
            )
            .values(
                status="superseded",
                metadata_json={
                    "superseded_reason": "later_whole_book_quality_gate_passed",
                    "passed_chapter_number": chapter.chapter_number,
                },
            )
        )
        _emit_progress(
            progress,
            "whole_book_quality_gate_passed",
            {"project_slug": project.slug, "chapter_number": chapter.chapter_number},
        )
        return

    opening_contract = metadata.get("opening_quality_contract") or metadata.get(
        "qimao_opening_contract"
    )
    finding_codes = _whole_book_quality_gate_finding_codes(report_payload)
    warn_only = metadata.get(
        "whole_book_quality_gate_warn_only"
    ) is True or _whole_book_quality_gate_can_warn_only(report_payload)
    strategy = whole_book_quality_strategy_for_findings(report.findings)
    rewrite_task = RewriteTaskModel(
        project_id=project.id,
        trigger_type="whole_book_quality_gate",
        trigger_source_id=chapter.id,
        rewrite_strategy=strategy,
        priority=2,
        status="pending",
        instructions=build_whole_book_quality_rewrite_instructions(
            report.findings,
            chapter_number=chapter.chapter_number,
            opening_quality_contract=(
                opening_contract if isinstance(opening_contract, dict) else None
            ),
        ),
        context_required=[
            "whole_book_engagement_ledger",
            "current_chapter_draft",
            "whole_book_quality_findings",
        ],
        metadata_json={
            **rewrite_source_binding(chapter_draft, chapter=chapter),
            "chapter_id": str(chapter.id),
            "chapter_number": chapter.chapter_number,
            "chapter_draft_id": str(chapter_draft.id),
            "whole_book_quality_report": report_payload,
            "whole_book_engagement_ledger": report_payload.get("ledger", []),
        },
    )
    session.add(rewrite_task)
    project_metadata = {**(project.metadata_json or {})}
    project_metadata["whole_book_quality_gate_codes"] = finding_codes
    project_metadata["whole_book_quality_gate_strategy"] = strategy
    workflow_metadata = {
        **(workflow_run.metadata_json or {}),
        "whole_book_quality_rewrite_strategy": strategy,
        "whole_book_quality_gate_codes": finding_codes,
    }
    if warn_only:
        project_metadata["whole_book_quality_gate_warning_codes"] = finding_codes
        project_metadata["whole_book_quality_gate_warning_count"] = (
            int(project_metadata.get("whole_book_quality_gate_warning_count", 0)) + 1
        )
        project_metadata["whole_book_quality_gate_warning_scope"] = (
            "auto_recoverable"
            if (
                metadata.get("whole_book_quality_gate_warn_only") is not True
                and _whole_book_quality_gate_can_warn_only(report_payload)
            )
            else "manual"
        )
        project_metadata["whole_book_quality_gate_warning"] = True
        workflow_metadata["whole_book_quality_gate_warning"] = True
    else:
        project_metadata["whole_book_quality_gate_block_codes"] = finding_codes
        project_metadata["whole_book_quality_gate_block_count"] = (
            int(project_metadata.get("whole_book_quality_gate_block_count", 0)) + 1
        )
        project_metadata["whole_book_quality_gate_blocked"] = True
        workflow_metadata["whole_book_quality_gate_blocked"] = True
    project.metadata_json = project_metadata
    workflow_run.metadata_json = workflow_metadata
    _emit_progress(
        progress,
        "whole_book_quality_gate_warning" if warn_only else "whole_book_quality_gate_failed",
        {
            "project_slug": project.slug,
            "chapter_number": chapter.chapter_number,
            "findings": report_payload.get("findings", []),
            "rewrite_strategy": strategy,
        },
    )
    if warn_only:
        return
    # Soft by default (autonomous closure): the rewrite task is queued above; flag
    # the chapter and continue instead of killing the whole book on a weak
    # engagement reading. The bare raise only suits the worker self-heal retry
    # loop and is opt-in via whole_book_quality_gate_block_on_failure.
    _block = settings is not None and getattr(
        settings.pipeline, "whole_book_quality_gate_block_on_failure", False
    )
    if _block:
        raise ValueError(_whole_book_quality_gate_error_message(report_payload))


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
    if (getattr(project_payload, "metadata", None) or {}).get("creation_configuration") is not None:
        return True
    if settings.pipeline.progressive_planning:
        return True
    target_chapters = int(getattr(project_payload, "target_chapters", 0) or 0)
    if getattr(settings.pipeline, "enable_rolling_outline", True) and target_chapters > int(
        getattr(settings.pipeline, "rolling_outline_window_size", 8) or 8
    ):
        return True
    return target_chapters >= PROGRESSIVE_CHAPTER_THRESHOLD


def _expand_volume_plan_into_rolling_windows(
    volume_plan: Sequence[Mapping[str, Any]],
    *,
    window_size: int,
) -> list[dict[str, Any]]:
    """Split execution into detail windows without changing narrative volumes."""

    if not 6 <= window_size <= 10:
        raise ValueError("rolling outline window_size must be between 6 and 10")
    windows: list[dict[str, Any]] = []
    cursor = 1
    for index, raw in enumerate(volume_plan, start=1):
        entry = dict(raw)
        volume_number = int(entry.get("volume_number") or index)
        count = int(entry.get("chapter_count_target") or 0)
        if count <= 0:
            raise ValueError(f"volume {volume_number} has no chapter_count_target")
        start = int(entry.get("start_chapter_number") or cursor)
        original_end = start + count - 1
        minimum_windows = math.ceil(count / 10)
        maximum_windows = count // 6
        is_final_volume = index == len(volume_plan)
        if count < 6 and not is_final_volume:
            raise ValueError(
                f"non-final volume {volume_number} has fewer than 6 chapters; "
                "the narrative volume plan must be rebalanced"
            )
        if count <= 10:
            sizes = [count]
        elif minimum_windows > maximum_windows:
            if is_final_volume and count == 11:
                sizes = [6, 5]
            else:
                raise ValueError(
                    f"volume {volume_number} chapter count {count} cannot be split into "
                    "6-10 chapter rolling windows"
                )
        else:
            window_count = min(
                maximum_windows,
                max(minimum_windows, round(count / window_size)),
            )
            base, remainder = divmod(count, window_count)
            sizes = [base + (1 if idx < remainder else 0) for idx in range(window_count)]
        window_count = len(sizes)
        window_start = start
        for window_index, current_size in enumerate(sizes, start=1):
            window_end = window_start + current_size - 1
            windows.append(
                {
                    **entry,
                    "volume_number": volume_number,
                    "chapter_count_target": window_end - window_start + 1,
                    "start_chapter_number": window_start,
                    "end_chapter_number": window_end,
                    "chapter_range": [window_start, window_end],
                    "rolling_window_index": window_index,
                    "rolling_window_count": window_count,
                    "narrative_volume_chapter_count": count,
                    "narrative_volume_chapter_range": [start, original_end],
                }
            )
            window_start = window_end + 1
        cursor = original_end + 1
    return windows


_ROLLING_REPLAN_RESET_KEYS = (
    "macro_outline_plan",
    "rolling_outline_plan",
    "rolling_outline_windows",
    "rolling_outline_windows_hash",
    "rolling_outline_status",
    "rolling_outline_integrity_error",
)


def _reset_stale_rolling_outline_for_explicit_replan(
    metadata: Mapping[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Invalidate stale rolling authority only inside the dedicated replan lane."""

    updated = dict(metadata)
    previous_plan = updated.get("rolling_outline_plan")
    for key in _ROLLING_REPLAN_RESET_KEYS:
        updated.pop(key, None)
    updated["rolling_outline_replan_reset_reason"] = str(reason)[:1000]
    if isinstance(previous_plan, Mapping):
        updated["rolling_outline_replan_superseded"] = {
            "plan_hash": previous_plan.get("plan_hash"),
            "source_snapshot_hash": previous_plan.get("source_snapshot_hash"),
            "window_start": previous_plan.get("window_start"),
            "window_end": previous_plan.get("window_end"),
        }
    return updated


def _volume_plan_for_rolling_window(
    canonical_plan: Sequence[Mapping[str, Any]],
    window_entry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return full-book volume context with only the active volume detail-bounded."""

    active_volume = int(window_entry.get("volume_number") or 0)
    return [
        dict(window_entry) if int(item.get("volume_number") or 0) == active_volume else dict(item)
        for item in canonical_plan
    ]


def _build_progressive_macro_slots(
    volume_plan: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build full-book structural anchors before any detailed chapter planning."""

    slots: list[dict[str, Any]] = []
    cursor = 1
    for index, raw in enumerate(volume_plan, start=1):
        entry = dict(raw)
        volume_number = int(entry.get("volume_number") or index)
        count = int(entry.get("chapter_count_target") or 0)
        start = int(entry.get("start_chapter_number") or cursor)
        volume_goal = str(
            entry.get("volume_goal")
            or entry.get("goal")
            or entry.get("volume_theme")
            or f"第{volume_number}卷推进主线"
        ).strip()
        for local_index, chapter_number in enumerate(range(start, start + count), start=1):
            slots.append(
                {
                    "chapter_number": chapter_number,
                    "anchor": (f"第{volume_number}卷第{local_index}/{count}章：{volume_goal}"),
                    "metadata": {
                        "volume_number": volume_number,
                        "volume_title": entry.get("volume_title"),
                        "conflict_phase": entry.get("conflict_phase"),
                        "local_chapter_number": local_index,
                    },
                }
            )
        cursor = start + count
    return slots


def _collect_output_files(output_dir: Path) -> list[str]:
    if not output_dir.exists() or not output_dir.is_dir():
        return []
    return [
        str(path.resolve())
        for path in sorted(output_dir.iterdir(), key=lambda item: item.name)
        if path.is_file()
    ]


async def _apply_post_chapter_phase_b(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_md: str,
) -> None:
    """Run Phase B1+B2 classification and persist history.

    Controlled by ``phase_b_line_tracker.enabled`` in
    ``config/quality_gates.yaml``. A no-op when the flag is off; safe to
    call unconditionally from pipeline hooks.
    """

    try:
        cfg = get_quality_gates_config()
        if not cfg.phase_b.enabled:
            return
        language = getattr(project, "language", None) or "zh-CN"
        classification = classify_chapter_lines(
            chapter_md or "",
            chapter_no=chapter.chapter_number,
            language=language,
        )
        chapter.dominant_line = classification.dominant_line
        chapter.support_lines = list(classification.support_lines) or None
        chapter.line_intensity = (
            float(classification.line_intensity) if classification.line_intensity else None
        )
        project.metadata_json = persist_line_history(
            project.metadata_json,
            classification,
        )
        logger.info(
            "Phase B ch%d classified dominant=%s intensity=%.2f",
            chapter.chapter_number,
            classification.dominant_line,
            classification.line_intensity,
        )
    except Exception:
        logger.debug("Phase B classification failed (non-fatal)", exc_info=True)


async def _apply_post_chapter_phase_c(
    *,
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
) -> None:
    """Run Phase C3 debt interest accrual for the chapter tick — DB-backed.

    Controlled by ``phase_c_overrides.enabled``. The debts written by the
    override auto-sign path live in ``ChaseDebtModel`` rows (the durable
    source of truth across workers/runs), so accrual loads those rows,
    compounds their balances + flips overdue ones forward to
    ``chapter_number``, and persists the mutations via ``session.flush``.

    Idempotent per chapter: ``accrued_through_chapter`` catches up on the
    first call, so a repeat for the same chapter is a no-op. Honors the
    ``only_enforce_from_chapter`` gray-out and is a no-op when Phase C is
    off. Errors are logged and swallowed — a debt-ledger crash must never
    fail the chapter.
    """

    try:
        cfg = get_quality_gates_config()
        if not cfg.phase_c.enabled:
            return
        only_from = cfg.phase_c.only_enforce_from_chapter
        if only_from is not None and chapter_number < only_from:
            return
        rows = list(
            await session.scalars(
                select(ChaseDebtModel).where(
                    ChaseDebtModel.project_id == project_id,
                    ChaseDebtModel.status.in_(("active", "overdue")),
                )
            )
        )
        if not rows:
            return
        accrued, newly_overdue = accrue_debt_rows(rows, chapter_number)
        if accrued or newly_overdue:
            await session.flush()
            logger.info(
                "Phase C ch%d: accrued interest on %d debt(s), %d newly overdue",
                chapter_number,
                accrued,
                newly_overdue,
            )
    except Exception:
        logger.debug("Phase C accrual failed (non-fatal)", exc_info=True)


async def _collect_phase_d_reports(
    *,
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    snapshot: ChapterStateSnapshotModel | None,
    strict: bool = False,
    candidate_draft_id: UUID | None = None,
) -> list[Any]:
    """Return Phase D3 ``CheckerReport`` envelopes for the just-finalized chapter.

    Controlled by ``phase_d_time.enabled``; returns an empty list when the
    flag is off. ``snapshot`` is the row we just persisted — we load the
    previous chapter's snapshot and run the two pure validators against
    the pair. Errors are logged and swallowed.
    """

    if snapshot is not None:
        snapshot._phase_d_epistemic_context = None
    try:
        cfg = get_quality_gates_config()
        if not cfg.phase_d.enabled:
            return []
        if snapshot is None:
            if strict:
                raise ValueError("chapter_continuity_snapshot_missing")
            return []
        from bestseller.services.chapter_continuity_admission import read_phase_d_snapshot_context

        cur_ctx = await read_phase_d_snapshot_context(
            session, project_id=project_id, chapter_number=chapter_number,
            snapshot=snapshot, candidate_draft_id=candidate_draft_id,
        )
        snapshot._phase_d_epistemic_context = cur_ctx
        prev_ctx = await load_previous_chapter_snapshot(
            session,
            project_id=project_id,
            current_chapter_number=chapter_number,
        )
        reports: list[Any] = []
        if cfg.phase_d.countdown_arithmetic_enabled:
            reports.append(check_countdown_arithmetic(cur_ctx, prev_ctx))
        if cfg.phase_d.regression_check_enabled:
            reports.append(check_time_regression(cur_ctx, prev_ctx))
        return reports
    except Exception:
        if strict:
            raise
        logger.debug("Phase D validators failed (non-fatal)", exc_info=True)
        return []


async def _ensure_chapter_continuity_before_adoption(
    session: AsyncSession, settings: AppSettings, *, project_id: UUID,
    chapter: ChapterModel, draft: ChapterDraftVersionModel, workflow_run_id: UUID | None,
) -> None:
    """A hard timeline failure cannot be published and repaired afterwards."""
    if not get_quality_gates_config().phase_d.enabled:
        return
    from bestseller.services.chapter_continuity_admission import extract_continuity_admission_preview

    receipt = {
        "draft_id": str(draft.id),
        "body_sha256": chapter_review_body_fingerprint(draft.content_md or "")["digest"],
        "workflow_run_id": str(workflow_run_id) if workflow_run_id else None,
        "passed": False,
    }
    try:
        preview = await extract_continuity_admission_preview(
            session, settings, project_id=project_id, chapter=chapter,
            draft=draft, workflow_run_id=workflow_run_id,
        )
        reports = await _collect_phase_d_reports(
            session=session, project_id=project_id, chapter_number=chapter.chapter_number,
            snapshot=preview, strict=True, candidate_draft_id=draft.id,
        )
        checked_context = getattr(preview, "_phase_d_epistemic_context", None)
        if checked_context is not None:
            receipt["epistemic_view_sha256"] = checked_context.epistemic_view_sha256
            receipt["verified_fact_count"] = len(checked_context.facts)
            receipt["reported_observation_count"] = len(checked_context.reported_observations)
            receipt["epistemic_truth_approved"] = False
        contract = (preview.facts or {}).get("snapshot_contract", {})
        receipt["snapshot_dependencies"] = contract.get("snapshot_dependencies")
        receipt["preview_raw_sha256"] = hashlib.sha256((preview.raw_extraction or "").encode()).hexdigest()
        receipt["reports"] = [_checker_report_gate_payload(report) for report in reports]
        receipt["passed"] = not any(report.blocks_write for report in reports)
        receipt["status"] = "passed" if receipt["passed"] else "hard_failure"
    except ExecutionBudgetError:
        raise
    except Exception as exc:
        receipt.update(status="unavailable", error_type=type(exc).__name__)
        error = exc
    else:
        error = None
    from bestseller.services.adopted_chapter_source import continuity_admissions_by_source, continuity_source_key

    metadata = dict(chapter.metadata_json or {})
    sources = continuity_admissions_by_source(metadata)
    source_key = continuity_source_key(receipt)
    assert source_key is not None
    sources[source_key] = receipt
    metadata["continuity_admission_by_source"] = sources
    history = list(metadata.get("continuity_admission_history", []))
    if metadata.get("continuity_admission"):
        history.append(metadata["continuity_admission"])
    metadata.update(continuity_admission=receipt, continuity_admission_history=history)
    if not receipt["passed"]:
        metadata["continuity_adoption_block"] = receipt
        metadata["blocked_by_phase_d_time_gate"] = True
        chapter.production_state = "blocked"
        chapter.status = ChapterStatus.REVISION.value
    else:
        prior_block = metadata.get("continuity_adoption_block")
        if isinstance(prior_block, dict) and continuity_source_key(prior_block) == source_key:
            metadata.pop("continuity_adoption_block", None)
            metadata["resolved_continuity_adoption_block"] = prior_block
        metadata.pop("blocked_by_phase_d_time_gate", None)
    chapter.metadata_json = metadata
    await session.flush()
    if not receipt["passed"]:
        # Preserve the exact rejected evidence. No adoption/knowledge writes
        # have run; a normal repair can now target this retained candidate.
        await _checkpoint_commit(session)
        raise ValueError("chapter_continuity_" + str(receipt["status"])) from error


def _checker_report_gate_payload(report: Any) -> dict[str, Any]:
    def _issue_payload(issue: Any) -> dict[str, Any]:
        if hasattr(issue, "to_dict"):
            return issue.to_dict()
        return {
            "id": getattr(issue, "id", ""),
            "type": getattr(issue, "type", ""),
            "severity": getattr(issue, "severity", ""),
            "location": getattr(issue, "location", ""),
            "description": getattr(issue, "description", str(issue)),
            "suggestion": getattr(issue, "suggestion", ""),
            "can_override": getattr(issue, "can_override", False),
        }

    return {
        "agent": getattr(report, "agent", ""),
        "chapter": getattr(report, "chapter", None),
        "summary": getattr(report, "summary", ""),
        "issues": [
            _issue_payload(issue) for issue in list(getattr(report, "issues", ()) or ())[:10]
        ],
    }


def _emit_progress(
    progress: ProgressCallback | None,
    stage: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if progress is None:
        return
    progress(stage, payload)



async def _record_chapter_rewrite_outcome(
    session: AsyncSession,
    *,
    workflow_run_id: UUID,
    step_name: str,
    step_order: int,
    chapter_draft: Any,
    rewrite_task: Any,
    project_slug: str,
    chapter_number: int,
    iteration: int,
    progress: ProgressCallback | None,
) -> None:
    """Report the rewrite decision, not merely that its coroutine returned."""
    rejected = getattr(rewrite_task, "status", None) == "failed"
    error = (
        str(getattr(rewrite_task, "error_log", "") or "Chapter rewrite candidate rejected")
        if rejected else None
    )
    payload = {
        "project_slug": project_slug,
        "chapter_number": chapter_number,
        "iteration": iteration,
        "rewrite_task_id": str(rewrite_task.id),
        "chapter_draft_id": str(chapter_draft.id),
        "chapter_draft_version_no": chapter_draft.version_no,
        "word_count": int(getattr(chapter_draft, "word_count", 0) or 0),
    }
    if rejected:
        metadata = getattr(rewrite_task, "metadata_json", None) or {}
        payload.update({
            "error": error,
            "rewrite_status": "failed",
            "current_draft_preserved": True,
            "rejection_evidence": {
                key: metadata[key]
                for key in (
                    "quality_gate_rejected_current_promotion",
                    "quality_retrofit_rejected_current_promotion",
                    "preserved_current_quality_gate_outcome",
                    "takeover_violation_codes",
                    "took_current_decision",
                )
                if key in metadata
            },
        })
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name=step_name,
        step_order=step_order,
        status=WorkflowStatus.FAILED if rejected else WorkflowStatus.COMPLETED,
        error_message=error,
        output_ref=payload,
    )
    _emit_progress(
        progress, "chapter_rewrite_rejected" if rejected else "chapter_rewrite_completed", payload,
    )


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
        for n in (ch.get("chapter_number") for ch in incoming_chapters if isinstance(ch, dict))
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


def _outline_content_chapters(content: Any) -> list[Any]:
    if isinstance(content, dict):
        chapters = content.get("chapters")
        return chapters if isinstance(chapters, list) else []
    if isinstance(content, list):
        return content
    return []


def _outline_chapters_for_volume(content: Any, volume_number: int) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    for chapter in _outline_content_chapters(content):
        if not isinstance(chapter, dict):
            continue
        try:
            chapter_volume = int(chapter.get("volume_number") or 0)
        except (TypeError, ValueError):
            chapter_volume = 0
        if chapter_volume == volume_number:
            chapters.append(chapter)
    return chapters


async def _load_approved_volume_chapter_outline_artifacts(
    session: AsyncSession,
    *,
    project_id: UUID,
) -> list[PlanningArtifactVersionModel]:
    """Load only canonical per-volume outlines for production merging."""

    result = await session.scalars(
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type
            == ArtifactType.VOLUME_CHAPTER_OUTLINE.value,
            PlanningArtifactVersionModel.status == "approved",
        )
        .order_by(
            PlanningArtifactVersionModel.version_no.asc(),
            PlanningArtifactVersionModel.created_at.asc(),
        )
    )
    return list(result.all())


async def _resume_outline_chapters_for_volume(
    session: AsyncSession,
    *,
    project_id: UUID,
    volume_number: int,
    expected_count: int,
) -> list[dict[str, Any]]:
    artifact = await get_latest_approved_planning_artifact(
        session,
        project_id=project_id,
        artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
    )
    if artifact is None:
        return []
    chapters = _outline_chapters_for_volume(artifact.content, volume_number)
    if not chapters:
        return []
    if expected_count > 0 and len(chapters) < expected_count:
        return []
    return chapters


_WRITTEN_CHAPTER_STATUSES: tuple[str, ...] = (
    ChapterStatus.DRAFTING.value,
    ChapterStatus.REVIEW.value,
    ChapterStatus.REVISION.value,
    ChapterStatus.COMPLETE.value,
)


def _chapter_has_safe_draft_for_review_stall(
    chapter: ChapterModel,
    chapter_draft: ChapterDraftVersionModel | None,
) -> bool:
    """Return True when review can accept the best draft without machine repair."""
    if chapter_draft is None:
        return False
    return getattr(chapter, "production_state", None) == "ok"


def _project_consistency_warn_only_scope(
    *,
    current_volume_number: int | None,
    chapter_numbers: set[int] | None,
    written_chapters: int = 0,
    target_chapters: int = 0,
) -> str | None:
    """Project consistency is advisory while processing a partial write slice.

    A book that is still being written is a partial slice too. The progressive
    loop says so explicitly via ``current_volume_number``, but the self-heal
    lane calls ``run_project_pipeline`` for the whole project with neither
    marker set — so an 8-of-30-chapter book was judged by whole-book criteria,
    came back ``attention`` (correctly: it has no ending yet), and was
    hard-blocked with ``requires_human_review``. That stopped the per-window
    loop from ever planning window 2, and the book could not pass chapter 8
    (2026-08-03, xianxia-upgrade-1785697772).

    You cannot review the consistency of a whole book that is not whole yet.
    The full verdict still applies in force once every planned chapter exists.
    """
    if current_volume_number is not None:
        return "partial_volume"
    if chapter_numbers is not None:
        return "chapter_slice"
    if target_chapters > 0 and written_chapters < target_chapters:
        return "book_in_progress"
    return None


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

    "Written" means *settled*, not *flawless*. ``quality_debt`` is the quality
    system's own terminal verdict — "stop repairing, ship the best draft" — and
    ``book_closure`` already ships and promotes such a chapter. Counting only
    ``production_state == "ok"`` here put the same fact in two places with two
    answers: closure called a chapter shipped while this counter called it
    unwritten. A 30-chapter book whose first window settled all 8 chapters as
    ``quality_debt`` reported ``0/8 written``, so the per-window loop logged
    "not advancing to later volumes", never planned window 2, and the writer
    was stranded at chapter 8 forever (2026-08-03, xianxia-upgrade-1785697772).
    """
    stmt = (
        select(func.count(ChapterModel.id))
        .join(VolumeModel, ChapterModel.volume_id == VolumeModel.id)
        .where(
            ChapterModel.project_id == project_id,
            VolumeModel.volume_number == volume_number,
            ChapterModel.status.in_(_WRITTEN_CHAPTER_STATUSES),
            ChapterModel.production_state.in_(tuple(SETTLED_PRODUCTION_STATES)),
        )
    )
    result = await session.scalar(stmt)
    return int(result or 0)


def _only_conceded_quality_debt(
    rows: Sequence[tuple[int, str | None, Mapping[str, Any] | None]],
    expected_chapter_numbers: set[int],
) -> bool:
    """Return whether a full window is settled and contains only conceded debt.

    This is an advisory handoff exception for progressive writing. It never
    changes a project or chapter review result; it only distinguishes the
    closure-mode ``quality_debt`` verdict from a structural/human blocker.
    Missing rows and any other production state remain blocking.
    """
    if not expected_chapter_numbers or len(rows) != len(expected_chapter_numbers):
        return False
    by_number = {number: (state, metadata) for number, state, metadata in rows}
    if set(by_number) != expected_chapter_numbers:
        return False
    has_quality_debt = False
    for state, metadata in by_number.values():
        if state == "quality_debt":
            if not bool((metadata or {}).get("chapter_quality_debt")):
                return False
            has_quality_debt = True
        elif state not in SETTLED_PRODUCTION_STATES:
            return False
    return has_quality_debt


def _progressive_quality_debt_handoff_allowed(
    settings: AppSettings, project: ProjectModel
) -> bool:
    """Limit the progressive exception to ordinary closure-mode drafts."""
    if _pipeline_quality_mode(settings) != "closure":
        return False
    from bestseller.services.story_enhancers import resolve_story_enhancers

    metadata = project.metadata_json if isinstance(project.metadata_json, Mapping) else {}
    return resolve_story_enhancers(metadata).is_empty()


async def _progressive_volume_has_only_conceded_quality_debt(
    session: AsyncSession,
    *,
    settings: AppSettings,
    project: ProjectModel,
    project_id: UUID,
    volume_number: int,
    expected_chapter_numbers: set[int],
    workflow_run_id: UUID,
) -> bool:
    """Identify the narrow quality-debt-only volume handoff case.

    Project-level consistency/export blockers are deliberately excluded even
    when all chapter rows happen to carry quality debt.
    """
    if not expected_chapter_numbers or not _progressive_quality_debt_handoff_allowed(
        settings, project
    ):
        return False
    result = await session.execute(
        select(
            ChapterModel.chapter_number,
            ChapterModel.status,
            ChapterModel.production_state,
            ChapterModel.metadata_json,
        )
        .join(VolumeModel, ChapterModel.volume_id == VolumeModel.id)
        .where(
            ChapterModel.project_id == project_id,
            VolumeModel.volume_number == volume_number,
            ChapterModel.chapter_number.in_(expected_chapter_numbers),
        )
    )
    # A few offline orchestration tests provide a session double without a
    # query result; fail closed and retain the existing repair behavior.
    if result is None:
        return False
    rows = result.all()
    if not _only_conceded_quality_debt(rows, expected_chapter_numbers):
        return False

    workflow_run = await session.get(WorkflowRunModel, workflow_run_id)
    if workflow_run is None:
        return False
    metadata = dict(getattr(workflow_run, "metadata_json", None) or {})
    return not any(
        metadata.get(key)
        for key in (
            "blocked_by_project_consistency",
            "milestone_consistency_blocked",
            "export_blocked_reason",
            "source_bound_blocked",
        )
    )


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


async def _chapter_numbers_in_volume(
    session: AsyncSession,
    project_id: UUID,
    volume_number: int,
) -> set[int]:
    """Return materialized chapter numbers for a volume from DB rows only."""
    stmt = (
        select(ChapterModel.chapter_number)
        .join(VolumeModel, ChapterModel.volume_id == VolumeModel.id)
        .where(
            ChapterModel.project_id == project_id,
            VolumeModel.volume_number == volume_number,
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    rows = await session.scalars(stmt)
    return {
        int(chapter_number)
        for chapter_number in rows.all()
        if isinstance(chapter_number, int) and chapter_number > 0
    }


async def _project_has_scene_machine_blocked_chapter(
    session: AsyncSession,
    project_id: UUID,
) -> bool:
    """Return True when chapter repair must stop at the scene blocker.

    A scene-level ``scene_rewrite_stalled_blocked`` means the bounded machine
    repair loop already failed to improve the draft. Letting the outer
    autowrite/project-repair layer start another project repair only repeats
    the same scene rewrites and burns LLM calls.
    """

    rows = await session.scalars(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.project_id == project_id,
            WorkflowRunModel.workflow_type == WORKFLOW_TYPE_CHAPTER_PIPELINE,
            WorkflowRunModel.status == WorkflowStatus.MACHINE_BLOCKED.value,
            WorkflowRunModel.current_step == "scene_machine_repair_required",
        )
        .order_by(WorkflowRunModel.updated_at.desc())
        .limit(5)
    )
    candidates = rows.all() if hasattr(rows, "all") else list(rows)
    for run in candidates:
        metadata = dict(getattr(run, "metadata_json", None) or {})
        if metadata.get("auto_repair_skipped_reason") == "scene_machine_blocked":
            return True
    return False


async def _ensure_project_invariants(
    session: AsyncSession,
    project: ProjectModel,
    settings: AppSettings,
) -> None:
    """Preserve existing contracts; initialize missing technical defaults once.

    The initialization is independently audited before paid processing. Invalid
    existing contracts require explicit repair, never automatic reseeding.
    """
    from bestseller.services.framework_state_migration import migrate_project_prompt_state
    from bestseller.services.project_invariant_initialization import ensure_missing_project_invariants

    if migrate_project_prompt_state(project, apply=True)["changed"]:
        await _checkpoint_commit(session)
    if project.invariants_json is not None:
        parsed = project_invariants_from_row(project)
        if parsed is None or parsed.project_id != project.id:
            raise InvariantSeedError("existing_project_invariants_invalid")
        return
    audit = await create_workflow_run(
        session, project_id=project.id, workflow_type="project_invariant_initialization",
        status=WorkflowStatus.RUNNING, scope_type="project", scope_id=project.id,
        requested_by="chapter_pipeline", current_step="missing_contract_preflight",
        metadata={"body_gate_pass_asserted": False},
    )
    await _checkpoint_commit(session)
    try:
        receipt = await ensure_missing_project_invariants(
            session, settings, project_id=project.id, workflow_id=audit.id,
        )
        if receipt["status"] == "existing_invalid":
            raise InvariantSeedError("existing_project_invariants_invalid")
        audit.metadata_json = {**(audit.metadata_json or {}), "invariant_initialization": receipt}
        audit.status = WorkflowStatus.COMPLETED.value
        audit.current_step = "technical_contract_initialized"
        await _checkpoint_commit(session)
    except BaseException as exc:
        await session.rollback()
        await session.refresh(audit)
        audit.status = WorkflowStatus.FAILED.value
        audit.current_step = "invariant_initialization_unavailable"
        audit.metadata_json = {**(audit.metadata_json or {}), "failure_type": type(exc).__name__,
                               "body_gate_pass_asserted": False}
        await _checkpoint_commit(session)
        raise


async def _enforce_truth_version_guard(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
) -> None:
    if not getattr(settings.pipeline, "enable_truth_version_guard", True):
        return
    await assert_truth_materializations_fresh(session, project)


async def _refresh_stale_truth_materializations_for_resume(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    *,
    requested_by: str,
    progress: ProgressCallback | None = None,
) -> bool:
    if not getattr(settings.pipeline, "enable_truth_version_guard", True):
        return False
    try:
        await assert_truth_materializations_fresh(session, project)
        return False
    except TruthVersionStaleError as exc:
        _emit_progress(
            progress,
            "truth_materialization_refresh_started",
            {
                "project_slug": project.slug,
                "truth_version": exc.truth_version,
                "components": [item.component for item in exc.stale_components],
            },
        )

    try:
        await materialize_latest_story_bible(
            session,
            project.slug,
            requested_by=requested_by,
        )
        await _checkpoint_commit(session)
        await materialize_latest_chapter_outline_batch(
            session,
            project.slug,
            requested_by=requested_by,
        )
        await _checkpoint_commit(session)
        await materialize_latest_narrative_graph(
            session,
            project.slug,
            requested_by=requested_by,
        )
        await _checkpoint_commit(session)
        await materialize_latest_narrative_tree(
            session,
            project.slug,
            requested_by=requested_by,
        )
        await _checkpoint_commit(session)
    except ValueError as exc:
        if "L2 bible gate failed" not in str(exc):
            raise
        await _accept_legacy_truth_materializations_for_resume(
            session,
            project,
            reason=str(exc).splitlines()[0],
        )
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "truth_materialization_refresh_requires_revision",
            {
                "project_slug": project.slug,
                "reason": str(exc).splitlines()[0],
            },
        )
        from bestseller.services.truth_version import TruthMaterializationRevisionRequired
        raise TruthMaterializationRevisionRequired(
            "Existing source failed materialization; revise its source contract before resuming."
        ) from exc
    _emit_progress(
        progress,
        "truth_materialization_refresh_completed",
        {"project_slug": project.slug},
    )
    return True


async def _accept_legacy_truth_materializations_for_resume(
    session: AsyncSession,
    project: ProjectModel,
    *,
    reason: str,
) -> None:
    # Compatibility is an audit event, never evidence of rematerialization.
    metadata = dict(project.metadata_json or {})
    metadata["legacy_truth_resume_block"] = {
        "reason": reason, "status": "requires_source_revision",
        "required_truth_version": metadata.get("truth_version"),
    }
    project.metadata_json = metadata

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
    from bestseller.services.budget import raise_if_execution_stopped
    raise_if_execution_stopped()
    commit = getattr(session, "commit", None)
    if commit is None:
        return
    await commit()


async def _completed_story_bible_materialization_is_reusable(
    session: AsyncSession,
    workflow_run: WorkflowRunModel | None,
) -> bool:
    """Reject completion markers whose committed outputs no longer exist.

    Workflow rows are audit records and may survive a scoped project cleanup or
    a cancelled outer transaction. They therefore cannot, by themselves, prove
    that the story bible and its source artifacts are reusable.
    """

    if workflow_run is None:
        return False
    metadata = workflow_run.metadata_json or {}
    source_ids = metadata.get("source_artifact_ids")
    if not isinstance(source_ids, Mapping) or not source_ids:
        # Legacy completed runs predate output lineage. Preserve their existing
        # resume behavior; new runs always record source_artifact_ids.
        return True
    try:
        expected_artifact_ids = {UUID(str(value)) for value in source_ids.values()}
    except (TypeError, ValueError):
        return False
    artifact_count = int(
        await session.scalar(
            select(func.count())
            .select_from(PlanningArtifactVersionModel)
            .where(PlanningArtifactVersionModel.id.in_(expected_artifact_ids))
        )
        or 0
    )
    if artifact_count != len(expected_artifact_ids):
        return False
    for model, metadata_key in (
        (CharacterModel, "characters_upserted"),
        (WorldRuleModel, "world_rules_upserted"),
        (VolumeModel, "volumes_upserted"),
    ):
        expected_count = int(metadata.get(metadata_key) or 0)
        if expected_count <= 0:
            continue
        actual_count = int(
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.project_id == workflow_run.project_id)
            )
            or 0
        )
        if actual_count < expected_count:
            return False
    return True


async def _count_pending_chapter_rewrite_tasks(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
) -> int:
    from bestseller.services.review_source_ordering import archived_rewrite_task_ids
    archived = await archived_rewrite_task_ids(session, project_id=project_id, chapter_id=chapter_id)
    count = await session.scalar(
        select(func.count())
        .select_from(RewriteTaskModel)
        .where(
            RewriteTaskModel.project_id == project_id,
            RewriteTaskModel.trigger_source_id == chapter_id,
            RewriteTaskModel.status.in_(("pending", "queued")),
            RewriteTaskModel.id.not_in(archived),
        )
    )
    return int(count or 0)


async def _supersede_obsolete_stitched_draft_tasks(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
) -> int:
    """Clear stale stitched-draft rewrite tasks when the current draft is clean.

    The stitched-draft detector is intentionally conservative, but its older
    false positives can leave ``pending`` chapter rewrite tasks behind. The
    chapter outline readiness gate blocks on any pending rewrite task, so a
    clean current draft must be allowed to supersede obsolete stitched-draft
    tasks before readiness is evaluated.
    """

    tasks = list(
        await session.scalars(
            select(RewriteTaskModel).where(
                RewriteTaskModel.project_id == project_id,
                RewriteTaskModel.trigger_source_id == chapter_id,
                RewriteTaskModel.status.in_(("pending", "queued")),
                RewriteTaskModel.rewrite_strategy == "chapter_coherence_bridge_rewrite",
            )
        )
    )
    from bestseller.services.review_source_ordering import archived_rewrite_task_ids
    archived = await archived_rewrite_task_ids(session, project_id=project_id, chapter_id=chapter_id)
    tasks = [task for task in tasks if task.id not in archived]
    stitched_tasks = [
        task
        for task in tasks
        if "拼接稿" in (task.instructions or "")
        or "stitched draft" in (task.instructions or "").lower()
    ]
    if not stitched_tasks:
        return 0

    current_draft = await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id == chapter_id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    if current_draft is None or not current_draft.content_md:
        return 0

    try:
        from bestseller.services.deduplication import (
            detect_intra_chapter_stitched_drafts,
        )
    except Exception:
        logger.debug("deduplication import failed during stale task cleanup", exc_info=True)
        return 0

    if detect_intra_chapter_stitched_drafts(current_draft.content_md):
        return 0

    for task in stitched_tasks:
        task.status = "superseded"
        task.metadata_json = {
            **(task.metadata_json or {}),
            "superseded_reason": "current_draft_no_longer_matches_stitched_draft_detector",
            "superseded_by_current_chapter_draft_id": str(current_draft.id),
            "superseded_by_current_chapter_draft_version": current_draft.version_no,
        }
    await session.flush()
    return len(stitched_tasks)


async def _supersede_pending_chapter_rewrite_tasks_for_regeneration(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    reason: str,
) -> int:
    """Clear queued chapter rewrite tasks before an explicit full regeneration.

    This is deliberately narrower than a global cleanup: it only runs when the
    caller opted into chapter-first regeneration, so stale rewrite tasks do not
    block a fresh one-pass chapter draft.
    """

    tasks = list(
        await session.scalars(
            select(RewriteTaskModel).where(
                RewriteTaskModel.project_id == project_id,
                RewriteTaskModel.trigger_source_id == chapter_id,
                RewriteTaskModel.status.in_(("pending", "queued")),
            )
        )
    )
    from bestseller.services.review_source_ordering import archived_rewrite_task_ids
    archived = await archived_rewrite_task_ids(session, project_id=project_id, chapter_id=chapter_id)
    tasks = [task for task in tasks if task.id not in archived]
    for task in tasks:
        task.status = "superseded"
        task.metadata_json = {
            **(task.metadata_json or {}),
            "superseded_reason": reason,
        }
    if tasks:
        await session.flush()
    return len(tasks)


def _clear_explicit_chapter_regeneration_residue(chapter: ChapterModel) -> bool:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    residue_keys = (
        "auto_repair_in_progress",
        "auto_repair_exhausted",
        "auto_repair_last_block_codes",
        "auto_repair_attempts",
        "retention_retry_count",
        "retention_auto_repair_exhausted",
        "retention_gate_passed",
        "retention_gate_last_findings",
        "quality_gate_block_codes",
        "production_block_code",
        "last_generation_gate_error",
        "write_safety_block_code",
        "blocked_by_write_safety_gate",
    )
    changed = False
    for key in residue_keys:
        if key in metadata:
            metadata.pop(key, None)
            changed = True
    if changed:
        metadata["explicit_regeneration_residue_cleared"] = True
        chapter.metadata_json = metadata
        chapter.production_state = "ok"
    return changed


_EXPLICIT_SCENE_REGENERATION_RESIDUE_KEYS: frozenset[str] = frozenset(
    {
        "auto_repair_adjusted_target_word_count",
        "auto_repair_attempt",
        "auto_repair_hint",
        "auto_repair_length_scale",
        "auto_repair_min_scene_target_floor",
        "auto_repair_original_target_word_count",
        "auto_repair_scene_target_cap",
        "auto_repair_source_block_code",
        "auto_repair_target_word_count_clamped",
        "auto_repair_block_codes",
    }
)


def _compact_repair_instruction_text(value: Any, *, max_chars: int = 2600) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...（已截断，仅保留高优先级修复要求）"


def _render_chapter_first_repair_hard_constraints(
    chapter: ChapterModel,
    scenes: list[SceneCardModel] | tuple[SceneCardModel, ...] = (),
) -> str:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    object_signal = metadata.get("object_signal_contract")
    lines: list[str] = []
    forbidden_terms: list[str] = []
    if isinstance(object_signal, dict):
        chapter_mode = str(object_signal.get("chapter_mode") or "").strip()
        if chapter_mode:
            lines.append(f"- 物件信号合同：{chapter_mode}")
        values = object_signal.get("forbidden_signals")
        if isinstance(values, list):
            forbidden_terms.extend(str(item).strip() for item in values if str(item).strip())
    if int(getattr(chapter, "chapter_number", 0) or 0) <= 10:
        forbidden_terms.extend(_front10_forbidden_signal_terms(chapter))
    foreshadowing = getattr(chapter, "foreshadowing_actions", None)
    if isinstance(foreshadowing, dict):
        values = foreshadowing.get("forbidden_early_leaks")
        if isinstance(values, list):
            forbidden_terms.extend(str(item).strip() for item in values if str(item).strip())
    scene_forbidden: list[str] = []
    seen_scene_forbidden: set[str] = set()
    for scene in scenes:
        for action in getattr(scene, "forbidden_actions", None) or []:
            text = str(action or "").strip()
            if text and text not in seen_scene_forbidden:
                seen_scene_forbidden.add(text)
                scene_forbidden.append(text)
    if scene_forbidden:
        safe_scene_forbidden = _prompt_safe_forbidden_actions(scene_forbidden)
        lines.append("- 场景卡禁写动作：" + "；".join(safe_scene_forbidden[:30]))
    unique_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in forbidden_terms:
        if term and term not in seen_terms:
            seen_terms.add(term)
            unique_terms.append(term)
    if unique_terms:
        lines.append("- 禁写/暂缓词：" + "、".join(unique_terms[:30]))
    if not lines:
        return ""
    rendered = (
        "【章节硬约束优先级】以下约束来自章节细纲、物件信号合同和场景卡，"
        "高于本次自动修复目标；补长、修尾、修时间线时也不得突破。\n" + "\n".join(lines)
    )
    return _redact_front10_prompt_leaks(rendered, chapter, scenes)


def _chapter_continuity_repair_hints(chapter: ChapterModel) -> tuple[str, ...]:
    """Read advisory continuity findings stamped by the chapter-first generator.

    The critic never blocks, so without this the findings would be recorded and
    never acted on. When some *other* gate sends the chapter back for a patch,
    the known contradictions ride along and get fixed in the same pass.
    """

    metadata = getattr(chapter, "metadata_json", None)
    if not isinstance(metadata, dict):
        return ()
    payload = metadata.get("chapter_continuity_latest")
    if not isinstance(payload, dict):
        return ()
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return ()
    hints: list[str] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        detail = str(item.get("detail") or "").strip()
        if not detail:
            continue
        category = str(item.get("category") or "").strip() or "continuity"
        first = str(item.get("first_evidence") or "").strip()
        second = str(item.get("second_evidence") or "").strip()
        hints.append(f"[{category}] {detail}（前文：{first} / 后文：{second}）")
    return tuple(hints)


def _render_chapter_first_local_repair_instructions(
    *,
    chapter: ChapterModel,
    block_codes: tuple[str, ...],
    scene_hints: list[str],
    scenes: list[SceneCardModel] | tuple[SceneCardModel, ...] = (),
) -> str:
    chapter_meta = dict(getattr(chapter, "metadata_json", None) or {})
    strict_hint = str(chapter_meta.get("retention_retry_strict_prompt") or "").strip()
    merged_hints = "\n".join(dict.fromkeys(item for item in scene_hints if item.strip()))
    if strict_hint:
        merged_hints = "\n".join(item for item in (merged_hints, strict_hint) if item)
    continuity_hints = _chapter_continuity_repair_hints(chapter)
    if continuity_hints:
        merged_hints = "\n".join(
            item
            for item in (
                merged_hints,
                "【章内事实矛盾（必须改掉，改动限于矛盾处）】",
                *continuity_hints,
            )
            if item
        )
    if not merged_hints:
        merged_hints = "本章触发章节级质量门，请只修复命中的阻断点。"
    structural_opening_codes = {"OPENING_SCENE_DRIFT"}
    structural_signal_codes = {
        "FRONT10_FORBIDDEN_SIGNAL",
        "FRONT10_SCENE_FORBIDDEN_ACTION",
        "FRONT10_RULE_LECTURE_DENSITY",
    }
    normalized_codes = {str(code).strip().upper() for code in block_codes if str(code).strip()}
    needs_opening_rebuild = bool(normalized_codes & structural_opening_codes)
    needs_signal_rebuild = bool(normalized_codes & structural_signal_codes)
    hard_constraints = _render_chapter_first_repair_hard_constraints(chapter, scenes)
    if needs_opening_rebuild or needs_signal_rebuild:
        opening_contract = str(getattr(chapter, "opening_situation", None) or "").strip()
        forbidden_terms = _front10_forbidden_signal_terms(chapter)
        structural_lines = [
            "【结构性开篇修复合同】",
            "本次命中了前10章结构性门禁，不按普通 patch-first 处理。",
        ]
        if needs_opening_rebuild:
            structural_lines.extend(
                [
                    "必须重写开篇前500字；如果首场使用了章节合同未规划的【禁用通联转送桥段】"
                    "来承担入场或召唤逻辑，直接替换整个首场开头段落。",
                    "开篇必须从第一场现场可见动作进入；电话、手机、微信、短信、语音、录音、"
                    "来电等媒介不是绝对禁用，但只有在章节合同已经交代来源、转交人、可信原因"
                    "和到场动机时才能使用，不能作为突兀背景或捷径。",
                ]
            )
            if opening_contract:
                structural_lines.append(f"第一场开局合同：{opening_contract}")
        if needs_signal_rebuild and forbidden_terms:
            structural_lines.append(
                "物件/符号异常不得再写成发热发烫捷径；禁用词：" + "、".join(forbidden_terms[:40])
            )
        structural_lines.extend(
            [
                "保留章内核心事件顺序、人物在场关系和章末主钩子；允许为了修掉结构性开篇问题重写首场，"
                "但不要新增额外场景或另起支线。",
                "全文仍需落在章节动态字数带内，优先 2200-2800 字，硬上限 3500 字（绝对红线，超过即判废重写）。",
                hard_constraints,
                "【具体修复要求】",
                _compact_repair_instruction_text(merged_hints),
            ]
        )
        return _redact_front10_prompt_leaks(
            "\n".join(line for line in structural_lines if str(line or "").strip()),
            chapter,
            scenes,
        )
    # 字数阻断与「保持篇幅稳定」是互斥任务（2026-08-13 真机定罪：ch1 的
    # LENGTH_UNDER 修复轮在无条件稳定器指令下只补了 ~60 字，整轮白烧）。
    # 命中字数下限时切换成补缺口合同，其余场合维持 patch-first 稳定器。
    # 超长侧（2026-08-14 真机 ch3）：首稿 5116 字对 2600 目标，修复轮砍到
    # 2095（偏短）又反弹到 4972，6 轮全阻断——因为超长时走的是通用
    # patch-first 分支，那里写着「保持当前篇幅基本稳定」，对一篇必须砍掉
    # 一半的稿子是自相矛盾的指令，于是模型要么不敢砍要么砍过头来回震荡。
    over_length_codes = {
        "LENGTH_OVER",
        "CHAPTER_LENGTH_BLOCK_HIGH",
        "LENGTH_BLOCK_HIGH",
    }
    if normalized_codes & over_length_codes:
        target_wc = int(getattr(chapter, "target_word_count", 0) or 0)
        band = (
            f"本章目标篇幅 {target_wc} 字。请压到 {int(target_wc * 0.92)}–{target_wc} 字之间"
            if target_wc
            else "请压到章节动态字数带内"
        )
        lines = [
            "【章节自动修复任务｜超长压缩优先】",
            f"命中阻断码：{', '.join(block_codes) if block_codes else 'unknown'}。",
            f"{band}——**一次压到位**：只砍几十字会在下一轮因同一个码被打回，"
            "白烧一轮；砍过头掉到下限以下同样阻断，来回震荡最伤稿子。",
            "先砍这些：与主线无关的环境铺陈、重复的心理复述、同义的动作描写、"
            "把一件事说两遍的段落、可以并入前后句的过渡句。",
            "禁止砍掉：已发生的关键事件、人物的当场决定与后果、章末钩子、"
            "本章必须交代的信息——压缩的是水，不是骨头。",
            "其余命中的问题仍按 patch-first 局部替换：不得改变核心事件顺序、"
            "章末主钩子、人物在场关系。",
            hard_constraints,
            "【具体修复要求】",
            _compact_repair_instruction_text(merged_hints),
        ]
        return _redact_front10_prompt_leaks(
            "\n".join(line for line in lines if str(line or "").strip()),
            chapter,
            scenes,
        )
    length_codes = {"LENGTH_UNDER", "CHAPTER_LENGTH_BLOCK_LOW", "LENGTH_BLOCK_LOW"}
    if normalized_codes & length_codes:
        target_wc = int(getattr(chapter, "target_word_count", 0) or 0)
        target_line = f"本章目标篇幅 {target_wc} 字（动态字数带内），" if target_wc else ""
        # 具体数字必须逐字写进 prompt——模型看不见的契约就是不存在的契约。
        # 2026-08-22 真机（ch11）：在架稿 1797 字、下限 1800，差 3 个字整本
        # 书停产；三轮重写 1726/1726/1797 全在下限边缘试——因为 prompt 只说
        # 「按质检结论的差值补齐」，从没写出当前多少字、要补多少。
        # 补字对齐**目标**而不是下限：贴着下限的稿后续任何修改都会再跌破。
        _cur_wc = int(getattr(chapter, "current_word_count", 0) or 0)
        gap_line = ""
        if target_wc and _cur_wc:
            _required = max(target_wc - _cur_wc, 300)
            gap_line = (
                f"当前草稿约 {_cur_wc} 字、目标 {target_wc} 字："
                f"本轮必须新增至少 {_required} 字的新内容，"
                "补到目标附近，而不是刚过下限——贴着下限的稿会在后续修改中"
                "再次跌破，又挂回同一个码。"
            )
        lines = [
            "【章节自动修复任务｜字数缺口优先】",
            f"命中阻断码：{', '.join(block_codes) if block_codes else 'unknown'}。",
            f"{target_line}首要任务是补足字数缺口：按质检结论给出的当前字数与"
            "下限的差值，一次性补齐差值并再加约 200 字缓冲——只补几十个字"
            "等于白烧一轮，下一轮还会因为同一个码被打回。",
            gap_line,
            # 2026-08-14 真机 ch7：合同在场，模型仍把 1594 字的稿子「重写」成
            # 1020 字——补字数的轮次反而丢了内容，9 轮全挂同一个码。短章修复
            # 必须是「保底追加」而不是「整章重写」。
            (
                "保留已有合法事件、信息和有效细节；对同时命中的非长度问题按批准来源逐段修正，"
                "包括按当前批准文风统一叙述视角。不得为了保留原句而照抄触发阻断的错误；"
                "修正后仍须补足目标篇幅，不能把修稿写得更短。"
                if normalized_codes - (length_codes | {"CHAPTER_TOO_SHORT", "BLOCK_LOW"}) else
                "**已有正文一字不改地全部保留**：先原样输出当前草稿的每一段，"
                "再在合适位置插入新增内容。这一轮只许加，不许删改已有段落——"
                "补字数的轮次把稿子改短是最坏结果，会连着几轮挂在同一个码上。"
            ),
            "补进来的必须是有推进的新内容：一段新的对话交锋、一个当场落地的"
            "动作后果、或对本章钩子的一步深化；禁止把已有句子抻长、堆环境"
            "形容或复述设定——注水段落会被去水门撤销，补了也白补。",
            # 2026-08-15《端盘画神》定罪：扩写轮是「时刻切片」句法病的出生地
            # （病变均值 +6.8/轮、62% 恶化；ch38 一轮 4→41 处）。字数最便宜的
            # 写法就是把一个动作切成一串瞬间，必须点名禁止。
            "尤其禁止用「时刻切片」凑字数：把一个动作切成多个瞬间接力"
            "（上一句动词被下一句拎出来续写那个瞬间，或用量词切片逐步推进）"
            "——这是检测器盯防的注水句法，写了整章会被打回重写。"
            "一个动作一句写完；要加字就加新的事件、对话或后果。",
            "总篇幅不得超过章节动态字数带上限（超上限同样阻断，宁可停在带内中位）。",
            "其余命中的问题仍按 patch-first 局部替换：不得改变核心事件顺序、"
            "章末主钩子、人物在场关系。",
            hard_constraints,
            "【具体修复要求】",
            _compact_repair_instruction_text(merged_hints),
        ]
        return _redact_front10_prompt_leaks(
            "\n".join(line for line in lines if str(line or "").strip()),
            chapter,
            scenes,
        )
    lines = [
        "【章节自动修复任务｜局部替换优先】",
        f"命中阻断码：{', '.join(block_codes) if block_codes else 'unknown'}。",
        "本次不是重新生成章节，也不是按场景卡扩写新稿。必须以当前草稿为底稿，只替换问题段、"
        "补一两句必要桥接、或删除/合并导致问题的句段。",
        "不得大幅扩写环境、心理、设定解释或新增场景；"
        "除非命中全文结构不可用，不得改变核心事件顺序、章末主钩子、人物在场关系。",
        "如果修复一个问题需要新增信息，必须删掉等量解释或重复句，保持当前篇幅基本稳定。",
        "输出仍然是一章完整正文，但改动策略必须是 patch-first：问题点替换、局部段落重写、全章一致性轻校准。",
        hard_constraints,
        "【具体修复要求】",
        _compact_repair_instruction_text(merged_hints),
    ]
    return _redact_front10_prompt_leaks(
        "\n".join(line for line in lines if str(line or "").strip()),
        chapter,
        scenes,
    )


async def _create_chapter_first_local_auto_repair_task(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    block_codes: tuple[str, ...],
    attempt_number: int,
    source_draft_version_id: UUID | None = None,
) -> RewriteTaskModel | None:
    # Serialize first-pointer recovery through the owning chapter. Never
    # replace an incumbent just to make a rejected challenger repairable.
    owner = await session.scalar(select(ChapterModel).where(
        ChapterModel.id == chapter.id, ChapterModel.project_id == project.id,
    ).with_for_update())
    if owner is None:
        raise ValueError("chapter auto repair source has the wrong project or chapter")
    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )
    scene_hints: list[str] = []
    seen_scene_hints: set[str] = set()
    for scene in scenes:
        metadata = dict(getattr(scene, "metadata_json", None) or {})
        hint = str(metadata.get("auto_repair_hint") or "").strip()
        if hint and hint not in seen_scene_hints:
            seen_scene_hints.add(hint)
            scene_hints.append(f"场景{scene.scene_number}：{hint}")
    parent = await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.project_id == project.id,
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        ).order_by(ChapterDraftVersionModel.version_no.desc()).limit(1)
        .with_for_update().execution_options(populate_existing=True)
    )
    pointer_initialized = False
    if parent is not None and source_draft_version_id is not None and parent.id != source_draft_version_id:
        return None  # Caller retains both versions instead of repairing the wrong prose.
    if parent is None and source_draft_version_id is not None:
        candidate = await session.scalar(select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.id == source_draft_version_id,
            ChapterDraftVersionModel.project_id == project.id,
            ChapterDraftVersionModel.chapter_id == chapter.id,
        ).with_for_update().execution_options(populate_existing=True))
        latest = await session.scalar(select(func.max(ChapterDraftVersionModel.version_no)).where(
            ChapterDraftVersionModel.project_id == project.id,
            ChapterDraftVersionModel.chapter_id == chapter.id,
        ))
        if (candidate is None or candidate.promotion_state != "candidate"
            or not (candidate.content_md or "").strip() or candidate.version_no != latest):
            raise ValueError("chapter auto repair requires the exact latest repairable candidate")
        candidate.is_current = True
        chapter.current_word_count = candidate.word_count
        await session.flush()
        parent = candidate
        pointer_initialized = True
    if parent is None:
        raise ValueError("chapter auto repair requires a current parent draft")
    task = RewriteTaskModel(
        project_id=project.id,
        trigger_type="chapter_auto_repair",
        trigger_source_id=chapter.id,
        rewrite_strategy="localized_patch_first_chapter_repair",
        priority=1,
        status="pending",
        instructions=_render_chapter_first_local_repair_instructions(
            chapter=chapter,
            block_codes=block_codes,
            scene_hints=scene_hints,
            scenes=scenes,
        ),
        context_required=[
            "current_chapter_draft",
            "chapter_context",
            "quality_gate_findings",
        ],
        metadata_json={
            "source": "chapter_first_auto_repair",
            **rewrite_source_binding(parent, chapter=chapter),
            "patch_first": True,
            "attempt_number": attempt_number,
            "block_codes": list(block_codes),
            "working_pointer_initialized": pointer_initialized,
        },
    )
    session.add(task)
    await session.flush()
    return task


def _clear_explicit_scene_regeneration_residue(
    scenes: list[SceneCardModel],
    *,
    chapter_target_word_count: int | None,
    preserve_source_targets: bool = False,
) -> dict[str, Any]:
    """Normalize stale scene repair state before explicit chapter regeneration."""

    metadata_cleared = 0
    for scene in scenes:
        metadata = dict(getattr(scene, "metadata_json", None) or {})
        next_metadata = {
            key: value
            for key, value in metadata.items()
            if key not in _EXPLICIT_SCENE_REGENERATION_RESIDUE_KEYS
        }
        if next_metadata != metadata:
            scene.metadata_json = next_metadata
            metadata_cleared += 1

    target_rebalanced = False
    try:
        chapter_target = int(chapter_target_word_count or 0)
    except (TypeError, ValueError):
        chapter_target = 0
    if scenes and chapter_target > 0 and not preserve_source_targets:
        scene_count = len(scenes)
        current_targets: list[int] = []
        for scene in scenes:
            try:
                target = int(getattr(scene, "target_word_count", 0) or 0)
            except (TypeError, ValueError):
                target = 0
            current_targets.append(target)

        expected = chapter_target / scene_count
        low_scene_threshold = max(350, int(expected * 0.65))
        high_scene_threshold = max(low_scene_threshold + 1, int(expected * 1.75))
        target_sum = sum(current_targets)
        scene_sum_min_threshold, scene_sum_max_threshold = chapter_scene_budget_sum_thresholds(
            chapter_target
        )
        budget_is_stale = (
            target_sum < scene_sum_min_threshold
            or target_sum > scene_sum_max_threshold
            or any(target <= 0 for target in current_targets)
            or any(target < low_scene_threshold for target in current_targets)
            or any(target > high_scene_threshold for target in current_targets)
        )
        if budget_is_stale:
            base_target = chapter_target // scene_count
            remainder = chapter_target - base_target * scene_count
            for index, scene in enumerate(scenes):
                scene.target_word_count = base_target + (1 if index < remainder else 0)
            target_rebalanced = True

    return {
        "scene_count": len(scenes),
        "metadata_residue_cleared": metadata_cleared,
        "target_rebalanced": target_rebalanced,
        "target_word_count_sum": sum(
            int(getattr(scene, "target_word_count", 0) or 0) for scene in scenes
        ),
    }


def _project_chapter_first_preference(project: ProjectModel | None) -> bool | None:
    """Read the per-book generation-unit preference, or ``None`` if unset.

    ``generation_unit_mode`` is the forward-looking key; the two legacy keys are
    what ``repair.py`` already reads, so both stay honoured rather than leaving
    books that were marked before this change stranded on the global default.
    An explicit ``scene`` / ``False`` must return ``False`` (not ``None``) so a
    book can be pinned back to scene mode even after the global flag flips on.
    """

    return generation_unit_preference_from_metadata(getattr(project, "metadata_json", None))


def _chapter_first_requested(
    settings: AppSettings,
    chapter_number: int,
    explicit: bool | None,
    chapter: ChapterModel | None = None,
    project: ProjectModel | None = None,
) -> bool:
    """Resolve the generation unit for one chapter.

    Precedence: explicit caller argument > per-book metadata > global settings.

    The per-book layer exists so a single book can run chapter-first without
    flipping the global default for every in-flight book. It reads the same
    metadata keys the repair path already honours
    (``repair.py`` ``use_chapter_first``), so a book marked chapter-first
    generates and repairs under the same unit instead of silently switching
    units between the two paths.
    """

    if explicit is not None:
        return bool(explicit)
    per_book = _project_chapter_first_preference(project)
    if per_book is not None:
        return per_book
    if not bool(getattr(settings.pipeline, "enable_chapter_first_generation", False)):
        return False
    cap = int(getattr(settings.pipeline, "chapter_first_max_chapter_number", 3) or 3)
    if chapter_number <= cap:
        return True
    threshold = int(getattr(settings.pipeline, "chapter_first_short_chapter_threshold", 3500) or 0)
    if chapter is not None and threshold > 0:
        target = int(getattr(chapter, "target_word_count", 0) or 0)
        if 0 < target <= threshold:
            return True
    return False


async def _persist_chapter_numeric_adoption(
    session: Any, *, chapter: Any, draft: Any, quality: Any, report: Any, workflow: Any,
    settings: AppSettings,
) -> dict | None:
    """Persist the exact debt adoption before any knowledge service can use it."""
    receipt = (workflow.metadata_json or {}).get("numeric_promotion_debt")
    if receipt is not None and receipt != numeric_promotion_debt_receipt(draft, quality, report):
        receipt = None
    if receipt and not chapter_local_debt_allows_continuation(
        {**(chapter.metadata_json or {}), **(workflow.metadata_json or {}),
         "numeric_promotion_debt": receipt},
        has_current_draft=True, numeric_debt_binding_verified=True,
    ):
        receipt = None
    if receipt:
        try:
            length_request = (draft.promotion_metadata or {}).get("book_length_control")
            if "book_length_control" in (draft.promotion_metadata or {}):
                length_request = full_flow_length.require_feasible_request(length_request)
                preview = full_flow_length.evaluate_candidate(length_request, draft.content_md)
                if not preview["length_passed"]:
                    raise full_flow_length.LengthControlError(preview["issue_code"])
                length_project = await session.scalar(select(ProjectModel).where(
                    ProjectModel.id == chapter.project_id))
                if length_project is None:
                    raise full_flow_length.LengthControlError("LENGTH_CONTROL_PROJECT_UNKNOWN")
                await session.flush()
                length_check = await full_flow_length.revalidate_request(
                    session, settings, project=length_project, chapter=chapter,
                    receipt=length_request)
                if not length_check["valid"]:
                    raise full_flow_length.LengthControlError(length_check["issue_code"])
            await _ensure_chapter_continuity_before_adoption(
                session, settings, project_id=chapter.project_id, chapter=chapter,
                draft=draft, workflow_run_id=workflow.id,
            )
            if receipt != numeric_promotion_debt_receipt(draft, quality, report):
                raise ValueError("chapter_continuity_adoption_source_changed")
        except ExecutionBudgetError:
            raise
        except ValueError as exc:
            receipt = None
            workflow.metadata_json = {**(workflow.metadata_json or {}),
                "blocked_by_phase_d_time_gate": True, "continuity_admission_error": str(exc)}
    chapter.metadata_json = {
        **(chapter.metadata_json or {}), "numeric_promotion_debt": receipt,
        "chapter_quality_debt": True,
        "chapter_quality_debt_reason": (
            (workflow.metadata_json or {}).get("chapter_quality_debt_reason") or "chapter_not_promoted"
        ),
    }
    await session.flush()
    if receipt and length_request is not None:
        await full_flow_length.refresh_after_adoption(
            session, settings, project=length_project, chapter=chapter, receipt=length_request)
    return receipt


def _chapter_critic_evidence_unknown(report: Any) -> bool:
    payload = getattr(report, "structured_output", None) or {}
    evidence = payload.get("critic_evidence") or {}
    failure = (payload.get("evidence_summary") or {}).get("review_evidence_failure")
    return bool(
        getattr(report, "verdict", None) == "unknown"
        or failure or evidence.get("status") in {"evidence_invalid", "fallback", "unavailable"}
    )


class _ChapterReviewSourceChanged(RuntimeError):
    def __init__(self, draft):
        super().__init__("chapter_review_recovery_source_changed")
        self.draft = draft


def _chapter_review_source_matches(draft, binding: Mapping[str, str]) -> bool:
    return bool(
        draft is not None
        and str(draft.id) == binding["draft_id"]
        and chapter_review_body_fingerprint(draft.content_md or "")["digest"] == binding["body_hash"]
    )


def _chapter_critic_cycle_id(
    project_id: UUID,
    chapter_id: UUID,
    body_hash: str,
    policy: str = "initial",
) -> str:
    return hashlib.sha256(
        f"chapter-critic-cycle.v1\n{project_id}\n{chapter_id}\n{body_hash}\n{policy}".encode()
    ).hexdigest()


async def _locked_critic_chapter(
    session: AsyncSession,
    draft: ChapterDraftVersionModel,
) -> ChapterModel:
    chapter = await session.scalar(
        select(ChapterModel)
        .where(
            ChapterModel.id == draft.chapter_id,
            ChapterModel.project_id == draft.project_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if chapter is None:
        raise ValueError("chapter_critic_cycle_chapter_missing")
    return chapter


def _store_chapter_critic_cycle(chapter: ChapterModel, cycle: Mapping) -> None:
    metadata = dict(chapter.metadata_json or {})
    cycles = dict(metadata.get("chapter_critic_cycles") or {})
    cycles[cycle["cycle_id"]] = deepcopy(dict(cycle))
    chapter.metadata_json = {**metadata, "chapter_critic_cycles": cycles}


async def _retained_chapter_critic_result(
    session: AsyncSession,
    draft: ChapterDraftVersionModel,
    attempt: Mapping,
) -> tuple:
    from bestseller.domain.review import ChapterReviewResult

    report = await session.get(ReviewReportModel, UUID(attempt["review_report_id"]))
    quality = await session.get(QualityScoreModel, UUID(attempt["quality_score_id"]))
    if (
        report is None
        or quality is None
        or report.project_id != draft.project_id
        or report.target_type != "chapter"
        or report.target_id != draft.chapter_id
        or (report.structured_output or {}).get("draft_id") != str(draft.id)
        or (report.structured_output or {}).get("reviewed_body_fingerprint")
        != chapter_review_body_fingerprint(draft.content_md or "")
        or quality.project_id != draft.project_id
        or quality.target_id != draft.chapter_id
        or quality.target_type != "chapter"
        or quality.review_report_id != report.id
        or quality.chapter_draft_version_id != draft.id
    ):
        raise ValueError("chapter_critic_cycle_result_binding_mismatch")
    task_id = attempt.get("rewrite_task_id")
    task = await session.get(RewriteTaskModel, UUID(task_id)) if task_id else None
    if task_id and (
        task is None
        or task.project_id != draft.project_id
        or (task.metadata_json or {}).get("review_report_id") != str(report.id)
    ):
        raise ValueError("chapter_critic_cycle_rewrite_binding_mismatch")
    return ChapterReviewResult.model_validate(attempt["review_result"]), report, quality, task


async def _chapter_critic_semantic_dependencies(
    session: AsyncSession,
    settings: AppSettings,
    chapter: ChapterModel,
) -> str:
    """Conservative data/config fence, excluding mutable review/control receipts.

    This is not an effective-prompt or semantic-correctness proof. Missing data
    raises before reuse/send; changed dependencies never authorize another cycle.
    No retrieval, embeddings or model calls are made here.
    """
    from bestseller.settings import apply_runtime_llm_profile
    settings = apply_runtime_llm_profile(settings)
    from sqlalchemy import inspect

    from bestseller.services.chapter_generation_input_builder import (
        _without_runtime_evidence,
        build_chapter_generation_input_stamp,
    )
    from bestseller.services.chapter_llm_quality_judge import (
        _judge_samples_count,
        resolve_commercial_judge_model_key,
    )
    from bestseller.services.commercial_review_context import load_authorized_commercial_history as load_commercial_review_history

    await session.flush()
    project = await session.scalar(
        select(ProjectModel)
        .where(
            ProjectModel.id == chapter.project_id,
        )
        .execution_options(populate_existing=True)
    )
    if project is None:
        raise ValueError("chapter_critic_semantic_project_missing")
    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(
                SceneCardModel.chapter_id == chapter.id,
                SceneCardModel.project_id == project.id,
            )
            .order_by(SceneCardModel.scene_number)
            .execution_options(populate_existing=True)
        )
    )
    if not scenes:
        raise ValueError("chapter_critic_semantic_scenes_missing")
    bundle = build_chapter_generation_input_bundle(
        project=project,
        chapter=chapter,
        scenes=scenes,
        context_packet=None,
        target_word_count=int(chapter.target_word_count or 0),
    )
    semantic_bundle = bundle.model_dump(mode="json")
    # The full chapter metadata includes this very cycle and reviewer receipts.
    # Authored contracts are already projected separately by the existing builder.
    semantic_bundle["chapter"].pop("metadata", None)
    prefix = (chapter.metadata_json or {}).get("outline_prefix_readiness") or {}
    semantic_bundle["outline_prefix_binding"] = {
        key: prefix.get(key) for key in ("artifact_id", "projection_hash")
    }
    history = await load_commercial_review_history(
        session,
        project_id=project.id,
        chapter_number=chapter.chapter_number,
    )
    designs = list(
        await session.scalars(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.status.in_(("approved", "canonical")),
                PlanningArtifactVersionModel.artifact_type.in_(
                    [
                        kind.value
                        for kind in (
                            ArtifactType.CREATION_INTENT,
                            ArtifactType.CONCEPTION_SNAPSHOT,
                            ArtifactType.PREMISE,
                            ArtifactType.BOOK_SPEC,
                            ArtifactType.WORLD_SPEC,
                            ArtifactType.CAST_SPEC,
                            ArtifactType.VOLUME_PLAN,
                            ArtifactType.CHAPTER_OUTLINE_BATCH,
                            ArtifactType.STORY_DESIGN_KERNEL,
                            ArtifactType.PUBLIC_EMOTION_KERNEL,
                            ArtifactType.ENTRY_SYSTEM_KERNEL,
                            ArtifactType.EMOTION_DRIVEN_KERNEL,
                            ArtifactType.VOLUME_CHAPTER_OUTLINE,
                        )
                    ]
                ),
            )
            .order_by(PlanningArtifactVersionModel.id)
            .execution_options(populate_existing=True)
        )
    )
    state = {}
    for model in (WorldRuleModel, CharacterModel, ChapterContractModel):
        query = select(model).where(model.project_id == project.id)
        if model is ChapterContractModel:
            query = query.where(model.chapter_id == chapter.id)
        rows = list(
            await session.scalars(
                query.order_by(model.id).execution_options(populate_existing=True)
            )
        )
        state[model.__tablename__] = [
            _without_runtime_evidence(
                {
                    attr.key: getattr(row, attr.key)
                    for attr in inspect(model).column_attrs
                    if attr.key not in {"created_at", "updated_at", "metadata_json", "status"}
                }
            )
            for row in rows
        ]
    metadata = project.metadata_json or {}
    project_choices = {
        key: metadata[key]
        for key in (
            "creation_intent_contract",
            "genre_intent_contract",
            "concept_contract",
            "premise",
            "writing_profile",
            "story_enhancers",
            "book_design_snapshot",
            "book_design_snapshot_hash",
            "creation_canonical_snapshot_id",
            "creation_canonical_snapshot_hash",
            "creation_intent_snapshot_id",
            "creation_intent_contract_hash",
            "llm_model_id",
            "prompt_pack_name",
            "prompt_pack_key",
            "approved_creation_contracts",
        )
        if key in metadata
    }
    config = {
        key: getattr(settings, key).model_dump(mode="json")
        for key in ("llm", "pipeline", "quality", "generation")
    }
    config["commercial_judge_selection"] = {
        "model_key": resolve_commercial_judge_model_key(settings),
        "sample_count": _judge_samples_count(),
    }
    # These are calibration assets, not writer/story authority. Include actual
    # disk bytes too, so a same-process cached corpus cannot hide an asset update.
    root = Path(__file__).resolve().parents[3]
    assets = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((root / "config/reference_corpora").glob("*.yaml"))
    }
    payload = {
        "schema": "chapter-critic-semantic-dependencies.v1",
        "bundle": semantic_bundle,
        "input_stamp": build_chapter_generation_input_stamp(bundle),
        "adopted_history": history,
        "project_choices": project_choices,
        "project_invariants": project.invariants_json,
        "state": state,
        "designs": [
            {
                "id": str(row.id),
                "type": row.artifact_type,
                "content": {
                    key: value for key, value in (row.content or {}).items() if key != "_meta"
                } if isinstance(row.content, Mapping) else row.content,
            }
            for row in designs
        ],
        "configuration": config,
        "calibration_assets": assets,
    }
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


async def _review_chapter_with_evidence_retry(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    workflow: WorkflowRunModel,
    draft: ChapterDraftVersionModel,
    source_binding: Mapping[str, str] | None = None,
    cycle_id: str | None = None,
) -> tuple:
    """Resume two durable rounds on one body, independently of workflow UUIDs."""
    from bestseller.services.llm import bind_execution_operation
    from bestseller.services.chapter_review_prerequisite import (
        WAITING, prerequisite_wait_receipt, wait_binding, waiting_history,
    )

    fingerprint = chapter_review_body_fingerprint(draft.content_md or "")
    chapter = await _locked_critic_chapter(session, draft)
    active = (chapter.metadata_json or {}).get("chapter_review_active_cycle") or {}
    if cycle_id is not None and active.get("cycle_id") != cycle_id:
        raise ValueError("chapter_critic_cycle_not_authorized")
    if cycle_id is None and active.get("source_body_hash") == fingerprint["digest"]:
        cycle_id = active.get("cycle_id")
    cycle_id = cycle_id or _chapter_critic_cycle_id(
        draft.project_id,
        draft.chapter_id,
        fingerprint["digest"],
    )
    cycle = deepcopy(
        ((chapter.metadata_json or {}).get("chapter_critic_cycles") or {}).get(cycle_id)
    )
    policy_hash = chapter_review_recovery_source_hash()
    semantic_hash = await _chapter_critic_semantic_dependencies(session, settings, chapter)
    if active.get("f0_supersession_receipt_sha256") and active.get("source_body_hash") == fingerprint["digest"]:
        from bestseller.services.review_source_ordering import read_fresh_review_preflight
        await read_fresh_review_preflight(session, settings, chapter=chapter, draft=draft)
    if cycle is None:
        # Legacy paid rounds have no resumable cycle receipt. Never silently
        # replenish them; an explicit policy recovery establishes a new cycle.
        recovery = active.get("cycle_id") == cycle_id
        if not recovery:
            prior_runs = list(
                await session.scalars(
                    select(WorkflowRunModel).where(
                        WorkflowRunModel.project_id == draft.project_id,
                        WorkflowRunModel.scope_id == draft.chapter_id,
                        WorkflowRunModel.workflow_type == WORKFLOW_TYPE_CHAPTER_PIPELINE,
                    )
                )
            )
            if any(
                item.get("body_fingerprint", {}).get("digest") == fingerprint["digest"]
                for run in [workflow, *prior_runs]
                for item in (run.metadata_json or {}).get("critic_evidence_attempts", [])
            ):
                raise ValueError("chapter_critic_legacy_attempts_require_explicit_recovery")
        cycle = {
            "schema_version": "chapter-critic-cycle.v1",
            "cycle_id": cycle_id,
            "source_draft_id": str(draft.id),
            "source_body_hash": fingerprint["digest"],
            "policy_source_hash": policy_hash,
            "semantic_dependencies_sha256": semantic_hash,
            "review_workflow_run_id": str(workflow.id),
            "state": "prepared",
            "attempts": [],
        }
        _store_chapter_critic_cycle(chapter, cycle)
        await _checkpoint_commit(session)
    if (
        cycle.get("source_draft_id") != str(draft.id)
        or cycle.get("source_body_hash") != fingerprint["digest"]
    ):
        raise ValueError("chapter_critic_cycle_source_mismatch")
    if cycle.get("policy_source_hash") != policy_hash:
        raise ValueError("chapter_critic_policy_changed_requires_explicit_recovery")
    if cycle.get("semantic_dependencies_sha256") != semantic_hash or cycle.get(
        "semantic_dependency_change_detected"
    ):
        raise ValueError("chapter_critic_semantic_dependencies_changed")
    attempts = cycle["attempts"]
    for attempt in range(2):
        if await _chapter_critic_semantic_dependencies(session, settings, chapter) != semantic_hash:
            raise ValueError("chapter_critic_semantic_dependencies_changed")
        if source_binding is not None:
            current = await session.scalar(
                select(ChapterDraftVersionModel)
                .where(
                    ChapterDraftVersionModel.project_id == draft.project_id,
                    ChapterDraftVersionModel.chapter_id == draft.chapter_id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
                .execution_options(populate_existing=True)
            )
            if not _chapter_review_source_matches(current, source_binding):
                raise _ChapterReviewSourceChanged(current)
        if len(attempts) > attempt and attempts[attempt].get("state") == "completed":
            result = await _retained_chapter_critic_result(session, draft, attempts[attempt])
            if attempts[attempt]["outcome"] != "unknown" or attempt == 1:
                from bestseller.services.chapter_review_fact_repair import retained_fact_review_result

                return await retained_fact_review_result(
                    session, settings=settings, draft=draft, review_tuple=result,
                )
            continue
        if len(attempts) > attempt and attempts[attempt].get("state") == WAITING:
            from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing
            proof = await validate_retained_review_before_processing(session, settings, chapter)
            if (not proof or proof.get("reuse_kind") != WAITING or proof.get("cycle_id") != cycle_id):
                raise ValueError("chapter_critic_waiting_receipt_invalid")
            return await _retained_chapter_critic_result(
                session, draft, waiting_history(cycle, attempts[attempt])[-1],
            )
        if cycle["state"] == "completed":
            raise ValueError("chapter_critic_completed_cycle_cannot_reopen")
        if len(attempts) == attempt:
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "state": "prepared",
                    "operation_namespace": f"chapter-critic:{cycle_id}:{attempt + 1}",
                }
            )
            _store_chapter_critic_cycle(chapter, cycle)
            await _checkpoint_commit(session)
        entry = attempts[attempt]
        entry["state"] = "running"
        cycle["state"] = "running"
        _store_chapter_critic_cycle(chapter, cycle)
        # Bind the active workflow before any semantic/commercial/primary work
        # can fail. The chapter cursor and owner receipt share this checkpoint.
        workflow.metadata_json = {
            **(workflow.metadata_json or {}), "critic_cycle_id": cycle_id,
        }
        await _checkpoint_commit(session)
        kwargs = {"workflow_run_id": UUID(cycle["review_workflow_run_id"])}
        if attempt:
            kwargs["evidence_retry_context"] = {
                "attempt": attempt + 1,
                "reviewed_body_fingerprint": fingerprint,
                "prior_report_id": attempts[attempt - 1]["review_report_id"],
                "failure": "previous critic evidence was unavailable or did not match this body",
            }
        with bind_execution_operation(entry["operation_namespace"]):
            result = await review_chapter_draft(
                session,
                settings,
                project_slug,
                chapter_number,
                **kwargs,
            )
        if await _chapter_critic_semantic_dependencies(session, settings, chapter) != semantic_hash:
            # Retain the paid report and operation without authorizing its use.
            cycle["semantic_dependency_change_detected"] = True
            entry["received_report_id"] = str(result[1].id)
            _store_chapter_critic_cycle(chapter, cycle)
            await _checkpoint_commit(session)
            raise ValueError("chapter_critic_semantic_dependencies_changed")
        unknown = _chapter_critic_evidence_unknown(result[1])
        if unknown or result[0].verdict == "unknown":
            unknown = True
            quarantined = deepcopy(result[0])
            quarantined.verdict = "unknown"
            if hasattr(quarantined, "rewrite_instructions"):
                quarantined.rewrite_instructions = None
            result = (quarantined, result[1], result[2], None)
        waiting_receipt = prerequisite_wait_receipt(result[1], binding=wait_binding(
            cycle, entry, project_id=draft.project_id, chapter_id=draft.chapter_id,
            body_fingerprint=fingerprint,
        )) if unknown else None
        if waiting_receipt is not None:
            record = {
                "attempt": attempt + 1, "outcome": "unknown", "draft_id": str(draft.id),
                "body_fingerprint": fingerprint, "review_report_id": str(result[1].id),
                "quality_score_id": str(result[2].id), "rewrite_task_id": None,
                "review_result": result[0].model_dump(mode="json"),
                "waiting_receipt": waiting_receipt,
            }
            entry.update(state=WAITING, draft_id=str(draft.id), body_fingerprint=fingerprint,
                infrastructure_history=[*(entry.get("infrastructure_history") or []), record])
            cycle["state"] = WAITING
            await session.flush()
            chapter = await _locked_critic_chapter(session, draft)
            _store_chapter_critic_cycle(chapter, cycle)
            workflow.metadata_json = {
                **(workflow.metadata_json or {}), "critic_cycle_id": cycle_id,
                "critic_evidence_attempts": [
                    *((workflow.metadata_json or {}).get("critic_evidence_attempts") or []), deepcopy(entry)],
            }
            await _checkpoint_commit(session)
            return result  # Keep this prepared slot; no second internal round or dispatch permission.
        entry.update(
            {
                "state": "completed",
                "attempt": attempt + 1,
                "review_report_id": str(result[1].id),
                "draft_id": str(draft.id),
                "body_fingerprint": fingerprint,
                "outcome": "unknown" if unknown else "available",
                "quality_score_id": str(result[2].id),
                "rewrite_task_id": str(result[3].id) if result[3] is not None else None,
                "review_result": result[0].model_dump(mode="json"),
            }
        )
        if not unknown or attempt == 1:
            cycle["state"] = "completed"
        # Refresh only after review outputs have been flushed: external calls
        # release transactions, so another owner may have updated metadata.
        await session.flush()
        chapter = await _locked_critic_chapter(session, draft)
        _store_chapter_critic_cycle(chapter, cycle)
        history = list((workflow.metadata_json or {}).get("critic_evidence_attempts") or [])
        workflow.metadata_json = {
            **(workflow.metadata_json or {}),
            "critic_evidence_attempts": [*history, deepcopy(entry)],
            "critic_cycle_id": cycle_id,
        }
        await _checkpoint_commit(session)
        if not unknown:
            return result
    return result


def _evaluate_chapter_ai_flavor_without_edits(*, chapter_number, content_md, language, config):
    """Evaluate original bytes without invoking either patcher or deslop."""
    from bestseller.services.ai_flavor_gate import (
        AiFlavorGateOutcome,
        _block_threshold,
        _build_report,
        _warn_threshold,
        detect,
    )

    detected = detect(
        content_md,
        language=language,
        chapter_number=chapter_number,
        data_dir=Path(config.data_dir),
    )
    block = _block_threshold(config, detected.language)
    warn = _warn_threshold(config, detected.language)
    blocked = config.block_on_residual and (
        bool(detected.block_spans) or detected.overall_score >= block
    )
    decision = "block" if blocked else "pass"
    return AiFlavorGateOutcome(
        enabled=True,
        language=detected.language,
        chapter_number=chapter_number,
        before_score=detected.overall_score,
        after_score=detected.overall_score,
        patched_text=None,
        decision=decision,
        report=_build_report(
            chapter_number=chapter_number,
            pre_report=detected,
            post_report=detected,
            patch=None,
            decision=decision,
            block_score=block,
            warn_score=warn,
        ),
        metrics={
            "before_score": detected.overall_score,
            "after_score": detected.overall_score,
            "block_threshold": block,
            "warn_threshold": warn,
            "edits": 0,
            "retained_body_evaluation_only": True,
        },
    )


async def _finish_unknown_chapter_review(
    session,
    *,
    settings=None,
    workflow,
    chapter,
    draft,
    report=None,
    quality=None,
    review_iterations=0,
    rewrite_iterations=0,
    reason="critic_evidence_unknown",
) -> ChapterPipelineResult:
    """Persist a fail-closed result even if an optional service detached ORM state."""
    from bestseller.services.chapter_review_prerequisite import WAITING
    waiting = None
    if reason == "critic_evidence_unknown" and report is not None and any(
        isinstance(c, Mapping) and c.get("state") == WAITING
        for c in ((chapter.metadata_json or {}).get("chapter_critic_cycles") or {}).values()
    ):
        from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing
        if settings is None:
            raise ValueError("chapter_critic_waiting_settings_required")
        proof = await validate_retained_review_before_processing(session, settings, chapter)
        if proof and proof.get("reuse_kind") == WAITING and proof.get("report_id") == str(report.id):
            waiting = proof
    requires_human = waiting is None or bool(
        (workflow.metadata_json or {}).get("requires_human_review")
        or (chapter.metadata_json or {}).get("requires_human_review")
    )
    chapter.status = ChapterStatus.REVIEW.value if waiting and not requires_human else ChapterStatus.REVISION.value
    chapter.production_state = "review_unknown" if waiting and not requires_human else "blocked"
    workflow.status = WorkflowStatus.MACHINE_BLOCKED.value
    workflow.current_step = WAITING if waiting else reason
    workflow.metadata_json = {
        **(workflow.metadata_json or {}),
        "final_verdict": "unknown",
        "requires_human_review": requires_human,
        **({"review_prerequisite_wait": waiting} if waiting else {}),
        "chapter_critic_evidence_unknown": True,
        "chapter_draft_id": str(draft.id),
        "chapter_draft_version_no": draft.version_no,
        "review_report_id": str(report.id) if report else None,
        "quality_score_id": str(quality.id) if quality else None,
        "chapter_review_iterations": review_iterations,
        "chapter_rewrite_iterations": rewrite_iterations,
        "actual_adoption_receipt": {
            "adopted": False,
            "draft_id": str(draft.id),
            "body_fingerprint": chapter_review_body_fingerprint(draft.content_md or ""),
            "promotion_state": draft.promotion_state,
            "numeric_adoption_receipt": None,
        },
    }
    await session.flush()
    # Use explicit writes: a returned Python object is not a durable terminal receipt.
    await session.execute(
        update(WorkflowRunModel)
        .where(WorkflowRunModel.id == workflow.id)
        .values(
            status=workflow.status,
            current_step=workflow.current_step,
            metadata_json=workflow.metadata_json,
        )
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(ChapterModel)
        .where(ChapterModel.id == chapter.id)
        .values(
            status=chapter.status,
            production_state=chapter.production_state,
        )
        .execution_options(synchronize_session=False)
    )
    await _checkpoint_commit(session)
    return ChapterPipelineResult(
        workflow_run_id=workflow.id,
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        chapter_number=chapter.chapter_number,
        chapter_draft_id=draft.id,
        chapter_draft_version_no=draft.version_no,
        final_verdict="unknown",
        review_report_id=report.id if report else None,
        quality_score_id=quality.id if quality else None,
        chapter_review_iterations=review_iterations,
        chapter_rewrite_iterations=rewrite_iterations,
        requires_human_review=requires_human,
    )


def _record_chapter_adoption_outcome(
    *,
    workflow: WorkflowRunModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    numeric_receipt: Mapping | None,
) -> bool:
    adopted = bool(numeric_receipt) or draft.promotion_state == DraftPromotionState.PROMOTED.value
    workflow.metadata_json = {
        **(workflow.metadata_json or {}),
        "actual_adoption_receipt": {
            "adopted": adopted,
            "draft_id": str(draft.id),
            "body_fingerprint": chapter_review_body_fingerprint(draft.content_md or ""),
            "promotion_state": draft.promotion_state,
            "numeric_adoption_receipt": deepcopy(numeric_receipt),
        },
    }
    if not adopted:
        chapter.production_state = "blocked"
        chapter.status = ChapterStatus.REVISION.value
        workflow.status = WorkflowStatus.MACHINE_BLOCKED.value
        workflow.current_step = "chapter_adoption_required"
    return adopted


def chapter_review_recovery_source_hash() -> str:
    """Hash actual review/pipeline policy sources, never an operator-made nonce."""
    root = Path(__file__).resolve().parents[3]
    services = root / "src/bestseller/services"
    policy_files = {
        services / name
        for name in (
            "chapter_writer_delivery.py", "adopted_source_correction.py", "draft_continuity_revalidation.py", "chapter_repair_attempt_history.py",
            "draft_continuity_source_actions.py", "draft_continuity_supplement.py", "draft_continuity_reference_transition.py",
            "book_reference_source_transition.py", "book_source_review.py", "book_continuity_index.py",
            "draft_continuity.py", "draft_continuity_adjudication.py", "draft_continuity_axis_adjudication.py", "planning_continuity_context.py", "drafts.py",
            "pipelines.py", "workflows.py", "invariants.py", "project_invariant_initialization.py", "chapter_draft_candidate_eligibility.py", "chapter_review_preflight.py", "creation_configuration.py", "execution_windows.py", "chapter_rewrite_outcome.py", "rewrite_candidate_evidence.py", "rewrite_repair_source.py", "context.py", "reviews.py", "review_source_context.py", "editable_plan_source.py", "critic_evidence_gate.py",
            "opening_hook_density_gate.py", "narrative_obligation_migration.py", "narrative.py",
            "creation_contract_authority.py",
            "methodology_overlay.py",
            "chapter_generation_input_builder.py",
            "chapter_predraft_quality_gate.py",
            "feedback.py", "draft_promotion.py", "gate_registry.py",
            "payoff_ledger.py", "payoff_ledger_runtime.py", "payoff_ledger_gate.py",
            "hook_ledger.py", "hook_ledger_runtime.py", "chekhov_emphasis_gate.py",
            "chapter_quality_bundle.py", "chapter_splice_coherence_gate.py",
            "chapter_first_sentence_diversity_gate.py", "chapter_validator.py",
            "retention_safety_gate.py", "deduplication.py", "judge_rubrics.py",
            "quality_gates_config.py", "quality_contract_registry.py",
            "chapter_llm_quality_judge.py", "independent_quality_judge.py",
            "chapter_window_quality_judge.py", "volume_quality_judge.py",
            "commercial_review_context.py", "commercial_source_evidence.py",
            "commercial_judge_recovery.py", "commercial_protocol_transition.py",
            "commercial_protocol_transition_runtime.py", "ideology_methodology.py",
            "commercial_request_purpose.py", "unsent_reclaim_control.py", "commercial_ambiguity_retirement.py",
            "legacy_request_purpose.py", "legacy_fanqie_beat_request.py", "execution_control_purpose.py",
            "explicit_author_pov.py", "writing_profile.py", "materialized_outline_source_recovery.py",
            "materialized_outline_admission_read.py", "outline_source_entry.py", "planning_draft_admission.py",
            "chapter_outline_readiness.py",
            "planning_output_budget.py", "planning_review_literal_transport.py",
            "planning_admission_transport_recovery.py",
            "planning_admission_id_protocol.py", "planning_projection_consistency.py",
            "outline_projection_consistency_recovery.py",
            "planning_native_deadline.py", "planning_admission_spent_transport.py",
            "planning_reference_observations.py",
            "outline_revision_proposal_v2.py", "planning_surgical_repair.py",
            "outline_domain_recovery.py", "planning_domain_field_protocol.py",
            "outline_domain_transport_recovery.py", "planning_domain_field_flat_transport.py",
            "single_dispatch_authorization.py",
            "budget.py",
            "planning_readiness_gate.py",
            "narrative_contracts.py",
            "planning_surgical_budget.py", "planning_surgical_transport.py",
            "planning_capacity_continuation.py", "source_complete_retention.py",
            "planning_admission_ref_protocol.py", "fanqie_segment_projection.py", "materialized_outline_revision.py", "outline_revision_lifecycle.py", "outline_revision_proposal.py", "rewrite_outline_dispatch.py", "rewrite_outline_dispatch_recovery.py", "rewrite_outline_paid_recovery.py", "retained_request_guard.py", "outline_full_source_reconciliation.py", "rewrite_outline_binding.py",
            "llm.py", "minimax_m31_protocol.py", "planning_admission_submission.py",
            "known_terminal_planning_attempt.py",
            "terminal_planning_recovery.py", "fanqie_short_pipeline.py", "production_control.py", "fanqie_short_finalizer.py",
            "fanqie_short_source_export.py", "fanqie_short_plan_resume.py",
            "fanqie_short_planner.py", "fanqie_beat_recovery.py",
            "commercial_ambiguity_continuation.py",
            "primary_review_submission.py", "primary_review_transport.py", "chapter_primary_timeout_recovery.py",
            "chapter_review_prerequisite.py", "foundation_selection.py",
            "foundation_volume_edit.py", "foundation_revision_selection.py", "foundation_catalog_transition.py", "operator_action_approval.py", "volume_source_admission_input.py", "draft_continuity_applicable_review.py",
            "foundation_field_recovery.py", "planning_correction_input_binding.py",
            "planning_literal_capacity_binding.py",
            "chapter_seam.py",
            "commercial_novel_gate.py",
            "rewrite_patch.py",
            "retained_rewrite_projection.py",
            "retained_fact_candidate.py",
            "rewrite_fact_scope.py",
            "stalled_selection_recovery.py",
            "source_bound_rolling_outline_prompt.py", "rolling_outline_source_transport.py",
            "migrated_candidate_continuation.py",
            "common_sense_gate.py", "chapter_constraint_manifest.py", "quality_finding_schema.py",
            "deterministic_audit_actions.py", "drafts.py",
            "chapter_cancelled_review_recovery.py", "chapter_failed_review_recovery.py", "chapter_review_recovery_lineage.py", "commercial_slot_quarantine.py",
            "chapter_plan_observation_recovery.py", "chapter_pending_review_policy_recovery.py",
            "chapter_scene_contract_materializer.py", "prompt_compiler.py", "writer_source_capacity.py",
            "knowledge_epistemic_admission.py", "story_bible.py", "story_bible_projection.py",
            "world_rule_identity_recovery.py", "world_rule_current.py", "story_bible_read_views.py", "production_prompt_audit.py", "retrieval.py", "narrative_tree.py", "query_broker.py", "world_expansion.py", "consistency.py",
            "character_snapshot_sources.py", "llm_capacity.py",
            "legacy_book_state_bootstrap.py", "book_closure.py", "book_delivery_readiness.py",
            "chapter_continuity_admission.py", "adopted_knowledge_resume.py", "continuity.py",
            "snapshot_dependency_recovery.py",
            "repair.py", "story_engine_review.py",
            "commercial_review_wire.py", "commercial_review_tool.py",
            "commercial_review_transport.py", "commercial_source_span_transport.py", "retained_review_input_proof.py", "waiting_chapter_review_resume.py", "planning_capability_selection.py",
            "emotion_driven_kernel.py", "public_emotion_kernel.py", "story_design_kernel.py",
            "emotion_kernel_backfill.py", "public_emotion_backfill.py", "entry_system_backfill.py",
            "premium_genre_engine.py", "kernel_composer.py",
            "commercial_action_projection.py",
            "chapter_verified_fact_disposition.py", "chapter_review_fact_repair.py",
            "review_task_supersession.py",
            "lossless_rewrite_prompt.py", "lossless_source_encoding.py", "lossless_adopted_span_encoding.py",
            "source_complete_outline_json.py",
            "commercial_protocol_successor.py", "commercial_protocol_successor_runtime.py",
            "judge_genre_context.py", "genre_review_profiles.py", "genre_profile_thresholds.py",
            "adopted_chapter_source.py", "story_effect_realization.py",
            "story_enhancers.py", "ideology_coherence_gate.py", "ideology_judge.py",
            "ideology_kernel.py", "entry_system_kernel.py",
            "full_flow_length/__init__.py", "fanqie_whole_repair.py", "full_flow_title_sources.py", "review_issuer_archive.py",
            "review_issuer_view.py", "chapter_writer_sources.py", "source_bound_planning_content.py",
            "reader_judge.py", "chapter_revision.py", "deslop_revise.py", "conception_project_handoff.py",
            "chapter_automatic_revision_budget.py",
        )
    }
    policy_files.update((services / "quality_levers").rglob("*.py"))
    policy_files.add(root / "src/bestseller/domain/llm_quality_judge.py")
    policy_files.update(root / "src/bestseller/domain" / name
                        for name in ("context.py", "project.py", "workflow.py"))
    policy_files.add(root / "src/bestseller/domain/ideology.py")
    policy_files.add(root / "config/quality_gates.yaml")
    policy_files.add(root / "config/model_catalog.yaml")
    policy_files.add(root / "config/methodology_profiles/reader_first_planning_v1.yaml")
    policy_files.add(root / 'config/genre_profile_thresholds/action-progression.yaml')
    policy_files.add(root / 'config/genre_profile_thresholds/base-building.yaml')
    policy_files.add(root / 'config/genre_profile_thresholds/eastern-aesthetic.yaml')
    policy_files.add(root / 'config/genre_profile_thresholds/esports-competition.yaml')
    policy_files.add(root / 'config/genre_profile_thresholds/female-growth-ncp.yaml')
    policy_files.add(root / 'config/genre_profile_thresholds/relationship-driven.yaml')
    policy_files.add(root / 'config/genre_profile_thresholds/strategy-worldbuilding.yaml')
    policy_files.add(root / 'config/genre_profile_thresholds/suspense-mystery.yaml')
    policy_files.add(root / 'config/genre_profile_thresholds/unspecified.yaml')
    if any(path not in policy_files for path in
           (root / "config/genre_profile_thresholds").glob("*.yaml")):
        raise ValueError("chapter_review_genre_policy_inventory_incomplete")
    digest = hashlib.sha256()
    for path in sorted(policy_files):
        digest.update(str(path.relative_to(root)).encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


async def _assert_chapter_review_source_unadopted(
    session: AsyncSession, *, project: Any, chapter: Any, draft: Any,
) -> None:
    from bestseller.services.feedback import validate_adopted_knowledge_source

    _, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md or "",
        lock=True,
    )
    if rejection is None:
        raise ValueError("chapter_review_recovery_source_already_adopted")
    if rejection != "source_not_adopted":
        raise ValueError("chapter_review_recovery_source_mismatch")


async def resume_chapter_review_after_policy_change(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    previous_workflow_run_id: UUID,
    expected_draft_id: UUID,
    expected_body_hash: str,
    policy_revision: str,
    policy_source_hash: str,
    reason: str,
    requested_by: str = "system",
    progress: ProgressCallback | None = None,
) -> ChapterPipelineResult:
    """Explicit bounded recovery on the retained body; preserve prior paid history."""
    from bestseller.infra.db.models import ExecutionOperationModel
    from bestseller.services.budget import current_execution_scope

    if not policy_revision.strip() or not reason.strip():
        raise ValueError("chapter_review_recovery_requires_policy_and_reason")
    if policy_source_hash != chapter_review_recovery_source_hash():
        raise ValueError("chapter_review_recovery_policy_hash_mismatch")
    scope = current_execution_scope()
    if not scope or await session.scalar(
        select(func.count())
        .select_from(ExecutionOperationModel)
        .where(
            ExecutionOperationModel.scope_id == scope,
            ExecutionOperationModel.status.in_(("pending", "unknown")),
        )
    ):
        raise ValueError("chapter_review_recovery_requires_settled_scope")
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError("chapter_review_recovery_project_missing")
    chapter = await session.scalar(
        select(ChapterModel)
        .where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    previous = await session.get(WorkflowRunModel, previous_workflow_run_id)
    draft = await session.get(ChapterDraftVersionModel, expected_draft_id)
    cancelled_source = previous is not None and previous.status == WorkflowStatus.CANCELLED.value
    failed_source = previous is not None and previous.status == WorkflowStatus.FAILED.value
    if (
        chapter is None
        or previous is None
        or draft is None
        or previous.project_id != project.id
        or previous.scope_id != chapter.id
        or previous.workflow_type != WORKFLOW_TYPE_CHAPTER_PIPELINE
        or previous.status
        not in {WorkflowStatus.MACHINE_BLOCKED.value, WorkflowStatus.COMPLETED.value}
        and not cancelled_source
        and not failed_source
        or draft.project_id != project.id
        or draft.chapter_id != chapter.id
        or not draft.is_current
        or draft.promotion_state == DraftPromotionState.PROMOTED.value
        or chapter_review_body_fingerprint(draft.content_md or "")["digest"] != expected_body_hash
    ):
        raise ValueError("chapter_review_recovery_source_mismatch")
    # A failed owner remains authoritative until an exact native successor
    # lineage proves its origin was consumed. Never substitute an older WF.
    failed_lineage_proof = None
    if not failed_source:
        for stored_cycle in ((chapter.metadata_json or {}).get("chapter_critic_cycles") or {}).values():
            if (not isinstance(stored_cycle, Mapping)
                or stored_cycle.get("source_draft_id") != str(draft.id)
                or stored_cycle.get("source_body_hash") != expected_body_hash):
                continue
            owner_id = stored_cycle.get("review_workflow_run_id")
            if owner_id and owner_id != str(previous.id):
                owner = await session.get(WorkflowRunModel, UUID(owner_id))
                if owner is not None and owner.status == WorkflowStatus.FAILED.value:
                    from bestseller.services.chapter_review_recovery_lineage import (
                        validate_failed_review_recovery_lineage,
                    )
                    try:
                        failed_lineage_proof = await validate_failed_review_recovery_lineage(
                            session, settings=settings, project=project, chapter=chapter,
                            draft=draft, previous=previous, execution_scope_id=scope,
                            current_policy_hash=policy_source_hash,
                        )
                    except ValueError as error:
                        raise ValueError(
                            f"chapter_review_recovery_requires_failed_origin:{error}"
                        ) from error
                    break
    cancelled_source_proof = None
    if cancelled_source:
        from bestseller.services.chapter_cancelled_review_recovery import validate_cancelled_review_policy_source
        cancelled_source_proof = await validate_cancelled_review_policy_source(
            session, project=project, chapter=chapter, draft=draft, previous=previous,
            execution_scope_id=scope, current_policy_hash=policy_source_hash,
        )
    failed_source_proof = None
    if failed_source:
        from bestseller.services.chapter_failed_review_recovery import validate_failed_review_policy_source
        failed_source_proof = await validate_failed_review_policy_source(
            session, settings=settings, project=project, chapter=chapter, draft=draft, previous=previous,
            execution_scope_id=scope, current_policy_hash=policy_source_hash,
        )
    await _assert_chapter_review_source_unadopted(
        session,
        project=project,
        chapter=chapter,
        draft=draft,
    )
    metadata = deepcopy(chapter.metadata_json or {})
    # A waiting automatic cycle already owns this body's logical slots. A new
    # recovery label under the same policy cannot manufacture another namespace.
    current_cycles = metadata.get("chapter_critic_cycles") or {}
    if isinstance(current_cycles, Mapping) and any(
        isinstance(item, Mapping) and item.get("state") == "waiting_infrastructure"
        and item.get("policy_source_hash") == policy_source_hash
        and item.get("source_body_hash") == expected_body_hash
        for item in current_cycles.values()
    ):
        raise ValueError("chapter_review_recovery_waiting_requires_original_cycle")
    waiting_source_proof = None
    if previous.status == WorkflowStatus.MACHINE_BLOCKED.value and previous.current_step == "waiting_infrastructure":
        from bestseller.services.chapter_review_preflight import validate_waiting_review_policy_source
        waiting_source_proof = await validate_waiting_review_policy_source(
            session, settings=settings, project=project, chapter=chapter,
            draft=draft, previous=previous, execution_scope_id=scope,
            current_policy_hash=policy_source_hash,
        )
    history = list(metadata.get("chapter_review_recovery_cycles") or [])
    matching = next(
        (
            item
            for item in history
            if item.get("policy_source_hash") == policy_source_hash
            and item.get("source_body_hash") in {None, expected_body_hash}
        ),
        None,
    )
    if matching is not None:
        # Legacy entries lack resumable state and remain consumed. A new label
        # or draft UUID cannot manufacture permission for an extra cycle.
        if matching.get("state") not in {"prepared", "running"}:
            raise ValueError("chapter_review_recovery_policy_already_used")
        if (
            matching.get("source_draft_id") != str(draft.id)
            or matching.get("previous_workflow_run_id") != str(previous.id)
            or matching.get("execution_scope_id") != scope
        ):
            raise ValueError("chapter_review_recovery_cycle_binding_mismatch")
        # The first checkpoint consumes the waiting owner and changes its step.
        # Reentry must still fence the persisted origin before the new-policy
        # route can bypass the old waiting preflight or start any paid work.
        previous_metadata = previous.metadata_json or {}
        previous_recovery = previous_metadata.get("review_recovery_policy") or {}
        waiting_receipt = previous_metadata.get("review_prerequisite_wait") or {}
        if (
            "waiting_origin_proof" in matching
            or (
                isinstance(previous_recovery, Mapping)
                and "waiting_origin_proof" in previous_recovery
            )
            or (
                isinstance(waiting_receipt, Mapping)
                and waiting_receipt.get("reuse_kind") == "waiting_infrastructure"
            )
        ):
            from bestseller.services.chapter_review_preflight import _waiting_digest

            origin = matching.get("waiting_origin_proof")
            if not isinstance(origin, Mapping):
                raise ValueError("chapter_review_recovery_waiting_origin_proof_invalid")
            try:
                integrity = origin.get("proof_sha256") == _waiting_digest(
                    {key: value for key, value in origin.items() if key != "proof_sha256"}
                )
            except (TypeError, ValueError, OverflowError, RecursionError):
                integrity = False
            source_cycles = metadata.get("chapter_critic_cycles") or {}
            source_cycle_id = origin.get("source_cycle_id")
            source_cycle = (
                source_cycles.get(source_cycle_id)
                if isinstance(source_cycles, Mapping) and isinstance(source_cycle_id, str)
                else None
            )
            bound_cycle = (
                source_cycles.get(matching.get("cycle_id"))
                if isinstance(source_cycles, Mapping) else None
            )
            if any(
                isinstance(item, Mapping) and item.get("semantic_dependency_change_detected")
                for item in (matching, source_cycle, bound_cycle)
            ):
                raise ValueError("chapter_critic_semantic_dependencies_changed")
            if (
                not integrity
                or origin.get("schema") != "waiting-review-policy-source.v1"
                or origin.get("status") != "verified_waiting_review_origin"
                or origin.get("project_id") != str(project.id)
                or origin.get("chapter_id") != str(chapter.id)
                or origin.get("draft_id") != str(draft.id)
                or origin.get("previous_workflow_run_id") != str(previous.id)
                or origin.get("previous_status") != "machine_blocked"
                or origin.get("previous_step") != "waiting_infrastructure"
                or previous.scope_type != "chapter"
                or origin.get("execution_scope_id") != scope
                or (project.metadata_json or {}).get("execution_scope_id") != scope
                or previous_metadata.get("execution_scope_id") != scope
                or origin.get("source_body_fingerprint")
                != chapter_review_body_fingerprint(draft.content_md or "")
                or matching.get("source_body_hash") != expected_body_hash
                or matching.get("cycle_id") != _chapter_critic_cycle_id(
                    project.id, chapter.id, expected_body_hash, policy_source_hash
                )
                or origin.get("current_policy_source_hash") != policy_source_hash
                or not isinstance(previous_recovery, Mapping)
                or origin != previous_recovery.get("waiting_origin_proof")
                or not isinstance(source_cycle, Mapping)
                or source_cycle.get("schema_version") != "chapter-critic-cycle.v1"
                or source_cycle.get("state") != "waiting_infrastructure"
                or source_cycle.get("cycle_id") != source_cycle_id
                or source_cycle.get("source_draft_id") != str(draft.id)
                or source_cycle.get("source_body_hash") != expected_body_hash
                or source_cycle.get("review_workflow_run_id") != str(previous.id)
                or source_cycle.get("policy_source_hash")
                != origin.get("previous_policy_source_hash")
                or not isinstance(origin.get("previous_policy_source_hash"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", origin["previous_policy_source_hash"])
                or origin.get("previous_policy_source_hash") == policy_source_hash
                or source_cycle_id not in {
                    _chapter_critic_cycle_id(project.id, chapter.id, expected_body_hash),
                    _chapter_critic_cycle_id(
                        project.id, chapter.id, expected_body_hash,
                        origin["previous_policy_source_hash"],
                    ),
                }
                or origin.get("source_cycle_sha256") != _waiting_digest(source_cycle)
                or source_cycle.get("semantic_dependencies_sha256")
                != origin.get("semantic_dependencies_sha256")
                or previous_metadata.get("critic_cycle_id") != source_cycle_id
                or previous_metadata.get("review_report_id") != origin.get("latest_report_id")
                or previous_metadata.get("quality_score_id")
                != origin.get("latest_quality_score_id")
                or origin.get("origin_only") is not True
                or any(origin.get(key) is not False for key in (
                    "grade_reuse_allowed", "adoption_allowed", "attempt_reset_allowed",
                    "new_credit_allowed", "dispatch_allowed",
                ))
            ):
                raise ValueError("chapter_review_recovery_waiting_origin_proof_mismatch")
            semantic = await _chapter_critic_semantic_dependencies(session, settings, chapter)
            if origin.get("semantic_dependencies_sha256") != semantic:
                raise ValueError("chapter_critic_semantic_dependencies_changed")
        cycle = deepcopy(matching)
        return await _run_prepared_chapter_review_cycle(
            session,
            settings,
            project_slug,
            chapter_number,
            chapter=chapter,
            cycle=cycle,
            requested_by=requested_by,
            progress=progress,
        )
    # Retain all tasks and diagnostics, but quarantine invalid-critic tasks so
    # they cannot be consumed by the new cycle or its predraft readiness gate.
    from bestseller.services.review_source_ordering import archived_rewrite_task_ids
    archived = await archived_rewrite_task_ids(session, project_id=project.id, chapter_id=chapter.id)
    tasks = list(
        await session.scalars(
            select(RewriteTaskModel).where(
                RewriteTaskModel.project_id == project.id,
                RewriteTaskModel.trigger_source_id == chapter.id,
                RewriteTaskModel.trigger_type == "chapter_review",
                RewriteTaskModel.status.in_(("pending", "queued")),
                RewriteTaskModel.id.not_in(archived),
            )
        )
    )
    quarantined = []
    for task in tasks:
        report_id = (task.metadata_json or {}).get("review_report_id")
        report = await session.get(ReviewReportModel, UUID(str(report_id))) if report_id else None
        if report is not None and _chapter_critic_evidence_unknown(report):
            task.metadata_json = {
                **(task.metadata_json or {}),
                "quarantined_reason": "critic_evidence_unknown",
                "prior_status": task.status,
            }
            task.status = "quarantined"
            quarantined.append(str(task.id))
    cycle = {
        "cycle_id": _chapter_critic_cycle_id(
            project.id, chapter.id, expected_body_hash, policy_source_hash
        ),
        "state": "prepared",
        "execution_scope_id": scope,
        "policy_source_hash": policy_source_hash,
        "policy_revision": policy_revision,
        "reason": reason,
        "previous_workflow_run_id": str(previous.id),
        "source_draft_id": str(draft.id),
        "source_body_hash": expected_body_hash,
        "attempts_base": int(metadata.get("chapter_review_attempts_active") or 0),
        "quarantined_rewrite_task_ids": quarantined,
    }
    if cancelled_source_proof is not None:
        cycle["cancelled_origin_proof"] = cancelled_source_proof
    if failed_source_proof is not None:
        cycle["failed_origin_proof"] = failed_source_proof
    if failed_lineage_proof is not None:
        cycle["failed_lineage_proof"] = failed_lineage_proof
    if waiting_source_proof is not None:
        cycle["waiting_origin_proof"] = waiting_source_proof
    chapter.metadata_json = {
        **metadata,
        "chapter_review_recovery_cycles": [*history, cycle],
        "chapter_review_active_cycle": cycle,
        "chapter_first_resume_existing_draft_id": str(draft.id),
    }
    prior_metadata = deepcopy(previous.metadata_json or {})
    previous.metadata_json = {
        **prior_metadata,
        "terminal_state_history": [
            *(prior_metadata.get("terminal_state_history") or []),
            {"status": previous.status, "step": previous.current_step},
        ],
        "review_recovery_policy": cycle,
    }
    if not cancelled_source and not failed_source:
        _record_chapter_adoption_outcome(
            workflow=previous,
            chapter=chapter,
            draft=draft,
            numeric_receipt=None,
        )
    await _checkpoint_commit(session)
    return await _run_prepared_chapter_review_cycle(
        session,
        settings,
        project_slug,
        chapter_number,
        chapter=chapter,
        cycle=cycle,
        requested_by=requested_by,
        progress=progress,
    )


def _store_chapter_review_recovery_cycle(chapter: ChapterModel, cycle: Mapping) -> None:
    metadata = dict(chapter.metadata_json or {})
    history = list(metadata.get("chapter_review_recovery_cycles") or [])
    chapter.metadata_json = {
        **metadata,
        "chapter_review_recovery_cycles": [
            deepcopy(dict(cycle)) if item.get("cycle_id") == cycle["cycle_id"] else item
            for item in history
        ],
        "chapter_review_active_cycle": deepcopy(dict(cycle)),
    }


async def _run_prepared_chapter_review_cycle(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    chapter: ChapterModel,
    cycle: dict,
    requested_by: str,
    progress: ProgressCallback | None,
) -> ChapterPipelineResult:
    # The prepared receipt can share objects with the ORM JSON value. Mutating
    # it first would hide the transition from SQLAlchemy's change tracking.
    cycle = deepcopy(cycle)
    if cycle.get("state") != "waiting_infrastructure":
        cycle["state"] = "running"
    _store_chapter_review_recovery_cycle(chapter, cycle)
    await _checkpoint_commit(session)
    result = await run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        requested_by=requested_by,
        chapter_first=True,
        resume_existing_draft_only=True,
        review_source_binding={
            "draft_id": cycle["source_draft_id"],
            "body_hash": cycle["source_body_hash"],
        },
        critic_cycle_id=cycle["cycle_id"],
        supersede_pending_rewrites=False,
        export_markdown=False,
        progress=progress,
    )
    # A process exit/exception before this commit leaves a resumable running
    # cycle. Its paid operations and two-round cursor are retained separately.
    await session.flush()
    refreshed = await session.scalar(
        select(ChapterModel)
        .where(
            ChapterModel.id == chapter.id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    chapter = refreshed if refreshed is not None else chapter
    critic = ((chapter.metadata_json or {}).get("chapter_critic_cycles") or {}).get(cycle["cycle_id"]) or {}
    cycle["state"] = "waiting_infrastructure" if critic.get("state") == "waiting_infrastructure" else "completed"
    cycle["result_workflow_run_id"] = (
        str(result.workflow_run_id) if hasattr(result, "workflow_run_id") else None
    )
    _store_chapter_review_recovery_cycle(chapter, cycle)
    await _checkpoint_commit(session)
    return result


async def _extract_chapter_knowledge_if_enabled(
    session: Any,
    settings: Any,
    *,
    project_id: Any,
    chapter: Any,
    chapter_md: str,
    workflow_run_id: Any,
    source_chapter_draft_version_id: Any = None,
) -> Any:
    """Extract only an adopted source and expose its durable knowledge receipt.

    The paid gateway must run outside a SAVEPOINT. The service owns the
    atomic, pure-DB application after verifying the source again. Terminal
    spending/cancellation boundaries must reach the enclosing workflow.
    """

    if not getattr(getattr(settings, "pipeline", None), "enable_chapter_feedback", False):
        return
    if not str(chapter_md or "").strip():
        return
    try:
        from bestseller.services.feedback import extract_chapter_feedback

        return await extract_chapter_feedback(
            session,
            settings,
            project_id=project_id,
            chapter=chapter,
            chapter_md=chapter_md,
            source_chapter_draft_version_id=source_chapter_draft_version_id,
            workflow_run_id=workflow_run_id,
        )
    except ExecutionBudgetError:
        raise
    except Exception as exc:
        logger.warning(
            "Chapter %s knowledge extraction failed (non-fatal): %s",
            getattr(chapter, "chapter_number", "?"),
            exc,
        )
        await _recover_session_after_nonfatal_error(session, exc)


async def _recover_session_after_nonfatal_error(
    session: AsyncSession,
    exc: Exception,
) -> None:
    """Rollback when a tolerated helper error leaves the DB session dirty."""

    if not _is_db_session_failure(session, exc):
        return
    rollback = getattr(session, "rollback", None)
    if rollback is None:
        return
    await rollback()


def _is_db_session_failure(session: AsyncSession, exc: Exception) -> bool:
    """Return true when an exception means the current async DB session is unsafe."""

    return isinstance(exc, (PendingRollbackError, DBAPIError, MissingGreenlet)) or not getattr(
        session, "is_active", True
    )


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
    draft = await session.scalar(
        select(SceneDraftVersionModel).where(
            SceneDraftVersionModel.scene_card_id == scene_id,
            SceneDraftVersionModel.is_current.is_(True),
        )
    )
    if draft is not None:
        return draft
    # Never self-heal by selecting the latest version.  ``is_current`` is a
    # work-in-progress pointer and a missing pointer is an integrity event, not
    # evidence that the last failed/stalled rewrite is usable.  In particular,
    # restoring it here used to turn a stalled candidate into implicit input for
    # assembly and knowledge extraction.  Callers either regenerate a candidate
    # explicitly or surface the missing draft for repair.
    return None


def _pipeline_quality_mode(settings: AppSettings) -> str:
    """Return the explicit quality mode while mapping the legacy stall switch.

    ``accept_on_stall=False`` was the old request for a hard stop.  Preserve
    that safety intent during the deprecation window; the default legacy value
    remains compatible with ``closure`` but no longer means quality approval.
    """

    pipeline = getattr(settings, "pipeline", None)
    if getattr(pipeline, "accept_on_stall", None) is False:
        return "strict"
    if getattr(pipeline, "chapter_review_block_on_failure", None) is True:
        return "strict"
    mode = str(getattr(pipeline, "quality_mode", "closure"))
    return mode if mode in {"closure", "strict"} else "closure"


async def _promote_reviewed_scene_draft(
    session: AsyncSession,
    *,
    project: ProjectModel,
    scene: SceneCardModel,
    draft: SceneDraftVersionModel,
    quality: object,
    workflow_run_id: UUID | None,
) -> bool:
    """Promote only the exact reviewed scene version under the parent lock.

    Unit pipeline tests frequently use lightweight score stubs.  They validate
    control flow but cannot satisfy a database-level version FK, so the real
    transaction is intentionally exercised by the promotion integration tests.
    Production always supplies ``QualityScoreModel`` and therefore never takes
    this compatibility branch.
    """

    if not isinstance(quality, QualityScoreModel):
        return True
    await session.flush()
    if draft.promotion_state == DraftPromotionState.PROMOTED.value:
        return True
    await mark_candidate_under_review(
        session,
        project_id=project.id,
        draft_kind="scene",
        draft_id=draft.id,
        workflow_run_id=workflow_run_id,
    )
    await mark_draft_eligible(
        session,
        project_id=project.id,
        draft_kind="scene",
        draft_id=draft.id,
        quality_score_id=quality.id,
        workflow_run_id=workflow_run_id,
    )
    outcome = await promote_scene_draft(
        session,
        project_id=project.id,
        scene_card_id=scene.id,
        judge_key=str(quality.judge_key or "").strip(),
        workflow_run_id=workflow_run_id,
    )
    return outcome.promoted_draft_id == draft.id


async def _quarantine_scene_candidate(
    session: AsyncSession,
    *,
    project: ProjectModel,
    draft: SceneDraftVersionModel,
    workflow_run_id: UUID | None,
    reason_code: str,
) -> None:
    """Record closure debt without treating a stalled draft as approved."""

    if not isinstance(draft, SceneDraftVersionModel):
        return
    current = str(draft.promotion_state or "candidate")
    if current in {DraftPromotionState.QUARANTINED.value, DraftPromotionState.PROMOTED.value}:
        return
    await quarantine_draft(
        session,
        project_id=project.id,
        draft_kind="scene",
        draft_id=draft.id,
        reason_codes=[reason_code],
        evidence={"pipeline": "scene", "reason": reason_code},
        workflow_run_id=workflow_run_id,
    )


def rank_chapter_draft_candidate(
    draft: Any,
    quality: Any,
    *,
    hard_min: int,
    hard_max: int,
    target_words: int,
) -> tuple[int, float, int, int]:
    """Sort key for best-of-N chapter promotion; higher is better.

    Extracted from the promotion function so the ordering is testable without a
    database — the ceiling bug below lived undetected inside a closure.

    1. **Inside the contract length band.** Symmetric on purpose. The original
       rule checked only ``hard_min``, and the ceiling — already computed and
       then discarded — was ignored, so an over-long draft could win term 2 on
       a score its own excess length inflates. 《纸背》 shipped 4942 words
       against a 2600 target while a 2702-word draft sat unused (2026-07-26).
    2. **Quality score**, highest first; unscored sorts last.
    3. **Distance from the assigned target**, closest first.
    4. **Version number**, highest first — pure tiebreak.

    Degrades safely: an unknown bound simply stops constraining that end, so a
    missing band never invents a length preference.
    """

    # 字数**现算**，不信 `word_count` 列。
    #
    # 2026-08-22 真机（custom-xuanhuan-1787383584 ch7）：v1 的 word_count
    # 记着 2558 而正文实际是 18902 汉字（三行各重复 121 遍的退化稿）。
    # 于是它靠这个过期字段落进 1800-3500 的窗口拿到 in_band=1，而两份
    # 真实字数超上限的重写稿都是 0——**退化稿因此胜出，被换回在架稿**。
    #
    # `word_count` 是 content_md 的副本，而 9 个原地修改 content_md 的地方
    # 只有 2 个同步了它：全库 19% 的稿字数记录与实际不符（《书院笔仙》
    # 276 稿里 52 稿，最大偏差 16688）。排序判据不能建在这种字段上。
    #
    # 读不到正文时退回存储值——取不到内容不该把一份稿判死。
    _body = getattr(draft, "content_md", None)
    if isinstance(_body, str) and _body:
        words = count_zh_chars(_body)
    else:
        words = int(getattr(draft, "word_count", 0) or 0)
    above_floor = words >= hard_min if hard_min else True
    below_ceiling = words <= hard_max if hard_max else True
    in_band = 1 if (hard_min or hard_max) and above_floor and below_ceiling else 0
    score = float(getattr(quality, "score_overall", None) or -1.0)
    target_distance = abs(words - target_words) if target_words else 0
    version = int(getattr(draft, "version_no", 0) or 0)
    return (in_band, score, -target_distance, version)


def _quality_row_precedence(quality: Any) -> tuple[int, int, float]:
    """Which of a draft's score rows speaks for it; higher wins."""

    if quality is None:
        return (0, 0, 0.0)
    is_current = 1 if bool(getattr(quality, "is_current", False)) else 0
    created_at = getattr(quality, "created_at", None)
    try:
        recency = created_at.timestamp() if created_at is not None else 0.0
    except (AttributeError, TypeError, ValueError, OSError):
        recency = 0.0
    return (1, is_current, recency)


def dedupe_drafts_by_current_score(
    rows: Iterable[tuple[Any, Any]],
) -> list[tuple[Any, Any]]:
    """Collapse an outer-joined (draft, score) result to one row per draft.

    ``quality_scores`` is versioned: re-scoring a draft after a repair pass adds
    a row and flips the previous one to ``is_current=False``. The promotion
    query joins without an ``is_current`` predicate, so a re-scored draft
    arrives once per verdict and ``max()`` would happily rank it on a number
    that has already been superseded — the *higher* stale one, since that is
    what wins term 2.

    Filtering the join to current rows is the tempting fix and the wrong one:
    5 of the 15 scored chapter drafts in the live database carry only
    superseded rows, and a filter would silently reclassify them as unscored,
    dropping them below every scored rival. So prefer the current verdict, fall
    back to the most recent, and never lose a draft.
    """

    best: dict[Any, tuple[Any, Any]] = {}
    for draft, quality in rows:
        key = getattr(draft, "id", None)
        if key is None:
            key = id(draft)
        incumbent = best.get(key)
        if incumbent is None or _quality_row_precedence(quality) > _quality_row_precedence(
            incumbent[1]
        ):
            best[key] = (draft, quality)
    return list(best.values())


async def _promote_best_scoring_chapter_draft_on_stall(
    session: AsyncSession,
    *,
    chapter: ChapterModel,
    current_draft: ChapterDraftVersionModel,
    project: ProjectModel | None = None,
    settings: AppSettings | None = None,
) -> ChapterDraftVersionModel:
    """Select only currently source-bound, available reviewed working candidates.

    Eligibility precedes the existing length-band/score/target/version ranking.
    A legacy or unknown high score cannot displace the expected current draft.
    If no candidate is proved eligible, retain current without claiming a pass.
    Selection does not adopt a draft or grant numeric-debt permission; those
    remain the downstream promotion/source guards' responsibility.
    """

    from bestseller.services.chapter_draft_candidate_eligibility import (
        POLICY, eligible_reviewed_candidates, record_selection,
    )

    audit = {"policy": POLICY, "selection_only": True, "adoption_authorized": False,
             "expected_current_draft_id": str(current_draft.id),
             "chosen_draft_id": str(current_draft.id), "candidates": []}
    stale_current = await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.project_id == chapter.project_id,
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        ).with_for_update()
    )
    if stale_current is None or stale_current.id != current_draft.id:
        audit["reason"] = "expected_current_changed"
        audit["observed_current_draft_id"] = str(stale_current.id) if stale_current is not None else None
        record_selection(chapter, audit)
        raise ValueError("chapter_best_draft_current_changed")
    rows = (
        await session.execute(
            select(ChapterDraftVersionModel, QualityScoreModel)
            .outerjoin(
                QualityScoreModel,
                QualityScoreModel.chapter_draft_version_id == ChapterDraftVersionModel.id,
            )
            .where(ChapterDraftVersionModel.chapter_id == chapter.id)
        )
    ).all()
    if not rows:
        audit["reason"] = "no_draft_candidates"
        record_selection(chapter, audit)
        return current_draft
    # One row per draft, on its current verdict — the join is versioned and
    # would otherwise let a superseded, higher score win term 2.
    rows = dedupe_drafts_by_current_score(rows)
    try:
        if settings is None:
            raise ValueError("actual_review_settings_missing")
        if await chapter_contract_has_adopted_source(session, chapter=chapter):
            raise ValueError("chapter_has_adopted_source")
        semantic_hash = await _chapter_critic_semantic_dependencies(session, settings, chapter)
        policy_hash = chapter_review_recovery_source_hash()
    except (ValueError, TypeError) as exc:
        audit["reason"] = "selection_context_unverified"
        audit["context_error"] = str(exc)
        audit["candidates"] = [{"draft_id": str(d.id), "eligible": False,
                                 "reasons": [str(exc)]} for d, _q in rows]
        record_selection(chapter, audit)
        return current_draft
    audit.update(semantic_dependencies_sha256=semantic_hash, policy_source_hash=policy_hash)
    rows, audit["candidates"] = await eligible_reviewed_candidates(
        session, chapter=chapter, rows=rows, semantic_hash=semantic_hash, policy_hash=policy_hash,
        fingerprint=chapter_review_body_fingerprint, retained_result=_retained_chapter_critic_result,
        evidence_unknown=_chapter_critic_evidence_unknown,
    )
    if not rows:
        audit["reason"] = "no_eligible_reviewed_candidate"
        record_selection(chapter, audit)
        return current_draft

    hard_min = 0
    hard_max = 0
    if project is not None:
        try:
            hard_min, _target, hard_max = _chapter_length_contract_band(
                project,
                int(getattr(chapter, "target_word_count", 0) or 0) or None,
            )
        except Exception:
            logger.debug("chapter %s: length band unavailable for best-of-N", chapter.id)
            hard_min = 0
            hard_max = 0

    target_words = int(getattr(chapter, "target_word_count", 0) or 0)

    best_draft, best_quality = max(
        rows,
        key=lambda row: rank_chapter_draft_candidate(
            row[0],
            row[1],
            hard_min=hard_min,
            hard_max=hard_max,
            target_words=target_words,
        ),
    )
    audit.update(chosen_draft_id=str(best_draft.id), reason="eligible_best_of_n")
    record_selection(chapter, audit)
    if best_draft.id == current_draft.id:
        chapter.current_word_count = int(getattr(best_draft, "word_count", 0) or 0)
        return current_draft

    if stale_current is not None:
        stale_current.is_current = False
        # ``uq_chapter_draft_current`` is a partial unique index on
        # (chapter_id) WHERE is_current — same shape as the scene index, so
        # the same two-flush ordering is required to avoid colliding on it.
        await session.flush()
    best_draft.is_current = True
    chapter.current_word_count = int(getattr(best_draft, "word_count", 0) or 0)
    await session.flush()
    logger.info(
        "Chapter %s exhausted its repair budget: promoting draft v%d "
        "(words=%d, score=%s) over most-recent attempt v%d (words=%d) — "
        "best attempt ships, not most-recent.",
        chapter.id,
        best_draft.version_no,
        int(getattr(best_draft, "word_count", 0) or 0),
        (
            f"{float(best_quality.score_overall):.2f}"
            if best_quality is not None and best_quality.score_overall is not None
            else "unscored"
        ),
        current_draft.version_no,
        int(getattr(current_draft, "word_count", 0) or 0),
    )
    return best_draft


async def _promote_best_scoring_scene_draft_on_stall(
    session: AsyncSession,
    *,
    scene: SceneCardModel,
    current_draft: SceneDraftVersionModel,
    current_quality: QualityScoreModel,
) -> tuple[SceneDraftVersionModel, QualityScoreModel]:
    """Ship the best-scoring attempt, not just the most recent one.

    ``rewrite_scene_from_task`` always flips ``is_current`` to the newest
    attempt with no comparison against prior attempts (see its ``UPDATE ...
    is_current=False`` immediately before inserting the new draft). A rewrite
    is not guaranteed to improve the score — that is precisely what the
    stalled-rewrite detector above is watching for. When the bounded retry
    loop exhausts its budget without a passing verdict, the draft that
    happens to be ``is_current`` is whichever one was generated *last*, which
    can be strictly worse (by score, or by introducing a fresh defect such as
    a duplicated beat) than an earlier attempt in the same loop. Compare every
    attempt's own quality score and promote whichever scored highest before
    quarantining the rest, instead of silently shipping "most recent" as
    "best". Returns the (draft, quality) pair that is now ``is_current`` so
    callers can keep both in sync for downstream reporting.
    """

    # NOTE: ``QualityScoreModel.is_current`` is scoped to the *scene* (its
    # unique index is on (target_type, target_id) where target_id is the
    # scene, not the draft) — it marks the latest scene-level assessment, not
    # "is this score still valid for the draft it was computed against". Do
    # NOT filter on it here: every earlier attempt's score row still
    # correctly records what that specific draft scored and remains valid
    # historical evidence for this best-of-N comparison, even though it is
    # no longer the scene's "current" evaluation.
    rows = (
        await session.execute(
            select(SceneDraftVersionModel, QualityScoreModel)
            .join(
                QualityScoreModel,
                QualityScoreModel.scene_draft_version_id == SceneDraftVersionModel.id,
            )
            .where(SceneDraftVersionModel.scene_card_id == scene.id)
            .order_by(
                QualityScoreModel.score_overall.desc(),
                SceneDraftVersionModel.version_no.desc(),
            )
        )
    ).all()
    if not rows:
        return current_draft, current_quality
    best_draft, best_quality = rows[0]
    if best_draft.id == current_draft.id:
        return current_draft, current_quality

    stale_current = await session.scalar(
        select(SceneDraftVersionModel).where(
            SceneDraftVersionModel.scene_card_id == scene.id,
            SceneDraftVersionModel.is_current.is_(True),
        )
    )
    if stale_current is not None:
        stale_current.is_current = False
        # ``uq_scene_draft_current`` is a partial unique index on
        # (scene_card_id) WHERE is_current. Flushing the False update on its
        # own row first, before flipping the winner to True, guarantees the
        # two UPDATEs never race inside the same statement batch and collide
        # on that constraint.
        await session.flush()
    best_draft.is_current = True
    await session.flush()
    logger.info(
        "Scene %s stalled rewrite loop: promoting draft v%d (score=%.2f) over "
        "most-recent attempt v%d — best-scoring attempt ships, not most-recent.",
        scene.id,
        best_draft.version_no,
        float(best_quality.score_overall or 0.0),
        current_draft.version_no,
    )
    return best_draft, best_quality


async def _chapter_source_mode_is_promotable(
    session: AsyncSession,
    *,
    chapter_draft: ChapterDraftVersionModel,
) -> tuple[bool, str]:
    """Validate source semantics without ever parsing chapter-first placeholders.

    Scene-assembled drafts carry real scene-version UUIDs.  Chapter-first
    drafts deliberately carry ``chapter_first_scene:<id>`` provenance markers;
    those are not draft IDs and must never be coerced into UUIDs or used as a
    latest-draft fallback.
    """

    source_ids = [str(value) for value in (chapter_draft.assembled_from_scene_draft_ids or [])]
    if any(value.startswith("chapter_first_scene:") for value in source_ids):
        return True, "chapter_first"
    if not source_ids:
        return False, "scene_assembled_missing_sources"
    try:
        version_ids = [UUID(value) for value in source_ids]
    except (TypeError, ValueError):
        return False, "scene_assembled_invalid_source_id"
    rows = (
        await session.scalars(
            select(SceneDraftVersionModel).where(
                SceneDraftVersionModel.id.in_(version_ids),
                SceneDraftVersionModel.promotion_state.in_(
                    (
                        DraftPromotionState.ELIGIBLE.value,
                        DraftPromotionState.PROMOTED.value,
                    )
                ),
            )
        )
    ).all()
    return (len(rows) == len(set(version_ids))), "scene_assembled"


async def _promote_reviewed_chapter_draft(
    session: AsyncSession,
    *,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    quality: object,
    workflow_run_id: UUID | None,
) -> tuple[bool, str]:
    """Promote an exact chapter version only after its source-mode contract."""

    length_metadata = getattr(draft, "promotion_metadata", None) or {}
    length_request = length_metadata.get("book_length_control")
    if "book_length_control" in length_metadata:
        length_request = full_flow_length.require_feasible_request(length_request)
        length_preview = full_flow_length.evaluate_candidate(length_request, draft.content_md)
        draft.promotion_metadata = {**(draft.promotion_metadata or {}),
                                    "book_length_control_candidate": length_preview}
        if not length_preview["length_passed"]:
            return False, length_preview["issue_code"]
        await session.flush()
        length_check = await full_flow_length.revalidate_request(
            session, settings, project=project, chapter=chapter, receipt=length_request)
        if not length_check["valid"]:
            return False, length_check["issue_code"]
    if not isinstance(quality, QualityScoreModel):
        return True, "test_stub"
    from bestseller.services.chapter_verified_fact_disposition import decide_verified_fact_disposition

    fact_disposition = decide_verified_fact_disposition(
        content_md=draft.content_md or "", evidence=quality.evidence_summary or {},
    )
    if fact_disposition["status"] in {"blocked", "unknown"}:
        return False, "verified_fact_conflict" if fact_disposition["status"] == "blocked" else "verified_fact_evidence_unavailable"
    admission_body_sha256 = hashlib.sha256((draft.content_md or "").encode()).hexdigest()
    await _ensure_chapter_continuity_before_adoption(
        session, settings, project_id=project.id, chapter=chapter, draft=draft,
        workflow_run_id=workflow_run_id,
    )
    effect_report = audit_persisted_story_effect_realization(
        project,
        chapter,
        draft.content_md or "",
    )
    if effect_report.get("selected_effects"):
        draft.promotion_metadata = {
            **(draft.promotion_metadata or {}),
            "effect_realization_gate": effect_report,
        }
        chapter.metadata_json = {
            **(chapter.metadata_json or {}),
            "effect_realization_gate": effect_report,
            "effect_realization_blocked": effect_report.get("passed") is not True,
        }
        if not effect_report.get("passed"):
            # A candidate may have been created by an older path with
            # ``is_current=True``.  Remove that pointer before returning so a
            # failed effect cannot silently ship; the report remains attached
            # to the candidate and the chapter for audit/retry.
            draft.is_current = False
            chapter.production_state = "blocked"
            await session.flush()
            return False, "story_effect_realization_blocked"
    source_ok, source_mode = await _chapter_source_mode_is_promotable(
        session,
        chapter_draft=draft,
    )
    if not source_ok:
        return False, source_mode
    story_engine_decision = await resolve_story_engine_rollout_decision_from_db(
        session,
        project,
        settings,
        chapter_number=chapter.chapter_number,
    )
    story_engine_mode = story_engine_decision.effective_mode
    if story_engine_mode == "dual_write":
        chapter_metadata = (
            chapter.metadata_json if isinstance(chapter.metadata_json, Mapping) else {}
        )
        shadow_core = chapter_metadata.get("story_engine_shadow_projection")
        if isinstance(shadow_core, Mapping) and workflow_run_id is not None:
            try:
                shadow_observation = await extract_story_engine_receipt_observation(
                    session,
                    settings,
                    creative_core=shadow_core,
                    draft_content_md=draft.content_md,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    draft_version_id=draft.id,
                    workflow_run_id=workflow_run_id,
                )
                shadow_review = review_story_engine_transition(
                    creative_core=shadow_core,
                    observation=shadow_observation,
                    draft_content_md=draft.content_md,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    draft_version_id=draft.id,
                    workflow_run_id=workflow_run_id,
                    allow_non_authoritative=True,
                )
                draft.promotion_metadata = {
                    **(draft.promotion_metadata or {}),
                    "story_engine_shadow_review": {
                        "verdict": shadow_review.verdict.value,
                        "blocking_codes": list(shadow_review.blocking_codes),
                        "replay_passed": shadow_review.replay_passed,
                        "post_state_hash": str(shadow_review.content.get("post_state_hash") or ""),
                        "projection_hash": str(
                            shadow_review.content.get("_meta", {}).get("projection_hash") or ""
                        ),
                        "workflow_run_id": str(workflow_run_id),
                        "canonical_receipt_appended": False,
                    },
                }
            except Exception as exc:  # noqa: BLE001 - shadow must not block legacy
                draft.promotion_metadata = {
                    **(draft.promotion_metadata or {}),
                    "story_engine_shadow_review": {
                        "verdict": "unavailable",
                        "blocking_codes": ["STORY_ENGINE_SHADOW_REVIEW_ERROR"],
                        "error": str(exc)[:500],
                        "workflow_run_id": str(workflow_run_id),
                        "canonical_receipt_appended": False,
                    },
                }
    if story_engine_mode in {"canary", "canonical"}:
        if workflow_run_id is None:
            raise StoryEngineReceiptRejected(
                "StoryEngine-controlled promotion requires workflow lineage",
                blocking_codes=("STORY_ENGINE_RECEIPT_WORKFLOW_MISSING",),
            )
        chapter_metadata = (
            chapter.metadata_json if isinstance(chapter.metadata_json, Mapping) else {}
        )
        creative_core = chapter_metadata.get("story_engine_projection")
        if not isinstance(creative_core, Mapping) or (
            creative_core.get("can_drive_generation") is not True
        ):
            raise StoryEngineReceiptRejected(
                "StoryEngine-controlled promotion requires a current-chapter creative projection",
                blocking_codes=("STORY_ENGINE_RECEIPT_PROJECTION_MISSING",),
            )
        draft_metadata = (
            draft.promotion_metadata if isinstance(draft.promotion_metadata, Mapping) else {}
        )
        observation = draft_metadata.get("story_engine_observation")
        if not isinstance(observation, Mapping):
            observation = await extract_story_engine_receipt_observation(
                session,
                settings,
                creative_core=creative_core,
                draft_content_md=draft.content_md,
                project_id=project.id,
                chapter_id=chapter.id,
                draft_version_id=draft.id,
                workflow_run_id=workflow_run_id,
            )
        receipt_review = review_story_engine_transition(
            creative_core=creative_core,
            observation=observation,
            draft_content_md=draft.content_md,
            project_id=project.id,
            chapter_id=chapter.id,
            draft_version_id=draft.id,
            workflow_run_id=workflow_run_id,
        )
        if receipt_review.verdict is not StoryEngineReceiptVerdict.MATCHED:
            raise StoryEngineReceiptRejected(
                "StoryEngine transition receipt rejected the chapter draft",
                review=receipt_review,
            )
        await promote_chapter_draft_with_story_engine_receipt(
            session,
            project_id=project.id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            draft=draft,
            quality_score_id=quality.id,
            judge_key=str(quality.judge_key or "").strip(),
            workflow_run_id=workflow_run_id,
            review=receipt_review,
            expected_body_sha256=admission_body_sha256,
        )
        if length_request is not None:
            await session.flush()
            await full_flow_length.refresh_after_adoption(
                session, settings, project=project, chapter=chapter, receipt=length_request)
        draft.promotion_metadata = {
            **(draft.promotion_metadata or {}),
            "source_mode": source_mode,
            "story_engine_mode": story_engine_mode,
        }
        return True, source_mode
    await session.flush()
    if draft.promotion_state != DraftPromotionState.PROMOTED.value:
        if draft.promotion_state != DraftPromotionState.ELIGIBLE.value:
            await mark_candidate_under_review(
                session,
                project_id=project.id,
                draft_kind="chapter",
                draft_id=draft.id,
                workflow_run_id=workflow_run_id,
            )
        await mark_draft_eligible(
            session,
            project_id=project.id,
            draft_kind="chapter",
            draft_id=draft.id,
            quality_score_id=quality.id,
            workflow_run_id=workflow_run_id,
        )
        outcome = await promote_chapter_draft(
            session,
            project_id=project.id,
            chapter_id=chapter.id,
            judge_key=str(quality.judge_key or "").strip(),
            workflow_run_id=workflow_run_id,
            expected_draft_id=draft.id,
            expected_body_sha256=admission_body_sha256,
        )
        if outcome.promoted_draft_id != draft.id:
            return False, "chapter_promotion_not_selected"
    draft.promotion_metadata = {
        **(draft.promotion_metadata or {}),
        "source_mode": source_mode,
    }
    return True, source_mode


def _scene_requires_auto_repair_generation(
    scene: SceneCardModel,
    current_draft: SceneDraftVersionModel | None,
) -> bool:
    """Force a new version while retaining the old current draft as fallback."""

    if current_draft is None or scene.status != SceneStatus.NEEDS_REWRITE.value:
        return False
    metadata = scene.metadata_json if isinstance(scene.metadata_json, dict) else {}
    return bool(
        str(metadata.get("auto_repair_hint") or "").strip()
        or metadata.get("auto_repair_block_codes")
    )


@pipeline_operation_stage
async def run_scene_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    *,
    requested_by: str = "system",
    parent_workflow_run_id: UUID | None = None,
    allow_structural_repair: bool = False,
    progress: ProgressCallback | None = None,
) -> ScenePipelineResult:
    project, chapter, scene = await _load_scene_identifiers(
        session,
        project_slug,
        chapter_number,
        scene_number,
    )
    _emit_progress(
        progress,
        "scene_pipeline_started",
        {
            "project_slug": project_slug,
            "chapter_number": chapter_number,
            "scene_number": scene_number,
        },
    )
    _assert_project_not_blocked_for_structural_repair(
        project,
        project_slug=project_slug,
        operation=f"scene pipeline {chapter_number}.{scene_number}",
        allow_structural_repair=allow_structural_repair,
    )
    await _ensure_emotion_kernel_backfill_for_pipeline(
        session,
        settings,
        project,
        requested_by=requested_by,
    )
    await _ensure_public_emotion_kernel_backfill_for_pipeline(
        session,
        settings,
        project,
        requested_by=requested_by,
    )
    await _ensure_entry_system_backfill_for_pipeline(
        session,
        settings,
        project,
        requested_by=requested_by,
    )
    await _enforce_truth_version_guard(session, settings, project)

    # Resume: skip already-complete scenes to avoid re-drafting
    if settings.pipeline.resume_enabled and scene.status == SceneStatus.APPROVED.value:
        logger.info(
            "Scene %d.%d already complete — skipping (resume)",
            chapter_number,
            scene_number,
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
    force_auto_repair_generation = _scene_requires_auto_repair_generation(scene, draft)

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
                "force_auto_repair_generation": force_auto_repair_generation,
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
        except Exception as exc:
            # Match the pre-Opt-B behavior in review_scene_draft: tolerate context
            # build failures (tests / mocks may not provide everything). Downstream
            # functions handle context_packet=None correctly. The SAVEPOINT above
            # ensures any failed query inside the context build does not poison the
            # outer transaction (asyncpg PendingRollbackError).
            await _recover_session_after_nonfatal_error(session, exc)
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
                _scene_meta = getattr(scene, "metadata_json", None) or {}
                _repair_hint = str(_scene_meta.get("auto_repair_hint") or "").strip()
                if _repair_hint:
                    _repair_codes = _scene_meta.get("auto_repair_block_codes") or ()
                    _prefix = (
                        f"[章节自动修复 {','.join(_repair_codes)}] "
                        if _repair_codes
                        else "[章节自动修复] "
                    )
                    # Prepend so the writer sees the repair reason before
                    # any subsequent non-critical warnings.
                    shared_context.contradiction_warnings.insert(0, _prefix + _repair_hint)
            except Exception:
                logger.debug(
                    "auto_repair_hint injection failed for ch%d sc%d (non-fatal)",
                    chapter_number,
                    scene_number,
                    exc_info=True,
                )

        # ── Anti-slop scene beat sheet ──
        # Translate abstract chapter/scene contracts into camera-level beats
        # before drafting. This is intentionally deterministic and non-fatal:
        # missing context still leaves the legacy prompt path intact.
        if shared_context is not None:
            try:
                _prose_cfg = get_quality_gates_config().prose_quality
                if _prose_cfg.beat_planner_enabled:
                    from bestseller.services.scene_beat_planner import (
                        build_scene_beat_sheet,
                    )
                    from bestseller.services.scene_beat_renderer import (
                        render_scene_beat_sheet_block,
                    )

                    _scene_contract = (
                        shared_context.scene_contract.model_dump(mode="json")
                        if shared_context.scene_contract is not None
                        else None
                    )
                    _chapter_contract = (
                        shared_context.chapter_contract.model_dump(mode="json")
                        if shared_context.chapter_contract is not None
                        else None
                    )
                    _beat_sheet = build_scene_beat_sheet(
                        chapter_number=chapter_number,
                        scene_number=scene_number,
                        scene_title=getattr(scene, "title", None),
                        scene_type=getattr(scene, "scene_type", None),
                        time_label=getattr(scene, "time_label", None),
                        participants=list(getattr(scene, "participants", None) or []),
                        chapter_goal=getattr(chapter, "chapter_goal", None),
                        story_purpose=(getattr(scene, "purpose", None) or {}).get("story"),
                        emotion_purpose=(getattr(scene, "purpose", None) or {}).get("emotion"),
                        entry_state=getattr(scene, "entry_state", None) or {},
                        exit_state=getattr(scene, "exit_state", None) or {},
                        scene_contract=_scene_contract,
                        chapter_contract=_chapter_contract,
                        word_target=getattr(scene, "target_word_count", None),
                    )
                    _lang = getattr(project, "language", None) or settings.generation.language
                    shared_context.scene_beat_block = (
                        render_scene_beat_sheet_block(_beat_sheet, language=_lang) or None
                    )
            except Exception:
                logger.debug(
                    "scene beat sheet injection failed for ch%d sc%d (non-fatal)",
                    chapter_number,
                    scene_number,
                    exc_info=True,
                )

        # ── Prewrite planning-kernel directives ──
        # These are generated from the project-level prewrite readiness gate.
        # They make macro-planning failures immediately visible to active
        # scene drafting instead of waiting for the next full replanning run.
        if shared_context is not None:
            from bestseller.services.planning_capability_selection import (
                PlanningCapabilitySelectionError,
            )

            try:
                from bestseller.services.prewrite_review import project_scene_prewrite_directives

                _projection = project_scene_prewrite_directives(
                    project,
                    language=getattr(project, "language", None) or settings.generation.language,
                )
                _directives = _projection["directives"]
                _directive_texts = [str(item).strip() for item in _directives if str(item).strip()]
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "prewrite_directive_projection": _projection["receipt"],
                    "prewrite_repair_directives_applied": bool(_directive_texts),
                }
                if _directive_texts:
                    _prefix = (
                        "[Prewrite planning gate] "
                        if is_english_language(
                            getattr(project, "language", None) or settings.generation.language
                        )
                        else "[写前规划门禁] "
                    )
                    for directive in reversed(_directive_texts[:5]):
                        shared_context.contradiction_warnings.insert(
                            0,
                            _prefix + directive,
                        )
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "prewrite_repair_directives_applied": True,
                    }
            except PlanningCapabilitySelectionError:
                raise
            except Exception:
                logger.debug(
                    "Prewrite repair directive injection failed for ch%d sc%d (non-fatal)",
                    chapter_number,
                    scene_number,
                    exc_info=True,
                )

        # ── Inject character identity constraints (Tier 0 — never dropped) ──
        _identity_registry = []
        try:
            from bestseller.services.identity_guard import (
                build_identity_constraint_block,
                load_identity_registry,
            )

            _identity_registry = await load_identity_registry(session, project.id)
            if shared_context is not None and _identity_registry:
                shared_context.identity_registry = _identity_registry
                shared_context.identity_constraint_block = build_identity_constraint_block(
                    _identity_registry,
                    language=getattr(project, "language", None) or "zh-CN",
                    participant_names=list(scene.participants or []),
                )
        except Exception as exc:
            await _recover_session_after_nonfatal_error(session, exc)
            logger.warning(
                "Identity guard load failed for ch%d sc%d (non-fatal)",
                chapter_number,
                scene_number,
                exc_info=True,
            )

        # ── Narrative contract gate (zero LLM cost, pre-draft) ──
        if (draft is None or force_auto_repair_generation) and getattr(
            settings.pipeline, "require_pre_draft_scene_contract", True
        ):
            try:
                from bestseller.services.methodology_overlay import (
                    resolve_methodology_contract_mode,
                )
                from bestseller.services.narrative_contracts import (
                    repair_legacy_scene_contract_pre_draft,
                    repair_missing_scene_methodology_contract_pre_draft,
                    repair_missing_scene_participants_pre_draft,
                    validate_scene_contract_pre_draft,
                )

                _repair_count = repair_legacy_scene_contract_pre_draft(
                    scene,
                    chapter_number=chapter_number,
                )
                _offstage_names = frozenset()
                try:
                    from bestseller.services.drafts import (
                        _load_offstage_character_names_before_chapter,
                        _scrub_offstage_scene_references,
                    )

                    _offstage_names = await _load_offstage_character_names_before_chapter(
                        session,
                        project.id,
                        chapter_number,
                    )
                    _removed_participants, _removed_state_refs = _scrub_offstage_scene_references(
                        scene,
                        _offstage_names,
                    )
                    if _removed_participants or _removed_state_refs:
                        _repair_count += len(_removed_participants) + len(_removed_state_refs)
                except Exception:
                    logger.debug(
                        "Offstage scene participant scrub failed for ch%d sc%d (non-fatal)",
                        chapter_number,
                        scene_number,
                        exc_info=True,
                    )
                _participant_repair_count = repair_missing_scene_participants_pre_draft(
                    scene,
                    identity_registry=_identity_registry,
                    excluded_names=_offstage_names,
                )
                _repair_count += _participant_repair_count
                _methodology_repair_count = repair_missing_scene_methodology_contract_pre_draft(
                    scene,
                    chapter=chapter,
                    chapter_number=chapter_number,
                )
                _repair_count += _methodology_repair_count
                if _repair_count:
                    _scene_meta = dict(getattr(scene, "metadata_json", {}) or {})
                    _scene_meta["legacy_scene_contract_repair"] = {
                        "field_updates": _repair_count,
                        "chapter_number": chapter_number,
                        "scene_number": scene_number,
                    }
                    if _participant_repair_count:
                        _scene_meta["participant_repair"] = {
                            "source": "identity_registry_and_scene_context",
                            "added_count": _participant_repair_count,
                            "participants": list(scene.participants or []),
                        }
                    if _methodology_repair_count:
                        _scene_meta["methodology_contract_repair"] = {
                            "source": "legacy_scene_context",
                            "added_count": _methodology_repair_count,
                        }
                    scene.metadata_json = _scene_meta

                _contract = validate_scene_contract_pre_draft(
                    scene,
                    identity_registry=_identity_registry,
                    require_identity_registry=True,
                    excluded_names=_offstage_names,
                    methodology_contract_mode=resolve_methodology_contract_mode(
                        project,
                        settings=settings,
                    ),
                )
                if _contract.violations or _contract.warnings:
                    _scene_meta = dict(getattr(scene, "metadata_json", {}) or {})
                    _scene_meta["pre_draft_scene_contract"] = _contract.to_dict()
                    scene.metadata_json = _scene_meta
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "pre_draft_scene_contract": _contract.to_dict(),
                    }
                _contract.raise_for_blocks(
                    project_slug=project_slug,
                    artifact=f"scene {chapter_number}.{scene_number}",
                )
            except ValueError:
                raise
            except Exception:
                logger.debug("Pre-draft scene contract gate failed (non-fatal)", exc_info=True)

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
                    build_genre_constraint_block,
                    get_genre_profile,
                )

                _genre = getattr(project, "genre", None) or settings.generation.genre
                _sub_genre = (project.metadata_json or {}).get("sub_genre")
                _gprofile = get_genre_profile(_genre, _sub_genre)
                if _gprofile:
                    # Build character states from latest snapshot
                    _latest_snap = await session.scalar(
                        select(ChapterStateSnapshotModel)
                        .where(
                            ChapterStateSnapshotModel.project_id == project.id,
                    )
                        .order_by(ChapterStateSnapshotModel.chapter_number.desc())
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
                            _gprofile,
                            _char_states,
                            language=_lang,
                        )
            except Exception:
                logger.debug("Genre constraint injection failed (non-fatal)", exc_info=True)

        # ── Ranking capability profile: book-specific benchmark constraints ──
        # This reads DB metadata first and falls back to output/<slug>/story-bible/
        # ranking-capability-profile.md so recovered/current tasks can consume
        # the new capability without needing their persisted payload rewritten.
        if shared_context is not None:
            try:
                from bestseller.services.ranking_capability_profile import (
                    apply_ranking_capability_profile_to_context,
                )

                _project_meta = project.metadata_json or {}
                _story_bible = (
                    shared_context.story_bible
                    if isinstance(shared_context.story_bible, dict)
                    else {}
                )
                _applied_profile = apply_ranking_capability_profile_to_context(
                    shared_context,
                    project_slug=project.slug,
                    project_metadata=_project_meta,
                    story_bible_context=_story_bible,
                    output_base_dir=getattr(settings.output, "base_dir", None),
                )
                if _applied_profile:
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "ranking_capability_profile_applied": True,
                    }
            except Exception:
                logger.debug(
                    "Ranking capability profile injection failed (non-fatal)",
                    exc_info=True,
                )

        # ── Premium genre engines: progression causality + protagonist decisions ──
        # These blocks are built from persisted story-bible metadata and injected into
        # the same shared context that the live scene writer prompt consumes.
        if shared_context is not None:
            try:
                _project_meta = project.metadata_json or {}
                _lang = getattr(project, "language", None) or settings.generation.language
                _volume_payload = (
                    shared_context.story_bible.get("volume", {})
                    if isinstance(shared_context.story_bible, dict)
                    else {}
                )
                _current_volume = None
                if isinstance(_volume_payload, dict):
                    _volume_no = _volume_payload.get("volume_number")
                    if isinstance(_volume_no, int):
                        _current_volume = _volume_no
                _sub_genre = _project_meta.get("sub_genre")
                _engine_blocks = build_premium_genre_engine_blocks(
                    project_metadata=_project_meta,
                    story_bible_context=shared_context.story_bible,
                    genre=getattr(project, "genre", None) or settings.generation.genre,
                    sub_genre=_sub_genre if isinstance(_sub_genre, str) else None,
                    language=_lang,
                    current_volume=_current_volume,
                )
                if _engine_blocks.progression_context_block:
                    shared_context.progression_context_block = (
                        _engine_blocks.progression_context_block
                    )
                if _engine_blocks.decision_policy_block:
                    shared_context.decision_policy_block = _engine_blocks.decision_policy_block
                if _engine_blocks.rule_system_context_block:
                    shared_context.rule_system_context_block = (
                        _engine_blocks.rule_system_context_block
                    )
                if _engine_blocks.faction_ecology_context_block:
                    shared_context.faction_ecology_context_block = (
                        _engine_blocks.faction_ecology_context_block
                    )
                if _engine_blocks.relationship_agency_context_block:
                    shared_context.relationship_agency_context_block = (
                        _engine_blocks.relationship_agency_context_block
                    )
                if _engine_blocks.entry_system_context_block:
                    shared_context.entry_system_context_block = (
                        _engine_blocks.entry_system_context_block
                    )
                if _engine_blocks.entry_registry_context_block:
                    shared_context.entry_registry_context_block = (
                        _engine_blocks.entry_registry_context_block
                    )
                if _engine_blocks.entry_state_ledger_block:
                    shared_context.entry_state_ledger_block = (
                        _engine_blocks.entry_state_ledger_block
                    )
                if _engine_blocks.warnings:
                    shared_context.contradiction_warnings.extend(
                        f"[精品类型引擎] {warning}" for warning in _engine_blocks.warnings
                    )
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "premium_genre_engine_warnings": list(_engine_blocks.warnings),
                    }
            except PlanningCapabilitySelectionError:
                raise
            except Exception:
                logger.debug("Premium genre engine injection failed (non-fatal)", exc_info=True)

        # ── Inject opening diversity block (only for scene 1 — chapter opener) ──
        # Show the LLM the last 12 chapter openings so it avoids repeating the
        # same sentence structure or setting description.
        if shared_context is not None and scene_number == 1:
            try:
                from bestseller.infra.db.models import ChapterDraftVersionModel
                from bestseller.services.deduplication import (
                    build_opening_diversity_block,
                    extract_chapter_opening,
                )

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
                    .limit(12)
                )
                _recent_openings: list[tuple[int, str]] = []
                for _ch_num, _content in _recent_drafts.fetchall():
                    _opening = extract_chapter_opening(_content or "")
                    if _opening:
                        _recent_openings.append((_ch_num, _opening))
                if _recent_openings:
                    _lang = getattr(project, "language", None) or settings.generation.language
                    shared_context.opening_diversity_block = build_opening_diversity_block(
                        _recent_openings,
                        language=_lang,
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
                    session,
                    project.id,
                    current_chapter=chapter_number,
                    current_scene=scene_number,
                    window=10,
                )
                _last_emerging_ch = (project.metadata_json or {}).get(
                    "_last_emerging_conflict_chapter"
                )
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
                    session,
                    project.id,
                    current_chapter=chapter_number,
                    current_scene=scene_number,
                    window=5,
                )
                shared_context.scene_purpose_diversity_block = build_scene_purpose_diversity_block(
                    _purposes,
                    language=_lang,
                )

                _envs = await compute_env_history(
                    session,
                    project.id,
                    current_chapter=chapter_number,
                    current_scene=scene_number,
                    window=3,
                )
                shared_context.env_diversity_block = build_env_diversity_block(
                    _envs,
                    language=_lang,
                )
            except Exception:
                logger.debug(
                    "Stage A/B diversity block injection failed (non-fatal)", exc_info=True
                )

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
                    session,
                    project.id,
                    pov_character_name=_pov_name,
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

                # Cliffhanger taxonomy guides the CHAPTER-END hook — only the
                # closing scene writes it. Injecting it into every scene was
                # mistargeted: openers/middles got ~200 tokens of hook-type
                # rotation they must not act on (and sometimes did, planting
                # mid-chapter fake-out hooks). Closer detection mirrors the
                # DiversityBudget section below.
                _max_scene_row = await session.execute(
                    select(func.max(SceneCardModel.scene_number)).where(
                        SceneCardModel.chapter_id == chapter.id,
                    )
                )
                _stage_d_max_scene = _max_scene_row.scalar_one_or_none() or scene_number
                if int(scene_number) >= int(_stage_d_max_scene):
                    _hook_types = await compute_recent_hook_types(
                        session,
                        project.id,
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
                    session,
                    project.id,
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
                    session,
                    project.id,
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
                from bestseller.infra.db.models import SceneCardModel as _SCM_for_closer
                from bestseller.services.diversity_budget import (
                    load_diversity_budget,
                    render_budget_diversity_block,
                )

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
                    GoldenFingerLadder,
                    extract_ladder_from_growth_curve,
                )
                from bestseller.services.prompt_constructor import (
                    build_chapter_hype_blocks,
                )

                _invariants_for_hype = None
                if project.invariants_json:
                    _invariants_for_hype = project_invariants_from_row(project)
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
                    _growth_curve = (project.metadata_json or {}).get("growth_curve") or ""
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
                        pacing_profile=getattr(settings.generation, "pacing_profile", "medium")
                        or "medium",
                        golden_finger_ladder=_ladder,
                        sanitize_for_prose=get_quality_gates_config().prose_quality.sanitize_prompt,
                    )
                    shared_context.reader_contract_block = (
                        _hype_blocks.reader_contract_block or None
                    )
                    shared_context.hype_constraints_block = (
                        _hype_blocks.hype_constraints_block or None
                    )
                    if _hype_blocks.assigned_hype_type is not None:
                        shared_context.assigned_hype_type = _hype_blocks.assigned_hype_type.value
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
                    _l3_cfg = get_quality_gates_config().l3
                    if _l3_cfg.enabled and _invariants_for_hype is not None:
                        from bestseller.services.kernel_composer import (
                            narrative_richness_context_from_metadata,
                        )
                        from bestseller.services.prompt_constructor import (
                            build_chapter_l3_blocks,
                        )

                        _richness_context = narrative_richness_context_from_metadata(
                            project.metadata_json or {}
                        )
                        _l3_blocks = build_chapter_l3_blocks(
                            _invariants_for_hype,
                            _budget_for_hype,
                            chapter_no=chapter_number,
                            narrative_richness_context=_richness_context,
                            hot_vocab_window=_l3_cfg.hot_vocab_window_chapters,
                            hot_vocab_top_n=_l3_cfg.hot_vocab_top_n,
                            hot_vocab_min_count=_l3_cfg.hot_vocab_min_count,
                            no_repeat_within_openings=_l3_cfg.no_repeat_within_openings,
                        )
                        if not _l3_blocks.is_empty:
                            shared_context.l3_prompt_block = _l3_blocks.as_prompt_block() or None
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
                except PlanningCapabilitySelectionError:
                    raise
                except Exception:
                    logger.debug(
                        "L3 prompt block injection failed for ch%d sc%d (non-fatal)",
                        chapter_number,
                        scene_number,
                        exc_info=True,
                    )
            except PlanningCapabilitySelectionError:
                raise
            except Exception:
                logger.debug(
                    "Hype block injection failed for ch%d sc%d (non-fatal)",
                    chapter_number,
                    scene_number,
                    exc_info=True,
                )

        # ── P1 Originality Engine block injection ──
        # When enabled (and the project has file-backed DNA / market /
        # signature plan / prior persona feedback under
        # ``output/<slug>/``), stamp four extra prompt blocks onto the
        # shared context. Missing artifacts → corresponding blocks stay
        # None, downstream concatenation skips them. Never fatal.
        if shared_context is not None:
            try:
                _orig_cfg = get_quality_gates_config().originality_engine
                if _orig_cfg.enabled:
                    from bestseller.services.chapter_orchestrator import (
                        ensure_signature_plan as _ensure_signature_plan,
                    )
                    from bestseller.services.chapter_orchestrator import (
                        prepare_chapter_context as _prepare_chapter_context,
                    )
                    from bestseller.services.exposition_density_gate import (
                        check_exposition_density as _check_exposition_density,
                    )
                    from bestseller.services.exposition_density_gate import (
                        render_exposition_density_block as _render_exposition_block,
                    )
                    from bestseller.services.market_constraint_compiler import (
                        render_chapter_constraints_block as _render_constraints_block,
                    )
                    from bestseller.services.reader_persona_simulator import (
                        render_persona_feedback_block as _render_persona_block,
                    )
                    from bestseller.services.signature_scene_planner import (
                        render_signature_scene_block as _render_signature_block,
                    )
                    from bestseller.services.voice_signature import (
                        render_voice_dna_block as _render_voice_block,
                    )

                    _orig_mode_b = (
                        bool(_orig_cfg.mode_b_override)
                        if (_orig_cfg.mode_b_override is not None)
                        else False
                    )
                    _orig_lang = (
                        _invariants_for_hype.language
                        if _invariants_for_hype is not None
                        else "zh-CN"
                    )
                    try:
                        _prev_text = await _load_prev_chapter_draft_text(
                            session,
                            project,
                            chapter_number,
                        )
                    except Exception:
                        logger.debug(
                            "prev chapter draft lookup failed for ch%d (non-fatal)",
                            chapter_number,
                            exc_info=True,
                        )
                        _prev_text = None
                    # Book-derived anchor tokens: designed once per book
                    # from its own premise (imagery system). They feed BOTH
                    # the signature-scene mandates and the hook-echo domain
                    # vocabulary — the framework supplies no genre-flavored
                    # anchor content of its own.
                    _book_anchor_tokens: tuple[str, ...] = ()
                    try:
                        from bestseller.services.imagery_system_design import (
                            ensure_book_imagery_system as _ensure_imagery,
                        )
                        from bestseller.services.imagery_system_design import (
                            imagery_anchor_phrases as _imagery_anchors,
                        )

                        await _ensure_imagery(session, settings, project)
                        _book_anchor_tokens = _imagery_anchors(project)
                    except Exception:
                        logger.debug(
                            "imagery anchor derivation failed for ch%d (non-fatal)",
                            chapter_number,
                            exc_info=True,
                        )
                    # Self-bootstrap the signature-scene plan: the CLI
                    # ``book bootstrap`` is the only other producer and
                    # platform-run books never execute it. Never overwrites
                    # an existing plan on disk.
                    if _orig_cfg.auto_signature_plan:
                        try:
                            _sig_total = int(
                                getattr(project, "target_chapters", 0)
                                or (project.metadata_json or {}).get("target_chapter_count")
                                or 0
                            )
                            if _sig_total >= 1:
                                # R25: derive concrete mandate targets from
                                # the project's own chapter outline so the
                                # bootstrap never plants empty archetype
                                # shells. Missing outline → mandates stay
                                # skeletons and are withheld from prompts.
                                _outline_hints = None
                                try:
                                    from bestseller.services.signature_outline_hints import (
                                        load_chapter_outline_hints as _load_outline_hints,
                                    )

                                    _outline_hints = await _load_outline_hints(session, project.id)
                                except Exception:
                                    logger.debug(
                                        "chapter outline hint derivation "
                                        "failed for ch%d (non-fatal)",
                                        chapter_number,
                                        exc_info=True,
                                    )
                                _ensure_signature_plan(
                                    project.slug,
                                    total_chapters=max(_sig_total, chapter_number),
                                    output_base_dir=settings.output.base_dir,
                                    mode_b=_orig_mode_b,
                                    anchor_images=_book_anchor_tokens or None,
                                    chapter_outline=_outline_hints or None,
                                )
                        except Exception:
                            logger.debug(
                                "signature plan auto-bootstrap failed for ch%d (non-fatal)",
                                chapter_number,
                                exc_info=True,
                            )
                    _orig_ctx = _prepare_chapter_context(
                        project.slug,
                        chapter_number,
                        output_base_dir=settings.output.base_dir,
                        mode_b=_orig_mode_b,
                        prev_chapter_text=_prev_text,
                        hook_domain_tokens=_book_anchor_tokens,
                    )
                    if _orig_ctx.voice_dna is not None:
                        shared_context.voice_dna_block = (
                            _render_voice_block(_orig_ctx.voice_dna, language=_orig_lang) or None
                        )
                    if _orig_ctx.market_constraints is not None:
                        shared_context.chapter_market_constraints_block = (
                            _render_constraints_block(
                                _orig_ctx.market_constraints,
                                language=_orig_lang,
                        )
                            or None
                        )
                    if _orig_ctx.signature_scene_mandate is not None:
                        shared_context.signature_scene_block = (
                            _render_signature_block(
                                _orig_ctx.signature_scene_mandate,
                                language=_orig_lang,
                        )
                            or None
                        )
                    if _orig_ctx.prior_persona_feedback is not None:
                        shared_context.prior_persona_feedback_block = (
                            _render_persona_block(
                                _orig_ctx.prior_persona_feedback,
                                language=_orig_lang,
                        )
                            or None
                        )
                    if _orig_ctx.hook_echo_report is not None:
                        shared_context.hook_echo_block = (
                            _orig_ctx.hook_echo_block(language=_orig_lang) or None
                        )
                        # Raw tokens for the first scene's opening-echo duty
                        # (acceptance_contract.render_scene_acceptance_block).
                        _prev_tokens = list(_orig_ctx.hook_echo_report.finding.prev_hook_tokens)
                        shared_context.prev_hook_tokens = _prev_tokens or None
                    try:
                        shared_context.exposition_density_block = (
                            _render_exposition_block(
                                _check_exposition_density(
                                    "",
                                    chapter_position=chapter_number,
                                ),
                                language=_orig_lang,
                            )
                            or None
                        )
                    except Exception:
                        logger.debug(
                            "exposition density block injection failed for ch%d (non-fatal)",
                            chapter_number,
                            exc_info=True,
                        )
                    # Canon guardrails — chapter-aware forbidden character/term list.
                    # This is the primary defense against premature cast drift
                    # (e.g. 裴镜渊 leaking into ch1-15 of 青囊不语问阴阳).
                    try:
                        from bestseller.services.canon_guardrails import (
                            load_canon_guardrails_for_project as _load_guard,
                        )
                        from bestseller.services.canon_guardrails import (
                            render_canon_guardrails_block as _render_guard,
                        )

                        _guard = _load_guard(
                            project,
                            output_base_dir=settings.output.base_dir,
                        )
                        if not _guard.is_empty:
                            shared_context.canon_guardrails_block = (
                                _render_guard(
                                    _guard,
                                    chapter_number=chapter_number,
                                    language=_orig_lang,
                                )
                                or None
                            )
                    except Exception:
                        logger.debug(
                            "canon guardrails injection failed for ch%d (non-fatal)",
                            chapter_number,
                            exc_info=True,
                        )

                    # ── Story Integrity blocks (LLM-first whitelists) ──
                    # 2026-05-23: previously the writing prompt did NOT
                    # inject timeline-canon, scene-coherence, or character-
                    # role rules into prompts. The LLM was given the bible
                    # markdown as free text only — too soft. Force them as
                    # structured whitelist blocks here.
                    _bible_root = (
                        Path(settings.output.base_dir) / project.slug / "story-bible"
                        if settings.output.base_dir
                        else None
                    )
                    if _bible_root is not None:
                        try:
                            from bestseller.services.timeline_consistency_gate import (
                                load_timeline_canon as _load_canon,
                            )
                            from bestseller.services.timeline_consistency_gate import (
                                render_timeline_canon_block as _render_canon,
                            )

                            _canon = _load_canon(_bible_root / "timeline-canon.md")
                            if _canon is not None:
                                shared_context.timeline_canon_block = (
                                    _render_canon(_canon, language=_orig_lang) or None
                                )
                        except Exception:
                            logger.debug(
                                "timeline canon block injection failed for ch%d (non-fatal)",
                                chapter_number,
                                exc_info=True,
                            )
                        try:
                            from bestseller.services.scene_coherence_gate import (
                                render_scene_coherence_block as _render_scene,
                            )

                            shared_context.scene_coherence_block = (
                                _render_scene(language=_orig_lang) or None
                            )
                        except Exception:
                            logger.debug(
                                "scene coherence block injection failed for ch%d (non-fatal)",
                                chapter_number,
                                exc_info=True,
                            )
                        try:
                            from bestseller.services.character_role_gate import (
                                load_character_profiles as _load_profiles,
                            )
                            from bestseller.services.character_role_gate import (
                                render_character_role_block as _render_role,
                            )
                            from bestseller.services.dialogue_voice_blocks import (
                                render_dialogue_voice_block as _render_dialogue_voice,
                            )

                            _profiles = _load_profiles(_bible_root / "cast-and-promises.md")
                            if _profiles:
                                shared_context.character_role_block = (
                                    _render_role(_profiles, language=_orig_lang) or None
                                )
                                _voice_profiles = tuple(
                                    profile.dialogue_voice
                                    for profile in _profiles
                                    if profile.dialogue_voice is not None
                                )
                                if _voice_profiles:
                                    shared_context.dialogue_voice_block = (
                                        _render_dialogue_voice(
                                            _voice_profiles,
                                            language=_orig_lang,
                                        )
                                        or None
                                    )
                        except Exception:
                            logger.debug(
                                "character/dialogue role block injection failed for ch%d "
                                "(non-fatal)",
                                chapter_number,
                                exc_info=True,
                            )
                    # Chapter length block — independent of bible files.
                    try:
                        from bestseller.services.chapter_length_gate import (
                            render_chapter_length_block as _render_length,
                        )

                        shared_context.chapter_length_block = (
                            _render_length(language=_orig_lang) or None
                        )
                    except Exception:
                        logger.debug(
                            "chapter length block injection failed for ch%d (non-fatal)",
                            chapter_number,
                            exc_info=True,
                        )
            except Exception:
                logger.debug(
                    "P1 Originality Engine block injection failed for ch%d sc%d (non-fatal)",
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
                    )
                    if shared_context.scene_contract
                    else None,
                    settings=settings,
                    language=getattr(project, "language", None),
                    scene=scene,
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
                        "write_safety_findings": serialize_write_safety_findings(_safety_findings),
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
        if draft is None and getattr(settings.pipeline, "enable_scene_plan_richness_gate", True):
            try:
                from bestseller.services.prewrite_quality_profile import (
                    is_strict_prewrite_project,
                )
                from bestseller.services.scene_plan_richness import (
                    repair_scene_model_state_defaults,
                    validate_scene_model,
                )

                _lang = getattr(project, "language", None) or settings.generation.language
                _richness = validate_scene_model(scene, language=_lang)
                if (
                    _richness.severity == "critical"
                    and any(
                        i.code
                        in {
                            "entry_state_empty_or_generic",
                            "exit_state_empty_or_generic",
                            "no_state_delta",
                        }
                        for i in _richness.critical_issues
                    )
                    and repair_scene_model_state_defaults(scene, language=_lang)
                ):
                    await session.flush()
                    _richness = validate_scene_model(scene, language=_lang)
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "plan_richness_state_auto_repaired": True,
                    }
                if _richness.issues:
                    _codes = [i.code for i in _richness.issues]
                    logger.warning(
                        "Scene %d.%d richness %s — issues=%s",
                        chapter_number,
                        scene_number,
                        _richness.severity,
                        _codes,
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
                    # Optionally block — but RECOVERABLY. Default is soft:
                    # prompt-block + contradiction warnings are already injected
                    # above, so the writer continues with explicit guidance.
                    # Only when ``scene_richness_block_on_critical`` is opted back
                    # on (strict prewrite) do we block — and even then we raise
                    # ``WriteSafetyBlockError`` so the chapter pipeline's existing
                    # recovery path (mark chapter blocked → auto-repair/self-heal)
                    # engages instead of a bare ``ValueError`` that aborts the
                    # whole book. The previous code raised a plain ``ValueError``
                    # which NOTHING caught — a single thin scene card killed the
                    # entire run (framework self-harm; 2026-06 fix).
                    if (
                        _richness.severity == "critical"
                        and getattr(settings.pipeline, "scene_richness_block_on_critical", False)
                        and is_strict_prewrite_project(project)
                    ):
                        workflow_run.metadata_json = {
                            **workflow_run.metadata_json,
                            "blocked_by_richness_gate": True,
                            "richness_issue_codes": _codes,
                        }
                        from bestseller.services.write_safety_gate import (
                            WriteSafetyFinding,
                        )

                        raise WriteSafetyBlockError(
                            f"Scene {chapter_number}.{scene_number} blocked by plan-richness "
                            f"gate: {_codes}. Re-plan required (card too thin).",
                            findings=[
                                WriteSafetyFinding(
                                    source="plan_richness",
                                    code=str(_codes[0]) if _codes else "thin_card",
                                    severity="critical",
                                    message=(
                                        f"场景 {chapter_number}.{scene_number} 卡片过薄: {_codes}"
                                    ),
                                )
                            ],
                        )
            except WriteSafetyBlockError:
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

        if draft is None or force_auto_repair_generation:
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
            _emit_progress(
                progress,
                "scene_draft_generated",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "scene_number": scene_number,
                    "draft_id": str(draft.id),
                    "version_no": draft.version_no,
                },
            )

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
                _id_language = getattr(project, "language", None) or "zh-CN"
                _id_violations = validate_scene_text_identity(
                    draft.content_md,
                    _id_registry,
                    language=_id_language,
                    participant_names=list(scene.participants or []),
                    chapter_number=chapter_number,
                )
                if (
                    _id_violations
                    and _id_language.lower().startswith("zh")
                    and any(v.violation_type == "pronoun_mismatch" for v in _id_violations)
                ):
                    # Deterministic in-place pronoun swap before any rewrite:
                    # pronoun_mismatch used to consume a whole auto-repair
                    # round even though the fix is a one-character edit.
                    from bestseller.services.identity_guard import (
                        fix_zh_pronoun_mismatches,
                    )

                    _fixed_text, _fix_count = fix_zh_pronoun_mismatches(
                        draft.content_md,
                        _id_registry,
                        participant_names=list(scene.participants or []),
                    )
                    if _fix_count > 0:
                        _revalidated = validate_scene_text_identity(
                            _fixed_text,
                            _id_registry,
                            language=_id_language,
                            participant_names=list(scene.participants or []),
                            chapter_number=chapter_number,
                        )
                        _old_pronoun = sum(
                            1 for v in _id_violations if v.violation_type == "pronoun_mismatch"
                        )
                        _new_pronoun = sum(
                            1 for v in _revalidated if v.violation_type == "pronoun_mismatch"
                        )
                        if _new_pronoun < _old_pronoun:
                            logger.info(
                                "ch%d sc%d: deterministically fixed %d pronoun "
                                "mismatch(es) (%d→%d remaining)",
                                chapter_number,
                                scene_number,
                                _fix_count,
                                _old_pronoun,
                                _new_pronoun,
                            )
                            draft.content_md = _fixed_text
                            resync_draft_word_count(draft, language=project.language or "zh-CN")
                            await session.flush()
                            _id_violations = _revalidated
                if _id_violations:
                    logger.warning(
                        "Identity violations in ch%d sc%d: %s",
                        chapter_number,
                        scene_number,
                        [
                            (v.character_name, v.violation_type, v.expected, v.found)
                            for v in _id_violations
                        ],
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
                    select(SceneDraftVersionModel)
                    .join(
                        SceneCardModel,
                        SceneDraftVersionModel.scene_card_id == SceneCardModel.id,
                    )
                    .join(
                        ChapterModel,
                        SceneCardModel.chapter_id == ChapterModel.id,
                    )
                    .where(
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
                        _existing_texts.append(
                            (_ch.chapter_number, _sc.scene_number, ed.content_md)
                        )

                _dedup_findings = check_scene_duplication(draft.content_md, _existing_texts)
                if _dedup_findings:
                    logger.warning(
                        "Deduplication findings in ch%d sc%d: %s",
                        chapter_number,
                        scene_number,
                        [
                            (f["chapter"], f["scene"], f["similarity"], f["severity"])
                            for f in _dedup_findings
                        ],
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
            _emit_progress(
                progress,
                "scene_review_completed",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "scene_number": scene_number,
                    "review_iterations": review_iterations,
                    "verdict": review_result.verdict,
                },
            )
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
                    stalled_count = (
                        int((workflow_run.metadata_json or {}).get("stalled_rewrite_count") or 0)
                        + 1
                    )
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "stalled_rewrite": True,
                        "stalled_rewrite_count": stalled_count,
                        "stalled_rewrite_score_delta": round(score_delta, 4),
                        "stalled_rewrite_threshold": settings.quality.min_scene_rewrite_improvement,
                    }
                    if stalled_count >= 2:
                        reached_revision_limit = True
                        # Closure can finish this workflow branch with explicit
                        # quality debt, but it never turns a stalled candidate
                        # into an approved/promoted scene.  Strict pauses.
                        if _pipeline_quality_mode(settings) == "closure":
                            logger.info(
                                "Scene %d.%d rewrite stalled twice (delta=%.4f) — recording quality debt",
                                chapter_number,
                                scene_number,
                                score_delta,
                            )
                            workflow_run.metadata_json = {
                                **(workflow_run.metadata_json or {}),
                                "scene_quality_debt": True,
                                "scene_quality_debt_reason": "scene_rewrite_stalled_after_two_attempts",
                            }
                        else:
                            requires_human_review = True
                            workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                            workflow_run.current_step = "scene_rewrite_stalled_blocked"
                            workflow_run.metadata_json = {
                                **(workflow_run.metadata_json or {}),
                                "machine_blocker": "scene_rewrite_stalled_after_two_attempts",
                            }
                        break
                    logger.info(
                        "Scene %d.%d rewrite stalled once (delta=%.4f) — trying one more bounded rewrite",
                        chapter_number,
                        scene_number,
                        score_delta,
                    )

            if rewrite_iterations >= settings.quality.max_scene_revisions:
                reached_revision_limit = True
                # A bounded rewrite limit is operational closure, never quality
                # approval.  In closure mode the candidate is quarantined and
                # the run carries debt; strict blocks for human intervention.
                if _pipeline_quality_mode(settings) == "closure":
                    logger.info(
                        "Scene %d.%d reached max revisions (%d) — recording quality debt",
                        chapter_number,
                        scene_number,
                        rewrite_iterations,
                    )
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "scene_quality_debt": True,
                        "scene_quality_debt_reason": "scene_rewrite_revision_limit",
                    }
                else:
                    requires_human_review = True
                    workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                    workflow_run.current_step = "scene_rewrite_stalled_blocked"
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "machine_blocker": "scene_rewrite_revision_limit",
                    }
                break

            previous_scene_score = current_scene_score
            previous_rewrite_instructions = getattr(review_result, "rewrite_instructions", None)

            rewrite_iterations += 1
            current_step_name = f"rewrite_scene_v{rewrite_iterations}"
            workflow_run.current_step = current_step_name
            try:
                draft, rewrite_task = await rewrite_scene_from_task(
                    session,
                    project_slug,
                    chapter_number,
                    scene_number,
                    rewrite_task_id=rewrite_task.id,
                    settings=settings,
                    workflow_run_id=workflow_run.id,
                    context_packet=shared_context,
                )
            except ValueError as exc:
                missing_current_draft = "does not have a current draft" in str(exc)
                if not missing_current_draft:
                    raise
                logger.warning(
                    "Scene %d.%d rewrite requested but current draft is missing; "
                    "regenerating scene draft and continuing review loop",
                    chapter_number,
                    scene_number,
                )
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "scene_rewrite_missing_current_draft_recovered": True,
                    "scene_rewrite_missing_current_draft_error": str(exc),
                    "scene_rewrite_missing_current_draft_iteration": rewrite_iterations,
                }
                current_step_name = "recover_missing_scene_draft"
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
                        "recovered_from": "missing_current_scene_draft",
                    },
                )
                step_order += 1
                previous_scene_score = None
                previous_rewrite_instructions = None
                _emit_progress(
                    progress,
                    "scene_draft_regenerated_after_missing_current",
                    {
                        "project_slug": project_slug,
                        "chapter_number": chapter_number,
                        "scene_number": scene_number,
                        "draft_id": str(draft.id),
                        "version_no": draft.version_no,
                    },
                )
                continue
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

        scene_promoted = False
        if review_result.verdict == "pass" and not requires_human_review:
            try:
                scene_promoted = await _promote_reviewed_scene_draft(
                    session,
                    project=project,
                    scene=scene,
                    draft=draft,
                    quality=quality,
                    workflow_run_id=workflow_run.id,
                )
            except (ValueError, RuntimeError) as exc:
                logger.warning(
                    "Scene %d.%d promotion evidence was not eligible: %s",
                    chapter_number,
                    scene_number,
                    exc,
                )
                if _pipeline_quality_mode(settings) == "strict":
                    requires_human_review = True
                    workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                    workflow_run.current_step = "scene_promotion_blocked"
                else:
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "scene_quality_debt": True,
                        "scene_quality_debt_reason": "scene_promotion_ineligible",
                    }
            if scene_promoted:
                scene.status = SceneStatus.APPROVED.value

        if reached_revision_limit and not requires_human_review:
            # See the corresponding compatibility note in
            # ``_promote_reviewed_scene_draft``: control-flow unit doubles do
            # not carry a persisted exact-version score.
            if isinstance(quality, QualityScoreModel):
                stalled_draft = draft
                draft, quality = await _promote_best_scoring_scene_draft_on_stall(
                    session,
                    scene=scene,
                    current_draft=draft,
                    current_quality=quality,
                )
                await _quarantine_scene_candidate(
                    session,
                    project=project,
                    draft=stalled_draft,
                    workflow_run_id=workflow_run.id,
                    reason_code=str(
                        (workflow_run.metadata_json or {}).get("scene_quality_debt_reason")
                        or "scene_rewrite_stalled"
                    ),
                )
                # Retire the orphan rewrite task. The final review_scene_draft of a
                # stalled loop creates a fresh pending RewriteTaskModel that the loop
                # breaks BEFORE consuming — leaving a pending task for a scene that has
                # already shipped its best attempt with accepted debt. Left pending, a
                # later run_project_repair sweep would redundantly re-run this quarantined
                # scene (wasted tokens, and a late rewrite that no longer matches the
                # assembled chapter). Superseding it keeps the queue honest.
                if rewrite_task is not None and getattr(rewrite_task, "status", None) in (
                    "pending",
                    "queued",
                ):
                    rewrite_task.status = "superseded"
                    rewrite_task.metadata_json = {
                        **(getattr(rewrite_task, "metadata_json", None) or {}),
                        "superseded_reason": "scene_rewrite_budget_exhausted",
                    }
            scene.status = SceneStatus.NEEDS_REWRITE.value

        # Canon/timeline/discovery materialisation is a promoted-only consumer.
        # A closure run may finish with a quarantined candidate, but it must not
        # enrich future writer context with that candidate's assertions.
        if scene_promoted:
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
            canon_fact_count = (
                knowledge_result.canon_facts_created + knowledge_result.canon_facts_reused
            )
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
            _emit_progress(
                progress,
                "scene_knowledge_refreshed",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "scene_number": scene_number,
                    "canon_fact_count": canon_fact_count,
                    "timeline_event_count": timeline_event_count,
                },
            )

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
            except Exception:
                logger.warning(
                    "Scene %d:%d discovery propagation failed (non-fatal)",
                    chapter.chapter_number,
                    scene.scene_number,
                    exc_info=True,
                )

            # Scene-level bible delta (Phase B+ design).  Opt-in via
            # BESTSELLER_BIBLE_INCREMENTAL_ENABLED; default off so existing
            # projects use the chapter-end batch path.
            try:
                from bestseller.services.story_bible import (
                    apply_scene_bible_delta,
                    extract_scene_bible_deltas,
                    filter_fresh_deltas,
                    is_bible_incremental_enabled,
                )

                if is_bible_incremental_enabled() and len(draft.content_md or "") >= 500:
                    project_id_str = str(project.id)
                    metadata = project.metadata_json or {}
                    seen_keys = set(
                        (metadata.get("scene_bible_deltas") or {}).get(
                            str(chapter.chapter_number), []
                        )
                        or []
                    )
                    scene_deltas = await extract_scene_bible_deltas(
                        session,
                        settings,
                        project=project,
                        chapter=chapter,
                        scene=scene,
                        scene_text=draft.content_md or "",
                        project_id=project_id_str,
                        workflow_run_id=workflow_run.id,
                    )
                    fresh = filter_fresh_deltas(
                        project_id_str, chapter.chapter_number, scene_deltas, seen_keys
                    )
                    applied_count = 0
                    for delta in fresh:
                        ok = await apply_scene_bible_delta(session, project=project, delta=delta)
                        if ok:
                            applied_count += 1
                            seen_keys.add(delta.delta_key)
                    if applied_count:
                        # Persist seen keys so next scene knows what's been
                        # applied; idempotency is the only state we keep.
                        scene_meta = dict(metadata.get("scene_bible_deltas") or {})
                        scene_meta[str(chapter.chapter_number)] = sorted(seen_keys)
                        project.metadata_json = {
                            **metadata,
                            "scene_bible_deltas": scene_meta,
                        }
                        await create_workflow_step_run(
                            session,
                            workflow_run_id=workflow_run.id,
                            step_name="scene_bible_delta",
                            step_order=step_order,
                            status=WorkflowStatus.COMPLETED,
                            output_ref={
                                "scene_number": scene.scene_number,
                                "extracted_count": len(scene_deltas),
                                "fresh_count": len(fresh),
                                "applied_count": applied_count,
                            },
                        )
                        step_order += 1
                        _emit_progress(
                            progress,
                            "scene_bible_delta_applied",
                            {
                                "project_slug": project_slug,
                                "chapter_number": chapter_number,
                                "scene_number": scene_number,
                                "extracted": len(scene_deltas),
                                "fresh": len(fresh),
                                "applied": applied_count,
                            },
                        )
            except Exception:
                logger.warning(
                    "Scene %d:%d bible delta path failed (non-fatal)",
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
            "scene_promoted": scene_promoted,
            "promotion_state": getattr(draft, "promotion_state", None),
            "final_verdict": review_result.verdict,
            "canon_fact_count": canon_fact_count,
            "timeline_event_count": timeline_event_count,
            "llm_run_ids": [str(llm_run_id) for llm_run_id in llm_run_ids],
        }
        await session.flush()

        result = ScenePipelineResult(
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
        # Persist the scene result before a failure in the next scene, even
        # when no later model call would incidentally commit these records.
        await _checkpoint_commit(session)
        return result
    except CancelledError as exc:
        attach_workflow_failure(exc, workflow_run)
        raise
    except Exception as exc:
        attach_workflow_failure(exc,workflow_run)
        # Any SQLAlchemy DB-level failure (LockNotAvailableError wrapped in
        # DBAPIError, PendingRollbackError, connection errors) leaves the
        # session unusable. Attempting further writes triggers autoflush →
        # connection checkout → pool_pre_ping → ``MissingGreenlet`` which
        # masks the real error. Rollback first and re-raise so the reaper
        # can pick up the workflow_run row instead.
        if _is_db_session_failure(session, exc):
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
        except (PendingRollbackError, DBAPIError, MissingGreenlet):
            await session.rollback()
        raise


@pipeline_operation_stage
async def run_chapter_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    requested_by: str = "system",
    export_markdown: bool = False,
    allow_structural_repair: bool = False,
    chapter_first: bool | None = None,
    supersede_pending_rewrites: bool | None = None,
    resume_existing_draft_only: bool = False,
    review_source_binding: Mapping[str, str] | None = None,
    critic_cycle_id: str | None = None,
    progress: ProgressCallback | None = None,
) -> ChapterPipelineResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    from bestseller.services.creation_configuration import project_settings, read_window
    settings = project_settings(settings, project)
    execution_window = read_window(project)
    if execution_window is not None and chapter_number not in execution_window.numbers():
        raise ValueError("chapter_outside_execution_window")
    from bestseller.services.planner import _assert_planning_capabilities_available

    _assert_planning_capabilities_available(project, settings)
    project_id = project.id
    project_target_chapters = project.target_chapters
    _assert_project_not_blocked_for_structural_repair(
        project,
        project_slug=project_slug,
        operation=f"chapter pipeline {chapter_number}",
        allow_structural_repair=allow_structural_repair,
    )
    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project_id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")
    chapter_id = chapter.id
    loaded_chapter_number = int(chapter.chapter_number or chapter_number)
    # Zero-call native compatibility/rearm precedes any NEW chapter WF or
    # paid outline/backfill work. Project runners call the same helper before
    # creating their parent WF; an existing active owner is never bypassed.
    if (chapter.metadata_json or {}).get("chapter_critic_cycles"):
        from bestseller.services.chapter_review_reconciliation import prepare_native_review_resume
        await prepare_native_review_resume(session, settings, project_id=project_id,
            chapter_id=chapter_id, requested_by=requested_by)
    locked_review_source = None
    if resume_existing_draft_only:
        if export_markdown or supersede_pending_rewrites:
            raise ValueError("chapter_review_recovery_incompatible_mode")
        entry_draft = await session.scalar(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.project_id == project_id,
                ChapterDraftVersionModel.chapter_id == chapter_id,
                ChapterDraftVersionModel.is_current.is_(True),
            ).execution_options(populate_existing=True)
        )
        if entry_draft is None:
            raise ValueError("chapter_review_recovery_current_draft_missing")
        actual_binding = {
            "draft_id": str(entry_draft.id),
            "body_hash": chapter_review_body_fingerprint(entry_draft.content_md or "")["digest"],
        }
        if review_source_binding is not None and dict(review_source_binding) != actual_binding:
            raise ValueError("chapter_review_recovery_source_mismatch")
        locked_review_source = dict(actual_binding)

    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )
    if not scenes:
        raise ValueError(f"Chapter {chapter_number} does not have any scene cards to process.")

    # A chapter-prefix approval authorizes only its exact materialized scope.
    # Recheck current source/grade evidence before any paid backfill or prose.
    prefix_marker = (chapter.metadata_json or {}).get("outline_prefix_readiness")
    from bestseller.services.outline_source_entry import current_outline_artifact
    latest_batch = await current_outline_artifact(session, project=project, chapter=chapter)
    prefix_source = (
        ((latest_batch.content.get("_meta") or {}).get("sourcebound_readiness_receipt"))
        if latest_batch is not None and isinstance(latest_batch.content, dict) else None
    )
    if prefix_marker or prefix_source:
        from bestseller.services.chapter_outline_readiness import verify_outline_prefix_readiness

        if not prefix_marker:
            raise ValueError("Approved prefix is not bound to this materialized chapter")
        await verify_outline_prefix_readiness(
            session, project=project, artifact_id=UUID(prefix_marker["artifact_id"]),
            chapter=chapter, scenes=scenes,
        )
    elif latest_batch is not None:
        from bestseller.services.materialized_outline_admission_read import require_materialized_outline_source_admission
        await require_materialized_outline_source_admission(
            session, settings, project=project, artifact=latest_batch, chapter=chapter, scenes=scenes,
        )

    if not resume_existing_draft_only and "outline_source_admission" in (chapter.metadata_json or {}):
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from bestseller.services.rewrite_outline_dispatch import (
            prepare_outline_alignment_dispatch, execute_outline_alignment_dispatch,
        )
        from bestseller.services.retry_ledger import project_execution_scope
        from bestseller.services.rewrite_patch import text_hash
        current = await session.scalar(select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.project_id == project_id,
            ChapterDraftVersionModel.chapter_id == chapter_id,
            ChapterDraftVersionModel.is_current.is_(True)))
        if current is not None:
            # No old-body retained-review preflight or optional style work may
            # intercept an explicitly staged, source-bound alignment rewrite.
            marker = deepcopy(chapter.metadata_json["outline_source_admission"])
            draft_id, body_hash = current.id, text_hash(current.content_md or "")
            scope = project_execution_scope(project)
            await _checkpoint_commit(session)
            alignment = await prepare_outline_alignment_dispatch(session,
                project_id=project_id, chapter_id=chapter_id,
                expected_draft_id=draft_id, expected_body_sha256=body_hash,
                expected_plan_binding=marker, original_execution_scope_id=scope,
                requested_by=requested_by, settings=settings)
            # Release short preparation locks before the independently retained
            # LLM run links to its durable workflow row.
            await _checkpoint_commit(session)
            if alignment.state == "prepared":
                _emit_progress(progress, "chapter_outline_alignment_started", {
                    "project_slug": project_slug, "chapter_number": chapter_number,
                    "workflow_run_id": str(alignment.workflow_run_id),
                    "rewrite_task_id": str(alignment.task_id)})
                alignment = await execute_outline_alignment_dispatch(
                    async_sessionmaker(session.bind, expire_on_commit=False), settings,
                    workflow_run_id=alignment.workflow_run_id)
            if alignment.state == "review_ready":
                session.expire_all()
                reviewed = await run_chapter_pipeline(session, settings, project_slug, chapter_number,
                    requested_by=requested_by, export_markdown=False,
                    allow_structural_repair=allow_structural_repair, chapter_first=True,
                    supersede_pending_rewrites=False, resume_existing_draft_only=True,
                    review_source_binding=alignment.review_source_binding, progress=progress)
                await _checkpoint_commit(session)
                if reviewed.final_verdict == "rewrite":
                    # The new body's actual review now owns any further edits.
                    # The dispatcher verifies that receipt and lets the usual
                    # bounded cached-review/rewrite loop continue once.
                    return await run_chapter_pipeline(session, settings, project_slug, chapter_number,
                        requested_by=requested_by, export_markdown=export_markdown,
                        allow_structural_repair=allow_structural_repair, chapter_first=chapter_first,
                        supersede_pending_rewrites=False, progress=progress)
                if export_markdown and reviewed.final_verdict == "pass":
                    await export_chapter_markdown(session, settings, project_slug, chapter_number,
                        created_by_run_id=reviewed.workflow_run_id)
                return reviewed
            if alignment.state != "not_applicable":
                raise ValueError(alignment.reason or "outline_alignment_not_executable")

    use_chapter_first = _chapter_first_requested(
        settings,
        chapter_number,
        chapter_first,
        chapter,
        project,
    )
    if resume_existing_draft_only and not use_chapter_first:
        raise ValueError("chapter_review_recovery_requires_chapter_first")
    should_supersede_pending_rewrites = (
        bool(supersede_pending_rewrites)
        if supersede_pending_rewrites is not None
        else bool(getattr(settings.pipeline, "chapter_first_supersede_pending_rewrites", False))
    )

    retained_review_reuse = None
    waiting_cycles = (chapter.metadata_json or {}).get("chapter_critic_cycles") or {}
    active_review = (chapter.metadata_json or {}).get("chapter_review_active_cycle") or {}
    # A separately bound new-policy recovery can create its own cycle. Waiting
    # within an existing cycle never authorizes ordinary re-entry to dispatch.
    new_bound_recovery = bool(
        resume_existing_draft_only and critic_cycle_id
        and isinstance(active_review, Mapping)
        and active_review.get("cycle_id") == critic_cycle_id
        and active_review.get("state") in {"prepared", "running"}
        and isinstance(waiting_cycles, Mapping)
        and critic_cycle_id not in waiting_cycles
    )
    has_waiting_review = isinstance(waiting_cycles, Mapping) and any(
        isinstance(c, Mapping) and c.get("state") == "waiting_infrastructure"
        for c in waiting_cycles.values()
    )
    if (has_waiting_review and not new_bound_recovery) or (
        use_chapter_first and not resume_existing_draft_only and not should_supersede_pending_rewrites
    ):
        from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing
        retained_review_reuse = await validate_retained_review_before_processing(session, settings, chapter)
    if retained_review_reuse and retained_review_reuse.get("reuse_kind") == "waiting_infrastructure":
        return ChapterPipelineResult(
            workflow_run_id=UUID(retained_review_reuse["review_workflow_run_id"]),
            project_id=project.id, chapter_id=chapter.id, chapter_number=chapter.chapter_number,
            chapter_draft_id=UUID(retained_review_reuse["draft_id"]),
            final_verdict="unknown", review_report_id=UUID(retained_review_reuse["report_id"]),
            quality_score_id=UUID(retained_review_reuse["quality_score_id"]),
            requires_human_review=retained_review_reuse["requires_human_review"],
        )

    def retained_review_matches(draft):
        return bool(
            retained_review_reuse
            and draft is not None
            and str(draft.id) == retained_review_reuse["draft_id"]
            and chapter_review_body_fingerprint(draft.content_md or "")["digest"]
                == retained_review_reuse["body_sha256"]
            and chapter_review_recovery_source_hash() == retained_review_reuse["policy_source_hash"]
        )

    # Current source/policy rejection must precede any paid prefix recovery.
    # A valid later chapter may then fill only its adopted prefix's missing KG.
    if not settings.quality.draft_mode and loaded_chapter_number > 1:
        from bestseller.services.adopted_knowledge_resume import resume_project_adopted_knowledge

        await resume_project_adopted_knowledge(
            session, settings, project=project,
            before_chapter_number=loaded_chapter_number, require_adopted_prefix=True,
        )

    # Single-chapter/resume entry must have the same persisted technical
    # contract as the full-project entry before any paid backfill or prose.
    invariants_were_missing = project.invariants_json is None
    await _ensure_project_invariants(session, project, settings)
    if invariants_were_missing and retained_review_reuse is not None:
        from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing
        retained_review_reuse = await validate_retained_review_before_processing(session, settings, chapter)

    if retained_review_reuse is None and not resume_existing_draft_only:
        await _ensure_emotion_kernel_backfill_for_pipeline(
            session, settings, project, requested_by=requested_by,
        )
        await _ensure_public_emotion_kernel_backfill_for_pipeline(
            session, settings, project, requested_by=requested_by,
        )
        await _ensure_entry_system_backfill_for_pipeline(
            session, settings, project, requested_by=requested_by,
        )
    await _enforce_truth_version_guard(session, settings, project)

    workflow_run = await create_workflow_run(
        session,
        project_id=project_id,
        workflow_type=WORKFLOW_TYPE_CHAPTER_PIPELINE,
        status=WorkflowStatus.RUNNING,
        scope_type="chapter",
        scope_id=chapter_id,
        requested_by=requested_by,
        current_step="load_chapter_context",
        metadata={
            "project_slug": project_slug,
            "chapter_number": chapter_number,
            "scene_count": len(scenes),
            "export_markdown": export_markdown,
            "retained_review_first_reuse": deepcopy(retained_review_reuse),
            "chapter_first": use_chapter_first,
            **({"review_recovery_source": dict(locked_review_source)} if locked_review_source else {}),
        },
    )
    workflow_run_id = workflow_run.id

    step_order = 1
    current_step_name = "load_chapter_context"
    scene_results: list[ChapterPipelineSceneSummary] = []
    chapter_first_context_packet = None

    try:
        _emit_progress(
            progress,
            "chapter_step_started",
            {
                "project_slug": project_slug,
                "chapter_number": chapter_number,
                "step": current_step_name,
                "scene_count": len(scenes),
            },
        )
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
        _emit_progress(
            progress,
            "chapter_step_completed",
            {
                "project_slug": project_slug,
                "chapter_number": chapter_number,
                "step": current_step_name,
                "workflow_run_id": str(workflow_run.id),
            },
        )
        # Child scene pipelines can roll back the shared session on hard DB
        # errors. Persist the chapter workflow shell before descending.
        await _checkpoint_commit(session)

        source_owned_plan = any(key in (chapter.metadata_json or {}) for key in (
            "outline_prefix_readiness", "outline_source_admission",
        ))
        full_source_owned = "outline_source_admission" in (chapter.metadata_json or {})
        if use_chapter_first and source_owned_plan:
            # Marker presence protects ownership; only the actual source reader
            # can authorize this plan. Never renew a marker after runtime edits.
            if "outline_prefix_readiness" in (chapter.metadata_json or {}):
                from bestseller.services.chapter_outline_readiness import (
                    verify_outline_prefix_readiness,
                )

                marker = chapter.metadata_json["outline_prefix_readiness"]
                if not isinstance(marker, Mapping) or not marker.get("artifact_id"):
                    raise ValueError("source_plan_prefix_marker_invalid")
                await verify_outline_prefix_readiness(
                    session, project=project, artifact_id=UUID(marker["artifact_id"]),
                    chapter=chapter, scenes=scenes, settings=settings,
                )
            if full_source_owned:
                from bestseller.services.outline_source_entry import (
                    require_current_outline_source_admission,
                )

                if await require_current_outline_source_admission(
                    session, settings, project=project, chapter=chapter, scenes=scenes,
                ) is None:
                    raise ValueError("source_plan_current_admission_required")

        if use_chapter_first and should_supersede_pending_rewrites:
            async with session.begin_nested():
                residue_cleared = _clear_explicit_chapter_regeneration_residue(chapter)
                scene_residue_report = _clear_explicit_scene_regeneration_residue(
                    scenes,
                    chapter_target_word_count=chapter.target_word_count,
                    preserve_source_targets=source_owned_plan,
                )
                if prefix_marker or prefix_source:
                    await verify_outline_prefix_readiness(
                        session, project=project, artifact_id=UUID(prefix_marker["artifact_id"]),
                        chapter=chapter, scenes=scenes, settings=settings,
                    )
                if full_source_owned:
                    if await require_current_outline_source_admission(
                        session, settings, project=project, chapter=chapter, scenes=scenes,
                    ) is None:
                        raise ValueError("source_plan_current_admission_required")
            superseded_count = await _supersede_pending_chapter_rewrite_tasks_for_regeneration(
                session,
                project_id=project.id,
                chapter_id=chapter.id,
                reason="explicit_chapter_first_regeneration",
            )
            if superseded_count:
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "chapter_first_superseded_pending_rewrite_tasks": superseded_count,
                }
            if residue_cleared:
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "chapter_first_regeneration_residue_cleared": True,
                }
            if (
                scene_residue_report["metadata_residue_cleared"]
                or scene_residue_report["target_rebalanced"]
            ):
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "chapter_first_scene_regeneration_residue": scene_residue_report,
                }
            if (
                superseded_count
                or residue_cleared
                or scene_residue_report["metadata_residue_cleared"]
                or scene_residue_report["target_rebalanced"]
            ):
                await _checkpoint_commit(session)

        if (
            use_chapter_first
            and settings.pipeline.enable_chapter_scene_contract_materializer
            and int(getattr(project, "target_chapters", 0) or 0)
            >= int(settings.pipeline.commercial_planning_min_target_chapters)
        ):
            current_step_name = "chapter_scene_contract_materializer"
            workflow_run.current_step = current_step_name
            chapter_contract = await session.scalar(
                select(ChapterContractModel).where(
                    ChapterContractModel.project_id == project.id,
                    ChapterContractModel.chapter_id == chapter.id,
                )
            )
            verified_adoption = await chapter_contract_has_adopted_source(session, chapter=chapter)
            from bestseller.services.methodology_overlay import resolve_methodology_contract_mode

            # Keep unauthorized runtime changes inside a rollback boundary. The
            # original prefix marker is checked, never refreshed to bless drift.
            full_source_owned = "outline_source_admission" in (chapter.metadata_json or {})
            async with session.begin_nested():
                chapter_materialization_report = materialize_chapter_contract_from_chapter(
                    chapter=chapter,
                    chapter_contract=chapter_contract,
                    verified_adoption=verified_adoption,
                    source_plan_locked=any(key in (chapter.metadata_json or {}) for key in (
                        "outline_prefix_readiness", "outline_source_admission",
                    )),
                )
                materialization_report = materialize_chapter_scene_contracts(
                    chapter=chapter,
                    scenes=scenes,
                    verified_adoption=verified_adoption,
                    source_plan_locked=any(key in (chapter.metadata_json or {}) for key in (
                        "outline_prefix_readiness", "outline_source_admission",
                    )),
                    methodology_selected=resolve_methodology_contract_mode(project, settings=settings) != "off",
                )
                if prefix_marker or prefix_source:
                    await verify_outline_prefix_readiness(
                        session, project=project, artifact_id=UUID(prefix_marker["artifact_id"]),
                        chapter=chapter, scenes=scenes, settings=settings,
                    )
                if full_source_owned:
                    if await require_current_outline_source_admission(
                        session, settings, project=project, chapter=chapter, scenes=scenes,
                    ) is None:
                        raise ValueError("source_plan_current_admission_required")
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "chapter_contract": chapter_materialization_report.to_dict(),
                    "scene_contracts": materialization_report.to_dict(),
                },
            )
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "chapter_scene_contract_materialization": materialization_report.to_dict(),
                "chapter_contract_materialization": chapter_materialization_report.to_dict(),
            }
            step_order += 1
            await session.flush()
            await _checkpoint_commit(session)

        _output_base_dir = getattr(settings.output, "base_dir", None)
        if getattr(settings.pipeline, "enable_story_bible_write_gate", True) and _output_base_dir:
            from bestseller.services.story_bible_write_gate import (
                evaluate_story_bible_write_readiness,
            )

            _bible_root = _project_story_bible_root(project, _output_base_dir)
            if _bible_root.is_dir():
                _sb_report = evaluate_story_bible_write_readiness(_bible_root)
                if not _sb_report.passed and settings.pipeline.story_bible_write_block_on_failure:
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "blocked"
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "blocked_by_story_bible_write_gate": True,
                        "story_bible_write_block_codes": list(_sb_report.blocking_codes),
                    }
                    workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                    workflow_run.current_step = "story_bible_write_gate"
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "requires_human_review": True,
                        "blocked_before_scene_generation": True,
                    }
                    await session.flush()
                    await _checkpoint_commit(session)
                    return ChapterPipelineResult(
                        workflow_run_id=workflow_run.id,
                        project_id=project.id,
                        chapter_id=chapter.id,
                        chapter_number=chapter.chapter_number,
                        scene_results=scene_results,
                        chapter_draft_id=None,
                        chapter_draft_version_no=None,
                        export_artifact_id=None,
                        output_path=None,
                        requires_human_review=True,
                    )

        chapter_outline_readiness_required = (
            settings.pipeline.enable_chapter_outline_readiness_gate
            and int(getattr(project, "target_chapters", 0) or 0)
            >= int(settings.pipeline.commercial_planning_min_target_chapters)
        )
        if chapter_outline_readiness_required:
            current_step_name = "chapter_outline_readiness_gate"
            workflow_run.current_step = current_step_name
            await _supersede_obsolete_stitched_draft_tasks(
                session,
                project_id=project.id,
                chapter_id=chapter.id,
            )
            pending_rewrite_task_count = await _count_pending_chapter_rewrite_tasks(
                session,
                project_id=project.id,
                chapter_id=chapter.id,
            )
            readiness_report = evaluate_chapter_outline_readiness(
                chapter_number=chapter_number,
                chapter_title=chapter.title,
                chapter_target_word_count=chapter.target_word_count,
                chapter_metadata=chapter.metadata_json or {},
                scene_cards=scenes,
                pending_rewrite_task_count=pending_rewrite_task_count,
            )
            cleared_outline_residue = 0
            if _readiness_blocked_only_by_stale_auto_repair_residue(readiness_report):
                cleared_outline_residue = _clear_stale_scene_auto_repair_residue_for_outline_retry(
                    scenes
                )
                if cleared_outline_residue:
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "outline_readiness_auto_cleared_stale_repair_residue": (
                            cleared_outline_residue
                        ),
                    }
                    await session.flush()
                    readiness_report = evaluate_chapter_outline_readiness(
                        chapter_number=chapter_number,
                        chapter_title=chapter.title,
                        chapter_target_word_count=chapter.target_word_count,
                        chapter_metadata=chapter.metadata_json or {},
                        scene_cards=scenes,
                        pending_rewrite_task_count=pending_rewrite_task_count,
                    )
            if not readiness_report.blocked:
                cleared_chapter_outline_block = _clear_chapter_outline_readiness_block_metadata(
                        chapter,
                        recovered_by=(
                            "stale_scene_auto_repair_residue_retry"
                            if cleared_outline_residue
                            else "readiness_passed"
                        ),
                    )
                if cleared_chapter_outline_block:
                    await session.flush()
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=(
                    WorkflowStatus.MACHINE_BLOCKED
                    if readiness_report.blocked
                    and settings.pipeline.chapter_outline_readiness_block_on_failure
                    else WorkflowStatus.COMPLETED
                ),
                output_ref=readiness_report.to_dict(),
            )
            step_order += 1
            if (
                readiness_report.blocked
                and settings.pipeline.chapter_outline_readiness_block_on_failure
            ):
                blocking_issues = readiness_report.blocking_issues
                first_issue = blocking_issues[0] if blocking_issues else None
                issue_codes = [issue.code for issue in blocking_issues]
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                chapter.metadata_json = {
                    **(chapter.metadata_json or {}),
                    "blocked_by_chapter_outline_readiness_gate": True,
                    "chapter_outline_readiness_block_codes": issue_codes,
                    "chapter_outline_readiness_hint": (
                        first_issue.repair_hint
                        if first_issue
                        else "Repair the chapter outline before rerunning."
                    ),
                    "chapter_outline_readiness_report": readiness_report.to_dict(),
                }
                workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                workflow_run.current_step = current_step_name
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "requires_human_review": True,
                    "blocked_before_scene_generation": True,
                    "chapter_outline_readiness_report": readiness_report.to_dict(),
                }
                await session.flush()
                await _checkpoint_commit(session)
                return ChapterPipelineResult(
                    workflow_run_id=workflow_run.id,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    chapter_number=chapter.chapter_number,
                    scene_results=scene_results,
                    chapter_draft_id=None,
                    chapter_draft_version_no=None,
                    export_artifact_id=None,
                    output_path=None,
                    requires_human_review=True,
                )

        chapter_predraft_gate_required = (
            use_chapter_first
            and settings.pipeline.enable_chapter_predraft_quality_gate
            and int(getattr(project, "target_chapters", 0) or 0)
            >= int(settings.pipeline.commercial_planning_min_target_chapters)
        )
        if chapter_predraft_gate_required:
            current_step_name = "chapter_predraft_quality_gate"
            workflow_run.current_step = current_step_name
            target_word_count = int(
                chapter.target_word_count or settings.generation.words_per_chapter.target or 2500
            )
            chapter_first_context_packet = await build_chapter_writer_context(
                session,
                settings,
                project_slug,
                chapter_number,
            )
            predraft_bundle = build_chapter_generation_input_bundle(
                project=project,
                chapter=chapter,
                scenes=scenes,
                context_packet=chapter_first_context_packet,
                target_word_count=target_word_count,
            )
            predraft_report = evaluate_chapter_predraft_quality(predraft_bundle)
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=(
                    WorkflowStatus.MACHINE_BLOCKED
                    if predraft_report.blocked
                    and settings.pipeline.chapter_predraft_quality_gate_block_on_failure
                    else WorkflowStatus.COMPLETED
                ),
                output_ref=predraft_report.to_dict(),
            )
            step_order += 1
            if (
                predraft_report.blocked
                and settings.pipeline.chapter_predraft_quality_gate_block_on_failure
            ):
                blocking_issues = predraft_report.blocking_issues
                first_issue = blocking_issues[0] if blocking_issues else None
                issue_codes = [issue.code for issue in blocking_issues]
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                chapter.metadata_json = {
                    **(chapter.metadata_json or {}),
                    "blocked_by_chapter_predraft_quality_gate": True,
                    "chapter_predraft_quality_block_codes": issue_codes,
                    "chapter_predraft_quality_hint": (
                        first_issue.repair_hint
                        if first_issue
                        else "Repair the chapter generation input bundle before rerunning."
                    ),
                    "chapter_predraft_quality_report": predraft_report.to_dict(),
                    "chapter_generation_input_bundle": predraft_bundle.model_dump(mode="json"),
                }
                workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                workflow_run.current_step = current_step_name
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "requires_human_review": True,
                    "blocked_before_chapter_generation": True,
                    "chapter_predraft_quality_report": predraft_report.to_dict(),
                }
                await session.flush()
                await _checkpoint_commit(session)
                return ChapterPipelineResult(
                    workflow_run_id=workflow_run.id,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    chapter_number=chapter.chapter_number,
                    scene_results=scene_results,
                    chapter_draft_id=None,
                    chapter_draft_version_no=None,
                    export_artifact_id=None,
                    output_path=None,
                    requires_human_review=True,
                )

        scene_requires_human_review = False
        # Resume support: filter out already-completed scenes. In chapter-first
        # mode we intentionally skip per-scene prose generation: scene cards are
        # used as beat constraints inside one full-chapter writer call.
        pending_scenes = (
            []
            if use_chapter_first
            else (
                [s for s in scenes if s.status != SceneStatus.APPROVED.value]
                if settings.pipeline.resume_enabled
                else scenes
        )
        )
        skipped_scene_count = len(scenes) - len(pending_scenes)
        if skipped_scene_count > 0:
            logger.info(
                "Chapter %d %s: skipping %d scene pipeline(s), %d pending",
                chapter_number,
                "chapter-first" if use_chapter_first else "resume",
                skipped_scene_count,
                len(pending_scenes),
            )
            _emit_progress(
                progress,
                "chapter_resume_skipped_scenes",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "skipped_scene_count": skipped_scene_count,
                    "pending_scene_count": len(pending_scenes),
                    "scene_count": len(scenes),
                },
            )
        _scene_loop_blocked = False
        for scene_index, scene in enumerate(pending_scenes, start=1):
            current_step_name = f"scene_pipeline_{scene.scene_number}"
            workflow_run.current_step = current_step_name
            _emit_progress(
                progress,
                "chapter_scene_pipeline_started",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "scene_number": scene.scene_number,
                    "scene_progress": f"{scene_index}/{len(pending_scenes)}",
                    "chapter_workflow_run_id": str(workflow_run.id),
                },
            )
            try:
                scene_result = await run_scene_pipeline(
                    session,
                    settings,
                    project_slug,
                    chapter_number,
                    scene.scene_number,
                    requested_by=requested_by,
                    parent_workflow_run_id=workflow_run.id,
                    allow_structural_repair=allow_structural_repair,
                    progress=progress,
                )
            except WriteSafetyBlockError as exc:
                # contradiction/identity block raised during scene pipeline —
                # stamp the chapter as blocked so self-heal / auto-repair can
                # engage on the next run.  Persist the block code + hint
                # so maybe_prepare_chapter_auto_repair can find them.
                _block_code = exc.findings[0].code if exc.findings else "unknown"
                _hint = exc.findings[0].message if exc.findings else str(exc)
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                chapter.metadata_json = {
                    **(chapter.metadata_json or {}),
                    "blocked_by_write_safety_gate": True,
                    "write_safety_block_code": _block_code,
                    "write_safety_hint": _hint,
                }
                await session.flush()
                await _checkpoint_commit(session)
                _scene_loop_blocked = True
                break
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
            await _checkpoint_commit(session)
            _emit_progress(
                progress,
                "chapter_scene_pipeline_completed",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "scene_number": scene.scene_number,
                    "scene_progress": f"{scene_index}/{len(pending_scenes)}",
                    "scene_workflow_run_id": str(scene_result.workflow_run_id),
                    "final_verdict": scene_result.final_verdict,
                    "rewrite_iterations": scene_result.rewrite_iterations,
                    "requires_human_review": scene_result.requires_human_review,
                },
            )

            if scene_result.requires_human_review:
                scene_requires_human_review = True

        # Resume optimisation: if every scene was already APPROVED (nothing
        # to process) and a chapter draft already exists, reuse it rather
        # than creating a redundant new version with identical content.
        current_step_name = "assemble_chapter_draft"
        workflow_run.current_step = current_step_name
        _emit_progress(
            progress,
            "chapter_step_started",
            {
                "project_slug": project_slug,
                "chapter_number": chapter_number,
                "step": current_step_name,
                "chapter_first": use_chapter_first,
            },
        )
        chapter_draft = None
        _existing_chapter_draft: ChapterDraftVersionModel | None = None
        if use_chapter_first and not _scene_loop_blocked:
            chapter_metadata = dict(chapter.metadata_json or {})
            resume_draft_id = str(
                chapter_metadata.pop("chapter_first_resume_existing_draft_id", "") or ""
            ).strip()
            if resume_draft_id:
                try:
                    parsed_resume_draft_id = UUID(resume_draft_id)
                except ValueError:
                    parsed_resume_draft_id = None
                if parsed_resume_draft_id is not None:
                    _existing_chapter_draft = await session.scalar(
                        select(ChapterDraftVersionModel).where(
                            ChapterDraftVersionModel.chapter_id == chapter.id,
                            ChapterDraftVersionModel.id == parsed_resume_draft_id,
                            ChapterDraftVersionModel.is_current.is_(True),
                        )
                    )
                chapter.metadata_json = chapter_metadata
            if _existing_chapter_draft is not None:
                current_step_name = "resume_chapter_first_existing_draft"
                workflow_run.current_step = current_step_name
                chapter_draft = _existing_chapter_draft
                logger.info(
                    "Chapter %d chapter-first restart: reusing bounded draft v%d",
                    chapter_number,
                    chapter_draft.version_no,
                )
            else:
                # A normal chapter-first resume must review the current
                # candidate instead of discarding it and starting a shorter
                # draft.  Explicit regeneration is the only opt-out; stale
                # regeneration metadata is intentionally not treated as one.
                if settings.pipeline.resume_enabled and not should_supersede_pending_rewrites:
                    _existing_chapter_draft = await session.scalar(
                        select(ChapterDraftVersionModel).where(
                            ChapterDraftVersionModel.chapter_id == chapter.id,
                            ChapterDraftVersionModel.is_current.is_(True),
                        )
                    )
                if _existing_chapter_draft is not None:
                    current_step_name = "resume_chapter_first_existing_draft"
                    workflow_run.current_step = current_step_name
                    chapter_draft = _existing_chapter_draft
                    logger.info(
                        "Chapter %d chapter-first resume: reusing current draft v%d",
                        chapter_number,
                        chapter_draft.version_no,
                    )
                else:
                    if resume_existing_draft_only:
                        raise ValueError("chapter_review_recovery_current_draft_missing")
                    current_step_name = "generate_chapter_draft_once"
                    workflow_run.current_step = current_step_name
                    chapter_draft = await generate_chapter_draft_once(
                        session,
                        project_slug,
                        chapter_number,
                        settings=settings,
                        workflow_run_id=workflow_run.id,
                        context_packet=chapter_first_context_packet,
                    )
        elif (
            settings.pipeline.resume_enabled
            and not pending_scenes
            and getattr(chapter, "production_state", None) != "blocked"
        ):
            _existing_chapter_draft = await session.scalar(
                select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.chapter_id == chapter.id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
            )
            try:
                _budget = settings.generation.words_per_chapter
                (
                    _chapter_length_recheck_needed,
                    _actual_wc,
                ) = _existing_chapter_draft_needs_length_recheck(
                    _existing_chapter_draft,
                    language=str(getattr(project, "language", None) or "zh-CN"),
                    hard_min=int(_budget.min),
                    hard_max=int(_budget.max),
                )
                _stored_wc = int(getattr(chapter, "current_word_count", None) or 0)
                _draft_wc = (
                    int(getattr(_existing_chapter_draft, "word_count", None) or 0)
                    if _existing_chapter_draft is not None
                    else 0
                )
                # Stored values remain in the log for diagnosis, but body
                # truth alone decides whether resume needs regeneration.
            except Exception:
                _chapter_length_recheck_needed = False
            if _existing_chapter_draft is not None and not _chapter_length_recheck_needed:
                chapter_draft = _existing_chapter_draft
                logger.info(
                    "Chapter %d resume: reusing existing draft v%d",
                    chapter_number,
                    chapter_draft.version_no,
                )
            elif _existing_chapter_draft is not None:
                logger.info(
                    "Chapter %d resume: current draft v%d needs length recheck; "
                    "chapter_wc=%s draft_wc=%s actual_wc=%s",
                    chapter_number,
                    _existing_chapter_draft.version_no,
                    getattr(chapter, "current_word_count", None),
                    getattr(_existing_chapter_draft, "word_count", None),
                    locals().get("_actual_wc"),
                )
        if chapter_draft is None and not _scene_loop_blocked:
            chapter_draft = await assemble_chapter_draft(
                session, project_slug, chapter_number, settings=settings
            )
        if chapter_draft is not None:
            length_request = (chapter_draft.promotion_metadata or {}).get("book_length_control")
            if "book_length_control" in (chapter_draft.promotion_metadata or {}):
                length_preview = full_flow_length.evaluate_candidate(
                    length_request, chapter_draft.content_md)
                if not length_preview["length_passed"]:
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "blocked"
                    workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                    workflow_run.current_step = "book_length_control_blocked"
                    workflow_run.error_message = length_preview["issue_code"]
                    workflow_run.metadata_json = {**(workflow_run.metadata_json or {}),
                        "book_length_control_candidate": length_preview,
                        "chapter_draft_id": str(chapter_draft.id),
                        "requires_human_review": True, "auto_accepted": False}
                    await session.flush()
                    return ChapterPipelineResult(
                        workflow_run_id=workflow_run.id, project_id=project.id,
                        chapter_id=chapter.id, chapter_number=chapter.chapter_number,
                        scene_results=scene_results, chapter_draft_id=chapter_draft.id,
                        chapter_draft_version_no=chapter_draft.version_no,
                        requires_human_review=True)
            # 爽点落库的汇合点。整章生成(chapter_first)直接产出草稿、不经过
            # assemble_chapter_draft，而爽点盖戳逻辑原本只内联在后者里——
            # 2026-08-16 真机定罪：三本书 109 章 hype 三字段 100% NULL。
            #
            # ⚠️ 这里曾有守卫 `hype_type is None 才盖`，本意是防止二次盖戳把
            # 同一爽点重复登记进 DiversityBudget。但它把「防重复登记」做成了
            # 「戳永不刷新」：整章重生成产出丢失爽点的新版无条件上位后，旧戳
            # 变成幽灵——2026-08-17 真机定罪，玄幻书 20 个戳 11 个幽灵
            # （ch18 v1-v5 全有 status_jump，v6 重写丢失后照样上位）。
            # 改为 refresh 模式：已盖戳章重算戳（预算不重复登记——守卫的本意
            # 由 stamp 内部的 refresh 分支承担），爽点丢失时清戳留痕。
            try:
                from bestseller.services.drafts import stamp_chapter_hype

                await stamp_chapter_hype(
                    session,
                    chapter=chapter,
                    chapter_number=chapter_number,
                    content_md=chapter_draft.content_md or "",
                    project=project,
                    scene_drafts=(),
                    refresh=getattr(chapter, "hype_type", None) is not None,
                )
            except Exception:
                logger.debug(
                    "chapter %d: hype stamping at convergence failed (non-fatal)",
                    chapter_number,
                    exc_info=True,
                )
        if chapter_draft is not None:
            _emit_progress(
                progress,
                "chapter_step_completed",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "step": current_step_name,
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                    "word_count": int(getattr(chapter_draft, "word_count", 0) or 0),
                },
            )
            try:
                if await _maybe_apply_deterministic_hook_echo_bridge_before_review(
                    session,
                    settings=settings,
                    project=project,
                    chapter=chapter,
                    chapter_draft=chapter_draft,
                    chapter_number=chapter_number,
                ):
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "deterministic_hook_echo_bridge_before_review": True,
                        "chapter_draft_id": str(chapter_draft.id),
                        "chapter_draft_version_no": chapter_draft.version_no,
                    }
                    _emit_progress(
                        progress,
                        "chapter_deterministic_hook_echo_bridge_applied",
                        {
                            "project_slug": project_slug,
                            "chapter_number": chapter_number,
                            "phase": "before_review",
                            "chapter_draft_id": str(chapter_draft.id),
                            "chapter_draft_version_no": chapter_draft.version_no,
                        },
                    )
                if await _maybe_apply_deterministic_length_trim_before_export(
                    session,
                    settings=settings,
                    project=project,
                    chapter=chapter,
                    chapter_draft=chapter_draft,
                    chapter_number=chapter_number,
                ):
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "deterministic_length_trim_before_review": True,
                        "chapter_draft_id": str(chapter_draft.id),
                        "chapter_draft_version_no": chapter_draft.version_no,
                    }
                    _emit_progress(
                        progress,
                        "chapter_deterministic_length_trim_applied",
                        {
                            "project_slug": project_slug,
                            "chapter_number": chapter_number,
                            "phase": "before_review",
                            "chapter_draft_id": str(chapter_draft.id),
                            "chapter_draft_version_no": chapter_draft.version_no,
                            "word_count": int(getattr(chapter_draft, "word_count", 0) or 0),
                        },
                    )
            except Exception:
                logger.debug(
                    "deterministic pre-review cleanup failed for ch%d",
                    chapter_number,
                    exc_info=True,
                )

            if scene_requires_human_review and not use_chapter_first:
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                export_artifact_id: UUID | None = None
                output_path: str | None = None
                if export_markdown:
                    current_step_name = "export_chapter_markdown"
                    workflow_run.current_step = current_step_name
                    _emit_progress(
                        progress,
                        "chapter_export_started",
                        {
                            "project_slug": project_slug,
                            "chapter_number": chapter_number,
                        },
                    )
                    try:
                        artifact, artifact_path = await export_chapter_markdown(
                            session,
                            settings,
                            project_slug,
                            chapter_number,
                            created_by_run_id=workflow_run.id,
                        )
                    except (ValueError, OSError) as exc:
                        chapter.metadata_json = {
                            **(chapter.metadata_json or {}),
                            "export_blocked_reason": str(exc),
                            "export_blocked_by_run_id": str(workflow_run.id),
                        }
                        await create_workflow_step_run(
                            session,
                            workflow_run_id=workflow_run.id,
                            step_name=current_step_name,
                            step_order=step_order,
                            status=WorkflowStatus.COMPLETED,
                            output_ref={"export_blocked": str(exc)},
                        )
                        step_order += 1
                        _emit_progress(
                            progress,
                            "chapter_export_blocked",
                            {
                                "project_slug": project_slug,
                                "chapter_number": chapter_number,
                                "reason": str(exc),
                            },
                        )
                    else:
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
                            "chapter_export_completed",
                            {
                                "project_slug": project_slug,
                                "chapter_number": chapter_number,
                                "export_artifact_id": str(export_artifact_id),
                                "output_path": output_path,
                            },
                        )
                workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                workflow_run.current_step = "scene_machine_repair_required"
                workflow_run.metadata_json = {
                    **workflow_run.metadata_json,
                    "requires_human_review": True,
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                    "scene_requires_human_review": True,
                    "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
                    "auto_repair_skipped_reason": "scene_machine_blocked",
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

            # ── P1 Originality Engine — post-write persona feedback ──
            # Grade the assembled chapter against the 7 reader personas
            # and persist the result to
            # ``output/<slug>/knowledge/persona-feedback/after-ch-NNN.json``.
            # The next chapter's pre-write hook (above) reads this file
            # via ``load_latest_feedback`` and injects the directives into
            # the next chapter's prompt. Non-fatal on any failure.
            try:
                _orig_cfg = get_quality_gates_config().originality_engine
                if (
                    _orig_cfg.enabled
                    and _orig_cfg.persist_persona_feedback
                    and chapter_draft.content_md
                ):
                    from bestseller.services.chapter_orchestrator import (
                        ensure_voice_dna as _ensure_voice_dna,
                    )
                    from bestseller.services.chapter_orchestrator import (
                        grade_chapter as _grade_chapter,
                    )
                    from bestseller.services.chapter_orchestrator import (
                        prepare_chapter_context as _prep_for_grade,
                    )

                    _grade_mode_b = (
                        bool(_orig_cfg.mode_b_override)
                        if (_orig_cfg.mode_b_override is not None)
                        else False
                    )
                    _grade_text = chapter_draft.content_md or ""
                    if (
                        _orig_cfg.grading_text_cap_chars > 0
                        and len(_grade_text) > _orig_cfg.grading_text_cap_chars
                    ):
                        _grade_text = _grade_text[: _orig_cfg.grading_text_cap_chars]
                    # Self-bootstrap Voice DNA from the book's own earliest
                    # accepted prose — platform projects have no external
                    # reference corpus, so the first chapter long enough to
                    # extract from becomes the voice anchor later chapters
                    # are held to. No-op once voice-dna.json exists.
                    if _orig_cfg.auto_voice_dna:
                        try:
                            _ensure_voice_dna(
                                project_slug,
                                sample_text=chapter_draft.content_md or "",
                                source_id=f"self-ch{chapter_number}",
                                source_label=(f"{project_slug} self-anchor ch{chapter_number}"),
                                excluded_phrases=_voice_dna_excluded_names(project),
                                min_sample_chars=(_orig_cfg.voice_dna_min_sample_chars),
                                output_base_dir=settings.output.base_dir,
                                mode_b=_grade_mode_b,
                            )
                        except Exception:
                            logger.debug(
                                "voice DNA auto-bootstrap failed for ch%d (non-fatal)",
                                chapter_number,
                                exc_info=True,
                            )
                    _grade_ctx = _prep_for_grade(
                        project_slug,
                        chapter_number,
                        output_base_dir=settings.output.base_dir,
                        mode_b=_grade_mode_b,
                    )
                    # P2: LLM reader-judge. Always persist when enabled; only
                    # feed persona when audit_only is False. Voice-axis enforce
                    # is separate and defaults OFF (safe for in-flight books).
                    _prose_quality_score = None
                    try:
                        _rq_cfg = get_quality_gates_config().reader_quality
                        if (
                            getattr(_rq_cfg, "enable_llm_reader_judge", False)
                            and not retained_review_matches(chapter_draft)
                            and not (
                                resume_existing_draft_only
                                and getattr(_rq_cfg, "reader_judge_audit_only", True)
                                and not getattr(_rq_cfg, "enforce_reader_judge_voice_axes", False)
                            )
                        ):
                            from bestseller.services.reader_judge import (
                                judge_chapter_readability,
                                voice_axis_failures,
                            )

                            _judge = await judge_chapter_readability(
                                session,
                                settings,
                                _grade_text,
                                chapter_number=chapter_number,
                                project_id=project.id,
                                workflow_run_id=workflow_run.id,
                                text_cap_chars=int(
                                    getattr(_rq_cfg, "reader_judge_text_cap_chars", 8000) or 8000
                                ),
                            )
                            _audit_only = bool(getattr(_rq_cfg, "reader_judge_audit_only", True))
                            if not _audit_only:
                                _prose_quality_score = _judge.prose_quality_score
                            _voice_issues = voice_axis_failures(
                                _judge.dimensions,
                                min_ai_taste=float(getattr(_rq_cfg, "min_ai_taste", 0.55)),
                                min_human_voice=float(getattr(_rq_cfg, "min_human_voice", 0.55)),
                                enforce=bool(
                                    getattr(
                                        _rq_cfg,
                                        "enforce_reader_judge_voice_axes",
                                        False,
                                    )
                                ),
                            )
                            _prior_meta = chapter.metadata_json or {}
                            _voice_rewrites = int(
                                _prior_meta.get("reader_judge_voice_rewrite_count") or 0
                            )
                            if _voice_issues:
                                _voice_rewrites += 1
                            _stall_after = int(
                                getattr(
                                    _rq_cfg,
                                    "reader_judge_voice_rewrite_stall_after",
                                    2,
                                )
                                or 2
                            )
                            _voice_stalled = bool(_voice_issues and _voice_rewrites >= _stall_after)
                            chapter.metadata_json = {
                                **_prior_meta,
                                "reader_judge": {
                                    **_judge.to_dict(),
                                    "audit_only": _audit_only,
                                },
                                "reader_judge_voice_failures": _voice_issues,
                                "reader_judge_voice_rewrite_count": _voice_rewrites,
                                "reader_judge_voice_stalled": _voice_stalled,
                                "reader_judge_voice_debt": _voice_stalled,
                            }
                    except Exception:
                        logger.debug(
                            "reader_judge failed ch%d (non-fatal)",
                            chapter_number,
                            exc_info=True,
                        )
                    _grade_chapter(
                        _grade_ctx,
                        _grade_text,
                        output_base_dir=settings.output.base_dir,
                        mode_b=_grade_mode_b,
                        prose_quality_score=_prose_quality_score,
                        persist=True,
                    )
            except Exception:
                logger.debug(
                    "P1 Originality Engine post-write grading failed for ch%d (non-fatal)",
                    chapter_number,
                    exc_info=True,
                )
            try:
                _orig_cfg = get_quality_gates_config().originality_engine
                if _orig_cfg.enabled and chapter_draft.content_md:
                    await _evaluate_retention_safety_after_assembly(
                        session,
                        project=project,
                        chapter=chapter,
                        chapter_draft=chapter_draft,
                        chapter_number=chapter_number,
                        output_base_dir=settings.output.base_dir,
                    )
            except Exception:
                logger.debug(
                    "retention_safety_gate evaluation failed for ch%d (non-fatal)",
                    chapter_number,
                    exc_info=True,
                )

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
        if use_chapter_first:
            _project_meta = project.metadata_json if isinstance(project.metadata_json, dict) else {}
            _local_cap = int(_project_meta.get("chapter_first_local_repair_max_attempts") or 2)
            _full_cap = int(_project_meta.get("chapter_first_full_regeneration_max_attempts") or 1)
            auto_repair_cap = min(
                auto_repair_cap,
                max(0, _local_cap) + max(0, _full_cap),
            )
        auto_repair_enabled = bool(getattr(settings.pipeline, "enable_chapter_auto_repair", False))
        if resume_existing_draft_only or retained_review_matches(chapter_draft):
            # Run the actual gates on retained prose, but do not turn a new
            # review-policy cycle into a scene/whole-chapter regeneration.
            auto_repair_enabled = False
        # Cross-run cumulative budget. ``auto_repair_cap`` above is intra-run
        # only; this counter survives across ``chapter_pipeline`` invocations
        # via ``chapter.metadata_json['auto_repair_total_attempts']``, which
        # gets bumped inside ``maybe_prepare_chapter_auto_repair`` and wiped
        # whenever the chapter clears the quality gate. When the cumulative
        # budget is spent we refuse to enter the loop and route to human
        # review — prevents the cross-run loops we observed on 青囊 ch1.
        auto_repair_total_cap = int(
            getattr(
                settings.pipeline,
                "chapter_auto_repair_total_max_attempts",
                0,
            )
            or 0
        )
        auto_repair_total_used = int(
            (chapter.metadata_json or {}).get("auto_repair_total_attempts") or 0
        )
        chapter_first_local_repair_used = int(
            (chapter.metadata_json or {}).get("chapter_first_local_repair_count") or 0
        )
        retention_auto_repair_exhausted = False
        auto_repair_codes = tuple(
            str(c)
            for c in getattr(
                settings.pipeline,
                "chapter_auto_repair_repairable_codes",
                (),
            )
            or ()
            if c
        )
        try:
            from bestseller.services.retention_safety_gate import (
                AUTO_REPAIR_RETENTION_CODES,
            )

            auto_repair_codes = tuple(
                dict.fromkeys((*auto_repair_codes, *AUTO_REPAIR_RETENTION_CODES))
            )
        except Exception:
            logger.debug("retention auto-repair code merge failed", exc_info=True)

        cross_run_budget_exhausted = (
            auto_repair_enabled
            and auto_repair_total_cap > 0
            and auto_repair_total_used >= auto_repair_total_cap
            and (getattr(chapter, "production_state", None) == "blocked" or _scene_loop_blocked)
            )

        # Retention/persona findings are advisory for the default pipeline.
        # A chapter can already have a clean chapter-quality report while the
        # retention scorer leaves codes such as ENDING_HOOK_MISSING or
        # PERSONA_WEIGHTED_SCORE_LOW on the chapter. Treating those codes as a
        # hard _scene_loop_blocked signal re-runs every scene and can spend
        # the entire book repeatedly rewriting the same chapter. Preserve the
        # diagnostics and accept the best assembled draft on this pass; strict
        # retention mode still keeps the old blocking behavior.
        _retention_only_codes = _block_codes_are_retention_only(
            _current_auto_repair_block_codes(chapter)
        )
        if (
            _retention_only_codes
            and getattr(chapter, "production_state", None) == "blocked"
            and not _retention_gate_blocks_for_project(project, settings)
        ):
            chapter.production_state = "ok"
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "retention_accepted_on_stall": True,
                "retention_acceptance_reason": "retention_only_findings",
                "requires_machine_repair": False,
                "auto_accepted": True,
            }
            _scene_loop_blocked = False
            await session.flush()
            logger.info(
                "Chapter %d: accepted assembled draft with retention-only findings "
                "without chapter-wide auto-repair",
                chapter_number,
            )

        if cross_run_budget_exhausted:
            logger.warning(
                "Chapter %d: cross-run auto_repair budget exhausted "
                "(total_used=%d cap=%d); refusing to enter repair loop, "
                "routing to machine repair.",
                chapter_number,
                auto_repair_total_used,
                auto_repair_total_cap,
            )
            chapter.status = ChapterStatus.REVISION.value
            chapter.production_state = "blocked"
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "auto_repair_exhausted": True,
                "auto_repair_cross_run_exhausted": True,
                "auto_repair_in_progress": False,
                "auto_accepted": False,
                "requires_machine_repair": True,
                "requires_human_review": False,
            }
            scene_requires_human_review = False
            await session.flush()

        while (
            auto_repair_enabled
            and auto_repair_cap > 0
            and auto_repair_attempts < auto_repair_cap
            and not cross_run_budget_exhausted
            and (getattr(chapter, "production_state", None) == "blocked" or _scene_loop_blocked)
        ):
            if await _stop_auto_repair_if_latest_quality_clean(session, chapter):
                _scene_loop_blocked = False
                logger.info(
                    "Chapter %d: stopped auto-repair loop after latest clean quality report",
                    chapter_number,
                )
                break
            try:
                _retry_cfg = get_quality_gates_config().originality_engine
                _pre_repair_block_codes = _current_auto_repair_block_codes(chapter)
                if use_chapter_first:
                    _preview_full_regen_reason = _chapter_first_full_regeneration_reason(
                        project,
                        chapter,
                        chapter_draft,
                        _pre_repair_block_codes,
                        attempt_number=auto_repair_attempts + 1,
                    )
                    if (
                        _preview_full_regen_reason is None
                        and chapter_first_local_repair_used >= max(0, _local_cap)
                    ):
                        logger.warning(
                            "Chapter %d: chapter-first local repair budget exhausted "
                            "(%d/%d); refusing a third local rewrite",
                            chapter_number,
                            chapter_first_local_repair_used,
                            max(0, _local_cap),
                        )
                        break
                if _apply_rewrite_escalation(
                    chapter,
                    _pre_repair_block_codes,
                    _retry_cfg,
                ):
                    logger.warning(
                        "Chapter %d: retention auto-repair exhausted after %d attempt(s); "
                        "routing to machine repair",
                        chapter_number,
                        int((chapter.metadata_json or {}).get("retention_retry_count", 0) or 0),
                            )
                    retention_auto_repair_exhausted = True
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "blocked"
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "auto_repair_exhausted": True,
                        "retention_auto_repair_exhausted": True,
                        "requires_machine_repair": True,
                        "requires_human_review": False,
                        "auto_accepted": False,
                    }
                    scene_requires_human_review = False
                    await session.flush()
                    break
                from bestseller.services.drafts import (
                    maybe_prepare_chapter_auto_repair,
                )

                repair_triggered, block_codes = await maybe_prepare_chapter_auto_repair(
                    session,
                    settings=settings,
                    project=project,
                    chapter=chapter,
                    repairable_codes=auto_repair_codes,
                    attempt_number=auto_repair_attempts + 1,
                )
            except Exception:
                logger.warning(
                    "Chapter %d auto-repair prepare failed (non-fatal)",
                    chapter_number,
                    exc_info=True,
                )
                break

            if not repair_triggered:
                # 没有任何阻断码 = 这一章其实是干净的（软判定如
                # PERSONA_WEIGHTED_SCORE_LOW 给不出可修的东西），把它留在
                # blocked 只是让它去排机器修复的队、并给全书攒一笔假债。
                # 2026-08-07 已为同一族误杀（plateau 判「没长进」标 blocked）
                # 付过学费，指纹相同：production_state=blocked 而该章从无
                # blocks_write=true 的质检报告。真机 2026-08-14 ch1 复现。
                if not block_codes:
                    logger.info(
                        "Chapter %d: no blocking codes — clean chapter, "
                        "recording quality debt instead of blocking",
                        chapter_number,
                    )
                    # 这条出口是 2026-08-14 修 plateau 误杀时新开的，当时漏了
                    # 选稿：和另外三条 quality_debt 出口一样，必须先把分最高的
                    # 草稿扶正再盖债务戳，否则发布的是「最后跑完的那一版」而不是
                    # 「最好的那一版」（真机 ch1 曾 1860→1599→1570 逐版退化）。
                    # 债务戳一旦盖在即将被替换的稿子上，账就记错了对象。
                    if use_chapter_first and chapter_draft is not None and not resume_existing_draft_only:
                        chapter_draft = await _promote_best_scoring_chapter_draft_on_stall(
                            session,
                            chapter=chapter,
                            current_draft=chapter_draft,
                            project=project,
                            settings=settings,
                        )
                    chapter.production_state = "quality_debt"
                    await session.flush()
                    break
                logger.info(
                    "Chapter %d: block codes %s not auto-repairable — leaving "
                    "chapter in blocked state",
                    chapter_number,
                    list(block_codes),
                )
                break

            auto_repair_attempts += 1
            current_step_name = f"chapter_auto_repair_attempt_{auto_repair_attempts}"
            workflow_run.current_step = current_step_name
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "chapter_auto_repair_attempts": auto_repair_attempts,
                "chapter_auto_repair_last_block_codes": list(block_codes) if block_codes else [],
            }
            logger.warning(
                "Chapter %d: auto-repair attempt %d/%d triggered for blocks %s",
                chapter_number,
                auto_repair_attempts,
                auto_repair_cap,
                list(block_codes) if block_codes else [],
            )
            _emit_progress(
                progress,
                "chapter_auto_repair_started",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "attempt": auto_repair_attempts,
                    "max_attempts": auto_repair_cap,
                    "block_codes": list(block_codes) if block_codes else [],
                },
            )

            if use_chapter_first:
                full_regen_reason = _chapter_first_full_regeneration_reason(
                    project,
                    chapter,
                    chapter_draft,
                    block_codes,
                    attempt_number=auto_repair_attempts,
                )
                chapter_repair_task = None
                if full_regen_reason:
                    current_step_name = (
                        f"chapter_first_auto_repair_regenerate_{auto_repair_attempts}"
                    )
                    workflow_run.current_step = current_step_name
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "chapter_first_full_regeneration_instead_of_patch": True,
                        "chapter_first_full_regeneration_reason": full_regen_reason,
                        "chapter_first_full_regeneration_attempt": auto_repair_attempts,
                        "chapter_first_full_regeneration_count": int(
                            (chapter.metadata_json or {}).get(
                                "chapter_first_full_regeneration_count", 0
                            )
                            or 0
                        )
                        + 1,
                    }
                    _emit_progress(
                        progress,
                        "chapter_auto_repair_regenerating_full_chapter",
                        {
                            "project_slug": project_slug,
                            "chapter_number": chapter_number,
                            "attempt": auto_repair_attempts,
                            "reason": full_regen_reason,
                            "block_codes": list(block_codes) if block_codes else [],
                        },
                    )
                    chapter_draft = await generate_chapter_draft_once(
                        session,
                        project_slug,
                        chapter_number,
                        settings=settings,
                        workflow_run_id=workflow_run.id,
                        context_packet=chapter_first_context_packet,
                        regeneration_feedback=build_regeneration_feedback(project, chapter, chapter_draft, block_codes, full_regen_reason),
                    )
                    generation_mode = "chapter_first_full_regeneration"
                else:
                    current_step_name = f"chapter_first_auto_repair_rewrite_{auto_repair_attempts}"
                    workflow_run.current_step = current_step_name
                    chapter_repair_task = await _create_chapter_first_local_auto_repair_task(
                        session,
                        project=project,
                        chapter=chapter,
                        block_codes=block_codes,
                        attempt_number=auto_repair_attempts,
                        source_draft_version_id=chapter_draft.id,
                    )
                    if chapter_repair_task is None:
                        chapter.metadata_json = {
                            **(chapter.metadata_json or {}),
                            "chapter_first_local_repair_source_blocked": {
                                "reason": "candidate_is_not_current",
                                "candidate_draft_id": str(chapter_draft.id),
                            },
                        }
                        break
                    chapter_draft, chapter_repair_task = await rewrite_chapter_from_task(
                        session,
                        project_slug,
                        chapter_number,
                        rewrite_task_id=chapter_repair_task.id,
                        settings=settings,
                        workflow_run_id=workflow_run.id,
                    )
                    chapter_first_local_repair_used += 1
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "chapter_first_local_repair_count": (chapter_first_local_repair_used),
                    }
                    generation_mode = "chapter_first_local_rewrite"
                try:
                    _orig_cfg = get_quality_gates_config().originality_engine
                    if _orig_cfg.enabled and chapter_draft.content_md:
                        await _evaluate_retention_safety_after_assembly(
                            session,
                            project=project,
                            chapter=chapter,
                            chapter_draft=chapter_draft,
                            chapter_number=chapter_number,
                            output_base_dir=settings.output.base_dir,
                        )
                except Exception:
                    logger.debug(
                        "retention_safety_gate chapter-first repair evaluation failed "
                        "for ch%d (non-fatal)",
                        chapter_number,
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
                        "auto_repair_attempt": auto_repair_attempts,
                        "generation_mode": generation_mode,
                        "full_regeneration_reason": full_regen_reason,
                        "rewrite_task_id": str(chapter_repair_task.id)
                        if chapter_repair_task is not None
                        else None,
                    },
                )
                step_order += 1
                _emit_progress(
                    progress,
                    "chapter_auto_repair_completed",
                    {
                        "project_slug": project_slug,
                        "chapter_number": chapter_number,
                        "attempt": auto_repair_attempts,
                        "chapter_draft_id": str(chapter_draft.id),
                        "chapter_draft_version_no": chapter_draft.version_no,
                        "generation_mode": generation_mode,
                        "full_regeneration_reason": full_regen_reason,
                        "rewrite_task_id": str(chapter_repair_task.id)
                        if chapter_repair_task is not None
                        else None,
                    },
                )
                if await _stop_auto_repair_if_latest_quality_clean(
                    session,
                    chapter,
                ):
                    _scene_loop_blocked = False
                    logger.info(
                        "Chapter %d: chapter-first auto-repair produced a clean "
                        "quality report; stopping loop",
                        chapter_number,
                    )
                    break
                continue

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
            _repair_blocked_again = False
            for _repair_scene in repair_scenes:
                try:
                    _repair_result = await run_scene_pipeline(
                        session,
                        settings,
                        project_slug,
                        chapter_number,
                        _repair_scene.scene_number,
                        requested_by=requested_by,
                        parent_workflow_run_id=workflow_run.id,
                        allow_structural_repair=allow_structural_repair,
                        progress=progress,
                    )
                except WriteSafetyBlockError as exc:
                    # The repair pass tripped the same kind of safety block as
                    # the initial run. Re-stamp the chapter so the next while
                    # iteration (or the final post-loop check below) sees the
                    # blocked state and either retries or escalates to human
                    # review — whichever the auto_repair_cap dictates.
                    _block_code = exc.findings[0].code if exc.findings else "unknown"
                    _hint = exc.findings[0].message if exc.findings else str(exc)
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "blocked"
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "blocked_by_write_safety_gate": True,
                        "write_safety_block_code": _block_code,
                        "write_safety_hint": _hint,
                    }
                    await session.flush()
                    await _checkpoint_commit(session)
                    _repair_blocked_again = True
                    break
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
            # If the repair pass itself tripped a safety block, skip the
            # reassemble step (the chapter is still blocked) and let the
            # while-loop's blocked-state check decide whether to retry or
            # escalate.
            if _repair_blocked_again:
                continue
            _scene_loop_blocked = False

            # Re-assemble with the repaired scenes so the next gate pass sees
            # a fresh chapter_draft + the length-stability helper re-scores.
            current_step_name = f"chapter_auto_repair_reassemble_{auto_repair_attempts}"
            workflow_run.current_step = current_step_name
            chapter_draft = await assemble_chapter_draft(
                session, project_slug, chapter_number, settings=settings
            )
            try:
                _orig_cfg = get_quality_gates_config().originality_engine
                if _orig_cfg.enabled and chapter_draft.content_md:
                    await _evaluate_retention_safety_after_assembly(
                        session,
                        project=project,
                        chapter=chapter,
                        chapter_draft=chapter_draft,
                        chapter_number=chapter_number,
                        output_base_dir=settings.output.base_dir,
                    )
            except Exception:
                logger.debug(
                    "retention_safety_gate repair-pass evaluation failed for ch%d (non-fatal)",
                    chapter_number,
                    exc_info=True,
                )

            # The repair pass re-runs the retention scorer and may stamp the
            # same advisory codes back onto the chapter. Release that
            # retention-only block immediately; otherwise the while-loop
            # starts another full scene rewrite even though the assembled
            # chapter is usable and strict retention mode is disabled.
            if _block_codes_are_retention_only(
                    _current_auto_repair_block_codes(chapter)
            ) and not _retention_gate_blocks_for_project(project, settings):
                chapter.production_state = "ok"
                chapter.metadata_json = {
                    **(chapter.metadata_json or {}),
                    "retention_accepted_on_stall": True,
                    "retention_acceptance_reason": "retention_only_findings",
                    "requires_machine_repair": False,
                    "auto_accepted": True,
                }
                _scene_loop_blocked = False
                await session.flush()
                logger.info(
                    "Chapter %d: stopping retention-only repair loop after one bounded pass",
                    chapter_number,
                )
                break

            _emit_progress(
                progress,
                "chapter_auto_repair_completed",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "attempt": auto_repair_attempts,
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                },
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

        if chapter_draft is None:
            logger.warning(
                "Chapter %d: scene pipeline produced no assemblable draft — "
                "blocking chapter for machine repair",
                chapter_number,
            )
            chapter.status = ChapterStatus.REVISION.value
            chapter.production_state = "blocked"
            workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
            workflow_run.current_step = "blocked_no_assemblable_draft"
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "requires_human_review": True,
                "chapter_draft_id": None,
                "chapter_draft_version_no": None,
                "scene_requires_human_review": True,
                "blocked_before_chapter_assembly": True,
                "auto_accepted": False,
            }
            await session.flush()
            return ChapterPipelineResult(
                workflow_run_id=workflow_run_id,
                project_id=project_id,
                chapter_id=chapter_id,
                chapter_number=loaded_chapter_number,
                scene_results=scene_results,
                chapter_draft_id=None,
                chapter_draft_version_no=None,
                export_artifact_id=None,
                output_path=None,
                requires_human_review=True,
            )

        if (
            not retention_auto_repair_exhausted
            and auto_repair_attempts > 0
            and getattr(chapter, "production_state", None) == "blocked"
        ):
            # Soft-retention fuse. The legacy soft path required
            # retention_retry_count > retention_max_retries (default 5), but
            # the repair loop exits after chapter_auto_repair_max_attempts
            # (default 3) — 3 < 6 made the soft branch unreachable and every
            # retention-blocked chapter dead-ended in machine repair. When
            # the exhausted repair budget leaves ONLY retention-class codes,
            # route through the same soft acceptance the retention gate was
            # designed for.
            _remaining_codes = _current_auto_repair_block_codes(chapter)
            if _block_codes_are_retention_only(_remaining_codes):
                logger.warning(
                    "Chapter %d: auto-repair budget exhausted with only "
                    "retention codes remaining (%s); applying soft retention "
                    "fuse instead of machine repair",
                    chapter_number,
                    list(_remaining_codes),
                )
                retention_auto_repair_exhausted = True

        if retention_auto_repair_exhausted and not _retention_gate_blocks_for_project(
            project, settings
        ):
            # Soft retention gate (default). The writer model exhausted the
            # retention/persona auto-repair budget on a bar it structurally
            # cannot clear (e.g. PERSONA_WEIGHTED_SCORE_LOW: 0.62 gate vs ~0.51
            # model ceiling per reader_persona_calibration). Rather than pausing
            # the whole book to machine repair, accept the best draft on-stall,
            # flag it, clear the blocked production_state set during exhaustion,
            # and fall through to chapter-review finalization (accept_chapter_on_stall
            # completes it). Mirrors chapter_review accept-on-stall — the gate still
            # RAN and flagged the weak chapter; only the terminal hard-block relaxes.
            # The comment above says "accept the best draft on-stall"; make that
            # true rather than accepting whichever attempt happened to run last.
            if use_chapter_first and chapter_draft is not None and not resume_existing_draft_only:
                chapter_draft = await _promote_best_scoring_chapter_draft_on_stall(
                    session,
                    chapter=chapter,
                    current_draft=chapter_draft,
                    project=project,
                    settings=settings,
                )
            chapter.production_state = "ok"
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "retention_auto_repair_exhausted": True,
                "retention_accepted_on_stall": True,
                "low_retention_quality": True,
                "requires_machine_repair": False,
                "auto_accepted": True,
            }
            retention_auto_repair_exhausted = False
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name="retention_auto_repair_accepted_on_stall",
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                    "block_codes": list(_current_auto_repair_block_codes(chapter)),
                },
            )
            step_order += 1
            _emit_progress(
                progress,
                "retention_auto_repair_accepted_on_stall",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                },
            )
            await session.flush()

        if retention_auto_repair_exhausted:
            workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
            workflow_run.current_step = "retention_auto_repair_exhausted"
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "requires_machine_repair": True,
                "requires_human_review": False,
                "chapter_draft_id": str(chapter_draft.id),
                "chapter_draft_version_no": chapter_draft.version_no,
                "retention_auto_repair_exhausted": True,
                "auto_accepted": False,
            }
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name="retention_auto_repair_exhausted",
                step_order=step_order,
                status=WorkflowStatus.MACHINE_BLOCKED,
                output_ref={
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                    "retention_retry_count": int(
                        (chapter.metadata_json or {}).get("retention_retry_count", 0) or 0
                    ),
                    "block_codes": list(_current_auto_repair_block_codes(chapter)),
                },
            )
            _emit_progress(
                progress,
                "retention_auto_repair_exhausted",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                },
            )
            await session.flush()
            return ChapterPipelineResult(
                workflow_run_id=workflow_run_id,
                project_id=project_id,
                chapter_id=chapter_id,
                chapter_number=loaded_chapter_number,
                scene_results=scene_results,
                chapter_draft_id=chapter_draft.id,
                chapter_draft_version_no=chapter_draft.version_no,
                final_verdict="rewrite",
                export_artifact_id=None,
                output_path=None,
                requires_human_review=False,
            )

        stale_auto_repair_block_released = (
            await _release_stale_auto_repair_block_if_latest_quality_clean(
                session,
                chapter,
            )
        )
        if stale_auto_repair_block_released:
            logger.info(
                "Chapter %d: released stale auto-repair block after clean quality report",
                chapter_number,
            )

        if auto_repair_attempts > 0 and getattr(chapter, "production_state", None) == "blocked":
            logger.warning(
                "Chapter %d: auto-repair exhausted %d attempt(s), still blocked — "
                "routing best available draft to machine repair",
                chapter_number,
                auto_repair_attempts,
            )
            # "best available draft" has to be computed, not assumed: every
            # repair path flips is_current to what it just produced, so without
            # this the chapter hands its *last* attempt to machine repair even
            # when an earlier attempt scored higher.
            if use_chapter_first and chapter_draft is not None and not resume_existing_draft_only:
                chapter_draft = await _promote_best_scoring_chapter_draft_on_stall(
                    session,
                    chapter=chapter,
                    current_draft=chapter_draft,
                    project=project,
                    settings=settings,
                )
            # 进终态前按**当前保留的草稿**重判一次长度（2026-08-14 真机 ch3）：
            # 提升「最佳草稿」之后，章带的往往是上一份被丢弃草稿的码。ch3 就
            # 这样带着「太长」进了 requires_machine_repair——而它实际只有 1762
            # 字（偏短）。终态是进得去出不来的：此后再也不走自动修复，我那条
            # 「按当前草稿重判长度」的修复根本够不到它，只能靠人手捞。
            # 码与稿子矛盾时，先纠正码，别把错误判决锁进终态。
            try:
                from bestseller.services.drafts import (
                    _length_direction_from_payload as _len_dir,
                )

                _cur_wc = int(getattr(chapter, "current_word_count", 0) or 0)
                _tgt_wc = int(getattr(chapter, "target_word_count", 0) or 0)
                _dir = _len_dir({"word_count": _cur_wc, "target_words": _tgt_wc})
                _stale_meta = dict(chapter.metadata_json or {})
                _stale_code = str(_stale_meta.get("production_block_code") or "")
                _wrong_high = _dir == "BLOCK_LOW" and "HIGH" in _stale_code.upper()
                _wrong_low = _dir == "BLOCK_HIGH" and "LOW" in _stale_code.upper()
                if _wrong_high or _wrong_low:
                    _fixed = (
                        "CHAPTER_LENGTH_BLOCK_LOW"
                        if _dir == "BLOCK_LOW"
                        else "CHAPTER_LENGTH_BLOCK_HIGH"
                    )
                    logger.warning(
                        "Chapter %d: stale block code %s contradicts the promoted "
                        "draft (%d chars); correcting to %s before machine repair",
                        chapter_number,
                        _stale_code,
                        _cur_wc,
                        _fixed,
                    )
                    chapter.metadata_json = {
                        **_stale_meta,
                        "production_block_code": _fixed,
                        "production_block_code_corrected_from": _stale_code,
                    }
            except Exception:
                logger.debug(
                    "chapter %d: stale length-code correction skipped",
                    chapter_number,
                    exc_info=True,
                )
            chapter.status = ChapterStatus.REVISION.value
            chapter.production_state = "blocked"
            scene_requires_human_review = True
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "auto_repair_exhausted": True,
                "auto_repair_attempts": auto_repair_attempts,
                "auto_accepted": False,
            }
            _project_meta = project.metadata_json if isinstance(project.metadata_json, dict) else {}
            # 默认值改从 settings 取：这个开关只写在 projects.metadata 里时会被
            # 跑书中的管线整块覆盖掉（2026-08-24 现场复现，写入几分钟后消失）。
            # project metadata 仍然优先（它存活时表达的是 per-book 意图）。
            _stop_default = bool(
                getattr(
                    settings.pipeline,
                    "chapter_first_stop_after_repair_exhaustion",
                    True,
                )
            )
            if use_chapter_first and bool(
                _project_meta.get("chapter_first_stop_after_repair_exhaustion", _stop_default)
            ):
                workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                workflow_run.current_step = "chapter_first_repair_budget_exhausted"
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "requires_human_review": True,
                    "final_verdict": "rewrite",
                    "chapter_first_repair_budget_exhausted": True,
                    "chapter_auto_repair_attempts": auto_repair_attempts,
                    "chapter_first_full_regeneration_count": int(
                        (chapter.metadata_json or {}).get(
                            "chapter_first_full_regeneration_count", 0
                        )
                        or 0
                    ),
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                }
                await session.flush()
                return ChapterPipelineResult(
                    workflow_run_id=workflow_run_id,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    chapter_number=loaded_chapter_number,
                    scene_results=scene_results,
                    chapter_draft_id=chapter_draft.id,
                    chapter_draft_version_no=chapter_draft.version_no,
                    final_verdict="rewrite",
                    export_artifact_id=None,
                    output_path=None,
                    requires_human_review=True,
                )

        # L2 per-chapter bible validation: detect stance flips lacking
        # a turning-point arc beat and deceased speakers; log findings on
        # the step output so the regen_loop can consume them on the next
        # scene pass.
        bible_findings: dict[str, int] | None = None
        try:
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
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "blocked"
                    scene_requires_human_review = True
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "blocked_by_l2_bible_gate": True,
                        "bible_gate_violations": [
                            {
                                "check_type": getattr(v, "check_type", ""),
                                "severity": getattr(v, "severity", ""),
                                "message": getattr(v, "message", ""),
                                "evidence": getattr(v, "evidence", ""),
                            }
                            for v in _bible_result.violations[:10]
                        ],
                    }
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

        if (
            getattr(settings.pipeline, "enable_fanqie_long_ranking_gate", False)
            and not retained_review_matches(chapter_draft)
            and (
                not resume_existing_draft_only
                or getattr(settings.pipeline, "fanqie_long_ranking_block_on_failure", False)
            )
        ):
            try:
                fanqie_gate_payload = await _run_fanqie_long_gate_for_chapter(
                    session,
                    settings=settings,
                    workflow_run_id=workflow_run.id,
                    project=project,
                    project_slug=project_slug,
                    chapter_number=chapter_number,
                    chapter_draft=chapter_draft,
                    block_on_failure=bool(
                        getattr(settings.pipeline, "fanqie_long_ranking_block_on_failure", False)
                    ),
                    chapter=chapter,
                    block_attempt_cap=int(
                        getattr(
                            settings.pipeline,
                            "fanqie_long_ranking_block_max_attempts",
                            3,
                        )
                        or 3
                    ),
                )
                if fanqie_gate_payload["blocks_write"]:
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "blocked"
                    scene_requires_human_review = True
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "blocked_by_fanqie_long_ranking_gate": True,
                        "fanqie_long_ranking_gate": fanqie_gate_payload,
                    }
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name="fanqie_long_ranking_gate",
                    step_order=step_order,
                    status=WorkflowStatus.COMPLETED,
                    output_ref=fanqie_gate_payload,
                )
                step_order += 1
            except Exception:
                # A failed required review is not evidence that prose passed.
                if getattr(settings.pipeline, "fanqie_long_ranking_block_on_failure", False):
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "blocked"
                    scene_requires_human_review = True
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "blocked_by_fanqie_long_ranking_gate": True,
                        "fanqie_long_ranking_gate": {
                            "blocks_write": True,
                            "error": "review_unavailable",
                            "chapter_draft_id": str(chapter_draft.id),
                        },
                    }
                logger.warning("fanqie_long_ranking_gate review unavailable", exc_info=True)

        # ── AI-flavor gate ─────────────────────────────────────────────
        # Runs after bible_gate and before export/signing. Detects span-
        # level AI "味" and applies *only* localized fixes at the marked
        # positions. Surrounding prose is never rewritten. When the
        # post-patch score is still above the block threshold the chapter
        # is routed to machine repair — same escape hatch the rest of the
        # gates use.
        from bestseller.services.style_phase_receipts import (
            StylePhaseReceiptError, close_style_phase, completed_style_phase,
            draft_application_binding, style_policy_fingerprint, successful_style_rounds,
        )

        from bestseller.services.budget import ExecutionBudgetError

        ai_flavor_outcome = None
        try:
            _af_gates_cfg = get_quality_gates_config()
            if (
                _af_gates_cfg.ai_flavor.enabled
                and chapter_draft is not None
                and chapter_draft.content_md
            ):
                from bestseller.services.ai_flavor_gate import (
                    run_ai_flavor_gate,
                )

                _af_lang = getattr(project, "language", None) or "zh-CN"
                _af_output_dir = (Path(settings.output.base_dir) / project.slug).resolve()
                try:
                    _af_policy = style_policy_fingerprint(
                        settings=settings, project=project, chapter=chapter,
                        config=_af_gates_cfg.ai_flavor,
                    )
                    _af_input_binding = await draft_application_binding(session, chapter_draft)
                    _af_reuse = await completed_style_phase(
                        session, draft=chapter_draft, policy=_af_policy,
                    )
                    _af_review_reuse = retained_review_matches(chapter_draft)
                except ExecutionBudgetError:
                    raise
                except Exception as exc:
                    raise StylePhaseReceiptError("style phase identity unavailable") from exc
                _af_phase_complete = True
                _af_paid_requested = False
                _af_phase_observation = None
                flavor_evaluator = (
                    _evaluate_chapter_ai_flavor_without_edits
                    if resume_existing_draft_only or _af_reuse or _af_review_reuse else run_ai_flavor_gate
                )
                flavor_kwargs = {} if resume_existing_draft_only or _af_reuse or _af_review_reuse else {
                    "project_output_dir": _af_output_dir,
                }
                ai_flavor_outcome = flavor_evaluator(
                    chapter_number=chapter_number,
                    content_md=chapter_draft.content_md,
                    language=_af_lang,
                    config=_af_gates_cfg.ai_flavor,
                    **flavor_kwargs,
                )
                # 进门时的原始分。deslop 采纳后 ``ai_flavor_outcome`` 会被
                # ``_recheck`` 整个替换，而 recheck 的 before/after 都测在**已重写**
                # 的稿子上——于是 step 记录里 before==after，DB 上看永远是「零改善」
                # （2026-08-31 排障：我据此差点把「修复通道有效」误判成「完全失效」）。
                # 单独留一份进门分，改善幅度才可查。
                _af_entry_score = ai_flavor_outcome.before_score
                if not resume_existing_draft_only and not _af_reuse and not _af_review_reuse and ai_flavor_outcome.patched_text is not None:
                    chapter_draft = await _persist_deslop_candidate(
                        session,
                        chapter=chapter,
                        parent_draft=chapter_draft,
                        content=ai_flavor_outcome.patched_text,
                        llm_run_id=None,
                        round_no=0,
                        language=_af_lang,
                        transformation="local_patch",
                        make_current=True,
                    )
                # 去 AI 味二次清洗：gate 判 block(AI 味过多)时，先让写手做一遍
                # 定向去AI味改写再复检，而不是直接打回人工修复。span 级 patcher 只
                # 能删/换词，改不了"信息旁白/结论先行/解释规则"这类话语腔；这一步
                # 用整段重写补上。改写后分数确实降到阈值下就采用，否则照常 block。
                # 触发条件不止「block」：规则解释/对仗/结论先行这类话语腔分数低
                # (advisory) 却恰是 deslop 专治、span patcher 改不掉的，必须也触发。
                from bestseller.services.ai_flavor_gate import needs_deslop_revise

                # 2026-08-30 死链清理：debt_metaphor_leak 检测器 2026-08-02 退役
                # （恒返回 []，见 detector._detect_debt_metaphor_leak docstring），
                # 这里原有的「命中即强制硬 block」分支与 metadata 键永不可能触发，
                # 已随触发集/引文豁免集一并拆除——留着只会误导审读。

                if not resume_existing_draft_only and not _af_reuse and not _af_review_reuse and needs_deslop_revise(ai_flavor_outcome) and getattr(
                    _af_gates_cfg.ai_flavor, "deslop_revise_enabled", True
                ):
                    _af_paid_requested = True
                    _af_phase_complete = False
                    try:
                        from bestseller.services.deslop_revise import revise_prose_deslop

                        _deslop_parent = chapter_draft
                        await session.flush()
                        _deslop_length_request = await full_flow_length.freeze_request(
                            session, settings, project=project, chapter=chapter,
                            stage="deslop", parent_draft=_deslop_parent)
                        full_flow_length.require_feasible_request(_deslop_length_request)
                        _deslop_parent_by_content = {
                            chapter_draft.content_md: chapter_draft,
                        }
                        _deslop_candidates: list[ChapterDraftVersionModel] = []

                        async def _observe_deslop_candidate(
                            observation: dict[str, object],
                        ) -> None:
                            parent = _deslop_parent_by_content.get(
                                str(observation.get("parent_content") or ""),
                                _deslop_parent,
                            )
                            candidate = await _persist_deslop_candidate(
                                session,
                                chapter=chapter,
                                parent_draft=parent,
                                content=str(observation["content"]),
                                llm_run_id=cast(UUID | None, observation.get("llm_run_id")),
                                round_no=int(cast(int, observation.get("round") or 0)),
                                language=_af_lang,
                                information_preservation=cast(dict | None, observation.get("information_preservation")),
                                length_request=_deslop_length_request,
                                settings=settings,
                            )
                            _deslop_candidates.append(candidate)
                            _deslop_parent_by_content[candidate.content_md] = candidate

                        # target_chars must anchor on the chapter CONTRACT, not
                        # the draft's own length: a padded draft (ch25: 5091 字,
                        # 43 处时刻切片) fed back as its own target means honest
                        # de-padding gets rejected as "too short" — the padding
                        # defends itself (2026-08-15, same shape as the deslop
                        # length-floor fix inside revise itself).
                        _af_target = int(getattr(chapter, "target_word_count", 0) or 0) or len(
                            chapter_draft.content_md
                        )
                        from bestseller.services.writing_profile import get_project_runtime_writing_profile, render_writer_voice_data
                        _revised = await revise_prose_deslop(
                            session,
                            settings,
                            content=chapter_draft.content_md,
                            language=_af_lang,
                            project_id=project.id,
                            workflow_run_id=workflow_run.id,
                            target_chars=_af_target,
                            rounds=2,
                            chapter_number=chapter_number,
                            observe_candidate=_observe_deslop_candidate,
                            length_request=_deslop_length_request,
                            writing_voice_data=render_writer_voice_data(get_project_runtime_writing_profile(project)),
                            # 爽点保全：deslop 是主要修订通道之一，去 AI 味
                            # 时同样会把结算段删成转述（2026-08-19 真机：
                            # 只给 chapter_rewrite 加保全后盖戳照掉）。
                            # 2026-09-10 (W3)：`chapter_draft.content_md` 就是
                            # 下面 `content=` 传进去的同一份正文——把它作为
                            # `source_body`，保全块附上带出处的原文锚点。
                            hype_preservation_block=render_hype_preservation_block(
                                chapter,
                                source_body=chapter_draft.content_md,
                                source_label="deslop 改写前的正文",
                                language=_af_lang,
                            ),
                        )
                        _af_phase_observation = getattr(_revised, "phase_status", None)
                        _af_rounds_complete = await successful_style_rounds(
                            session,
                            candidates=[
                                candidate
                                for candidate in _deslop_candidates
                                if (candidate.promotion_metadata or {})
                                .get("book_length_control_candidate", {})
                                .get("length_passed", True)
                            ],
                            workflow_run_id=workflow_run.id,
                            phase_result=_revised, input_content=_deslop_parent.content_md,
                        )
                        if _deslop_length_request is not None:
                            _af_rounds_complete = (
                                _af_rounds_complete
                                and full_flow_length.evaluate_candidate(
                                    _deslop_length_request, str(_revised)
                                )["length_passed"]
                            )
                        _selected_deslop = next(
                            (
                                candidate
                                for candidate in reversed(_deslop_candidates)
                                if candidate.content_md == _revised
                            ),
                            None,
                        )
                        _recheck = run_ai_flavor_gate(
                            chapter_number=chapter_number,
                            content_md=_revised,
                            language=_af_lang,
                            config=_af_gates_cfg.ai_flavor,
                            project_output_dir=_af_output_dir,
                        )
                        # 采纳判据：清干净了收，**没清干净但更干净了也收**
                        # （2026-08-19 定罪）。旧逻辑是 all-or-nothing：只有
                        # decision != block 才采纳，于是把 12 处命中清到 4 处
                        # 的改稿整份丢弃、用回脏原稿——「越脏的章越改不动」，
                        # 与「注水在保护自己」「算了却丢弃」同族。真机受控
                        # 实验：ch13 命中 12→4（negated_definition 5→1）却因
                        # 残留仍 block 而被扔掉，成稿 16 章 AI 味几乎零改善。
                        _af_improved = _recheck.after_score < ai_flavor_outcome.after_score
                        if _recheck.decision != "block" or _af_improved:
                            _final_text = _recheck.patched_text or _revised
                            if _final_text == chapter_draft.content_md:
                                pass  # Deterministic no-op: retain the exact current application.
                            elif (
                                _selected_deslop is not None
                                and _final_text == _selected_deslop.content_md
                            ):
                                chapter_draft = await _select_deslop_candidate(
                                    session,
                                    chapter=chapter,
                                    candidate=_selected_deslop,
                                    language=_af_lang,
                                    selected_reason="existing_deslop_quality_selection",
                                    selection_pool=_deslop_candidates,
                                    settings=settings,
                                )
                            else:
                                _postcheck_candidate = await _persist_deslop_candidate(
                                    session,
                                    chapter=chapter,
                                    parent_draft=_selected_deslop or _deslop_parent,
                                    content=_final_text,
                                    llm_run_id=None,
                                    round_no=(
                                        int((_selected_deslop.promotion_metadata or {}).get("deslop_round") or 0)
                                        if _selected_deslop is not None
                                        else 0
                                    ),
                                    make_current=True,
                                    language=_af_lang,
                                    transformation="deslop_postcheck_patch",
                                    length_request=_deslop_length_request,
                                    settings=settings,
                                )
                                if _postcheck_candidate.is_current:
                                    chapter_draft = _postcheck_candidate
                                else:
                                    _af_rounds_complete = False
                            if chapter_draft.content_md == _final_text:
                                ai_flavor_outcome = _recheck
                            logger.info(
                                "ai_flavor_gate ch%d: deslop revise %s (%.1f → %.1f)",
                                chapter_number,
                                "cleared block"
                                if _recheck.decision != "block"
                                else "kept as improvement (still blocking)",
                                _recheck.before_score,
                                _recheck.after_score,
                            )
                        _af_phase_complete = _af_rounds_complete and _recheck.decision != "block"
                    except (ExecutionBudgetError, full_flow_length.LengthControlError):
                        raise
                    except (PendingRollbackError, DBAPIError, MissingGreenlet):
                        # Candidate persistence is the source receipt for the
                        # returned body.  A DB failure must abort this unit of
                        # work; continuing would let downstream consumers use
                        # prose whose model lineage was never stored.
                        raise
                    except Exception:
                        logger.debug(
                            "ai_flavor_gate: deslop revise failed (non-fatal)",
                            exc_info=True,
                        )
                if ai_flavor_outcome.decision == "block":
                    logger.warning(
                        "ai_flavor_gate ch%d: residual score %.1f >= threshold, "
                        "routing to machine repair",
                        chapter_number,
                        ai_flavor_outcome.after_score,
                    )
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "blocked"
                    scene_requires_human_review = True
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "blocked_by_ai_flavor_gate": True,
                        "ai_flavor_before_score": ai_flavor_outcome.before_score,
                        "ai_flavor_after_score": ai_flavor_outcome.after_score,
                    }
                # A fresh deterministic pass also needs durable closure; otherwise
                # zero/early-call completions have no reusable phase boundary.
                if (
                    ai_flavor_outcome.before_score > 0
                    or ai_flavor_outcome.decision != "pass"
                    or _af_paid_requested
                    or (not resume_existing_draft_only and not _af_reuse and not _af_review_reuse)
                ):
                    _af_step = await create_workflow_step_run(
                        session,
                        workflow_run_id=workflow_run.id,
                        step_name="ai_flavor_gate",
                        step_order=step_order,
                        status=WorkflowStatus.COMPLETED,
                        output_ref={
                            "reused_style_phase_step_id": _af_reuse["step_id"] if _af_reuse else None,
                            "reused_review_report_id": retained_review_reuse["report_id"] if _af_review_reuse else None,
                            "decision": ai_flavor_outcome.decision,
                            "before_score": ai_flavor_outcome.before_score,
                            "after_score": ai_flavor_outcome.after_score,
                            "edits": len(ai_flavor_outcome.edits),
                            # 进门原始分 vs 最终分：deslop 的真实战果只能从这一对
                            # 读出来（见上方 _af_entry_score 注释）。
                            "entry_score": _af_entry_score,
                            "improvement": round(
                                _af_entry_score - ai_flavor_outcome.after_score, 1
                            ),
                        },
                    )
                    step_order += 1
                    if not resume_existing_draft_only and not _af_reuse and not _af_review_reuse:
                        try:
                            await close_style_phase(
                                session, draft=chapter_draft, policy=_af_policy,
                                step=_af_step, input_binding=_af_input_binding,
                                outcome=ai_flavor_outcome, complete=_af_phase_complete,
                                phase_evidence=(
                                    _af_phase_observation.to_dict() if _af_phase_observation else {
                                        "state": (
                                            "completed" if _af_phase_complete else "incomplete"
                                        ),
                                        "stop_reason": (
                                            "gate_evaluated" if not _af_paid_requested
                                            else "missing_phase_status"
                                        ),
                                        "attempted_rounds": 0 if not _af_paid_requested else None,
                                        "max_rounds": 2,
                                    }
                                ),
                            )
                            # Commit selected body + receipt before the next paid phase.
                            await _checkpoint_commit(session)
                        except ExecutionBudgetError:
                            raise
                        except Exception as exc:
                            raise StylePhaseReceiptError("style phase checkpoint failed") from exc
        except (ExecutionBudgetError, full_flow_length.LengthControlError):
            raise
        except (PendingRollbackError, DBAPIError, MissingGreenlet, StylePhaseReceiptError):
            raise
        except Exception:
            if resume_existing_draft_only:
                raise
            logger.debug("ai_flavor_gate failed (non-fatal)", exc_info=True)

        # ── 文采 advisory（榜单对标闭环 P2.1）──────────────────────────
        # 短篇管线已验证的 LitStyle 判官接入长篇 finalize：仅记录分数供
        # 对标回归与 dossier 展示，绝不影响章节状态或阻断成书。
        # LITSTYLE_LONGFORM_ADVISORY=0 可关（默认开，单采样、critic 档）。
        try:
            if (
                os.getenv("LITSTYLE_LONGFORM_ADVISORY", "1").strip().lower()
                not in {"0", "false", "off"}
                and chapter_draft is not None
                and chapter_draft.content_md
                and not is_english_language(getattr(project, "language", None))
                and not retained_review_matches(chapter_draft)
                and not resume_existing_draft_only
            ):
                from bestseller.services.judge_genre_context import (
                    resolve_judge_genre_context,
                )
                from bestseller.services.litstyle_prose_judge import (
                    judge_chapter_litstyle_stable,
                )

                _litstyle_result = await judge_chapter_litstyle_stable(
                    session,
                    settings,
                    project_id=project.id,
                    chapter_number=chapter_number,
                    content_md=chapter_draft.content_md,
                    genre_context=resolve_judge_genre_context(
                        genre=getattr(project, "genre", None),
                        sub_genre=getattr(project, "sub_genre", None),
                    ),
                    language="zh",
                    workflow_run_id=workflow_run.id,
                )
                if "LITSTYLE_JUDGE_UNAVAILABLE" not in _litstyle_result.top_issues:
                    await create_workflow_step_run(
                        session,
                        workflow_run_id=workflow_run.id,
                        step_name="litstyle_advisory",
                        step_order=step_order,
                        status=WorkflowStatus.COMPLETED,
                        output_ref={
                            "final_score": _litstyle_result.final_score,
                            "level": _litstyle_result.level,
                            "ai_tone_penalty": _litstyle_result.ai_tone_penalty,
                            "top_issues": list(_litstyle_result.top_issues)[:3],
                        },
                    )
                    step_order += 1
                    logger.info(
                        "litstyle_advisory ch%d: score=%s level=%s",
                        chapter_number,
                        _litstyle_result.final_score,
                        _litstyle_result.level,
                    )
        except Exception:
            logger.debug("litstyle advisory failed (non-fatal)", exc_info=True)

        # ── Anti-slop prose gates ───────────────────────────────────────
        # Structural prose checks added by the Anti-Slop Prose System:
        # anti-meta blocks chapter-boundary/design-language leaks and
        # out-of-scene endings; show-don't-tell is advisory by default.
        try:
            _prose_cfg = get_quality_gates_config().prose_quality
            if (
                chapter_draft is not None
                and chapter_draft.content_md
                and (_prose_cfg.anti_meta_enabled or _prose_cfg.in_scene_ending_enabled
                     or _prose_cfg.show_dont_tell_enabled)
            ):
                if _prose_cfg.anti_meta_enabled or _prose_cfg.in_scene_ending_enabled:
                    from bestseller.services.anti_meta_gate import (
                        check_anti_meta_gate,
                        effective_anti_meta_blockers,
                    )

                    _anti_meta = check_anti_meta_gate(
                        chapter_draft.content_md,
                        chapter_position=chapter_number,
                    )
                    _anti_report = _anti_meta.to_checker_report()
                    _anti_blocks = bool(effective_anti_meta_blockers(_anti_meta, _prose_cfg))
                    if _anti_blocks:
                        chapter.status = ChapterStatus.REVISION.value
                        chapter.production_state = "blocked"
                        scene_requires_human_review = True
                        workflow_run.metadata_json = {
                            **workflow_run.metadata_json,
                            "blocked_by_anti_meta_gate": True,
                            "anti_meta_metrics": _anti_report.metrics,
                        }
                    if _anti_report.issues:
                        await create_workflow_step_run(
                            session,
                            workflow_run_id=workflow_run.id,
                            step_name="anti_meta_gate",
                            step_order=step_order,
                            status=WorkflowStatus.COMPLETED,
                            output_ref={
                                "passed": _anti_report.passed,
                                "issues": len(_anti_report.issues),
                                "metrics": _anti_report.metrics,
                            },
                        )
                        step_order += 1
                if _prose_cfg.show_dont_tell_enabled:
                    from bestseller.services.show_dont_tell_gate import (
                        check_show_dont_tell_gate,
                    )

                    _show = check_show_dont_tell_gate(
                        chapter_draft.content_md,
                        chapter_position=chapter_number,
                    )
                    _show_report = _show.to_checker_report()
                    _show_blocks = (
                        not _show.passed and _prose_cfg.show_dont_tell_severity == "block"
                    )
                    if _show_blocks:
                        chapter.status = ChapterStatus.REVISION.value
                        chapter.production_state = "blocked"
                        scene_requires_human_review = True
                        workflow_run.metadata_json = {
                            **workflow_run.metadata_json,
                            "blocked_by_show_dont_tell_gate": True,
                            "show_dont_tell_metrics": _show_report.metrics,
                        }
                    if _show_report.issues:
                        await create_workflow_step_run(
                            session,
                            workflow_run_id=workflow_run.id,
                            step_name="show_dont_tell_gate",
                            step_order=step_order,
                            status=WorkflowStatus.COMPLETED,
                            output_ref={
                                "passed": _show_report.passed,
                                "issues": len(_show_report.issues),
                                "metrics": _show_report.metrics,
                                "blocks_write": _show_blocks,
                            },
                        )
                        step_order += 1
        except AntiMetaActionConfigError:
            raise
        except Exception:
            logger.debug("anti-slop prose gates failed (non-fatal)", exc_info=True)

        # ── World-law consistency gate (advisory only) ─────────────────
        # Prose must obey the book's derived world laws (catches "everyone can
        # fly yet drives a car"). Advanced tier: stamps warning metadata + a
        # step run but must NEVER block the chapter (WS-C policy).
        try:
            _wm_meta = getattr(project, "metadata_json", None)
            _wm_meta = _wm_meta if isinstance(_wm_meta, dict) else {}
            if chapter_draft is not None and chapter_draft.content_md and _wm_meta and not retained_review_matches(chapter_draft) and not resume_existing_draft_only:
                from bestseller.services.world_law_consistency_gate import (
                    evaluate_world_law_consistency_llm,
                )
                from bestseller.services.world_model_injection import extract_world_model

                _world_model_payload = extract_world_model(_wm_meta)
                if _world_model_payload:
                    # LLM semantic judge + deterministic tier check; degrades to the
                    # deterministic detector if the LLM is unavailable.
                    _wl_report = (
                        await evaluate_world_law_consistency_llm(
                            session,
                            settings,
                            chapter_draft.content_md,
                            chapter_position=chapter_number,
                            world_model=_world_model_payload,
                            language=getattr(project, "language", None) or "zh",
                            project_id=project.id,
                            workflow_run_id=workflow_run.id,
                        )
                    ).to_checker_report()
                    if _wl_report.issues:
                        workflow_run.metadata_json = {
                            **workflow_run.metadata_json,
                            "world_law_consistency_metrics": _wl_report.metrics,
                            "world_law_consistency_issue_codes": [
                                issue.id for issue in _wl_report.issues[:6]
                            ],
                        }
                        await create_workflow_step_run(
                            session,
                            workflow_run_id=workflow_run.id,
                            step_name="world_law_consistency_gate",
                            step_order=step_order,
                            status=WorkflowStatus.COMPLETED,
                            output_ref={
                                "passed": _wl_report.passed,
                                "issues": len(_wl_report.issues),
                                "metrics": _wl_report.metrics,
                            },
                        )
                        step_order += 1
        except Exception:
            logger.debug("world_law_consistency_gate failed (non-fatal)", exc_info=True)

        # ── Opening golden-chapter gate (ch1-3, advisory only) ──────────
        # Deterministic 黄金一章 acceptance checks on the opening chapters'
        # prose. Advanced tier: a hit stamps warning metadata and records a
        # step run but must NEVER block the chapter (WS-C policy).
        try:
            if chapter_draft is not None and chapter_draft.content_md and chapter_number <= 3:
                from bestseller.services.opening_golden_chapter_gate import (
                    check_opening_golden_chapter_gate,
                )

                _golden = check_opening_golden_chapter_gate(
                    chapter_draft.content_md,
                    chapter_position=chapter_number,
                    protagonist_name=_fanqie_gate_protagonist_name(project),
                )
                _golden_report = _golden.to_checker_report()
                if _golden_report.issues:
                    # Warn-only: never stamps ``blocked_by_*`` and never
                    # touches production_state — the metrics key is the
                    # warning signal for downstream reviewers.
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "opening_golden_chapter_metrics": _golden_report.metrics,
                        "opening_golden_chapter_issue_codes": [
                            issue.id for issue in _golden_report.issues[:6]
                        ],
                    }
                    await create_workflow_step_run(
                        session,
                        workflow_run_id=workflow_run.id,
                        step_name="opening_golden_chapter_gate",
                        step_order=step_order,
                        status=WorkflowStatus.COMPLETED,
                        output_ref={
                            "passed": _golden_report.passed,
                            "issues": len(_golden_report.issues),
                            "metrics": _golden_report.metrics,
                            "blocks_write": False,
                        },
                    )
                    step_order += 1
        except Exception:
            logger.debug("opening golden-chapter gate failed (non-fatal)", exc_info=True)

        export_blocked_reason: str | None = None

        async def _export_current_chapter_markdown() -> tuple[UUID | None, str | None]:
            nonlocal current_step_name
            nonlocal step_order
            nonlocal export_blocked_reason
            if not export_markdown:
                return None, None

            # Normalize the final word target before running terminal gates so
            # the gated bytes are the same bytes handed to the exporter.
            try:
                _trimmed = await _maybe_apply_deterministic_length_trim_before_export(
                    session,
                    settings=settings,
                    project=project,
                    chapter=chapter,
                    chapter_draft=chapter_draft,
                    chapter_number=chapter_number,
                )
                if _trimmed:
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "deterministic_length_trim_before_export": True,
                        "chapter_draft_id": str(chapter_draft.id),
                        "chapter_draft_version_no": chapter_draft.version_no,
                    }
            except Exception as exc:
                export_blocked_reason = f"terminal_length_normalization_error: {exc}"
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                chapter.metadata_json = {
                    **(chapter.metadata_json or {}),
                    "export_blocked_reason": export_blocked_reason,
                    "terminal_quality_gate_blocked": True,
                    "export_blocked_by_run_id": str(workflow_run.id),
                }
                return None, None

            # Re-run the one publication gate against the exact bytes handed to
            # the exporter. Evaluator errors are deliberately fail-closed.
            terminal_result = run_final_quality_gates(
                chapter_number=chapter_number,
                content_md=chapter_draft.content_md if chapter_draft is not None else "",
                project=project,
                settings=settings,
                chapter_metadata=chapter.metadata_json
                if isinstance(chapter.metadata_json, dict)
                else None,
            )
            if terminal_result.patched_text is not None and chapter_draft is not None:
                chapter_draft.content_md = terminal_result.patched_text
                resync_draft_word_count(chapter_draft, language=project.language or "zh-CN")
            terminal_gate_error = None
            if not terminal_result.passed:
                terminal_gate_error = "terminal_quality_gate_blocked: " + ";".join(
                    [*terminal_result.errors, *terminal_result.issues]
                )

            if terminal_gate_error is not None:
                export_blocked_reason = terminal_gate_error
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                chapter.metadata_json = {
                    **(chapter.metadata_json or {}),
                    "export_blocked_reason": terminal_gate_error,
                    "terminal_quality_gate_blocked": True,
                    "export_blocked_by_run_id": str(workflow_run.id),
                }
                workflow_run.current_step = "terminal_quality_gate_blocked"
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name="terminal_quality_gate",
                    step_order=step_order,
                    status=WorkflowStatus.MACHINE_BLOCKED,
                    output_ref={"export_blocked": terminal_gate_error, "blocks_write": True},
                )
                step_order += 1
                return None, None
            # Persist the exact post-gate bytes before the exporter reloads the
            # promoted draft in a separate query.
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                "terminal_quality_gate_content_hash": hashlib.sha256(
                    (chapter_draft.content_md or "").encode("utf-8")
                ).hexdigest(),
            }
            await session.flush()
            current_step_name = "export_chapter_markdown"
            workflow_run.current_step = current_step_name
            _emit_progress(
                progress,
                "chapter_export_started",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                },
            )
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
                # process, but they are still publication blockers. A chapter
                # cannot be reported complete when the frontend/export surface
                # would continue showing the previous artifact.
                export_blocked_reason = str(exc)
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                chapter.metadata_json = {
                    **(chapter.metadata_json or {}),
                    "export_blocked_reason": export_blocked_reason,
                    "export_blocked_by_run_id": str(workflow_run.id),
                }
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
                _emit_progress(
                    progress,
                    "chapter_export_blocked",
                    {
                        "project_slug": project_slug,
                        "chapter_number": chapter_number,
                        "reason": str(exc),
                    },
                )
                return None, None
            export_blocked_reason = None
            if chapter.metadata_json and (
                chapter.metadata_json.get("export_blocked_reason")
                or chapter.metadata_json.get("export_blocked_by_run_id")
            ):
                next_meta = dict(chapter.metadata_json)
                next_meta.pop("export_blocked_reason", None)
                next_meta.pop("export_blocked_by_run_id", None)
                chapter.metadata_json = next_meta
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
            _emit_progress(
                progress,
                "chapter_export_completed",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "export_artifact_id": str(artifact_id),
                    "output_path": artifact_output_path,
                },
            )
            return artifact_id, artifact_output_path

        if scene_requires_human_review and not use_chapter_first:
            chapter.status = ChapterStatus.REVISION.value
            export_artifact_id, output_path = await _export_current_chapter_markdown()
            workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
            workflow_run.current_step = "machine_repair_required"
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
            if getattr(chapter, "production_state", None) != "blocked":
                chapter.production_state = "ok"
            phase_d_block_reports: list[dict[str, Any]] = []
            try:
                snapshot = await extract_chapter_state_snapshot(
                    session,
                    settings,
                    project_id=project.id,
                    chapter=chapter,
                    chapter_md=chapter_draft.content_md,
                    source_chapter_draft_version_id=chapter_draft.id,
                    source_is_promoted=False,
                    workflow_run_id=workflow_run.id,
                )
                async with session.begin_nested():
                    # Phase B — classify + persist dominance history.
                    await _apply_post_chapter_phase_b(
                        session=session,
                        project=project,
                        chapter=chapter,
                        chapter_md=chapter_draft.content_md or "",
                    )
                    # Phase C — accrue interest on any outstanding debts.
                    await _apply_post_chapter_phase_c(
                        session=session,
                        project_id=project.id,
                        chapter_number=chapter.chapter_number,
                    )
                    # Phase D — run countdown / time-regression validators.
                    phase_d_reports = await _collect_phase_d_reports(
                        session=session,
                        project_id=project.id,
                        chapter_number=chapter.chapter_number,
                        snapshot=snapshot,
                    )
                    for _pd_report in phase_d_reports:
                        if not _pd_report.passed:
                            logger.warning(
                                "Phase D ch%d %s: %s",
                                chapter.chapter_number,
                                _pd_report.agent,
                                _pd_report.summary,
                            )
                        if getattr(_pd_report, "blocks_write", False):
                            phase_d_block_reports.append(_checker_report_gate_payload(_pd_report))
                    # Only the same admitted context used by Phase D may
                    # supply numerical or genre-progression assertions.
                    _current_fact_context = None
                    if snapshot is not None:
                        from bestseller.services.chapter_continuity_admission import read_phase_d_snapshot_context

                        # Monotonicity and genre checks are independent from
                        # the optional Phase D report flag. Revalidate instead
                        # of depending on that reporter's cached attributes.
                        _current_fact_context = await read_phase_d_snapshot_context(
                            session, project_id=project.id, chapter_number=chapter.chapter_number,
                            snapshot=snapshot,
                        )
                    _previous_fact_context = None
                    if _current_fact_context is not None and chapter.chapter_number > 1:
                        _previous_fact_context = await load_previous_chapter_snapshot(
                            session, project_id=project.id,
                            current_chapter_number=chapter.chapter_number,
                        )
                        if _previous_fact_context is not None:
                            _mono_warnings = validate_fact_monotonicity(
                                _current_fact_context.facts, _previous_fact_context.facts,
                            )
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
                                        (project.metadata_json or {}).get(
                                            "_pending_consistency_warnings", []
                                        )
                                        + _mono_warnings[:5]
                                    ),
                                }

                    # ── Genre-specific progression validation ──
                    try:
                        from bestseller.services.genre_consistency import (
                            get_genre_profile,
                            validate_xianxia_progression,
                        )

                        _genre = getattr(project, "genre", None) or settings.generation.genre
                        _sub_genre = (project.metadata_json or {}).get("sub_genre")
                        _gprofile = get_genre_profile(_genre, _sub_genre)
                        if _gprofile and _current_fact_context is not None:
                            _genre_warnings: list[str] = []
                            if (
                                _gprofile.progression_system == "cultivation_tiers"
                                and _previous_fact_context is not None
                            ):
                                for f in _current_fact_context.facts:
                                    _fd = f.model_dump(mode="json")
                                    if _fd.get("kind") == "level":
                                        _char = _fd.get("subject", "")
                                        _cur_val = _fd.get("value", "")
                                        # Find matching previous fact
                                        for pf in _previous_fact_context.facts:
                                            _pfd = pf.model_dump(mode="json")
                                            if (
                                                _pfd.get("kind") == "level"
                                                and _pfd.get("subject") == _char
                                            ):
                                                _genre_warnings.extend(
                                                    validate_xianxia_progression(
                                                        _char,
                                                        _cur_val,
                                                        _pfd.get("value", ""),
                                                        _gprofile.tier_names,
                                                    )
                                                )
                            if _genre_warnings:
                                logger.warning(
                                    "Genre violations ch%d: %s",
                                    chapter.chapter_number,
                                    _genre_warnings,
                                )
                                project.metadata_json = {
                                    **(project.metadata_json or {}),
                                    "_pending_consistency_warnings": (
                                        (project.metadata_json or {}).get(
                                            "_pending_consistency_warnings", []
                                        )
                                        + _genre_warnings[:3]
                                    ),
                                }
                    except Exception:
                        logger.debug("Genre consistency check failed (non-fatal)", exc_info=True)

                    # ── Book-level overused phrase tracking ──
                    try:
                        await _refresh_overused_phrase_block(session, project, settings)
                    except Exception:
                        logger.debug("Overused phrase tracking failed (non-fatal)", exc_info=True)

            except ExecutionBudgetError:
                raise
            except Exception as exc:
                logger.warning(
                    "Chapter %d hard-fact extraction failed (non-fatal): %s",
                    chapter.chapter_number,
                    exc,
                )
            # A draft-mode candidate has no adoption authority. The service
            # checks that before any paid extraction or story-bible mutation.
            try:
                from bestseller.services.story_bible import update_story_bible_from_chapter

                await update_story_bible_from_chapter(
                    session,
                    settings,
                    project=project,
                    chapter=chapter,
                    chapter_text=chapter_draft.content_md or "",
                    source_chapter_draft_version_id=chapter_draft.id,
                    workflow_run_id=workflow_run.id,
                )
            except ExecutionBudgetError:
                raise
            except Exception as exc:
                logger.warning("Chapter bible update failed: %s", type(exc).__name__)
                await _recover_session_after_nonfatal_error(session, exc)
            if phase_d_block_reports:
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                export_artifact_id, output_path = await _export_current_chapter_markdown()
                workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                workflow_run.current_step = "machine_repair_required"
                workflow_run.metadata_json = {
                    **workflow_run.metadata_json,
                    "draft_mode": True,
                    "requires_human_review": True,
                    "blocked_by_phase_d_time_gate": True,
                    "phase_d_reports": phase_d_block_reports,
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                    "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
                }
                await session.flush()
                return ChapterPipelineResult(
                    workflow_run_id=workflow_run_id,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    chapter_number=loaded_chapter_number,
                    scene_results=scene_results,
                    chapter_draft_id=chapter_draft.id,
                    chapter_draft_version_no=chapter_draft.version_no,
                    export_artifact_id=export_artifact_id,
                    output_path=str(output_path) if output_path else None,
                    requires_human_review=True,
                )
            export_artifact_id: UUID | None = None
            output_path: str | None = None
            if export_markdown:
                export_artifact_id, output_path = await _export_current_chapter_markdown()
                if export_blocked_reason:
                    workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                    workflow_run.current_step = "export_blocked"
                    workflow_run.metadata_json = {
                        **workflow_run.metadata_json,
                        "draft_mode": True,
                        "requires_human_review": True,
                        "export_blocked_reason": export_blocked_reason,
                        "chapter_draft_id": str(chapter_draft.id),
                        "chapter_draft_version_no": chapter_draft.version_no,
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
                        requires_human_review=True,
                    )
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
            if resume_existing_draft_only:
                current_review_draft = await session.scalar(
                    select(ChapterDraftVersionModel).where(
                        ChapterDraftVersionModel.project_id == project.id,
                        ChapterDraftVersionModel.chapter_id == chapter.id,
                        ChapterDraftVersionModel.is_current.is_(True),
                    ).execution_options(populate_existing=True)
                )
                if (
                    not _chapter_review_source_matches(current_review_draft, locked_review_source)
                    or not _chapter_review_source_matches(chapter_draft, locked_review_source)
                ):
                    return await _finish_unknown_chapter_review(
                        session, workflow=workflow_run, chapter=chapter,
                        draft=current_review_draft or chapter_draft,
                        reason="chapter_review_source_changed",
                    )
                chapter_draft = current_review_draft
            _emit_progress(
                progress,
                "chapter_review_started",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "iteration": chapter_review_iterations,
                },
            )
            try:
                (
                    chapter_review_result,
                    chapter_report,
                    chapter_quality,
                    chapter_rewrite_task,
                ) = await _review_chapter_with_evidence_retry(
                    session,
                    settings,
                    project_slug,
                    chapter_number,
                    workflow=workflow_run,
                    draft=chapter_draft,
                    source_binding=locked_review_source,
                    cycle_id=critic_cycle_id,
                )
            except _ChapterReviewSourceChanged as exc:
                return await _finish_unknown_chapter_review(
                    session, workflow=workflow_run, chapter=chapter,
                    draft=exc.draft or chapter_draft,
                    review_iterations=chapter_review_iterations,
                    reason="chapter_review_source_changed",
                )
            binding_code = _chapter_review_body_binding_code(chapter_report, chapter_draft)
            if resume_existing_draft_only:
                current_review_draft = await session.scalar(
                    select(ChapterDraftVersionModel).where(
                        ChapterDraftVersionModel.project_id == project.id,
                        ChapterDraftVersionModel.chapter_id == chapter.id,
                        ChapterDraftVersionModel.is_current.is_(True),
                    ).execution_options(populate_existing=True)
                )
                if not _chapter_review_source_matches(current_review_draft, locked_review_source):
                    binding_code = "REVIEW_BODY_STALE"
                elif _chapter_review_body_binding_code(chapter_report, current_review_draft):
                    binding_code = "REVIEW_BODY_STALE"
                if current_review_draft is not None:
                    chapter_draft = current_review_draft
            if (
                _chapter_critic_evidence_unknown(chapter_report)
                or chapter_review_result.verdict == "unknown"
                or binding_code
            ):
                if binding_code:
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}), "review_body_gate_code": binding_code,
                    }
                return await _finish_unknown_chapter_review(
                    session, workflow=workflow_run, chapter=chapter, draft=chapter_draft,
                    settings=settings,
                    report=chapter_report, quality=chapter_quality,
                    review_iterations=chapter_review_iterations,
                    rewrite_iterations=chapter_rewrite_iterations,
                    reason="chapter_review_source_changed" if binding_code else "critic_evidence_unknown",
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
            _emit_progress(
                progress,
                "chapter_review_completed",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "iteration": chapter_review_iterations,
                    "verdict": chapter_review_result.verdict,
                    "rewrite_task_id": (
                        str(chapter_rewrite_task.id) if chapter_rewrite_task is not None else None
                    ),
                    "quality_score": (
                        float(chapter_quality.score_overall)
                        if getattr(chapter_quality, "score_overall", None) is not None
                        else None
                    ),
                },
            )

            if resume_existing_draft_only and chapter_review_result.verdict != "pass":
                # Keep the source-bound task for a separate targeted edit. This
                # recovery invocation cannot change its own reviewed source.
                requires_human_review = True
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "review_recovery_targeted_edit_required": True,
                }
                break

            at_chapter_rewrite_limit = (
                chapter_rewrite_iterations >= settings.quality.max_chapter_revisions
            )
            safe_draft_available = _chapter_has_safe_draft_for_review_stall(
                chapter,
                chapter_draft,
            )
            chapter_review_warn_only = not _chapter_review_blocks_for_project(project, settings)
            legacy_stall_completion_requested = (
                at_chapter_rewrite_limit
                and settings.pipeline.accept_on_stall
                and chapter_review_warn_only
                and safe_draft_available
            )
            # Warn-only projects (e.g. a compressed finale) accept the best
            # available draft once the chapter critic stops producing an
            # actionable rewrite — whether the pipeline hit its rewrite limit
            # OR the review budget was exhausted (rewrite_task is None, which
            # otherwise drops straight to REVISION). Generation + review still
            # ran via the framework's own models; only the terminal hard-block
            # is relaxed for this project.
            legacy_warn_only_completion_requested = (
                chapter_review_warn_only
                and settings.pipeline.accept_on_stall
                and chapter_draft is not None
                and (at_chapter_rewrite_limit or chapter_rewrite_task is None)
            )
            if legacy_stall_completion_requested or legacy_warn_only_completion_requested:
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "chapter_quality_debt": True,
                    "chapter_quality_debt_reason": "legacy_accept_on_stall_deprecated",
                }
            # A finished retry budget is not a quality verdict.  Keep the
            # deprecated switch as a closure/debt signal only; it must never
            # select the last chapter draft for completion, export or Canon.
            accept_chapter_on_stall = False
            if (
                chapter_number % int(settings.pipeline.consistency_check_interval or 20) == 0
                and chapter_review_result.verdict == "pass"
            ):
                try:
                    from bestseller.services.milestone_consistency_gate import (
                        evaluate_milestone_consistency,
                    )

                    _ms = evaluate_milestone_consistency(
                        chapter_position=chapter_number,
                        consistency_verdict=str(
                            (workflow_run.metadata_json or {}).get("project_consistency_verdict")
                            or "pass"
                        ),
                        interval=int(settings.pipeline.consistency_check_interval or 20),
                    )
                    if _ms.blocking and _ms.findings:
                        milestone_repair_item = {
                            "source_audit": f"milestone-ch-{chapter_number:03d}",
                            "issue_type": "consistency_audit",
                            "affected_chapter": chapter_number,
                            "description": "; ".join(finding.detail for finding in _ms.findings),
                        }
                        chapter.metadata_json = {
                            **(chapter.metadata_json or {}),
                            "milestone_consistency_blocked": True,
                            "milestone_consistency_codes": [f.code for f in _ms.findings],
                            "milestone_repair_items": [milestone_repair_item],
                        }
                        chapter.production_state = "blocked"
                        requires_human_review = True
                        # PostgreSQL is canonical. Mode B projects project this
                        # committed intent into progress.yaml only after the DB
                        # transaction succeeds (see mode_b_chapter_bridge.py).
                        if bool((getattr(project, "metadata_json", None) or {}).get("mode_b")):
                            workflow_run.metadata_json = {
                                **(workflow_run.metadata_json or {}),
                                "mode_b_repair_projection_pending": True,
                                "mode_b_repair_projection_chapter": chapter_number,
                            }
                except Exception:
                    logger.debug(
                        "milestone consistency gate failed ch%d",
                        chapter_number,
                        exc_info=True,
                    )

            chapter_promoted = False
            chapter_source_mode: str | None = None
            _review_body_gate_code = None
            if chapter_review_result.verdict == "pass" and chapter_draft is not None:
                _review_body_gate_code = _chapter_review_body_binding_code(
                    chapter_report,
                    chapter_draft,
                )
                if _review_body_gate_code:
                    chapter_source_mode = _review_body_gate_code
                    requires_human_review = True
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "blocked"
                    workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                    workflow_run.current_step = "chapter_review_body_unverified"
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "requires_human_review": True,
                        "review_body_gate_code": _review_body_gate_code,
                        "review_body_gate_draft_id": str(chapter_draft.id),
                    }
            if (
                chapter_review_result.verdict == "pass"
                and chapter_draft is not None
                and _review_body_gate_code is None
            ):
                try:
                    chapter_promoted, chapter_source_mode = await _promote_reviewed_chapter_draft(
                        session,
                        settings=settings,
                        project=project,
                        chapter=chapter,
                        draft=chapter_draft,
                        quality=chapter_quality,
                        workflow_run_id=workflow_run_id,
                    )
                    if chapter_source_mode == "story_effect_realization_blocked":
                        requires_human_review = True
                        chapter.status = ChapterStatus.REVISION.value
                        chapter.production_state = "blocked"
                        workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                        workflow_run.current_step = "story_effect_realization_blocked"
                        workflow_run.metadata_json = {
                            **(workflow_run.metadata_json or {}),
                            "effect_realization_blocked": True,
                            "effect_realization_draft_id": str(chapter_draft.id),
                        }
                except StoryEngineReceiptRejected as exc:
                    block_codes = tuple(exc.blocking_codes or ("STORY_ENGINE_RECEIPT_REJECTED",))
                    chapter_source_mode = "story_engine_receipt_rejected"
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "story_engine_receipt_rejected": True,
                        "story_engine_receipt_block_codes": list(block_codes),
                        "auto_repair_block_codes": list(block_codes),
                        "requires_full_chapter_regeneration": True,
                    }
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "story_engine_receipt_rejected": True,
                        "story_engine_receipt_block_codes": list(block_codes),
                        "story_engine_receipt_chapter": chapter_number,
                        "story_engine_receipt_draft_id": str(chapter_draft.id),
                    }
                    await create_workflow_step_run(
                        session,
                        workflow_run_id=workflow_run.id,
                        step_name=f"story_engine_receipt_rejected_v{chapter_review_iterations}",
                        step_order=step_order,
                        status=WorkflowStatus.FAILED,
                        input_ref={
                            "chapter_id": str(chapter.id),
                            "chapter_draft_id": str(chapter_draft.id),
                        },
                        output_ref={"blocking_codes": list(block_codes)},
                        error_message=str(exc),
                    )
                    step_order += 1
                    full_regen_reason = None
                    if use_chapter_first and not resume_existing_draft_only:
                        full_regen_reason = _chapter_first_full_regeneration_reason(
                            project,
                            chapter,
                            chapter_draft,
                            block_codes,
                            attempt_number=chapter_rewrite_iterations + 1,
                        )
                    if (
                        full_regen_reason
                        and chapter_rewrite_iterations < settings.quality.max_chapter_revisions
                    ):
                        chapter_rewrite_iterations += 1
                        current_step_name = (
                            f"story_engine_full_chapter_regeneration_{chapter_rewrite_iterations}"
                        )
                        workflow_run.current_step = current_step_name
                        chapter.metadata_json = {
                            **(chapter.metadata_json or {}),
                            "chapter_first_full_regeneration_instead_of_patch": True,
                            "chapter_first_full_regeneration_reason": full_regen_reason,
                            "chapter_first_full_regeneration_count": int(
                                (chapter.metadata_json or {}).get(
                                    "chapter_first_full_regeneration_count", 0
                                )
                                or 0
                            )
                            + 1,
                        }
                        chapter_draft = await generate_chapter_draft_once(
                            session,
                            project_slug,
                            chapter_number,
                            settings=settings,
                            workflow_run_id=workflow_run.id,
                            context_packet=chapter_first_context_packet,
                            regeneration_feedback=build_regeneration_feedback(project, chapter, chapter_draft, block_codes, full_regen_reason),
                        )
                        await create_workflow_step_run(
                            session,
                            workflow_run_id=workflow_run.id,
                            step_name=current_step_name,
                            step_order=step_order,
                            status=WorkflowStatus.COMPLETED,
                            input_ref={
                                "blocking_codes": list(block_codes),
                                "rejected_draft_id": str(
                                    workflow_run.metadata_json["story_engine_receipt_draft_id"]
                                ),
                            },
                            output_ref={
                                "chapter_draft_id": str(chapter_draft.id),
                                "chapter_draft_version_no": chapter_draft.version_no,
                                "generation_mode": ("story_engine_full_chapter_regeneration"),
                            },
                        )
                        step_order += 1
                        continue
                    requires_human_review = True
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "blocked"
                    workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                    workflow_run.current_step = "story_engine_receipt_machine_blocked"
                    workflow_run.error_message = str(exc)
                    break
                except (ValueError, RuntimeError) as exc:
                    logger.warning(
                        "Chapter %d promotion evidence was not eligible: %s",
                        chapter_number,
                        exc,
                    )
                    chapter_source_mode = "chapter_promotion_ineligible"
                    debt_receipt = None
                    if (isinstance(exc, DraftQualityIneligibleError)
                            and exc.draft_id == chapter_draft.id
                            and exc.quality_score_id == chapter_quality.id):
                        debt_receipt = numeric_promotion_debt_receipt(
                            chapter_draft, chapter_quality, chapter_report
                        )
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "numeric_promotion_debt": debt_receipt,
                        "blocked_by_chapter_promotion_evidence_gate": debt_receipt is None,
                    }
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "numeric_promotion_debt": None,
                        "blocked_by_chapter_promotion_evidence_gate": debt_receipt is None,
                    }
                    if debt_receipt is None:
                        # Unknown promotion failures cannot borrow an older local
                        # flag to advance. Keep their current error structural.
                        requires_human_review = True
                    if _pipeline_quality_mode(settings) == "strict":
                        requires_human_review = True
                    else:
                        workflow_run.metadata_json = {
                            **(workflow_run.metadata_json or {}),
                            "chapter_quality_debt": True,
                            "chapter_quality_debt_reason": chapter_source_mode,
                        }

            # Publish adopted knowledge only after pre-adoption continuity
            # checks. Exact preview extraction bytes are reused by continuity.
            _snapshot_row = None
            try:
                _snapshot_row = await extract_chapter_state_snapshot(
                    session,
                    settings,
                    project_id=project_id,
                    chapter=chapter,
                    chapter_md=chapter_draft.content_md,
                    source_chapter_draft_version_id=chapter_draft.id,
                    source_is_promoted=chapter_promoted,
                    workflow_run_id=workflow_run_id,
                )
            except ExecutionBudgetError:
                raise
            except Exception:
                logger.warning(
                    "chapter %d: state snapshot extraction failed (non-fatal)",
                    chapter_number,
                    exc_info=True,
                )

            effect_realization_blocked = (
                chapter_source_mode == "story_effect_realization_blocked"
                or bool(
                    (getattr(chapter, "metadata_json", None) or {}).get(
                        "effect_realization_blocked"
                    )
                )
            )
            if (chapter_review_result.verdict == "pass" and chapter_promoted) or (
                accept_chapter_on_stall and not effect_realization_blocked
            ):
                if accept_chapter_on_stall:
                    reached_chapter_revision_limit = True
                    logger.info(
                        "Chapter %d accepting best draft on warn-only/stall "
                        "(iter=%d, verdict=%s, production_state=%s)",
                        chapter_number,
                        chapter_rewrite_iterations,
                        chapter_review_result.verdict,
                        getattr(chapter, "production_state", None),
                    )
                chapter.status = ChapterStatus.COMPLETE.value
                if chapter_promoted:
                    chapter.production_state = "ok"
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "promotion_state": getattr(chapter_draft, "promotion_state", None),
                        "promotion_source_mode": chapter_source_mode,
                    }
                if accept_chapter_on_stall:
                    # Warn-only acceptance: clear any stall production_state
                    # ("repair_exhausted"/"blocked") so downstream export and
                    # dashboards treat this chapter as a completed draft.
                    chapter.production_state = "ok"
                # Extract hard-fact snapshot for cross-chapter continuity.
                # Failures are logged and swallowed — continuity is a quality
                # enhancement, not a hard dependency for chapter completion.
                # Wrap in a SAVEPOINT so an internal DB error (e.g. missing
                # table, constraint violation) does not poison the outer
                # transaction shared across the rest of the chapter loop.
                try:
                    async with session.begin_nested():
                        # 快照已在进入本分支前提取（见上），这里直接复用。
                        # Phase B — classify + persist dominance history.
                        await _apply_post_chapter_phase_b(
                            session=session,
                            project=project,
                            chapter=chapter,
                            chapter_md=chapter_draft.content_md or "",
                        )
                        # Phase C — accrue interest on outstanding debts.
                        await _apply_post_chapter_phase_c(
                            session=session,
                            project_id=project_id,
                            chapter_number=loaded_chapter_number,
                        )
                        # Phase D has already validated the candidate before
                        # adoption; these are post-adoption B/C effects only.
                except Exception as exc:
                    logger.warning(
                        "Chapter %d hard-fact extraction failed (non-fatal): %s",
                        loaded_chapter_number,
                        exc,
                    )
                    await _recover_session_after_nonfatal_error(session, exc)

                # ── Post-chapter feedback extraction (1 LLM call) ──
                await _extract_chapter_knowledge_if_enabled(
                    session,
                    settings,
                    project_id=project_id,
                    chapter=chapter,
                    chapter_md=chapter_draft.content_md,
                    workflow_run_id=workflow_run_id,
                    source_chapter_draft_version_id=chapter_draft.id,
                )

                # ── Dynamic world-state ripple (case law) ──
                # When the chapter touches a state variable's change triggers,
                # advance that variable's current_value so later chapters read an
                # up-to-date world state. Non-fatal; additive to metadata.
                try:
                    from bestseller.services.world_ripple import apply_world_state_ripples

                    async with session.begin_nested():
                        await apply_world_state_ripples(
                            session,
                            project,
                            chapter_number=getattr(chapter, "chapter_number", chapter_number),
                            chapter_text=chapter_draft.content_md or "",
                        )
                except Exception as exc:
                    logger.warning(
                        "Chapter %d world-state ripple failed (non-fatal): %s",
                        loaded_chapter_number,
                        exc,
                    )
                    await _recover_session_after_nonfatal_error(session, exc)

                # ── Living Story Bible update (non-draft path) ──
                try:
                    from bestseller.services.story_bible import update_story_bible_from_chapter

                    await update_story_bible_from_chapter(
                        session,
                        settings,
                        project=project,
                        chapter=chapter,
                        chapter_text=chapter_draft.content_md or "",
                        source_chapter_draft_version_id=chapter_draft.id,
                        workflow_run_id=workflow_run_id,
                    )
                except ExecutionBudgetError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Chapter %d bible update failed (non-fatal): %s",
                        loaded_chapter_number,
                        exc,
                    )
                    await _recover_session_after_nonfatal_error(session, exc)

                # ── Book-level overused phrase tracking (non-draft path) ──
                try:
                    await _refresh_overused_phrase_block(session, project, settings)
                except Exception as exc:
                    logger.debug(
                        "Overused phrase tracking failed (non-fatal)",
                        exc_info=True,
                    )
                    await _recover_session_after_nonfatal_error(session, exc)

                # ── L7 per-chapter audit (lightweight) ──
                # Runs PleasureDistributionAudit + SetupPayoffTrackerAudit
                # filtered to findings on the current chapter, then promotes
                # PLEASURE_SETUP_PAYOFF_DEBT to a pending RewriteTask so the
                # review loop compensates in a later chapter rather than
                # waiting for the book-end audit. Failures non-fatal.
                try:
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
                                project_id=project_id,
                                audit=build_per_chapter_audit(),
                                chapter_number=loaded_chapter_number,
                            )
                            _rewrites_created = await spawn_rewrite_tasks_from_findings(
                                session, _audit_report
                            )
                            if _rewrites_created:
                                logger.info(
                                    "Chapter %d L7 audit spawned %d rewrite task(s)",
                                    loaded_chapter_number,
                                    _rewrites_created,
                                )
                except Exception as exc:
                    logger.debug(
                        "Chapter %d L7 per-chapter audit failed (non-fatal)",
                        loaded_chapter_number,
                        exc_info=True,
                    )
                    await _recover_session_after_nonfatal_error(session, exc)

                # ── L8 per-chapter scorecard refresh ──
                # Upserts NovelScorecardModel so dashboards see post-chapter
                # quality scores without waiting for book-end Stage 11.
                # Idempotent; failures non-fatal.
                try:
                    _l8_cfg = get_quality_gates_config().l8
                    if _l8_cfg.enabled:
                        from bestseller.services.scorecard import (
                            update_scorecard_incrementally,
                        )

                        async with session.begin_nested():
                            await update_scorecard_incrementally(
                                session,
                                project_id=project_id,
                                chapter_number=loaded_chapter_number,
                                expected_chapter_count=project_target_chapters,
                            )
                except Exception as exc:
                    logger.debug(
                        "Chapter %d L8 per-chapter scorecard failed (non-fatal)",
                        loaded_chapter_number,
                        exc_info=True,
                    )
                    await _recover_session_after_nonfatal_error(session, exc)
                break

            if chapter_rewrite_task is None:
                if requires_human_review:
                    break
                if _pipeline_quality_mode(settings) == "closure":
                    # Real books exit here, not through the auto-repair
                    # exhaustion branch: closure mode accepts the chapter with
                    # debt. Promote the best-scoring attempt before stamping the
                    # debt, otherwise the chapter ships whichever rewrite ran
                    # last — observed on a live run where chapter 1 degraded
                    # 1860 -> 1599 -> 1570 words across three attempts.
                    if use_chapter_first and chapter_draft is not None and not resume_existing_draft_only:
                        chapter_draft = await _promote_best_scoring_chapter_draft_on_stall(
                            session,
                            chapter=chapter,
                            current_draft=chapter_draft,
                            project=project,
                            settings=settings,
                        )
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "quality_debt"
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "chapter_quality_debt": True,
                        "chapter_quality_debt_reason": (
                            chapter_source_mode or "chapter_review_without_promotion"
                        ),
                    }
                    break
                requires_human_review = True
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                workflow_run.current_step = "machine_repair_required"
                workflow_run.metadata_json = {
                    **workflow_run.metadata_json,
                    "requires_human_review": True,
                    "blocked_after_chapter_review_without_rewrite_task": True,
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                    "final_verdict": chapter_review_result.verdict,
                    "review_report_id": str(chapter_report.id),
                    "quality_score_id": str(chapter_quality.id),
                }
                break

            if chapter_rewrite_task.status == "failed":
                from bestseller.services.chapter_rewrite_outcome import retain_failed_chapter_rewrite_outcome

                await retain_failed_chapter_rewrite_outcome(
                    session, workflow=workflow_run, chapter=chapter,
                    current_draft=chapter_draft, rewrite_task=chapter_rewrite_task,
                )
                requires_human_review = True
                await _checkpoint_commit(session)
                break

            if at_chapter_rewrite_limit:
                # Either accept_on_stall is disabled or chapter review is
                # configured as a hard quality gate. Do not mark a rejected
                # chapter complete after exhausting rewrites.
                reached_chapter_revision_limit = True
                if _pipeline_quality_mode(settings) == "closure":
                    # Rewrites are exhausted — the canonical best-of-N moment.
                    # Without this the chapter ships attempt N even when an
                    # earlier attempt scored higher, which is the scene-level
                    # bug fixed in 2026-07-13 reappearing at chapter scope.
                    if use_chapter_first and chapter_draft is not None and not resume_existing_draft_only:
                        chapter_draft = await _promote_best_scoring_chapter_draft_on_stall(
                            session,
                            chapter=chapter,
                            current_draft=chapter_draft,
                            project=project,
                            settings=settings,
                        )
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.production_state = "quality_debt"
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "chapter_quality_debt": True,
                        "chapter_quality_debt_reason": "chapter_rewrite_revision_limit",
                        "chapter_draft_id": str(chapter_draft.id),
                        "chapter_draft_version_no": chapter_draft.version_no,
                    }
                    break
                requires_human_review = True
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                workflow_run.current_step = "machine_repair_required"
                workflow_run.metadata_json = {
                    **workflow_run.metadata_json,
                    "requires_human_review": True,
                    "blocked_after_chapter_rewrite_limit": True,
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                    "final_verdict": chapter_review_result.verdict,
                    "review_report_id": str(chapter_report.id),
                    "quality_score_id": str(chapter_quality.id),
                    "rewrite_task_id": str(chapter_rewrite_task.id),
                }
                break

            review_full_regen_reason = None
            if use_chapter_first and not resume_existing_draft_only:
                review_full_regen_reason = _chapter_review_full_regeneration_reason(
                    project,
                    chapter,
                    chapter_draft,
                    chapter_report,
                    chapter_quality,
                    rewrite_iterations=chapter_rewrite_iterations,
                )

            chapter_rewrite_iterations += 1
            if review_full_regen_reason:
                current_step_name = f"chapter_first_review_regenerate_{chapter_rewrite_iterations}"
                workflow_run.current_step = current_step_name
                if chapter_rewrite_task is not None:
                    from bestseller.services.review_source_ordering import archived_rewrite_task_ids, CODE
                    archived = await archived_rewrite_task_ids(session, project_id=project.id, chapter_id=chapter.id)
                    if chapter_rewrite_task.id in archived:
                        raise ValueError(CODE)
                    chapter_rewrite_task.status = "superseded"
                    chapter_rewrite_task.metadata_json = {
                        **(chapter_rewrite_task.metadata_json or {}),
                        "superseded_reason": (
                            "chapter_review_full_regeneration_instead_of_rewrite"
                        ),
                        "full_regeneration_reason": review_full_regen_reason,
                    }
                chapter.metadata_json = {
                    **(chapter.metadata_json or {}),
                    "chapter_review_full_regeneration_instead_of_rewrite": True,
                    "chapter_review_full_regeneration_reason": review_full_regen_reason,
                    "chapter_review_full_regeneration_iteration": chapter_rewrite_iterations,
                    "chapter_first_full_regeneration_count": int(
                        (chapter.metadata_json or {}).get(
                            "chapter_first_full_regeneration_count", 0
                        )
                        or 0
                    )
                    + 1,
                }
                _emit_progress(
                    progress,
                    "chapter_review_regenerating_full_chapter",
                    {
                        "project_slug": project_slug,
                        "chapter_number": chapter_number,
                        "iteration": chapter_rewrite_iterations,
                        "rewrite_task_id": str(chapter_rewrite_task.id),
                        "reason": review_full_regen_reason,
                    },
                )
                chapter_draft = await generate_chapter_draft_once(
                    session,
                    project_slug,
                    chapter_number,
                    settings=settings,
                    workflow_run_id=workflow_run.id,
                    context_packet=chapter_first_context_packet,
                    regeneration_feedback=build_regeneration_feedback(project, chapter, chapter_draft, block_codes, review_full_regen_reason),
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
                        "generation_mode": "chapter_first_review_full_regeneration",
                        "full_regeneration_reason": review_full_regen_reason,
                    },
                )
                step_order += 1
                _emit_progress(
                    progress,
                    "chapter_rewrite_completed",
                    {
                        "project_slug": project_slug,
                        "chapter_number": chapter_number,
                        "iteration": chapter_rewrite_iterations,
                        "rewrite_task_id": str(chapter_rewrite_task.id),
                        "chapter_draft_id": str(chapter_draft.id),
                        "chapter_draft_version_no": chapter_draft.version_no,
                        "generation_mode": "chapter_first_review_full_regeneration",
                        "full_regeneration_reason": review_full_regen_reason,
                        "word_count": int(getattr(chapter_draft, "word_count", 0) or 0),
                    },
                )
                continue

            current_step_name = f"rewrite_chapter_v{chapter_rewrite_iterations}"
            workflow_run.current_step = current_step_name
            _emit_progress(
                progress,
                "chapter_rewrite_started",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "iteration": chapter_rewrite_iterations,
                    "rewrite_task_id": str(chapter_rewrite_task.id),
                },
            )
            chapter_draft, chapter_rewrite_task = await rewrite_chapter_from_task(
                session,
                project_slug,
                chapter_number,
                rewrite_task_id=chapter_rewrite_task.id,
                settings=settings,
                workflow_run_id=workflow_run.id,
            )
            await _record_chapter_rewrite_outcome(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                chapter_draft=chapter_draft,
                rewrite_task=chapter_rewrite_task,
                project_slug=project_slug,
                chapter_number=chapter_number,
                iteration=chapter_rewrite_iterations,
                progress=progress,
            )
            from bestseller.services.chapter_rewrite_outcome import retain_failed_chapter_rewrite_outcome

            rejected_rewrite_outcome = await retain_failed_chapter_rewrite_outcome(
                session, workflow=workflow_run, chapter=chapter,
                current_draft=chapter_draft, rewrite_task=chapter_rewrite_task,
            )
            step_order += 1
            await _checkpoint_commit(session)
            if rejected_rewrite_outcome is not None:
                requires_human_review = True
                break

        if getattr(chapter, "production_state", None) == "blocked":
            requires_human_review = True
            chapter.status = ChapterStatus.REVISION.value
            workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
            workflow_run.current_step = "machine_repair_required"
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "requires_human_review": True,
                "blocked_after_chapter_rewrite_quality_gate": True,
                "chapter_draft_id": str(chapter_draft.id),
                "chapter_draft_version_no": chapter_draft.version_no,
            }

        if requires_human_review:
            _record_chapter_adoption_outcome(
                workflow=workflow_run, chapter=chapter, draft=chapter_draft, numeric_receipt=None,
            )
            chapter.status = ChapterStatus.REVISION.value
            chapter.production_state = "blocked"
            workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
            critic_unknown = bool((workflow_run.metadata_json or {}).get("chapter_critic_evidence_unknown"))
            workflow_run.current_step = "critic_evidence_unknown" if critic_unknown else "machine_repair_required"
            if critic_unknown:
                export_artifact_id, output_path = None, None
            else:
                export_artifact_id, output_path = await _export_current_chapter_markdown()
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "requires_human_review": True,
                "chapter_review_iterations": chapter_review_iterations,
                "chapter_rewrite_iterations": chapter_rewrite_iterations,
                "reached_chapter_revision_limit": reached_chapter_revision_limit,
                "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
                "final_verdict": chapter_review_result.verdict if chapter_review_result else None,
            }
            await session.flush()
            if resume_existing_draft_only:
                await _checkpoint_commit(session)
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

        # Closure can retain a quarantined candidate for diagnostics and later
        # repair, but it must return before any export path.  This is the
        # chapter-level counterpart to scene quality debt.
        if (
            isinstance(chapter_quality, QualityScoreModel)
            and getattr(chapter_draft, "promotion_state", None)
            != DraftPromotionState.PROMOTED.value
        ):
            # Same reasoning as the closure branch above: the retained candidate
            # must be the best attempt, not the most recent one.
            if use_chapter_first and chapter_draft is not None and not resume_existing_draft_only:
                chapter_draft = await _promote_best_scoring_chapter_draft_on_stall(
                    session,
                    chapter=chapter,
                    current_draft=chapter_draft,
                    project=project,
                    settings=settings,
                )
            chapter.status = ChapterStatus.REVISION.value
            chapter.production_state = "quality_debt"
            # Persist a DURABLE chapter-level debt marker. production_state can be
            # flipped back to "blocked" by a later quality-gate re-run (e.g. a
            # retroactive reassembly), which would erase the only signal that this
            # chapter already shipped its accepted best attempt — causing the
            # repair sweep to re-select it every self-heal cycle and starve
            # forward writing. A metadata flag on the chapter survives re-blocking.
            _numeric_debt = await _persist_chapter_numeric_adoption(
                session, chapter=chapter, draft=chapter_draft, quality=chapter_quality,
                report=chapter_report, workflow=workflow_run, settings=settings,
            )
            # Adoption must be durable before the source guard can admit it.
            # A candidate with only a quality_debt flag remains quarantined.
            if _numeric_debt and chapter_draft is not None:
                await extract_chapter_state_snapshot(
                    session,
                    settings,
                    project_id=project_id,
                    chapter=chapter,
                    chapter_md=chapter_draft.content_md,
                    source_chapter_draft_version_id=chapter_draft.id,
                    source_is_promoted=False,
                    workflow_run_id=workflow_run_id,
                )
                await _extract_chapter_knowledge_if_enabled(
                    session,
                    settings,
                    project_id=project_id,
                    chapter=chapter,
                    chapter_md=chapter_draft.content_md,
                    source_chapter_draft_version_id=chapter_draft.id,
                    workflow_run_id=workflow_run_id,
                )
            adopted = _record_chapter_adoption_outcome(
                workflow=workflow_run, chapter=chapter, draft=chapter_draft,
                numeric_receipt=_numeric_debt,
            )
            if adopted:
                workflow_run.status = WorkflowStatus.COMPLETED.value
                workflow_run.current_step = "completed_with_quality_debt"
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "requires_human_review": True,
                "chapter_quality_debt": True,
                "chapter_quality_debt_reason": (
                    (workflow_run.metadata_json or {}).get("chapter_quality_debt_reason")
                    or "chapter_not_promoted"
                ),
                "chapter_draft_id": str(chapter_draft.id),
                "chapter_draft_version_no": chapter_draft.version_no,
                "promotion_state": getattr(chapter_draft, "promotion_state", None),
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
                quality_score_id=chapter_quality.id,
                rewrite_task_id=(
                    chapter_rewrite_task.id if chapter_rewrite_task is not None else None
                ),
                chapter_review_iterations=chapter_review_iterations,
                chapter_rewrite_iterations=chapter_rewrite_iterations,
                requires_human_review=True,
            )

        export_artifact_id: UUID | None = None
        output_path: str | None = None
        if export_markdown:
            export_artifact_id, output_path = await _export_current_chapter_markdown()
            if export_blocked_reason:
                requires_human_review = True
                workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                workflow_run.current_step = "export_blocked"
                workflow_run.metadata_json = {
                    **workflow_run.metadata_json,
                    "requires_human_review": True,
                    "export_blocked_reason": export_blocked_reason,
                    "chapter_draft_id": str(chapter_draft.id),
                    "chapter_draft_version_no": chapter_draft.version_no,
                    "chapter_review_iterations": chapter_review_iterations,
                    "chapter_rewrite_iterations": chapter_rewrite_iterations,
                    "final_verdict": chapter_review_result.verdict
                    if chapter_review_result is not None
                    else None,
                    "review_report_id": str(chapter_report.id)
                    if chapter_report is not None
                    else None,
                    "quality_score_id": str(chapter_quality.id)
                    if chapter_quality is not None
                    else None,
                    "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
                }
                await session.flush()
                return ChapterPipelineResult(
                    workflow_run_id=workflow_run_id,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    chapter_number=loaded_chapter_number,
                    scene_results=scene_results,
                    chapter_draft_id=chapter_draft.id,
                    chapter_draft_version_no=chapter_draft.version_no,
                    final_verdict=chapter_review_result.verdict
                    if chapter_review_result is not None
                    else None,
                    review_report_id=chapter_report.id if chapter_report is not None else None,
                    quality_score_id=chapter_quality.id if chapter_quality is not None else None,
                    rewrite_task_id=chapter_rewrite_task.id
                    if chapter_rewrite_task is not None
                    else None,
                    chapter_review_iterations=chapter_review_iterations,
                    chapter_rewrite_iterations=chapter_rewrite_iterations,
                    export_artifact_id=export_artifact_id,
                    output_path=output_path,
                    requires_human_review=True,
                )
        if getattr(chapter, "production_state", None) not in {"blocked", "quality_debt"}:
            chapter.production_state = "ok"
            chapter_meta = dict(chapter.metadata_json or {})
            if chapter_meta.get("auto_repair_exhausted") or chapter_meta.get(
                "auto_repair_in_progress"
            ):
                chapter_meta.pop("auto_repair_exhausted", None)
                chapter_meta.pop("auto_repair_in_progress", None)
                if auto_repair_attempts > 0:
                    chapter_meta["auto_repair_last_successful_attempts"] = auto_repair_attempts
                chapter.metadata_json = chapter_meta

        adopted = _record_chapter_adoption_outcome(
            workflow=workflow_run, chapter=chapter, draft=chapter_draft, numeric_receipt=None,
        )
        if adopted:
            workflow_run.status = WorkflowStatus.COMPLETED.value
            workflow_run.current_step = "completed"
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "requires_human_review": not adopted,
            "chapter_draft_id": str(chapter_draft.id),
            "chapter_draft_version_no": chapter_draft.version_no,
            "chapter_review_iterations": chapter_review_iterations,
            "chapter_rewrite_iterations": chapter_rewrite_iterations,
            "final_verdict": chapter_review_result.verdict
            if chapter_review_result is not None
            else None,
            "review_report_id": str(chapter_report.id) if chapter_report is not None else None,
            "quality_score_id": str(chapter_quality.id) if chapter_quality is not None else None,
            "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
        }
        await session.flush()

        return ChapterPipelineResult(
            workflow_run_id=workflow_run_id,
            project_id=project_id,
            chapter_id=chapter_id,
            chapter_number=loaded_chapter_number,
            scene_results=scene_results,
            chapter_draft_id=chapter_draft.id,
            chapter_draft_version_no=chapter_draft.version_no,
            final_verdict=chapter_review_result.verdict
            if chapter_review_result is not None
            else None,
            review_report_id=chapter_report.id if chapter_report is not None else None,
            quality_score_id=chapter_quality.id if chapter_quality is not None else None,
            rewrite_task_id=chapter_rewrite_task.id if chapter_rewrite_task is not None else None,
            chapter_review_iterations=chapter_review_iterations,
            chapter_rewrite_iterations=chapter_rewrite_iterations,
            export_artifact_id=export_artifact_id,
            output_path=output_path,
            requires_human_review=not adopted,
        )
    except CancelledError as exc:
        attach_workflow_failure(exc, workflow_run)
        raise
    except Exception as exc:
        attach_workflow_failure(exc,workflow_run)
        # Mirror the guard in ``run_scene_pipeline`` — any DB-level error
        # leaves the session unusable and follow-up writes explode with
        # ``MissingGreenlet``. Rollback first and let the reaper clean up.
        if _is_db_session_failure(session, exc):
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
        except (PendingRollbackError, DBAPIError, MissingGreenlet):
            await session.rollback()
        raise


async def _load_project_chapters(
    session: AsyncSession,
    project_id: UUID,
) -> list[ChapterModel]:
    return list(
        await session.scalars(
            select(ChapterModel)
            .options(selectinload(ChapterModel.scenes))
            .where(ChapterModel.project_id == project_id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )


async def _numeric_promotion_debt_binding_is_current(session, chapter_id, metadata) -> bool:
    """Revalidate a durable numeric debt against current draft and exact review."""
    receipt = (metadata or {}).get("numeric_promotion_debt")
    if not isinstance(receipt, Mapping) or receipt.get("schema") != "numeric_promotion_debt_v1":
        return False
    try:
        draft_id = UUID(str(receipt["draft_id"]))
        score_id = UUID(str(receipt["quality_score_id"]))
        report_id = UUID(str(receipt["review_report_id"]))
    except (KeyError, ValueError, TypeError):
        return False
    draft = await session.get(ChapterDraftVersionModel, draft_id)
    if draft is None or draft.chapter_id != chapter_id:
        return False
    score = await session.get(QualityScoreModel, score_id)
    report = await session.get(ReviewReportModel, report_id)
    return bool(score is not None and report is not None
                and numeric_promotion_debt_receipt(draft, score, report) == receipt)


async def _select_pending_chapters_for_resume(
    session: AsyncSession,
    chapters: list[ChapterModel],
    *,
    resume_enabled: bool,
    accept_on_stall: bool,
) -> tuple[list[ChapterModel], list[int]]:
    """Skip settled adopted bodies only after exact knowledge closure.

    Numeric debt requires the same durable adoption receipt as export.
    ``accept_on_stall`` remains compatibility input and grants no exception.
    """
    if not resume_enabled:
        return list(chapters), []

    chapter_ids = [ch.id for ch in chapters]
    drafted_ids: set[UUID] = set()
    if chapter_ids:
        drafted_rows = await session.execute(
            select(ChapterDraftVersionModel.chapter_id, ChapterDraftVersionModel.content_md).where(
                ChapterDraftVersionModel.chapter_id.in_(chapter_ids),
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
        drafted_ids = {chapter_id for chapter_id, body in drafted_rows if (body or "").strip()}

    # Verify the same adoption and knowledge used by export, rather than
    # trusting a historical complete/ok projection or a generic debt flag.
    from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
    from bestseller.services.adopted_knowledge_resume import read_adopted_knowledge

    ready: dict[UUID, bool] = {}
    for ch in chapters:
        adopted, debt_receipt = await select_adopted_chapter_source(session, chapter=ch, lock=False)
        ready[ch.id] = False
        if adopted is None or not (adopted.content_md or "").strip():
            continue
        _, knowledge = await read_adopted_knowledge(
            session, project_id=ch.project_id, chapter_id=ch.id,
            source_chapter_draft_version_id=adopted.id, chapter_md=adopted.content_md,
            require_bible=True,
        )
        ready[ch.id] = bool(knowledge["closed"] and (
            debt_receipt is not None or (
                ch.status == ChapterStatus.COMPLETE.value
                and str(ch.production_state or "").lower() in SETTLED_PRODUCTION_STATES
            )
        ))

    def _is_resume_done(ch: ChapterModel) -> bool:
        # A draft is evidence of prior work, never evidence of formal chapter
        # completion.  In particular, do not let candidate drafts on chapter
        # N or N+1 hide the earliest unfinished chapter N.
        return ready[ch.id]

    pending = [ch for ch in chapters if not _is_resume_done(ch)]
    draftless_revisions = [
        ch.chapter_number
        for ch in chapters
        if ch.status == ChapterStatus.REVISION.value and ch.id not in drafted_ids
    ]
    return pending, draftless_revisions


def _chapter_requires_sequence_stop(
    chapter: ChapterModel,
    chapter_result: ChapterPipelineResult,
    settings: AppSettings,
    *,
    has_current_draft: bool = False,
    numeric_debt_binding_verified: bool = False,
) -> bool:
    """Stop a production book before advancing past an uncleared chapter."""
    if bool(getattr(getattr(settings, "quality", None), "draft_mode", False)):
        return False
    if chapter_local_debt_allows_continuation(
        getattr(chapter, "metadata_json", None), has_current_draft=has_current_draft,
        numeric_debt_binding_verified=numeric_debt_binding_verified,
    ):
        return False
    return bool(
        chapter_result.requires_human_review
        or chapter.status != ChapterStatus.COMPLETE.value
        or str(getattr(chapter, "production_state", "") or "").strip().lower()
        not in SETTLED_PRODUCTION_STATES
    )


async def _load_prior_incomplete_chapter_numbers(
    session: AsyncSession,
    *,
    project_id: UUID,
    before_chapter_number: int,
) -> list[int]:
    """Return earlier chapters that are not safe to skip before drafting."""
    if before_chapter_number <= 1:
        return []

    rows = await session.execute(
        select(
            ChapterModel.chapter_number,
            ChapterModel.status,
            ChapterModel.production_state,
            ChapterModel.metadata_json,
            func.count(ChapterDraftVersionModel.id).label("current_draft_count"),
        )
        .outerjoin(
            ChapterDraftVersionModel,
            and_(
                ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                ChapterDraftVersionModel.is_current.is_(True),
                func.length(func.regexp_replace(
                    ChapterDraftVersionModel.content_md, r"\s", "", "g"
                )) > 0,
            ),
        )
        .where(
            ChapterModel.project_id == project_id,
            ChapterModel.chapter_number < before_chapter_number,
        )
        .group_by(
            ChapterModel.id,
            ChapterModel.chapter_number,
            ChapterModel.status,
            ChapterModel.production_state,
            ChapterModel.metadata_json,
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    incomplete: list[int] = []
    for chapter_number, status, production_state, metadata_json, current_draft_count in rows.all():
        has_current_draft = int(current_draft_count or 0) > 0
        numeric_bound = False
        if (metadata_json or {}).get("numeric_promotion_debt"):
            prior_id = await session.scalar(select(ChapterModel.id).where(
                ChapterModel.project_id == project_id,
                ChapterModel.chapter_number == chapter_number,
            ))
            numeric_bound = await _numeric_promotion_debt_binding_is_current(
                session, prior_id, metadata_json
            )
        if chapter_local_debt_allows_continuation(
            metadata_json, has_current_draft=has_current_draft,
            numeric_debt_binding_verified=numeric_bound,
        ):
            continue
        if str(status or "").strip().lower() != ChapterStatus.COMPLETE.value:
            incomplete.append(int(chapter_number))
            continue
        if not has_current_draft:
            incomplete.append(int(chapter_number))
            continue
        state = str(production_state or "").strip().lower()
        if state in SETTLED_PRODUCTION_STATES:
            # Settled means the quality system finished with this chapter and
            # chose to ship the current draft; it does not gate the next one.
            continue
        # A non-settled production state is an incomplete chapter regardless
        # of the metadata classifier. This keeps low-level callers aligned
        # with the run-project sequence guard and prevents a blocked chapter
        # with an unrecognized metadata shape from being skipped.
        incomplete.append(int(chapter_number))
    return incomplete


def _scorecard_score_for_premium_gate(scorecard: object) -> tuple[float | None, str | None]:
    """Only use a complete scorecard with current-draft evidence as a quality signal."""
    if int(getattr(scorecard, "missing_chapters", 0) or 0) > 0:
        return None, "project_in_progress_missing_chapters"
    if getattr(scorecard, "quality_report_unknown_chapters", ()):
        return None, "current_draft_quality_reports_unverified"
    return float(getattr(scorecard, "quality_score")), None


@pipeline_operation_stage
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
    allow_structural_repair: bool = False,
    chapter_first: bool | None = None,
    stop_on_chapter_failure: bool = False,
) -> ProjectPipelineResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    from bestseller.services.creation_configuration import project_settings, read_window, filter_window
    settings = project_settings(settings, project)
    execution_window = read_window(project)
    if execution_window is not None:
        from bestseller.services.execution_windows import completed_window_resume
        cached_window = await completed_window_resume(session, settings, project,
            export_markdown=export_markdown, progress=progress)
        if cached_window is not None:
            return cached_window
    if execution_window is not None:
        total_target_chapters = project.target_chapters
    requested_chapter_numbers = set(chapter_numbers) if chapter_numbers is not None else None
    if execution_window is not None:
        requested_chapter_numbers = filter_window(project,
            requested_chapter_numbers if requested_chapter_numbers is not None else execution_window.numbers())
    _assert_project_not_blocked_for_structural_repair(
        project,
        project_slug=project_slug,
        operation="project pipeline",
        allow_structural_repair=allow_structural_repair,
    )

    # Native zero-call policy compatibility/rearm before any project WF,
    # invariant/backfill/model work. Earlier worker/web admission is idempotent.
    from bestseller.services.chapter_review_reconciliation import prepare_native_review_resume
    await prepare_native_review_resume(session, settings, project_id=project.id,
        requested_by=requested_by)

    # L1 ProjectInvariants — seed once, re-use across all downstream stages.
    # Seeding must happen before any LLM call so prompt construction and
    # output validation see a coherent contract from chapter 1 onward.
    await _ensure_project_invariants(session, project, settings)

    if getattr(settings.pipeline, "require_foundation_identity_lock", True):
        await ensure_project_identity_manifest(
            session,
            project,
            project_slug=project_slug,
        )
        await _enforce_book_design_consistency(session, project)

    await _ensure_emotion_kernel_backfill_for_pipeline(
        session,
        settings,
        project,
        requested_by=requested_by,
        progress=progress,
    )
    await _ensure_public_emotion_kernel_backfill_for_pipeline(
        session,
        settings,
        project,
        requested_by=requested_by,
        progress=progress,
    )
    await _ensure_entry_system_backfill_for_pipeline(
        session,
        settings,
        project,
        requested_by=requested_by,
        progress=progress,
    )

    # Material Forge is intentionally not run before planning.  Before an
    # approved WorldSpec exists it can only differentiate generic genre seeds,
    # which are reference candidates rather than this book's facts.  Scoped
    # material compilation may run after canon materialisation in a dedicated
    # lane; the core generation path never promotes the unscoped inventory.

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
            artifact = await get_latest_approved_planning_artifact(
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

    chapters = await _select_rolling_outline_window(
        session,
        settings,
        project,
        chapters,
    )
    if not chapters:
        raise ProjectRepairPauseError(
            f"Project '{project_slug}' has no approved rolling-outline window to write."
        )

    if requested_chapter_numbers is not None:
        chapters = [ch for ch in chapters if ch.chapter_number in requested_chapter_numbers]
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
    loaded_chapter_numbers = sorted(ch.chapter_number for ch in chapters)
    sequence_gaps = detect_chapter_sequence_gaps(loaded_chapter_numbers)
    if sequence_gaps:
        prefix_max = contiguous_prefix_max(loaded_chapter_numbers)
        if settings.pipeline.resume_enabled and prefix_max is not None:
            logger.warning(
                "Chapter sequence has gaps for '%s': keeping contiguous 1..%d, "
                "deferring %d discontiguous chapter(s) %s",
                project_slug,
                prefix_max,
                len(sequence_gaps),
                sequence_gaps[:10] + (["..."] if len(sequence_gaps) > 10 else []),
            )
            chapters = [ch for ch in chapters if ch.chapter_number <= prefix_max]
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
    resume_filter_enabled = settings.pipeline.resume_enabled
    if should_materialize:
        resume_filter_enabled = False
    elif (
        requested_chapter_numbers is not None
        and execution_window is None
        and current_volume_number is None
        and total_volumes is None
    ):
        # A direct project-pipeline call with an explicit chapter slice is a
        # manual rerun/repair request. Do not silently skip the selected
        # chapter just because an earlier run marked it complete. Progressive
        # autowrite passes volume context, so it still gets true resume
        # behavior for already-written volume slices.
        resume_filter_enabled = False

    # A completed prose label does not establish knowledge closure. Repair
    # exact adopted sources before the resume filter can skip their chapters.
    # Manual chapter reruns still retain their existing writing semantics.
    if resume_filter_enabled and not settings.quality.draft_mode:
        from bestseller.services.adopted_knowledge_resume import resume_project_adopted_knowledge

        await resume_project_adopted_knowledge(
            session, settings, project=project,
            chapter_numbers={ch.chapter_number for ch in chapters},
        )

    pending_chapters, draftless_revisions = await _select_pending_chapters_for_resume(
        session,
        chapters,
        resume_enabled=resume_filter_enabled,
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

        if getattr(settings.pipeline, "enable_outline_semantic_gate", True):
            current_step_name = "outline_semantic_gate"
            workflow_run.current_step = current_step_name
            semantic_gate_chapters = await _load_project_chapters(session, project.id)
            semantic_gate_report = _record_outline_semantic_gate(
                project,
                semantic_gate_chapters,
                settings,
            )
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "outline_semantic_gate_report": semantic_gate_report,
            }
            if not semantic_gate_report.get("promotion_allowed", False):
                _emit_progress(
                    progress,
                    "outline_semantic_gate_failed",
                    {
                        "project_slug": project_slug,
                        "findings": semantic_gate_report.get("findings", []),
                    },
                )
                await _checkpoint_commit(session)
                if getattr(
                    settings.pipeline,
                    "outline_semantic_gate_block_on_failure",
                    True,
                ):
                    codes = [
                        str(item.get("code") or "")
                        for item in semantic_gate_report.get("findings", [])
                        if isinstance(item, Mapping)
                    ]
                    raise ProjectRepairPauseError(
                        "Whole-book outline semantic gate failed; prose promotion is "
                        f"blocked until replan. issues={', '.join(codes[:12]) or 'unknown'}"
                    )
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref=semantic_gate_report,
            )
            step_order += 1
            _emit_progress(
                progress,
                (
                    "outline_semantic_gate_passed"
                    if semantic_gate_report.get("promotion_allowed", False)
                    else "outline_semantic_gate_warn_only"
                ),
                {"project_slug": project_slug},
            )

        if (
            getattr(settings.pipeline, "enforce_sequential_chapter_generation", True)
            and pending_chapters
        ):
            first_pending_chapter = min(chapter.chapter_number for chapter in pending_chapters)
            prior_incomplete_chapters = await _load_prior_incomplete_chapter_numbers(
                session,
                project_id=project.id,
                before_chapter_number=first_pending_chapter,
            )
            if prior_incomplete_chapters:
                current_step_name = "chapter_sequence_gap_guard"
                workflow_run.current_step = current_step_name
                project.status = ProjectStatus.REVISING.value
                workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "requires_human_review": True,
                    "sequence_gap_guard": {
                        "first_requested_chapter_number": first_pending_chapter,
                        "prior_incomplete_chapter_numbers": prior_incomplete_chapters[:50],
                        "prior_incomplete_chapter_count": len(prior_incomplete_chapters),
                    },
                }
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name=current_step_name,
                    step_order=step_order,
                    status=WorkflowStatus.MACHINE_BLOCKED,
                    output_ref={
                        "first_requested_chapter_number": first_pending_chapter,
                        "prior_incomplete_chapter_numbers": prior_incomplete_chapters[:50],
                        "prior_incomplete_chapter_count": len(prior_incomplete_chapters),
                    },
                )
                await _checkpoint_commit(session)
                _emit_progress(
                    progress,
                    "chapter_sequence_gap_guard_blocked",
                    {
                        "project_slug": project_slug,
                        "first_requested_chapter_number": first_pending_chapter,
                        "prior_incomplete_chapter_numbers": prior_incomplete_chapters[:50],
                        "prior_incomplete_chapter_count": len(prior_incomplete_chapters),
                    },
                )
                return ProjectPipelineResult(
                    workflow_run_id=workflow_run.id,
                    project_id=project.id,
                    project_slug=project.slug,
                    chapter_results=[],
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
                    final_verdict="chapter_sequence_gap",
                    requires_human_review=True,
                )

        qimao_gate_report = _record_qimao_planning_gate(project, chapters=chapters)
        if qimao_gate_report is not None:
            current_step_name = "qimao_planning_gate"
            workflow_run.current_step = current_step_name
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "qimao_planning_gate_report": qimao_gate_report,
            }
            if not qimao_gate_report.get("passed", False):
                _emit_progress(
                    progress,
                    "qimao_planning_gate_failed",
                    {
                        "project_slug": project_slug,
                        "findings": qimao_gate_report.get("findings", []),
                    },
                )
                # Warn-able: heuristic platform-fit gate. When block_on_failure
                # is off, findings are kept in metadata (rewrite directives)
                # and drafting proceeds instead of aborting the whole project
                # at planning (2026-05-29).
                if getattr(
                    settings.pipeline,
                    "qimao_planning_gate_block_on_failure",
                    True,
                ):
                    raise ValueError(_qimao_planning_gate_error_message(qimao_gate_report))
                logger.warning(
                    "qimao_planning_gate failed but block_on_failure is off; continuing: %s",
                    _qimao_planning_gate_error_message(qimao_gate_report),
                )
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref=qimao_gate_report,
            )
            step_order += 1
            _emit_progress(
                progress,
                "qimao_planning_gate_passed",
                {"project_slug": project_slug},
            )

        if getattr(settings.pipeline, "enable_commercial_planning_readiness_gate", True):
            commercial_gate_report = _record_commercial_planning_readiness_gate(
                project,
                chapters=chapters,
                package_root=(Path(settings.output.base_dir) / project.slug),
                long_serial_min_chapters=int(
                    getattr(
                        settings.pipeline,
                        "commercial_planning_min_target_chapters",
                        50,
                    )
                    or 50
                ),
            )
            if commercial_gate_report is not None:
                current_step_name = "commercial_planning_readiness_gate"
                workflow_run.current_step = current_step_name
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "commercial_planning_readiness_report": commercial_gate_report,
                }
                deterministic_actionable_block = _commercial_planning_has_actionable_blockers(
                    commercial_gate_report
                )

                # ── LLM judge path (default) ────────────────────────────────
                # The deterministic gate is advisory; the LLM gives the final
                # verdict using the deterministic findings as reference context.
                use_llm_judge = getattr(
                    settings.pipeline, "enable_commercial_planning_llm_judge", True
                )
                commercial_gate_passed: bool
                llm_judge_payload: dict[str, Any] | None = None

                if use_llm_judge:
                    from bestseller.services.outline_llm_judge import (
                        judge_commercial_planning_readiness_stable,
                    )
                    from bestseller.services.prompt_packs import resolve_prompt_pack

                    llm_threshold = float(
                        getattr(
                            settings.pipeline,
                            "commercial_planning_llm_judge_threshold",
                            0.75,
                        )
                        or 0.75
                    )

                    # Build chapters payload from the current chapter models.
                    # A named builder (not a one-shot comprehension) because the
                    # readiness repair path re-reads the models after mutation.
                    def _build_golden_payload() -> list[dict[str, Any]]:
                        return [
                        {
                            "chapter_number": int(getattr(ch, "chapter_number", 0) or 0),
                            "title": getattr(ch, "title", None) or "",
                            "chapter_goal": getattr(ch, "chapter_goal", None) or "",
                            "opening_situation": getattr(ch, "opening_situation", None) or "",
                            "main_conflict": getattr(ch, "main_conflict", None) or "",
                            "hook_description": getattr(ch, "hook_description", None) or "",
                            "methodology_contract": (
                                (getattr(ch, "metadata_json", None) or {}).get(
                                    "methodology_contract",
                                    {},
                                )
                                if isinstance(getattr(ch, "metadata_json", None), Mapping)
                                else {}
                            ),
                            "causal_contract": (
                                (getattr(ch, "metadata_json", None) or {}).get(
                                    "causal_contract",
                                    {},
                                )
                                if isinstance(getattr(ch, "metadata_json", None), Mapping)
                                else {}
                            ),
                            "event_cycle_contract": (
                                (getattr(ch, "metadata_json", None) or {}).get(
                                    "event_cycle_contract",
                                    {},
                                )
                                if isinstance(getattr(ch, "metadata_json", None), Mapping)
                                else {}
                            ),
                            "hype_type": getattr(ch, "hype_type", None) or "",
                            "hype_intensity": getattr(ch, "hype_intensity", None),
                            "scenes": [
                                {
                                        "scene_number": int(getattr(sc, "scene_number", 0) or 0),
                                    "scene_type": getattr(sc, "scene_type", None) or "",
                                    "title": getattr(sc, "title", None) or "",
                                        "participants": list(getattr(sc, "participants", []) or []),
                                    "purpose": getattr(sc, "purpose", None),
                                    "entry_state": getattr(sc, "entry_state", None),
                                    "exit_state": getattr(sc, "exit_state", None),
                                        "hook_requirement": getattr(sc, "hook_requirement", None)
                                        or "",
                                    "methodology_contract": (
                                        (getattr(sc, "metadata_json", None) or {}).get(
                                            "methodology_contract",
                                            {},
                                        )
                                        if isinstance(
                                            getattr(sc, "metadata_json", None),
                                            Mapping,
                                        )
                                        else {}
                                    ),
                                }
                                for sc in (getattr(ch, "scenes", []) or [])
                            ],
                        }
                        for ch in chapters
                        if int(getattr(ch, "chapter_number", 0) or 0) in (1, 2, 3)
                        ]

                    _golden_payload = _build_golden_payload()
                    _project_metadata = (
                        project.metadata_json
                        if isinstance(getattr(project, "metadata_json", None), dict)
                        else {}
                    )
                    _judge_world_artifact = await get_latest_approved_planning_artifact(
                        session, project_id=project.id, artifact_type=ArtifactType.WORLD_SPEC, settings=settings
                    )
                    _project_brief = _outline_judge_project_brief(
                        project,
                        metadata=_project_metadata,
                        semantic_candidates=[],
                        world_spec=(
                            _judge_world_artifact.content
                            if _judge_world_artifact is not None
                            else None
                        ),
                    )
                    _pack = resolve_prompt_pack(
                        _project_metadata.get("prompt_pack_name")
                        or _project_metadata.get("prompt_pack_key"),
                        genre=str(
                            getattr(project, "genre", "general-fiction") or "general-fiction"
                        ),
                        sub_genre=getattr(project, "sub_genre", None),
                    )
                    try:
                        # 多采样表决：这个判官的裁决对整本书是终审(单样本毙过
                        # 确定性门全过、黄金三章扎实的真书 2026-07-16)。阻断须
                        # 过半样本独立判阻,样本失败弃权——判官抖动既不能单票
                        # 杀书,也不能单票放行。
                        llm_judge_result = await judge_commercial_planning_readiness_stable(
                            session,
                            settings,
                            chapters_payload=_golden_payload,
                            samples=int(
                                getattr(
                                    settings.pipeline,
                                    "commercial_planning_llm_judge_samples",
                                    3,
                                )
                                or 3
                            ),
                            deterministic_findings=commercial_gate_report,
                            project_brief=_project_brief,
                            threshold=llm_threshold,
                            workflow_run_id=str(workflow_run.id) if workflow_run.id else None,
                            pack=_pack,
                        )
                        llm_judge_payload = llm_judge_result.model_dump(mode="json", by_alias=True)
                        llm_judge_should_block = _commercial_planning_llm_judge_should_block(
                                llm_judge_result
                            )
                        if not llm_judge_result.passed and not llm_judge_should_block:
                            logger.warning(
                                "commercial_planning_llm_judge_unactionable_failure",
                                extra={
                                    "project_slug": project_slug,
                                    "overall_score": llm_judge_result.overall_score,
                                },
                            )
                        commercial_gate_passed = (
                            not llm_judge_should_block and not deterministic_actionable_block
                        )
                        # Persist LLM judge result alongside deterministic report
                        project.metadata_json = {
                            **(getattr(project, "metadata_json", None) or {}),
                            "commercial_planning_llm_judge": llm_judge_payload,
                            "commercial_planning_readiness_status": (
                                "llm_gate_passed"
                                if llm_judge_result.passed and not deterministic_actionable_block
                                else "llm_gate_unactionable_warn_only"
                                if commercial_gate_passed
                                else "deterministic_actionable_gate_failed"
                                if deterministic_actionable_block
                                else "llm_gate_failed"
                            ),
                        }
                    except Exception as _llm_exc:
                        # A judge outage is not positive quality evidence. Keep
                        # the deterministic report and fail closed so a broken
                        # evaluator cannot promote an unreadable outline.
                        logger.warning(
                            "commercial_planning_llm_judge_error",
                            exc_info=_llm_exc,
                        )
                        commercial_gate_passed = False
                        llm_judge_payload = {
                            "pass": False,
                            "evaluator_error": type(_llm_exc).__name__,
                            "message": str(_llm_exc),
                            "blocking_issues": [
                                {
                                    "code": "COMMERCIAL_LLM_JUDGE_UNAVAILABLE",
                                    "severity": "critical",
                                    "evidence": str(_llm_exc),
                                    "required_fix": "Retry the evaluator before promotion.",
                                }
                            ],
                        }
                else:
                    # Deterministic-only fallback (legacy behaviour)
                    commercial_gate_passed = (
                        commercial_gate_report.get("passed", True)
                        and not deterministic_actionable_block
                    )

                # ── Bounded readiness repair ────────────────────────────────
                # The judge's blocking_issues carry executable required_fix
                # directives; until 2026-07-16 nothing consumed them and every
                # block was instant task death. Two focused golden-3 revisions
                # may build on each other inside one transaction; a still-
                # failing re-judge falls through to the fail-closed raise.
                _readiness_repair_enabled = bool(
                    use_llm_judge
                    and llm_judge_payload
                    and not llm_judge_payload.get("evaluator_error")
                    and not deterministic_actionable_block
                    and getattr(
                        settings.pipeline,
                        "commercial_planning_repair_enabled",
                        True,
                    )
                )
                _readiness_repair_rounds = min(
                    max(
                        int(
                            getattr(
                                settings.pipeline,
                                "commercial_planning_repair_max_rounds",
                                2,
                            )
                            or 2
                        ),
                        1,
                    ),
                    3,
                )
                for _repair_round in range(_readiness_repair_rounds):
                    if commercial_gate_passed or not _readiness_repair_enabled:
                        break
                    from bestseller.services.golden_three_repair import (
                        repair_golden_three_outline,
                    )

                    _emit_progress(
                        progress,
                        "commercial_planning_readiness_repair_started",
                        {
                            "project_slug": project_slug,
                            "repair_round": _repair_round + 1,
                            "max_rounds": _readiness_repair_rounds,
                        },
                    )
                    _repaired = await repair_golden_three_outline(
                        session,
                        settings,
                        chapters=chapters,
                        llm_judge_payload=llm_judge_payload,
                        project=project,
                        project_brief=_project_brief,
                    )
                    if not _repaired:
                        break
                    await session.flush()
                    try:
                        llm_judge_result = await judge_commercial_planning_readiness_stable(
                            session,
                            settings,
                            chapters_payload=_build_golden_payload(),
                            samples=int(
                                getattr(
                                    settings.pipeline,
                                    "commercial_planning_llm_judge_samples",
                                    3,
                                )
                                or 3
                            ),
                            deterministic_findings=commercial_gate_report,
                            project_brief=_project_brief,
                            threshold=llm_threshold,
                            workflow_run_id=str(workflow_run.id) if workflow_run.id else None,
                            pack=_pack,
                        )
                        llm_judge_payload = llm_judge_result.model_dump(mode="json", by_alias=True)
                        commercial_gate_passed = not _commercial_planning_llm_judge_should_block(
                                llm_judge_result
                            )
                        project.metadata_json = {
                            **(getattr(project, "metadata_json", None) or {}),
                            "commercial_planning_llm_judge": llm_judge_payload,
                            "commercial_planning_readiness_repair": {
                                "applied": True,
                                "rounds_attempted": _repair_round + 1,
                                "passed_after_repair": commercial_gate_passed,
                            },
                        }
                        logger.warning(
                            "commercial planning readiness repair round %d/%d: re-judge %s",
                            _repair_round + 1,
                            _readiness_repair_rounds,
                            "PASSED" if commercial_gate_passed else "still blocked",
                        )
                    except Exception:
                        logger.warning(
                            "readiness re-judge after repair failed; keeping block",
                            exc_info=True,
                        )
                        break

                if not commercial_gate_passed:
                    # (2026-08-02) Exhausting the repair rounds no longer kills
                    # the book. This verdict is about retention craft — whether
                    # the first three chapters hook hard enough — and the model
                    # already had its repair rounds against the same feedback.
                    # Blocking here meant a book that had passed conception,
                    # foundation and the full outline died before writing a
                    # single word. The unresolved findings are recorded as
                    # quality debt so the report still shows what was conceded.
                    _emit_progress(
                        progress,
                        "commercial_planning_readiness_gate_conceded",
                        {
                            "project_slug": project_slug,
                            "findings": commercial_gate_report.get("findings", []),
                            "llm_judge": llm_judge_payload,
                        },
                    )
                    _block_reason = "LLM judge" if use_llm_judge else "deterministic gate"
                    logger.warning(
                        "commercial planning readiness conceded for %s after repair "
                        "rounds (%s); proceeding with recorded quality debt",
                        project_slug,
                        _block_reason,
                    )
                    project.metadata_json = {
                        **(getattr(project, "metadata_json", None) or {}),
                        "commercial_planning_readiness_status": "conceded_quality_debt",
                        "commercial_planning_readiness_debt": {
                            "block_reason": _block_reason,
                            "findings": commercial_gate_report.get("findings", [])[:12],
                            "recorded_at": _dt.datetime.now(_dt.UTC).isoformat(),
                        },
                    }
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name=current_step_name,
                    step_order=step_order,
                    status=WorkflowStatus.COMPLETED,
                    output_ref={
                        "deterministic": commercial_gate_report,
                        "llm_judge": llm_judge_payload,
                        "passed": commercial_gate_passed,
                    },
                )
                step_order += 1
                # Only a book that actually cleared the gate reports as passed.
                # Emitting this unconditionally showed a green gate on the
                # dashboard for a book whose own metadata says
                # ``conceded_quality_debt`` — the concession event above would
                # scroll past and the last word the operator saw was "passed".
                if commercial_gate_passed:
                    _emit_progress(
                        progress,
                        "commercial_planning_readiness_gate_passed",
                        {"project_slug": project_slug},
                    )

        # Child chapter pipelines can roll back the shared session. Persist
        # the project workflow shell before entering the chapter loop.
        project.status = ProjectStatus.WRITING.value
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

        qimao_opening_texts: dict[int, str] = {}
        whole_book_quality_texts: dict[int, str] = {}

        for chapter in pending_chapters:
            # 操作台停止/暂停检查点（2026-08-19 用户报「停止有延迟」）。
            # 此前唯一的在飞检查点在**卷边界**（run_progressive_autowrite_pipeline
            # 的 volume 循环）——一卷 8-16 章、每章数分钟，于是点了停止要等到
            # 下一卷才生效，用户体感就是"停不掉/延迟很久"。章边界是最细的
            # 安全切点（章内停会留半截草稿），读的是同一个事实源
            # book_production_control，与卷边界检查同源。
            try:
                _ch_control = await load_control_state(session, project.id)
            except Exception:
                # 停止检查自身永远不许中断一次运行（与卷边界同款 fail-open）
                logger.debug(
                    "could not read production control for %s at chapter %s; continuing",
                    project_slug,
                    chapter.chapter_number,
                    exc_info=True,
                )
                _ch_control = None
            if _ch_control is not None and _ch_control.halted:
                if execution_window is not None:
                    workflow_run.metadata_json = {**(workflow_run.metadata_json or {}),
                        "execution_window_halted": _ch_control.to_payload()}
                logger.info(
                    "chapter loop halted by operator intent project=%s chapter=%s "
                    "intent=%s reason=%s",
                    project_slug,
                    chapter.chapter_number,
                    _ch_control.intent.value,
                    _ch_control.reason,
                )
                _emit_progress(
                    progress,
                    "chapter_loop_halted_by_operator",
                    {
                        "project_slug": project_slug,
                        "chapter_number": chapter.chapter_number,
                        **_ch_control.to_payload(),
                    },
                )
                break
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
                allow_structural_repair=allow_structural_repair,
                chapter_first=chapter_first,
                progress=progress,
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
            _numeric_debt_bound = await _numeric_promotion_debt_binding_is_current(
                session, chapter.id, getattr(chapter, "metadata_json", None)
            )
            _has_current_chapter_body = False
            if chapter_result.chapter_draft_id and chapter_local_debt_allows_continuation(
                getattr(chapter, "metadata_json", None), has_current_draft=True,
                numeric_debt_binding_verified=_numeric_debt_bound,
            ):
                # A result ID may point to a rejected/non-current candidate.
                # Bind the actual current nonempty body before allowing debt.
                _has_current_chapter_body = bool(await session.scalar(
                    select(ChapterDraftVersionModel.id).where(
                        ChapterDraftVersionModel.id == chapter_result.chapter_draft_id,
                        ChapterDraftVersionModel.chapter_id == chapter.id,
                        ChapterDraftVersionModel.is_current.is_(True),
                        func.length(func.regexp_replace(
                            ChapterDraftVersionModel.content_md, r"\s", "", "g"
                        )) > 0,
                    ).limit(1)
                ))
            _chapter_sequence_stop = _chapter_requires_sequence_stop(
                chapter, chapter_result, settings,
                has_current_draft=_has_current_chapter_body,
                numeric_debt_binding_verified=_numeric_debt_bound,
            )
            if chapter_result.requires_human_review or _chapter_sequence_stop:
                requires_human_review = True
                # A production book cannot advance past a chapter that still
                # needs review or has not reached formal completion. A later
                # chapter candidate must not make this gap look safe on resume.
                _pause_whole_book = _chapter_sequence_stop
                _flagged = list(
                    (workflow_run.metadata_json or {}).get("chapters_requiring_review") or []
                )
                if chapter.chapter_number not in _flagged:
                    _flagged.append(chapter.chapter_number)
                _emit_progress(
                    progress,
                    "chapter_pipeline_machine_repair_required"
                    if _pause_whole_book
                    else "chapter_flagged_for_review_continuing",
                    {
                        "project_slug": project_slug,
                        "chapter_number": chapter.chapter_number,
                        "workflow_run_id": str(chapter_result.workflow_run_id),
                        "chapters_requiring_review": _flagged,
                    },
                )
                if _pause_whole_book:
                    project.status = ProjectStatus.REVISING.value
                    workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                    workflow_run.current_step = "machine_repair_required"
                    workflow_run.metadata_json = {
                        **(workflow_run.metadata_json or {}),
                        "requires_human_review": True,
                        "paused_after_chapter_number": chapter.chapter_number,
                        "blocked_chapter_workflow_run_id": str(chapter_result.workflow_run_id),
                        "processed_chapter_count": len(chapter_results),
                        "chapters_requiring_review": _flagged,
                    }
                    await sync_world_expansion_progress(session, project=project)
                    await _checkpoint_commit(session)
                    break
                # soft-continue: record the flag and move on to the next chapter
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "requires_human_review": True,
                    "chapters_requiring_review": _flagged,
                }
                await _checkpoint_commit(session)
            if project_uses_signing_quality_gate(project) and chapter.chapter_number <= 3:
                current_step_name = f"qimao_opening_gate_chapter_{chapter.chapter_number}"
                workflow_run.current_step = current_step_name
                await _enforce_qimao_opening_gate_after_chapter(
                    session,
                    project=project,
                    chapter=chapter,
                    chapter_result=chapter_result,
                    opening_texts=qimao_opening_texts,
                    workflow_run=workflow_run,
                    settings=settings,
                    progress=progress,
                )
            if _project_uses_whole_book_quality_gate(project):
                current_step_name = f"whole_book_quality_gate_chapter_{chapter.chapter_number}"
                workflow_run.current_step = current_step_name
                await _enforce_whole_book_quality_gate_after_chapter(
                    session,
                    project=project,
                    chapter=chapter,
                    chapter_result=chapter_result,
                    chapter_texts=whole_book_quality_texts,
                    workflow_run=workflow_run,
                    progress=progress,
                    settings=settings,
                )
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
                and chapter
                != pending_chapters[-1]  # Skip if last chapter (full check happens later)
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
                        (
                            interim_review,
                            interim_report,
                            interim_quality,
                        ) = await review_project_consistency(
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
                            _consistency_warnings = [
                                f.message for f in interim_review.findings[:10]
                            ]
                            project.metadata_json = {
                                **(project.metadata_json or {}),
                                "_pending_consistency_warnings": _consistency_warnings,
                            }
                            await session.flush()
                        except Exception:
                            logger.debug(
                                "Failed to store consistency warnings in project metadata",
                                exc_info=True,
                            )
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
                        "from_chapter": max(
                            1, chapter.chapter_number - rolling_summary_interval + 1
                        ),
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
                            from_chapter=max(
                                1, chapter.chapter_number - rolling_summary_interval + 1
                            ),
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
                            session,
                            project.id,
                            arc_start,
                            chapter.chapter_number,
                        )
                        arc_summary = await generate_linear_arc_summary(
                            session,
                            settings,
                            project,
                            arc_start,
                            chapter.chapter_number,
                            chapter_summaries=chapter_summaries,
                        )
                        await store_linear_arc_summary(
                            session,
                            project,
                            arc_idx,
                            arc_summary,
                            arc_start,
                            chapter.chapter_number,
                        )
                        if settings.pipeline.world_snapshot_enabled:
                            snapshot = await generate_linear_world_snapshot(
                                session,
                                settings,
                                project,
                                chapter.chapter_number,
                                arc_summary,
                            )
                            await store_linear_world_snapshot(
                                session,
                                project,
                                chapter.chapter_number,
                                snapshot,
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

        if execution_window is not None:
            from bestseller.services.execution_windows import finish_project_window
            return await finish_project_window(session, settings, project, workflow_run, chapter_results,
                export_markdown=export_markdown, progress=progress,
                scope_numbers={chapter.chapter_number for chapter in chapters})

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
            # Non-fatal: a combined project export can be blocked by the
            # publish-hygiene check when ANY chapter is still in revision /
            # blocked. That must NOT abort the whole generation run (it would
            # turn one unfinished chapter into a 0-output book, and stop later
            # chapters from ever being drafted). Per-chapter markdown files
            # remain available; the combined export retries on a later run.
            try:
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
            except ValueError as _proj_export_err:
                strict_export_reason = str(_proj_export_err)
                logger.warning(
                    "Project export blocked for %s; trying explicit closure draft: %s",
                    project_slug,
                    _proj_export_err,
                )
                try:
                    closure_artifact, closure_path = await export_project_closure_draft_markdown(
                        session,
                        settings,
                        project_slug,
                        created_by_run_id=workflow_run.id,
                    )
                    export_artifact_id = closure_artifact.id
                    output_path = str(closure_path.resolve())
                    await create_workflow_step_run(
                        session,
                        workflow_run_id=workflow_run.id,
                        step_name=current_step_name,
                        step_order=step_order,
                        status=WorkflowStatus.COMPLETED,
                        output_ref={
                            "export_artifact_id": str(export_artifact_id),
                            "output_path": output_path,
                            "export_kind": "closure_draft",
                            "publication_ready": False,
                            "strict_export_blocked": strict_export_reason,
                        },
                    )
                    step_order += 1
                    _emit_progress(
                        progress,
                        "project_closure_draft_exported",
                        {
                            "project_slug": project_slug,
                            "export_artifact_id": str(export_artifact_id),
                            "output_path": output_path,
                            "export_kind": "closure_draft",
                            "publication_ready": False,
                            "strict_export_blocked": strict_export_reason,
                        },
                    )
                except ValueError as _closure_export_err:
                    logger.warning(
                        "Project closure draft also unavailable for %s; continuing pipeline: %s",
                        project_slug,
                        _closure_export_err,
                    )
                    await create_workflow_step_run(
                        session,
                        workflow_run_id=workflow_run.id,
                        step_name=current_step_name,
                        step_order=step_order,
                        status=WorkflowStatus.COMPLETED,
                        output_ref={
                            "export_blocked": strict_export_reason,
                            "closure_draft_blocked": str(_closure_export_err),
                            "publication_ready": False,
                        },
                    )
                    step_order += 1
                    _emit_progress(
                        progress,
                        "project_export_skipped",
                        {
                            "project_slug": project_slug,
                            "reason": strict_export_reason,
                            "closure_draft_reason": str(_closure_export_err),
                            "publication_ready": False,
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
        project_consistency_warn_only_scope = _project_consistency_warn_only_scope(
            current_volume_number=current_volume_number,
            chapter_numbers=requested_chapter_numbers,
            written_chapters=int(getattr(project, "current_chapter_number", 0) or 0),
            target_chapters=int(getattr(project, "target_chapters", 0) or 0),
        )
        if project_review_not_pass:
            if settings.quality.draft_mode:
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "project_consistency_warn_only": True,
                    "project_consistency_scope": "draft_mode",
                    "project_consistency_verdict": review_result.verdict,
                }
                logger.warning(
                    "Project %s consistency verdict=%s during draft mode — recorded "
                    "as warning; draft-mode writes are not whole-book blockers.",
                    project_slug,
                    review_result.verdict,
                )
            elif project_consistency_warn_only_scope is not None:
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "project_consistency_warn_only": True,
                    "project_consistency_scope": project_consistency_warn_only_scope,
                    "project_consistency_verdict": review_result.verdict,
                }
                logger.warning(
                    "Project %s consistency verdict=%s during %s — recorded as "
                    "warning; partial write slices are not whole-book blockers.",
                    project_slug,
                    review_result.verdict,
                    project_consistency_warn_only_scope,
                )
            elif getattr(settings.pipeline, "project_consistency_block_on_failure", True):
                requires_human_review = True
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "blocked_by_project_consistency": True,
                    "project_consistency_verdict": review_result.verdict,
                }
                logger.warning(
                    "Project %s consistency verdict=%s — blocking for review; "
                    "accept_on_stall does not override whole-book consistency.",
                    project_slug,
                    review_result.verdict,
                )
            elif settings.pipeline.accept_on_stall:
                logger.info(
                    "Project %s consistency verdict=%s — accepting per accept_on_stall; "
                    "skipping machine-repair pause.",
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

        processed_chapter_number = max(
            (item.chapter_number for item in chapter_results),
            default=max(chapter.chapter_number for chapter in chapters),
        )
        project.current_chapter_number = max(
            int(project.current_chapter_number or 0),
            processed_chapter_number,
        )
        await sync_world_expansion_progress(session, project=project)
        project.status = (
            ProjectStatus.REVISING.value if requires_human_review else ProjectStatus.WRITING.value
        )

        workflow_run.status = (
            WorkflowStatus.MACHINE_BLOCKED.value
            if requires_human_review
            else WorkflowStatus.COMPLETED.value
        )
        workflow_run.current_step = (
            "machine_repair_required" if requires_human_review else "completed"
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
            audit_report = await run_and_persist_audit(session, project.id, build_phase1_audit())
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
        scorecard_quality_score_for_premium_gate: float | None = None
        scorecard_quality_score_ignored_reason: str | None = None
        try:
            scorecard_project_dir = (
                Path(getattr(settings.output, "base_dir", ".") or ".") / project_slug
            )
            scorecard = await compute_scorecard(
                session,
                project.id,
                expected_chapter_count=project.target_chapters,
                project_dir=(scorecard_project_dir if scorecard_project_dir.exists() else None),
            )
            await save_scorecard(session, scorecard)
            scorecard_quality_score = scorecard.quality_score
            (
                scorecard_quality_score_for_premium_gate,
                scorecard_quality_score_ignored_reason,
            ) = _scorecard_score_for_premium_gate(scorecard)
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

        # Stage 12 — Premium Book Gate.
        # ---------------------------------------------------------------
        # This is a project-level structural readiness gate. By default it
        # records telemetry and repair actions without blocking legacy runs;
        # operators can enable hard blocking via pipeline settings.
        premium_book_gate_payload: dict[str, Any] | None = None
        premium_book_gate_passed: bool | None = None
        try:
            if getattr(settings.pipeline, "enable_premium_book_gate", True):
                current_step_name = "premium_book_gate"
                workflow_run.current_step = current_step_name
                from bestseller.services.premium_book_gate import (
                    evaluate_premium_project_readiness,
                    premium_book_gate_report_to_dict,
                )

                premium_report = evaluate_premium_project_readiness(
                    project,
                    scorecard_quality_score=scorecard_quality_score_for_premium_gate,
                )
                premium_book_gate_payload = premium_book_gate_report_to_dict(premium_report)
                premium_book_gate_passed = premium_report.passed
                project.metadata_json = {
                    **(project.metadata_json or {}),
                    "premium_book_gate_report": premium_book_gate_payload,
                }
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name=current_step_name,
                    step_order=step_order,
                    status=WorkflowStatus.COMPLETED,
                    output_ref={
                        "passed": premium_report.passed,
                        "score": premium_report.score,
                        "blocking_codes": [
                            finding.code for finding in premium_report.blocking_findings
                        ],
                    },
                )
                step_order += 1
                if not premium_report.passed and getattr(
                        settings.pipeline,
                        "premium_book_gate_block_on_failure",
                        False,
                ):
                    requires_human_review = True
                    project.status = ProjectStatus.REVISING.value
                    workflow_run.status = WorkflowStatus.MACHINE_BLOCKED.value
                    workflow_run.current_step = "machine_repair_required"
                _emit_progress(
                    progress,
                    "premium_book_gate_completed",
                    {
                        "project_slug": project.slug,
                        "passed": premium_report.passed,
                        "score": premium_report.score,
                        "blocking_count": len(premium_report.blocking_findings),
                    },
                )
        except Exception as premium_gate_exc:  # pragma: no cover - telemetry guard
            logger.warning(
                "Stage 12 premium book gate failed for project %s: %s",
                project.slug,
                premium_gate_exc,
            )

        # A finished book must be able to say so on its own. Before this, the
        # happy path ended in WRITING — a state asserting work is in progress
        # when nothing was running — and no automatic path could ever reach
        # COMPLETED (the sole writer was a manual web endpoint). Two real books
        # drafted every chapter with zero failed workflows and sat in that lie
        # indefinitely (2026-07-26).
        # Re-read from the database: production_state is written by the repair
        # and promotion paths after these rows were last loaded, so anything
        # cached in this scope would judge the book on a stale state.
        #
        # Shared with the repair lane, which is where a real run usually ends:
        # this check fires while chapters may still be in flight, so whichever
        # lane finishes last must reach the same verdict from the same code.
        closure = await settle_project_status_on_closure(
            session,
            project,
            settings=settings,
            fallback_status=(
                ProjectStatus.REVISING.value
                if requires_human_review
                else ProjectStatus.WRITING.value
            ),
            now_iso=_dt.datetime.now(_dt.UTC).isoformat(),
        )
        workflow_run.status = (
            WorkflowStatus.MACHINE_BLOCKED.value
            if requires_human_review and not closure.is_complete
            else WorkflowStatus.COMPLETED.value
        )
        workflow_run.current_step = (
            "machine_repair_required" if requires_human_review else "completed"
        )
        final_project_verdict = (
            "draft"
            if settings.quality.draft_mode
            and review_result is not None
            and review_result.verdict != "pass"
            and not requires_human_review
            else (review_result.verdict if review_result is not None else None)
        )
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "audit_finding_count": audit_finding_count,
            "scorecard_quality_score": scorecard_quality_score,
            "scorecard_quality_score_for_premium_gate": scorecard_quality_score_for_premium_gate,
            "scorecard_quality_score_ignored_reason": scorecard_quality_score_ignored_reason,
            "premium_book_gate_passed": premium_book_gate_passed,
            "premium_book_gate_report": premium_book_gate_payload,
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
                "final_verdict": final_project_verdict,
                "requires_human_review": requires_human_review,
                "output_path": output_path,
                "audit_finding_count": audit_finding_count,
                "scorecard_quality_score": scorecard_quality_score,
                "premium_book_gate_passed": premium_book_gate_passed,
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
            final_verdict=final_project_verdict,
            export_artifact_id=export_artifact_id,
            output_path=output_path,
            requires_human_review=requires_human_review,
        )
    except Exception as exc:
        attach_workflow_failure(exc,workflow_run)
        # Same guard as the scene/chapter pipelines — DB-level failures must
        # rollback-and-raise so follow-up writes don't trigger
        # ``MissingGreenlet`` during connection checkout.
        if _is_db_session_failure(session, exc):
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
        except (PendingRollbackError, DBAPIError, MissingGreenlet):
            await session.rollback()
        raise


def _repair_round_made_progress(*, previous: int, current: int) -> bool:
    """Whether a repair round settled more chapters than the one before it.

    Repair reopens chapters it decides to rework, so the settled count both
    rises and falls during a run. Only an increase counts: equal means the
    round achieved nothing, and a decrease means it reopened more than it
    closed. Either way, running the same round again would repeat it.
    """

    return int(current) > int(previous)


async def _settled_chapter_count(session: AsyncSession, project_id: Any) -> int | None:
    """Settled chapters, or ``None`` when the count cannot be read.

    ``None`` is not zero: an unreadable count means the driver has no evidence
    of progress either way, and it stops rather than guessing. A book that
    generated successfully must never be disrupted by a bookkeeping query.
    """

    from bestseller.services.book_closure import SETTLED_PRODUCTION_STATES

    try:
        rows = (
            (
                await session.execute(
                    select(ChapterModel.production_state).where(
                        ChapterModel.project_id == project_id
                    )
                )
            )
            .scalars()
            .all()
        )
    except Exception:
        return None
    return sum(1 for state in rows if str(state or "").strip().lower() in SETTLED_PRODUCTION_STATES)


async def _drive_repair_to_closure(
    session: AsyncSession,
    settings: AppSettings,
    project: Any,
    *,
    requested_by: str,
    export_markdown: bool,
    progress: Any,
    first_result: Any,
    max_rounds: int = 6,
) -> Any:
    """Keep repairing until the book closes or a round stops helping.

    One repair pass rarely settles every chapter — on real runs the first pass
    ends at one or two of three — and the previous code stopped there, leaving
    the book in ``revising`` until a self-heal sweep happened along. It did get
    there (three sweeps drove one book to completion over half an hour), but
    "start the project and it finishes" should not depend on a background
    sweep's cadence.

    Bounded twice over: it stops as soon as closure reports the book complete,
    and it stops when a round fails to settle more chapters than the one before
    — which is also what a stuck book looks like. Self-heal remains the safety
    net for the cases this cannot cover, such as the process dying mid-run.
    """

    from bestseller.services.book_delivery_readiness import read_book_delivery_closure
    from bestseller.services.repair import run_project_repair

    result = first_result
    settled = await _settled_chapter_count(session, project.id)
    if settled is None:
        return result

    for round_index in range(2, max_rounds + 1):
        closure = await read_book_delivery_closure(session, project)
        if closure.verdict.is_complete:
            return result

        result = await run_project_repair(
            session,
            settings,
            project.slug,
            requested_by=requested_by,
            export_markdown=export_markdown,
            progress=progress,
        )
        current = await _settled_chapter_count(session, project.id)
        _emit_progress(
            progress,
            "auto_repair_round_completed",
            {
                "project_slug": project.slug,
                "round": round_index,
                "settled_chapters": current,
                "previous_settled_chapters": settled,
            },
        )
        if current is None or not _repair_round_made_progress(previous=settled, current=current):
            return result
        settled = current

    return result


def _prepare_story_architecture_migration_metadata(
    current_metadata: Mapping[str, Any] | None,
    replacement_metadata: Mapping[str, Any] | None,
    *,
    target_chapters: int,
    operation: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate and audit a reconception cutover before touching project truth."""

    from bestseller.services.concept_contract import (
        require_conception_contract_for_target,
    )

    previous_meta = dict(current_metadata or {})
    migration_meta = dict(replacement_metadata or {})
    fresh_contract = require_conception_contract_for_target(
        migration_meta.get("concept_contract"),
        target_chapters=int(target_chapters),
    )
    if fresh_contract is None:
        raise ValueError("structural reconception completed without a promotable concept contract")
    previous_contract = previous_meta.get("concept_contract")
    previous_contract = dict(previous_contract) if isinstance(previous_contract, Mapping) else {}
    migration_history = previous_meta.get("story_architecture_migration_history")
    migration_history = (
        [dict(item) for item in migration_history if isinstance(item, Mapping)]
        if isinstance(migration_history, Sequence)
        and not isinstance(migration_history, (str, bytes))
        else []
    )
    migration_history.append(
        {
            "migrated_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "operation": operation,
            "previous_champion_id": str(previous_contract.get("champion_id") or ""),
            "previous_input_hash": str(previous_contract.get("input_hash") or ""),
            "new_champion_id": str(fresh_contract.get("champion_id") or ""),
            "new_input_hash": str(fresh_contract.get("input_hash") or ""),
            "schema_version": "story-architecture.v1",
        }
    )
    return (
        {
            **previous_meta,
            **migration_meta,
            "concept_contract_version": "2",
            "concept_contract": fresh_contract,
            "story_architecture_migration_history": migration_history[-10:],
            "story_architecture_migration_status": "validated",
        },
        fresh_contract,
    )


@pipeline_operation_stage
@project_prompt_audit_scope
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
    use_conception: bool = False,
    allow_outline_replan: bool = False,
    force_foundation_replan: bool = False,
) -> AutowriteResult:
    """Run the full novel pipeline.

    When *use_conception* is ``True``, a multi-agent conception pipeline
    runs **before** project creation (mirroring the Web UI's flow).
    The conception result is merged into *project_payload* and *premise*
    so downstream planning benefits from the richer premise / writing profile.
    """
    from bestseller.domain.enums import ProjectType

    # Conception is an explicit creation-boundary phase.  Do not infer it from
    # chapter count: the web flow already runs the initial conception before
    # materialisation, and silently running a second conception here overwrites
    # the user's V1 handoff and creates identity drift.
    use_conception = bool(use_conception)

    # ── Conception pre-pass (mandatory for new long-form books) ──
    if use_conception:
        from bestseller.services.conception import run_conception_pipeline
        from bestseller.services.genre_intent_contract import contract_from_payload
        from bestseller.services.story_enhancers import wants_wild_concept

        genre_key = (
            (project_payload.metadata or {}).get("genre_canonical") or project_payload.genre or ""
        )
        # 脑洞全开开关从建书 metadata 线程化进构思(单一真源);未勾选则 hints 不含
        # 该键,user_hints 与现状逐字节一致。
        from bestseller.services.production_strategy import normalize_profile
        _conception_hints: dict[str, Any] = {"production_profile": normalize_profile(
            (project_payload.metadata or {}).get("production_profile"))}
        if premise:
            _conception_hints["premise"] = premise
        if wants_wild_concept(project_payload.metadata or {}):
            _conception_hints["wild_concept"] = True
        conception_result = await run_conception_pipeline(
            session,
            settings,
            genre_key=genre_key,
            chapter_count=project_payload.target_chapters,
            user_hints=_conception_hints or None,
            genre=project_payload.genre,
            sub_genre=project_payload.sub_genre,
            genre_intent_contract=contract_from_payload(project_payload.metadata or {}),
            progress=progress,
        )
        # Merge conception results into payload (same pattern as web/server.py)
        if conception_result.premise:
            premise = conception_result.premise
        if conception_result.title:
            project_payload = project_payload.model_copy(update={"title": conception_result.title})
        if conception_result.writing_profile:
            project_payload = project_payload.model_copy(
                update={"writing_profile": conception_result.writing_profile}
            )
        # Enrich metadata with conception artifacts
        _meta = dict(project_payload.metadata or {})
        _meta.update(
            {
            "premise": premise,
            "conception_brief": conception_result.commercial_brief,
            "synopsis": conception_result.synopsis,
            "tags": conception_result.tags,
            "story_spine": conception_result.story_spine,
            "concept_methodology": conception_result.concept_methodology,
            "conception_degraded": conception_result.degraded,
            "conception_degradation_events": [
                {
                    "stage": event.stage,
                    "component": event.component,
                    "reason": event.reason,
                    "severity": event.severity,
                    "fallback": event.fallback,
                    "model": event.model,
                    "metadata": event.metadata,
                }
                for event in conception_result.degradation_events
            ],
            }
        )
        # create_project() promotes a v2 conception package only when it can
        # read the final promotion receipt. The service path previously dropped
        # story_appeal, which made real CLI creation bypass the contract.
        if conception_result.story_appeal:
            _meta["conception_artifacts"] = {
                **(
                    dict(_meta.get("conception_artifacts") or {})
                    if isinstance(_meta.get("conception_artifacts"), Mapping)
                    else {}
                ),
                "story_appeal": dict(conception_result.story_appeal),
            }
        _concept_contract = getattr(conception_result, "concept_contract", None)
        if isinstance(_concept_contract, dict) and _concept_contract:
            _meta["concept_contract_version"] = "2"
            _meta["concept_contract"] = _concept_contract
            _meta["hook_card"] = getattr(conception_result, "hook_card", {})
            _meta["seriality_proof"] = getattr(conception_result, "seriality_proof", {})
            _meta["story_spine"] = _concept_contract.get(
                "story_spine", conception_result.story_spine
            )
            _meta.pop("hook_spec", None)
        if conception_result.hook_spec:
            _meta["hook_spec"] = conception_result.hook_spec
        # ── Advisory market validation (opt-in, never gates) ──
        # Runs against the conception outputs (title/premise/synopsis) and
        # stashes only a summary into metadata; any failure degrades silently
        # so the pipeline is byte-identical when the flag is off or data
        # sources are down.
        # Defensive flag read: an advisory add-on must never crash creation,
        # and ``settings`` may be a stub without ``.pipeline`` in some callers.
        if bool(
            getattr(
                getattr(settings, "pipeline", None),
                "enable_market_validation",
                False,
            )
        ):
            try:
                from bestseller.services.market_validation.request_builder import (
                    build_creation_request,
                )
                from bestseller.services.market_validation.service import (
                    run_market_validation,
                )

                _mv_report = await run_market_validation(
                    build_creation_request(
                        metadata=_meta,
                        genre_label=str(project_payload.genre or ""),
                        sub_genre_label=str(project_payload.sub_genre or ""),
                        title=str(project_payload.title or ""),
                        concept=str(premise or ""),
                        blurb=str(conception_result.synopsis or ""),
                        fallback_genre_key=genre_key,
                        project_slug=str(project_payload.slug or ""),
                    ),
                    settings=settings,
                    session=session,
                )
                _meta["market_validation_summary"] = _mv_report.summary()
            except Exception:
                logger.warning("Advisory market validation failed (fail-open)", exc_info=True)
        project_payload = project_payload.model_copy(update={"metadata": _meta})

    # Existing projects that predate story-architecture.v1 need an explicit
    # reconception migration before any structural repair.  Merely running the
    # conception pre-pass used to mutate only the transient ProjectCreate
    # payload; because the project row already existed, the approved contract
    # was then discarded and the repair lane rearranged the same thin premise.
    # Persist the newly validated conception truth before routing into either
    # planning implementation.
    if use_conception and (allow_outline_replan or force_foundation_replan):
        existing_project = await get_project_by_slug(session, project_payload.slug)
        if existing_project is not None:
            operation = "foundation_rebuild" if force_foundation_replan else "outline_replan"
            merged_meta, fresh_contract = _prepare_story_architecture_migration_metadata(
                    existing_project.metadata_json,
                    project_payload.metadata,
                    target_chapters=int(existing_project.target_chapters),
                    operation=operation,
                )
            existing_project.metadata_json = merged_meta
            existing_project.title = project_payload.title
            existing_project.genre = project_payload.genre
            existing_project.sub_genre = project_payload.sub_genre
            existing_project.audience = project_payload.audience
            await import_planning_artifact(
                session,
                existing_project.slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.PREMISE,
                    content={"premise": premise},
                    notes=(
                        "story-architecture.v1 reconception migration; "
                        f"champion_id={fresh_contract.get('champion_id')}"
                    ),
                ),
            )
            await _checkpoint_commit(session)
            _emit_progress(
                progress,
                "story_architecture_migrated",
                {
                    "project_slug": existing_project.slug,
                    "schema_version": "story-architecture.v1",
                    "champion_id": fresh_contract.get("champion_id"),
                    "requires_foundation_replan": True,
                },
            )

    if project_payload.project_type == ProjectType.FANQIE_SHORT:
        from bestseller.services.fanqie_short_pipeline import run_fanqie_short_pipeline

        return await run_fanqie_short_pipeline(
            session,
            settings,
            project_payload=project_payload,
            premise=premise,
            requested_by=requested_by,
            export_markdown=export_markdown,
            progress=progress,
        )

    # ── Route to progressive pipeline if enabled or target warrants it ──
    if _should_use_progressive_pipeline(settings, project_payload):
        return await run_progressive_autowrite_pipeline(
            session,
            settings,
            project_payload=project_payload,
            premise=premise,
            requested_by=requested_by,
            export_markdown=export_markdown,
            auto_repair_on_attention=auto_repair_on_attention,
            progress=progress,
            allow_outline_replan=allow_outline_replan,
            force_foundation_replan=force_foundation_replan,
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
    bind_resolved_project_prompt_audit(
        project.id, reason="Normal autowrite project request and paid-return custody",
    )
    if await _clear_auto_resumable_project_pause(session, project):
        await _checkpoint_commit(session)
    if allow_outline_replan or force_foundation_replan:
        from bestseller.services.concept_contract import (
            require_story_architecture_for_operation,
        )

        require_story_architecture_for_operation(
            project.metadata_json,
            target_chapters=int(project.target_chapters),
            operation=("foundation_rebuild" if force_foundation_replan else "outline_replan"),
        )
    _assert_project_not_blocked_for_structural_repair(
        project,
        project_slug=project.slug,
        operation="autowrite pipeline",
        allow_structural_repair=allow_outline_replan,
    )
    if _mark_project_autowrite_started(project):
        await _checkpoint_commit(session)

    # Resume: check if planning artifact already exists. Short books can also
    # land in a partial-planning state: foundation artifacts and VOLUME_PLAN are
    # approved, but the first per-volume chapter outline failed before the
    # merged CHAPTER_OUTLINE_BATCH was imported. In that case the non-progressive
    # path would rerun generate_novel_plan from BookSpec, wasting tokens and
    # risking drift. Delegate to the progressive resume loop so it skips the
    # foundation and continues at volume outline generation.
    existing_plan_artifact = await get_latest_approved_planning_artifact(
        session,
        settings=settings, project_id=project.id,
        artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
    )
    if existing_plan_artifact is None and settings.pipeline.resume_enabled:
        existing_volume_plan_artifact = await get_latest_approved_planning_artifact(
            session,
            settings=settings, project_id=project.id,
            artifact_type=ArtifactType.VOLUME_PLAN,
        )
        if existing_volume_plan_artifact is not None:
            _emit_progress(
                progress,
                "planning_resume_rerouted_progressive",
                {
                    "project_slug": project.slug,
                    "reason": "volume_plan_exists_without_chapter_outline_batch",
                },
            )
            return await run_progressive_autowrite_pipeline(
                session,
                settings,
                project_payload=project_payload,
                premise=premise,
                requested_by=requested_by,
                export_markdown=export_markdown,
                auto_repair_on_attention=auto_repair_on_attention,
                progress=progress,
                allow_outline_replan=allow_outline_replan,
                force_foundation_replan=force_foundation_replan,
            )
    if (
        existing_plan_artifact is not None
        and settings.pipeline.resume_enabled
        and not allow_outline_replan
    ):
        _emit_progress(
            progress,
            "planning_skipped_resume",
            {"project_slug": project.slug, "reason": "planning artifacts already exist"},
        )
        # Create a minimal planning result placeholder for downstream references
        from bestseller.domain.planning import NovelPlanningResult

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
            progress=progress,
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

    completed_bible_run = (
        await get_latest_completed_workflow_run(
            session,
            project_id=project.id,
            workflow_type=WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        )
        if existing_plan_artifact is not None and settings.pipeline.resume_enabled
        else None
    )
    if (
        completed_bible_run is not None
        and not await _completed_story_bible_materialization_is_reusable(
        session, completed_bible_run
        )
    ):
        _emit_progress(
            progress,
            "story_bible_materialization_resume_invalidated",
            {
                "project_slug": project.slug,
                "workflow_run_id": str(completed_bible_run.id),
                "reason": "completed_marker_outputs_missing",
            },
        )
        completed_bible_run = None
    if completed_bible_run is not None:
        from bestseller.domain.story_bible import StoryBibleMaterializationResult

        story_bible_result = StoryBibleMaterializationResult(
            workflow_run_id=completed_bible_run.id,
            project_id=project.id,
        )
        _emit_progress(
            progress,
            "story_bible_materialization_skipped_resume",
            {"project_slug": project.slug, "workflow_run_id": str(completed_bible_run.id)},
        )
    else:
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

    completed_outline_run = (
        await get_latest_completed_workflow_run(
            session,
            project_id=project.id,
            workflow_type=WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE,
        )
        if existing_plan_artifact is not None and settings.pipeline.resume_enabled
        else None
    )
    if completed_outline_run is not None:
        from bestseller.domain.workflow import WorkflowMaterializationResult

        outline_result = WorkflowMaterializationResult(
            workflow_run_id=completed_outline_run.id,
            project_id=project.id,
            batch_name="resume-reused-outline",
            chapters_created=0,
            scenes_created=0,
        )
        _emit_progress(
            progress,
            "outline_materialization_skipped_resume",
            {"project_slug": project.slug, "workflow_run_id": str(completed_outline_run.id)},
        )
    else:
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

    completed_graph_run = (
        await get_latest_completed_workflow_run(
            session,
            project_id=project.id,
            workflow_type=WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH,
        )
        if existing_plan_artifact is not None and settings.pipeline.resume_enabled
        else None
    )
    if completed_graph_run is not None:
        from bestseller.domain.narrative import NarrativeGraphMaterializationResult

        narrative_graph_result = NarrativeGraphMaterializationResult(
            workflow_run_id=completed_graph_run.id,
            project_id=project.id,
        )
        _emit_progress(
            progress,
            "narrative_graph_materialization_skipped_resume",
            {"project_slug": project.slug, "workflow_run_id": str(completed_graph_run.id)},
        )
    else:
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

    completed_tree_run = (
        await get_latest_completed_workflow_run(
            session,
            project_id=project.id,
            workflow_type=WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_TREE,
        )
        if existing_plan_artifact is not None and settings.pipeline.resume_enabled
        else None
    )
    if completed_tree_run is not None:
        from bestseller.domain.narrative_tree import NarrativeTreeMaterializationResult

        narrative_tree_result = NarrativeTreeMaterializationResult(
            workflow_run_id=completed_tree_run.id,
            project_id=project.id,
        )
        _emit_progress(
            progress,
            "narrative_tree_materialization_skipped_resume",
            {"project_slug": project.slug, "workflow_run_id": str(completed_tree_run.id)},
        )
    else:
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
    from bestseller.services.creation_configuration import read_window
    if read_window(project) is not None:
        from bestseller.services.execution_windows import as_autowrite_window_result
        return as_autowrite_window_result(project, project_result,
            planning_workflow_run_id=planning_result.workflow_run_id)
    repair_result = None
    # Only suppress auto-repair for scene-machine-blocked chapters in the legacy
    # hard-pause mode. In soft-continue mode (default) we WANT the framework's own
    # project-repair pass to engage on the flagged chapters instead of leaving the
    # "attention" verdict with no automatic remediation.
    skip_auto_repair_for_scene_block = (
        project_result.requires_human_review
        and getattr(settings.pipeline, "whole_book_pause_on_scene_review", False)
        and await _project_has_scene_machine_blocked_chapter(session, project.id)
    )
    if skip_auto_repair_for_scene_block:
        _emit_progress(
            progress,
            "auto_repair_skipped_scene_machine_blocked",
            {
                "project_slug": project.slug,
                "project_workflow_run_id": str(project_result.workflow_run_id),
            },
        )
    if (
        project_result.requires_human_review
        and auto_repair_on_attention
        and not skip_auto_repair_for_scene_block
    ):
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
        # One pass rarely settles every chapter. Keep going in this run rather
        # than leaving the book for the next self-heal sweep.
        repair_result = await _drive_repair_to_closure(
            session,
            settings,
            project,
            requested_by=requested_by,
            export_markdown=export_markdown,
            progress=progress,
            first_result=repair_result,
        )

    # No actionable repair does not resolve the pipeline's original blocker.
    # Keep the attempt receipt, but preserve the unresolved writing result.
    repair_final_result = (
        repair_result
        if repair_result is not None and repair_result.final_verdict != "no_actionable_repair"
        else None
    )
    final_review_report_id = (
        repair_final_result.review_report_id
        if repair_final_result is not None
        else project_result.review_report_id
    )
    final_quality_score_id = (
        repair_final_result.quality_score_id
        if repair_final_result is not None
        else project_result.quality_score_id
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
    final_verdict = (
        repair_final_result.final_verdict
        if repair_final_result is not None
        else project_result.final_verdict
    )
    final_requires_human_review = (
        repair_final_result.requires_human_review
        if repair_final_result is not None
        else project_result.requires_human_review
    )
    output_dir = (Path(settings.output.base_dir) / project.slug).resolve()
    output_files = _collect_output_files(output_dir)
    closure_draft_exported = bool(
        final_output_path
        and Path(final_output_path).name == "project-draft-with-quality-debt.md"
    )
    if closure_draft_exported:
        final_requires_human_review = True
        if final_verdict in {None, "pass"}:
            final_verdict = "attention"
    export_status = (
        "draft_exported_requires_human_review"
        if closure_draft_exported
        else "exported_requires_human_review"
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


@pipeline_operation_stage
@project_prompt_audit_scope
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
    allow_outline_replan: bool = False,
    force_foundation_replan: bool = False,
) -> AutowriteResult:
    """Progressive planning pipeline: Foundation → per-volume (plan → write → feedback) loop.

    Characters and world evolve with the story — each volume's planning is
    informed by feedback from the previous volume's actual writing output.
    """
    from bestseller.services.planning_context import (
        collect_volume_writing_feedback,
        summarize_volume_feedback,
    )

    project = await get_project_by_slug(session, project_payload.slug)
    if project is None:
        _emit_progress(progress, "project_creation_started", {"project_slug": project_payload.slug})
        project = await create_project(session, project_payload, settings)
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "project_creation_completed",
            {"project_slug": project.slug, "project_id": str(project.id)},
        )
    bind_resolved_project_prompt_audit(
        project.id, reason="Progressive autowrite project request and paid-return custody",
    )
    from bestseller.services.creation_configuration import project_settings, read_window, filter_window
    settings = project_settings(settings, project)
    execution_window = read_window(project)
    from bestseller.services.execution_windows import completed_window_resume, as_autowrite_window_result
    if execution_window is not None and not (allow_outline_replan or force_foundation_replan):
        cached_window = await completed_window_resume(session, settings, project,
            export_markdown=export_markdown, progress=progress)
        if cached_window is not None:
            return as_autowrite_window_result(project, cached_window)
    from bestseller.services.concept_contract_resume_repair import (
        ensure_project_concept_contract_lineage,
    )

    contract_repair = await ensure_project_concept_contract_lineage(session, settings, project)
    if contract_repair is not None and contract_repair.status == "repaired":
        force_foundation_replan = True
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "concept_contract_lineage_repaired",
            {"project_slug": project.slug, **contract_repair.to_dict()},
        )
    if (project.metadata_json or {}).get("foundation_truth_status") == "superseded_pending_materialization":
        force_foundation_replan = True
    if (
        allow_outline_replan
        and settings.pipeline.resume_enabled
        and not force_foundation_replan
        and await _world_snapshot_requires_foundation_repair(session, project=project, settings=settings)
    ):
        force_foundation_replan = True
        _emit_progress(
            progress, "foundation_snapshot_repair_required",
            {"project_slug": project.slug, "reason": "stale_or_incomplete_foundation"},
        )
    if force_foundation_replan:
        # Persist before any replacement artifact is written. If generation
        # fails midway, a newer WorldSpec plus an older successful readiness
        # report must not make the next resume skip the unfinished rebuild.
        metadata = dict(project.metadata_json or {})
        metadata["foundation_truth_status"] = "superseded_pending_materialization"
        project.metadata_json = metadata
        await _checkpoint_commit(session)
    if await _clear_auto_resumable_project_pause(session, project):
        await _checkpoint_commit(session)
    if allow_outline_replan or force_foundation_replan:
        from bestseller.services.concept_contract import (
            require_story_architecture_for_operation,
        )

        require_story_architecture_for_operation(
            project.metadata_json,
            target_chapters=int(project.target_chapters),
            operation=("foundation_rebuild" if force_foundation_replan else "outline_replan"),
        )
    _assert_project_not_blocked_for_structural_repair(
        project,
        project_slug=project.slug,
        operation="progressive autowrite pipeline",
        allow_structural_repair=allow_outline_replan or force_foundation_replan,
    )
    if _mark_project_autowrite_started(project):
        await _checkpoint_commit(session)

    # ── Phase A: Foundation Plan ──
    existing_volume_plan = await get_latest_approved_planning_artifact(
        session,
        settings=settings, project_id=project.id,
        artifact_type=ArtifactType.VOLUME_PLAN,
    )
    foundation_readiness = (project.metadata_json or {}).get("prewrite_readiness_report")
    if (
        existing_volume_plan is not None
        and settings.pipeline.resume_enabled
        and not force_foundation_replan
        # A volume artifact is stored before the foundation readiness gate.
        # Its presence cannot turn a failed foundation into a successful resume.
        and (
            not isinstance(foundation_readiness, Mapping)
            or foundation_readiness.get("passed", True) is True
        )
    ):
        _emit_progress(
            progress, "foundation_planning_skipped_resume", {"project_slug": project.slug}
        )
        from bestseller.domain.planning import NovelPlanningResult

        planning_result = NovelPlanningResult(
            workflow_run_id=existing_volume_plan.source_run_id or UUID(int=0),
            project_id=project.id,
            premise=premise,
            volume_count=0,
            chapter_count=0,
        )
    else:
        _emit_progress(progress, "foundation_planning_started", {"project_slug": project.slug})
        planning_result = await generate_foundation_plan(
            session,
            settings,
            project.slug,
            premise,
            requested_by=requested_by,
            progress=progress,
        )
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "foundation_planning_completed",
            {
            "project_slug": project.slug,
            "workflow_run_id": str(planning_result.workflow_run_id),
            "volume_count": planning_result.volume_count,
            },
        )

    if not force_foundation_replan:
        from bestseller.services.chapter_outline_readiness import current_terminal_prefix_blocker
        prefix_blocker = await current_terminal_prefix_blocker(session, settings, project=project)
        if prefix_blocker is not None:
            _emit_progress(progress, "current_outline_prefix_requires_decision", prefix_blocker)
            error = ProjectRepairPauseError(
                "current_source_prefix_rejected: workflow=" + prefix_blocker["workflow_run_id"]
                + "; existing candidate/repair history retained; resume its bound decision before planning a new window")
            error.prefix_resume_blocker = prefix_blocker
            raise error

    if force_foundation_replan:
        latest_cast_for_supersession = await get_latest_approved_planning_artifact(
            session,
            settings=settings, project_id=project.id,
            artifact_type=ArtifactType.CAST_SPEC,
        )
        if latest_cast_for_supersession is None or not isinstance(
            latest_cast_for_supersession.content, dict
        ):
            raise ValueError("foundation replan did not produce an active cast contract")
        from bestseller.services.book_design import (
            ensure_project_book_design_snapshot,
        )
        from bestseller.services.story_bible import (
            supersede_materialized_cast_for_foundation,
        )

        supersession_audit = await supersede_materialized_cast_for_foundation(
            session,
            project,
            cast_spec_content=latest_cast_for_supersession.content,
            source_artifact_id=latest_cast_for_supersession.id,
        )
        # The foundation was generated against this locked snapshot. Runtime
        # cast cleanup must not rebuild its source from materialized entities.
        ensure_project_book_design_snapshot(project)
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "foundation_truth_superseded",
            {
                "project_slug": project.slug,
                **supersession_audit,
            },
        )

    # ── Materialize story bible from foundation ──
    # Resume guard: re-running materialization on every restart is non-idempotent
    # because the L2 bible-completeness gate may now reject content that was
    # previously accepted (gate criteria can tighten over time). Once a project
    # has a completed bible materialization the persisted DB state is already
    # the source of truth — re-running risks looping forever on resumes.
    existing_bible_run = await get_latest_completed_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
    )
    if (
        existing_bible_run is not None
        and not await _completed_story_bible_materialization_is_reusable(
        session, existing_bible_run
        )
    ):
        _emit_progress(
            progress,
            "story_bible_materialization_resume_invalidated",
            {
            "project_slug": project.slug,
            "workflow_run_id": str(existing_bible_run.id),
            "reason": "completed_marker_outputs_missing",
            },
        )
        existing_bible_run = None
    if (
        existing_bible_run is not None
        and settings.pipeline.resume_enabled
        and not force_foundation_replan
    ):
        _emit_progress(
            progress,
            "story_bible_materialization_skipped_resume",
            {
            "project_slug": project.slug,
            "workflow_run_id": str(existing_bible_run.id),
            },
        )
        from bestseller.domain.story_bible import StoryBibleMaterializationResult

        story_bible_result = StoryBibleMaterializationResult(
            workflow_run_id=existing_bible_run.id,
            project_id=project.id,
        )
    else:
        _emit_progress(
            progress, "story_bible_materialization_started", {"project_slug": project.slug}
        )
        story_bible_result = await materialize_latest_story_bible(
            session, project.slug, requested_by=requested_by
        )
        if force_foundation_replan:
            refreshed_metadata = dict(project.metadata_json or {})
            refreshed_metadata["foundation_truth_status"] = "active"
            refreshed_metadata["foundation_truth_materialized_run_id"] = str(
                story_bible_result.workflow_run_id
            )
            project.metadata_json = refreshed_metadata
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "story_bible_materialization_completed",
            {
                "project_slug": project.slug,
                "workflow_run_id": str(story_bible_result.workflow_run_id),
            },
        )

    if getattr(settings.pipeline, "require_foundation_identity_lock", True):
        await ensure_project_identity_manifest(
            session,
            project,
            project_slug=project.slug,
        )
        await _enforce_book_design_consistency(session, project)
        await _checkpoint_commit(session)

    # ── Load planning artifacts for volume loop ──
    book_spec_art = await get_latest_approved_planning_artifact(
        session, settings=settings, project_id=project.id, artifact_type=ArtifactType.BOOK_SPEC
    )
    world_spec_art = await _get_current_world_spec_artifact(session, project=project, settings=settings)
    cast_spec_art = await get_latest_approved_planning_artifact(
        session, settings=settings, project_id=project.id, artifact_type=ArtifactType.CAST_SPEC
    )
    volume_plan_art = await get_latest_approved_planning_artifact(
        session, settings=settings, project_id=project.id, artifact_type=ArtifactType.VOLUME_PLAN
    )

    book_spec_payload = book_spec_art.content if book_spec_art else {}
    world_spec_payload = world_spec_art.content if world_spec_art else {}
    cast_spec_payload = cast_spec_art.content if cast_spec_art else {}
    volume_plan_payload = volume_plan_art.content if volume_plan_art else []

    # An outline-only replan intentionally reuses foundation artifacts.  If a
    # repaired final premise changed a planner-inferred protagonist, scrub the
    # superseded name from those reused payloads before they enter outline
    # prompts, and persist the repaired versions so later resumes cannot reload
    # the stale identity.
    if allow_outline_replan:
        from bestseller.services.book_design import extract_creation_protagonist_name
        from bestseller.services.planner import _repair_protagonist_name_drift_for_planner

        canonical_name = extract_creation_protagonist_name(project.metadata_json or {})
        repaired_book_spec = _repair_protagonist_name_drift_for_planner(
            project,
            dict(book_spec_payload) if isinstance(book_spec_payload, Mapping) else {},
            protagonist_name=canonical_name,
            artifact_type=ArtifactType.BOOK_SPEC.value,
        )
        if repaired_book_spec != book_spec_payload:
            book_spec_payload = repaired_book_spec
            await import_planning_artifact(
                session,
                project.slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.BOOK_SPEC,
                    content=book_spec_payload,
                ),
            )
        repaired_cast_spec = _repair_protagonist_name_drift_for_planner(
            project,
            dict(cast_spec_payload) if isinstance(cast_spec_payload, Mapping) else {},
            protagonist_name=canonical_name,
            artifact_type=ArtifactType.CAST_SPEC.value,
        )
        if repaired_cast_spec != cast_spec_payload:
            cast_spec_payload = repaired_cast_spec
            await import_planning_artifact(
                session,
                project.slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.CAST_SPEC,
                    content=cast_spec_payload,
                ),
            )
        await _checkpoint_commit(session)

    # Normalize volume plan. Some recovered/legacy plans store chapter_range
    # but omit chapter_count_target; the planner can derive the count, so use
    # its normalization here before the volume loop makes skip/replan decisions.
    from bestseller.services.planner import _normalize_volume_plan_payload

    volume_plan_list = _normalize_volume_plan_payload(volume_plan_payload)

    rolling_enabled = getattr(settings.pipeline, "enable_rolling_outline", True)
    preferred_window_size = int(getattr(settings.pipeline, "rolling_outline_window_size", 8) or 8)
    execution_plan_list = (
        _expand_volume_plan_into_rolling_windows(
            volume_plan_list,
            window_size=preferred_window_size,
        )
        if rolling_enabled
        else [dict(item) for item in volume_plan_list]
    )
    rolling_schedule = (
        [
        {
            "window_start": int(item["start_chapter_number"]),
            "window_end": int(item["end_chapter_number"]),
            "volume_number": int(item["volume_number"]),
            "window_index": int(item["rolling_window_index"]),
        }
        for item in execution_plan_list
        ]
        if rolling_enabled
        else []
    )
    rolling_prefix_scope = None
    if rolling_enabled:
        from bestseller.services.book_design import ensure_project_book_design_snapshot
        from bestseller.services.rolling_outline import (
            build_macro_plan,
            build_rolling_outline_plan,
            load_rolling_outline_plan,
            promote_rolling_outline,
            rolling_window_schedule_hash,
        )

        snapshot = ensure_project_book_design_snapshot(project)
        project_metadata = dict(project.metadata_json or {})
        stored_macro = project_metadata.get("macro_outline_plan")
        stored_rolling = project_metadata.get("rolling_outline_plan")
        stored_schedule = project_metadata.get("rolling_outline_windows")
        if allow_outline_replan and (stored_macro is not None or stored_rolling is not None):
            try:
                if not isinstance(stored_macro, Mapping) or not isinstance(stored_rolling, Mapping):
                    raise ValueError("stored rolling outline is incomplete")
                if stored_schedule != rolling_schedule:
                    raise ValueError("rolling execution window schedule mismatch")
                if project_metadata.get(
                    "rolling_outline_windows_hash"
                ) != rolling_window_schedule_hash(rolling_schedule):
                    raise ValueError("rolling execution window schedule hash mismatch")
                load_rolling_outline_plan(
                    stored_macro,
                    stored_rolling,
                    source_snapshot_hash=snapshot.source_hash,
                )
            except (TypeError, ValueError) as exc:
                project_metadata = _reset_stale_rolling_outline_for_explicit_replan(
                    project_metadata,
                    reason=str(exc),
                )
                project.metadata_json = project_metadata
                stored_macro = None
                stored_rolling = None
                stored_schedule = None
        if isinstance(stored_macro, Mapping) and isinstance(stored_rolling, Mapping):
            try:
                if not allow_outline_replan:
                    from bestseller.services.rolling_outline import bind_retained_rolling_schedule
                    execution_plan_list = bind_retained_rolling_schedule(
                        volume_plan_list, stored_schedule,
                        project_metadata.get("rolling_outline_windows_hash"))
                    rolling_schedule = [dict(row) for row in stored_schedule]
                if stored_schedule != rolling_schedule:
                    raise ValueError("rolling execution window schedule mismatch")
                if project_metadata.get(
                    "rolling_outline_windows_hash"
                ) != rolling_window_schedule_hash(rolling_schedule):
                    raise ValueError("rolling execution window schedule hash mismatch")
                from bestseller.services.chapter_outline_readiness import load_rolling_plan_for_prefix_consumption
                _, retained_plan, rolling_prefix_scope = await load_rolling_plan_for_prefix_consumption(
                    session, settings, project=project, raw_macro=stored_macro, raw_plan=stored_rolling,
                    source_snapshot_hash=snapshot.source_hash)
                if not any(row["window_start"] == retained_plan.window_start
                           and row["window_end"] == retained_plan.window_end for row in rolling_schedule):
                    raise ValueError("rolling active window is absent from retained schedule")
            except (TypeError, ValueError) as exc:
                project_metadata.update(
                    {
                        "rolling_outline_status": "needs_replan",
                        "rolling_outline_integrity_error": str(exc),
                        "planning_status": "needs_replan",
                        "production_paused": True,
                        "production_pause_reason": "rolling_outline_invalid",
                        "generation_resume_blocked_until_repair_audit": True,
                    }
                )
                project.metadata_json = project_metadata
                project.status = ProjectStatus.NEEDS_REPLAN.value
                await _checkpoint_commit(session)
                raise ProjectRepairPauseError(
                    f"Stored rolling outline is invalid; explicit replan is required. reason={exc}"
                ) from exc
        elif stored_macro is not None or stored_rolling is not None:
            project_metadata.update(
                {
                    "rolling_outline_status": "needs_replan",
                    "planning_status": "needs_replan",
                    "production_paused": True,
                    "production_pause_reason": "rolling_outline_incomplete",
                    "generation_resume_blocked_until_repair_audit": True,
                }
            )
            project.metadata_json = project_metadata
            project.status = ProjectStatus.NEEDS_REPLAN.value
            await _checkpoint_commit(session)
            raise ProjectRepairPauseError(
                "Stored rolling outline is incomplete; explicit replan is required."
            )
        else:
            legacy_detailed_chapters = await _load_project_chapters(session, project.id)
            if legacy_detailed_chapters and not allow_outline_replan:
                project_metadata.update(
                    {
                        "rolling_outline_status": "needs_replan",
                        "planning_status": "needs_replan",
                        "production_paused": True,
                        "production_pause_reason": "rolling_migration_requires_replan",
                        "generation_resume_blocked_until_repair_audit": True,
                        "legacy_detailed_chapter_count": len(legacy_detailed_chapters),
                    }
                )
                project.metadata_json = project_metadata
                project.status = ProjectStatus.NEEDS_REPLAN.value
                await _checkpoint_commit(session)
                raise ProjectRepairPauseError(
                    "Legacy full-detail outline cannot be promoted into rolling mode; "
                    "explicit outline replan is required."
                )
            if legacy_detailed_chapters:
                project_metadata["rolling_outline_replan_existing_planned_chapters"] = len(
                    legacy_detailed_chapters
                )
            macro_plan = build_macro_plan(_build_progressive_macro_slots(volume_plan_list))
            current_chapter = max(0, int(getattr(project, "current_chapter_number", 0) or 0))
            remaining = macro_plan.total_chapters - current_chapter
            if remaining > 0:
                initial_window = next(
                    (
                        item
                        for item in rolling_schedule
                        if int(item["window_start"]) == current_chapter + 1
                    ),
                    None,
                )
                if initial_window is None:
                    raise ProjectRepairPauseError(
                        "Rolling execution schedule does not continue from the current chapter."
                    )
                initial_window_size = (
                    int(initial_window["window_end"]) - int(initial_window["window_start"]) + 1
                )
                rolling_plan = promote_rolling_outline(
                    build_rolling_outline_plan(
                        macro_plan,
                        current_state_snapshot={
                            "current_chapter": current_chapter,
                            "facts": [],
                        },
                        next_macro_anchor=(
                            macro_plan.slots[current_chapter + initial_window_size].to_dict()
                            if current_chapter + initial_window_size < macro_plan.total_chapters
                            else "book_complete"
                        ),
                        source_snapshot_hash=snapshot.source_hash,
                        window_start=current_chapter + 1,
                        window_size=initial_window_size,
                        batch_size=int(
                            getattr(settings.pipeline, "rolling_outline_batch_size", 4) or 4
                        ),
                        confirmed_chapters=tuple(range(1, current_chapter + 1)),
                    ),
                    "approved",
                )
                project_metadata.update(
                    {
                        "macro_outline_plan": macro_plan.to_dict(),
                        "rolling_outline_plan": rolling_plan.to_dict(),
                        "rolling_outline_windows": rolling_schedule,
                        "rolling_outline_windows_hash": rolling_window_schedule_hash(
                            rolling_schedule
                        ),
                        "rolling_outline_status": "approved",
                    }
                )
                project.metadata_json = project_metadata
                await _checkpoint_commit(session)

    prior_feedback_summary: str | None = None
    prior_world_snapshot: str | None = None
    all_chapter_results: list[Any] = []
    # Global progress baseline across volumes.
    # Important: this is NOT "chapters written in this run". It tracks how many
    # chapters are already considered complete before entering each volume so
    # per-chapter `global_progress` remains monotonic in resume scenarios.
    #
    # Why this exists:
    # - `len(all_chapter_results)` only counts chapters freshly processed in the
    #   current loop.
    # - Fully-written volumes skipped by resume never extend that list.
    # - Passing `len(all_chapter_results)` as global offset under-reports
    #   progress (observed as 51/1200 while repairing chapter 400).
    global_completed_chapter_offset = 0
    total_volumes = len(volume_plan_list)
    # Initialize variables used after the loop to avoid UnboundLocalError
    outline_result = None
    narrative_graph_result = None
    narrative_tree_result = None
    vol_project_result = None
    blocked_volume_repair_required = False
    blocked_volume_final_verdict: str | None = None
    blocked_volume_repair_chapter_numbers: set[int] | None = None

    # Book-wide totals so the web UI can render progress across the entire
    # multi-volume run, not just the current volume.
    _emit_progress(
        progress,
        "progressive_autowrite_started",
        {
        "project_slug": project.slug,
        "volume_count": total_volumes,
        "project_chapter_count": project.target_chapters or 0,
        },
    )

    # ── Phase B: Per-volume loop ──
    for vol_idx, vol_entry in enumerate(execution_plan_list, start=1):
        if execution_window is not None:
            entry_start = int(vol_entry.get("start_chapter_number") or (vol_entry.get("chapter_range") or [1])[0])
            entry_end = int(vol_entry.get("end_chapter_number") or (vol_entry.get("chapter_range") or [project.target_chapters])[-1])
            if entry_end < execution_window.start or entry_start > execution_window.end:
                continue
        # Operator stop/pause checkpoint. Read fresh from ``book_production_control``
        # (never from a value captured before the loop) so a command issued while
        # this run is in flight takes effect at the next volume boundary instead
        # of only being noticed by the next self-heal sweep.
        try:
            _control = await load_control_state(session, project.id)
        except Exception:
            # Never let the stop check itself end a run. The authoritative
            # enforcement points are the worker entry guard and the self-heal
            # sweep; this one is a fast path for a book already in flight.
            logger.debug(
                "could not read production control for %s; continuing",
                project.slug,
                exc_info=True,
            )
            _control = None
        if _control is not None and _control.halted:
            logger.info(
                "autowrite halted by operator intent project=%s intent=%s reason=%s",
                project.slug,
                _control.intent.value,
                _control.reason,
            )
            _emit_progress(
                progress,
                "autowrite_halted_by_operator",
                {
                "project_slug": project.slug,
                **_control.to_payload(),
                },
            )
            break

        # Configuration-drift checkpoint. Freezing on the first volume and
        # re-verifying on every later one is what turns "chapters 51+ die for no
        # visible reason" into a named, actionable event.
        _drift = await _checkpoint_book_runtime_guard(session, settings, project, progress=progress)
        if _drift is not None and _drift.blocks_production:
            logger.warning(
                "autowrite halted by config drift project=%s detail=%s",
                project.slug,
                _drift.describe(),
            )
            break

        vol_num = int(vol_entry.get("volume_number", 0)) or vol_idx
        active_volume_plan = (
            _volume_plan_for_rolling_window(volume_plan_list, vol_entry)
            if rolling_enabled
            else volume_plan_list
        )

        resume_existing_chapter_numbers: set[int] | None = None
        resumed_verified_prefix = False
        used_resume_outline_chapters = False

        # Skip replanning for any already-materialized volume during resume.
        # A partial volume means "write/repair existing rows", not "generate
        # a fresh outline". Re-running generate_volume_plan against a drifted
        # volume_plan is what produced the xianxia-upgrade-1776137730 gap:
        # volume 1 was replanned at max(chapter_number)+1, first appending
        # 552-601 and then 602-651 instead of repairing the existing frontier.
        # Evidence is DB-only — the decision must not depend on plan targets
        # that the drift could have corrupted.
        if settings.pipeline.resume_enabled and not allow_outline_replan:
            if rolling_enabled:
                existing_numbers = await _chapter_numbers_in_volume(session, project.id, vol_num)
                window_start = int(vol_entry.get("start_chapter_number") or 0)
                window_end = int(vol_entry.get("end_chapter_number") or 0)
                expected_window_numbers = set(range(window_start, window_end + 1))
                if execution_window is not None:
                    # A validated opening/continuation scope can consume an
                    # approved prefix of a larger rolling window. Missing rows
                    # outside this invocation's scope are not partial writes.
                    expected_window_numbers &= set(range(execution_window.start, execution_window.end + 1))
                present_window_numbers = existing_numbers & expected_window_numbers
                if rolling_prefix_scope is not None:
                    present_window_numbers &= rolling_prefix_scope
                if (execution_window is not None and present_window_numbers
                        and present_window_numbers != expected_window_numbers):
                    from bestseller.services.chapter_outline_readiness import verify_materialized_prefix_for_resume
                    resumed_verified_prefix = await verify_materialized_prefix_for_resume(
                        session, settings, project=project, present_numbers=present_window_numbers,
                        expected_numbers=expected_window_numbers)
                if (present_window_numbers and present_window_numbers != expected_window_numbers
                        and not resumed_verified_prefix):
                    project.metadata_json = {
                        **(project.metadata_json or {}),
                        "rolling_outline_status": "needs_replan",
                        "planning_status": "needs_replan",
                        "production_paused": True,
                        "production_pause_reason": "rolling_window_partial_materialization",
                        "generation_resume_blocked_until_repair_audit": True,
                        "rolling_window_missing_chapters": sorted(
                            expected_window_numbers - present_window_numbers
                        ),
                    }
                    project.status = ProjectStatus.NEEDS_REPLAN.value
                    await _checkpoint_commit(session)
                    raise ProjectRepairPauseError(
                        "Rolling outline window was only partially materialized; "
                        "replan is required."
                    )
                if present_window_numbers == expected_window_numbers or resumed_verified_prefix:
                    # A window whose chapters are all settled is done: the
                    # rolling plan has already promoted the *next* window, so
                    # re-entering this one made ``run_project_pipeline`` select
                    # a window whose chapters do not exist yet and abort. The
                    # loop then never reached the window that actually needed
                    # planning, and a 30-chapter book sat at chapter 8 while
                    # both self-heal lanes handed it back and forth
                    # (2026-08-03, xianxia-upgrade-1785697772).
                    #
                    # "Behind the frontier" is decided by the same pointer the
                    # window selector uses to advance (``current_chapter >=
                    # window_end``), so the two can never disagree. An earlier
                    # attempt counted settled chapters instead, and a repair
                    # pass that flipped one chapter from ``quality_debt`` to
                    # ``blocked`` made the count 7-of-8 — the loop re-entered a
                    # finished window and aborted again. Repairing that one
                    # chapter is the repair lane's job, not this write loop's.
                    written_frontier = int(getattr(project, "current_chapter_number", 0) or 0)
                    if written_frontier >= window_end and not resumed_verified_prefix:
                        logger.info(
                            "Rolling window %d-%d is behind the written frontier "
                            "(chapter %d) — advancing to the next window.",
                            window_start,
                            window_end,
                            written_frontier,
                        )
                        _emit_progress(
                            progress,
                            "rolling_window_skipped_already_settled",
                            {
                                "project_slug": project.slug,
                                "volume_number": vol_num,
                                "window_start": window_start,
                                "window_end": window_end,
                                "written_frontier": written_frontier,
                            },
                        )
                        global_completed_chapter_offset = max(
                            global_completed_chapter_offset, window_end
                        )
                        continue
                    resume_existing_chapter_numbers = present_window_numbers
                    _emit_progress(
                        progress,
                        "rolling_window_planning_skipped_resume_existing_rows",
                        {
                            "project_slug": project.slug,
                            "volume_number": vol_num,
                            "window_start": window_start,
                            "window_end": window_end,
                        },
                    )
            else:
                fully_written, written_count, total_count = await _volume_fully_written(
                    session,
                    project.id,
                    vol_num,
                )
                if fully_written:
                    logger.info(
                        "Volume %d already fully written (%d/%d chapters) — skipping replanning.",
                        vol_num,
                        written_count,
                        total_count,
                    )
                    _emit_progress(
                        progress,
                        "volume_planning_skipped_resume",
                        {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                        "written": written_count,
                        "total": total_count,
                        },
                    )
                    global_completed_chapter_offset += int(written_count or 0)
                    continue
                if total_count > 0:
                    existing_numbers = await _chapter_numbers_in_volume(
                        session, project.id, vol_num
                    )
                    if existing_numbers:
                        resume_existing_chapter_numbers = existing_numbers
                        logger.info(
                            "Volume %d already materialized (%d/%d written, %d total) — "
                            "skipping replanning and writing existing chapter rows.",
                            vol_num,
                            written_count,
                            total_count,
                            len(existing_numbers),
                        )
                        _emit_progress(
                            progress,
                            "volume_planning_skipped_resume_existing_rows",
                            {
                            "project_slug": project.slug,
                            "volume_number": vol_num,
                            "written": written_count,
                            "total": total_count,
                            "chapter_count": len(existing_numbers),
                            },
                        )

        if resume_existing_chapter_numbers is None:
            # Plan this volume (cast expansion + world disclosure + outline).
            expected_volume_chapters = int(vol_entry.get("chapter_count_target") or 0)
            resume_outline_chapters: list[Any] = []
            if settings.pipeline.resume_enabled and not rolling_enabled:
                resume_outline_chapters = await _resume_outline_chapters_for_volume(
                    session,
                    project_id=project.id,
                    volume_number=vol_num,
                    expected_count=expected_volume_chapters,
                )
                if resume_outline_chapters:
                    _emit_progress(
                        progress,
                        "volume_planning_skipped_resume_existing_outline",
                        {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                        "chapter_count": len(resume_outline_chapters),
                        },
                    )

            if resume_outline_chapters:
                vol_chapters = resume_outline_chapters
                used_resume_outline_chapters = True
            else:
                _emit_progress(
                    progress,
                    "volume_planning_started",
                    {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                        "total_volumes": total_volumes,
                    },
                )

                try:
                    vol_plan_result = await generate_volume_plan(
                        session,
                        settings,
                        project.slug,
                        vol_num,
                        book_spec=book_spec_payload,
                        world_spec=world_spec_payload,
                        cast_spec=cast_spec_payload,
                        volume_plan=active_volume_plan,
                        prior_feedback_summary=prior_feedback_summary,
                        prior_world_snapshot=prior_world_snapshot,
                        requested_by=requested_by,
                        progress=progress,
                    )
                except PlannerFallbackError as exc:
                    if not _is_volume_outline_auto_repairable(exc):
                        raise
                    auto_repair_reason = _volume_outline_auto_repair_reason(exc)
                    repair_constraints = _volume_outline_auto_repair_constraints(
                        language=project.language,
                        volume_number=vol_num,
                        expected_count=expected_volume_chapters,
                        error_message=str(exc),
                    )
                    _emit_progress(
                        progress,
                        "volume_planning_auto_repair_started",
                        {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                        "reason": auto_repair_reason,
                        "expected_count": expected_volume_chapters,
                        },
                    )
                    logger.warning(
                        "Volume %d planning failed %s for project '%s'; "
                        "retrying once with auto-repair constraints.",
                        vol_num,
                        auto_repair_reason,
                        project.slug,
                    )
                    vol_plan_result = await generate_volume_plan(
                        session,
                        settings,
                        project.slug,
                        vol_num,
                        book_spec=book_spec_payload,
                        world_spec=world_spec_payload,
                        cast_spec=cast_spec_payload,
                        volume_plan=active_volume_plan,
                        prior_feedback_summary=prior_feedback_summary,
                        prior_world_snapshot=prior_world_snapshot,
                        requested_by=requested_by,
                        extra_constraints=repair_constraints,
                        progress=progress,
                    )
                    _emit_progress(
                        progress,
                        "volume_planning_auto_repair_completed",
                        {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                        "reason": auto_repair_reason,
                        "chapter_count": vol_plan_result.chapter_count,
                        },
                    )
                await _checkpoint_commit(session)

                _emit_progress(
                    progress,
                    "volume_planning_completed",
                    {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                    "chapter_count": vol_plan_result.chapter_count,
                    "new_characters": vol_plan_result.new_characters_introduced,
                    },
                )

                # Refresh canonical world/cast specs materialized by generate_volume_plan
                # so this volume's writing and the next volume's planning both see the
                # latest canon instead of the foundation snapshot.
                _emit_progress(
                    progress,
                    "story_bible_refresh_started",
                    {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                    },
                )
                story_bible_result = await materialize_latest_story_bible(
                    session,
                    project.slug,
                    requested_by=requested_by,
                )
                await _checkpoint_commit(session)
                _emit_progress(
                    progress,
                    "story_bible_refresh_completed",
                    {
                    "project_slug": project.slug,
                    "volume_number": vol_num,
                    "workflow_run_id": str(story_bible_result.workflow_run_id),
                    },
                )

                latest_world_spec = await _get_current_world_spec_artifact(session, project=project, settings=settings)
                latest_cast_spec = await get_latest_approved_planning_artifact(
                    session,
                    settings=settings, project_id=project.id,
                    artifact_type=ArtifactType.CAST_SPEC,
                )
                if latest_world_spec and isinstance(latest_world_spec.content, dict):
                    world_spec_payload = latest_world_spec.content
                if latest_cast_spec and isinstance(latest_cast_spec.content, dict):
                    cast_spec_payload = latest_cast_spec.content

                # Materialize the per-volume outline into the combined CHAPTER_OUTLINE_BATCH
                # so the existing chapter writing pipeline can pick it up.
                #
                # (2026-08-03) Fold EVERY volume-outline version, oldest first —
                # not just the latest. The rolling planner writes one
                # VOLUME_CHAPTER_OUTLINE version per batch (v1=ch1-3, v2=ch4-6,
                # v3=ch7-8), and this merge runs once at the end of the volume.
                # Reading only the newest version therefore materialized the
                # LAST batch alone: 《雾街债主》 and its A/B twin both ended up
                # with chapters [7,8] and nothing else, which left the approved
                # rolling window incomplete, which made every write attempt raise
                # "Approved rolling outline window is not fully materialized" and
                # self-heal re-queue the same replan indefinitely.
                # `_merge_progressive_outline_batch` makes each incoming batch
                # authoritative from its own first chapter onward, so folding in
                # ascending version order is order-safe and idempotent: replaying
                # v1 drops the tail, and v2/v3 immediately restore it.
                vol_outline_versions = await _load_approved_volume_chapter_outline_artifacts(
                    session,
                    project_id=project.id,
                )
                vol_outline_art = vol_outline_versions[-1] if vol_outline_versions else None
                vol_chapters = []
                if vol_outline_art and vol_outline_art.content:
                    # Merge volume outline into cumulative CHAPTER_OUTLINE_BATCH
                    existing_batch_art = await get_latest_approved_planning_artifact(
                        session,
                        settings=settings, project_id=project.id,
                        artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                    )
                    merged_chapters = _outline_content_chapters(
                        existing_batch_art.content if existing_batch_art else None
                    )
                    for _version in vol_outline_versions:
                        _version_chapters = _outline_content_chapters(_version.content)
                        if not _version_chapters:
                            continue
                        merged_chapters = _merge_progressive_outline_batch(
                            merged_chapters,
                            _version_chapters,
                        )
                    vol_chapters = _outline_content_chapters(vol_outline_art.content)
                    merged = {
                        "batch_name": "progressive-merged-outline",
                        "chapters": merged_chapters,
                    }
                    await import_planning_artifact(
                        session,
                        project.slug,
                        PlanningArtifactCreate(
                            artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                            content=merged,
                        ),
                    )
                    await _checkpoint_commit(session)

                # The flag covers the whole run, but the pending gate is released
                # once at the first approved window; refresh before deciding so
                # later windows do not re-assert an already released gate.
                if allow_outline_replan:
                    await session.refresh(project)
                    if (project.metadata_json or {}).get("outline_replan_in_progress"):
                        if not _release_approved_outline_replan_gate(
                            project,
                            vol_outline_art,
                        ):
                            raise ProjectRepairPauseError(
                                "Outline replan did not produce a newer approved outline; "
                                "prose remains blocked."
                            )
                        await _checkpoint_commit(session)
                        _emit_progress(
                            progress,
                            "outline_replan_gate_released",
                            {
                                "project_slug": project.slug,
                                "volume_number": vol_num,
                                "outline_version": int(getattr(vol_outline_art, "version_no", 0) or 0),
                            },
                        )

            # Materialize outline + narrative structures for this volume's chapters
            _emit_progress(
                progress, "outline_materialization_started", {"project_slug": project.slug}
            )
            outline_result = await materialize_latest_chapter_outline_batch(
                session, project.slug, requested_by=requested_by
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
                progress, "narrative_graph_materialization_started", {"project_slug": project.slug}
            )
            narrative_graph_result = await materialize_latest_narrative_graph(
                session, project.slug, requested_by=requested_by
            )
            await _checkpoint_commit(session)
            _emit_progress(
                progress,
                "narrative_graph_materialization_completed",
                {
                    "project_slug": project.slug,
                    "workflow_run_id": str(narrative_graph_result.workflow_run_id),
                },
            )

            _emit_progress(
                progress, "narrative_tree_materialization_started", {"project_slug": project.slug}
            )
            narrative_tree_result = await materialize_latest_narrative_tree(
                session, project.slug, requested_by=requested_by
            )
            await _checkpoint_commit(session)
            _emit_progress(
                progress,
                "narrative_tree_materialization_completed",
                {
                    "project_slug": project.slug,
                    "workflow_run_id": str(narrative_tree_result.workflow_run_id),
                },
            )

            current_volume_chapter_numbers = {
                ch.get("chapter_number")
                for ch in vol_chapters
                if isinstance(ch, dict) and isinstance(ch.get("chapter_number"), int)
            }
        else:
            current_volume_chapter_numbers = resume_existing_chapter_numbers

        if execution_window is not None:
            current_volume_chapter_numbers = filter_window(project, current_volume_chapter_numbers)
            if not current_volume_chapter_numbers:
                continue
        # Write this volume's chapters via the existing project pipeline.
        # In multi-volume mode we deliberately skip the per-volume full-book
        # markdown export:
        #   1. The preflight hygiene check scans the full project, so a single
        #      natural-prose false positive anywhere would abort every volume.
        #   2. The per-chapter markdown files are still written incrementally
        #      by assemble_chapter_draft, and a final best-effort project
        #      export runs once after the whole loop completes.
        _emit_progress(
            progress,
            "volume_writing_started",
            {
                "project_slug": project.slug,
                "volume_number": vol_num,
            "total_volumes": total_volumes,
            },
        )
        if resume_existing_chapter_numbers is not None or used_resume_outline_chapters:
            await _refresh_stale_truth_materializations_for_resume(
                session,
                settings,
                project,
                requested_by=requested_by,
                progress=progress,
            )
        vol_project_result = await run_project_pipeline(
            session,
            settings,
            project.slug,
            requested_by=requested_by,
            materialize_story_bible=False,
            materialize_outline=False,
            materialize_narrative_graph=False,
            materialize_narrative_tree=False,
            export_markdown=False,
            progress=progress,
            # Use the true completed baseline, not just chapters written in this
            # process, so global progress stays aligned with DB reality.
            global_chapter_offset=global_completed_chapter_offset,
            total_target_chapters=project.target_chapters or 0,
            current_volume_number=vol_num,
            total_volumes=total_volumes,
            chapter_numbers=current_volume_chapter_numbers,
        )
        await _checkpoint_commit(session)
        if execution_window is not None:
            all_chapter_results.extend(vol_project_result.chapter_results)
            if export_markdown and vol_project_result.final_verdict == "execution_window_completed":
                from bestseller.services.exports import export_execution_window_markdown
                artifact, path = await export_execution_window_markdown(session, settings, project.slug,
                    created_by_run_id=vol_project_result.workflow_run_id)
                vol_project_result.export_artifact_id = artifact.id
                vol_project_result.output_path = str(path)
                await _checkpoint_commit(session)
            if (vol_project_result.final_verdict == "execution_window_completed"
                    or vol_project_result.requires_human_review):
                vol_project_result.chapter_results = all_chapter_results
                return as_autowrite_window_result(project, vol_project_result,
                    planning_workflow_run_id=planning_result.workflow_run_id)
            if resumed_verified_prefix:
                _emit_progress(progress, "rolling_prefix_consumed_window_still_pending", {
                    "project_slug": project.slug,
                    "consumed_scope": sorted(resume_existing_chapter_numbers or []),
                    "pending_scope": sorted(expected_window_numbers - present_window_numbers),
                })
                # Never leap over the unmaterialized remainder to a later
                # rolling window. Completion still uses adopted body/knowledge.
                break
            continue
        volume_fully_written_after_run = False
        volume_written_count_after_run = 0
        volume_total_count_after_run = 0
        if settings.pipeline.resume_enabled:
            (
                volume_fully_written_after_run,
                volume_written_count_after_run,
                volume_total_count_after_run,
            ) = await _volume_fully_written(session, project.id, vol_num)
        # For the next volume's baseline, add both:
        # 1) chapters already written in this volume before this run; and
        # 2) chapters processed by this run in this volume.
        if rolling_enabled:
            global_completed_chapter_offset = max(
                global_completed_chapter_offset,
                int(getattr(project, "current_chapter_number", 0) or 0),
            )
        elif settings.pipeline.resume_enabled:
            if volume_fully_written_after_run:
                global_completed_chapter_offset += int(volume_written_count_after_run or 0)
            else:
                global_completed_chapter_offset += len(vol_project_result.chapter_results)
        else:
            global_completed_chapter_offset += len(vol_project_result.chapter_results)
        all_chapter_results.extend(vol_project_result.chapter_results)
        _emit_progress(
            progress,
            "volume_writing_completed",
            {
                "project_slug": project.slug,
                "volume_number": vol_num,
            "chapters_written": len(vol_project_result.chapter_results),
            "written": volume_written_count_after_run,
            "total": volume_total_count_after_run,
            "fully_written": volume_fully_written_after_run,
            },
        )
        volume_incomplete_after_run = (
            settings.pipeline.resume_enabled
            and volume_total_count_after_run > 0
            and not volume_fully_written_after_run
        )
        quality_debt_only_review = False
        if vol_project_result.requires_human_review and not volume_incomplete_after_run:
            quality_debt_only_review = await _progressive_volume_has_only_conceded_quality_debt(
                session,
                settings=settings,
                project=project,
                project_id=project.id,
                volume_number=vol_num,
                expected_chapter_numbers=set(current_volume_chapter_numbers),
                workflow_run_id=vol_project_result.workflow_run_id,
            )
            if quality_debt_only_review:
                _emit_progress(
                    progress,
                    "volume_writing_quality_debt_advisory_continuing",
                    {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                        "requires_human_review": True,
                        "handoff": "settled_quality_debt_only",
                    },
                )
        if (
            (vol_project_result.requires_human_review and not quality_debt_only_review)
            or volume_incomplete_after_run
        ):
            blocked_volume_repair_required = True
            blocked_volume_repair_chapter_numbers = set(current_volume_chapter_numbers)
            blocked_volume_final_verdict = (
                vol_project_result.final_verdict
                if vol_project_result.requires_human_review
                else "attention"
            ) or "attention"
            if vol_project_result.requires_human_review:
                logger.warning(
                    "Volume %d writing for project %s machine-blocked for repair.",
                    vol_num,
                    project.slug,
                )
                _emit_progress(
                    progress,
                    "volume_writing_machine_repair_required",
                    {
                    "project_slug": project.slug,
                    "volume_number": vol_num,
                    "chapters_written": len(vol_project_result.chapter_results),
                    "final_verdict": vol_project_result.final_verdict,
                    },
                )
            if volume_incomplete_after_run:
                logger.warning(
                    "Volume %d writing for project %s stopped incomplete (%d/%d written); "
                    "not advancing to later volumes.",
                    vol_num,
                    project.slug,
                    volume_written_count_after_run,
                    volume_total_count_after_run,
                )
                _emit_progress(
                    progress,
                    "volume_writing_incomplete_current_volume",
                    {
                    "project_slug": project.slug,
                    "volume_number": vol_num,
                    "written": volume_written_count_after_run,
                    "total": volume_total_count_after_run,
                    "chapters_written": len(vol_project_result.chapter_results),
                    "final_verdict": vol_project_result.final_verdict,
                    },
                )
                break
            if not getattr(settings.pipeline, "progressive_continue_after_volume_block", False):
                break
            _emit_progress(
                progress,
                "volume_writing_repair_parallelized",
                {
                "project_slug": project.slug,
                "volume_number": vol_num,
                "next_volume_number": vol_num + 1 if vol_idx < total_volumes else None,
                },
            )
            # Do not collect feedback from a blocked volume: those chapters are
            # not clean canon yet. Later volume planning can still proceed from
            # the stable foundation plan and the last successful feedback.
            continue

        # ── Collect feedback (反哺) for next volume ──
        _emit_progress(
            progress,
            "volume_feedback_collection_started",
            {
                "project_slug": project.slug,
                "volume_number": vol_num,
            },
        )
        feedback = await collect_volume_writing_feedback(session, project.id, vol_num)
        prior_feedback_summary = summarize_volume_feedback(feedback, language=project.language)
        # Extract world snapshot for next volume's world disclosure
        world_snap = feedback.get("world_snapshot")
        if world_snap and isinstance(world_snap, dict):
            prior_world_snapshot = world_snap.get("summary", "")
        _emit_progress(
            progress,
            "volume_feedback_collected",
            {
                "project_slug": project.slug,
                "volume_number": vol_num,
            "character_evolutions": len(feedback.get("character_states", [])),
                "unresolved_threads": len(
                    feedback.get("arc_summary", {}).get("unresolved_threads", [])
                ),
            },
        )

        # ── Volume audit (质量反哺) — best-effort; never fails the pipeline ──
        try:
            from bestseller.services.volume_audit import run_volume_audit

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
            _emit_progress(
                progress,
                "volume_audit_completed",
                {
                    "project_slug": project.slug,
                    "volume_number": vol_num,
                "digest": audit_digest[:120] if audit_digest else "",
                },
            )
        except Exception as _audit_exc:
            logger.warning(
                "volume audit skipped for %s v%s: %s",
                project.slug,
                vol_num,
                _audit_exc,
            )

    if execution_window is not None:
        if vol_project_result is None:
            raise ProjectRepairPauseError("execution_window_has_no_dispatchable_planning_entry")
        vol_project_result.chapter_results = all_chapter_results
        return as_autowrite_window_result(project, vol_project_result,
            planning_workflow_run_id=planning_result.workflow_run_id)

    # ── Final export + review ──
    # Best-effort project export: surface preflight failures as a warning
    # event but never let them mask a successful multi-volume write. The
    # per-chapter markdown files are still available even when the combined
    # project export is blocked by the hygiene check.
    exported_artifact = None
    exported_output_path: str | None = None
    if export_markdown:
        try:
            exported_artifact, exported_path = await export_project_markdown(
                session,
                settings,
                project.slug,
                final_quality_gate=run_final_quality_gates,
            )
            exported_output_path = str(exported_path)
        except ValueError as export_err:
            strict_export_reason = str(export_err)
            try:
                exported_artifact, closure_path = await export_project_closure_draft_markdown(
                    session,
                    settings,
                    project.slug,
                    created_by_run_id=vol_project_result.workflow_run_id
                    if vol_project_result is not None
                    else None,
                )
                exported_output_path = str(closure_path.resolve())
                _emit_progress(
                    progress,
                    "project_closure_draft_exported",
                    {
                        "project_slug": project.slug,
                        "export_artifact_id": str(exported_artifact.id),
                        "output_path": exported_output_path,
                        "export_kind": "closure_draft",
                        "publication_ready": False,
                        "strict_export_blocked": strict_export_reason,
                    },
                )
            except ValueError as closure_err:
                _emit_progress(
                    progress,
                    "project_export_skipped",
                    {
                        "project_slug": project.slug,
                        "reason": strict_export_reason,
                        "closure_draft_reason": str(closure_err),
                        "publication_ready": False,
                    },
                )
                logger.warning(
                    "Final project export and closure draft blocked for %s: %s; %s "
                    "(continuing; per-chapter markdown files remain available).",
                    project.slug,
                    strict_export_reason,
                    closure_err,
                )

    project_result = (
        vol_project_result
        if vol_project_result is not None
        else ProjectPipelineResult(
            workflow_run_id=UUID(int=0),
            project_id=project.id,
            project_slug=project.slug,
            chapter_results=[],
            review_report_id=None,
            quality_score_id=None,
            export_artifact_id=None,
            output_path=None,
            final_verdict=None,
            requires_human_review=False,
    )
    )

    repair_result = None
    project_requires_repair = project_result.requires_human_review or blocked_volume_repair_required
    # See note above: only skip auto-repair on scene-machine-block in legacy
    # hard-pause mode; soft-continue lets the repair pass remediate flagged chapters.
    skip_auto_repair_for_scene_block = (
        project_requires_repair
        and getattr(settings.pipeline, "whole_book_pause_on_scene_review", False)
        and await _project_has_scene_machine_blocked_chapter(session, project.id)
    )
    project_repair_verdict = (
        blocked_volume_final_verdict
        if blocked_volume_repair_required and project_result.final_verdict in (None, "pass")
        else project_result.final_verdict
    )
    if skip_auto_repair_for_scene_block:
        _emit_progress(
            progress,
            "auto_repair_skipped_scene_machine_blocked",
            {
            "project_slug": project.slug,
            "final_verdict": project_repair_verdict,
            },
        )
    if (
        project_requires_repair
        and auto_repair_on_attention
        and not skip_auto_repair_for_scene_block
    ):
        _emit_progress(
            progress,
            "auto_repair_started",
            {
                "project_slug": project.slug,
                "final_verdict": project_repair_verdict,
            },
        )
        from bestseller.services.repair import run_project_repair

        repair_result = await run_project_repair(
            session,
            settings,
            project.slug,
            requested_by=requested_by,
            export_markdown=export_markdown,
            target_chapter_numbers=blocked_volume_repair_chapter_numbers,
            progress=progress,
        )
        _emit_progress(
            progress,
            "auto_repair_completed",
            {
                "project_slug": project.slug,
                "workflow_run_id": str(repair_result.workflow_run_id),
            },
        )

    repair_final_result = (
        repair_result
        if repair_result is not None and repair_result.final_verdict != "no_actionable_repair"
        else None
    )
    final_review_report_id = (
        repair_final_result.review_report_id
        if repair_final_result is not None
        else project_result.review_report_id
    )
    final_quality_score_id = (
        repair_final_result.quality_score_id
        if repair_final_result is not None
        else project_result.quality_score_id
    )
    final_export_artifact_id = (
        repair_result.export_artifact_id
        if repair_result and repair_result.export_artifact_id
        else project_result.export_artifact_id
        or (exported_artifact.id if exported_artifact else None)
    )
    final_output_path = (
        repair_result.output_path
        if repair_result and repair_result.output_path
        else project_result.output_path or exported_output_path
    )
    final_verdict = (
        repair_final_result.final_verdict
        if repair_final_result is not None
        else project_repair_verdict
    )
    final_requires_human_review = (
        repair_final_result.requires_human_review
        if repair_final_result is not None
        else project_requires_repair
    )
    output_dir = (Path(settings.output.base_dir) / project.slug).resolve()
    output_files = _collect_output_files(output_dir)
    closure_draft_exported = bool(
        final_output_path
        and Path(final_output_path).name == "project-draft-with-quality-debt.md"
    )
    if closure_draft_exported:
        final_requires_human_review = True
        if final_verdict in {None, "pass"}:
            final_verdict = "attention"
    export_status = (
        "draft_exported_requires_human_review"
        if closure_draft_exported
        else "exported_requires_human_review"
        if final_export_artifact_id and final_requires_human_review
        else "exported"
        if final_export_artifact_id
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
            "final_verdict": final_verdict,
        },
    )
    return AutowriteResult(
        project_id=project.id,
        project_slug=project.slug,
        planning_workflow_run_id=planning_result.workflow_run_id,
        story_bible_workflow_run_id=story_bible_result.workflow_run_id,
        outline_workflow_run_id=outline_result.workflow_run_id
        if outline_result is not None
        else UUID(int=0),
        narrative_graph_workflow_run_id=narrative_graph_result.workflow_run_id
        if narrative_graph_result is not None
        else UUID(int=0),
        narrative_tree_workflow_run_id=narrative_tree_result.workflow_run_id
        if narrative_tree_result is not None
        else UUID(int=0),
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
    (PlanningArtifactVersionModel,)
