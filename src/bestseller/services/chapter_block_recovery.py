from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterAuditFindingModel,
    ChapterModel,
    ChapterQualityReportModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
)
from bestseller.services.chapter_outline_readiness_gate import (
    evaluate_chapter_outline_readiness,
)
from bestseller.services.gate_registry import registered_block_metadata_keys

_OUTLINE_KEYS = (
    "blocked_by_chapter_outline_readiness_gate",
    "chapter_outline_readiness_block_codes",
    "chapter_outline_readiness_hint",
    "chapter_outline_readiness_report",
)
_QUALITY_BLOCK_KEYS = (
    "auto_repair_exhausted",
    "auto_repair_in_progress",
    "auto_repair_last_block_codes",
    "auto_repair_total_attempts",
    "auto_repair_cross_run_exhausted",
    "quality_bundle_blocking_codes",
    "quality_gate_block_codes",
    "quality_gate_block_code",
    "quality_gate_block_hint",
    "quality_gate_block_source",
    "production_block_code",
    "requires_human_review",
)
_RETENTION_KEYS = (
    "retention_auto_repair_exhausted",
    "retention_retry_count",
    "retention_retry_last_block_codes",
    "retention_retry_strict_prompt",
)
_SCENE_AUTO_REPAIR_RESIDUE_KEYS = (
    "auto_repair_adjusted_target_word_count",
    "auto_repair_block_codes",
    "auto_repair_length_scale",
    "auto_repair_hint",
    "auto_repair_attempt",
    "auto_repair_min_scene_target_floor",
    "auto_repair_scene_target_cap",
    "auto_repair_source_block_code",
    "auto_repair_original_target_word_count",
    "auto_repair_target_word_count_clamped",
)
_OK_REPAIR_RESIDUE_KEYS = (
    *_QUALITY_BLOCK_KEYS,
    *_RETENTION_KEYS,
    "autonomous_quality_retrofit_attempts_active",
    "autonomous_quality_retrofit_exhausted",
)
_STALE_BLOCK_RESIDUE_KEYS = tuple(
    dict.fromkeys(
        (
            *_QUALITY_BLOCK_KEYS,
            *_RETENTION_KEYS,
            *_OUTLINE_KEYS,
            "autonomous_quality_retrofit_attempts_active",
            "autonomous_quality_retrofit_exhausted",
        )
    )
)


@dataclass(frozen=True)
class BlockRecoveryReport:
    chapter_number: int
    block_kind: str
    recoverable: bool
    actions_taken: tuple[str, ...]
    new_state: str
    reason: str
    issue_codes_now: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "chapter_number": self.chapter_number,
            "block_kind": self.block_kind,
            "recoverable": self.recoverable,
            "actions_taken": list(self.actions_taken),
            "new_state": self.new_state,
            "reason": self.reason,
            "issue_codes_now": list(self.issue_codes_now),
        }


def _latest_quality_report_is_clean(report: ChapterQualityReportModel | None) -> bool:
    if report is None or bool(getattr(report, "blocks_write", False)):
        return False
    payload = report.report_json if isinstance(report.report_json, dict) else {}
    blocking_codes = payload.get("blocking_codes")
    if isinstance(blocking_codes, (list, tuple, set)) and any(
        str(code).strip() for code in blocking_codes
    ):
        return False
    return True


def _metadata(chapter: ChapterModel) -> dict[str, Any]:
    return dict(getattr(chapter, "metadata_json", None) or {})


def _pop_keys(metadata: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, ...]:
    removed: list[str] = []
    for key in keys:
        if key in metadata:
            metadata.pop(key, None)
            removed.append(key)
    return tuple(removed)


