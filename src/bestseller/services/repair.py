from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
import json
import logging
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.exc import DBAPIError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ProjectStatus, WorkflowStatus
from bestseller.domain.pipeline import ProjectRepairChapterSummary, ProjectRepairResult
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterQualityReportModel,
    ProjectModel,
    RewriteImpactModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneDraftVersionModel,
)
from bestseller.services.consistency import review_project_consistency
from bestseller.services.exports import export_project_markdown
from bestseller.services.pipelines import run_chapter_pipeline
from bestseller.services.projects import get_project_by_slug
from bestseller.services.quality_failure_events import (
    QualityFailureEvent,
    quality_failure_events_to_dicts,
)
from bestseller.services.rewrite_impacts import refresh_rewrite_impacts
from bestseller.services.source_artifact_audit import (
    SourceArtifactAuditReport,
    audit_source_artifacts,
)
from bestseller.services.word_targets import (
    normalize_chapter_word_target,
    scene_word_target_for_chapter,
    word_target_policy,
)
from bestseller.services.workflows import create_workflow_run, create_workflow_step_run
from bestseller.services.world_expansion import sync_world_expansion_progress
from bestseller.settings import AppSettings

WORKFLOW_TYPE_PROJECT_REPAIR = "project_repair"
ProgressCallback = Callable[[str, dict[str, Any] | None], None]
logger = logging.getLogger(__name__)


async def _chapter_has_incomplete_scene_drafts(
    session: AsyncSession,
    chapter: ChapterModel,
    *,
    language: str | None,
) -> bool:
    """Return True when current scene drafts suggest a chapter stopped mid-build."""

    from bestseller.services.drafts import count_words  # noqa: PLC0415
    from bestseller.services.output_hygiene import (  # noqa: PLC0415
        collect_unfinished_artifact_issues,
    )

    rows = await session.execute(
        select(SceneCardModel, SceneDraftVersionModel)
        .outerjoin(
            SceneDraftVersionModel,
            and_(
                SceneDraftVersionModel.scene_card_id == SceneCardModel.id,
                SceneDraftVersionModel.is_current.is_(True),
            ),
        )
        .where(SceneCardModel.chapter_id == chapter.id)
        .order_by(SceneCardModel.scene_number.asc())
    )
    payloads = list(rows.all())
    if not payloads:
        return False

    scene_numbers = [
        int(scene.scene_number)
        for scene, _draft in payloads
        if getattr(scene, "scene_number", None) is not None
    ]
    last_scene_number = max(scene_numbers) if scene_numbers else 0

    for scene, draft in payloads:
        scene_number = int(getattr(scene, "scene_number", 0) or 0)
        if draft is None:
            return True
        content = draft.content_md or ""
        if collect_unfinished_artifact_issues(content, language=language):
            return True

        try:
            target = int(getattr(scene, "target_word_count", 0) or 0)
        except (TypeError, ValueError):
            target = 0
        if target < 300:
            continue
        try:
            word_count = int(getattr(draft, "word_count", 0) or 0)
        except (TypeError, ValueError):
            word_count = 0
        if word_count <= 0:
            word_count = count_words(content)
        floor_ratio = 0.70 if scene_number == last_scene_number else 0.55
        floor_words = max(120, int(target * floor_ratio))
        if word_count < floor_words:
            return True
    return False
_ORPHAN_REWRITE_TASK_ERRORS = (
    "Rewrite task does not point to a source scene.",
    "Source scene for rewrite task was not found.",
    "Source chapter for rewrite task was not found.",
)


