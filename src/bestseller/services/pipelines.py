from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import traceback
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bestseller.domain.context import SceneWriterContextPacket
from bestseller.domain.enums import (
    ArtifactType,
    ChapterStatus,
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
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneDraftVersionModel,
    VolumeModel,
    WorkflowRunModel,
)
from bestseller.services.audit_loop import (
    build_phase1_audit,
    run_and_persist_audit,
)
from bestseller.services.chase_debt_ledger import ChaseDebtLedger
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
from bestseller.services.context import build_scene_writer_context_from_models
from bestseller.services.continuity import (
    check_countdown_arithmetic,
    check_time_regression,
    extract_chapter_state_snapshot,
    load_previous_chapter_snapshot,
    validate_fact_monotonicity,
)
from bestseller.services.drafts import assemble_chapter_draft, count_words, generate_scene_draft
from bestseller.services.exports import export_chapter_markdown, export_project_markdown
from bestseller.services.emotion_kernel_backfill import ensure_project_emotion_driven_kernel
from bestseller.services.entry_system_backfill import ensure_project_entry_system_compat
from bestseller.services.public_emotion_backfill import ensure_project_public_emotion_kernels
from bestseller.services.invariants import (
    InvariantSeedError,
    invariants_from_dict,
    invariants_to_dict,
    seed_invariants,
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
    generate_foundation_plan,
    generate_novel_plan,
    generate_volume_plan,
    project_uses_signing_quality_gate,
)
from bestseller.services.premium_genre_engine import build_premium_genre_engine_blocks
from bestseller.services.projects import (
    create_project,
    get_project_by_slug,
    import_planning_artifact,
    load_json_file,
)
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
    qimao_opening_rewrite_strategy_for_findings,
    review_chapter_draft,
    review_scene_draft,
    rewrite_chapter_from_task,
    rewrite_scene_from_task,
)
from bestseller.services.scorecard import compute_scorecard, save_scorecard
from bestseller.services.summarization import compress_knowledge_window
from bestseller.services.truth_version import (
    TruthVersionStaleError,
    assert_truth_materializations_fresh,
    truth_metadata_for_workflow,
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
    WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
    create_workflow_run,
    create_workflow_step_run,
    ensure_project_identity_manifest,
    get_latest_completed_workflow_run,
    get_latest_planning_artifact,
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
from bestseller.services.writing_presets import infer_genre_preset
from bestseller.services.writing_profile import is_english_language
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


WORKFLOW_TYPE_SCENE_PIPELINE = "scene_pipeline"


def _is_volume_outline_auto_repairable(exc: Exception) -> bool:
    message = str(exc)
    return (
        "failed chapter-outline repair loop" in message
        and "returned" in message
        and "chapters" in message
    )


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
    if is_en:
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
    """Raised when normal writing is blocked by a structural repair pause."""


def _project_blocked_for_structural_repair(project: ProjectModel) -> bool:
    metadata = getattr(project, "metadata_json", None) or {}
    return bool(
        metadata.get("generation_resume_blocked_until_repair_audit")
        or metadata.get("production_paused")
        or metadata.get("structural_repair_required")
    )


async def _ensure_emotion_kernel_backfill_for_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    *,
    requested_by: str,
    progress: ProgressCallback | None = None,
) -> None:
    if not getattr(settings.pipeline, "enable_emotion_driven_kernel", True):
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
    if not getattr(settings.pipeline, "enable_entry_system_kernel", True):
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
        "他必须保住现场证据并逼出谁在掩盖关键死因，否则案件会被错误结掉、真凶脱身。"
    )
    repaired["protagonist_immediate_goal"] = (
        f"先在《{first_title}》现场保住证据，确认第一处异常，并当场决定下一步追问对象。"
    )
    repaired["visible_loss_if_fail"] = (
        f"失败会让《{first_title}》里的证据被当场抹掉，案件按普通死亡结案，真凶脱身。"
    )
    repaired["chapter_1_small_turn"] = (
        f"{protagonist_name}用自己的优势保住关键证据，反制误判，并把线索钩到「{first_hook}」。"
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
            default="主角拿到第一份可验证证据。",
        )
        repaired["chapter_3_payoff"] = (
            f"{protagonist_name}在《{third_title}》拿到可验证证据：{third_payoff}"
        )

    repaired["first_10000_loop"] = (
        "尸体喊冤触发冲突 -> 主角当场取证并反制误判 -> "
        "拿到第一条线索同时承受凶手反压 -> 章尾用新证据钩出更深谜题"
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
    report = evaluate_qimao_planning_gate(payload_to_check)
    if contract and not report.passed and chapters:
        repaired_contract = _repair_qimao_opening_contract_from_outline(
            dict(contract),
            chapters,
        )
        repaired_report = evaluate_qimao_planning_gate(
            {"qimao_opening_contract": repaired_contract}
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
    )


def _chapter_probe_from_model(chapter: ChapterModel) -> ChapterPlanProbe:
    try:
        raw_scenes = list(getattr(chapter, "scenes", []) or [])
    except Exception:
        raw_scenes = []
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
        scenes=tuple(_scene_probe_from_model(scene) for scene in raw_scenes),
    )


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
    report = evaluate_commercial_planning_readiness(
        [_chapter_probe_from_model(chapter) for chapter in chapters],
        target_chapters=int(getattr(project, "target_chapters", 0) or 0),
        package_root=package_root,
        long_serial_min_chapters=long_serial_min_chapters,
    )
    payload = commercial_planning_readiness_report_to_dict(report)
    project.metadata_json = {
        **(getattr(project, "metadata_json", None) or {}),
        "commercial_planning_readiness_report": payload,
        "commercial_planning_readiness_status": (
            "planned_gate_passed" if report.passed else "planned_gate_failed"
        ),
    }
    return payload


