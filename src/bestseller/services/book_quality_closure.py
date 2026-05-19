from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterQualityReportModel,
    LlmRunModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
)
from bestseller.services.autonomous_book_repair import AUTONOMOUS_REPAIR_TRIGGER
from bestseller.services.legacy_book_acceptance_gate import (
    evaluate_legacy_book_acceptance,
)
from bestseller.services.llm import (
    LLMCompletionRequest,
    LLMCompletionResult,
    complete_text,
)
from bestseller.services.premium_book_gate import (
    evaluate_premium_project_readiness,
    premium_book_gate_report_to_dict,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.services.scorecard import compute_scorecard
from bestseller.settings import AppSettings

_REAL_PROVIDER_BLOCKLIST = {"mock", "fallback"}
_VERIFY_PREFIXES = ("verify-", "verify_")
_VERIFY_MARKERS = ("verify-", "verify_", "test-", "test_")


@dataclass(frozen=True, slots=True)
class LLMPreflightReport:
    ready: bool
    provider: str | None = None
    model_name: str | None = None
    finish_reason: str | None = None
    llm_run_id: str | None = None
    reason: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "ready": self.ready,
            "provider": self.provider,
            "model_name": self.model_name,
            "finish_reason": self.finish_reason,
            "llm_run_id": self.llm_run_id,
            "reason": self.reason,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class RewriteGenerationAudit:
    checked: int
    valid: int
    invalid: int
    gate_rejected: int = 0
    invalid_task_ids: tuple[str, ...] = ()
    invalid_generation_modes: tuple[str, ...] = ()
    gate_rejected_task_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "checked": self.checked,
            "valid": self.valid,
            "invalid": self.invalid,
            "gate_rejected": self.gate_rejected,
            "invalid_task_ids": list(self.invalid_task_ids),
            "invalid_generation_modes": list(self.invalid_generation_modes),
            "gate_rejected_task_ids": list(self.gate_rejected_task_ids),
        }


@dataclass(frozen=True, slots=True)
class ChapterGenerationAudit:
    checked: int
    valid: int
    invalid: int
    gate_rejected: int = 0
    invalid_chapter_numbers: tuple[int, ...] = ()
    invalid_generation_modes: tuple[str, ...] = ()
    gate_rejected_chapter_numbers: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "checked": self.checked,
            "valid": self.valid,
            "invalid": self.invalid,
            "gate_rejected": self.gate_rejected,
            "invalid_chapter_numbers": list(self.invalid_chapter_numbers),
            "invalid_generation_modes": list(self.invalid_generation_modes),
            "gate_rejected_chapter_numbers": list(
                self.gate_rejected_chapter_numbers
            ),
        }


@dataclass(frozen=True, slots=True)
class MissingChapterContinuationPlan:
    slug: str
    target_chapters: int
    planned_chapters: int
    current_chapters: int
    draftless_planned_chapters: int
    unplanned_chapters: int
    next_chapter_numbers: tuple[int, ...] = ()

    @property
    def has_executable_outline_batch(self) -> bool:
        return bool(self.next_chapter_numbers)

    @property
    def needs_outline_extension(self) -> bool:
        return self.unplanned_chapters > 0 and not self.next_chapter_numbers

    def to_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "target_chapters": self.target_chapters,
            "planned_chapters": self.planned_chapters,
            "current_chapters": self.current_chapters,
            "draftless_planned_chapters": self.draftless_planned_chapters,
            "unplanned_chapters": self.unplanned_chapters,
            "next_chapter_numbers": list(self.next_chapter_numbers),
            "has_executable_outline_batch": self.has_executable_outline_batch,
            "needs_outline_extension": self.needs_outline_extension,
        }