def _emit_progress(
    progress: ProgressCallback | None,
    stage: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if progress is None:
        return
    progress(stage, payload)


async def _checkpoint_repair_progress(session: AsyncSession) -> None:
    """Persist repair progress between long chapter rewrites.

    The web UI and worker self-heal inspect workflow rows from separate
    sessions.  If project repair holds one transaction open for hours, those
    rows look missing/stale and the self-heal reaper can misclassify the repair
    as abandoned.  Commit only when the session implementation supports it so
    lightweight unit-test fakes remain simple.
    """
    commit = getattr(session, "commit", None)
    if callable(commit):
        await commit()


def _project_repair_source_artifact_audit(
    settings: AppSettings,
    project: ProjectModel,
) -> tuple[SourceArtifactAuditReport | None, str | None, str | None]:
    """Audit source artifacts for project repair when an output package exists."""

    output_dir = Path(settings.output.base_dir)
    package_dir = output_dir / project.slug
    if not package_dir.exists():
        return None, None, "output_package_missing"

    metadata = getattr(project, "metadata_json", None) or {}
    report = audit_source_artifacts(
        project.slug,
        output_dir=output_dir,
        expected_language=getattr(project, "language", None),
        expected_platform=_source_audit_expected_platform(metadata),
        expected_category=_source_audit_expected_category(project, metadata),
    )
    out_path = package_dir / "audits" / "source-artifacts" / "report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report, str(out_path), None


def _source_audit_expected_platform(metadata: dict[str, Any]) -> str | None:
    for key in (
        "platform",
        "target_platform",
        "publishing_platform",
        "canonical_platform",
    ):
        value = str(metadata.get(key) or "").strip()
        if value and value != "framework":
            return value
    return None


def _source_audit_expected_category(
    project: ProjectModel,
    metadata: dict[str, Any],
) -> str | None:
    for value in (
        metadata.get("canonical_category"),
        metadata.get("category_key"),
        getattr(project, "genre", None),
        getattr(project, "sub_genre", None),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return None


def _source_audit_blocks_project_repair(
    report: SourceArtifactAuditReport | None,
) -> bool:
    return bool(report and report.blocking_findings)


def _source_audit_payload(
    report: SourceArtifactAuditReport | None,
) -> dict[str, Any]:
    return report.to_dict() if report is not None else {}


def _rewrite_task_failure_codes(task: RewriteTaskModel) -> list[str]:
    metadata = task.metadata_json or {}
    raw_codes = metadata.get("cause_ids") or metadata.get("blocking_codes")
    if isinstance(raw_codes, str):
        codes = [item.strip() for item in re.split(r"[;,]", raw_codes) if item.strip()]
    elif isinstance(raw_codes, list):
        codes = [str(item).strip() for item in raw_codes if str(item).strip()]
    else:
        codes = []
    if not codes and metadata.get("write_safety_block_code"):
        codes = [str(metadata["write_safety_block_code"])]
    if not codes:
        trigger = str(task.trigger_type or "rewrite_task").upper()
        codes = [f"{trigger}_REPAIR_TASK"]
    return codes[:8]


def _rewrite_task_event_severity(task: RewriteTaskModel) -> str:
    try:
        priority = int(task.priority or 3)
    except (TypeError, ValueError):
        priority = 3
    if priority <= 1:
        return "critical"
    if priority <= 2:
        return "high"
    if priority <= 4:
        return "medium"
    return "low"


def _quality_failure_events_for_rewrite_task(
    project: ProjectModel,
    task: RewriteTaskModel,
    chapter_numbers: Iterable[int],
) -> list[dict[str, Any]]:
    chapter_number = next(iter(_dedupe_sorted(chapter_numbers)), None)
    severity = _rewrite_task_event_severity(task)
    events = [
        QualityFailureEvent(
            slug=project.slug,
            chapter_number=chapter_number,
            stage="project_repair",
            gate_id=str(task.trigger_type or "rewrite_task"),
            code=code,
            severity=severity,
            language=getattr(project, "language", None),
            platform=_source_audit_expected_platform(project.metadata_json or {}),
            source_stage="draft",
            preventable_stage="chapter_quality_gate",
            remediation_class=str(task.rewrite_strategy or "repair_chapter"),
            evidence_ref=str(task.id) if getattr(task, "id", None) else None,
            repair_task_id=str(task.id) if getattr(task, "id", None) else None,
            details={
                "trigger_type": task.trigger_type,
                "rewrite_strategy": task.rewrite_strategy,
                "priority": task.priority,
                "status": task.status,
            },
        )
        for code in _rewrite_task_failure_codes(task)
    ]
    return quality_failure_events_to_dicts(events)


def _stamp_project_repair_task_metadata(
    project: ProjectModel,
    task: RewriteTaskModel,
    *,
    chapter_numbers: Iterable[int],
    source_audit_report: SourceArtifactAuditReport | None,
) -> None:
    metadata = dict(task.metadata_json or {})
    metadata["project_repair_source_audit_checked"] = source_audit_report is not None
    if source_audit_report is not None:
        metadata["source_audit_report"] = _source_audit_payload(source_audit_report)
    if not metadata.get("quality_failure_events"):
        metadata["quality_failure_events"] = _quality_failure_events_for_rewrite_task(
            project,
            task,
            chapter_numbers,
        )
    task.metadata_json = metadata


async def _refresh_stale_truth_materializations_for_repair(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    *,
    requested_by: str,
    progress: ProgressCallback | None = None,
) -> bool:
    """Bring truth-version materializations current before repair rewrites.

    Resume/autowrite paths already refresh stale story bible, outline, and
    narrative graph components before drafting. Project repair also calls the
    chapter pipeline, so it needs the same preflight or it can be re-queued by
    self-heal forever while failing before the first repaired chapter.
    """

    from bestseller.services.pipelines import (  # noqa: PLC0415
        _refresh_stale_truth_materializations_for_resume,
    )

    return await _refresh_stale_truth_materializations_for_resume(
        session,
        settings,
        project,
        requested_by=requested_by,
        progress=progress,
    )


async def _load_pending_rewrite_tasks(
    session: AsyncSession,
    *,
    project_id: UUID,
    limit: int | None = None,
) -> list[RewriteTaskModel]:
    query = (
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
    if limit is not None and int(limit) > 0:
        query = query.limit(int(limit))
    return list(await session.scalars(query))


async def _load_publication_blocked_chapter_numbers(
    session: AsyncSession,
    *,
    project: Any,
    settings: AppSettings,
    scan_publication_gate_candidates: bool = False,
) -> set[int]:
    """Find current chapters that cannot pass publication/export gates.

    Project repair used to depend only on pending ``rewrite_tasks``. That left
    chapters marked ``production_state=blocked`` by deterministic gates, such
    as cross-chapter repetition, outside the actual repair run unless some
    separate review task also existed. Use the publication gate itself as the
    repair target oracle so blocked chapters cannot fall through that gap.
    """

    rows = await session.execute(
        select(ChapterModel, ChapterDraftVersionModel)
        .outerjoin(
            ChapterDraftVersionModel,
            and_(
                ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                ChapterDraftVersionModel.is_current.is_(True),
            ),
        )
        .where(
            ChapterModel.project_id == project.id,
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    payloads = list(rows.all())
    from bestseller.services.deduplication import (  # noqa: PLC0415
        detect_chapter_text_loop,
        detect_cross_chapter_repetition,
        detect_intra_chapter_repetition,
        detect_short_cluster_near_repeat,
    )
    from bestseller.services.drafts import count_words  # noqa: PLC0415
    from bestseller.services.output_hygiene import (
        collect_unfinished_artifact_issues,  # noqa: PLC0415
    )

    blocked: set[int] = set()
    language = getattr(project, "language", None)
    hard_word_max = int(getattr(settings.generation.words_per_chapter, "max", 0) or 0)
    identity_registry: list[Any] | None = None
    for chapter, draft in payloads:
        chapter_number = int(chapter.chapter_number)
        status = (getattr(chapter, "status", "") or "").lower()
        production_state = (getattr(chapter, "production_state", "") or "").lower()
        content = draft.content_md if draft is not None else ""

        # Project repair is for chapters that have failed production gates. A
        # long-running book can legitimately have hundreds of current drafts in
        # revision/pending states; those belong to the normal writing pipeline,
        # not to a gate repair sweep.
        explicitly_blocked = production_state == "blocked"
        if explicitly_blocked:
            if _chapter_has_identity_write_safety_block(chapter) and identity_registry is None:
                from bestseller.services.identity_guard import (
                    load_identity_registry,  # noqa: PLC0415
                )

                identity_registry = await load_identity_registry(session, project.id)
            explicitly_blocked = await _heal_chapter_gate_state_before_repair(
                session,
                chapter,
                draft,
                project=project,
                settings=settings,
                identity_registry=identity_registry or (),
                language=language or "zh-CN",
            )
            production_state = (getattr(chapter, "production_state", "") or "").lower()
        publication_candidate = status == "complete"
        if explicitly_blocked:
            blocked.add(chapter_number)
        if not scan_publication_gate_candidates:
            continue
        if not publication_candidate:
            continue
        if production_state != "ok":
            blocked.add(chapter_number)
        if draft is None or not list(getattr(draft, "assembled_from_scene_draft_ids", None) or []):
            blocked.add(chapter_number)
        if (
            production_state != "ok"
            and hard_word_max > 0
            and count_words(content) > hard_word_max
        ):
            blocked.add(chapter_number)
        if collect_unfinished_artifact_issues(content, language=language):
            blocked.add(chapter_number)
        if await _chapter_has_incomplete_scene_drafts(
            session,
            chapter,
            language=language,
        ):
            blocked.add(chapter_number)
        if (
            detect_chapter_text_loop(content)
            or detect_short_cluster_near_repeat(content)
            or detect_intra_chapter_repetition(content)
        ):
            blocked.add(chapter_number)

    cross_findings = detect_cross_chapter_repetition(
        [
            (int(chapter.chapter_number), draft.content_md or "")
            for chapter, draft in payloads
            if scan_publication_gate_candidates
            and draft is not None
            and getattr(chapter, "chapter_number", None) is not None
            and (
                (getattr(chapter, "production_state", "") or "").lower() == "blocked"
                or (getattr(chapter, "status", "") or "").lower() == "complete"
            )
        ]
    )
    cross_eligible_chapters = {
        int(chapter.chapter_number)
        for chapter, _draft in payloads
        if getattr(chapter, "chapter_number", None) is not None
        and (
            (getattr(chapter, "production_state", "") or "").lower() == "blocked"
            or (getattr(chapter, "status", "") or "").lower() == "complete"
        )
    }
    for finding in cross_findings:
        chapter_number = int(finding.get("chapter") or 0)
        if chapter_number in cross_eligible_chapters:
            blocked.add(chapter_number)
    return blocked


async def _heal_chapter_gate_state_before_repair(
    session: AsyncSession,
    chapter: Any,
    draft: Any,
    *,
    project: Any,
    settings: AppSettings,
    identity_registry: Iterable[Any],
    language: str,
) -> bool:
    """Normalize or release a blocked chapter before choosing repair strategy.

    This is the repair workflow's gate-status preflight. It prevents stale
    historical metadata from masquerading as an active production block and
    gives every still-active block a standard code that auto-repair can route.
    Returns True when the chapter remains blocked.
    """

    if (getattr(chapter, "production_state", "") or "").lower() != "blocked":
        return False

    if _release_resolved_identity_write_safety_block(
        chapter,
        draft,
        identity_registry=identity_registry,
        language=language,
    ):
        return False

    latest_report = await _load_latest_chapter_quality_report(session, chapter)
    if latest_report is not None:
        report_codes = _quality_report_blocking_codes(latest_report)
        if report_codes:
            _stamp_standard_gate_block(
                chapter,
                report_codes,
                source="chapter_quality_report",
                hint=_quality_report_block_hint(latest_report, report_codes),
            )
            return True
        if _can_release_stale_nonblocking_quality_gate(chapter):
            _release_stale_gate_block(
                chapter,
                code="chapter_quality_gate",
                resolved_by="nonblocking_quality_report_revalidation",
            )
            return False

    legacy_length_code = _legacy_repair_audit_length_block_code(
        chapter,
        draft,
        settings=settings,
    )
    if legacy_length_code:
        _stamp_standard_gate_block(
            chapter,
            (legacy_length_code,),
            source="legacy_repair_audit",
            hint="历史修复审计发现章节长度仍不在发布范围内，进入章节自动修复。",
        )
        return True
    if _legacy_repair_audit_length_block_is_resolved(chapter, draft, settings=settings):
        _release_stale_gate_block(
            chapter,
            code="current_chapter_length_out_of_range",
            resolved_by="repair_audit_length_revalidation",
        )
        return False

    metadata = getattr(chapter, "metadata_json", None) or {}
    auto_repair_codes = tuple(
        str(code)
        for code in metadata.get("auto_repair_last_block_codes", ())
        if code
    ) if isinstance(metadata, dict) else ()
    if auto_repair_codes:
        _stamp_standard_gate_block(
            chapter,
            auto_repair_codes,
            source="auto_repair_last_block_codes",
            hint="章节上次自动修复仍未通过门禁，继续按标准门禁 code 路由修复。",
            write_safety=False,
        )
    return True


async def _load_latest_chapter_quality_report(
    session: AsyncSession,
    chapter: Any,
) -> Any | None:
    try:
        report = await session.scalar(
            select(ChapterQualityReportModel)
            .where(ChapterQualityReportModel.chapter_id == chapter.id)
            .order_by(ChapterQualityReportModel.created_at.desc())
            .limit(1)
        )
        if report is None:
            return None
        if not hasattr(report, "report_json") and not hasattr(report, "blocks_write"):
            return None
        return report
    except Exception:
        logger.debug(
            "chapter %s: latest quality report lookup failed during repair preflight",
            getattr(chapter, "chapter_number", "?"),
            exc_info=True,
        )
        return None


def _quality_report_blocking_codes(report: Any) -> tuple[str, ...]:
    payload = getattr(report, "report_json", None) or {}
    if not isinstance(payload, dict):
        payload = {}
    codes = tuple(str(code) for code in (payload.get("blocking_codes") or ()) if code)
    if codes:
        return codes
    if bool(getattr(report, "blocks_write", False)):
        return ("quality_gate_blocked",)
    return ()


def _quality_report_block_hint(report: Any, codes: tuple[str, ...]) -> str:
    payload = getattr(report, "report_json", None) or {}
    if not isinstance(payload, dict):
        payload = {}
    details: list[str] = []
    for violation in payload.get("violations") or ():
        if not isinstance(violation, dict):
            continue
        code = str(violation.get("code") or "")
        detail = str(violation.get("detail") or "").strip()
        if code in codes and detail:
            details.append(f"{code}: {detail}")
    if details:
        return "；".join(details[:3])
    return f"章节质量门禁未通过：{', '.join(codes)}"


def _can_release_stale_nonblocking_quality_gate(chapter: Any) -> bool:
    metadata = getattr(chapter, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        return True
    active_block_flags = {
        "blocked_by_write_safety_gate",
        "post_assembly_duplicate_gate",
        "blocked_by_l2_bible_gate",
        "blocked_before_chapter_assembly",
    }
    return not any(metadata.get(flag) for flag in active_block_flags)


def _stamp_standard_gate_block(
    chapter: Any,
    codes: Iterable[str],
    *,
    source: str,
    hint: str,
    write_safety: bool = True,
) -> None:
    normalized = tuple(_canonical_gate_block_code(str(code)) for code in codes if code)
    if not normalized:
        return
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    metadata["production_block_code"] = normalized[0]
    metadata["quality_gate_block_code"] = normalized[0]
    metadata["quality_gate_block_codes"] = list(normalized)
    metadata["quality_gate_block_source"] = source
    metadata["quality_gate_block_hint"] = hint
    if write_safety:
        metadata["blocked_by_write_safety_gate"] = True
        metadata["write_safety_block_code"] = normalized[0]
        metadata["write_safety_hint"] = hint
    chapter.metadata_json = metadata
    chapter.production_state = "blocked"


def _canonical_gate_block_code(code: str) -> str:
    text = str(code).strip()
    return {
        "BLOCK_LOW": "CHAPTER_LENGTH_BLOCK_LOW",
        "LENGTH_UNDER": "CHAPTER_LENGTH_BLOCK_LOW",
        "BLOCK_HIGH": "CHAPTER_LENGTH_BLOCK_HIGH",
        "LENGTH_OVER": "CHAPTER_LENGTH_BLOCK_HIGH",
    }.get(text, text)


def _release_stale_gate_block(
    chapter: Any,
    *,
    code: str,
    resolved_by: str,
) -> None:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    previous = {
        key: metadata.get(key)
        for key in (
            "production_block_code",
            "quality_gate_block_code",
            "quality_gate_block_codes",
            "quality_gate_block_source",
            "quality_gate_block_hint",
            "write_safety_block_code",
            "write_safety_hint",
            "auto_repair_last_block_codes",
            "blocked_by_repair_audit",
        )
        if key in metadata
    }
    for key in (
        "blocked_by_write_safety_gate",
        "write_safety_block_code",
        "write_safety_hint",
        "production_block_code",
        "quality_gate_block_code",
        "quality_gate_block_codes",
        "quality_gate_block_source",
        "quality_gate_block_hint",
        "auto_repair_exhausted",
        "auto_repair_in_progress",
        "auto_repair_last_block_codes",
        "blocked_by_repair_audit",
    ):
        metadata.pop(key, None)
    metadata["resolved_quality_gate_block"] = {
        "code": code,
        "resolved_by": resolved_by,
        "previous": previous,
    }
    chapter.metadata_json = metadata
    chapter.production_state = "ok"


def _legacy_repair_audit_length_block_code(
    chapter: Any,
    draft: Any,
    *,
    settings: AppSettings,
) -> str | None:
    metadata = getattr(chapter, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        return None
    if metadata.get("blocked_by_repair_audit") != "current_chapter_length_out_of_range":
        return None
    word_count = _current_repair_audit_word_count(chapter, draft)
    min_words, max_words = _repair_audit_length_bounds(metadata, settings=settings)
    if word_count <= 0:
        return "CHAPTER_LENGTH_BLOCK_LOW"
    if min_words > 0 and word_count < min_words:
        return "CHAPTER_LENGTH_BLOCK_LOW"
    if max_words > 0 and word_count > max_words:
        return "CHAPTER_LENGTH_BLOCK_HIGH"
    return None


def _legacy_repair_audit_length_block_is_resolved(
    chapter: Any,
    draft: Any,
    *,
    settings: AppSettings,
) -> bool:
    metadata = getattr(chapter, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        return False
    if metadata.get("blocked_by_repair_audit") != "current_chapter_length_out_of_range":
        return False
    return _legacy_repair_audit_length_block_code(
        chapter,
        draft,
        settings=settings,
    ) is None


def _current_repair_audit_word_count(chapter: Any, draft: Any) -> int:
    if draft is not None:
        try:
            draft_words = int(getattr(draft, "word_count", 0) or 0)
            if draft_words > 0:
                return draft_words
        except (TypeError, ValueError):
            pass
    metadata = getattr(chapter, "metadata_json", None) or {}
    if isinstance(metadata, dict):
        try:
            return int(metadata.get("repair_audit_word_count") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _repair_audit_length_bounds(
    metadata: dict[str, Any],
    *,
    settings: AppSettings,
) -> tuple[int, int]:
    try:
        min_words = int(metadata.get("repair_audit_min_words") or 0)
    except (TypeError, ValueError):
        min_words = 0
    try:
        max_words = int(metadata.get("repair_audit_max_words") or 0)
    except (TypeError, ValueError):
        max_words = 0
    if min_words <= 0:
        min_words = int(getattr(settings.generation.words_per_chapter, "min", 0) or 0)
    if max_words <= 0:
        max_words = int(getattr(settings.generation.words_per_chapter, "max", 0) or 0)
    return min_words, max_words


_IDENTITY_WRITE_SAFETY_BLOCK_CODES = frozenset(
    {
        "pronoun_mismatch",
        "dead_alive",
        "character_resurrection",
    }
)


def _chapter_identity_write_safety_block_code(chapter: Any) -> str | None:
    metadata = getattr(chapter, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        return None
    code = str(metadata.get("write_safety_block_code") or "").strip()
    if code in _IDENTITY_WRITE_SAFETY_BLOCK_CODES:
        return code
    return None


def _chapter_has_identity_write_safety_block(chapter: Any) -> bool:
    return _chapter_identity_write_safety_block_code(chapter) is not None


def _release_resolved_identity_write_safety_block(
    chapter: Any,
    draft: Any,
    *,
    identity_registry: Iterable[Any],
    language: str,
) -> bool:
    """Clear stale identity blocks when the current identity gate now passes."""

    block_code = _chapter_identity_write_safety_block_code(chapter)
    if block_code is None:
        return False
    content = str(getattr(draft, "content_md", "") or "") if draft is not None else ""
    if not content:
        return False

    from bestseller.services.identity_guard import validate_scene_text_identity  # noqa: PLC0415

    violations = validate_scene_text_identity(
        content,
        list(identity_registry),
        language=language or "zh-CN",
        chapter_number=getattr(chapter, "chapter_number", None),
    )
    if any(violation.violation_type == block_code for violation in violations):
        return False

    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    previous_hint = metadata.get("write_safety_hint")
    for key in (
        "blocked_by_write_safety_gate",
        "write_safety_block_code",
        "write_safety_hint",
        "auto_repair_exhausted",
        "auto_repair_in_progress",
        "auto_repair_last_block_codes",
    ):
        metadata.pop(key, None)
    metadata["resolved_write_safety_block"] = {
        "code": block_code,
        "resolved_by": "identity_guard_revalidation",
        "previous_hint": previous_hint,
    }
    chapter.metadata_json = metadata
    chapter.production_state = "ok"
    return True


async def _normalize_project_word_targets(
    session: AsyncSession,
    *,
    project: Any,
    settings: AppSettings,
) -> dict[str, Any]:
    """Self-heal stale chapter/scene targets before repair writes new drafts."""

    chapters = list(
        await session.scalars(
            select(ChapterModel)
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )
    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.project_id == project.id)
            .order_by(SceneCardModel.chapter_id.asc(), SceneCardModel.scene_number.asc())
        )
    )
    scenes_by_chapter: dict[UUID, list[SceneCardModel]] = defaultdict(list)
    for scene in scenes:
        scenes_by_chapter[scene.chapter_id].append(scene)

    policy = word_target_policy(settings)
    chapter_updates = 0
    scene_updates = 0
    changed_chapters: list[int] = []
    for chapter in chapters:
        original_chapter_target = int(chapter.target_word_count or 0)
        normalized_chapter_target = normalize_chapter_word_target(
            original_chapter_target,
            project,
            settings,
        )
        chapter_changed = original_chapter_target != normalized_chapter_target
        if chapter_changed:
            chapter.target_word_count = normalized_chapter_target
            chapter_updates += 1
            changed_chapters.append(int(chapter.chapter_number))

        chapter_scenes = scenes_by_chapter.get(chapter.id, [])
        if not chapter_scenes:
            continue
        scene_total = sum(int(scene.target_word_count or 0) for scene in chapter_scenes)
        needs_scene_normalization = (
            chapter_changed
            or scene_total < policy.chapter_min
            or scene_total > policy.chapter_max
            or any(int(scene.target_word_count or 0) <= 0 for scene in chapter_scenes)
        )
        if not needs_scene_normalization:
            continue
        scene_target = scene_word_target_for_chapter(
            chapter.target_word_count,
            len(chapter_scenes),
            settings,
        )
        for scene in chapter_scenes:
            if int(scene.target_word_count or 0) == scene_target:
                continue
            scene.target_word_count = scene_target
            scene_updates += 1

    if chapter_updates or scene_updates:
        await session.flush()
    return {
        "chapter_updates": chapter_updates,
        "scene_updates": scene_updates,
        "changed_chapter_numbers": changed_chapters[:200],
        "changed_chapter_count": len(changed_chapters),
        "chapter_min": policy.chapter_min,
        "chapter_target": policy.chapter_target,
        "chapter_max": policy.chapter_max,
    }


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
            try:
                analysis = await refresh_rewrite_impacts(
                    session,
                    project_slug,
                    rewrite_task_id=task.id,
                )
            except ValueError as exc:
                if str(exc) not in _ORPHAN_REWRITE_TASK_ERRORS:
                    raise
                logger.warning(
                    "Skipping orphan rewrite task %s during project repair: %s",
                    task.id,
                    exc,
                )
                return chapter_numbers
            for impact in analysis.impacts:
                if impact.impacted_type == "chapter":
                    chapter_number = await _chapter_number_from_chapter_id(
                        session,
                        impact.impacted_id,
                    )
                elif impact.impacted_type == "scene":
                    chapter_number = await _chapter_number_from_scene_id(
                        session,
                        impact.impacted_id,
                    )
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
    include_pending_rewrite_tasks: bool = True,
    pending_rewrite_task_limit: int | None = None,
    scan_publication_gate_candidates: bool = False,
    progress: ProgressCallback | None = None,
) -> ProjectRepairResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    pending_tasks = (
        await _load_pending_rewrite_tasks(
            session,
            project_id=project.id,
            limit=pending_rewrite_task_limit,
        )
        if include_pending_rewrite_tasks
        else []
    )
    task_count = len(pending_tasks)
    _emit_progress(
        progress,
        "project_repair_started",
        {
            "project_slug": project_slug,
            "pending_rewrite_task_count": task_count,
            "include_pending_rewrite_tasks": include_pending_rewrite_tasks,
            "pending_rewrite_task_limit": pending_rewrite_task_limit,
            "scan_publication_gate_candidates": scan_publication_gate_candidates,
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
            "include_pending_rewrite_tasks": include_pending_rewrite_tasks,
            "pending_rewrite_task_limit": pending_rewrite_task_limit,
            "scan_publication_gate_candidates": scan_publication_gate_candidates,
        },
    )
    workflow_run_id = workflow_run.id
    await _checkpoint_repair_progress(session)

    step_order = 1
    current_step_name = "collect_pending_rewrite_tasks"

    try:
        source_audit_report, source_audit_path, source_audit_skip_reason = (
            _project_repair_source_artifact_audit(settings, project)
        )
        if source_audit_report is not None:
            current_step_name = "source_artifact_audit"
            workflow_run.current_step = current_step_name
            source_audit_blocks = _source_audit_blocks_project_repair(source_audit_report)
            source_audit_payload = _source_audit_payload(source_audit_report)
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=(
                    WorkflowStatus.FAILED
                    if source_audit_blocks
                    else WorkflowStatus.COMPLETED
                ),
                output_ref={
                    "project_slug": project_slug,
                    "source_audit_path": source_audit_path,
                    "source_blocked": source_audit_blocks,
                    "source_audit_report": source_audit_payload,
                },
                error_message=(
                    "Project repair blocked by source artifact audit"
                    if source_audit_blocks
                    else None
                ),
            )
            step_order += 1
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "source_artifact_audit_path": source_audit_path,
                "source_artifact_audit": source_audit_payload,
                "source_blocked": source_audit_blocks,
            }
            _emit_progress(
                progress,
                (
                    "project_repair_source_artifact_blocked"
                    if source_audit_blocks
                    else "project_repair_source_artifact_audit_passed"
                ),
                {
                    "project_slug": project_slug,
                    "source_audit_path": source_audit_path,
                    "source_blocked": source_audit_blocks,
                    "blocking_findings": source_audit_payload.get(
                        "blocking_findings",
                        [],
                    ),
                },
            )
            await _checkpoint_repair_progress(session)
            if source_audit_blocks:
                workflow_run.status = WorkflowStatus.WAITING_HUMAN.value
                workflow_run.current_step = "source_artifact_audit_blocked"
                project.status = ProjectStatus.REVISING.value
                await session.flush()
                await _checkpoint_repair_progress(session)
                return ProjectRepairResult(
                    workflow_run_id=workflow_run.id,
                    project_id=project.id,
                    project_slug=project.slug,
                    pending_rewrite_task_count=task_count,
                    superseded_task_count=0,
                    processed_chapters=[],
                    review_report_id=None,
                    quality_score_id=None,
                    final_verdict="source_artifact_blocked",
                    export_artifact_id=None,
                    output_path=None,
                    remaining_pending_rewrite_count=task_count,
                    requires_human_review=True,
                )
            current_step_name = "collect_pending_rewrite_tasks"
            workflow_run.current_step = current_step_name
        else:
            workflow_run.metadata_json = {
                **workflow_run.metadata_json,
                "source_artifact_audit_skipped": source_audit_skip_reason,
            }

        truth_refreshed = await _refresh_stale_truth_materializations_for_repair(
            session,
            settings,
            project,
            requested_by=requested_by,
            progress=progress,
        )
        if truth_refreshed:
            current_step_name = "refresh_truth_materializations"
            workflow_run.current_step = current_step_name
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "project_slug": project_slug,
                    "refreshed": True,
                },
            )
            step_order += 1
            await _checkpoint_repair_progress(session)
            current_step_name = "collect_pending_rewrite_tasks"
            workflow_run.current_step = current_step_name

        word_target_normalization = await _normalize_project_word_targets(
            session,
            project=project,
            settings=settings,
        )
        if (
            word_target_normalization["chapter_updates"]
            or word_target_normalization["scene_updates"]
        ):
            current_step_name = "normalize_word_targets"
            workflow_run.current_step = current_step_name
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref=word_target_normalization,
            )
            step_order += 1
            _emit_progress(
                progress,
                "project_repair_word_targets_normalized",
                {
                    "project_slug": project_slug,
                    **word_target_normalization,
                },
            )
            await _checkpoint_repair_progress(session)
            current_step_name = "collect_pending_rewrite_tasks"
            workflow_run.current_step = current_step_name

        chapter_task_ids: dict[int, list[UUID]] = defaultdict(list)
        for task in pending_tasks:
            chapter_numbers = await _resolve_rewrite_task_chapter_numbers(
                session,
                project_slug=project_slug,
                task=task,
                refresh_impacts=refresh_impacts,
            )
            _stamp_project_repair_task_metadata(
                project,
                task,
                chapter_numbers=chapter_numbers,
                source_audit_report=source_audit_report,
            )
            for chapter_number in chapter_numbers:
                chapter_task_ids[chapter_number].append(task.id)

        repair_gate_chapter_numbers = await _load_publication_blocked_chapter_numbers(
            session,
            project=project,
            settings=settings,
            scan_publication_gate_candidates=scan_publication_gate_candidates,
        )
        for chapter_number in repair_gate_chapter_numbers:
            chapter_task_ids.setdefault(chapter_number, [])

        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "pending_rewrite_task_ids": [str(task.id) for task in pending_tasks],
                "target_chapter_numbers": _dedupe_sorted(chapter_task_ids.keys()),
                "repair_gate_chapter_numbers": _dedupe_sorted(repair_gate_chapter_numbers),
            },
        )
        step_order += 1
        _emit_progress(
            progress,
            "project_repair_targets_collected",
            {
                "project_slug": project_slug,
                "pending_rewrite_task_count": task_count,
                "include_pending_rewrite_tasks": include_pending_rewrite_tasks,
                "pending_rewrite_task_limit": pending_rewrite_task_limit,
                "scan_publication_gate_candidates": scan_publication_gate_candidates,
                "target_chapter_numbers": _dedupe_sorted(chapter_task_ids.keys()),
                "repair_gate_chapter_numbers": _dedupe_sorted(repair_gate_chapter_numbers),
            },
        )
        await _checkpoint_repair_progress(session)

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
            await _checkpoint_repair_progress(session)

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
                allow_structural_repair=True,
            )
            repaired_chapter = await session.get(ChapterModel, chapter_result.chapter_id)
            repaired_chapter_status = (
                str(getattr(repaired_chapter, "status", "") or "")
                if repaired_chapter is not None
                else None
            )
            repaired_production_state = (
                str(getattr(repaired_chapter, "production_state", "") or "")
                if repaired_chapter is not None
                else None
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
                    "source_task_ids": [
                        str(task_id) for task_id in chapter_task_ids[chapter_number]
                    ],
                    "requires_human_review": chapter_result.requires_human_review,
                    "chapter_status": repaired_chapter_status,
                    "production_state": repaired_production_state,
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
                    "chapter_status": repaired_chapter_status,
                    "production_state": repaired_production_state,
                },
            )
            await _checkpoint_repair_progress(session)

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
            export_skipped_reason: str | None = None
            try:
                artifact, artifact_path = await export_project_markdown(
                    session,
                    settings,
                    project_slug,
                    created_by_run_id=workflow_run.id,
                )
                export_artifact_id = artifact.id
                output_path = str(artifact_path.resolve())
            except ValueError as exc:
                # Repair can run while a book is still mid-production.  In that
                # state full-project export may be correctly blocked by chapters
                # that are not part of this repair scope.  Keep repair alive and
                # let the consistency review report the remaining readiness gap.
                export_skipped_reason = str(exc)
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "export_artifact_id": str(export_artifact_id) if export_artifact_id else None,
                    "output_path": output_path,
                    "skipped": bool(export_skipped_reason),
                    "skip_reason": export_skipped_reason[:4000] if export_skipped_reason else None,
                },
            )
            step_order += 1
            if export_skipped_reason:
                _emit_progress(
                    progress,
                    "project_repair_export_skipped",
                    {
                        "project_slug": project_slug,
                        "reason": export_skipped_reason[:1000],
                    },
                )
            else:
                _emit_progress(
                    progress,
                    "project_repair_export_completed",
                    {
                        "project_slug": project_slug,
                        "export_artifact_id": str(export_artifact_id),
                        "output_path": output_path,
                    },
                )
            await _checkpoint_repair_progress(session)

        current_step_name = "review_project_consistency"
        workflow_run.current_step = current_step_name
        review_result, report, quality = await review_project_consistency(
            session,
            settings,
            project_slug,
            workflow_run_id=workflow_run.id,
            expect_project_export=bool(export_artifact_id),
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
        await _checkpoint_repair_progress(session)

        remaining_pending_rewrite_count = (
            int(
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
            if include_pending_rewrite_tasks
            else 0
        )
        requires_human_review = (
            requires_human_review
            or review_result.verdict != "pass"
            or remaining_pending_rewrite_count > 0
        )
        if processed_chapters:
            project.current_chapter_number = max(
                int(project.current_chapter_number or 0),
                max(item.chapter_number for item in processed_chapters),
            )
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
        await _checkpoint_repair_progress(session)
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
        if (
            isinstance(exc, (PendingRollbackError, DBAPIError))
            or not getattr(session, "is_active", True)
        ):
            await session.rollback()
            raise
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run_id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
        await _checkpoint_repair_progress(session)
        raise
