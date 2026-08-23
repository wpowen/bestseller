from __future__ import annotations

from argparse import Namespace
import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, suppress
import datetime as _dt
import importlib.util
import logging
import os
from pathlib import Path
import random
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from bestseller.domain.enums import ProjectStatus, WorkflowStatus
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.infra.db.session import get_server_session
from bestseller.services.planning_concurrency import PlanningConflictError
from bestseller.services.progress_context import set_ambient
from bestseller.settings import get_settings
from bestseller.worker.progress import RedisProgressReporter, make_sync_callback

logger = logging.getLogger(__name__)


def _pipeline_start_key(project_id: Any) -> str:
    """Mirror of api/routers/pipelines.py::_pipeline_start_key."""
    return f"pipeline:starting:{project_id}"


async def _clear_pipeline_start_marker(redis: Any, project_id: Any) -> None:
    """Drop the API's start reservation once the worker is actually running.

    The API holds a short-lived NX marker (``pipeline:starting:{project_id}``)
    to close the TOCTOU between the DB guard and the worker materializing its
    workflow row. Once the worker has loaded the project and is about to write
    rows, the marker is no longer needed; leaving it would 409 legitimate
    re-triggers of fast pipelines until its TTL expired (P07).
    """
    if project_id is None:
        return
    try:
        await redis.delete(_pipeline_start_key(project_id))
    except Exception:
        logger.debug("pipeline start marker clear failed", exc_info=True)


_ACTIVE_WORKFLOW_STATUSES = frozenset(
    {
        WorkflowStatus.PENDING.value,
        WorkflowStatus.QUEUED.value,
        WorkflowStatus.RUNNING.value,
    }
)
_PROJECT_HEARTBEAT_WORKFLOW_TYPES = frozenset(
    {
        "project_pipeline",
        "chapter_pipeline",
        "scene_pipeline",
        "project_repair",
        "generate_novel_plan",
        "generate_foundation_plan",
        "generate_volume_plan",
        "materialize_chapter_outline_batch",
        "materialize_story_bible",
        "materialize_narrative_graph",
        "materialize_narrative_tree",
    }
)
_WORKFLOW_HEARTBEAT_SECONDS = int(os.getenv("BESTSELLER_WORKFLOW_HEARTBEAT_SECONDS", "60"))
_ATTENTION_VERDICTS = frozenset(
    {
        "attention",
        "needs_attention",
        "machine_blocked",
        "machine_repair_required",
        "requires_human_review",
        "exported_requires_human_review",
        "skipped_requires_human_review",
    }
)
_AUTO_QUALITY_CLOSURE_ENV = "BESTSELLER_AUTO_QUALITY_CLOSURE"
_DEFAULT_CLOSURE_MAX_ROUNDS = int(os.getenv("BESTSELLER_CLOSURE_MAX_ROUNDS", "8"))
_DEFAULT_CLOSURE_ROUND_SIZE = int(os.getenv("BESTSELLER_CLOSURE_ROUND_SIZE", "10"))
MAX_OUTLINE_REPLAN_ATTEMPTS = int(
    os.getenv("BESTSELLER_OUTLINE_REPLAN_MAX_ATTEMPTS", "5")
)
OUTLINE_REPLAN_RETRY_BASE_SECONDS = int(
    os.getenv("BESTSELLER_OUTLINE_REPLAN_RETRY_BASE_SECONDS", "120")
)
OUTLINE_REPLAN_RETRY_MAX_SECONDS = int(
    os.getenv("BESTSELLER_OUTLINE_REPLAN_RETRY_MAX_SECONDS", "1800")
)

_OUTLINE_REPLAN_GATE_REASONS = frozenset(
    {
        "outline_semantic_gate_failed",
        "outline_replan_in_progress",
        "volume_outline_gate_failed",
        "commercial_planning_readiness_gate_failed",
        "outline_replan_required",
    }
)


def _clear_successful_outline_replan_blockers(metadata: dict[str, Any]) -> None:
    """Remove stale planning/self-heal blockers after an approved replan.

    A project can carry an independent quality/manual-review stop alongside a
    planning failure.  The planning replan must not erase that authoritative
    stop, so attention flags are only purged when the persisted gate reason is
    itself a planning reason (or no reason was recorded).
    """

    reason = str(
        metadata.get("last_generation_gate_reason")
        or metadata.get("production_pause_reason")
        or ""
    ).strip().lower().split(":", 1)[0]
    has_non_planning_attention = reason not in _OUTLINE_REPLAN_GATE_REASONS and bool(
        metadata.get("requires_human_review")
        or metadata.get("requires_machine_repair")
        or metadata.get("human_review_reason")
        or metadata.get("review_reason")
        or metadata.get("human_review_source")
    )

    planning_keys = (
        "self_heal_repair_strategy",
        "generation_gate_auto_retry_needed",
        "generation_resume_blocked_by_planning_gate",
        "generation_auto_repair_exhausted",
        "generation_gate_auto_resume_reason",
        "generation_gate_auto_resume_count",
    )
    for key in planning_keys:
        metadata.pop(key, None)
    if not has_non_planning_attention:
        metadata.pop("requires_human_review", None)
        metadata.pop("requires_machine_repair", None)
        for key in (
            "production_pause_reason",
            "last_generation_gate_reason",
            "last_generation_gate_error",
            "last_generation_gate_blocked_at",
        ):
            metadata.pop(key, None)


def _unlock_outline_replan_for_repair_revision(
    metadata: dict[str, Any],
    repair_revision: str,
) -> bool:
    """Grant one bounded retry after code/data repair changed the failure shape."""

    revision = str(repair_revision or "").strip()
    if (
        not revision
        or not metadata.get("outline_replan_retry_exhausted")
        or metadata.get("outline_replan_last_manual_repair_revision") == revision
    ):
        return False
    metadata["outline_replan_last_manual_repair_revision"] = revision
    metadata["outline_replan_retry_attempts"] = max(
        0,
        MAX_OUTLINE_REPLAN_ATTEMPTS - 1,
    )
    metadata.pop("outline_replan_retry_exhausted", None)
    metadata.pop("outline_replan_next_retry_at", None)
    if metadata.get("production_pause_reason") == "outline_replan_retry_exhausted":
        metadata.pop("requires_human_review", None)
        metadata.pop("requires_machine_repair", None)
    return True