@dataclass(frozen=True, slots=True)
class FleetBookRow:
    slug: str
    status: str
    next_action: str
    category: str | None = None
    target_chapters: int = 0
    current_chapters: int = 0
    quality_score: float | None = None
    blocked_chapters: int = 0
    repair_tasks: int = 0
    missing_chapters: int = 0
    acceptance_status: str = "unknown"
    error: str | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "slug": self.slug,
            "category": self.category,
            "target_chapters": self.target_chapters,
            "current_chapters": self.current_chapters,
            "quality_score": self.quality_score,
            "blocked_chapters": self.blocked_chapters,
            "repair_tasks": self.repair_tasks,
            "missing_chapters": self.missing_chapters,
            "acceptance_status": self.acceptance_status,
            "status": self.status,
            "next_action": self.next_action,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class BookClosureReport:
    slug: str
    status: str
    next_action: str
    model_preflight: LLMPreflightReport | None = None
    bootstrap_report: Mapping[str, object] | None = None
    before_acceptance: Mapping[str, object] | None = None
    repair_plan: Mapping[str, object] = field(default_factory=dict)
    task_sync: Mapping[str, object] = field(default_factory=dict)
    execution: Mapping[str, object] = field(default_factory=dict)
    rewrite_generation_audit: RewriteGenerationAudit | None = None
    continuation_plan: Mapping[str, object] = field(default_factory=dict)
    continuation_execution: Mapping[str, object] = field(default_factory=dict)
    chapter_generation_audit: ChapterGenerationAudit | None = None
    after_acceptance: Mapping[str, object] | None = None
    fleet_row: FleetBookRow | None = None
    lifecycle_evidence: Mapping[str, object] | None = None
    lifecycle_quality: Mapping[str, object] | None = None
    report_paths: Mapping[str, str] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object | None]:
        return {
            "slug": self.slug,
            "status": self.status,
            "next_action": self.next_action,
            "model_preflight": (
                self.model_preflight.to_dict() if self.model_preflight else None
            ),
            "bootstrap_report": dict(self.bootstrap_report or {}),
            "before_acceptance": dict(self.before_acceptance or {}),
            "repair_plan": dict(self.repair_plan),
            "task_sync": dict(self.task_sync),
            "execution": dict(self.execution),
            "rewrite_generation_audit": (
                self.rewrite_generation_audit.to_dict()
                if self.rewrite_generation_audit
                else None
            ),
            "continuation_plan": dict(self.continuation_plan),
            "continuation_execution": dict(self.continuation_execution),
            "chapter_generation_audit": (
                self.chapter_generation_audit.to_dict()
                if self.chapter_generation_audit
                else None
            ),
            "after_acceptance": dict(self.after_acceptance or {}),
            "fleet_row": self.fleet_row.to_dict() if self.fleet_row else None,
            "lifecycle_evidence": dict(self.lifecycle_evidence or {}),
            "lifecycle_quality": dict(self.lifecycle_quality or {}),
            "report_paths": dict(self.report_paths),
            "errors": list(self.errors),
        }


