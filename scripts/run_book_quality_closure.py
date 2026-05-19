"""Run the historical-book quality closure loop.

The runner bootstraps legacy state, builds/refreshes the repair plan,
optionally executes bounded rewrite or continuation batches, then reruns the
whole-book acceptance gate and fleet summary. By default it still performs one
round for compatibility, but ``--max-rounds`` can keep the loop moving until the
book is ready, blocked, stalled, or the round cap is reached.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any
from uuid import UUID

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
_SCRIPTS = _REPO_ROOT / "scripts"
for item in (_SRC, _SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

import autonomous_book_repair as repair_runner  # noqa: E402

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.autonomous_book_repair import (  # noqa: E402
    QualityRepairTaskSpec,
    create_quality_retrofit_rewrite_tasks,
    discover_output_book_slugs,
)
from bestseller.services.book_lifecycle_evidence import (  # noqa: E402
    build_book_lifecycle_evidence_payload,
)
from bestseller.services.book_lifecycle_evidence_repair import (  # noqa: E402
    repair_book_lifecycle_evidence,
)
from bestseller.services.book_lifecycle_quality_gate import (  # noqa: E402
    build_lifecycle_quality_report_from_closure,
)
from bestseller.services.book_quality_closure import (  # noqa: E402
    BookClosureReport,
    ChapterGenerationAudit,
    FleetBookRow,
    LLMPreflightReport,
    RewriteGenerationAudit,
    audit_chapter_generation_modes,
    audit_rewrite_task_generation_modes,
    blocked_fleet_row,
    build_legacy_acceptance_payload,
    build_missing_chapter_continuation_plan,
    determine_next_action,
    filter_fleet_slugs,
    fleet_row_from_acceptance,
    is_out_of_scope_slug,
    load_pending_autonomous_repair_task_ids,
    out_of_scope_fleet_row,
    repair_legacy_scene_cards_for_continuation,
    run_llm_execution_preflight,
)
from bestseller.services.legacy_book_state_bootstrap import (  # noqa: E402
    bootstrap_legacy_project_state,
)
from bestseller.services.projects import get_project_by_slug  # noqa: E402
from bestseller.settings import AppSettings, load_settings  # noqa: E402
from bestseller.infra.db.models import RewriteTaskModel  # noqa: E402

PREFERRED_BOOK_ORDER = (
    "exorcist-detective-1778051012",
    "exorcist-detective-1778428166",
    "romantasy-1776330993",
    "female-no-cp-1776303225",
    "xianxia-upgrade-1776137730",
)
DEFAULT_REPAIR_AUDIT_PLATFORM = "framework"
MAX_NO_METRIC_PROGRESS_ROUNDS = 3
_NO_EXECUTION_PROGRESS_REASONS = {
    "no_pending_rewrite_tasks",
    "no_missing_planned_chapters",
}


def _json_dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _book_dir(settings: AppSettings, slug: str) -> Path:
    return Path(settings.output.base_dir) / slug


def _empty_preflight(*, reason: str) -> LLMPreflightReport:
    return LLMPreflightReport(ready=False, reason=reason)


def _repair_plan_summary(payload: object | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"task_count": 0, "priority_counts": {}, "cause_counts": {}}
    if "task_count" in payload:
        return {
            "task_count": int(payload.get("task_count") or 0),
            "priority_counts": dict(payload.get("priority_counts") or {}),
            "cause_counts": dict(payload.get("cause_counts") or {}),
        }
    nested = payload.get("repair_plan")
    if isinstance(nested, dict):
        return _repair_plan_summary(nested)
    return {"task_count": 0, "priority_counts": {}, "cause_counts": {}}


def _load_existing_repair_plan(settings: AppSettings, slug: str) -> dict[str, Any]:
    path = (
        _book_dir(settings, slug)
        / "audits"
        / "quality-retrofit"
        / "autonomous-repair-plan.json"
    )
    if not path.exists():
        return {"task_count": 0, "priority_counts": {}, "cause_counts": {}}
    try:
        return _repair_plan_summary(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {"task_count": 0, "priority_counts": {}, "cause_counts": {}}


def _sort_slugs(slugs: list[str]) -> list[str]:
    preferred_index = {slug: index for index, slug in enumerate(PREFERRED_BOOK_ORDER)}
    return sorted(slugs, key=lambda slug: (preferred_index.get(slug, 9999), slug))


def _progress_signature(report: BookClosureReport) -> tuple[object, ...]:
    acceptance = (
        report.after_acceptance.get("acceptance", {})
        if isinstance(report.after_acceptance, dict)
        else {}
    )
    metrics = acceptance.get("metrics", {}) if isinstance(acceptance, dict) else {}
    scorecard = (
        report.after_acceptance.get("scorecard", {})
        if isinstance(report.after_acceptance, dict)
        else {}
    )
    if not isinstance(metrics, dict):
        metrics = {}
    if not isinstance(scorecard, dict):
        scorecard = {}
    return (
        report.status,
        report.next_action,
        int(scorecard.get("current_chapters") or 0),
        int(metrics.get("chapters_blocked") or 0),
        int(metrics.get("repair_task_count") or 0),
        int(metrics.get("draftless_chapters") or 0),
        int(metrics.get("missing_chapters") or 0),
        round(float(metrics.get("quality_score") or 0.0), 4),
    )


def _write_lifecycle_quality_report(
    settings: AppSettings,
    slug: str,
    closure_payload: dict[str, object | None],
) -> tuple[dict[str, object], str]:
    lifecycle_report = build_lifecycle_quality_report_from_closure(closure_payload)
    lifecycle_path = (
        _book_dir(settings, slug)
        / "audits"
        / "lifecycle-quality"
        / "report.json"
    )
    payload = lifecycle_report.to_dict()
    _json_dump(lifecycle_path, payload)
    return payload, str(lifecycle_path)


async def _build_lifecycle_evidence(
    settings: AppSettings,
    slug: str,
) -> tuple[dict[str, object], str]:
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return {"slug": slug, "error": "project_not_found_in_db"}, ""
        payload = await build_book_lifecycle_evidence_payload(session, project)
    evidence_path = (
        _book_dir(settings, slug)
        / "audits"
        / "lifecycle-evidence"
        / "report.json"
    )
    _json_dump(evidence_path, payload)
    return payload, str(evidence_path)


async def _repair_lifecycle_evidence(
    settings: AppSettings,
    slug: str,
    *,
    dry_run: bool,
) -> tuple[dict[str, object], str]:
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return {"slug": slug, "error": "project_not_found_in_db"}, ""
        report = await repair_book_lifecycle_evidence(
            session,
            project,
            package_dir=_book_dir(settings, slug),
            dry_run=dry_run,
        )
    payload = report.to_dict()
    report_path = (
        _book_dir(settings, slug)
        / "audits"
        / "lifecycle-evidence-repair"
        / "report.json"
    )
    _json_dump(report_path, payload)
    return payload, str(report_path)


def _lifecycle_execution_override(
    *,
    status: str,
    next_action: str,
    lifecycle_payload: dict[str, object],
) -> tuple[str, str]:
    if lifecycle_payload.get("passed") is True:
        return status, next_action
    findings = [
        item
        for item in lifecycle_payload.get("findings", [])
        if isinstance(item, dict) and item.get("severity") == "critical"
    ]
    critical_domains = {str(item.get("domain") or "") for item in findings}
    if "planning" in critical_domains:
        return "blocked", "repair_lifecycle_planning_evidence"
    if "character" in critical_domains:
        return "blocked", "repair_lifecycle_character_evidence"
    if status == "ready" and "anti_copy" in critical_domains:
        return "blocked", "run_reference_distance_gate"
    return status, next_action


def _can_continue_closure_loop(
    report: BookClosureReport,
    *,
    execute_requested: bool,
    dry_run: bool,
    model_preflight: LLMPreflightReport,
) -> bool:
    if dry_run or not execute_requested:
        return False
    if report.status == "ready":
        return False
    if report.status == "out_of_scope":
        return False
    if not model_preflight.ready and report.next_action == "fix_llm_provider_preflight":
        return False
    if report.next_action in {
        "inspect_fallback_or_invalid_rewrites",
        "inspect_closure_runner_failure",
        "import_or_recover_project_before_closure",
        "inspect_acceptance_findings",
        "skip_verify_or_test_output",
    }:
        return False
    return report.next_action in {
        "execute_next_repair_round",
        "generate_missing_chapters_under_state_gates",
    }


async def _run_preflight(
    settings: AppSettings,
    *,
    execute_requested: bool,
    dry_run: bool,
    timeout_seconds: float,
) -> LLMPreflightReport:
    if dry_run:
        return _empty_preflight(reason="dry_run")
    if not execute_requested:
        return _empty_preflight(reason="execute_not_requested")
    async with session_scope(settings) as session:
        return await run_llm_execution_preflight(
            session,
            settings,
            timeout_seconds=timeout_seconds,
        )


async def _run_bootstrap(
    settings: AppSettings,
    slug: str,
    *,
    dry_run: bool,
) -> tuple[dict[str, object] | None, FleetBookRow | None, str | None]:
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            row = blocked_fleet_row(
                slug,
                error="project_not_found_in_db",
                next_action="import_or_recover_project_before_closure",
            )
            return None, row, "project_not_found_in_db"
        report = await bootstrap_legacy_project_state(
            session,
            project,
            package_dir=_book_dir(settings, slug),
            dry_run=dry_run,
        )
    payload = report.to_dict()
    report_path = (
        _book_dir(settings, slug)
        / "audits"
        / "legacy-state-bootstrap"
        / "report.json"
    )
    _json_dump(report_path, payload)
    return {**payload, "report_path": str(report_path)}, None, None


async def _acceptance_payload(
    settings: AppSettings,
    slug: str,
    *,
    repair_plan: dict[str, Any],
    model_preflight: LLMPreflightReport,
) -> dict[str, object]:
    async with session_scope(settings) as session:
        return await build_legacy_acceptance_payload(
            session,
            settings,
            slug,
            repair_plan=repair_plan,
            model_execution_ready=model_preflight.ready,
        )


async def _execute_repair_round(
    settings: AppSettings,
    slug: str,
    *,
    round_size: int,
    model_preflight: LLMPreflightReport,
    task_ids: list[str] | None = None,
    task_timeout_seconds: float | None = None,
) -> tuple[dict[str, object], list[str]]:
    if not model_preflight.ready:
        return {
            "skipped": True,
            "reason": model_preflight.reason or "llm_preflight_failed",
        }, []

    selected_task_ids = _bounded_task_ids(task_ids or [], round_size)
    selected_task_ids = await _filter_task_ids_by_status(
        settings,
        slug,
        selected_task_ids,
    )
    if not selected_task_ids:
        async with session_scope(settings) as session:
            project = await get_project_by_slug(session, slug)
            if project is None:
                return {"skipped": True, "reason": "project_not_found_in_db"}, []
            selected_task_ids = await load_pending_autonomous_repair_task_ids(
                session,
                project,
                limit=round_size,
            )
    if not selected_task_ids:
        return {"skipped": True, "reason": "no_pending_rewrite_tasks"}, []

    execution = await repair_runner._execute_tasks(
        slug,
        selected_task_ids,
        limit=None,
        task_timeout_seconds=task_timeout_seconds,
    )
    return {**execution, "task_ids": selected_task_ids}, selected_task_ids


async def _filter_task_ids_by_status(
    settings: AppSettings,
    slug: str,
    task_ids: list[str],
) -> list[str]:
    if not task_ids:
        return []
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return []
        from sqlalchemy import select

        ids = []
        for raw_task_id in task_ids:
            try:
                ids.append(UUID(raw_task_id))
            except (TypeError, ValueError):
                continue
        if not ids:
            return []
        rows = list(
            (
                await session.execute(
                    select(RewriteTaskModel.id).where(
                        RewriteTaskModel.project_id == project.id,
                        RewriteTaskModel.id.in_(ids),
                        RewriteTaskModel.status.in_(["pending", "queued"]),
                    )
                )
            ).scalars()
        )
        filtered = [str(item) for item in rows if item is not None]
        if filtered:
            return filtered
        return []


def _bounded_task_ids(task_ids: list[str], round_size: int) -> list[str]:
    limit = max(int(round_size or 0), 1)
    return [str(task_id) for task_id in task_ids if str(task_id)][:limit]


def _float_metric(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_metric(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_ACCEPTANCE_GAP_REPAIR_ACTIONS: dict[str, str] = {
    "GOLDEN_THREE_WEAK": (
        "重建黄金三章的直接冲突、可见损失、读者爽点和章末钩子，"
        "让开篇从第一屏就进入可追读状态。"
    ),
    "HYPE_ASSIGNMENT_MISSING": (
        "补齐本章读者爽点类型、强度和兑现节奏，让情绪奖励可被 scorecard 识别。"
    ),
    "LENGTH_STABILITY_BELOW_BAR": (
        "在不破坏正典状态的前提下，把章节改写到目标字数附近，删除水化重复并补足有效情节。"
    ),
    "SCORECARD_BELOW_ACCEPTANCE_BAR": (
        "提升商业悬念密度、行动推进、信息差兑现和段落可读性，修复整书分数低的章节侧证。"
    ),
}


def _acceptance_gap_repair_specs_from_scorecard(
    *,
    slug: str,
    acceptance_payload: dict[str, object],
    chapter_rows: list[dict[str, object]],
) -> list[QualityRepairTaskSpec]:
    acceptance = (
        acceptance_payload.get("acceptance")
        if isinstance(acceptance_payload.get("acceptance"), dict)
        else {}
    )
    scorecard = (
        acceptance_payload.get("scorecard")
        if isinstance(acceptance_payload.get("scorecard"), dict)
        else {}
    )
    thresholds = (
        acceptance.get("thresholds") if isinstance(acceptance, dict) else {}
    )
    metrics = acceptance.get("metrics") if isinstance(acceptance, dict) else {}
    if not isinstance(thresholds, dict):
        thresholds = {}
    if not isinstance(metrics, dict):
        metrics = {}

    min_score = _float_metric(thresholds.get("min_scorecard_quality_score"), 80.0)
    max_length_cv = _float_metric(thresholds.get("max_length_cv"), 0.10)
    score = _float_metric(scorecard.get("quality_score") or metrics.get("quality_score"))
    length_cv = _float_metric(scorecard.get("length_cv") or metrics.get("length_cv"))
    hype_missing = _int_metric(
        scorecard.get("hype_missing_chapters") or metrics.get("hype_missing_chapters")
    )
    golden_three_weak = bool(scorecard.get("golden_three_weak"))

    causes_by_chapter: dict[int, set[str]] = {}
    details_by_chapter: dict[int, list[str]] = {}

    def add(number: int, cause: str, detail: str) -> None:
        causes_by_chapter.setdefault(number, set()).add(cause)
        details_by_chapter.setdefault(number, []).append(detail)

    sorted_rows = sorted(
        chapter_rows,
        key=lambda item: _int_metric(item.get("chapter_number")),
    )
    row_by_chapter = {
        _int_metric(row.get("chapter_number")): row
        for row in sorted_rows
        if _int_metric(row.get("chapter_number")) > 0
    }
    if golden_three_weak:
        for row in sorted_rows:
            chapter_number = _int_metric(row.get("chapter_number"))
            if 1 <= chapter_number <= 3:
                add(
                    chapter_number,
                    "GOLDEN_THREE_WEAK",
                    "黄金三章未达到榜单级开篇吸引力，需要重建冲突-损失-爽点-钩子链路。",
                )

    if hype_missing > 0:
        for row in sorted_rows:
            chapter_number = _int_metric(row.get("chapter_number"))
            if chapter_number <= 0:
                continue
            if not str(row.get("hype_type") or "").strip():
                add(
                    chapter_number,
                    "HYPE_ASSIGNMENT_MISSING",
                    "本章缺少可追踪的 reader-hype 类型，整书爽点节奏无法闭环评估。",
                )

    if length_cv > max_length_cv:
        for row in sorted_rows:
            chapter_number = _int_metric(row.get("chapter_number"))
            target = max(_int_metric(row.get("target_word_count")), 1)
            words = _int_metric(row.get("word_count"))
            if chapter_number <= 0 or words <= 0:
                continue
            ratio = words / target
            if ratio < 0.90 or ratio > 1.10:
                direction = "偏短" if ratio < 0.90 else "偏长"
                add(
                    chapter_number,
                    "LENGTH_STABILITY_BELOW_BAR",
                    f"当前约 {words} 字，目标 {target} 字，章节{direction}导致整书长度稳定性不达标。",
                )

    if score < min_score:
        selected = set(causes_by_chapter)
        if not selected:
            selected = {
                _int_metric(row.get("chapter_number"))
                for row in sorted_rows[:5]
                if _int_metric(row.get("chapter_number")) > 0
            }
        for chapter_number in selected:
            add(
                chapter_number,
                "SCORECARD_BELOW_ACCEPTANCE_BAR",
                f"整书 scorecard={score:.2f}，低于 acceptance 标准 {min_score:.2f}。",
            )

    specs: list[QualityRepairTaskSpec] = []
    for chapter_number in sorted(causes_by_chapter):
        cause_ids = tuple(sorted(causes_by_chapter[chapter_number]))
        chapter_row = row_by_chapter.get(chapter_number, {})
        target = max(_int_metric(chapter_row.get("target_word_count")), 1)
        words = _int_metric(chapter_row.get("word_count"))
        if target > 0 and words > 0:
            length_ratio = words / target
            if length_ratio < 0.90:
                word_count_reason = "underflow"
            elif length_ratio > 1.10:
                word_count_reason = "overflow"
            else:
                word_count_reason = "within_band"
        else:
            length_ratio = 0.0
            word_count_reason = ""
        patch_points = tuple(
            {
                "cause_id": cause,
                "location": f"第{chapter_number}章",
                "issue_summary": "；".join(details_by_chapter.get(chapter_number, [])),
                "repair_action_summary": _ACCEPTANCE_GAP_REPAIR_ACTIONS.get(
                    cause,
                    "按整书 acceptance gate 的失败项重写本章，并保持正典账本一致。",
                ),
                "snippet": "",
            }
            for cause in cause_ids
        )
        critical = "GOLDEN_THREE_WEAK" in cause_ids or "LENGTH_STABILITY_BELOW_BAR" in cause_ids
        specs.append(
            QualityRepairTaskSpec(
                slug=slug,
                chapter_number=chapter_number,
                priority="critical" if critical else "high",
                task_priority=1 if critical else 2,
                cause_ids=cause_ids,
                patch_points=patch_points,
                audit_row={
                "chapter_number": str(chapter_number),
                "priority": "critical" if critical else "high",
                "cause_ids": ";".join(cause_ids),
                "target_word_count": str(target if target > 0 else ""),
                "char_count": str(words if words > 0 else ""),
                "word_count_reason": word_count_reason,
                "length_ratio": f"{length_ratio:.4f}",
                "word_count_passed": str(word_count_reason == "within_band").lower(),
                "scorecard_quality_score": f"{score:.2f}",
                "length_cv": f"{length_cv:.4f}",
                "hype_missing_chapters": str(hype_missing),
                    "golden_three_weak": str(golden_three_weak).lower(),
                    "source": "legacy_acceptance_gap",
                },
            )
        )
    return specs


async def _sync_acceptance_gap_repair_tasks(
    settings: AppSettings,
    slug: str,
    acceptance_payload: dict[str, object],
    *,
    replace_existing: bool,
) -> tuple[dict[str, object], dict[str, Any]]:
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return {"db_project_found": False, "task_ids": []}, {}
        from sqlalchemy import select

        from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel

        rows = list(
            (
                await session.execute(
                    select(
                        ChapterModel.chapter_number,
                        ChapterModel.target_word_count,
                        ChapterDraftVersionModel.word_count,
                        ChapterModel.hype_type,
                    )
                    .join(
                        ChapterDraftVersionModel,
                        (
                            ChapterDraftVersionModel.chapter_id == ChapterModel.id
                        )
                        & ChapterDraftVersionModel.is_current.is_(True),
                    )
                    .where(ChapterModel.project_id == project.id)
                    .order_by(ChapterModel.chapter_number.asc())
                )
            ).all()
        )
        chapter_rows = [
            {
                "chapter_number": chapter_number,
                "target_word_count": target_word_count,
                "word_count": word_count,
                "hype_type": hype_type,
            }
            for chapter_number, target_word_count, word_count, hype_type in rows
        ]
        specs = _acceptance_gap_repair_specs_from_scorecard(
            slug=slug,
            acceptance_payload=acceptance_payload,
            chapter_rows=chapter_rows,
        )
        if not specs:
            return {"db_project_found": True, "task_ids": []}, {}
        sync = await create_quality_retrofit_rewrite_tasks(
            session,
            project,
            specs,
            replace_existing=replace_existing,
        )
        cause_counts: dict[str, int] = {}
        priority_counts: dict[str, int] = {}
        for spec in specs:
            priority_counts[spec.priority] = priority_counts.get(spec.priority, 0) + 1
            for cause in spec.cause_ids:
                cause_counts[cause] = cause_counts.get(cause, 0) + 1
        plan = {
            "task_count": len(specs),
            "priority_counts": priority_counts,
            "cause_counts": cause_counts,
        }
        return {"db_project_found": True, **sync.to_dict()}, plan


async def _audit_generation_modes(
    settings: AppSettings,
    task_ids: list[str],
) -> RewriteGenerationAudit:
    if not task_ids:
        return RewriteGenerationAudit(checked=0, valid=0, invalid=0)
    async with session_scope(settings) as session:
        return await audit_rewrite_task_generation_modes(session, task_ids)


async def _sync_blocking_quality_gate_tasks(
    settings: AppSettings,
    slug: str,
    *,
    replace_existing: bool,
) -> tuple[dict[str, object], dict[str, Any]]:
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return {"db_project_found": False, "task_ids": []}, {}
        from sqlalchemy import func, select

        from bestseller.infra.db.models import (
            ChapterDraftVersionModel,
            ChapterModel,
            ChapterQualityReportModel,
        )

        latest_quality_at = (
            select(
                ChapterQualityReportModel.chapter_id,
                func.max(ChapterQualityReportModel.created_at).label("latest_created_at"),
            )
            .where(
                ChapterQualityReportModel.chapter_id.in_(
                    select(ChapterModel.id).where(
                        ChapterModel.project_id == project.id
                    )
                )
            )
            .group_by(ChapterQualityReportModel.chapter_id)
            .subquery()
        )
        rows = list(
            (
                await session.execute(
                    select(
                        ChapterModel.chapter_number,
                        ChapterQualityReportModel.report_json,
                    )
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
                        ChapterModel,
                        ChapterModel.id == ChapterQualityReportModel.chapter_id,
                    )
                    .join(
                        ChapterDraftVersionModel,
                        (
                            ChapterDraftVersionModel.chapter_id == ChapterModel.id
                        )
                        & ChapterDraftVersionModel.is_current.is_(True),
                    )
                    .where(
                        ChapterModel.project_id == project.id,
                        ChapterQualityReportModel.blocks_write.is_(True),
                    )
                    .order_by(ChapterModel.chapter_number.asc())
                )
            ).all()
        )
        specs: list[QualityRepairTaskSpec] = []
        cause_counts: dict[str, int] = {}
        for chapter_number, report_json in rows:
            payload = report_json if isinstance(report_json, dict) else {}
            violations = [
                dict(item)
                for item in payload.get("violations", [])
                if isinstance(item, dict)
            ]
            blocking_codes = [
                str(item.get("code") or "QUALITY_GATE_BLOCK")
                for item in violations
                if str(item.get("severity") or "").lower() in {"block", "critical"}
                or str(item.get("code") or "") in payload.get("blocking_codes", [])
            ]
            if not blocking_codes:
                blocking_codes = [
                    str(item)
                    for item in payload.get("blocking_codes", [])
                    if str(item)
                ]
            if not blocking_codes:
                blocking_codes = ["QUALITY_GATE_BLOCK"]
            for code in blocking_codes:
                cause_counts[code] = cause_counts.get(code, 0) + 1
            specs.append(
                QualityRepairTaskSpec(
                    slug=slug,
                    chapter_number=int(chapter_number),
                    priority="critical",
                    task_priority=1,
                    cause_ids=tuple(dict.fromkeys(blocking_codes)),
                    patch_points=tuple(
                        {
                            "cause_id": str(item.get("code") or "QUALITY_GATE_BLOCK"),
                            "location": str(item.get("location") or ""),
                            "issue_summary": str(
                                item.get("detail") or item.get("message") or ""
                            ),
                            "repair_action_summary": (
                                "消除最近章节质量门硬阻断，并保持正典状态不回退。"
                            ),
                            "snippet": "",
                        }
                        for item in violations
                    ),
                    audit_row={
                        "chapter_number": str(chapter_number),
                        "priority": "critical",
                        "cause_ids": ";".join(dict.fromkeys(blocking_codes)),
                    },
                )
            )
        if not specs:
            return {"db_project_found": True, "task_ids": []}, {}
        sync = await create_quality_retrofit_rewrite_tasks(
            session,
            project,
            specs,
            replace_existing=replace_existing,
        )
        plan = {
            "task_count": len(specs),
            "priority_counts": {"critical": len(specs)},
            "cause_counts": cause_counts,
        }
        return {"db_project_found": True, **sync.to_dict()}, plan


async def _quarantine_invalid_continuation_outputs(
    session: Any,
    settings: AppSettings,
    project: Any,
    *,
    slug: str,
    chapter_numbers: tuple[int, ...],
    reason: str,
) -> dict[str, Any]:
    from sqlalchemy import select

    from bestseller.infra.db.models import ChapterModel

    quarantined: list[str] = []
    missing_files: list[str] = []
    marked_blocked: list[int] = []
    rejected_dir = _book_dir(settings, slug) / "audits" / "rejected-chapters"
    rejected_dir.mkdir(parents=True, exist_ok=True)
    for chapter_number in chapter_numbers:
        chapter = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == int(chapter_number),
            )
        )
        if chapter is not None and (
            str(getattr(chapter, "production_state", "") or "") != "ok"
            or str(getattr(chapter, "status", "") or "") != "complete"
        ):
            chapter.production_state = "blocked"
            marked_blocked.append(int(chapter_number))
        chapter_path = _book_dir(settings, slug) / f"chapter-{int(chapter_number):03d}.md"
        if chapter_path.exists():
            target = rejected_dir / f"chapter-{int(chapter_number):03d}.{reason}.md"
            if target.exists():
                target.unlink()
            chapter_path.replace(target)
            quarantined.append(str(target))
        else:
            missing_files.append(str(chapter_path))
    await session.flush()
    return {
        "reason": reason,
        "marked_blocked_chapters": marked_blocked,
        "quarantined_paths": quarantined,
        "missing_output_paths": missing_files,
    }


async def _execute_continuation_round(
    settings: AppSettings,
    slug: str,
    *,
    round_size: int,
    model_preflight: LLMPreflightReport,
    continuation_timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any], ChapterGenerationAudit]:
    if not model_preflight.ready:
        return (
            {},
            {
                "skipped": True,
                "reason": model_preflight.reason or "llm_preflight_failed",
            },
            ChapterGenerationAudit(checked=0, valid=0, invalid=0),
        )

    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return (
                {},
                {"skipped": True, "reason": "project_not_found_in_db"},
                ChapterGenerationAudit(checked=0, valid=0, invalid=0),
            )
        plan = await build_missing_chapter_continuation_plan(
            session,
            project,
            limit=round_size,
        )
        plan_payload = plan.to_dict()
        if not plan.next_chapter_numbers:
            reason = (
                "outline_extension_required"
                if plan.unplanned_chapters > 0
                else "no_missing_planned_chapters"
            )
            return (
                plan_payload,
                {"skipped": True, "reason": reason},
                ChapterGenerationAudit(checked=0, valid=0, invalid=0),
            )

        scene_plan_repair = await repair_legacy_scene_cards_for_continuation(
            session,
            project,
            plan.next_chapter_numbers,
        )
        if int(scene_plan_repair.get("unresolved") or 0) > 0:
            return (
                plan_payload,
                {
                    "skipped": True,
                    "reason": "scene_plan_richness_unresolved",
                    "scene_plan_repair": scene_plan_repair,
                },
                ChapterGenerationAudit(checked=0, valid=0, invalid=0),
            )

        from bestseller.services.pipelines import run_project_pipeline

        pipeline_coro = run_project_pipeline(
            session,
            settings,
            slug,
            requested_by="book_quality_closure",
            materialize_story_bible=False,
            materialize_outline=False,
            materialize_narrative_graph=False,
            materialize_narrative_tree=False,
            export_markdown=False,
            chapter_numbers=set(plan.next_chapter_numbers),
        )
        try:
            if continuation_timeout_seconds and continuation_timeout_seconds > 0:
                result = await asyncio.wait_for(
                    pipeline_coro,
                    timeout=float(continuation_timeout_seconds),
                )
            else:
                result = await pipeline_coro
        except TimeoutError:
            await session.rollback()
            cleanup = await _quarantine_invalid_continuation_outputs(
                session,
                settings,
                project,
                slug=slug,
                chapter_numbers=plan.next_chapter_numbers,
                reason="continuation_timeout",
            )
            audit = await audit_chapter_generation_modes(
                session,
                project,
                plan.next_chapter_numbers,
            )
            return (
                plan_payload,
                {
                    "skipped": True,
                    "reason": "continuation_timeout",
                    "timeout_seconds": float(continuation_timeout_seconds or 0),
                    "chapter_numbers": list(plan.next_chapter_numbers),
                    "invalid_output_cleanup": cleanup,
                },
                audit,
            )
        from bestseller.services.exports import export_chapter_markdown

        exported_paths: list[str] = []
        export_errors: list[str] = []
        for chapter_number in plan.next_chapter_numbers:
            try:
                _artifact, artifact_path = await export_chapter_markdown(
                    session,
                    settings,
                    slug,
                    chapter_number,
                    created_by_run_id=result.workflow_run_id,
                )
                exported_paths.append(str(artifact_path.resolve()))
            except (OSError, ValueError) as exc:
                export_errors.append(f"chapter {chapter_number}: {exc}")
        audit = await audit_chapter_generation_modes(
            session,
            project,
            plan.next_chapter_numbers,
        )
        cleanup: dict[str, Any] = {}
        if audit.gate_rejected or audit.invalid:
            cleanup = await _quarantine_invalid_continuation_outputs(
                session,
                settings,
                project,
                slug=slug,
                chapter_numbers=plan.next_chapter_numbers,
                reason="continuation_gate_rejected",
            )
            audit = await audit_chapter_generation_modes(
                session,
                project,
                plan.next_chapter_numbers,
            )
        return (
            plan_payload,
            {
                "skipped": False,
                "chapter_numbers": list(plan.next_chapter_numbers),
                "generated_count": len(result.chapter_results),
                "workflow_run_id": str(result.workflow_run_id),
                "requires_human_review": result.requires_human_review,
                "final_verdict": result.final_verdict,
                "scene_plan_repair": scene_plan_repair,
                "exported_paths": exported_paths,
                "export_errors": export_errors,
                "invalid_output_cleanup": cleanup,
            },
            audit,
        )


async def _build_continuation_plan_payload(
    settings: AppSettings,
    slug: str,
    *,
    round_size: int,
) -> dict[str, Any]:
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return {}
        plan = await build_missing_chapter_continuation_plan(
            session,
            project,
            limit=round_size,
        )
        return plan.to_dict()


async def _run_one_book(
    slug: str,
    *,
    settings: AppSettings,
    priorities: set[str],
    platform: str | None,
    round_size: int,
    continuation_size: int,
    model_preflight: LLMPreflightReport,
    execute_requested: bool,
    replace_existing: bool,
    dry_run: bool,
    include_verify: bool,
    repair_task_timeout_seconds: float | None,
    continuation_timeout_seconds: float | None,
) -> BookClosureReport:
    if is_out_of_scope_slug(slug, include_verify=include_verify):
        row = out_of_scope_fleet_row(slug)
        return BookClosureReport(
            slug=slug,
            status=row.status,
            next_action=row.next_action,
            model_preflight=model_preflight,
            fleet_row=row,
        )

    report_paths: dict[str, str] = {}
    bootstrap, blocked_row, blocked_error = await _run_bootstrap(
        settings,
        slug,
        dry_run=dry_run,
    )
    if blocked_row is not None:
        return BookClosureReport(
            slug=slug,
            status=blocked_row.status,
            next_action=blocked_row.next_action,
            model_preflight=model_preflight,
            bootstrap_report=bootstrap,
            fleet_row=blocked_row,
            errors=(blocked_error or "blocked",),
        )
    if bootstrap and isinstance(bootstrap.get("report_path"), str):
        report_paths["legacy_state_bootstrap"] = str(bootstrap["report_path"])

    existing_plan = _load_existing_repair_plan(settings, slug)
    before_acceptance = await _acceptance_payload(
        settings,
        slug,
        repair_plan=existing_plan,
        model_preflight=model_preflight,
    )
    acceptance_path = (
        _book_dir(settings, slug)
        / "audits"
        / "legacy-acceptance"
        / "report.before.json"
    )
    _json_dump(acceptance_path, before_acceptance)
    report_paths["legacy_acceptance_before"] = str(acceptance_path)

    repair_result = await repair_runner._run_for_slug(
        slug,
        platform=platform or DEFAULT_REPAIR_AUDIT_PLATFORM,
        priorities=priorities,
        audit=True,
        limit=None,
        create_tasks=execute_requested and model_preflight.ready and not dry_run,
        replace_existing=replace_existing,
        execute=False,
    )
    repair_plan = _repair_plan_summary(repair_result)
    task_sync = dict(repair_result.get("task_sync") or {})
    if (
        execute_requested
        and model_preflight.ready
        and not dry_run
        and _repair_plan_summary(repair_result).get("task_count") == 0
        and int(
            (before_acceptance.get("acceptance") or {})
            .get("metrics", {})
            .get("chapters_blocked", 0)
            if isinstance(before_acceptance.get("acceptance"), dict)
            else 0
        )
        > 0
    ):
        gate_task_sync, gate_repair_plan = await _sync_blocking_quality_gate_tasks(
            settings,
            slug,
            replace_existing=replace_existing,
        )
        if gate_task_sync.get("task_ids"):
            task_sync = gate_task_sync
            repair_plan = gate_repair_plan
    if (
        execute_requested
        and model_preflight.ready
        and not dry_run
        and _repair_plan_summary(repair_plan).get("task_count") == 0
        and isinstance(before_acceptance.get("acceptance"), dict)
        and before_acceptance["acceptance"].get("passed") is not True
    ):
        gap_task_sync, gap_repair_plan = await _sync_acceptance_gap_repair_tasks(
            settings,
            slug,
            before_acceptance,
            replace_existing=replace_existing,
        )
        if gap_task_sync.get("task_ids"):
            task_sync = gap_task_sync
            repair_plan = gap_repair_plan
    if isinstance(repair_result.get("repair_plan_path"), str):
        report_paths["autonomous_repair_plan"] = str(repair_result["repair_plan_path"])

    lifecycle_evidence, lifecycle_evidence_path = await _build_lifecycle_evidence(
        settings,
        slug,
    )
    if lifecycle_evidence_path:
        report_paths["lifecycle_evidence"] = lifecycle_evidence_path
    pre_execution_status, pre_execution_next_action = determine_next_action(
        acceptance=before_acceptance,
        repair_plan=repair_plan,
        model_preflight=model_preflight,
        execute_requested=execute_requested,
    )
    pre_execution_lifecycle = build_lifecycle_quality_report_from_closure(
        BookClosureReport(
            slug=slug,
            status=pre_execution_status,
            next_action=pre_execution_next_action,
            model_preflight=model_preflight,
            bootstrap_report=bootstrap,
            before_acceptance=before_acceptance,
            repair_plan=repair_plan,
            task_sync=task_sync,
            after_acceptance=before_acceptance,
            lifecycle_evidence=lifecycle_evidence,
            report_paths=report_paths,
        ).to_dict()
    ).to_dict()
    pre_execution_status, pre_execution_next_action = _lifecycle_execution_override(
        status=pre_execution_status,
        next_action=pre_execution_next_action,
        lifecycle_payload=pre_execution_lifecycle,
    )
    lifecycle_blocks_generation = pre_execution_next_action in {
        "repair_lifecycle_planning_evidence",
        "repair_lifecycle_character_evidence",
    }
    if (
        lifecycle_blocks_generation
        and execute_requested
        and model_preflight.ready
        and not dry_run
    ):
        _evidence_repair, evidence_repair_path = await _repair_lifecycle_evidence(
            settings,
            slug,
            dry_run=dry_run,
        )
        if evidence_repair_path:
            report_paths["lifecycle_evidence_repair"] = evidence_repair_path
        lifecycle_evidence, lifecycle_evidence_path = await _build_lifecycle_evidence(
            settings,
            slug,
        )
        if lifecycle_evidence_path:
            report_paths["lifecycle_evidence"] = lifecycle_evidence_path
        pre_execution_lifecycle = build_lifecycle_quality_report_from_closure(
            BookClosureReport(
                slug=slug,
                status=pre_execution_status,
                next_action=pre_execution_next_action,
                model_preflight=model_preflight,
                bootstrap_report=bootstrap,
                before_acceptance=before_acceptance,
                repair_plan=repair_plan,
                task_sync=task_sync,
                after_acceptance=before_acceptance,
                lifecycle_evidence=lifecycle_evidence,
                report_paths=report_paths,
            ).to_dict()
        ).to_dict()
        pre_execution_status, pre_execution_next_action = _lifecycle_execution_override(
            status=pre_execution_status,
            next_action=pre_execution_next_action,
            lifecycle_payload=pre_execution_lifecycle,
        )
        lifecycle_blocks_generation = pre_execution_next_action in {
            "repair_lifecycle_planning_evidence",
            "repair_lifecycle_character_evidence",
        }

    execution: dict[str, object] = {"skipped": True, "reason": "execute_not_requested"}
    executed_task_ids: list[str] = []
    if lifecycle_blocks_generation:
        execution = {"skipped": True, "reason": pre_execution_next_action}
    elif execute_requested and not dry_run:
        execution, executed_task_ids = await _execute_repair_round(
            settings,
            slug,
            round_size=round_size,
            model_preflight=model_preflight,
            task_ids=[str(item) for item in task_sync.get("task_ids", [])],
            task_timeout_seconds=repair_task_timeout_seconds,
        )
    elif dry_run:
        execution = {"skipped": True, "reason": "dry_run"}

    generation_audit = await _audit_generation_modes(settings, executed_task_ids)
    executed_count = int(execution.get("executed") or 0)
    if executed_count > 0:
        refreshed = await repair_runner._run_for_slug(
            slug,
            platform=platform or DEFAULT_REPAIR_AUDIT_PLATFORM,
            priorities=priorities,
            audit=True,
            limit=None,
            create_tasks=False,
            replace_existing=False,
            execute=False,
        )
        repair_plan = _repair_plan_summary(refreshed)

    after_acceptance = await _acceptance_payload(
        settings,
        slug,
        repair_plan=repair_plan,
        model_preflight=model_preflight,
    )

    status, next_action = determine_next_action(
        acceptance=after_acceptance,
        repair_plan=repair_plan,
        model_preflight=model_preflight,
        execute_requested=execute_requested,
        invalid_generation_count=generation_audit.invalid,
    )
    lifecycle_evidence, lifecycle_evidence_path = await _build_lifecycle_evidence(
        settings,
        slug,
    )
    if lifecycle_evidence_path:
        report_paths["lifecycle_evidence"] = lifecycle_evidence_path
    pre_continuation_lifecycle = build_lifecycle_quality_report_from_closure(
        BookClosureReport(
            slug=slug,
            status=status,
            next_action=next_action,
            model_preflight=model_preflight,
            bootstrap_report=bootstrap,
            before_acceptance=before_acceptance,
            repair_plan=repair_plan,
            task_sync=task_sync,
            execution=execution,
            rewrite_generation_audit=generation_audit,
            after_acceptance=after_acceptance,
            lifecycle_evidence=lifecycle_evidence,
            report_paths=report_paths,
        ).to_dict()
    ).to_dict()
    status, next_action = _lifecycle_execution_override(
        status=status,
        next_action=next_action,
        lifecycle_payload=pre_continuation_lifecycle,
    )

    continuation_plan: dict[str, Any] = {}
    continuation_execution: dict[str, Any] = {"skipped": True, "reason": "not_continuing"}
    chapter_generation_audit = ChapterGenerationAudit(checked=0, valid=0, invalid=0)
    if (
        status == "continuing"
        and next_action == "generate_missing_chapters_under_state_gates"
    ):
        if execute_requested and not dry_run:
            (
                continuation_plan,
                continuation_execution,
                chapter_generation_audit,
            ) = await _execute_continuation_round(
                settings,
                slug,
                round_size=continuation_size,
                model_preflight=model_preflight,
                continuation_timeout_seconds=continuation_timeout_seconds,
            )
        else:
            continuation_plan = await _build_continuation_plan_payload(
                settings,
                slug,
                round_size=continuation_size,
            )
            continuation_execution = {
                "skipped": True,
                "reason": "dry_run" if dry_run else "execute_not_requested",
            }
        if int(continuation_execution.get("generated_count") or 0) > 0:
            bootstrap, _, _ = await _run_bootstrap(
                settings,
                slug,
                dry_run=False,
            )
            refreshed = await repair_runner._run_for_slug(
                slug,
                platform=platform or DEFAULT_REPAIR_AUDIT_PLATFORM,
                priorities=priorities,
                audit=True,
                limit=None,
                create_tasks=model_preflight.ready,
                replace_existing=False,
                execute=False,
            )
            repair_plan = _repair_plan_summary(refreshed)
            if isinstance(refreshed.get("repair_plan_path"), str):
                report_paths["autonomous_repair_plan"] = str(
                    refreshed["repair_plan_path"]
                )
            after_acceptance = await _acceptance_payload(
                settings,
                slug,
                repair_plan=repair_plan,
                model_preflight=model_preflight,
            )
            status, next_action = determine_next_action(
                acceptance=after_acceptance,
                repair_plan=repair_plan,
                model_preflight=model_preflight,
                execute_requested=execute_requested,
                invalid_generation_count=(
                    generation_audit.invalid + chapter_generation_audit.invalid
                ),
            )
            lifecycle_evidence, lifecycle_evidence_path = await _build_lifecycle_evidence(
                settings,
                slug,
            )
            if lifecycle_evidence_path:
                report_paths["lifecycle_evidence"] = lifecycle_evidence_path
            post_continuation_lifecycle = build_lifecycle_quality_report_from_closure(
                BookClosureReport(
                    slug=slug,
                    status=status,
                    next_action=next_action,
                    model_preflight=model_preflight,
                    bootstrap_report=bootstrap,
                    before_acceptance=before_acceptance,
                    repair_plan=repair_plan,
                    task_sync=task_sync,
                    execution=execution,
                    rewrite_generation_audit=generation_audit,
                    continuation_plan=continuation_plan,
                    continuation_execution=continuation_execution,
                    chapter_generation_audit=chapter_generation_audit,
                    after_acceptance=after_acceptance,
                    lifecycle_evidence=lifecycle_evidence,
                    report_paths=report_paths,
                ).to_dict()
            ).to_dict()
            status, next_action = _lifecycle_execution_override(
                status=status,
                next_action=next_action,
                lifecycle_payload=post_continuation_lifecycle,
            )

    final_acceptance_path = (
        _book_dir(settings, slug) / "audits" / "legacy-acceptance" / "report.json"
    )
    _json_dump(final_acceptance_path, after_acceptance)
    report_paths["legacy_acceptance"] = str(final_acceptance_path)

    row = fleet_row_from_acceptance(
        slug=slug,
        acceptance_payload=after_acceptance,
        status=status,
        next_action=next_action,
    )
    closure_path = (
        _book_dir(settings, slug)
        / "audits"
        / "book-quality-closure"
        / "report.json"
    )
    report_paths = {**report_paths, "book_quality_closure": str(closure_path)}
    closure = BookClosureReport(
        slug=slug,
        status=status,
        next_action=next_action,
        model_preflight=model_preflight,
        bootstrap_report=bootstrap,
        before_acceptance=before_acceptance,
        repair_plan=repair_plan,
        task_sync=task_sync,
        execution=execution,
        rewrite_generation_audit=generation_audit,
        continuation_plan=continuation_plan,
        continuation_execution=continuation_execution,
        chapter_generation_audit=chapter_generation_audit,
        after_acceptance=after_acceptance,
        fleet_row=row,
        lifecycle_evidence=lifecycle_evidence,
        report_paths=report_paths,
    )
    lifecycle_payload, lifecycle_path = _write_lifecycle_quality_report(
        settings,
        slug,
        closure.to_dict(),
    )
    closure = replace(
        closure,
        lifecycle_quality=lifecycle_payload,
        report_paths={**report_paths, "lifecycle_quality": lifecycle_path},
    )
    _json_dump(closure_path, closure.to_dict())
    return closure


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--slug", help="One DB-backed output/<slug> book to close.")
    target.add_argument("--all", action="store_true", help="Process all output book dirs.")
    parser.add_argument(
        "--platform",
        default=DEFAULT_REPAIR_AUDIT_PLATFORM,
        help=(
            "Platform id for quality audit. Defaults to framework, "
            "which uses config generation.words_per_chapter."
        ),
    )
    parser.add_argument(
        "--priority",
        default="critical,high",
        help="Comma list: critical,high,medium,ok.",
    )
    parser.add_argument("--round-size", type=int, default=10, help="Rewrite tasks per round.")
    parser.add_argument(
        "--continuation-size",
        type=int,
        default=0,
        help=(
            "Missing planned chapters to generate when the book is in continuing "
            "state. Defaults to --round-size."
        ),
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=1,
        help=(
            "Maximum closure rounds per book. One round preserves the original "
            "manual-step behavior; larger values keep repairing/continuing until "
            "ready, blocked, stalled, or capped."
        ),
    )
    parser.add_argument(
        "--preflight-timeout",
        type=float,
        default=45.0,
        help="Maximum seconds to wait for the LLM preflight request.",
    )
    parser.add_argument(
        "--repair-task-timeout",
        type=float,
        default=420.0,
        help=(
            "Maximum seconds to wait for one rewrite task before marking it "
            "failed and continuing the closure run. Use 0 to disable."
        ),
    )
    parser.add_argument(
        "--continuation-timeout",
        type=float,
        default=600.0,
        help=(
            "Maximum seconds to wait for one missing-chapter generation batch "
            "before rolling back that batch and continuing the closure report. "
            "Use 0 to disable."
        ),
    )
    parser.add_argument("--max-books", type=int, default=0, help="Limit --all processing count.")
    parser.add_argument(
        "--include-verify",
        action="store_true",
        help="Include verify/test output directories in --all.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Supersede existing pending autonomous tasks when syncing the plan.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Preflight the LLM, sync tasks, and execute one bounded rewrite round.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Build reports without DB task creation, DB rewrite execution, "
            "or DB bootstrap mutation."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> dict[str, object]:
    settings = load_settings()
    priorities = repair_runner._parse_priorities(args.priority)
    round_size = max(int(args.round_size or 10), 1)
    continuation_size = max(int(args.continuation_size or round_size), 1)
    max_rounds = max(int(args.max_rounds or 1), 1)
    repair_task_timeout = (
        None
        if float(args.repair_task_timeout or 0.0) <= 0.0
        else max(float(args.repair_task_timeout), 1.0)
    )
    continuation_timeout = (
        None
        if float(args.continuation_timeout or 0.0) <= 0.0
        else max(float(args.continuation_timeout), 1.0)
    )
    slugs = (
        discover_output_book_slugs(Path(settings.output.base_dir))
        if args.all
        else [str(args.slug)]
    )
    slugs = _sort_slugs(slugs)
    if not args.include_verify:
        slugs = filter_fleet_slugs(slugs)
    if args.max_books and args.max_books > 0:
        slugs = slugs[: args.max_books]

    model_preflight = await _run_preflight(
        settings,
        execute_requested=bool(args.execute),
        dry_run=bool(args.dry_run),
        timeout_seconds=max(float(args.preflight_timeout or 45.0), 1.0),
    )
    reports: list[BookClosureReport] = []
    round_reports: list[dict[str, object]] = []
    loop_stop_reasons: dict[str, str] = {}
    rows: list[FleetBookRow] = []
    for slug in slugs:
        book_round_reports: list[dict[str, object]] = []
        previous_signature: tuple[object, ...] | None = None
        no_progress_count = 0
        final_report: BookClosureReport | None = None
        loop_stop_reason = "max_rounds_reached"
        try:
            for round_index in range(1, max_rounds + 1):
                report = await _run_one_book(
                    slug,
                    settings=settings,
                    priorities=priorities,
                    platform=args.platform,
                    round_size=round_size,
                    continuation_size=continuation_size,
                    model_preflight=model_preflight,
                    execute_requested=bool(args.execute),
                    replace_existing=bool(args.replace_existing),
                    dry_run=bool(args.dry_run),
                    include_verify=bool(args.include_verify),
                    repair_task_timeout_seconds=repair_task_timeout,
                    continuation_timeout_seconds=continuation_timeout,
                )
                final_report = report
                signature = _progress_signature(report)
                round_reports.append(
                    {
                        "slug": slug,
                        "round": round_index,
                        "status": report.status,
                        "next_action": report.next_action,
                        "progress_signature": list(signature),
                    }
                )
                book_round_reports.append(round_reports[-1])
                if not _can_continue_closure_loop(
                    report,
                    execute_requested=bool(args.execute),
                    dry_run=bool(args.dry_run),
                    model_preflight=model_preflight,
                ):
                    loop_stop_reason = (
                        "book_ready"
                        if report.status == "ready"
                        else "not_executable_without_intervention"
                    )
                    break

                execution_reason = str((report.execution or {}).get("reason") or "").strip()
                if previous_signature == signature:
                    no_progress_count += 1
                else:
                    no_progress_count = 0
                previous_signature = signature
                if report.status == "repairing" and execution_reason in _NO_EXECUTION_PROGRESS_REASONS:
                    loop_stop_reason = "no_executable_repair_tasks"
                    break
                if no_progress_count >= MAX_NO_METRIC_PROGRESS_ROUNDS:
                    loop_stop_reason = "no_metric_progress"
                    break
            report = final_report
            if report is None:
                raise RuntimeError("closure loop produced no report")
        except Exception as exc:
            row = blocked_fleet_row(
                slug,
                error=f"{type(exc).__name__}: {exc}",
                next_action="inspect_closure_runner_failure",
            )
            report = BookClosureReport(
                slug=slug,
                status=row.status,
                next_action=row.next_action,
                model_preflight=model_preflight,
                fleet_row=row,
                errors=(row.error or "unknown_error",),
            )
            loop_stop_reason = "runner_exception"
        loop_metadata = {
            "max_rounds": max_rounds,
            "stop_reason": loop_stop_reason,
            "rounds": book_round_reports,
        }
        loop_stop_reasons[slug] = loop_stop_reason
        if report.report_paths.get("book_quality_closure"):
            _json_dump(
                Path(str(report.report_paths["book_quality_closure"])),
                {**report.to_dict(), "loop": loop_metadata},
            )
        reports.append(report)
        if report.fleet_row is not None:
            rows.append(report.fleet_row)

    fleet_payload = {
        "model_execution_ready": model_preflight.ready,
        "model_preflight": model_preflight.to_dict(),
        "book_count": len(rows),
        "max_rounds": max_rounds,
        "rows": [row.to_dict() for row in rows],
        "lifecycle_rows": [
            {
                "slug": report.slug,
                "passed": bool((report.lifecycle_quality or {}).get("passed")),
                "readiness_level": (report.lifecycle_quality or {}).get(
                    "readiness_level"
                ),
                "finding_count": len(
                    list((report.lifecycle_quality or {}).get("findings") or [])
                ),
                "report_path": report.report_paths.get("lifecycle_quality"),
            }
            for report in reports
        ],
        "rounds": round_reports,
    }
    fleet_path = _REPO_ROOT / "data" / "legacy_book_quality_closure" / "fleet_report.json"
    _json_dump(fleet_path, fleet_payload)
    return {
        "fleet_report_path": str(fleet_path),
        "fleet": fleet_payload,
        "reports": [
            {
                **report.to_dict(),
                "loop": {
                    "max_rounds": max_rounds,
                    "stop_reason": loop_stop_reasons.get(report.slug),
                    "rounds": [
                        item for item in round_reports if item.get("slug") == report.slug
                    ],
                },
            }
            for report in reports
        ],
    }


def main() -> int:
    args = _parse_args()
    result = asyncio.run(_run(args))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"fleet_report: {result['fleet_report_path']}")
        fleet = result["fleet"]
        assert isinstance(fleet, dict)
        for row in fleet.get("rows", []):
            if not isinstance(row, dict):
                continue
            print(
                (
                    "{slug}: status={status} acceptance={acceptance} "
                    "score={score} blocked={blocked} repair_tasks={tasks} "
                    "missing={missing} next={next_action}"
                ).format(
                    slug=row.get("slug"),
                    status=row.get("status"),
                    acceptance=row.get("acceptance_status"),
                    score=row.get("quality_score"),
                    blocked=row.get("blocked_chapters"),
                    tasks=row.get("repair_tasks"),
                    missing=row.get("missing_chapters"),
                    next_action=row.get("next_action"),
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