def _metadata_int_value(metadata: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(metadata.get(key, default))
    except (TypeError, ValueError):
        return default


def _outline_replan_retry_delay_seconds(attempt: int) -> int:
    """Apply exponential backoff for bounded outline replan retries."""

    attempt_index = max(1, attempt)
    return min(
        OUTLINE_REPLAN_RETRY_MAX_SECONDS,
        OUTLINE_REPLAN_RETRY_BASE_SECONDS * (2 ** (attempt_index - 1)),
    )


def _outline_replan_retry_exhausted_metadata(
    metadata: dict[str, Any],
    attempts: int,
    now_iso: str,
    reason: str,
) -> dict[str, Any]:
    metadata.update(
        {
            "outline_replan_retry_attempts": attempts,
            "outline_replan_retry_exhausted": True,
            "outline_replan_required": True,
            "requires_human_review": True,
            "requires_machine_repair": True,
            "production_paused": True,
            "production_pause_reason": "outline_replan_retry_exhausted",
            "planning_status": "needs_replan",
            "outline_semantic_gate_status": "needs_replan",
            "outline_replan_last_failed_at": now_iso,
            "outline_replan_last_error": reason[:4000],
        }
    )
    metadata.pop("outline_replan_in_progress", None)
    metadata.pop("outline_replan_prior_outline_version", None)
    metadata.pop("outline_replan_next_retry_at", None)
    return metadata


def _outline_replan_retry_pending_metadata(
    metadata: dict[str, Any],
    attempts: int,
    now_iso: str,
    reason: str,
) -> tuple[dict[str, Any], str]:
    metadata.update(
        {
            "outline_replan_retry_attempts": attempts,
            "outline_replan_required": True,
            "outline_replan_last_failed_at": now_iso,
            "outline_replan_last_error": reason[:4000],
            "planning_status": "needs_replan",
            "outline_semantic_gate_status": "needs_replan",
            "production_paused": True,
            "production_pause_reason": "volume_outline_gate_failed",
            "generation_resume_blocked_until_repair_audit": True,
        }
    )
    next_retry_seconds = _outline_replan_retry_delay_seconds(attempts)
    next_retry_at = (
        _dt.datetime.now(_dt.UTC) + _dt.timedelta(seconds=next_retry_seconds)
    ).isoformat()
    metadata["outline_replan_next_retry_at"] = next_retry_at
    metadata.pop("outline_replan_in_progress", None)
    metadata.pop("outline_replan_prior_outline_version", None)
    return metadata, str(next_retry_at)


def _reconcile_outline_replan_creation_identity(
    project: ProjectModel,
    premise: str,
) -> str:
    """Refresh inferred creation identity before reusing foundation artifacts.

    Outline-only recovery deliberately skips foundation planning.  That also
    skips the normal character-name adjudication step, so a premise repaired
    after conception could otherwise remain paired with the stale inferred
    name stored by the first planning attempt.  Re-run the same deterministic
    adjudication here and let the snapshot service supersede the old lineage.
    """

    from bestseller.services.book_design import (
        ensure_project_book_design_snapshot,
    )
    from bestseller.services.planner import (
        _persist_creation_protagonist_choice,
        _replace_name_drift_in_value,
    )

    metadata = dict(project.metadata_json or {})
    previous_name = str(metadata.get("creation_protagonist_name") or "").strip()
    if premise:
        metadata["premise"] = premise
    project.metadata_json = metadata
    chosen = _persist_creation_protagonist_choice(
        project,
        metadata.get("creation_protagonist_name"),
    )
    forbidden_names = {
        str(item).strip()
        for item in (project.metadata_json.get("protagonist_forbidden_names") or [])
        if str(item).strip() and str(item).strip() != chosen
    }
    if previous_name and previous_name != chosen:
        forbidden_names.add(previous_name)
    if forbidden_names:
        # Only rewrite live inputs that subsequent planning/writing consumes.
        # Historical logs, reports, superseded snapshots, and forbidden-name
        # evidence intentionally keep the old value for auditability.
        for key in (
            "story_spine",
            "concept_contract",
            "hook_card",
            "commercial_brief",
            "seriality_proof",
            "series_engine",
            "conception_artifacts",
            "book_spec",
            "protagonist",
            "opening_quality_contract",
            "qimao_opening_contract",
        ):
            if key in project.metadata_json:
                project.metadata_json[key] = _replace_name_drift_in_value(
                    project.metadata_json[key],
                    forbidden_names=forbidden_names,
                    replacement=chosen,
                )
        concept_contract = project.metadata_json.get("concept_contract")
        if isinstance(concept_contract, dict):
            from bestseller.services.concept_contract import (
                reseal_concept_contract_lineage,
            )

            resealed = reseal_concept_contract_lineage(
                concept_contract,
                target_chapters=int(project.target_chapters),
            )
            project.metadata_json["concept_contract"] = resealed
            project.metadata_json["hook_card"] = dict(resealed["hook_card"])
            project.metadata_json["story_spine"] = dict(resealed["story_spine"])
            project.metadata_json["seriality_proof"] = dict(
                resealed["seriality_proof"]
            )
    active_snapshot = project.metadata_json.get("book_design_snapshot")
    active_snapshot_protagonist = (
        str(active_snapshot.get("protagonist", {}).get("name") or "").strip()
        if isinstance(active_snapshot, dict)
        and isinstance(active_snapshot.get("protagonist"), dict)
        else ""
    )
    # When the canonical protagonist was already repaired on an earlier retry,
    # ``ensure_project_book_design_snapshot`` would otherwise return the old
    # hash-valid snapshot without rebuilding stale reader-promise/engine text
    # that still names a forbidden protagonist. Rebuild only after active
    # surfaces were scrubbed and the snapshot identity itself is already
    # canonical; the first identity change still goes through the normal
    # supersession history path.
    ensure_project_book_design_snapshot(
        project,
        force_rebuild=bool(forbidden_names and active_snapshot_protagonist == chosen),
    )
    return chosen


def _coerce_workflow_run_uuid(workflow_run_id: str) -> UUID | None:
    try:
        return UUID(str(workflow_run_id))
    except (TypeError, ValueError):
        return None


# ── 项目级管线活体互斥锁（2026-08-23）────────────────────────────────
#
# docker rebuild 杀掉跑书中的 worker 后，「继续推进」与「self-heal 补救」
# 同秒各拉起一条 project_pipeline，双管线在两个 worker 上同时写同一章
# （55 秒 4 个草稿版本）。既有防线全部失守且各有结构性原因：API 层 start
# 锁只护 API 入口；self-heal 的所有权检查只枚举 heal 系 job id；
# workflow_runs 行心跳被项目级批量心跳「保鲜」不可信；arq abort 因
# allow_abort_jobs 未开从未生效。
#
# 修=锁和**进程**绑定：SET NX + TTL + 持锁进程续租。进程死了 TTL 到期
# 自动让位，僵尸行骗不了它；redis 不可用降级放行（宁可重复也不阻塞
# 合法工作，与 API start 锁同哲学）。
_PIPELINE_LIVE_TTL_SECONDS = 300
_PIPELINE_LIVE_RENEW_SECONDS = 60


def _pipeline_live_key(project_slug: str) -> str:
    return f"bestseller:pipeline-live:{project_slug}"


async def _acquire_pipeline_live(redis: Any, project_slug: str, run_token: str) -> str:
    """尝试取得本书的管线活体锁。

    返回 "acquired"（我持有）、"held_by_other"（另一条活管线在跑，调用方
    必须放弃）或 "degraded"（redis 不可用，放行但无锁保护）。
    """

    try:
        ok = await redis.set(
            _pipeline_live_key(project_slug),
            str(run_token),
            nx=True,
            ex=_PIPELINE_LIVE_TTL_SECONDS,
        )
    except Exception:
        logger.warning(
            "pipeline live mutex unavailable for %s (redis down) — proceeding unguarded",
            project_slug,
            exc_info=True,
        )
        return "degraded"
    return "acquired" if ok else "held_by_other"


async def _release_pipeline_live(redis: Any, project_slug: str, run_token: str) -> None:
    """只释放自己的锁——迟到的旧进程不得踢掉新任锁主。"""

    key = _pipeline_live_key(project_slug)
    try:
        current = await redis.get(key)
        if isinstance(current, bytes):
            current = current.decode()
        if current == str(run_token):
            await redis.delete(key)
    except Exception:
        logger.debug("pipeline live mutex release skipped", exc_info=True)


async def _renew_pipeline_live_once(
    redis: Any, project_slug: str, run_token: str
) -> None:
    key = _pipeline_live_key(project_slug)
    try:
        current = await redis.get(key)
        if isinstance(current, bytes):
            current = current.decode()
        if current == str(run_token):
            await redis.expire(key, _PIPELINE_LIVE_TTL_SECONDS)
    except Exception:
        logger.debug("pipeline live mutex renew skipped", exc_info=True)


async def _pipeline_live_renew_loop(
    redis: Any, project_slug: str, run_token: str
) -> None:
    while True:
        await asyncio.sleep(_PIPELINE_LIVE_RENEW_SECONDS)
        await _renew_pipeline_live_once(redis, project_slug, run_token)


@asynccontextmanager
async def _pipeline_live_guard(
    redis: Any, project_slug: str, run_token: str, state: str
) -> AsyncIterator[None]:
    """已 acquired 时维持续租并在退出时释放；degraded 时什么也不做。"""

    if state != "acquired":
        yield
        return
    renew = asyncio.create_task(
        _pipeline_live_renew_loop(redis, project_slug, run_token)
    )
    try:
        yield
    finally:
        renew.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await renew
        await _release_pipeline_live(redis, project_slug, run_token)


def _duplicate_pipeline_skip_payload(
    project_slug: str, workflow_run_id: str
) -> dict[str, Any]:
    return {
        "status": "skipped_duplicate_pipeline",
        "project_slug": project_slug,
        "workflow_run_id": str(workflow_run_id),
        "reason": "another live project pipeline already owns this book",
    }


def _single_run_heartbeat_stmt(run_uuid: UUID):
    # 经 FOR UPDATE SKIP LOCKED 子查询：行正被别的事务改时，那个事务自己就会
    # 刷 updated_at，心跳跳过它零损失；换来的是心跳事务从不等行锁，
    # 无法参与死锁环（2026-08-23 真机：批量心跳等锁 18s 与 heal 长事务
    # 互相持锁，postgres 杀掉 heal）。
    locked = (
        select(WorkflowRunModel.id)
        .where(
            WorkflowRunModel.id == run_uuid,
            WorkflowRunModel.status.in_(_ACTIVE_WORKFLOW_STATUSES),
        )
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    return (
        update(WorkflowRunModel)
        .where(WorkflowRunModel.id.in_(locked))
        .values(updated_at=func.now())
    )


def _project_heartbeat_stmt(
    *,
    project_slug: str,
    active_since: _dt.datetime | None,
):
    project_id = (
        select(ProjectModel.id)
        .where(ProjectModel.slug == project_slug)
        .scalar_subquery()
    )
    locked = (
        select(WorkflowRunModel.id)
        .where(
            WorkflowRunModel.project_id == project_id,
            WorkflowRunModel.workflow_type.in_(_PROJECT_HEARTBEAT_WORKFLOW_TYPES),
            WorkflowRunModel.status.in_(_ACTIVE_WORKFLOW_STATUSES),
        )
        .with_for_update(skip_locked=True)
    )
    if active_since is not None:
        locked = locked.where(WorkflowRunModel.created_at >= active_since)
    return (
        update(WorkflowRunModel)
        .where(WorkflowRunModel.id.in_(locked.scalar_subquery()))
        .values(updated_at=func.now())
    )


async def _touch_workflow_run_heartbeat(
    workflow_run_id: str,
    *,
    project_slug: str | None = None,
    active_since: _dt.datetime | None = None,
) -> None:
    run_uuid = _coerce_workflow_run_uuid(workflow_run_id)
    async with get_server_session() as session:
        if run_uuid is not None:
            await session.execute(_single_run_heartbeat_stmt(run_uuid))

        if project_slug:
            await session.execute(
                _project_heartbeat_stmt(
                    project_slug=project_slug,
                    active_since=active_since,
                )
            )

        await session.commit()


def _project_is_archived(project: ProjectModel | None) -> bool:
    if project is None:
        return False
    metadata = getattr(project, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    status = (getattr(project, "status", None) or "").lower()
    return status == ProjectStatus.ARCHIVED.value or bool(metadata.get("library_archived"))


async def _project_slug_is_archived(project_slug: str) -> bool:
    try:
        async with get_server_session() as session:
            project = await session.scalar(
                select(ProjectModel).where(ProjectModel.slug == project_slug)
            )
            return _project_is_archived(project)
    except Exception:
        logger.debug(
            "Could not check archived state for project %s",
            project_slug,
            exc_info=True,
        )
        return False


async def _skip_archived_project_if_needed(
    reporter: RedisProgressReporter,
    project_slug: str,
) -> dict[str, Any] | None:
    if not await _project_slug_is_archived(project_slug):
        return None
    payload = {
        "status": "skipped_archived",
        "project_slug": project_slug,
        "reason": "library_archived",
    }
    await reporter.emit("skipped_archived", payload, event_type="skipped_archived")
    return payload


async def _skip_halted_project_if_needed(
    reporter: RedisProgressReporter,
    project_slug: str,
) -> dict[str, Any] | None:
    """Refuse to run work on a book the operator stopped or paused.

    Guarding the *entry points* rather than the ~15 enqueue sites is deliberate.
    Every automatic continuation — gate auto-continue, repair/closure ping-pong,
    self-heal requeue, web zombie resume — ultimately arrives here, so one check
    covers all of them and no future enqueue path can be added that bypasses it.

    Reads ``book_production_control``, which the pipeline never rewrites; the
    old marker in ``projects.metadata`` was erased by the next checkpoint.
    """

    from bestseller.services.production_control import (
        auto_recovery_is_permitted,
        load_control_state,
    )

    try:
        async with get_server_session() as session:
            project = await session.scalar(
                select(ProjectModel).where(ProjectModel.slug == project_slug)
            )
            if project is None:
                return None
            state = await load_control_state(session, project.id)
    except Exception:
        # Fail *open* on infrastructure errors: a database blip must not look
        # like a stop and strand every book on the platform.
        logger.debug(
            "Could not check production control for project %s",
            project_slug,
            exc_info=True,
        )
        return None

    if auto_recovery_is_permitted(state):
        return None

    payload = {
        "status": "skipped_halted",
        "project_slug": project_slug,
        "reason": state.reason or state.intent.value,
        "intent": state.intent.value,
    }
    logger.info(
        "worker task skipped: project %s halted by operator (intent=%s reason=%s)",
        project_slug,
        state.intent.value,
        state.reason,
    )
    await reporter.emit("skipped_halted", payload, event_type="skipped_halted")
    return payload


async def _skip_outline_replan_project_if_needed(
    reporter: RedisProgressReporter,
    project_slug: str,
    workflow_run_id: str,
) -> dict[str, Any] | None:
    """Fail closed before any worker prose entry point consumes a bad outline."""

    from bestseller.services.gate_registry import project_blocks_all_prose_generation

    try:
        async with get_server_session() as session:
            project = await session.scalar(
                select(ProjectModel).where(ProjectModel.slug == project_slug)
            )
    except Exception:
        logger.debug(
            "Could not check outline-replan state for project %s",
            project_slug,
            exc_info=True,
        )
        return None
    if project is None or not project_blocks_all_prose_generation(project):
        return None
    payload = {
        "status": "skipped_outline_replan",
        "project_slug": project_slug,
        "workflow_run_id": workflow_run_id,
        "reason": "outline_semantic_gate_failed",
        "requires_machine_repair": True,
    }
    await reporter.emit(
        "repairable_auto_continue_pending",
        payload,
        event_type="repairable_auto_continue_pending",
    )
    logger.info(
        "Worker skipped prose task %s for %s because outline replan is required",
        workflow_run_id,
        project_slug,
    )
    return payload


async def _workflow_heartbeat_loop(
    workflow_run_id: str,
    *,
    project_slug: str | None = None,
    active_since: _dt.datetime | None = None,
    interval_seconds: int = _WORKFLOW_HEARTBEAT_SECONDS,
) -> None:
    interval = max(15, int(interval_seconds or 60))
    while True:
        await asyncio.sleep(interval)
        try:
            await _touch_workflow_run_heartbeat(
                workflow_run_id,
                project_slug=project_slug,
                active_since=active_since,
            )
        except Exception:
            logger.debug(
                "Workflow heartbeat failed for run %s",
                workflow_run_id,
                exc_info=True,
            )


@asynccontextmanager
async def _workflow_db_heartbeat(
    workflow_run_id: str,
    *,
    project_slug: str | None = None,
) -> AsyncIterator[None]:
    """Keep long-running workflow rows fresh while a worker awaits LLM calls."""
    active_since = _dt.datetime.now(_dt.UTC)
    task = asyncio.create_task(
        _workflow_heartbeat_loop(
            workflow_run_id,
            project_slug=project_slug,
            active_since=active_since,
        )
    )
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def _generation_gate_block(exc: Exception) -> tuple[str, str] | None:
    """Return an exhausted-repair reason for deterministic planning gates.

    These failures are not Docker/worker crashes. They mean the generated
    planning foundation still failed after the pipeline's automatic repair
    loops, so the project should stop requeueing until the next diagnostic
    run. The returned ``reason`` slug is intentionally concrete (e.g.
    ``story_bible_gate_failed:antagonist_motive_overlap``) so the UI can
    surface the actionable cause in the task badge without unfolding the
    full error blob.
    """
    message = str(exc)

    # A judge outage is not evidence that the outline itself is defective.
    # Let the normal task retry/self-heal path retry the same immutable
    # planning input instead of launching an outline rewrite that can pollute
    # an otherwise valid plan during a transient provider failure.
    if (
        "COMMERCIAL_LLM_JUDGE_UNAVAILABLE" in message
        or "commercial llm judge unavailable" in message.lower()
    ):
        return None

    def _with_subcode(reason: str) -> tuple[str, str]:
        # Best-effort extraction of the first structured violation code
        # from the error blob. Adds it as a sub-slug so the UI / log
        # consumer can group failures by concrete cause.
        import re
        match = re.search(r"\[([A-Z_][A-Z_0-9]+)\]", message)
        if match:
            return f"{reason}:{match.group(1).lower()}", message
        return reason, message

    if "L2 bible gate failed" in message:
        return _with_subcode("story_bible_gate_failed")
    if "failed chapter-outline repair loop" in message:
        return _with_subcode("volume_outline_gate_failed")
    if "Progressive volume outline did not earn semantic promotion" in message:
        import re

        match = re.search(r"promotion;\s*([A-Z][A-Z_0-9]+)", message)
        reason = "outline_semantic_gate_failed"
        if match:
            reason = f"{reason}:{match.group(1).lower()}"
        return reason, message
    if (
        "Whole-book outline semantic gate failed" in message
        or "Whole-book outline semantic gate rejected promotion" in message
    ):
        return _with_subcode("outline_semantic_gate_failed")
    if "Commercial planning readiness gate failed" in message:
        return _with_subcode("commercial_planning_readiness_gate_failed")
    if "chapter_plan_contract failed" in message:
        # Structured violation codes live as ``PLAN_*`` tokens not in
        # brackets — fish out the first occurrence so the slug is
        # specific (e.g. ``volume_outline_gate_failed:plan_scene_unknown_participant``).
        import re
        match = re.search(r"\b(PLAN_[A-Z_]+)\b", message)
        if match:
            return (
                f"volume_outline_gate_failed:{match.group(1).lower()}",
                message,
            )
        return "volume_outline_gate_failed", message
    if "plan fingerprint gate" in message:
        return "volume_outline_gate_failed:plan_fingerprint", message
    if "Refusing to pad or trim" in message and "chapter_outline" in message:
        return "volume_outline_gate_failed:padding_refused", message
    if "Prewrite readiness gate failed" in message:
        return _with_subcode("prewrite_readiness_gate_failed")
    if "Reverse outline gate failed" in message:
        return _with_subcode("reverse_outline_gate_failed")
    if "blocked by plan-richness gate" in message:
        import re

        match = re.search(r"['\"]([a-z_][a-z_0-9]+)['\"]", message)
        if match:
            return f"scene_plan_richness_gate_failed:{match.group(1)}", message
        return "scene_plan_richness_gate_failed", message
    # write-safety gates (canon violations) surface here when the scene
    # pipeline gives up.
    if "blocked by write-safety gate" in message:
        import re
        # Codes look like [identity:pronoun_mismatch:major] / [contradiction:...:error]
        match = re.search(r"\[([a-z_]+:[a-z_]+):(?:critical|major|error)\]", message)
        if match:
            return f"write_safety_gate_failed:{match.group(1).replace(':', '_')}", message
        return "write_safety_gate_failed", message
    return None


def _result_payload_requires_attention(payload: dict[str, Any]) -> bool:
    if payload.get("requires_machine_repair") is True or payload.get("requires_human_review") is True:
        return True
    # A full-book run without a terminal export is never a clean completion,
    # even when the last consistency verdict happened to be ``pass``.  Keep it
    # in the autonomous closure lane so export/preflight failures are retried.
    if str(payload.get("export_status") or "").strip().lower() == "not_exported":
        return True
    verdict = (
        str(
            payload.get("final_verdict")
            or payload.get("verdict")
            or payload.get("status")
            or payload.get("export_status")
            or ""
        )
        .strip()
        .lower()
    )
    return verdict in _ATTENTION_VERDICTS


async def _emit_terminal_pipeline_event(
    reporter: RedisProgressReporter,
    payload: dict[str, Any],
    *,
    completed_result: str,
    attention_reason: str,
) -> None:
    event_payload = {"result": completed_result, **payload}
    if _result_payload_requires_attention(payload):
        await reporter.emit(
            "repairable_auto_continue_pending",
            {**event_payload, "reason": attention_reason},
            event_type="repairable_auto_continue_pending",
        )
        return
    await reporter.emit("completed", event_payload, event_type="completed")
    # 榜单对标闭环 P4.2：干净完成的书自动入队对标回归（advisory，永不影响成书）。
    await _enqueue_benchmark_regression_if_needed(reporter, payload)


def _benchmark_regression_job_id(slug: str) -> str:
    return f"benchmark-regression:{slug}"


async def _enqueue_benchmark_regression_if_needed(
    reporter: RedisProgressReporter,
    payload: dict[str, Any],
) -> bool:
    from bestseller.services.benchmark_regression import (
        auto_benchmark_regression_enabled,
    )

    if not auto_benchmark_regression_enabled():
        return False
    project_slug = str(payload.get("project_slug") or "").strip()
    if not project_slug:
        return False
    job_id = _benchmark_regression_job_id(project_slug)
    try:
        job = await reporter._redis.enqueue_job(
            "run_benchmark_regression_task",
            workflow_run_id=job_id,
            payload={"project_slug": project_slug},
            _job_id=job_id,
            _expires=_dt.timedelta(days=2),
        )
    except (AttributeError, OSError):
        return False
    if job is not None:
        await reporter.emit(
            "benchmark_regression_queued",
            {"project_slug": project_slug, "job_id": job_id},
            event_type="benchmark_regression_queued",
        )
    return True


async def run_benchmark_regression_task(
    ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Post-book benchmark arena vs 真书语料（advisory；榜单对标闭环 P4.2）。"""
    from bestseller.services.benchmark_regression import run_benchmark_regression
    from bestseller.settings import get_settings

    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)
    project_slug = str(payload.get("project_slug") or "").strip()
    await reporter.emit(
        "benchmark_regression_started",
        {"project_slug": project_slug},
        event_type="benchmark_regression_started",
    )
    try:
        report = await run_benchmark_regression(
            project_slug,
            output_base_dir=get_settings().output.base_dir,
        )
    except Exception as exc:
        logger.warning("benchmark regression failed for %s", project_slug, exc_info=True)
        await reporter.emit(
            "benchmark_regression_failed",
            {"project_slug": project_slug, "error": str(exc)},
            event_type="benchmark_regression_failed",
        )
        return {"project_slug": project_slug, "status": "failed"}
    if report is None:
        await reporter.emit(
            "benchmark_regression_skipped",
            {"project_slug": project_slug, "reason": "corpus/judge unavailable"},
            event_type="benchmark_regression_skipped",
        )
        return {"project_slug": project_slug, "status": "skipped"}
    await reporter.emit(
        "benchmark_regression_completed",
        {
            "project_slug": project_slug,
            "vs_t1_win_rate": report["summaries"]["t1"]["win_rate"],
            "vs_t2_win_rate": report["summaries"]["t2"]["win_rate"],
            "passed": report["evaluation"]["passed"],
        },
        event_type="benchmark_regression_completed",
    )
    return {"project_slug": project_slug, "status": "completed", "report": report}


def _quality_closure_job_id(slug: str) -> str:
    return f"quality-closure:heal:{slug}"


def _project_repair_job_id(slug: str) -> str:
    return f"repair:heal:{slug}"


def _outline_replan_job_id(slug: str) -> str:
    return f"outline-replan:heal:{slug}"


def _project_pipeline_job_id(slug: str) -> str:
    return f"project-pipeline:heal:{slug}"


def _auto_quality_closure_enabled() -> bool:
    return os.getenv(_AUTO_QUALITY_CLOSURE_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _result_payload_auto_closure_candidate(payload: dict[str, Any]) -> bool:
    if not _auto_quality_closure_enabled():
        return False
    if not str(payload.get("project_slug") or "").strip():
        return False
    if not _result_payload_requires_attention(payload):
        return False
    status = str(payload.get("status") or "").strip().lower()
    if status in {"blocked_generation_gate", "failed"}:
        return False
    return True


async def _enqueue_quality_closure_if_needed(
    redis: Any,
    reporter: RedisProgressReporter,
    payload: dict[str, Any],
    *,
    source: str,
) -> bool:
    if not _result_payload_auto_closure_candidate(payload):
        return False
    project_slug = str(payload.get("project_slug") or "").strip()
    job_id = _quality_closure_job_id(project_slug)
    closure_payload = {
        "project_slug": project_slug,
        "requested_by": source,
        "round_size": _DEFAULT_CLOSURE_ROUND_SIZE,
        "max_rounds": _DEFAULT_CLOSURE_MAX_ROUNDS,
        "replace_existing": False,
    }
    try:
        job = await redis.enqueue_job(
            "run_book_quality_closure_task",
            workflow_run_id=job_id,
            payload=closure_payload,
            _job_id=job_id,
            _expires=_dt.timedelta(days=7),
        )
    except AttributeError:
        return False
    if job is None:
        await reporter.emit(
            "quality_closure_already_queued",
            {"project_slug": project_slug, "job_id": job_id, "source": source},
            event_type="quality_closure_already_queued",
        )
        return True
    await reporter.emit(
        "repairable_auto_continue",
        {
            "project_slug": project_slug,
            "job_id": job_id,
            "source": source,
            "round_size": closure_payload["round_size"],
            "max_rounds": closure_payload["max_rounds"],
        },
        event_type="repairable_auto_continue",
    )
    return True


async def _enqueue_project_repair_if_needed(
    redis: Any,
    reporter: RedisProgressReporter,
    project_slug: str,
    *,
    source: str,
    reason: str,
    current_job_id: str | None = None,
) -> bool:
    project_slug = str(project_slug or "").strip()
    if not project_slug:
        return False
    job_id = _project_repair_job_id(project_slug)
    if current_job_id and job_id == current_job_id:
        await reporter.emit(
            "repairable_auto_continue_deferred",
            {
                "project_slug": project_slug,
                "job_id": job_id,
                "source": source,
                "reason": reason,
            },
            event_type="repairable_auto_continue_pending",
        )
        return False
    repair_payload = {
        "project_slug": project_slug,
        "requested_by": source,
        "include_pending_rewrite_tasks": True,
        "pending_rewrite_task_limit": 25,
        "scan_publication_gate_candidates": False,
    }
    async def _enqueue() -> Any:
        return await redis.enqueue_job(
            "run_project_repair_task",
            workflow_run_id=job_id,
            payload=repair_payload,
            _job_id=job_id,
            _expires=_dt.timedelta(days=7),
        )

    try:
        job = await _enqueue()
        if job is None:
            active = bool(
                await redis.exists(
                    f"arq:job:{job_id}",
                    f"arq:in-progress:{job_id}",
                    f"arq:retry:{job_id}",
                )
            )
            if not active:
                active = await redis.zscore("arq:queue", job_id) is not None
            if not active:
                await redis.delete(
                    f"arq:result:{job_id}",
                    f"arq:retry:{job_id}",
                )
                job = await _enqueue()
    except AttributeError:
        return False
    if job is None:
        await reporter.emit(
            "repairable_auto_continue_already_queued",
            {
                "project_slug": project_slug,
                "job_id": job_id,
                "source": source,
                "reason": reason,
            },
            event_type="repairable_auto_continue_already_queued",
        )
        return True
    await reporter.emit(
        "repairable_auto_continue",
        {
            "project_slug": project_slug,
            "job_id": job_id,
            "source": source,
            "reason": reason,
        },
        event_type="repairable_auto_continue",
    )
    return True


async def _enqueue_outline_replan_if_needed(
    redis: Any,
    reporter: RedisProgressReporter,
    project_slug: str,
    *,
    source: str,
    reason: str,
    current_job_id: str | None = None,
) -> bool:
    """Immediately hand an exhausted outline judge to its dedicated owner.

    Waiting for the periodic stale-project scan made a fresh semantic failure
    look dead even though the recovery lane existed.  The deterministic job id
    preserves single ownership; a terminal ARQ result is cleared only after no
    live job/in-progress/queue evidence remains.
    """

    project_slug = str(project_slug or "").strip()
    if not project_slug:
        return False
    job_id = _outline_replan_job_id(project_slug)
    if current_job_id and job_id == current_job_id:
        return False
    payload = {
        "project_slug": project_slug,
        "premise": None,
        "progress_fingerprint": f"{reason}:{source}",
    }

    async def _enqueue() -> Any:
        return await redis.enqueue_job(
            "run_outline_replan_task",
            workflow_run_id=job_id,
            payload=payload,
            _job_id=job_id,
            _expires=_dt.timedelta(days=7),
        )

    try:
        job = await _enqueue()
        if job is None:
            active = bool(
                await redis.exists(
                    f"arq:job:{job_id}",
                    f"arq:in-progress:{job_id}",
                    f"arq:retry:{job_id}",
                )
            )
            if not active:
                active = await redis.zscore("arq:queue", job_id) is not None
            if not active:
                await redis.delete(
                    f"arq:result:{job_id}",
                    f"arq:retry:{job_id}",
                )
                job = await _enqueue()
    except AttributeError:
        return False

    event = "repairable_auto_continue" if job is not None else "repairable_auto_continue_already_queued"
    await reporter.emit(
        event,
        {
            "project_slug": project_slug,
            "job_id": job_id,
            "source": source,
            "reason": reason,
            "recovery_kind": "outline_replan",
        },
        event_type=event,
    )
    return True


async def _enqueue_project_pipeline_if_needed(
    redis: Any,
    reporter: RedisProgressReporter,
    project_slug: str,
    *,
    source: str,
    reason: str,
    current_job_id: str | None = None,
) -> bool:
    """Immediately continue an approved outline into chapter writing.

    This lane is intentionally different from ``project_repair``: a project
    with approved planned chapters but zero drafts needs the generation
    pipeline, not a quality sweep over drafts that do not exist yet.
    """

    project_slug = str(project_slug or "").strip()
    if not project_slug:
        return False
    job_id = _project_pipeline_job_id(project_slug)
    if current_job_id and job_id == current_job_id:
        return False

    async def _enqueue() -> Any:
        return await redis.enqueue_job(
            "run_project_pipeline_task",
            workflow_run_id=job_id,
            payload={"project_slug": project_slug},
            _job_id=job_id,
            _expires=_dt.timedelta(days=7),
        )

    try:
        job = await _enqueue()
        if job is None:
            active = bool(
                await redis.exists(
                    f"arq:job:{job_id}",
                    f"arq:in-progress:{job_id}",
                    f"arq:retry:{job_id}",
                )
            )
            if not active:
                active = await redis.zscore("arq:queue", job_id) is not None
            if not active:
                await redis.delete(
                    f"arq:result:{job_id}",
                    f"arq:retry:{job_id}",
                )
                job = await _enqueue()
    except AttributeError:
        return False

    event = (
        "repairable_auto_continue"
        if job is not None
        else "repairable_auto_continue_already_queued"
    )
    await reporter.emit(
        event,
        {
            "project_slug": project_slug,
            "job_id": job_id,
            "source": source,
            "reason": reason,
            "recovery_kind": "project_pipeline",
        },
        event_type=event,
    )
    return True


def _load_closure_runner_module() -> Any:
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "run_book_quality_closure.py"
    spec = importlib.util.spec_from_file_location("bestseller_quality_closure_runner", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load quality closure runner at {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _outline_semantic_report_proves_promotion(
    semantic_report: Mapping[str, Any] | None,
    *,
    current_outline_version: int,
    prior_outline_version: int,
) -> bool:
    """Accept either a newer artifact or an explicitly adjudicated restore.

    The replan owner records the prior version from ``volume_chapter_outline``
    while the full-book path promotes a ``chapter_outline_batch``. Comparing
    those independent version sequences can make a newly approved batch look
    older. The semantic gate's all-volume adjudication is the authoritative
    proof at that boundary.
    """

    if not isinstance(semantic_report, Mapping):
        return False
    adjudication = semantic_report.get("llm_adjudication")
    adjudication = adjudication if isinstance(adjudication, Mapping) else {}
    return bool(
        semantic_report.get("promotion_allowed") is True
        and (
            current_outline_version > prior_outline_version
            or semantic_report.get("llm_adjudicated_all_volumes") is True
            or adjudication.get("restored_declared_gate_pass") is True
        )
    )


async def _mark_project_generation_repair_exhausted(
    project_slug: str,
    *,
    reason: str,
    error_message: str,
) -> None:
    """Persist generation-gate diagnostics without creating a manual stop state."""
    async with get_server_session() as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == project_slug)
        )
        if project is None:
            return

        now = _dt.datetime.now(_dt.UTC).isoformat()
        metadata = dict(project.metadata_json or {})
        for key in (
            "generation_resume_blocked_by_planning_gate",
            "generation_auto_repair_exhausted",
            "production_paused",
            "production_pause_reason",
        ):
            metadata.pop(key, None)
        metadata.update(
            {
                "generation_gate_auto_retry_needed": True,
                "last_generation_gate_reason": reason,
                "last_generation_gate_error": error_message[:4000],
                "last_generation_gate_blocked_at": now,
            }
        )
        project.metadata_json = metadata
        if (getattr(project, "status", None) or "").lower() == ProjectStatus.PAUSED.value:
            project.status = ProjectStatus.REVISING.value

        active_runs = list(
            await session.scalars(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.project_id == project.id,
                    WorkflowRunModel.status.in_(_ACTIVE_WORKFLOW_STATUSES),
                )
            )
        )
        for run in active_runs:
            run.status = WorkflowStatus.FAILED.value
            run.error_message = error_message[:4000]
            run.metadata_json = {
                **(run.metadata_json or {}),
                "generation_gate_auto_retry_needed": True,
                "repairable_auto_continue": True,
                "generation_gate_reason": reason,
                "generation_gate_blocked_at": now,
            }


async def _mark_project_outline_replan_required(
    project_slug: str,
    *,
    reason: str,
    error_message: str,
    settings: Any,
) -> bool:
    """Persist the semantic-gate state before queueing its replan owner."""

    async with get_server_session() as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == project_slug)
        )
        if project is None:
            return False
        now = _dt.datetime.now(_dt.UTC).isoformat()
        metadata = dict(project.metadata_json or {})
        max_attempts = int(getattr(settings, "outline_replan_auto_retry_max_attempts", 3) or 3)
        attempts = int(metadata.get("outline_replan_auto_retry_count") or 0)
        if attempts >= max_attempts:
            metadata.update(
                {
                    "outline_replan_required": False,
                    "outline_replan_auto_retry_exhausted": True,
                    "outline_replan_auto_retry_last_reason": reason,
                    "outline_replan_auto_retry_last_error": error_message[:4000],
                    "production_paused": True,
                    "production_pause_reason": "outline_replan_auto_retry_exhausted",
                    "requires_human_review": True,
                    "planning_status": "needs_replan",
                    "outline_semantic_gate_status": "needs_replan",
                }
            )
            project.metadata_json = metadata
            project.status = ProjectStatus.NEEDS_REPLAN.value
            return False

        attempts += 1
        metadata.pop("outline_replan_auto_retry_exhausted", None)
        metadata.update(
            {
                "planning_status": "needs_replan",
                "outline_semantic_gate_status": "needs_replan",
                "outline_replan_required": True,
                "production_paused": True,
                "production_pause_reason": "outline_semantic_gate_failed",
                "generation_resume_blocked_until_repair_audit": True,
                "outline_replan_last_error": error_message[:4000],
                "outline_replan_last_failed_at": now,
                "last_generation_gate_reason": reason,
                "outline_replan_auto_retry_count": attempts,
                "outline_replan_auto_retry_last_reason": reason,
                "outline_replan_auto_retry_last_error": error_message[:4000],
            }
        )
        project.metadata_json = metadata
        project.status = ProjectStatus.NEEDS_REPLAN.value
        return True


async def _mark_generation_gate_budget_exhausted(
    project_slug: str,
    *,
    reason: str,
    detail: str,
) -> None:
    """Park the book in a visible terminal state once its budget runs out.

    Deliberately clears ``generation_gate_auto_retry_needed`` so the self-heal
    scanner stops treating the book as a retry candidate, and sets a pause the
    operator can see and act on. The only ways out are human: fix the blocking
    gate, or explicitly restart the book (which re-opens a fresh chain).
    """

    async with get_server_session() as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == project_slug)
        )
        if project is None:
            return
        metadata = dict(project.metadata_json or {})
        metadata.pop("generation_gate_auto_retry_needed", None)
        metadata.update(
            {
                "production_paused": True,
                "production_pause_reason": "generation_gate_retry_budget_exhausted",
                "generation_gate_retry_budget_exhausted": True,
                "generation_gate_retry_budget_exhausted_reason": reason,
                "generation_gate_retry_budget_exhausted_detail": detail,
                "generation_gate_retry_budget_exhausted_at": _dt.datetime.now(
                    _dt.UTC
                ).isoformat(),
            }
        )
        project.metadata_json = metadata


async def _consume_generation_gate_retry_budget(
    project_slug: str,
    *,
    reason: str,
) -> RetryDecision | None:
    """Spend one unit of the gate's automatic-retry budget, or report exhaustion.

    The chain is persisted in ``projects.metadata`` — the same mapping that
    holds ``last_generation_gate_reason``, i.e. the state it guards — and is
    written *before* the retry is dispatched. Both properties are load-bearing:
    a counter kept somewhere else got purged independently (2026-07-25), and a
    counter written afterwards would be lost by a crash mid-attempt, in either
    case making the loop immortal.
    """

    from bestseller.services.retry_ledger import (
        RetryTrigger,
        evaluate_retry,
        generation_gate_budget,
        generation_gate_scope,
        load_chain,
        store_chain,
    )

    scope = generation_gate_scope(reason)
    try:
        async with get_server_session() as session:
            project = await session.scalar(
                select(ProjectModel).where(ProjectModel.slug == project_slug)
            )
            if project is None:
                return None
            metadata = dict(project.metadata_json or {})
            decision = evaluate_retry(
                load_chain(metadata, scope=scope),
                trigger=RetryTrigger.AUTO,
                budget=generation_gate_budget(),
                reason=reason,
            )
            if decision.allowed:
                metadata = store_chain(metadata, scope=scope, chain=decision.chain)
                metadata["generation_gate_retry_budget"] = decision.to_event_payload()
                project.metadata_json = metadata
            return decision
    except Exception:
        # Fail open on infrastructure errors — but say so, because a budget that
        # silently stops being enforced is how this class of bug returns.
        logger.warning(
            "could not evaluate generation gate retry budget for %s; "
            "allowing this attempt",
            project_slug,
            exc_info=True,
        )
        return None


async def _handle_generation_gate_auto_continue(
    redis: Any,
    reporter: RedisProgressReporter,
    project_slug: str,
    *,
    reason: str,
    message: str,
    source: str,
    current_job_id: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    await _mark_project_generation_repair_exhausted(
        project_slug,
        reason=reason,
        error_message=message,
    )

    budget_decision = await _consume_generation_gate_retry_budget(
        project_slug, reason=reason
    )
    if budget_decision is not None and budget_decision.exhausted:
        # Exhaustion is a terminal, visible state — never "try once more".
        await _mark_generation_gate_budget_exhausted(
            project_slug, reason=reason, detail=budget_decision.detail
        )
        payload = {
            "status": "generation_gate_budget_exhausted",
            "project_slug": project_slug,
            "reason": reason,
            "budget": budget_decision.to_event_payload(),
        }
        logger.warning(
            "%s for %s exhausted the generation-gate retry budget (%s); "
            "stopping automatic recovery and awaiting a human",
            source,
            project_slug,
            budget_decision.detail,
        )
        await reporter.emit(
            "generation_gate_budget_exhausted",
            payload,
            event_type="generation_gate_budget_exhausted",
        )
        return payload
    if reason.startswith(
        (
            "outline_semantic_gate_failed",
            "commercial_planning_readiness_gate_failed",
        )
    ):
        can_retry = await _mark_project_outline_replan_required(
            project_slug,
            reason=reason,
            error_message=message,
            settings=settings,
        )
        if can_retry:
            queued = await _enqueue_outline_replan_if_needed(
                redis,
                reporter,
                project_slug,
                source=source,
                reason=reason,
                current_job_id=current_job_id,
            )
        else:
            queued = False
    else:
        queued = await _enqueue_project_repair_if_needed(
            redis,
            reporter,
            project_slug,
            source=source,
            reason=reason,
            current_job_id=current_job_id,
        )
    if not queued:
        await reporter.emit(
            "repairable_auto_continue_pending",
            {
                "project_slug": project_slug,
                "reason": reason,
                "error": message,
            },
            event_type="repairable_auto_continue_pending",
        )
    logger.warning(
        "%s for %s hit generation gate; recorded diagnostics and kept auto-recovery active: %s",
        source,
        project_slug,
        reason,
    )
    return {
        "status": "generation_gate_auto_retry_pending",
        "project_slug": project_slug,
        "reason": reason,
        "repair_queued": queued,
    }


async def run_self_heal_task(ctx: dict[str, Any]) -> dict[str, Any]:
    """Periodic orphan reaper and stuck-project requeue."""
    from bestseller.worker.self_heal import heal_stuck_projects

    settings = get_settings()
    redis = ctx.get("redis")
    worker_id = f"{os.uname().nodename}:{os.getpid()}:periodic"
    dispatched = await heal_stuck_projects(
        settings,
        redis=redis,
        worker_id=worker_id,
    )
    return {"dispatched": dispatched}


def _apply_cancelled_outline_replan_state(
    project: ProjectModel,
    active_runs: list[WorkflowRunModel],
    *,
    error_message: str = "worker outline replan cancelled; safe retry required",
) -> None:
    """Close only the cancelled planner owner's rows and retain the hard gate."""

    now = _dt.datetime.now(_dt.UTC).isoformat()
    metadata = dict(project.metadata_json or {})
    metadata.pop("outline_replan_in_progress", None)
    metadata.pop("outline_replan_prior_outline_version", None)
    metadata.update(
        {
            "outline_replan_required": True,
            "planning_status": "needs_replan",
            "outline_semantic_gate_status": "needs_replan",
            "production_paused": True,
            "production_pause_reason": "outline_replan_cancelled_recoverable",
            "generation_resume_blocked_until_repair_audit": True,
            "outline_replan_last_failed_at": now,
            "outline_replan_last_error": error_message,
        }
    )
    project.metadata_json = metadata
    project.status = ProjectStatus.NEEDS_REPLAN.value
    for run in active_runs:
        run.status = WorkflowStatus.FAILED.value
        run.current_step = "cancelled_recoverable"
        run.error_message = error_message
        run.metadata_json = {
            **(run.metadata_json or {}),
            "cancelled_recoverable": True,
            "cancelled_at": now,
        }


async def _mark_cancelled_outline_replan_recoverable(project_slug: str) -> None:
    """Persist a fail-closed, retryable state before ARQ requeues cancellation."""

    async with get_server_session() as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == project_slug)
        )
        if project is None:
            return
        active_runs = list(
            await session.scalars(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.project_id == project.id,
                    WorkflowRunModel.requested_by == "worker_outline_replan",
                    WorkflowRunModel.status.in_(_ACTIVE_WORKFLOW_STATUSES),
                )
            )
        )
        _apply_cancelled_outline_replan_state(project, active_runs)
        await session.commit()


async def _recover_owned_outline_replan_conflict(
    project_slug: str,
    exc: PlanningConflictError,
    reporter: RedisProgressReporter,
) -> dict[str, Any]:
    """Close a prior dedicated-owner row that blocks its idempotent retry.

    The ARQ job id is unique per project, so a retry entering this handler
    cannot be a second legitimate outline owner.  The conflicting
    ``worker_outline_replan`` database row belongs to the interrupted prior
    attempt and must be closed before the next self-heal scan can requeue it.
    """

    await _mark_cancelled_outline_replan_recoverable(project_slug)
    failure = {
        "status": "outline_replan_retry_pending",
        "project_slug": project_slug,
        "reason": "owned_planning_conflict_recovered",
        "error": str(exc)[:4000],
    }
    await reporter.emit(
        "outline_replan_retry_pending",
        failure,
        event_type="repairable_auto_continue_pending",
    )
    return failure


async def run_outline_replan_task(
    ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Dedicated owner for a bounded ``needs_replan`` recovery.

    Normal prose and repair workers remain fail-closed.  This owner alone may
    enter the existing progressive pipeline with the structural-repair bypass;
    that pipeline still runs deterministic contract checks and the LLM outline
    judge before any prose step.  Failure restores ``needs_replan`` atomically,
    leaving the self-heal fingerprint budget to decide whether another attempt
    is useful.
    """

    from bestseller.domain.project import ProjectCreate, ProjectType
    from bestseller.services.pipelines import run_autowrite_pipeline
    from bestseller.services.projects import get_project_by_slug

    settings = get_settings()
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)
    set_ambient(make_sync_callback(reporter))
    project_slug = str(payload.get("project_slug") or "").strip()
    if not project_slug:
        raise ValueError("outline replan requires project_slug")

    archived = await _skip_archived_project_if_needed(reporter, project_slug)
    if archived is not None:
        return archived
    halted = await _skip_halted_project_if_needed(reporter, project_slug)
    if halted is not None:
        return halted

    async with get_server_session() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"Project '{project_slug}' not found")
        metadata = dict(project.metadata_json or {})
        _unlock_outline_replan_for_repair_revision(
            metadata,
            str(payload.get("repair_revision") or ""),
        )
        project.metadata_json = metadata
        if metadata.get("outline_replan_auto_retry_exhausted"):
            now_iso = _dt.datetime.now(_dt.UTC).isoformat()
            metadata = _outline_replan_retry_exhausted_metadata(
                metadata,
                attempts=_metadata_int_value(
                    metadata, "outline_replan_auto_retry_count", 0
                ),
                now_iso=now_iso,
                reason="outline_replan_auto_retry_exhausted",
            )
            project.status = ProjectStatus.PAUSED.value
            project.metadata_json = metadata
            await session.commit()
            blocked = {
                "status": "outline_replan_retry_exhausted",
                "project_slug": project_slug,
                "reason": "outline_replan_auto_retry_exhausted",
                "requires_human_review": True,
            }
            await reporter.emit(
                "outline_replan_retry_exhausted",
                blocked,
                event_type="repairable_auto_continue_pending",
            )
            logger.info(
                "outline replan skipped for %s because auto-retry was already exhausted",
                project_slug,
            )
            return blocked
        draft_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ChapterDraftVersionModel)
                .join(ChapterModel, ChapterModel.id == ChapterDraftVersionModel.chapter_id)
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
            )
            or 0
        )
        if draft_count:
            blocked = {
                "status": "skipped_outline_replan_after_prose",
                "project_slug": project_slug,
                "draft_count": draft_count,
                "reason": "outline_replan_requires_suffix_preserving_repair",
                "requires_machine_repair": True,
            }
            await reporter.emit(
                "repairable_auto_continue_pending",
                blocked,
                event_type="repairable_auto_continue_pending",
            )
            return blocked

        prior_outline_version = int(
            await session.scalar(
                select(func.max(PlanningArtifactVersionModel.version_no)).where(
                    PlanningArtifactVersionModel.project_id == project.id,
                    PlanningArtifactVersionModel.artifact_type
                    == "volume_chapter_outline",
                )
            )
            or 0
        )

        from bestseller.services.story_source import (
            load_accepted_project_premise,
            minimal_cost_source_contract_violations,
            repair_minimal_cost_source_contract,
        )

        premise = await load_accepted_project_premise(session, project)
        source_contract_requires_repair = bool(
            minimal_cost_source_contract_violations(project, premise)
        )
        force_foundation_replan = bool(
            payload.get("repair_foundation") or source_contract_requires_repair
        )
        if force_foundation_replan:
            from bestseller.domain.enums import ArtifactType
            from bestseller.domain.planning import PlanningArtifactCreate
            from bestseller.services.projects import import_planning_artifact

            repaired_premise, source_repair = await repair_minimal_cost_source_contract(
                session,
                settings,
                project,
                premise,
                repair_revision=str(payload.get("repair_revision") or ""),
                workflow_run_id=_coerce_workflow_run_uuid(workflow_run_id),
            )
            if repaired_premise != premise:
                premise = repaired_premise
                await import_planning_artifact(
                    session,
                    project.slug,
                    PlanningArtifactCreate(
                        artifact_type=ArtifactType.PREMISE,
                        content={"premise": premise},
                        notes=(
                            "creation contract repair: minimal-cost source conflict; "
                            f"revision={source_repair.get('repair_revision')}"
                        ),
                    ),
                )
                await reporter.emit(
                    "source_contract_repaired",
                    {
                        "project_slug": project.slug,
                        "repair_revision": source_repair.get("repair_revision"),
                        "changed_active_paths": source_repair.get(
                            "changed_active_paths", []
                        ),
                        "requires_foundation_replan": True,
                    },
                )
        _reconcile_outline_replan_creation_identity(project, premise)
        metadata = dict(project.metadata_json or {})

        previous_gate = {
            "status": project.status,
            "planning_status": metadata.get("planning_status"),
            "outline_semantic_gate_status": metadata.get(
                "outline_semantic_gate_status"
            ),
            "production_pause_reason": metadata.get("production_pause_reason"),
            "fingerprint": payload.get("progress_fingerprint"),
        }
        metadata.update(
            {
                "outline_replan_in_progress": True,
                "outline_replan_started_at": _dt.datetime.now(_dt.UTC).isoformat(),
                "outline_replan_source_gate": previous_gate,
                "outline_replan_prior_outline_version": prior_outline_version,
                "planning_status": "replanning",
                "production_paused": True,
                "production_pause_reason": "outline_replan_in_progress",
                "generation_resume_blocked_until_repair_audit": True,
            }
        )
        project.metadata_json = metadata
        project.status = ProjectStatus.PLANNING.value
        project_payload = ProjectCreate(
            slug=project.slug,
            title=project.title,
            genre=project.genre,
            sub_genre=project.sub_genre,
            audience=project.audience,
            target_word_count=project.target_word_count,
            target_chapters=project.target_chapters,
            project_type=ProjectType(project.project_type),
            metadata=dict(metadata),
        )
        await session.commit()

    await reporter.emit(
        "outline_replan_started",
        {
            "project_slug": project_slug,
            "workflow_run_id": workflow_run_id,
            "progress_fingerprint": payload.get("progress_fingerprint"),
        },
    )

    try:
        async with _workflow_db_heartbeat(workflow_run_id, project_slug=project_slug):
            async with get_server_session() as session:
                result = await run_autowrite_pipeline(
                    session=session,
                    settings=settings,
                    project_payload=project_payload,
                    premise=premise,
                    requested_by="worker_outline_replan",
                    progress=make_sync_callback(reporter),
                    allow_outline_replan=True,
                    force_foundation_replan=force_foundation_replan,
                )
    except asyncio.CancelledError:
        # ARQ requeues a cancelled job during rolling deployment, but the
        # planning workflow row created inside ``run_autowrite_pipeline`` is
        # independently committed. Leaving it ``running`` makes the retried
        # job collide with its own orphan via PlanningConflictError.
        await _mark_cancelled_outline_replan_recoverable(project_slug)
        raise
    except PlanningConflictError as exc:
        # A replacement worker may inherit the ARQ retry after the prior
        # process died before its independently committed planning row closed.
        # Treat that row as the interrupted attempt owned by this same
        # idempotent project job, close it fail-closed, and let self-heal retry.
        logger.warning(
            "outline replan recovered owned planning conflict for %s: %s",
            project_slug,
            exc,
        )
        return await _recover_owned_outline_replan_conflict(
            project_slug,
            exc,
            reporter,
        )
    except Exception as exc:  # keep the project blocked; scanner owns retries
        replan_gate_released = False
        async with get_server_session() as session:
            project = await get_project_by_slug(session, project_slug)
            if project is not None:
                metadata = dict(project.metadata_json or {})
                replan_gate_released = not bool(
                    metadata.get("outline_replan_in_progress")
                )
                if replan_gate_released:
                    for key in (
                        "outline_replan_required",
                        "generation_resume_blocked_until_repair_audit",
                        "production_paused",
                        "production_pause_reason",
                    ):
                        metadata.pop(key, None)
                    metadata.update(
                        {
                            "outline_replan_downstream_retry_pending": True,
                            "outline_replan_downstream_error": str(exc)[:4000],
                            "planning_status": "writing",
                            "outline_semantic_gate_status": "approved",
                        }
                    )
                    if project.status in {
                        ProjectStatus.PLANNING.value,
                        ProjectStatus.NEEDS_REPLAN.value,
                        ProjectStatus.PAUSED.value,
                    }:
                        project.status = ProjectStatus.WRITING.value
                else:
                    now_iso = _dt.datetime.now(_dt.UTC).isoformat()
                    metadata.pop("outline_replan_in_progress", None)
                    metadata.pop("outline_replan_prior_outline_version", None)
                    metadata["outline_replan_last_failed_at"] = now_iso
                    metadata["outline_replan_last_error"] = str(exc)[:4000]
                    status = ProjectStatus.NEEDS_REPLAN.value
                    attempts = _metadata_int_value(
                        metadata, "outline_replan_retry_attempts", 0
                    ) + 1
                    if attempts >= MAX_OUTLINE_REPLAN_ATTEMPTS:
                        metadata = _outline_replan_retry_exhausted_metadata(
                            metadata,
                            attempts=attempts,
                            now_iso=now_iso,
                            reason=str(exc),
                        )
                        status = ProjectStatus.PAUSED.value
                    else:
                        metadata, _ = _outline_replan_retry_pending_metadata(
                            metadata,
                            attempts=attempts,
                            now_iso=now_iso,
                            reason=str(exc),
                        )
                    if not metadata.get("outline_replan_retry_exhausted"):
                        metadata["outline_replan_last_retry_attempt"] = attempts
                    project.status = status
                project.metadata_json = metadata
                await session.commit()
        if replan_gate_released:
            queued = await _enqueue_project_pipeline_if_needed(
                redis,
                reporter,
                project_slug,
                source="outline_replan_downstream",
                reason="outline_replan_completed_downstream_failure",
                current_job_id=workflow_run_id,
            )
            failure = {
                "status": "outline_replan_completed_downstream_retry_pending",
                "project_slug": project_slug,
                "error": str(exc),
                "repair_queued": queued,
            }
            await reporter.emit(
                "outline_replan_completed_downstream_retry_pending",
                failure,
                event_type="repairable_auto_continue_pending",
            )
            logger.warning(
                "outline replan passed for %s but downstream writing failed; "
                "ordinary self-heal remains enabled: %s",
                project_slug,
                exc,
            )
            return failure
        failure = {
            "status": "outline_replan_retry_pending",
            "project_slug": project_slug,
            "error": str(exc),
        }
        await reporter.emit(
            "outline_replan_retry_pending",
            failure,
            event_type="repairable_auto_continue_pending",
        )
        logger.warning("outline replan failed for %s: %s", project_slug, exc)
        return failure

    async with get_server_session() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is not None:
            metadata = dict(project.metadata_json or {})
            semantic_report = metadata.get("outline_semantic_gate_report")
            current_outline_version = int(
                await session.scalar(
                    select(func.max(PlanningArtifactVersionModel.version_no)).where(
                        PlanningArtifactVersionModel.project_id == project.id,
                        PlanningArtifactVersionModel.artifact_type
                        == "volume_chapter_outline",
                    )
                )
                or 0
            )
            semantic_promoted = _outline_semantic_report_proves_promotion(
                semantic_report,
                current_outline_version=current_outline_version,
                prior_outline_version=prior_outline_version,
            )
            if not semantic_promoted:
                now_iso = _dt.datetime.now(_dt.UTC).isoformat()
                attempts = _metadata_int_value(
                    metadata, "outline_replan_retry_attempts", 0
                ) + 1
                status = ProjectStatus.NEEDS_REPLAN.value
                metadata.pop("outline_replan_in_progress", None)
                if attempts >= MAX_OUTLINE_REPLAN_ATTEMPTS:
                    _outline_replan_retry_exhausted_metadata(
                        metadata,
                        attempts=attempts,
                        now_iso=now_iso,
                        reason="replan pipeline returned without a new approved outline",
                    )
                    status = ProjectStatus.PAUSED.value
                else:
                    metadata["outline_replan_last_error"] = (
                        "replan pipeline returned without a new approved outline"
                    )
                    metadata, _ = _outline_replan_retry_pending_metadata(
                        metadata,
                        attempts=attempts,
                        now_iso=now_iso,
                        reason="replan pipeline returned without a new approved outline",
                    )
                if not metadata.get("outline_replan_retry_exhausted"):
                    metadata["outline_replan_last_retry_attempt"] = attempts
                project.metadata_json = metadata
                project.status = status
                await session.commit()
                failure = {
                    "status": "outline_replan_retry_pending",
                    "project_slug": project_slug,
                    "error": "new approved outline missing",
                }
                await reporter.emit(
                    "outline_replan_retry_pending",
                    failure,
                    event_type="repairable_auto_continue_pending",
                )
                return failure
            for key in (
                "outline_replan_in_progress",
                "outline_replan_prior_outline_version",
                "outline_replan_required",
                "outline_replan_retry_exhausted",
                "outline_replan_retry_attempts",
                "outline_replan_auto_retry_exhausted",
                "outline_replan_auto_retry_count",
                "outline_replan_auto_retry_last_reason",
                "outline_replan_auto_retry_last_error",
                "outline_replan_next_retry_at",
                "generation_resume_blocked_until_repair_audit",
                "production_paused",
                "outline_replan_downstream_retry_pending",
                "outline_replan_downstream_error",
                "outline_replan_last_error",
                "outline_replan_last_failed_at",
                "outline_replan_last_retry_attempt",
                "self_heal_abandoned",
                "self_heal_abandoned_at",
                "self_heal_abandoned_progress_fingerprint",
                "self_heal_abandoned_progress_rank",
                "self_heal_no_actionable_progress",
                "self_heal_no_progress_escalated",
                "self_heal_no_progress_escalated_at",
                "self_heal_no_progress_giveup",
                "self_heal_no_progress_machine_repair",
                "self_heal_last_progress_fingerprint",
                "self_heal_last_progress_rank",
                "self_heal_last_chapters_total",
            ):
                metadata.pop(key, None)
            _clear_successful_outline_replan_blockers(metadata)
            metadata.update(
                {
                    "outline_replan_completed_at": _dt.datetime.now(
                        _dt.UTC
                    ).isoformat(),
                    "planning_status": "writing",
                    "outline_semantic_gate_status": "approved",
                    "self_heal_no_progress_attempts": 0,
                }
            )
            if force_foundation_replan:
                metadata["source_contract_repair_status"] = "completed"
                metadata["source_contract_repair_completed_at"] = _dt.datetime.now(
                    _dt.UTC
                ).isoformat()
            project.metadata_json = metadata
            if project.status in {
                ProjectStatus.PLANNING.value,
                ProjectStatus.NEEDS_REPLAN.value,
                ProjectStatus.PAUSED.value,
            }:
                project.status = ProjectStatus.WRITING.value
            await session.commit()

    writing_queued = await _enqueue_project_pipeline_if_needed(
        redis,
        reporter,
        project_slug,
        source="outline_replan_completed",
        reason="approved_outline_ready_for_writing",
        current_job_id=workflow_run_id,
    )
    result_payload = {
        **result.model_dump(mode="json"),
        "writing_queued": writing_queued,
    }
    await reporter.emit(
        "outline_replan_completed", result_payload, event_type="completed"
    )
    return result_payload


async def run_autowrite_task(
    ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Full end-to-end autowrite pipeline.

    Expects payload: {"project_slug": str, "premise": str | None}
    The project must already exist in the DB (created via POST /api/v1/projects).
    """
    from bestseller.domain.project import ProjectCreate, ProjectType
    from bestseller.services.pipelines import run_autowrite_pipeline
    from bestseller.services.projects import get_project_by_slug

    settings = get_settings()
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)
    # Bind ambient emitter so deep gate/agent emits reach this job's Redis sink.
    # ARQ runs each job in its own copied context, so no paired reset is needed.
    set_ambient(make_sync_callback(reporter))

    project_slug = payload["project_slug"]
    archived = await _skip_archived_project_if_needed(reporter, project_slug)
    if archived is not None:
        return archived
    halted = await _skip_halted_project_if_needed(reporter, project_slug)
    if halted is not None:
        return halted
    replan_block = await _skip_outline_replan_project_if_needed(
        reporter, project_slug, workflow_run_id
    )
    if replan_block is not None:
        return replan_block

    _live_state = await _acquire_pipeline_live(redis, project_slug, workflow_run_id)
    if _live_state == "held_by_other":
        payload_skip = _duplicate_pipeline_skip_payload(project_slug, workflow_run_id)
        logger.warning(
            "autowrite for %s skipped: another live project pipeline owns the book",
            project_slug,
        )
        await reporter.emit("autowrite_skipped_duplicate", payload_skip)
        return payload_skip

    await reporter.emit(
        "autowrite_started",
        {
            "project_slug": project_slug,
            "workflow_run_id": workflow_run_id,
            "status": "started",
        },
    )

    _project_id: Any = None
    async with (
        _pipeline_live_guard(redis, project_slug, workflow_run_id, _live_state),
        _workflow_db_heartbeat(workflow_run_id, project_slug=project_slug),
    ):
        try:
            async with get_server_session() as session:
                # Load existing project to build ProjectCreate payload
                project = await get_project_by_slug(session, project_slug)
                if project is None:
                    raise ValueError(f"Project '{project_slug}' not found")
                _project_id = getattr(project, "id", None)

                # Build ProjectCreate from the existing project record
                meta = project.metadata_json or {}
                if (
                    str(workflow_run_id).startswith("autowrite:heal:")
                    and isinstance(meta, dict)
                    and meta.get("self_heal_suppressed")
                ):
                    await reporter.emit(
                        "framework_owned_self_heal_skipped",
                        {
                            "project_slug": project_slug,
                            "workflow_run_id": workflow_run_id,
                            "reason": meta.get("self_heal_suppressed_reason")
                            or "self_heal_suppressed",
                        },
                        event_type="completed",
                    )
                    return {
                        "status": "skipped_framework_owned",
                        "project_slug": project_slug,
                        "workflow_run_id": workflow_run_id,
                    }
                project_payload = ProjectCreate(
                    slug=project.slug,
                    title=project.title,
                    genre=project.genre,
                    sub_genre=project.sub_genre,
                    audience=project.audience,
                    target_word_count=project.target_word_count,
                    target_chapters=project.target_chapters,
                    project_type=ProjectType(project.project_type),
                    metadata=dict(meta),
                )

                from bestseller.services.story_source import load_accepted_project_premise

                premise = await load_accepted_project_premise(session, project)

                if project_payload.project_type == ProjectType.FANQIE_SHORT:
                    from bestseller.services.fanqie_short_pipeline import (
                        run_fanqie_short_pipeline,
                    )

                    result = await run_fanqie_short_pipeline(
                        session=session,
                        settings=settings,
                        project_payload=project_payload,
                        premise=premise,
                        progress=make_sync_callback(reporter),
                    )
                else:
                    result = await run_autowrite_pipeline(
                        session=session,
                        settings=settings,
                        project_payload=project_payload,
                        premise=premise,
                        progress=make_sync_callback(reporter),
                    )
        except Exception as exc:
            gate_block = _generation_gate_block(exc)
            if gate_block is not None:
                reason, message = gate_block
                return await _handle_generation_gate_auto_continue(
                    redis,
                    reporter,
                    project_slug,
                    reason=reason,
                    message=message,
                    source="autowrite",
                    current_job_id=workflow_run_id,
                )
            await reporter.emit("failed", {"error": str(exc)}, event_type="failed")
            raise

    result_payload = result.model_dump(mode="json")
    # The run has finished (rows materialized); the start reservation is no
    # longer protecting anything and must not 409 legitimate re-triggers of a
    # short run. Held until here so a second request during the run cannot slip
    # through the enqueue→row window.
    await _clear_pipeline_start_marker(redis, _project_id)
    if await _enqueue_quality_closure_if_needed(
        redis,
        reporter,
        result_payload,
        source="autowrite",
    ):
        return result_payload
    await _emit_terminal_pipeline_event(
        reporter,
        result_payload,
        completed_result="autowrite_done",
        attention_reason="autowrite_requires_attention",
    )
    return result_payload


async def run_project_pipeline_task(
    ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Project-level pipeline (draft all chapters)."""
    from bestseller.services import pipelines as pipeline_services
    from bestseller.services.projects import get_project_by_slug
    from bestseller.services.truth_version import TruthVersionStaleError

    settings = get_settings()
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)
    # Bind ambient emitter so deep gate/agent emits reach this job's Redis sink.
    # ARQ runs each job in its own copied context, so no paired reset is needed.
    set_ambient(make_sync_callback(reporter))
    project_slug = payload["project_slug"]
    archived = await _skip_archived_project_if_needed(reporter, project_slug)
    if archived is not None:
        return archived
    halted = await _skip_halted_project_if_needed(reporter, project_slug)
    if halted is not None:
        return halted
    replan_block = await _skip_outline_replan_project_if_needed(
        reporter, project_slug, workflow_run_id
    )
    if replan_block is not None:
        return replan_block

    _live_state = await _acquire_pipeline_live(redis, project_slug, workflow_run_id)
    if _live_state == "held_by_other":
        payload_skip = _duplicate_pipeline_skip_payload(project_slug, workflow_run_id)
        logger.warning(
            "project pipeline for %s skipped: another live pipeline owns the book",
            project_slug,
        )
        await reporter.emit("project_pipeline_skipped_duplicate", payload_skip)
        return payload_skip

    await reporter.emit(
        "project_pipeline_started",
        {
            "project_slug": project_slug,
            "workflow_run_id": workflow_run_id,
            "status": "started",
        },
    )

    _project_id: Any = None
    async with (
        _pipeline_live_guard(redis, project_slug, workflow_run_id, _live_state),
        _workflow_db_heartbeat(workflow_run_id, project_slug=project_slug),
    ):
        try:
            async with get_server_session() as session:
                if str(workflow_run_id).startswith("project-pipeline:heal:"):
                    project = await get_project_by_slug(session, project_slug)
                    project_meta = (
                        getattr(project, "metadata_json", None)
                        if project is not None
                        and isinstance(getattr(project, "metadata_json", None), dict)
                        else {}
                    )
                    if project_meta.get("self_heal_suppressed"):
                        await reporter.emit(
                            "framework_owned_self_heal_skipped",
                            {
                                "project_slug": project_slug,
                                "workflow_run_id": workflow_run_id,
                                "reason": project_meta.get(
                                    "self_heal_suppressed_reason"
                                )
                                or "self_heal_suppressed",
                            },
                            event_type="completed",
                        )
                        return {
                            "status": "skipped_framework_owned",
                            "project_slug": project_slug,
                            "workflow_run_id": workflow_run_id,
                        }
                try:
                    _proj = await get_project_by_slug(session, project_slug)
                    if _proj is not None:
                        _project_id = getattr(_proj, "id", None)
                except Exception:
                    logger.debug("project lookup for marker clear skipped", exc_info=True)
                result = await pipeline_services.run_project_pipeline(
                    session=session,
                    settings=settings,
                    project_slug=project_slug,
                    chapter_first=payload.get("chapter_first"),
                    stop_on_chapter_failure=bool(payload.get("stop_on_chapter_failure", False)),
                    progress=make_sync_callback(reporter),
                )
        except TruthVersionStaleError:
            async with get_server_session() as session:
                project = await get_project_by_slug(session, project_slug)
                if project is None:
                    raise ValueError(f"Project '{project_slug}' was not found.") from None
                await pipeline_services._refresh_stale_truth_materializations_for_resume(
                    session,
                    settings,
                    project,
                    requested_by="worker_truth_refresh",
                    progress=make_sync_callback(reporter),
                )
                result = await pipeline_services.run_project_pipeline(
                    session=session,
                    settings=settings,
                    project_slug=project_slug,
                    chapter_first=payload.get("chapter_first"),
                    stop_on_chapter_failure=bool(payload.get("stop_on_chapter_failure", False)),
                    progress=make_sync_callback(reporter),
                )
        except Exception as exc:
            gate_block = _generation_gate_block(exc)
            if gate_block is not None:
                reason, message = gate_block
                return await _handle_generation_gate_auto_continue(
                    redis,
                    reporter,
                    project_slug,
                    reason=reason,
                    message=message,
                    source="project_pipeline",
                    current_job_id=workflow_run_id,
                )
            await reporter.emit("failed", {"error": str(exc)}, event_type="failed")
            raise

    result_payload = result.model_dump(mode="json")
    await _clear_pipeline_start_marker(redis, _project_id)
    if await _enqueue_quality_closure_if_needed(
        redis,
        reporter,
        result_payload,
        source="project_pipeline",
    ):
        return result_payload
    await _emit_terminal_pipeline_event(
        reporter,
        result_payload,
        completed_result="project_pipeline_done",
        attention_reason="project_pipeline_requires_attention",
    )
    return result_payload


def _is_postgres_deadlock(exc: BaseException) -> bool:
    """True if ``exc`` wraps a Postgres deadlock (SQLSTATE 40P01)."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return str(sqlstate) == "40P01"


async def _run_with_deadlock_retry(
    operation: Callable[[], Awaitable[Any]],
    *,
    description: str,
    max_attempts: int = 3,
) -> Any:
    """Retry a self-session operation on Postgres deadlock (40P01).

    Concurrent chapter_pipeline + project_repair on the same project both UPDATE
    workflow_runs rows and insert workflow_step_runs child rows (taking a FK lock
    on the parent), and can acquire those locks in opposite order → Postgres
    kills one transaction with DeadlockDetectedError. A deadlock rolls the whole
    transaction back, so re-running ``operation`` from a fresh session is safe.
    The retry MUST wrap the session-opening call (not live inside it): the aborted
    session cannot be reused, so ``operation`` reopens its own session each try.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except DBAPIError as exc:
            if not _is_postgres_deadlock(exc) or attempt >= max_attempts:
                raise
            delay = min(0.25 * (2 ** (attempt - 1)), 2.0) + random.uniform(0, 0.25)
            logger.warning(
                "%s hit a Postgres deadlock (attempt %d/%d); retrying in %.2fs",
                description,
                attempt,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)


async def run_chapter_pipeline_task(
    ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Single chapter pipeline with progress reporting.

    Forwards the ARQ-level ``reporter`` into the chapter pipeline as a sync
    ``ProgressCallback`` (built via :func:`make_sync_callback`) so the inner
    scene-level emits surface in the SSE stream consumed by the Web UI.
    """
    from bestseller.services.pipelines import run_chapter_pipeline
    from bestseller.services.projects import get_project_by_slug

    settings = get_settings()
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)
    # Bind ambient emitter so deep gate/agent emits reach this job's Redis sink.
    # ARQ runs each job in its own copied context, so no paired reset is needed.
    set_ambient(make_sync_callback(reporter))
    project_slug = payload["project_slug"]

    archived = await _skip_archived_project_if_needed(reporter, project_slug)
    if archived is not None:
        return archived
    halted = await _skip_halted_project_if_needed(reporter, project_slug)
    if halted is not None:
        return halted
    replan_block = await _skip_outline_replan_project_if_needed(
        reporter, project_slug, workflow_run_id
    )
    if replan_block is not None:
        return replan_block

    await reporter.emit("started", {"chapter_number": payload["chapter_number"]})

    _project_id: Any = None
    async with _workflow_db_heartbeat(
        workflow_run_id,
        project_slug=project_slug,
    ):
        async def _chapter_op() -> Any:
            async with get_server_session() as session:
                nonlocal _project_id
                try:
                    _proj = await get_project_by_slug(session, project_slug)
                    if _proj is not None:
                        _project_id = getattr(_proj, "id", None)
                except Exception:
                    logger.debug("project lookup for marker clear skipped", exc_info=True)
                return await run_chapter_pipeline(
                    session=session,
                    settings=settings,
                    project_slug=project_slug,
                    chapter_number=payload["chapter_number"],
                    chapter_first=payload.get("chapter_first"),
                    progress=make_sync_callback(reporter),
                )

        try:
            result = await _run_with_deadlock_retry(
                _chapter_op,
                description=f"chapter pipeline (ch{payload['chapter_number']} of {project_slug})",
            )
        except Exception as exc:
            await reporter.emit("failed", {"error": str(exc)}, event_type="failed")
            raise

    result_payload = result.model_dump(mode="json")
    await _clear_pipeline_start_marker(redis, _project_id)
    await _emit_terminal_pipeline_event(
        reporter,
        result_payload,
        completed_result="chapter_pipeline_done",
        attention_reason="chapter_pipeline_requires_attention",
    )
    return result_payload


async def run_project_repair_task(
    ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Project repair pipeline for self-heal and queued repair jobs."""
    from bestseller.services.chapter_block_recovery import (
        summarize_block_recovery,
        sweep_recoverable_blocks,
    )
    from bestseller.services.projects import get_project_by_slug
    from bestseller.services.repair import run_project_repair

    settings = get_settings()
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)
    # Bind ambient emitter so deep gate/agent emits reach this job's Redis sink.
    # ARQ runs each job in its own copied context, so no paired reset is needed.
    set_ambient(make_sync_callback(reporter))

    project_slug = payload["project_slug"]
    archived = await _skip_archived_project_if_needed(reporter, project_slug)
    if archived is not None:
        return archived
    halted = await _skip_halted_project_if_needed(reporter, project_slug)
    if halted is not None:
        return halted
    replan_block = await _skip_outline_replan_project_if_needed(
        reporter, project_slug, workflow_run_id
    )
    if replan_block is not None:
        return replan_block

    await reporter.emit("project_repair_started", {"project_slug": project_slug})

    async with _workflow_db_heartbeat(workflow_run_id, project_slug=project_slug):
        try:
            # ── Release stale production blocks first (LLM-free) ───────────
            # A chapter repaired in an earlier pass can re-pass its quality
            # report yet keep a stale ``production_state == "blocked"`` flag.
            # The only sweep that cleared it used to live solely in the
            # end-of-book closure, so with sequential completion a single
            # stale-blocked early chapter deadlocked every later chapter for
            # the whole run. Sweep here (conservative: only release chapters
            # with a CLEAN report on record) so re-passing chapters are
            # released as soon as self-heal repairs the project.
            try:
                async with get_server_session() as sweep_session:
                    project = await get_project_by_slug(sweep_session, project_slug)
                    if project is not None:
                        reports = await sweep_recoverable_blocks(
                            sweep_session, project, require_clean_report=True
                        )
                        if reports:
                            summary = summarize_block_recovery(reports)
                            await sweep_session.commit()
                            if summary.get("recovered"):
                                await reporter.emit(
                                    "project_repair_released_stale_blocks",
                                    {
                                        "project_slug": project_slug,
                                        "recovered": summary.get("recovered", 0),
                                        "recovered_chapters": summary.get(
                                            "recovered_chapters", []
                                        ),
                                    },
                                )
            except Exception:
                logger.exception(
                    "stale-block sweep failed for %s; continuing to repair",
                    project_slug,
                )

            async def _repair_op() -> Any:
                async with get_server_session() as session:
                    return await run_project_repair(
                        session=session,
                        settings=settings,
                        project_slug=project_slug,
                        requested_by=str(payload.get("requested_by") or "worker_self_heal"),
                        refresh_impacts=bool(payload.get("refresh_impacts", True)),
                        export_markdown=bool(payload.get("export_markdown", True)),
                        include_pending_rewrite_tasks=bool(
                            payload.get("include_pending_rewrite_tasks", True)
                        ),
                        pending_rewrite_task_limit=int(
                            payload.get("pending_rewrite_task_limit")
                            or payload.get("round_size")
                            or 10
                        ),
                        scan_publication_gate_candidates=bool(
                            payload.get("scan_publication_gate_candidates", False)
                        ),
                        progress=make_sync_callback(reporter),
                    )

            result = await _run_with_deadlock_retry(
                _repair_op,
                description=f"project repair ({project_slug})",
            )
        except Exception as exc:
            gate_block = _generation_gate_block(exc)
            if gate_block is not None:
                reason, message = gate_block
                return await _handle_generation_gate_auto_continue(
                    redis,
                    reporter,
                    project_slug,
                    reason=reason,
                    message=message,
                    source="project_repair",
                    current_job_id=workflow_run_id,
                )
            await reporter.emit("failed", {"error": str(exc)}, event_type="failed")
            raise

    result_payload = result.model_dump(mode="json")
    result_payload.setdefault("project_slug", project_slug)
    if await _enqueue_quality_closure_if_needed(
        redis,
        reporter,
        result_payload,
        source="project_repair",
    ):
        return result_payload

    if result.requires_human_review:
        await reporter.emit(
            "repairable_auto_continue_pending",
            {
                "project_slug": project_slug,
                "reason": "project_repair_requires_attention",
                "workflow_run_id": str(result.workflow_run_id),
            },
            event_type="repairable_auto_continue_pending",
        )
    else:
        await reporter.emit("completed", {"result": "project_repair_done"})
    return result_payload


async def run_book_quality_closure_task(
    ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Run whole-book acceptance closure after generation finishes repairable."""
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)
    # Bind ambient emitter so deep gate/agent emits reach this job's Redis sink.
    # ARQ runs each job in its own copied context, so no paired reset is needed.
    set_ambient(make_sync_callback(reporter))
    project_slug = str(payload["project_slug"])
    round_size = max(int(payload.get("round_size") or _DEFAULT_CLOSURE_ROUND_SIZE), 1)
    max_rounds = max(int(payload.get("max_rounds") or _DEFAULT_CLOSURE_MAX_ROUNDS), 1)

    await reporter.emit(
        "book_quality_closure_started",
        {
            "project_slug": project_slug,
            "workflow_run_id": workflow_run_id,
            "round_size": round_size,
            "max_rounds": max_rounds,
            "requested_by": str(payload.get("requested_by") or "worker"),
        },
        event_type="book_quality_closure_started",
    )

    async with _workflow_db_heartbeat(workflow_run_id, project_slug=project_slug):
        try:
            runner = _load_closure_runner_module()
            result = await runner._run(
                Namespace(
                    slug=project_slug,
                    all=False,
                    platform=str(payload.get("platform") or "framework"),
                    priority=str(payload.get("priority") or "critical,high"),
                    round_size=round_size,
                    continuation_size=int(payload.get("continuation_size") or 0),
                    max_rounds=max_rounds,
                    preflight_timeout=float(payload.get("preflight_timeout") or 45.0),
                    repair_task_timeout=float(payload.get("repair_task_timeout") or 420.0),
                    continuation_timeout=float(payload.get("continuation_timeout") or 600.0),
                    max_books=0,
                    include_verify=False,
                    replace_existing=bool(payload.get("replace_existing", False)),
                    execute=True,
                    dry_run=False,
                    json=True,
                )
            )
        except Exception as exc:
            await reporter.emit(
                "book_quality_closure_failed",
                {"project_slug": project_slug, "error": str(exc)},
                event_type="failed",
            )
            raise

    reports = list(result.get("reports") or []) if isinstance(result, dict) else []
    report = reports[0] if reports and isinstance(reports[0], dict) else {}
    status = str(report.get("status") or "")
    next_action = str(report.get("next_action") or "")
    loop = report.get("loop") if isinstance(report.get("loop"), dict) else {}
    stop_reason = str(loop.get("stop_reason") or "")
    event_payload = {
        "project_slug": project_slug,
        "status": status,
        "next_action": next_action,
        "stop_reason": stop_reason,
        "fleet_report_path": result.get("fleet_report_path") if isinstance(result, dict) else None,
        "report_path": (report.get("report_paths") or {}).get("book_quality_closure")
        if isinstance(report.get("report_paths"), dict)
        else None,
    }
    if status == "ready":
        await reporter.emit(
            "book_quality_closure_completed",
            {**event_payload, "result": "book_quality_closure_done"},
            event_type="completed",
        )
    else:
        queued = await _enqueue_project_repair_if_needed(
            redis,
            reporter,
            project_slug,
            source="book_quality_closure",
            reason="book_quality_closure_requires_attention",
        )
        if not queued:
            await reporter.emit(
                "repairable_auto_continue_pending",
                {
                    **event_payload,
                    "reason": "book_quality_closure_requires_attention",
                },
                event_type="repairable_auto_continue_pending",
            )
    return result