def _commercial_planning_readiness_error_message(
    report_payload: dict[str, Any],
) -> str:
    findings = report_payload.get("findings")
    codes: list[str] = []
    if isinstance(findings, list):
        codes = [
            str(item.get("code"))
            for item in findings
            if isinstance(item, dict) and item.get("severity") == "critical"
        ]
    suffix = ", ".join(codes) if codes else "unknown"
    return f"Commercial planning readiness gate failed: {suffix}"


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


def _project_uses_whole_book_quality_gate(project: ProjectModel) -> bool:
    metadata = getattr(project, "metadata_json", None) or {}
    return metadata.get("whole_book_quality_gate_disabled") is not True


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
    progress: ProgressCallback | None,
) -> None:
    if not project_uses_signing_quality_gate(project):
        return
    if chapter.chapter_number > 3:
        return
    metadata = getattr(project, "metadata_json", None) or {}
    opening_contract = (
        metadata.get("opening_quality_contract")
        or metadata.get("qimao_opening_contract")
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
            *((getattr(project, "metadata_json", None) or {}).get(
                "opening_quality_gate_reports"
            ) or []),
            {"chapter_number": chapter.chapter_number, **report_payload},
        ],
        "qimao_opening_gate_report": report_payload,
        "qimao_opening_gate_reports": [
            *((getattr(project, "metadata_json", None) or {}).get(
                "qimao_opening_gate_reports"
            ) or []),
            {"chapter_number": chapter.chapter_number, **report_payload},
        ],
    }
    workflow_run.metadata_json = {
        **(workflow_run.metadata_json or {}),
        "opening_quality_gate_report": report_payload,
        "qimao_opening_gate_report": report_payload,
    }

    if report.passed:
        _emit_progress(
            progress,
            "qimao_opening_gate_passed",
            {"project_slug": project.slug, "chapter_number": chapter.chapter_number},
        )
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
    project.metadata_json = {
        **(project.metadata_json or {}),
        "opening_quality_gate_blocked": True,
        "qimao_opening_gate_blocked": True,
    }
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
) -> None:
    if not _project_uses_whole_book_quality_gate(project):
        return

    chapter_draft = await _load_chapter_draft_for_pipeline_result(session, chapter_result)
    if chapter_draft is None or not getattr(chapter_draft, "content_md", None):
        return

    chapter_texts[chapter.chapter_number] = chapter_draft.content_md or ""
    metadata = getattr(project, "metadata_json", None) or {}
    report = evaluate_whole_book_quality(
        chapter_texts,
        volume_plan=metadata.get("volume_plan"),
        emotion_driven_kernel=metadata.get("emotion_driven_kernel"),
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
        _emit_progress(
            progress,
            "whole_book_quality_gate_passed",
            {"project_slug": project.slug, "chapter_number": chapter.chapter_number},
        )
        return

    opening_contract = (
        metadata.get("opening_quality_contract")
        or metadata.get("qimao_opening_contract")
    )
    finding_codes = _whole_book_quality_gate_finding_codes(report_payload)
    warn_only = (
        metadata.get("whole_book_quality_gate_warn_only") is True
        or _whole_book_quality_gate_can_warn_only(report_payload)
    )
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


# Process-local ledger cache. Once a DB-backed ``ChaseDebtLedger`` lands we
# can replace this with a repository, but until then in-memory state shared
# across the workflow run is enough to exercise the accrual path end-to-end.
_CHASE_DEBT_LEDGER: ChaseDebtLedger | None = None


def _get_chase_debt_ledger() -> ChaseDebtLedger:
    global _CHASE_DEBT_LEDGER
    if _CHASE_DEBT_LEDGER is None:
        _CHASE_DEBT_LEDGER = ChaseDebtLedger()
    return _CHASE_DEBT_LEDGER


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
            float(classification.line_intensity)
            if classification.line_intensity
            else None
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
    project_id: UUID,
    chapter_number: int,
) -> None:
    """Run Phase C3 ledger interest accrual for the chapter tick.

    Controlled by ``phase_c_overrides.enabled``. In-memory ledger until a
    DB-backed model lands; the accrual path is idempotent per chapter so
    repeated calls for the same ``current_chapter`` are safe.
    """

    try:
        cfg = get_quality_gates_config()
        if not cfg.phase_c.enabled:
            return
        ledger = _get_chase_debt_ledger()
        touched = ledger.accrue_interest(str(project_id), chapter_number)
        if touched:
            logger.info(
                "Phase C accrued interest on %d debt(s) at ch%d",
                touched,
                chapter_number,
            )
    except Exception:
        logger.debug("Phase C accrual failed (non-fatal)", exc_info=True)