def _remaining_registered_block_keys(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(key for key in registered_block_metadata_keys() if bool(metadata.get(key)))


def _ok_repair_residue_keys(chapter: ChapterModel) -> tuple[str, ...]:
    if str(getattr(chapter, "production_state", "") or "").lower() != "ok":
        return ()
    metadata = _metadata(chapter)
    return tuple(key for key in _OK_REPAIR_RESIDUE_KEYS if key in metadata)


def _stale_block_residue_keys(chapter: ChapterModel) -> tuple[str, ...]:
    if str(getattr(chapter, "production_state", "") or "").lower() != "blocked":
        return ()
    metadata = _metadata(chapter)
    return tuple(
        dict.fromkeys(
            (
                *(key for key in _STALE_BLOCK_RESIDUE_KEYS if key in metadata),
                *_remaining_registered_block_keys(metadata),
            )
        )
    )


def _release_if_unblocked(chapter: ChapterModel, metadata: dict[str, Any]) -> str:
    if not _remaining_registered_block_keys(metadata):
        chapter.production_state = "ok"
    return str(getattr(chapter, "production_state", "") or "unknown")


async def _latest_quality_report(
    session: AsyncSession,
    chapter: ChapterModel,
) -> ChapterQualityReportModel | None:
    return await session.scalar(
        select(ChapterQualityReportModel)
        .where(ChapterQualityReportModel.chapter_id == chapter.id)
        .order_by(ChapterQualityReportModel.created_at.desc())
    )


async def _clear_scene_auto_repair_residue(
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
    cleared = 0
    for scene in scenes:
        metadata = dict(getattr(scene, "metadata_json", None) or {})
        removed = _pop_keys(metadata, _SCENE_AUTO_REPAIR_RESIDUE_KEYS)
        if removed:
            scene.metadata_json = metadata
            cleared += 1
    return cleared


async def _pending_rewrite_tasks(
    session: AsyncSession,
    chapter: ChapterModel,
) -> tuple[RewriteTaskModel, ...]:
    return tuple(
        (
            await session.scalars(
                select(RewriteTaskModel).where(
                    RewriteTaskModel.project_id == chapter.project_id,
                    RewriteTaskModel.status.in_(["pending", "queued"]),
                    or_(
                        RewriteTaskModel.trigger_source_id == chapter.id,
                        RewriteTaskModel.metadata_json["chapter_id"].astext == str(chapter.id),
                        RewriteTaskModel.metadata_json["chapter_number"].astext
                        == str(chapter.chapter_number),
                    ),
                )
            )
        )
        .unique()
        .all()
    )


async def _pending_rewrite_task_count(
    session: AsyncSession,
    chapter: ChapterModel,
) -> int:
    return len(await _pending_rewrite_tasks(session, chapter))


def _supersede_pending_rewrite_tasks(
    tasks: tuple[RewriteTaskModel, ...],
    *,
    chapter: ChapterModel,
) -> int:
    count = 0
    for task in tasks:
        metadata = dict(getattr(task, "metadata_json", None) or {})
        metadata["superseded_reason"] = "current_chapter_quality_clean"
        metadata["superseded_by_chapter_number"] = int(chapter.chapter_number)
        task.metadata_json = metadata
        task.status = "superseded"
        count += 1
    return count


async def _latest_critical_audit_chapters(
    session: AsyncSession,
    project: ProjectModel,
) -> frozenset[int]:
    latest_at = await session.scalar(
        select(func.max(ChapterAuditFindingModel.created_at)).where(
            ChapterAuditFindingModel.project_id == project.id
        )
    )
    if latest_at is None:
        return frozenset()
    rows = await session.execute(
        select(ChapterAuditFindingModel.chapter_no)
        .where(
            ChapterAuditFindingModel.project_id == project.id,
            ChapterAuditFindingModel.created_at == latest_at,
            ChapterAuditFindingModel.chapter_no.is_not(None),
            ChapterAuditFindingModel.severity == "critical",
        )
        .distinct()
    )
    return frozenset(int(row[0]) for row in rows if row[0] is not None)


def _audit_writer(
    package_dir: Path | None,
) -> Callable[[BlockRecoveryReport], None] | None:
    if package_dir is None:
        return None
    log_dir = package_dir / "audits" / "block-recovery-log"
    log_path = log_dir / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.jsonl"

    def write(report: BlockRecoveryReport) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report.to_dict(), ensure_ascii=False) + "\n")

    return write


async def attempt_recover_retention_exhausted(
    session: AsyncSession,
    chapter: ChapterModel,
    *,
    dry_run: bool = False,
) -> BlockRecoveryReport:
    metadata = _metadata(chapter)
    if not metadata.get("retention_auto_repair_exhausted"):
        return BlockRecoveryReport(
            chapter_number=int(chapter.chapter_number),
            block_kind="retention",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="not_retention_exhausted",
        )
    latest = await _latest_quality_report(session, chapter)
    if not _latest_quality_report_is_clean(latest):
        return BlockRecoveryReport(
            chapter_number=int(chapter.chapter_number),
            block_kind="retention",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="latest_quality_still_blocking",
            issue_codes_now=_blocking_codes(latest),
        )
    actions = [
        f"removed:{key}" for key in (*_RETENTION_KEYS, *_QUALITY_BLOCK_KEYS) if key in metadata
    ]
    if not dry_run:
        _pop_keys(metadata, _RETENTION_KEYS)
        _pop_keys(metadata, _QUALITY_BLOCK_KEYS)
        metadata["retention_recovered_by_closure_loop"] = True
        chapter.metadata_json = metadata
        new_state = _release_if_unblocked(chapter, metadata)
        await _clear_scene_auto_repair_residue(session, chapter)
        await session.flush()
    else:
        new_state = str(chapter.production_state)
    return BlockRecoveryReport(
        chapter_number=int(chapter.chapter_number),
        block_kind="retention",
        recoverable=True,
        actions_taken=tuple(actions),
        new_state=new_state,
        reason="latest_quality_clean",
    )


