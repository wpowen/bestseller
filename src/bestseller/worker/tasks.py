from __future__ import annotations

import asyncio
import datetime as _dt
import importlib.util
import logging
import os
from argparse import Namespace
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import UUID

from sqlalchemy import func, select, update

from bestseller.domain.enums import ProjectStatus, WorkflowStatus
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel
from bestseller.infra.db.session import get_server_session
from bestseller.settings import get_settings
from bestseller.services.progress_context import set_ambient
from bestseller.worker.progress import RedisProgressReporter, make_sync_callback

logger = logging.getLogger(__name__)


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


def _coerce_workflow_run_uuid(workflow_run_id: str) -> UUID | None:
    try:
        return UUID(str(workflow_run_id))
    except (TypeError, ValueError):
        return None


async def _touch_workflow_run_heartbeat(
    workflow_run_id: str,
    *,
    project_slug: str | None = None,
    active_since: _dt.datetime | None = None,
) -> None:
    run_uuid = _coerce_workflow_run_uuid(workflow_run_id)
    async with get_server_session() as session:
        if run_uuid is not None:
            await session.execute(
                update(WorkflowRunModel)
                .where(
                    WorkflowRunModel.id == run_uuid,
                    WorkflowRunModel.status.in_(_ACTIVE_WORKFLOW_STATUSES),
                )
                .values(updated_at=func.now())
            )

        if project_slug:
            project_id = (
                select(ProjectModel.id)
                .where(ProjectModel.slug == project_slug)
                .scalar_subquery()
            )
            heartbeat_stmt = (
                update(WorkflowRunModel)
                .where(
                    WorkflowRunModel.project_id == project_id,
                    WorkflowRunModel.workflow_type.in_(_PROJECT_HEARTBEAT_WORKFLOW_TYPES),
                    WorkflowRunModel.status.in_(_ACTIVE_WORKFLOW_STATUSES),
                )
                .values(updated_at=func.now())
            )
            if active_since is not None:
                heartbeat_stmt = heartbeat_stmt.where(
                    WorkflowRunModel.created_at >= active_since,
                )
            await session.execute(heartbeat_stmt)

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
        job = await reporter._redis.enqueue_job(  # noqa: SLF001 — worker-internal reporter
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
    except Exception as exc:  # noqa: BLE001 — advisory job must never crash the worker
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
    try:
        job = await redis.enqueue_job(
            "run_project_repair_task",
            workflow_run_id=job_id,
            payload=repair_payload,
            _job_id=job_id,
            _expires=_dt.timedelta(days=7),
        )
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


def _load_closure_runner_module() -> Any:
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "run_book_quality_closure.py"
    spec = importlib.util.spec_from_file_location("bestseller_quality_closure_runner", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load quality closure runner at {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    await _mark_project_generation_repair_exhausted(
        project_slug,
        reason=reason,
        error_message=message,
    )
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

    await reporter.emit(
        "autowrite_started",
        {
            "project_slug": project_slug,
            "workflow_run_id": workflow_run_id,
            "status": "started",
        },
    )

    async with _workflow_db_heartbeat(workflow_run_id, project_slug=project_slug):
        try:
            async with get_server_session() as session:
                # Load existing project to build ProjectCreate payload
                project = await get_project_by_slug(session, project_slug)
                if project is None:
                    raise ValueError(f"Project '{project_slug}' not found")

                # Build ProjectCreate from the existing project record
                meta = project.metadata_json or {}
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

                premise = payload.get("premise") or str(meta.get("premise") or project.title)

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

    await reporter.emit(
        "project_pipeline_started",
        {
            "project_slug": project_slug,
            "workflow_run_id": workflow_run_id,
            "status": "started",
        },
    )

    async with _workflow_db_heartbeat(workflow_run_id, project_slug=project_slug):
        try:
            async with get_server_session() as session:
                result = await pipeline_services.run_project_pipeline(
                    session=session,
                    settings=settings,
                    project_slug=project_slug,
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


async def run_chapter_pipeline_task(
    ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Single chapter pipeline with progress reporting.

    Forwards the ARQ-level ``reporter`` into the chapter pipeline as a sync
    ``ProgressCallback`` (built via :func:`make_sync_callback`) so the inner
    scene-level emits surface in the SSE stream consumed by the Web UI.
    """
    from bestseller.services.pipelines import run_chapter_pipeline

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

    await reporter.emit("started", {"chapter_number": payload["chapter_number"]})

    async with _workflow_db_heartbeat(
        workflow_run_id,
        project_slug=project_slug,
    ):
        try:
            async with get_server_session() as session:
                result = await run_chapter_pipeline(
                    session=session,
                    settings=settings,
                    project_slug=project_slug,
                    chapter_number=payload["chapter_number"],
                    progress=make_sync_callback(reporter),
                )
        except Exception as exc:
            await reporter.emit("failed", {"error": str(exc)}, event_type="failed")
            raise

    result_payload = result.model_dump(mode="json")
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

            async with get_server_session() as session:
                result = await run_project_repair(
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
