from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncIterator
from uuid import UUID

from sqlalchemy import func, select, update

from bestseller.domain.enums import ProjectStatus, WorkflowStatus
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel
from bestseller.infra.db.session import get_server_session
from bestseller.settings import get_settings
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


def _coerce_workflow_run_uuid(workflow_run_id: str) -> UUID | None:
    try:
        return UUID(str(workflow_run_id))
    except (TypeError, ValueError):
        return None


async def _touch_workflow_run_heartbeat(
    workflow_run_id: str,
    *,
    project_slug: str | None = None,
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
            await session.execute(
                update(WorkflowRunModel)
                .where(
                    WorkflowRunModel.project_id == project_id,
                    WorkflowRunModel.workflow_type.in_(_PROJECT_HEARTBEAT_WORKFLOW_TYPES),
                    WorkflowRunModel.status.in_(_ACTIVE_WORKFLOW_STATUSES),
                )
                .values(updated_at=func.now())
            )

        await session.commit()


async def _workflow_heartbeat_loop(
    workflow_run_id: str,
    *,
    project_slug: str | None = None,
    interval_seconds: int = _WORKFLOW_HEARTBEAT_SECONDS,
) -> None:
    interval = max(15, int(interval_seconds or 60))
    while True:
        await asyncio.sleep(interval)
        try:
            await _touch_workflow_run_heartbeat(
                workflow_run_id,
                project_slug=project_slug,
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
    task = asyncio.create_task(
        _workflow_heartbeat_loop(workflow_run_id, project_slug=project_slug)
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


async def _mark_project_generation_repair_exhausted(
    project_slug: str,
    *,
    reason: str,
    error_message: str,
) -> None:
    """Persist an exhausted generation gate so self-heal stops requeueing it."""
    async with get_server_session() as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == project_slug)
        )
        if project is None:
            return

        now = _dt.datetime.now(_dt.UTC).isoformat()
        metadata = dict(project.metadata_json or {})
        metadata.update(
            {
                "generation_resume_blocked_by_planning_gate": True,
                "generation_auto_repair_exhausted": True,
                "production_paused": True,
                "production_pause_reason": reason,
                "last_generation_gate_error": error_message[:4000],
                "last_generation_gate_blocked_at": now,
            }
        )
        project.metadata_json = metadata
        project.status = ProjectStatus.PAUSED.value

        active_runs = list(
            await session.scalars(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.project_id == project.id,
                    WorkflowRunModel.status.in_(_ACTIVE_WORKFLOW_STATUSES),
                )
            )
        )
        for run in active_runs:
            run.status = WorkflowStatus.WAITING_HUMAN.value
            run.error_message = error_message[:4000]
            run.metadata_json = {
                **(run.metadata_json or {}),
                "generation_gate_blocked": True,
                "generation_auto_repair_exhausted": True,
                "generation_gate_reason": reason,
                "generation_gate_blocked_at": now,
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

    project_slug = payload["project_slug"]

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
                await _mark_project_generation_repair_exhausted(
                    project_slug,
                    reason=reason,
                    error_message=message,
                )
                await reporter.emit(
                    "blocked_generation_gate",
                    {
                        "project_slug": project_slug,
                        "reason": reason,
                        "error": message,
                        "auto_repair_exhausted": True,
                    },
                    event_type="blocked_generation_gate",
                )
                logger.warning(
                    "Autowrite task for %s exhausted generation gate auto-repair: %s",
                    project_slug,
                    reason,
                )
                return {
                    "status": "blocked_generation_gate",
                    "project_slug": project_slug,
                    "reason": reason,
                }
            await reporter.emit("failed", {"error": str(exc)}, event_type="failed")
            raise

    await reporter.emit("completed", {"result": "autowrite_done"})
    return result.model_dump(mode="json")


async def run_project_pipeline_task(
    ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Project-level pipeline (draft all chapters)."""
    from bestseller.services.pipelines import run_project_pipeline

    settings = get_settings()
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)
    project_slug = payload["project_slug"]

    async with _workflow_db_heartbeat(workflow_run_id, project_slug=project_slug):
        try:
            async with get_server_session() as session:
                result = await run_project_pipeline(
                    session=session,
                    settings=settings,
                    project_slug=project_slug,
                    progress=make_sync_callback(reporter),
                )
        except Exception as exc:
            gate_block = _generation_gate_block(exc)
            if gate_block is not None:
                reason, message = gate_block
                await _mark_project_generation_repair_exhausted(
                    project_slug,
                    reason=reason,
                    error_message=message,
                )
                await reporter.emit(
                    "blocked_generation_gate",
                    {
                        "project_slug": project_slug,
                        "reason": reason,
                        "error": message,
                        "auto_repair_exhausted": True,
                    },
                    event_type="blocked_generation_gate",
                )
                logger.warning(
                    "Project pipeline task for %s exhausted generation gate auto-repair: %s",
                    project_slug,
                    reason,
                )
                return {
                    "status": "blocked_generation_gate",
                    "project_slug": project_slug,
                    "reason": reason,
                }
            await reporter.emit("failed", {"error": str(exc)}, event_type="failed")
            raise

    await reporter.emit("completed", {"result": "project_pipeline_done"})
    return result.model_dump(mode="json")


async def run_chapter_pipeline_task(
    ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Single chapter pipeline (no progress callback — pipeline doesn't support it)."""
    from bestseller.services.pipelines import run_chapter_pipeline

    settings = get_settings()
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)

    await reporter.emit("started", {"chapter_number": payload["chapter_number"]})

    async with _workflow_db_heartbeat(
        workflow_run_id,
        project_slug=payload["project_slug"],
    ):
        try:
            async with get_server_session() as session:
                result = await run_chapter_pipeline(
                    session=session,
                    settings=settings,
                    project_slug=payload["project_slug"],
                    chapter_number=payload["chapter_number"],
                )
        except Exception as exc:
            await reporter.emit("failed", {"error": str(exc)}, event_type="failed")
            raise

    await reporter.emit("completed", {"result": "chapter_pipeline_done"})
    return result.model_dump(mode="json")


async def run_project_repair_task(
    ctx: dict[str, Any], workflow_run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Project repair pipeline for self-heal and queued repair jobs."""
    from bestseller.services.repair import run_project_repair

    settings = get_settings()
    redis = ctx["redis"]
    reporter = RedisProgressReporter(redis, workflow_run_id)

    project_slug = payload["project_slug"]
    await reporter.emit("project_repair_started", {"project_slug": project_slug})

    async with _workflow_db_heartbeat(workflow_run_id, project_slug=project_slug):
        try:
            async with get_server_session() as session:
                result = await run_project_repair(
                    session=session,
                    settings=settings,
                    project_slug=project_slug,
                    requested_by=str(payload.get("requested_by") or "worker_self_heal"),
                    refresh_impacts=bool(payload.get("refresh_impacts", True)),
                    export_markdown=bool(payload.get("export_markdown", True)),
                    include_pending_rewrite_tasks=bool(
                        payload.get("include_pending_rewrite_tasks", False)
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
                await _mark_project_generation_repair_exhausted(
                    project_slug,
                    reason=reason,
                    error_message=message,
                )
                await reporter.emit(
                    "blocked_generation_gate",
                    {
                        "project_slug": project_slug,
                        "reason": reason,
                        "error": message,
                        "auto_repair_exhausted": True,
                    },
                    event_type="blocked_generation_gate",
                )
                logger.warning(
                    "Project repair task for %s exhausted generation gate auto-repair: %s",
                    project_slug,
                    reason,
                )
                return {
                    "status": "blocked_generation_gate",
                    "project_slug": project_slug,
                    "reason": reason,
                }
            await reporter.emit("failed", {"error": str(exc)}, event_type="failed")
            raise

    if result.requires_human_review:
        await reporter.emit(
            "waiting_human",
            {
                "project_slug": project_slug,
                "reason": "project_repair_requires_attention",
                "workflow_run_id": str(result.workflow_run_id),
            },
            event_type="waiting_human",
        )
    else:
        await reporter.emit("completed", {"result": "project_repair_done"})
    return result.model_dump(mode="json")