async def _collect_phase_d_reports(
    *,
    session: AsyncSession,
    project_id: UUID,
    chapter_number: int,
    snapshot: ChapterStateSnapshotModel | None,
) -> list[Any]:
    """Return Phase D3 ``CheckerReport`` envelopes for the just-finalized chapter.

    Controlled by ``phase_d_time.enabled``; returns an empty list when the
    flag is off. ``snapshot`` is the row we just persisted — we load the
    previous chapter's snapshot and run the two pure validators against
    the pair. Errors are logged and swallowed.
    """

    try:
        cfg = get_quality_gates_config()
        if not cfg.phase_d.enabled or snapshot is None:
            return []
        from bestseller.domain.context import (
            ChapterStateSnapshotContext as _Ctx,
        )
        from bestseller.services.continuity import (
            _facts_from_storage as _facts_from,
        )

        cur_ctx = _Ctx(
            chapter_number=snapshot.chapter_number,
            facts=_facts_from(snapshot.facts),
            time_anchor=snapshot.time_anchor,
            chapter_time_span=snapshot.chapter_time_span,
        )
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
        logger.debug("Phase D validators failed (non-fatal)", exc_info=True)
        return []


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
        "issues": [_issue_payload(issue) for issue in list(getattr(report, "issues", ()) or ())[:10]],
    }


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