def _as_mapping(value: object | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int(value: object | None, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_or_none(value: object | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _blocking_repair_task_count(repair_plan: Mapping[str, Any]) -> int:
    plan = (
        _as_mapping(repair_plan.get("repair_plan"))
        if "repair_plan" in repair_plan
        else repair_plan
    )
    priority_counts = _as_mapping(plan.get("priority_counts"))
    if priority_counts:
        return _int(priority_counts.get("critical")) + _int(priority_counts.get("high"))
    return _int(plan.get("task_count"))


def is_real_llm_provider(provider: str | None, *, finish_reason: str | None = None) -> bool:
    normalized = (provider or "").strip().lower()
    if not normalized or normalized in _REAL_PROVIDER_BLOCKLIST:
        return False
    if normalized.startswith("mock") or normalized.startswith("fallback"):
        return False
    reason = (finish_reason or "").strip().lower()
    return reason not in _REAL_PROVIDER_BLOCKLIST


async def run_llm_execution_preflight(
    session: AsyncSession,
    settings: AppSettings,
    *,
    timeout_seconds: float = 45.0,
    complete_text_fn: Callable[
        [AsyncSession, AppSettings, LLMCompletionRequest],
        Awaitable[LLMCompletionResult],
    ] = complete_text,
) -> LLMPreflightReport:
    request = LLMCompletionRequest(
        logical_role="editor",
        system_prompt="Return exactly: OK",
        user_prompt="LLM preflight. Return exactly: OK",
        fallback_response="FALLBACK_PRECHECK",
        prompt_template="book_quality_closure_preflight",
        prompt_version="1.0",
        max_tokens_override=256,
        metadata={"purpose": "book_quality_closure_preflight"},
    )
    try:
        result = await asyncio.wait_for(
            complete_text_fn(session, settings, request),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return LLMPreflightReport(
            ready=False,
            reason="provider_call_timeout",
            error=f"LLM preflight exceeded {timeout_seconds:.1f}s.",
        )
    except Exception as exc:
        return LLMPreflightReport(
            ready=False,
            reason="provider_call_failed",
            error=f"{type(exc).__name__}: {exc}",
        )

    ready = is_real_llm_provider(
        result.provider,
        finish_reason=result.finish_reason,
    )
    return LLMPreflightReport(
        ready=ready,
        provider=result.provider,
        model_name=result.model_name,
        finish_reason=result.finish_reason,
        llm_run_id=str(result.llm_run_id) if result.llm_run_id else None,
        reason=None if ready else "provider_returned_mock_or_fallback",
    )


def is_out_of_scope_slug(slug: str, *, include_verify: bool = False) -> bool:
    if include_verify:
        return False
    lowered = slug.lower()
    return lowered.startswith(_VERIFY_PREFIXES) or any(
        marker in lowered for marker in _VERIFY_MARKERS
    )


def filter_fleet_slugs(
    slugs: Sequence[str],
    *,
    include_verify: bool = False,
) -> list[str]:
    return [
        slug
        for slug in slugs
        if slug and not is_out_of_scope_slug(slug, include_verify=include_verify)
    ]


def determine_next_action(
    *,
    acceptance: Mapping[str, Any],
    repair_plan: Mapping[str, Any],
    model_preflight: LLMPreflightReport | None,
    execute_requested: bool,
    invalid_generation_count: int = 0,
) -> tuple[str, str]:
    acceptance_payload = _as_mapping(acceptance.get("acceptance") or acceptance)
    metrics = _as_mapping(acceptance_payload.get("metrics"))
    if invalid_generation_count > 0:
        return "blocked", "inspect_fallback_or_invalid_rewrites"
    if acceptance_payload.get("passed") is True:
        return "ready", "no_action"

    repair_tasks = _blocking_repair_task_count(repair_plan)
    if execute_requested and model_preflight and not model_preflight.ready and repair_tasks > 0:
        return "blocked", "fix_llm_provider_preflight"

    missing_chapters = _int(metrics.get("missing_chapters"))
    draftless_chapters = _int(metrics.get("draftless_chapters"))
    blocked_chapters = _int(metrics.get("chapters_blocked"))
    quality_score = _float_or_none(metrics.get("quality_score")) or 0.0

    if repair_tasks > 0 or blocked_chapters > 0:
        return "repairing", "execute_next_repair_round"
    if draftless_chapters > 0 or missing_chapters > 0:
        return "continuing", "generate_missing_chapters_under_state_gates"
    if quality_score < 80.0:
        return "blocked", "inspect_acceptance_findings"
    return "blocked", "inspect_acceptance_findings"


async def load_pending_autonomous_repair_task_ids(
    session: AsyncSession,
    project: ProjectModel,
    *,
    limit: int,
    max_priority: int | None = 2,
) -> list[str]:
    latest_quality_at = (
        select(
            ChapterQualityReportModel.chapter_id,
            func.max(ChapterQualityReportModel.created_at).label("latest_created_at"),
        )
        .where(
            ChapterQualityReportModel.chapter_id.in_(
                select(ChapterModel.id).where(ChapterModel.project_id == project.id)
            )
        )
        .group_by(ChapterQualityReportModel.chapter_id)
        .subquery()
    )
    fresh_blocked_rows = list(
        (
            await session.execute(
                select(ChapterQualityReportModel.chapter_id)
                .join(
                    latest_quality_at,
                    (
                        ChapterQualityReportModel.chapter_id
                        == latest_quality_at.c.chapter_id
                    )
                    & (
                        ChapterQualityReportModel.created_at
                        == latest_quality_at.c.latest_created_at
                    ),
                )
                .join(
                    ChapterDraftVersionModel,
                    (
                        ChapterDraftVersionModel.chapter_id
                        == ChapterQualityReportModel.chapter_id
                    )
                    & ChapterDraftVersionModel.is_current.is_(True),
                    isouter=True,
                )
                .where(
                    ChapterQualityReportModel.blocks_write.is_(True),
                    or_(
                        ChapterDraftVersionModel.created_at.is_(None),
                        ChapterQualityReportModel.created_at
                        >= ChapterDraftVersionModel.created_at,
                    ),
                )
            )
        ).scalars()
    )
    fresh_blocked_ids = tuple(fresh_blocked_rows)
    order_by = []
    if fresh_blocked_ids:
        order_by.append(
            case((RewriteTaskModel.trigger_source_id.in_(fresh_blocked_ids), 0), else_=1)
        )
    order_by.extend(
        [
            case((ChapterModel.production_state == "blocked", 0), else_=1),
            case((ChapterModel.status == "revision", 0), else_=1),
            RewriteTaskModel.priority.asc(),
            RewriteTaskModel.created_at.asc(),
        ]
    )
    stmt = (
        select(RewriteTaskModel)
        .join(ChapterModel, RewriteTaskModel.trigger_source_id == ChapterModel.id, isouter=True)
        .where(
            RewriteTaskModel.project_id == project.id,
            RewriteTaskModel.trigger_type == AUTONOMOUS_REPAIR_TRIGGER,
            RewriteTaskModel.status.in_(["pending", "queued"]),
        )
        .order_by(*order_by)
    )
    if max_priority is not None:
        stmt = stmt.where(RewriteTaskModel.priority <= int(max_priority))
    if limit > 0:
        stmt = stmt.limit(limit)
    rows = list((await session.execute(stmt)).scalars())
    return [str(row.id) for row in rows if row.id is not None]


def _generation_mode_is_valid(value: object) -> bool:
    mode = str(value or "").strip().lower()
    return bool(mode) and is_real_llm_provider(mode)


def _compact_text(value: object | None, *, limit: int = 120) -> str:
    text = " ".join(str(value or "").strip().split())
    return text[:limit]


def _participant_label(participants: object) -> str:
    if isinstance(participants, Sequence) and not isinstance(participants, (str, bytes)):
        names = [str(item).strip() for item in participants if str(item).strip()]
    else:
        names = []
    return "、".join(names[:4]) if names else "本场人物"


def build_legacy_scene_story_purpose(
    *,
    scene_anchor: object | None,
    chapter_goal: object | None,
    main_conflict: object | None,
    participants: object,
    language: str = "zh-CN",
) -> str:
    """Build a concrete continuation purpose for thin legacy scene cards."""

    anchor = _compact_text(scene_anchor, limit=80) or "推进本场关键行动"
    goal = _compact_text(chapter_goal, limit=140) or "承接本章主线目标"
    conflict = _compact_text(main_conflict, limit=140) or "当前压力"
    people = _participant_label(participants)
    if language.startswith("zh"):
        return (
            f"{anchor}：围绕“{goal}”，让{people}在“{conflict}”的压力下完成"
            "可见行动、获得具体线索，并把下一场必须追查的问题留在台面上。"
        )
    return (
        f"{anchor}: tie the scene to '{goal}', force {people} to act under "
        f"'{conflict}', surface a concrete clue, and leave the next required "
        "question visible."
    )


def build_legacy_scene_emotion_purpose(
    *,
    emotion_anchor: object | None,
    main_conflict: object | None,
    language: str = "zh-CN",
) -> str:
    emotion = _compact_text(emotion_anchor, limit=80)
    conflict = _compact_text(main_conflict, limit=120)
    if language.startswith("zh"):
        base = emotion or "惊疑、克制与判断压力"
        suffix = f"，并被“{conflict}”继续加压" if conflict else ""
        return f"{base}从表层反应推进到必须承担代价的选择{suffix}。"
    base = emotion or "unease, restraint, and pressure to judge"
    suffix = f" under '{conflict}'" if conflict else ""
    return f"{base} moves from surface reaction into a costly choice{suffix}."


def repair_legacy_scene_card_for_continuation(
    *,
    chapter: ChapterModel,
    scene: SceneCardModel,
    language: str = "zh-CN",
) -> tuple[bool, tuple[str, ...]]:
    from bestseller.services.scene_plan_richness import (
        repair_scene_model_state_defaults,
        validate_scene_model,
    )

    report = validate_scene_model(scene, language=language)
    issue_codes = tuple(issue.code for issue in report.issues)
    story_issue_codes = {
        "missing_story_purpose",
        "story_purpose_too_short",
        "story_purpose_generic_template",
    }
    emotion_issue_codes = {
        "missing_emotion_purpose",
        "emotion_purpose_too_short",
        "emotion_purpose_generic_template",
    }
    changed = False
    purpose = dict(scene.purpose or {})
    if any(code in story_issue_codes for code in issue_codes):
        purpose["story"] = build_legacy_scene_story_purpose(
            scene_anchor=purpose.get("story") or scene.title,
            chapter_goal=chapter.chapter_goal,
            main_conflict=chapter.main_conflict,
            participants=scene.participants,
            language=language,
        )
        changed = True
    if any(code in emotion_issue_codes for code in issue_codes):
        purpose["emotion"] = build_legacy_scene_emotion_purpose(
            emotion_anchor=purpose.get("emotion"),
            main_conflict=chapter.main_conflict,
            language=language,
        )
        changed = True
    if changed:
        scene.purpose = purpose
    if repair_scene_model_state_defaults(scene, language=language):
        changed = True
    if changed:
        meta = dict(scene.metadata_json or {})
        meta["legacy_continuation_scene_repair"] = {
            "issue_codes": list(issue_codes),
            "repair_source": "book_quality_closure",
        }
        scene.metadata_json = meta
    return changed, issue_codes


async def repair_legacy_scene_cards_for_continuation(
    session: AsyncSession,
    project: ProjectModel,
    chapter_numbers: Sequence[int],
) -> dict[str, object]:
    requested = tuple(int(number) for number in chapter_numbers if int(number) > 0)
    if not requested:
        return {"checked": 0, "repaired": 0, "unresolved": 0, "chapters": []}
    rows = list(
        (
            await session.execute(
                select(ChapterModel)
                .options(selectinload(ChapterModel.scenes))
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number.in_(requested),
                )
                .order_by(ChapterModel.chapter_number.asc())
            )
        ).scalars()
    )
    from bestseller.services.scene_plan_richness import validate_scene_model

    language = str(project.language or "zh-CN")
    checked = 0
    repaired = 0
    unresolved: list[dict[str, object]] = []
    repaired_chapters: set[int] = set()
    for chapter in rows:
        for scene in sorted(chapter.scenes, key=lambda item: item.scene_number):
            checked += 1
            changed, _issue_codes = repair_legacy_scene_card_for_continuation(
                chapter=chapter,
                scene=scene,
                language=language,
            )
            if changed:
                repaired += 1
                repaired_chapters.add(int(chapter.chapter_number))
            after = validate_scene_model(scene, language=language)
            if after.severity == "critical":
                unresolved.append(
                    {
                        "chapter_number": int(chapter.chapter_number),
                        "scene_number": int(scene.scene_number),
                        "issue_codes": [issue.code for issue in after.issues],
                    }
                )
    if repaired:
        await session.flush()
    return {
        "checked": checked,
        "repaired": repaired,
        "unresolved": len(unresolved),
        "unresolved_scenes": unresolved,
        "chapters": sorted(repaired_chapters),
    }


async def audit_rewrite_task_generation_modes(
    session: AsyncSession,
    task_ids: Sequence[str],
) -> RewriteGenerationAudit:
    if not task_ids:
        return RewriteGenerationAudit(checked=0, valid=0, invalid=0)
    parsed_ids: list[UUID] = []
    for task_id in task_ids:
        try:
            parsed_ids.append(UUID(str(task_id)))
        except ValueError:
            continue
    if not parsed_ids:
        return RewriteGenerationAudit(checked=0, valid=0, invalid=0)
    rows = list(
        (
            await session.execute(
                select(RewriteTaskModel).where(RewriteTaskModel.id.in_(parsed_ids))
            )
        ).scalars()
    )
    invalid_ids: list[str] = []
    invalid_modes: list[str] = []
    rejected_ids: list[str] = []
    valid = 0
    for task in rows:
        metadata = _as_mapping(task.metadata_json)
        mode = metadata.get("generation_mode")
        execution_error = str(
            metadata.get("closure_execution_error")
            or getattr(task, "error_log", "")
            or ""
        )
        execution_timed_out = (
            str(getattr(task, "status", "") or "") == "failed"
            and "timeouterror" in execution_error.lower()
        )
        if execution_timed_out and not mode:
            rejected_ids.append(str(task.id))
            continue
        if not _generation_mode_is_valid(mode):
            invalid_ids.append(str(task.id))
            invalid_modes.append(str(mode or "missing"))
            continue
        if task.status == "completed":
            valid += 1
            continue
        rejected_ids.append(str(task.id))
    return RewriteGenerationAudit(
        checked=len(rows),
        valid=valid,
        invalid=len(invalid_ids),
        gate_rejected=len(rejected_ids),
        invalid_task_ids=tuple(invalid_ids),
        invalid_generation_modes=tuple(invalid_modes),
        gate_rejected_task_ids=tuple(rejected_ids),
    )


async def count_current_chapter_drafts(
    session: AsyncSession,
    project: ProjectModel,
) -> int:
    value = await session.scalar(
        select(func.count(ChapterDraftVersionModel.id)).where(
            ChapterDraftVersionModel.project_id == project.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    return int(value or 0)


async def count_planned_chapters_without_current_draft(
    session: AsyncSession,
    project: ProjectModel,
) -> int:
    value = await session.scalar(
        select(func.count(ChapterModel.id))
        .select_from(ChapterModel)
        .join(
            ChapterDraftVersionModel,
            (
                (ChapterDraftVersionModel.chapter_id == ChapterModel.id)
                & ChapterDraftVersionModel.is_current.is_(True)
            ),
            isouter=True,
        )
        .where(
            ChapterModel.project_id == project.id,
            ChapterDraftVersionModel.id.is_(None),
        )
    )
    return int(value or 0)


async def build_missing_chapter_continuation_plan(
    session: AsyncSession,
    project: ProjectModel,
    *,
    limit: int,
) -> MissingChapterContinuationPlan:
    planned_chapters = int(
        await session.scalar(
            select(func.count(ChapterModel.id)).where(
                ChapterModel.project_id == project.id
            )
        )
        or 0
    )
    current_chapters = await count_current_chapter_drafts(session, project)
    draftless_numbers = list(
        (
            await session.execute(
                select(ChapterModel.chapter_number)
                .select_from(ChapterModel)
                .join(
                    ChapterDraftVersionModel,
                    (
                        (ChapterDraftVersionModel.chapter_id == ChapterModel.id)
                        & ChapterDraftVersionModel.is_current.is_(True)
                    ),
                    isouter=True,
                )
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterDraftVersionModel.id.is_(None),
                )
                .order_by(ChapterModel.chapter_number.asc())
                .limit(max(int(limit or 0), 1))
            )
        ).scalars()
    )
    target_chapters = int(project.target_chapters or 0)
    return MissingChapterContinuationPlan(
        slug=project.slug,
        target_chapters=target_chapters,
        planned_chapters=planned_chapters,
        current_chapters=current_chapters,
        draftless_planned_chapters=max(planned_chapters - current_chapters, 0),
        unplanned_chapters=max(target_chapters - planned_chapters, 0),
        next_chapter_numbers=tuple(int(chapter_no) for chapter_no in draftless_numbers),
    )


async def audit_chapter_generation_modes(
    session: AsyncSession,
    project: ProjectModel,
    chapter_numbers: Sequence[int],
) -> ChapterGenerationAudit:
    requested = tuple(int(number) for number in chapter_numbers if int(number) > 0)
    if not requested:
        return ChapterGenerationAudit(checked=0, valid=0, invalid=0)
    rows = list(
        (
            await session.execute(
                select(
                    ChapterModel.chapter_number,
                    ChapterModel.status,
                    ChapterModel.production_state,
                    LlmRunModel.provider,
                    LlmRunModel.finish_reason,
                )
                .select_from(ChapterModel)
                .join(
                    ChapterDraftVersionModel,
                    (
                        (ChapterDraftVersionModel.chapter_id == ChapterModel.id)
                        & ChapterDraftVersionModel.is_current.is_(True)
                    ),
                    isouter=True,
                )
                .join(
                    LlmRunModel,
                    LlmRunModel.id == ChapterDraftVersionModel.llm_run_id,
                    isouter=True,
                )
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number.in_(requested),
                )
            )
        ).all()
    )
    seen: set[int] = set()
    invalid_numbers: list[int] = []
    invalid_modes: list[str] = []
    gate_rejected_numbers: list[int] = []
    valid = 0
    for chapter_number, status, production_state, provider, finish_reason in rows:
        number = int(chapter_number)
        seen.add(number)
        if str(production_state or "") != "ok" or str(status or "") != "complete":
            gate_rejected_numbers.append(number)
            continue
        if is_real_llm_provider(provider, finish_reason=finish_reason):
            valid += 1
            continue
        invalid_numbers.append(number)
        invalid_modes.append(str(provider or "missing_llm_run"))
    for number in requested:
        if number not in seen:
            invalid_numbers.append(number)
            invalid_modes.append("chapter_missing")
    return ChapterGenerationAudit(
        checked=len(rows),
        valid=valid,
        invalid=len(invalid_numbers),
        gate_rejected=len(gate_rejected_numbers),
        invalid_chapter_numbers=tuple(invalid_numbers),
        invalid_generation_modes=tuple(invalid_modes),
        gate_rejected_chapter_numbers=tuple(gate_rejected_numbers),
    )


async def build_legacy_acceptance_payload(
    session: AsyncSession,
    settings: AppSettings,
    slug: str,
    *,
    repair_plan: Mapping[str, Any] | None = None,
    model_execution_ready: bool,
) -> dict[str, object]:
    project = await get_project_by_slug(session, slug)
    if project is None:
        raise ValueError(f"Project '{slug}' was not found.")
    scorecard = await compute_scorecard(
        session,
        project.id,
        expected_chapter_count=project.target_chapters,
    )
    premium_report = evaluate_premium_project_readiness(project)
    current_chapters = await count_current_chapter_drafts(session, project)
    draftless_chapters = await count_planned_chapters_without_current_draft(
        session,
        project,
    )
    scorecard_payload = {
        **scorecard.to_dict(),
        "current_chapters": current_chapters,
        "draftless_chapters": draftless_chapters,
    }
    acceptance = evaluate_legacy_book_acceptance(
        scorecard=scorecard_payload,
        premium_gate_report=premium_book_gate_report_to_dict(premium_report),
        repair_plan=repair_plan or {},
        model_execution_ready=model_execution_ready,
    )
    return {
        "slug": slug,
        "acceptance": acceptance.to_dict(),
        "scorecard": scorecard_payload,
        "premium_gate": premium_book_gate_report_to_dict(premium_report),
        "repair_plan": {
            "task_count": _int(_as_mapping(repair_plan).get("task_count")),
            "priority_counts": _as_mapping(repair_plan).get("priority_counts", {}),
            "cause_counts": _as_mapping(repair_plan).get("cause_counts", {}),
        },
        "current_chapters": current_chapters,
        "target_chapters": int(project.target_chapters or 0),
        "category": str(
            _as_mapping(project.metadata_json).get("canonical_category")
            or _as_mapping(project.metadata_json).get("category_key")
            or ""
        )
        or None,
    }


def fleet_row_from_acceptance(
    *,
    slug: str,
    acceptance_payload: Mapping[str, Any],
    status: str,
    next_action: str,
    error: str | None = None,
) -> FleetBookRow:
    acceptance = _as_mapping(acceptance_payload.get("acceptance"))
    scorecard = _as_mapping(acceptance_payload.get("scorecard"))
    repair_plan = _as_mapping(acceptance_payload.get("repair_plan"))
    return FleetBookRow(
        slug=slug,
        status=status,
        next_action=next_action,
        category=acceptance_payload.get("category") if isinstance(
            acceptance_payload.get("category"), str
        ) else None,
        target_chapters=_int(acceptance_payload.get("target_chapters")),
        current_chapters=_int(acceptance_payload.get("current_chapters")),
        quality_score=_float_or_none(scorecard.get("quality_score")),
        blocked_chapters=_int(scorecard.get("chapters_blocked")),
        repair_tasks=_blocking_repair_task_count(repair_plan),
        missing_chapters=_int(scorecard.get("missing_chapters")),
        acceptance_status=str(acceptance.get("readiness_level") or "unknown"),
        error=error,
    )


def blocked_fleet_row(slug: str, *, error: str, next_action: str) -> FleetBookRow:
    return FleetBookRow(
        slug=slug,
        status="blocked",
        next_action=next_action,
        acceptance_status="blocked",
        error=error,
    )


def out_of_scope_fleet_row(slug: str) -> FleetBookRow:
    return FleetBookRow(
        slug=slug,
        status="out_of_scope",
        next_action="skip_verify_or_test_output",
        acceptance_status="out_of_scope",
    )


__all__ = [
    "BookClosureReport",
    "ChapterGenerationAudit",
    "FleetBookRow",
    "LLMPreflightReport",
    "MissingChapterContinuationPlan",
    "RewriteGenerationAudit",
    "audit_chapter_generation_modes",
    "audit_rewrite_task_generation_modes",
    "blocked_fleet_row",
    "build_legacy_acceptance_payload",
    "build_legacy_scene_emotion_purpose",
    "build_legacy_scene_story_purpose",
    "build_missing_chapter_continuation_plan",
    "count_planned_chapters_without_current_draft",
    "determine_next_action",
    "filter_fleet_slugs",
    "fleet_row_from_acceptance",
    "is_out_of_scope_slug",
    "is_real_llm_provider",
    "load_pending_autonomous_repair_task_ids",
    "out_of_scope_fleet_row",
    "repair_legacy_scene_card_for_continuation",
    "repair_legacy_scene_cards_for_continuation",
    "run_llm_execution_preflight",
]