async def clear_ok_chapter_repair_residue(
    session: AsyncSession,
    chapter: ChapterModel,
    *,
    dry_run: bool = False,
) -> BlockRecoveryReport:
    residue_keys = _ok_repair_residue_keys(chapter)
    if not residue_keys:
        return BlockRecoveryReport(
            chapter_number=int(chapter.chapter_number),
            block_kind="ok_repair_residue",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="no_ok_repair_residue",
        )
    actions = [f"removed:{key}" for key in residue_keys]
    if not dry_run:
        metadata = _metadata(chapter)
        previous_repair_codes = metadata.pop("auto_repair_last_block_codes", None)
        _pop_keys(metadata, residue_keys)
        if previous_repair_codes:
            metadata["auto_repair_last_resolved_block_codes"] = previous_repair_codes
        metadata["ok_repair_residue_cleared_by_closure_loop"] = True
        chapter.metadata_json = metadata
        await _clear_scene_auto_repair_residue(session, chapter)
        await session.flush()
    return BlockRecoveryReport(
        chapter_number=int(chapter.chapter_number),
        block_kind="ok_repair_residue",
        recoverable=True,
        actions_taken=tuple(actions),
        new_state=str(chapter.production_state),
        reason="production_state_ok",
    )


async def attempt_release_stale_production_block(
    session: AsyncSession,
    chapter: ChapterModel,
    *,
    latest_critical_audit_chapters: frozenset[int] = frozenset(),
    dry_run: bool = False,
    require_clean_report: bool = False,
) -> BlockRecoveryReport:
    if str(getattr(chapter, "production_state", "") or "").lower() != "blocked":
        return BlockRecoveryReport(
            chapter_number=int(chapter.chapter_number),
            block_kind="stale_production_block",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="not_production_blocked",
        )

    chapter_number = int(chapter.chapter_number)
    if chapter_number in latest_critical_audit_chapters:
        return BlockRecoveryReport(
            chapter_number=chapter_number,
            block_kind="stale_production_block",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="latest_audit_still_blocking",
        )

    latest = await _latest_quality_report(session, chapter)
    pending_rewrite_tasks = await _pending_rewrite_tasks(session, chapter)
    if pending_rewrite_tasks and not _latest_quality_report_is_clean(latest):
        return BlockRecoveryReport(
            chapter_number=chapter_number,
            block_kind="stale_production_block",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="pending_rewrite_task",
        )

    residue_keys = _stale_block_residue_keys(chapter)
    if latest is not None and not _latest_quality_report_is_clean(latest):
        return BlockRecoveryReport(
            chapter_number=chapter_number,
            block_kind="stale_production_block",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="latest_quality_still_blocking",
            issue_codes_now=_blocking_codes(latest),
        )
    if latest is None and residue_keys:
        return BlockRecoveryReport(
            chapter_number=chapter_number,
            block_kind="stale_production_block",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="block_metadata_without_clean_quality_report",
            issue_codes_now=residue_keys,
        )
    if latest is None and require_clean_report:
        # Conservative mode (used by the in-run repair sweep): a chapter that is
        # production_state=blocked but has NO quality report on record is NOT
        # treated as stale — it may be a genuinely-blocked chapter awaiting
        # repair. Only release when a CLEAN report proves it already re-passed.
        return BlockRecoveryReport(
            chapter_number=chapter_number,
            block_kind="stale_production_block",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="no_quality_report_on_record",
        )

    reason = "latest_quality_clean" if latest is not None else "no_active_block_signal"
    actions = ["set:production_state=ok"]
    actions.extend(f"removed:{key}" for key in residue_keys)
    if pending_rewrite_tasks:
        actions.append(f"superseded_pending_rewrite_tasks:{len(pending_rewrite_tasks)}")
    if not dry_run:
        metadata = _metadata(chapter)
        _pop_keys(metadata, residue_keys)
        metadata["stale_production_block_released_by_closure_loop"] = True
        if pending_rewrite_tasks:
            metadata["stale_rewrite_tasks_superseded_by_closure_loop"] = len(
                pending_rewrite_tasks
            )
        chapter.metadata_json = metadata
        chapter.production_state = "ok"
        _supersede_pending_rewrite_tasks(pending_rewrite_tasks, chapter=chapter)
        await _clear_scene_auto_repair_residue(session, chapter)
        await session.flush()
    return BlockRecoveryReport(
        chapter_number=chapter_number,
        block_kind="stale_production_block",
        recoverable=True,
        actions_taken=tuple(actions),
        new_state="ok" if not dry_run else str(chapter.production_state),
        reason=reason,
    )