async def _resume_outline_chapters_for_volume(
    session: AsyncSession,
    *,
    project_id: UUID,
    volume_number: int,
    expected_count: int,
) -> list[dict[str, Any]]:
    artifact = await get_latest_planning_artifact(
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
    """Return True when review can accept the best draft without human review."""
    if chapter_draft is None:
        return False
    return getattr(chapter, "production_state", None) == "ok"


def _project_consistency_warn_only_scope(
    *,
    current_volume_number: int | None,
    chapter_numbers: set[int] | None,
) -> str | None:
    """Project consistency is advisory while processing a partial write slice."""
    if current_volume_number is not None:
        return "partial_volume"
    if chapter_numbers is not None:
        return "chapter_slice"
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
    """
    stmt = (
        select(func.count(ChapterModel.id))
        .join(VolumeModel, ChapterModel.volume_id == VolumeModel.id)
        .where(
            ChapterModel.project_id == project_id,
            VolumeModel.volume_number == volume_number,
            ChapterModel.status.in_(_WRITTEN_CHAPTER_STATUSES),
            ChapterModel.production_state == "ok",
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
            "truth_materialization_refresh_legacy_accepted",
            {
                "project_slug": project.slug,
                "reason": str(exc).splitlines()[0],
            },
        )
        return True
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
    truth_metadata = truth_metadata_for_workflow(project)
    for workflow_type in (
        WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE,
        WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH,
    ):
        run = await get_latest_completed_workflow_run(
            session,
            project_id=project.id,
            workflow_type=workflow_type,
        )
        if run is None:
            continue
        run.metadata_json = {
            **(run.metadata_json or {}),
            **truth_metadata,
            "legacy_truth_acceptance": {
                "reason": reason,
                "mode": "resume_after_l2_gate_tightening",
            },
        }


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


async def _recover_session_after_nonfatal_error(
    session: AsyncSession,
    exc: Exception,
) -> None:
    """Rollback when a tolerated helper error leaves the DB session dirty."""

    if not (
        isinstance(exc, (PendingRollbackError, DBAPIError))
        or not getattr(session, "is_active", True)
    ):
        return
    rollback = getattr(session, "rollback", None)
    if rollback is None:
        return
    await rollback()


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
    allow_structural_repair: bool = False,
) -> ScenePipelineResult:
    project, chapter, scene = await _load_scene_identifiers(
        session,
        project_slug,
        chapter_number,
        scene_number,
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

        # ── Prewrite planning-kernel directives ──
        # These are generated from the project-level prewrite readiness gate.
        # They make macro-planning failures immediately visible to active
        # scene drafting instead of waiting for the next full replanning run.
        if shared_context is not None:
            try:
                _project_meta = project.metadata_json or {}
                _directives = _project_meta.get("prewrite_repair_directives") or []
                if not _directives and _project_meta.get("prewrite_readiness_report"):
                    from bestseller.services.planning_kernel import (
                        build_prewrite_repair_directives,
                    )

                    _directives = build_prewrite_repair_directives(
                        _project_meta.get("prewrite_readiness_report"),
                        language=getattr(project, "language", None)
                        or settings.generation.language,
                    )
                _directive_texts = [
                    str(item).strip()
                    for item in _directives
                    if str(item).strip()
                ]
                if _directive_texts:
                    _prefix = (
                        "[Prewrite planning gate] "
                        if is_english_language(
                            getattr(project, "language", None)
                            or settings.generation.language
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
                chapter_number, scene_number,
                exc_info=True,
            )

        # ── Narrative contract gate (zero LLM cost, pre-draft) ──
        if (
            draft is None
            and getattr(settings.pipeline, "require_pre_draft_scene_contract", True)
        ):
            try:
                from bestseller.services.narrative_contracts import (
                    repair_legacy_scene_contract_pre_draft,
                    repair_missing_scene_participants_pre_draft,
                    validate_scene_contract_pre_draft,
                )
                from bestseller.services.methodology_overlay import (
                    resolve_methodology_contract_mode,
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
            except Exception:
                logger.debug("Premium genre engine injection failed (non-fatal)", exc_info=True)

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
                    chapter_number=chapter_number,
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
            except Exception:
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
    allow_structural_repair: bool = False,
    progress: ProgressCallback | None = None,
) -> ChapterPipelineResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    _assert_project_not_blocked_for_structural_repair(
        project,
        project_slug=project_slug,
        operation=f"chapter pipeline {chapter_number}",
        allow_structural_repair=allow_structural_repair,
    )
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
            },
        )
        chapter_draft = None
        _existing_chapter_draft: ChapterDraftVersionModel | None = None
        if (
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
                _actual_wc = (
                    count_words(_existing_chapter_draft.content_md or "")
                    if _existing_chapter_draft is not None
                    else 0
                )
                _stored_wc = int(getattr(chapter, "current_word_count", None) or 0)
                _draft_wc = (
                    int(getattr(_existing_chapter_draft, "word_count", None) or 0)
                    if _existing_chapter_draft is not None
                    else 0
                )
                _wc_candidates = [wc for wc in (_actual_wc, _stored_wc, _draft_wc) if wc > 0]
                _chapter_length_recheck_needed = any(
                    wc < int(_budget.min) or wc > int(_budget.max)
                    for wc in _wc_candidates
                )
            except Exception:
                _chapter_length_recheck_needed = False
            if _existing_chapter_draft is not None and not _chapter_length_recheck_needed:
                chapter_draft = _existing_chapter_draft
                logger.info(
                    "Chapter %d resume: reusing existing draft v%d",
                    chapter_number, chapter_draft.version_no,
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
            chapter_draft = await assemble_chapter_draft(session, project_slug, chapter_number, settings=settings)
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
            and (
                getattr(chapter, "production_state", None) == "blocked"
                or _scene_loop_blocked
            )
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
                "blocking chapter for human review",
                chapter_number,
            )
            chapter.status = ChapterStatus.REVISION.value
            chapter.production_state = "blocked"
            workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
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

        if auto_repair_attempts > 0 and getattr(chapter, "production_state", None) == "blocked":
            logger.warning(
                "Chapter %d: auto-repair exhausted %d attempt(s), still blocked — "
                "routing best available draft to human review",
                chapter_number,
                auto_repair_attempts,
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

        # ── AI-flavor gate ─────────────────────────────────────────────
        # Runs after bible_gate and before export/signing. Detects span-
        # level AI "味" and applies *only* localized fixes at the marked
        # positions. Surrounding prose is never rewritten. When the
        # post-patch score is still above the block threshold the chapter
        # is routed to human review — same escape hatch the rest of the
        # gates use.
        ai_flavor_outcome = None
        try:
            from bestseller.services.quality_gates_config import (
                get_quality_gates_config,
            )

            _af_gates_cfg = get_quality_gates_config()
            if (
                _af_gates_cfg.ai_flavor.enabled
                and chapter_draft is not None
                and chapter_draft.content_md
            ):
                from bestseller.services.ai_flavor_gate import run_ai_flavor_gate

                _af_lang = getattr(project, "language", None) or "zh-CN"
                _af_output_dir = (
                    Path(settings.output.base_dir) / project.slug
                ).resolve()
                ai_flavor_outcome = run_ai_flavor_gate(
                    chapter_number=chapter_number,
                    content_md=chapter_draft.content_md,
                    language=_af_lang,
                    config=_af_gates_cfg.ai_flavor,
                    project_output_dir=_af_output_dir,
                )
                if ai_flavor_outcome.patched_text is not None:
                    chapter_draft.content_md = ai_flavor_outcome.patched_text
                if ai_flavor_outcome.decision == "block":
                    logger.warning(
                        "ai_flavor_gate ch%d: residual score %.1f >= threshold, "
                        "routing to human review",
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
                # Only record a workflow step when the gate actually
                # detected something. Clean-pass no-ops would otherwise
                # clutter the step log on every chapter and break
                # downstream consumers that assume a fixed step count.
                if (
                    ai_flavor_outcome.before_score > 0
                    or ai_flavor_outcome.decision != "pass"
                ):
                    await create_workflow_step_run(
                        session,
                        workflow_run_id=workflow_run.id,
                        step_name="ai_flavor_gate",
                        step_order=step_order,
                        status=WorkflowStatus.COMPLETED,
                        output_ref={
                            "decision": ai_flavor_outcome.decision,
                            "before_score": ai_flavor_outcome.before_score,
                            "after_score": ai_flavor_outcome.after_score,
                            "edits": len(ai_flavor_outcome.edits),
                        },
                    )
                    step_order += 1
        except Exception:
            logger.debug("ai_flavor_gate failed (non-fatal)", exc_info=True)

        async def _export_current_chapter_markdown() -> tuple[UUID | None, str | None]:
            nonlocal current_step_name
            nonlocal step_order
            if not export_markdown:
                return None, None
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
            if getattr(chapter, "production_state", None) != "blocked":
                chapter.production_state = "ok"
            phase_d_block_reports: list[dict[str, Any]] = []
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
                    # Phase B — classify + persist dominance history.
                    await _apply_post_chapter_phase_b(
                        session=session,
                        project=project,
                        chapter=chapter,
                        chapter_md=chapter_draft.content_md or "",
                    )
                    # Phase C — accrue interest on any outstanding debts.
                    await _apply_post_chapter_phase_c(
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
                            phase_d_block_reports.append(
                                _checker_report_gate_payload(_pd_report)
                            )
                    # Validate monotonic facts against previous chapter
                    if snapshot is not None and snapshot.facts:
                        from bestseller.domain.context import HardFactContext as _HFC

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
                            build_overused_phrase_avoidance_block,
                            extract_frequent_phrases,
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
            except Exception as exc:
                logger.warning(
                    "Chapter %d hard-fact extraction failed (non-fatal): %s",
                    chapter.chapter_number,
                    exc,
                )
            if phase_d_block_reports:
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                export_artifact_id, output_path = await _export_current_chapter_markdown()
                workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
                workflow_run.current_step = "waiting_human_review"
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
            _emit_progress(
                progress,
                "chapter_review_started",
                {
                    "project_slug": project_slug,
                    "chapter_number": chapter_number,
                    "iteration": chapter_review_iterations,
                },
            )
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

            at_chapter_rewrite_limit = (
                chapter_rewrite_iterations >= settings.quality.max_chapter_revisions
            )
            safe_draft_available = _chapter_has_safe_draft_for_review_stall(
                chapter,
                chapter_draft,
            )
            accept_chapter_on_stall = (
                at_chapter_rewrite_limit
                and settings.pipeline.accept_on_stall
                and not getattr(settings.pipeline, "chapter_review_block_on_failure", True)
                and safe_draft_available
            )
            if (
                chapter_review_result.verdict == "pass"
                or chapter_rewrite_task is None
                or accept_chapter_on_stall
            ):
                if accept_chapter_on_stall:
                    reached_chapter_revision_limit = True
                    logger.info(
                        "Chapter %d reached max revisions (%d) — accepting best safe draft",
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
                        _snapshot_row = await extract_chapter_state_snapshot(
                            session,
                            settings,
                            project_id=project.id,
                            chapter=chapter,
                            chapter_md=chapter_draft.content_md,
                            workflow_run_id=workflow_run.id,
                        )
                        # Phase B — classify + persist dominance history.
                        await _apply_post_chapter_phase_b(
                            session=session,
                            project=project,
                            chapter=chapter,
                            chapter_md=chapter_draft.content_md or "",
                        )
                        # Phase C — accrue interest on outstanding debts.
                        await _apply_post_chapter_phase_c(
                            project_id=project.id,
                            chapter_number=chapter.chapter_number,
                        )
                        # Phase D — run countdown / time-regression validators.
                        _phase_d_reports = await _collect_phase_d_reports(
                            session=session,
                            project_id=project.id,
                            chapter_number=chapter.chapter_number,
                            snapshot=_snapshot_row,
                        )
                        for _pd_report in _phase_d_reports:
                            if not _pd_report.passed:
                                logger.warning(
                                    "Phase D ch%d %s: %s",
                                    chapter.chapter_number,
                                    _pd_report.agent,
                                    _pd_report.summary,
                                )
                            if getattr(_pd_report, "blocks_write", False):
                                requires_human_review = True
                                chapter.status = ChapterStatus.REVISION.value
                                chapter.production_state = "blocked"
                                workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
                                workflow_run.current_step = "waiting_human_review"
                                workflow_run.metadata_json = {
                                    **workflow_run.metadata_json,
                                    "blocked_by_phase_d_time_gate": True,
                                    "phase_d_reports": (
                                        (workflow_run.metadata_json or {}).get("phase_d_reports", [])
                                        + [_checker_report_gate_payload(_pd_report)]
                                    ),
                                }
                except Exception as exc:
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
                    except Exception as exc:
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
                except Exception as exc:
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
                except Exception:
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
                except Exception:
                    logger.debug(
                        "Chapter %d L8 per-chapter scorecard failed (non-fatal)",
                        chapter.chapter_number,
                        exc_info=True,
                    )
                break

            if at_chapter_rewrite_limit:
                # Either accept_on_stall is disabled or chapter review is
                # configured as a hard quality gate. Do not mark a rejected
                # chapter complete after exhausting rewrites.
                reached_chapter_revision_limit = True
                requires_human_review = True
                workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
                workflow_run.current_step = "waiting_human_review"
                break

            chapter_rewrite_iterations += 1
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
                    "word_count": int(getattr(chapter_draft, "word_count", 0) or 0),
                },
            )

        if getattr(chapter, "production_state", None) == "blocked":
            requires_human_review = True
            chapter.status = ChapterStatus.REVISION.value
            workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
            workflow_run.current_step = "waiting_human_review"
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "requires_human_review": True,
                "blocked_after_chapter_rewrite_quality_gate": True,
                "chapter_draft_id": str(chapter_draft.id),
                "chapter_draft_version_no": chapter_draft.version_no,
            }

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
        if getattr(chapter, "production_state", None) != "blocked":
            chapter.production_state = "ok"
            chapter_meta = dict(chapter.metadata_json or {})
            if (
                chapter_meta.get("auto_repair_exhausted")
                or chapter_meta.get("auto_repair_in_progress")
            ):
                chapter_meta.pop("auto_repair_exhausted", None)
                chapter_meta.pop("auto_repair_in_progress", None)
                if auto_repair_attempts > 0:
                    chapter_meta["auto_repair_last_successful_attempts"] = auto_repair_attempts
                chapter.metadata_json = chapter_meta

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
            .options(selectinload(ChapterModel.scenes))
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
        # ``production_state`` is the quality-gate state.  A chapter may still
        # have ``status=complete`` or an existing draft from an earlier pass,
        # but if the quality gate or a bulk repair reset it to pending/blocked
        # it must be regenerated instead of accepted as a resume skip.
        if getattr(ch, "production_state", None) != "ok":
            return False
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
    allow_structural_repair: bool = False,
) -> ProjectPipelineResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    requested_chapter_numbers = (
        set(chapter_numbers) if chapter_numbers is not None else None
    )
    _assert_project_not_blocked_for_structural_repair(
        project,
        project_slug=project_slug,
        operation="project pipeline",
        allow_structural_repair=allow_structural_repair,
    )

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

    # ── Batch 2: Material Forge ────────────────────────────────────────────
    # When ``enable_forge_pipeline`` is on, run all 5 Forges before the
    # Planner so that project_materials exist for reference-style prompting.
    # Runs only on the first pass (when no project_materials exist yet) to
    # avoid re-forging on every resume.  Failures are logged but do NOT
    # abort the pipeline — the old non-reference path is the safe fallback.
    if settings.pipeline.enable_forge_pipeline:
        try:
            from sqlalchemy import func, select

            from bestseller.infra.db.models import ProjectMaterialModel
            from bestseller.services.material_forge import forge_all_materials

            existing_count_result = await session.execute(
                select(func.count()).where(
                    ProjectMaterialModel.project_id == project.id
                )
            )
            if existing_count_result is None or not hasattr(
                existing_count_result,
                "scalar_one",
            ):
                existing_count = 1
            else:
                existing_count = existing_count_result.scalar_one()
            if existing_count == 0:
                _emit_progress(
                    progress,
                    "material_forge_started",
                    {"project_slug": project_slug},
                )
                project_metadata = project.metadata_json or {}
                genre = (
                    getattr(project, "genre", None)
                    or project_metadata.get("genre")
                    or ""
                )
                sub_genre = (
                    getattr(project, "sub_genre", None)
                    or project_metadata.get("sub_genre")
                )
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

    if requested_chapter_numbers is not None:
        chapters = [
            ch for ch in chapters
            if ch.chapter_number in requested_chapter_numbers
        ]
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
    resume_filter_enabled = settings.pipeline.resume_enabled
    if should_materialize:
        resume_filter_enabled = False
    elif (
        requested_chapter_numbers is not None
        and current_volume_number is None
        and total_volumes is None
    ):
        # A direct project-pipeline call with an explicit chapter slice is a
        # manual rerun/repair request. Do not silently skip the selected
        # chapter just because an earlier run marked it complete. Progressive
        # autowrite passes volume context, so it still gets true resume
        # behavior for already-written volume slices.
        resume_filter_enabled = False

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
                raise ValueError(_qimao_planning_gate_error_message(qimao_gate_report))
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
                should_block_commercial_gate = (
                    not commercial_gate_report.get("passed", False)
                    and getattr(
                        settings.pipeline,
                        "commercial_planning_readiness_block_on_failure",
                        True,
                    )
                )
                if should_block_commercial_gate:
                    _emit_progress(
                        progress,
                        "commercial_planning_readiness_gate_failed",
                        {
                            "project_slug": project_slug,
                            "findings": commercial_gate_report.get("findings", []),
                        },
                    )
                    raise ValueError(
                        _commercial_planning_readiness_error_message(
                            commercial_gate_report
                        )
                    )
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name=current_step_name,
                    step_order=step_order,
                    status=WorkflowStatus.COMPLETED,
                    output_ref=commercial_gate_report,
                )
                step_order += 1
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
            if chapter_result.requires_human_review:
                requires_human_review = True
                _emit_progress(
                    progress,
                    "chapter_pipeline_paused_for_human_review",
                    {
                        "project_slug": project_slug,
                        "chapter_number": chapter.chapter_number,
                        "workflow_run_id": str(chapter_result.workflow_run_id),
                    },
                )
                project.status = ProjectStatus.REVISING.value
                workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
                workflow_run.current_step = "waiting_human_review"
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "requires_human_review": True,
                    "paused_after_chapter_number": chapter.chapter_number,
                    "blocked_chapter_workflow_run_id": str(chapter_result.workflow_run_id),
                    "processed_chapter_count": len(chapter_results),
                }
                await sync_world_expansion_progress(session, project=project)
                await _checkpoint_commit(session)
                break
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
        project_consistency_warn_only_scope = _project_consistency_warn_only_scope(
            current_volume_number=current_volume_number,
            chapter_numbers=requested_chapter_numbers,
        )
        if project_review_not_pass:
            if project_consistency_warn_only_scope is not None:
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
            ProjectStatus.REVISING.value
            if requires_human_review
            else ProjectStatus.WRITING.value
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
        scorecard_quality_score_for_premium_gate: float | None = None
        scorecard_quality_score_ignored_reason: str | None = None
        try:
            scorecard = await compute_scorecard(
                session,
                project.id,
                expected_chapter_count=project.target_chapters,
            )
            await save_scorecard(session, scorecard)
            scorecard_quality_score = scorecard.quality_score
            if int(getattr(scorecard, "missing_chapters", 0) or 0) > 0:
                scorecard_quality_score_ignored_reason = "project_in_progress_missing_chapters"
            else:
                scorecard_quality_score_for_premium_gate = scorecard.quality_score
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
                premium_book_gate_payload = premium_book_gate_report_to_dict(
                    premium_report
                )
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
                            finding.code
                            for finding in premium_report.blocking_findings
                        ],
                    },
                )
                step_order += 1
                if (
                    not premium_report.passed
                    and getattr(
                        settings.pipeline,
                        "premium_book_gate_block_on_failure",
                        False,
                    )
                ):
                    requires_human_review = True
                    project.status = ProjectStatus.REVISING.value
                    workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
                    workflow_run.current_step = "waiting_human_review"
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
                "final_verdict": review_result.verdict if review_result is not None else None,
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
    from bestseller.domain.enums import ProjectType

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
    _assert_project_not_blocked_for_structural_repair(
        project,
        project_slug=project.slug,
        operation="autowrite pipeline",
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
    from bestseller.services.planning_context import (
        collect_volume_writing_feedback,
        summarize_volume_feedback,
    )

    project = await get_project_by_slug(session, project_payload.slug)
    if project is None:
        _emit_progress(progress, "project_creation_started", {"project_slug": project_payload.slug})
        project = await create_project(session, project_payload, settings)
        await _checkpoint_commit(session)
        _emit_progress(progress, "project_creation_completed", {"project_slug": project.slug, "project_id": str(project.id)})
    _assert_project_not_blocked_for_structural_repair(
        project,
        project_slug=project.slug,
        operation="progressive autowrite pipeline",
    )

    # ── Phase A: Foundation Plan ──
    existing_volume_plan = await get_latest_planning_artifact(
        session, project_id=project.id, artifact_type=ArtifactType.VOLUME_PLAN,
    )
    if existing_volume_plan is not None and settings.pipeline.resume_enabled:
        _emit_progress(progress, "foundation_planning_skipped_resume", {"project_slug": project.slug})
        from bestseller.domain.planning import NovelPlanningResult
        planning_result = NovelPlanningResult(
            workflow_run_id=existing_volume_plan.source_run_id or UUID(int=0),
            project_id=project.id, premise=premise, volume_count=0, chapter_count=0,
        )
    else:
        _emit_progress(progress, "foundation_planning_started", {"project_slug": project.slug})
        planning_result = await generate_foundation_plan(
            session, settings, project.slug, premise, requested_by=requested_by, progress=progress,
        )
        await _checkpoint_commit(session)
        _emit_progress(progress, "foundation_planning_completed", {
            "project_slug": project.slug,
            "workflow_run_id": str(planning_result.workflow_run_id),
            "volume_count": planning_result.volume_count,
        })

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
    if existing_bible_run is not None and settings.pipeline.resume_enabled:
        _emit_progress(progress, "story_bible_materialization_skipped_resume", {
            "project_slug": project.slug,
            "workflow_run_id": str(existing_bible_run.id),
        })
        from bestseller.domain.story_bible import StoryBibleMaterializationResult
        story_bible_result = StoryBibleMaterializationResult(
            workflow_run_id=existing_bible_run.id,
            project_id=project.id,
        )
    else:
        _emit_progress(progress, "story_bible_materialization_started", {"project_slug": project.slug})
        story_bible_result = await materialize_latest_story_bible(session, project.slug, requested_by=requested_by)
        await _checkpoint_commit(session)
        _emit_progress(progress, "story_bible_materialization_completed", {"project_slug": project.slug, "workflow_run_id": str(story_bible_result.workflow_run_id)})

    if getattr(settings.pipeline, "require_foundation_identity_lock", True):
        await ensure_project_identity_manifest(
            session,
            project,
            project_slug=project.slug,
        )
        await _checkpoint_commit(session)

    # ── Load planning artifacts for volume loop ──
    book_spec_art = await get_latest_planning_artifact(session, project_id=project.id, artifact_type=ArtifactType.BOOK_SPEC)
    world_spec_art = await get_latest_planning_artifact(session, project_id=project.id, artifact_type=ArtifactType.WORLD_SPEC)
    cast_spec_art = await get_latest_planning_artifact(session, project_id=project.id, artifact_type=ArtifactType.CAST_SPEC)
    volume_plan_art = await get_latest_planning_artifact(session, project_id=project.id, artifact_type=ArtifactType.VOLUME_PLAN)

    book_spec_payload = book_spec_art.content if book_spec_art else {}
    world_spec_payload = world_spec_art.content if world_spec_art else {}
    cast_spec_payload = cast_spec_art.content if cast_spec_art else {}
    volume_plan_payload = volume_plan_art.content if volume_plan_art else []

    # Normalize volume plan. Some recovered/legacy plans store chapter_range
    # but omit chapter_count_target; the planner can derive the count, so use
    # its normalization here before the volume loop makes skip/replan decisions.
    from bestseller.services.planner import _normalize_volume_plan_payload

    volume_plan_list = _normalize_volume_plan_payload(volume_plan_payload)

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

        resume_existing_chapter_numbers: set[int] | None = None
        used_resume_outline_chapters = False

        # Skip replanning for any already-materialized volume during resume.
        # A partial volume means "write/repair existing rows", not "generate
        # a fresh outline". Re-running generate_volume_plan against a drifted
        # volume_plan is what produced the xianxia-upgrade-1776137730 gap:
        # volume 1 was replanned at max(chapter_number)+1, first appending
        # 552-601 and then 602-651 instead of repairing the existing frontier.
        # Evidence is DB-only — the decision must not depend on plan targets
        # that the drift could have corrupted.
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
                # This whole volume is already complete, so it contributes to the
                # baseline for subsequent volumes.
                global_completed_chapter_offset += int(written_count or 0)
                continue
            if total_count > 0:
                existing_numbers = await _chapter_numbers_in_volume(session, project.id, vol_num)
                if existing_numbers:
                    resume_existing_chapter_numbers = existing_numbers
                    logger.info(
                        "Volume %d already materialized (%d/%d written, %d total) — "
                        "skipping replanning and writing existing chapter rows.",
                        vol_num, written_count, total_count, len(existing_numbers),
                    )
                    _emit_progress(progress, "volume_planning_skipped_resume_existing_rows", {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                        "written": written_count,
                        "total": total_count,
                        "chapter_count": len(existing_numbers),
                    })

        if resume_existing_chapter_numbers is None:
            # Plan this volume (cast expansion + world disclosure + outline).
            expected_volume_chapters = int(vol_entry.get("chapter_count_target") or 0)
            resume_outline_chapters: list[Any] = []
            if settings.pipeline.resume_enabled:
                resume_outline_chapters = await _resume_outline_chapters_for_volume(
                    session,
                    project_id=project.id,
                    volume_number=vol_num,
                    expected_count=expected_volume_chapters,
                )
                if resume_outline_chapters:
                    _emit_progress(progress, "volume_planning_skipped_resume_existing_outline", {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                        "chapter_count": len(resume_outline_chapters),
                    })

            if resume_outline_chapters:
                vol_chapters = resume_outline_chapters
                used_resume_outline_chapters = True
            else:
                _emit_progress(progress, "volume_planning_started", {
                    "project_slug": project.slug, "volume_number": vol_num, "total_volumes": total_volumes,
                })

                try:
                    vol_plan_result = await generate_volume_plan(
                        session, settings, project.slug, vol_num,
                        book_spec=book_spec_payload,
                        world_spec=world_spec_payload,
                        cast_spec=cast_spec_payload,
                        volume_plan=volume_plan_list,
                        prior_feedback_summary=prior_feedback_summary,
                        prior_world_snapshot=prior_world_snapshot,
                        requested_by=requested_by,
                        progress=progress,
                    )
                except PlannerFallbackError as exc:
                    if not _is_volume_outline_auto_repairable(exc):
                        raise
                    repair_constraints = _volume_outline_auto_repair_constraints(
                        language=project.language,
                        volume_number=vol_num,
                        expected_count=expected_volume_chapters,
                        error_message=str(exc),
                    )
                    _emit_progress(progress, "volume_planning_auto_repair_started", {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                        "reason": "chapter_outline_count_contract",
                        "expected_count": expected_volume_chapters,
                    })
                    logger.warning(
                        "Volume %d planning failed chapter count contract for project '%s'; "
                        "retrying once with auto-repair constraints.",
                        vol_num,
                        project.slug,
                    )
                    vol_plan_result = await generate_volume_plan(
                        session, settings, project.slug, vol_num,
                        book_spec=book_spec_payload,
                        world_spec=world_spec_payload,
                        cast_spec=cast_spec_payload,
                        volume_plan=volume_plan_list,
                        prior_feedback_summary=prior_feedback_summary,
                        prior_world_snapshot=prior_world_snapshot,
                        requested_by=requested_by,
                        extra_constraints=repair_constraints,
                        progress=progress,
                    )
                    _emit_progress(progress, "volume_planning_auto_repair_completed", {
                        "project_slug": project.slug,
                        "volume_number": vol_num,
                        "reason": "chapter_outline_count_contract",
                        "chapter_count": vol_plan_result.chapter_count,
                    })
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
                vol_chapters = []
                if vol_outline_art and vol_outline_art.content:
                    # Merge volume outline into cumulative CHAPTER_OUTLINE_BATCH
                    existing_batch_art = await get_latest_planning_artifact(
                        session, project_id=project.id, artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                    )
                    existing_chapters = _outline_content_chapters(
                        existing_batch_art.content if existing_batch_art else None
                    )
                    vol_chapters = _outline_content_chapters(vol_outline_art.content)
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

            current_volume_chapter_numbers = {
                ch.get("chapter_number")
                for ch in vol_chapters
                if isinstance(ch, dict) and isinstance(ch.get("chapter_number"), int)
            }
        else:
            current_volume_chapter_numbers = resume_existing_chapter_numbers

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
        if resume_existing_chapter_numbers is not None or used_resume_outline_chapters:
            await _refresh_stale_truth_materializations_for_resume(
                session,
                settings,
                project,
                requested_by=requested_by,
                progress=progress,
            )
        vol_project_result = await run_project_pipeline(
            session, settings, project.slug,
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
        # For the next volume's baseline, add both:
        # 1) chapters already written in this volume before this run; and
        # 2) chapters processed by this run in this volume.
        if settings.pipeline.resume_enabled:
            _vw_fully_written, _vw_written_count, _vw_total_count = await _volume_fully_written(
                session, project.id, vol_num,
            )
            if _vw_fully_written:
                global_completed_chapter_offset += int(_vw_written_count or 0)
            else:
                global_completed_chapter_offset += len(vol_project_result.chapter_results)
        else:
            global_completed_chapter_offset += len(vol_project_result.chapter_results)
        all_chapter_results.extend(vol_project_result.chapter_results)
        _emit_progress(progress, "volume_writing_completed", {
            "project_slug": project.slug, "volume_number": vol_num,
            "chapters_written": len(vol_project_result.chapter_results),
        })
        if vol_project_result.requires_human_review:
            logger.warning(
                "Volume %d writing for project %s paused for human review; "
                "skipping later volume planning until the blocker is resolved.",
                vol_num,
                project.slug,
            )
            _emit_progress(progress, "volume_writing_paused_for_human_review", {
                "project_slug": project.slug,
                "volume_number": vol_num,
                "chapters_written": len(vol_project_result.chapter_results),
                "final_verdict": vol_project_result.final_verdict,
            })
            break

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