async def attempt_recover_audit_budget_exhausted(
    session: AsyncSession,
    chapter: ChapterModel,
    *,
    dry_run: bool = False,
) -> BlockRecoveryReport:
    metadata = _metadata(chapter)
    if not (
        metadata.get("auto_repair_cross_run_exhausted") or metadata.get("auto_repair_exhausted")
    ):
        return BlockRecoveryReport(
            chapter_number=int(chapter.chapter_number),
            block_kind="audit_budget",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="not_audit_budget_exhausted",
        )
    latest = await _latest_quality_report(session, chapter)
    if not _latest_quality_report_is_clean(latest):
        return BlockRecoveryReport(
            chapter_number=int(chapter.chapter_number),
            block_kind="audit_budget",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="latest_quality_still_blocking",
            issue_codes_now=_blocking_codes(latest),
        )
    actions = [f"removed:{key}" for key in _QUALITY_BLOCK_KEYS if key in metadata]
    if not dry_run:
        _pop_keys(metadata, _QUALITY_BLOCK_KEYS)
        metadata["audit_budget_recovered_by_closure_loop"] = True
        chapter.metadata_json = metadata
        new_state = _release_if_unblocked(chapter, metadata)
        await _clear_scene_auto_repair_residue(session, chapter)
        await session.flush()
    else:
        new_state = str(chapter.production_state)
    return BlockRecoveryReport(
        chapter_number=int(chapter.chapter_number),
        block_kind="audit_budget",
        recoverable=True,
        actions_taken=tuple(actions),
        new_state=new_state,
        reason="latest_quality_clean",
    )


async def attempt_recover_outline_readiness(
    session: AsyncSession,
    chapter: ChapterModel,
    *,
    dry_run: bool = False,
) -> BlockRecoveryReport:
    metadata = _metadata(chapter)
    if not metadata.get("blocked_by_chapter_outline_readiness_gate"):
        return BlockRecoveryReport(
            chapter_number=int(chapter.chapter_number),
            block_kind="outline_readiness",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="not_outline_blocked",
        )
    pending_rewrite_task_count = int(
        await session.scalar(
            select(func.count(RewriteTaskModel.id)).where(
                RewriteTaskModel.project_id == chapter.project_id,
                RewriteTaskModel.status.in_(["pending", "queued"]),
                or_(
                    RewriteTaskModel.trigger_source_id == chapter.id,
                    RewriteTaskModel.metadata_json["chapter_id"].astext == str(chapter.id),
                    RewriteTaskModel.metadata_json["chapter_number"].astext
                    == str(chapter.chapter_number),
                ),
            )
        )
        or 0
    )
    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )
    # First-pass eval: figure out WHICH issues are currently blocking so we
    # can decide whether residue cleanup is safe to force.
    first_pass_report = evaluate_chapter_outline_readiness(
        chapter_number=int(chapter.chapter_number),
        chapter_title=chapter.title,
        chapter_target_word_count=chapter.target_word_count,
        chapter_metadata=metadata,
        scene_cards=scenes,
        pending_rewrite_task_count=pending_rewrite_task_count,
    )
    blocked_only_by_residue = bool(first_pass_report.blocking_issues) and all(
        issue.code == "OUTLINE_STALE_AUTO_REPAIR_RESIDUE"
        for issue in first_pass_report.blocking_issues
    )
    # Clearing scene auto-repair residue does not conflict with any pending
    # rewrite task — pending rewrites mutate scene prose, not these metadata
    # keys. If the gate is blocked ONLY by RESIDUE codes, force-clear even
    # when rewrite tasks are queued; otherwise the chapter is locked in a
    # circular dependency (residue keeps gate blocked → blocked chapter
    # never enters the rewrite pipeline → residue persists).
    cleared_scene_residue = 0
    should_clear_residue = not dry_run and (
        pending_rewrite_task_count <= 0 or blocked_only_by_residue
    )
    if should_clear_residue:
        cleared_scene_residue = await _clear_scene_auto_repair_residue(session, chapter)
        if cleared_scene_residue:
            scenes = list(
                await session.scalars(
                    select(SceneCardModel)
                    .where(SceneCardModel.chapter_id == chapter.id)
                    .order_by(SceneCardModel.scene_number.asc())
                )
            )
    report = evaluate_chapter_outline_readiness(
        chapter_number=int(chapter.chapter_number),
        chapter_title=chapter.title,
        chapter_target_word_count=chapter.target_word_count,
        chapter_metadata=metadata,
        scene_cards=scenes,
        pending_rewrite_task_count=pending_rewrite_task_count,
    )
    if report.blocked:
        return BlockRecoveryReport(
            chapter_number=int(chapter.chapter_number),
            block_kind="outline_readiness",
            recoverable=False,
            actions_taken=(),
            new_state=str(chapter.production_state),
            reason="still_blocked",
            issue_codes_now=tuple(issue.code for issue in report.blocking_issues),
        )
    actions = [f"removed:{key}" for key in _OUTLINE_KEYS if key in metadata]
    if cleared_scene_residue:
        actions.append(f"cleared_scene_auto_repair_residue:{cleared_scene_residue}")
    if not dry_run:
        _pop_keys(metadata, _OUTLINE_KEYS)
        metadata["outline_readiness_recovered_by_closure_loop"] = True
        chapter.metadata_json = metadata
        new_state = _release_if_unblocked(chapter, metadata)
        await session.flush()
    else:
        new_state = str(chapter.production_state)
    return BlockRecoveryReport(
        chapter_number=int(chapter.chapter_number),
        block_kind="outline_readiness",
        recoverable=True,
        actions_taken=tuple(actions),
        new_state=new_state,
        reason="gate_passes_now",
    )


async def sweep_recoverable_blocks(
    session: AsyncSession,
    project: ProjectModel,
    *,
    package_dir: Path | None = None,
    dry_run: bool = False,
    require_clean_report: bool = False,
) -> tuple[BlockRecoveryReport, ...]:
    writer = _audit_writer(package_dir)
    latest_critical_audit_chapters = await _latest_critical_audit_chapters(
        session,
        project,
    )
    chapters = list(
        await session.scalars(
            select(ChapterModel)
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )
    reports: list[BlockRecoveryReport] = []
    for chapter in chapters:
        residue_report = await clear_ok_chapter_repair_residue(
            session,
            chapter,
            dry_run=dry_run,
        )
        if residue_report.recoverable:
            reports.append(residue_report)
            if writer is not None:
                writer(residue_report)
        stale_block_report = await attempt_release_stale_production_block(
            session,
            chapter,
            latest_critical_audit_chapters=latest_critical_audit_chapters,
            dry_run=dry_run,
            require_clean_report=require_clean_report,
        )
        if stale_block_report.recoverable:
            reports.append(stale_block_report)
            if writer is not None:
                writer(stale_block_report)
            continue
        if writer is not None and stale_block_report.issue_codes_now:
            writer(stale_block_report)
        metadata = _metadata(chapter)
        if not _has_recovery_marker(metadata):
            continue
        for recover in (
            attempt_recover_retention_exhausted,
            attempt_recover_audit_budget_exhausted,
            attempt_recover_outline_readiness,
        ):
            report = await recover(session, chapter, dry_run=dry_run)
            if report.reason.startswith("not_"):
                continue
            reports.append(report)
            if writer is not None and (report.recoverable or report.issue_codes_now):
                writer(report)
    return tuple(reports)


def summarize_block_recovery(
    reports: tuple[BlockRecoveryReport, ...],
) -> dict[str, object]:
    recovered = [report for report in reports if report.recoverable]
    still_blocked = [report for report in reports if not report.recoverable]
    return {
        "checked": len(reports),
        "recovered": len(recovered),
        "still_blocked": len(still_blocked),
        "recovered_chapters": sorted({report.chapter_number for report in recovered}),
        "reports": [report.to_dict() for report in reports],
    }


def _has_recovery_marker(metadata: Mapping[str, Any]) -> bool:
    return bool(
        metadata.get("retention_auto_repair_exhausted")
        or metadata.get("auto_repair_cross_run_exhausted")
        or metadata.get("auto_repair_exhausted")
        or metadata.get("blocked_by_chapter_outline_readiness_gate")
    )


def _blocking_codes(report: ChapterQualityReportModel | None) -> tuple[str, ...]:
    if report is None or not isinstance(report.report_json, dict):
        return ()
    raw = report.report_json.get("blocking_codes")
    if not isinstance(raw, (list, tuple, set)):
        return ()
    return tuple(str(code) for code in raw if str(code).strip())
