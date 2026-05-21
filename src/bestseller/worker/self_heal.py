"""Detect and re-queue stuck generation pipelines at worker startup.

A project is considered *stuck* when:

1. Its ``project.metadata_json`` has ``stuck_at_chapter`` (set by
   :func:`bestseller.services.if_generation._persist_stuck_state` after
   chapter generation exhausted every retry), **or**
2. It has ``ChapterModel`` rows that lack a current ``ChapterDraftVersionModel``
   and has no active ``WorkflowRunModel`` — i.e. the pipeline was
   interrupted (container restart, kill -9) before the run finished and
   before a resumable marker could be persisted.

For each stuck project the scanner enqueues a fresh ``run_autowrite_task``
through the ARQ pool. The autowrite task is idempotent: it re-reads
``existing_chapters`` from disk (``_load_all_chapters``) and skips work
already persisted, so re-queueing is safe.

Before detection, :func:`reap_orphan_workflow_runs` flips workflow rows
that have been ``running`` / ``queued`` / ``pending`` past a threshold
(default 2 hours) to ``failed``. Without this step the active-pipeline
guard below would treat every zombie row left behind by ``kill -9`` as
legitimate and refuse to re-queue the project.

The scanner is intentionally conservative — projects that still have
a *genuinely* active workflow run (one whose ``updated_at`` was touched
recently) are left alone to avoid duplicate pipelines racing on the
same DB rows.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from sqlalchemy import and_, func, or_, select, text, update

if TYPE_CHECKING:  # pragma: no cover — import only for type hints
    from arq.connections import ArqRedis

from bestseller.domain.enums import ProjectStatus, WorkflowStatus
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.infra.db.session import get_server_session
from bestseller.settings import AppSettings

# Periodic self-heal must not reap legitimate long LLM calls.  A single
# planner attempt can take 15 minutes and the outline repair loop can run
# several attempts before emitting a new workflow-row update.  Startup still
# reaps old rows immediately via ``startup_cutoff``; this longer periodic
# timeout prevents active workers from being marked failed mid-call.
ORPHAN_WORKFLOW_TIMEOUT_SECONDS = 3 * 60 * 60

# Anything active + older than this at worker startup is, by definition,
# a ghost from a prior container that died before updating its row. The
# grace window accounts for rare cases where a job was just enqueued but
# the row write hasn't reached the replica yet.
STARTUP_GRACE_SECONDS = 5

# Redis lock key + TTL. Multiple worker containers boot concurrently —
# without this lock each one would independently scan stuck projects and
# enqueue duplicate autowrite tasks, which then race each other inside
# `rebuild_narrative_graph` and trip the `uq_pacing_curve_chapter`
# unique constraint. TTL is long enough to cover a slow scan + enqueue
# cycle but short enough to never block legitimate reboots.
SELF_HEAL_LOCK_KEY = "bestseller:self_heal:boot_lock"
SELF_HEAL_LOCK_TTL_SECONDS = 180

# Marker key web uses to confirm worker's startup heal scan has finished.
# Without it, web's ``auto_resume_zombies`` would race ahead of worker and
# see an empty ``arq:job:autowrite:heal:*`` set even when stuck projects
# exist — the heal keys only get created AFTER the scan runs. TTL covers
# the span between worker restarts; web tolerates a stale marker because
# the ARQ keys themselves are the source of truth once present.
SELF_HEAL_SCAN_DONE_KEY = "bestseller:self_heal:scan_done"
SELF_HEAL_SCAN_DONE_TTL_SECONDS = 7200

# ARQ's default job expiry is 24 hours.  A single long-form autowrite job can
# legitimately occupy a worker for longer than that, so startup self-heal jobs
# queued behind it must survive a multi-day backlog instead of expiring before
# they ever start.
SELF_HEAL_JOB_EXPIRES_DAYS = 7

# ARQ can leave ``arq:in-progress:*`` and ``arq:retry:*`` keys behind when a
# worker is rebuilt or killed mid-job. If the DB has no active workflow for the
# project and the queue score is already well in the past, the key set is a
# ghost and must be cleared before deterministic requeue can succeed.
STALE_ARQ_IN_PROGRESS_GRACE_SECONDS = 5 * 60

# Project repair writes a DB heartbeat through worker.tasks while it is alive,
# so a stale running repair row can be safely reaped by the periodic orphan
# scanner. A project_repair run that has already reached WAITING_HUMAN should
# only suppress self-heal briefly. Older waiting rows are historical evidence,
# not an active handoff; leaving them indefinite made blocked projects stall
# forever.
# Only suppress duplicate repair dispatches long enough for the just-finished
# worker state to settle. A waiting_human repair row is historical evidence,
# not an acceptable long-lived stop state for autonomous recovery.
WAITING_REPAIR_SUPPRESSION_SECONDS = 60
WRITE_SAFETY_REPAIR_RECOVERY_SECONDS = 15 * 60
WRITE_SAFETY_REPAIR_MAX_RECOVERY_SECONDS = 45 * 60

_AUTO_REPAIRABLE_WRITE_SAFETY_BLOCK_CODES = frozenset(
    {
        "block_low",
        "block_high",
        "dialog_unpaired",
        "ending_sentence_weak",
        "dead_alive",
        "pronoun_mismatch",
        "character_resurrection",
        "character_missing_appearance",
        "character_sealed_appearance",
        "character_sleeping_appearance",
        "character_comatose_appearance",
        "canon_forbidden_term",
        "canon_state_regression",
        "cross_chapter_repetition",
        "intra_chapter_repetition",
    }
)

# These planning gates have deterministic repair paths in the planner. If an
# older deployment paused a project after exhausting that path, startup self-heal
# may retry once the pause is no longer fresh instead of treating it as a manual
# structural stop forever.
GENERATION_GATE_RESUME_COOLDOWN_SECONDS = 15 * 60
_AUTO_RESUMABLE_GENERATION_GATE_REASONS = frozenset(
    {
        "scene_plan_richness_gate_failed",
        "story_bible_gate_failed",
        "volume_outline_gate_failed",
    }
)

logger = logging.getLogger(__name__)


_ACTIVE_STATUSES = frozenset(
    {
        WorkflowStatus.PENDING.value,
        WorkflowStatus.QUEUED.value,
        WorkflowStatus.RUNNING.value,
    }
)
_ORPHAN_ERROR_MESSAGE = "reaped by self-heal (abandoned by prior worker)"

_REAPABLE_WORKFLOW_TYPES = frozenset(
    {
        "autowrite_pipeline",
        "generate_novel_plan",
        "generate_volume_plan",
        "project_pipeline",
        "chapter_pipeline",
        "scene_pipeline",
        "project_repair",
    }
)
_STARTUP_ONLY_REAPABLE_WORKFLOW_TYPES = frozenset()
_SELF_HEAL_BLOCKING_WORKFLOW_TYPES = (
    _REAPABLE_WORKFLOW_TYPES
    | _STARTUP_ONLY_REAPABLE_WORKFLOW_TYPES
    | frozenset(
        {
            "materialize_story_bible",
            "materialize_chapter_outline_batch",
            "materialize_narrative_graph",
            "materialize_narrative_tree",
        }
    )
)


@dataclass(frozen=True)
class StuckProject:
    project_id: Any
    slug: str
    reason: str
    stuck_at_chapter: int | None
    chapters_total: int
    chapters_with_draft: int
    heal_kind: str = "autowrite"


async def reap_orphan_workflow_runs(
    session: Any,
    timeout_seconds: int = ORPHAN_WORKFLOW_TIMEOUT_SECONDS,
    startup_cutoff: _dt.datetime | None = None,
) -> int:
    """Flip active ``WorkflowRunModel`` rows that look abandoned to ``failed``.

    A row is reaped if **either**:

    * ``updated_at`` is older than ``timeout_seconds`` (no heartbeat), **or**
    * ``updated_at`` is older than ``startup_cutoff`` — the time the current
      worker container booted minus a small grace window. At worker startup
      every genuinely-active row must have been written by *this* worker
      instance, so anything older is a ghost from a previous container.

    The second rule is what actually unblocks the system after a crash
    since the first (heartbeat-based) rule forces us to wait
    ``timeout_seconds`` even when we *know* the old process is dead.

    Returns the number of rows reaped. Caller is responsible for commit.
    """
    now = _dt.datetime.now(_dt.UTC)
    heartbeat_cutoff = now - _dt.timedelta(seconds=timeout_seconds)

    stale_conditions = [WorkflowRunModel.updated_at < heartbeat_cutoff]
    if startup_cutoff is not None:
        stale_conditions.append(WorkflowRunModel.updated_at < startup_cutoff)
        # At process startup, any active workflow created before the new
        # worker's boot window belongs to a dead worker, even if it managed to
        # heartbeat shortly before the container was replaced.
        stale_conditions.append(WorkflowRunModel.created_at < startup_cutoff)

    result = await session.execute(
        update(WorkflowRunModel)
        .where(
            WorkflowRunModel.workflow_type.in_(_REAPABLE_WORKFLOW_TYPES),
            WorkflowRunModel.status.in_(_ACTIVE_STATUSES),
            or_(*stale_conditions),
        )
        .values(
            status=WorkflowStatus.FAILED.value,
            error_message=_ORPHAN_ERROR_MESSAGE,
        )
    )
    reaped = int(result.rowcount or 0)

    if startup_cutoff is not None:
        startup_result = await session.execute(
            update(WorkflowRunModel)
            .where(
                WorkflowRunModel.workflow_type.in_(_STARTUP_ONLY_REAPABLE_WORKFLOW_TYPES),
                WorkflowRunModel.status.in_(_ACTIVE_STATUSES),
                or_(
                    WorkflowRunModel.updated_at < startup_cutoff,
                    WorkflowRunModel.created_at < startup_cutoff,
                ),
            )
            .values(
                status=WorkflowStatus.FAILED.value,
                error_message=_ORPHAN_ERROR_MESSAGE,
            )
        )
        reaped += int(startup_result.rowcount or 0)

    # Parent/child workflow rows can be left inconsistent during rolling
    # rebuilds: the parent chapter/project run is reaped first, while a child
    # scene run created by the old worker still says "running". Reap those
    # immediately; otherwise inspection shows active work that no worker owns.
    child_result = await session.execute(
        text(
            """
            UPDATE workflow_runs AS child
            SET status = :failed_status,
                error_message = :error_message
            FROM workflow_runs AS parent
            WHERE child.status IN ('pending', 'queued', 'running')
              AND child.metadata ? 'parent_workflow_run_id'
              AND child.metadata ->> 'parent_workflow_run_id' = parent.id::text
              AND parent.status NOT IN ('pending', 'queued', 'running')
            """
        ),
        {
            "failed_status": WorkflowStatus.FAILED.value,
            "error_message": _ORPHAN_ERROR_MESSAGE,
        },
    )
    return reaped + int(child_result.rowcount or 0)


async def find_stuck_projects(session: Any) -> list[StuckProject]:
    """Return every project that looks stuck and has no active pipeline.

    Three detection paths:

    1. *Explicit*: ``project.metadata_json.stuck_at_chapter`` is set.
    2. *Missing drafts*: project has ``ChapterModel`` rows whose current
       ``ChapterDraftVersionModel`` is missing — i.e. a pipeline laid
       down chapter stubs but the writer never filled them in.
    3. *Under-target*: project has fewer ``ChapterModel`` rows than
       ``target_chapters`` AND its status is not terminal. This catches
       the case where the per-volume loop in
       ``run_progressive_autowrite_pipeline`` exited early (silent
       swallow of an inner exception, outline drift, etc.) so whole
       volumes were never planned — their chapter rows never got
       created, so ``chapters_with_draft >= chapters_total`` and path 2
       thinks the project is fine even though it is only partially
       written. Seen on ``romantasy-1776330993`` (150/800) and
       ``superhero-fiction-1776147970`` (250/800) in production.
    """
    projects = list(await session.scalars(select(ProjectModel)))
    stuck: list[StuckProject] = []

    for project in projects:
        # Skip projects with an active pipeline run.
        if await _has_active_pipeline_run(session, project.id):
            continue

        auto_resumable_generation_gate = _project_has_stale_auto_resumable_generation_gate(
            project
        )

        meta = project.metadata_json or {}
        explicit_stuck_ch = meta.get("stuck_at_chapter")

        chapters_total = (
            await session.scalar(
                select(func.count())
                .select_from(ChapterModel)
                .where(ChapterModel.project_id == project.id)
            )
        ) or 0

        chapters_with_draft = (
            await session.scalar(
                select(func.count(func.distinct(ChapterDraftVersionModel.chapter_id)))
                .select_from(ChapterDraftVersionModel)
                .join(
                    ChapterModel,
                    ChapterModel.id == ChapterDraftVersionModel.chapter_id,
                )
                .where(
                    and_(
                        ChapterModel.project_id == project.id,
                        ChapterDraftVersionModel.is_current.is_(True),
                    )
                )
            )
        ) or 0

        explicit_stuck_chapter: int | None = None
        if explicit_stuck_ch is not None:
            try:
                explicit_stuck_chapter = int(explicit_stuck_ch)
            except (TypeError, ValueError):
                explicit_stuck_chapter = None

        blocked_chapters = (
            await session.scalar(
                select(func.count())
                .select_from(ChapterModel)
                .where(
                    and_(
                        ChapterModel.project_id == project.id,
                        ChapterModel.production_state == "blocked",
                    )
                )
            )
        ) or 0
        if blocked_chapters > 0:
            if await _blocked_chapters_have_recent_waiting_repair(session, project.id):
                logger.info(
                    "self-heal: skipped slug=%s — blocked chapters already reached waiting_human repair",
                    project.slug,
                )
                continue
            stuck.append(
                StuckProject(
                    project_id=project.id,
                    slug=project.slug,
                    reason="blocked_chapters",
                    stuck_at_chapter=None,
                    chapters_total=int(chapters_total),
                    chapters_with_draft=int(chapters_with_draft),
                    heal_kind="repair",
                )
            )
            continue

        # A production pause such as ``structural_repair_before_continuation``
        # should stop continuation/autowrite, not the repair loop itself.
        # Check it only after blocked chapters have had a chance to dispatch a
        # repair heal job; otherwise projects can sit paused forever with no
        # visible repair progress.
        if _project_resume_is_blocked(project):
            continue

        if explicit_stuck_chapter is not None and chapters_with_draft < explicit_stuck_chapter:
            stuck.append(
                StuckProject(
                    project_id=project.id,
                    slug=project.slug,
                    reason="explicit_stuck_marker",
                    stuck_at_chapter=explicit_stuck_chapter,
                    chapters_total=int(chapters_total),
                    chapters_with_draft=int(chapters_with_draft),
                )
            )
            continue

        if chapters_total > 0 and chapters_with_draft < chapters_total:
            stuck.append(
                StuckProject(
                    project_id=project.id,
                    slug=project.slug,
                    reason="missing_drafts",
                    stuck_at_chapter=int(chapters_with_draft) + 1,
                    chapters_total=int(chapters_total),
                    chapters_with_draft=int(chapters_with_draft),
                    heal_kind="project_pipeline",
                )
            )
            continue

        # Under-target: volumes never got planned past a certain point.
        # Only trigger for projects still in a writing state — a project
        # the user explicitly finished or abandoned (``completed`` /
        # ``archived``) should not be auto-resumed.
        target_chapters = int(getattr(project, "target_chapters", 0) or 0)
        status = (getattr(project, "status", None) or "").lower()
        under_target_status = status in {
            "writing",
            "planning",
            "revising",
            "drafting",
            "",
        } or auto_resumable_generation_gate
        if target_chapters > 0 and chapters_total < target_chapters and under_target_status:
            stuck.append(
                StuckProject(
                    project_id=project.id,
                    slug=project.slug,
                    reason="under_target_chapters",
                    stuck_at_chapter=int(chapters_total) + 1,
                    chapters_total=int(chapters_total),
                    chapters_with_draft=int(chapters_with_draft),
                )
            )

    return stuck


async def _has_active_pipeline_run(session: Any, project_id: Any) -> bool:
    active = await session.scalar(
        select(WorkflowRunModel.id)
        .where(
            WorkflowRunModel.project_id == project_id,
            WorkflowRunModel.workflow_type.in_(_SELF_HEAL_BLOCKING_WORKFLOW_TYPES),
            WorkflowRunModel.status.in_(_ACTIVE_STATUSES),
        )
        .limit(1)
    )
    return active is not None


async def _blocked_chapters_have_recent_waiting_repair(
    session: Any,
    project_id: Any,
) -> bool:
    latest_blocked_row = await session.scalar(
        select(ChapterModel)
        .where(
            ChapterModel.project_id == project_id,
            ChapterModel.production_state == "blocked",
        )
        .order_by(ChapterModel.updated_at.desc())
        .limit(1)
    )
    latest_blocked_update = _ensure_utc(getattr(latest_blocked_row, "updated_at", None))
    latest_waiting_repair_update = await session.scalar(
        select(func.max(WorkflowRunModel.updated_at)).where(
            WorkflowRunModel.project_id == project_id,
            WorkflowRunModel.workflow_type == "project_repair",
            WorkflowRunModel.status == WorkflowStatus.WAITING_HUMAN.value,
        )
    )
    latest_waiting_repair_update = _ensure_utc(latest_waiting_repair_update)
    if latest_waiting_repair_update is None:
        return False
    if latest_waiting_repair_update < _dt.datetime.now(_dt.UTC) - _dt.timedelta(
        seconds=WAITING_REPAIR_SUPPRESSION_SECONDS
    ):
        return False
    if latest_blocked_update is None:
        return False

    if latest_waiting_repair_update < latest_blocked_update:
        return False

    if latest_blocked_row is not None:
        blocked_meta = dict(getattr(latest_blocked_row, "metadata_json", None) or {})
        blocked_code = str(blocked_meta.get("write_safety_block_code") or "")
        if _is_auto_repairable_write_safety_block(blocked_code):
            attempts = _metadata_int(blocked_meta, "auto_repair_attempts", 0)
            recovery_wait_seconds = _write_safety_repair_wait_seconds(attempts)
            if latest_waiting_repair_update >= _dt.datetime.now(_dt.UTC) - _dt.timedelta(
                seconds=recovery_wait_seconds
            ):
                return True
            return False

    return latest_waiting_repair_update >= latest_blocked_update


def _ensure_utc(value: _dt.datetime | None) -> _dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_dt.UTC)
    return value.astimezone(_dt.UTC)


def _metadata_datetime(metadata: dict[str, Any], *keys: str) -> _dt.datetime | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, _dt.datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            try:
                parsed = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                continue
        else:
            continue
        return _ensure_utc(parsed)
    return None


def _metadata_int(metadata: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(metadata.get(key, default))
    except (TypeError, ValueError):
        return default


def _is_auto_repairable_write_safety_block(block_code: str | None) -> bool:
    if not block_code:
        return False
    normalized = str(block_code).strip().lower()
    if not normalized:
        return False
    return any(code in normalized for code in _AUTO_REPAIRABLE_WRITE_SAFETY_BLOCK_CODES)


def _write_safety_repair_wait_seconds(auto_repair_attempts: int) -> int:
    attempts = max(1, int(auto_repair_attempts))
    return min(
        WRITE_SAFETY_REPAIR_MAX_RECOVERY_SECONDS,
        WRITE_SAFETY_REPAIR_RECOVERY_SECONDS * (2 ** (attempts - 1)),
    )


def _project_has_stale_auto_resumable_generation_gate(project: ProjectModel) -> bool:
    metadata = getattr(project, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        return False
    reason = str(metadata.get("production_pause_reason") or "").strip()
    base_reason = reason.split(":", 1)[0]
    if base_reason not in _AUTO_RESUMABLE_GENERATION_GATE_REASONS:
        return False
    if not (
        metadata.get("generation_resume_blocked_by_planning_gate")
        or metadata.get("generation_auto_repair_exhausted")
    ):
        return False
    blocked_at = _metadata_datetime(
        metadata,
        "last_generation_gate_blocked_at",
        "paused_at",
    )
    if blocked_at is None:
        return True
    cooldown_cutoff = _dt.datetime.now(_dt.UTC) - _dt.timedelta(
        seconds=GENERATION_GATE_RESUME_COOLDOWN_SECONDS
    )
    return blocked_at <= cooldown_cutoff


async def _clear_auto_resumable_generation_gate_pause(
    session: Any,
    project_id: Any,
) -> bool:
    project = await session.get(ProjectModel, project_id)
    if project is None or not _project_has_stale_auto_resumable_generation_gate(project):
        return False

    metadata = dict(getattr(project, "metadata_json", None) or {})
    reason = str(metadata.get("production_pause_reason") or "")
    metadata["last_generation_gate_auto_resumed_at"] = _dt.datetime.now(_dt.UTC).isoformat()
    metadata["last_generation_gate_auto_resumed_reason"] = reason
    for key in (
        "generation_resume_blocked_by_planning_gate",
        "generation_auto_repair_exhausted",
        "production_paused",
        "production_pause_reason",
        "last_generation_gate_blocked_at",
    ):
        metadata.pop(key, None)
    project.metadata_json = metadata
    if (getattr(project, "status", None) or "").lower() == ProjectStatus.PAUSED.value:
        project.status = ProjectStatus.REVISING.value
    await session.flush()
    logger.info(
        "self-heal: cleared stale generation gate pause slug=%s reason=%s",
        getattr(project, "slug", project_id),
        reason,
    )
    return True


def _project_resume_is_blocked(project: ProjectModel) -> bool:
    metadata = getattr(project, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    if _project_has_stale_auto_resumable_generation_gate(project):
        return False
    status = (getattr(project, "status", None) or "").lower()
    if status == ProjectStatus.PAUSED.value and not metadata.get("stuck_at_chapter"):
        return True
    return bool(
        metadata.get("generation_resume_blocked_until_repair_audit")
        or metadata.get("structural_repair_required")
        or metadata.get("production_pause_reason")
        or metadata.get("production_paused")
    )


def _arq_redis_settings(settings: AppSettings) -> Any:
    from arq.connections import RedisSettings  # noqa: PLC0415

    parsed = urlparse(settings.redis.url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or "0"),
        password=parsed.password,
    )


def _autowrite_heal_job_id(slug: str) -> str:
    """Deterministic ARQ job id so concurrent workers can't double-enqueue.

    ARQ rejects (returns ``None`` from ``enqueue_job``) any second job that
    shares a ``_job_id`` with one already queued or in-flight. Keying on the
    project slug means self-heal is naturally idempotent across workers
    even if the Redis lock somehow lapses.
    """
    return f"autowrite:heal:{slug}"


def _repair_heal_job_id(slug: str) -> str:
    """Deterministic ARQ job id for project repair self-heal."""
    return f"repair:heal:{slug}"


def _project_pipeline_heal_job_id(slug: str) -> str:
    """Deterministic ARQ job id for project-pipeline continuation self-heal."""
    return f"project-pipeline:heal:{slug}"


async def _heal_job_exists(pool: "ArqRedis", job_id: str) -> bool:
    job_key = f"arq:job:{job_id}"
    in_progress_key = f"arq:in-progress:{job_id}"
    retry_key = f"arq:retry:{job_id}"
    try:
        if await pool.exists(job_key, in_progress_key, retry_key):
            return True
        return await pool.zscore("arq:queue", job_id) is not None
    except AttributeError:
        return False


async def _clear_stale_heal_job_if_needed(pool: "ArqRedis", job_id: str) -> bool:
    """Clear a deterministic heal job only when ARQ left a ghost owner.

    Callers use this after DB-level active workflow checks have already decided
    the project is stuck. That context matters: a live long-running generation
    can legitimately hold ``arq:in-progress:*`` for many minutes, so this helper
    must not be used as a general liveness probe.
    """

    if not await _stale_in_progress_job(pool, job_id):
        return False
    await pool.delete(
        f"arq:job:{job_id}",
        f"arq:in-progress:{job_id}",
        f"arq:result:{job_id}",
        f"arq:retry:{job_id}",
    )
    await _safe_zrem(pool, "arq:queue", job_id)
    logger.info("self-heal: cleared stale ARQ heal job %s", job_id)
    return True


async def _requeue_autowrite(
    pool: "ArqRedis",
    stuck: StuckProject,
) -> str | None:
    """Enqueue a fresh ``run_autowrite_task`` for a stuck project.

    Returns the job id on success, or ``None`` if ARQ already has a
    pending/running job for the same slug (deterministic dedup).
    """
    repair_job_id = _repair_heal_job_id(stuck.slug)
    if await _heal_job_exists(pool, repair_job_id):
        if not await _clear_stale_heal_job_if_needed(pool, repair_job_id):
            logger.info(
                "self-heal: skipped autowrite slug=%s — repair job already owns project",
                stuck.slug,
            )
            return None
        logger.info(
            "self-heal: cleared stale repair owner before autowrite slug=%s",
            stuck.slug,
        )

    job_id = _autowrite_heal_job_id(stuck.slug)
    job = await pool.enqueue_job(
        "run_autowrite_task",
        workflow_run_id=job_id,
        payload={"project_slug": stuck.slug, "premise": None},
        _job_id=job_id,
        _expires=_dt.timedelta(days=SELF_HEAL_JOB_EXPIRES_DAYS),
    )
    if job is None:
        job_key = f"arq:job:{job_id}"
        in_progress_key = f"arq:in-progress:{job_id}"
        result_key = f"arq:result:{job_id}"
        retry_key = f"arq:retry:{job_id}"
        if not await _clear_stale_heal_job_if_needed(pool, job_id):
            in_flight = await pool.exists(job_key, in_progress_key)
            if in_flight:
                return None
            await pool.delete(result_key, retry_key)
        job = await pool.enqueue_job(
            "run_autowrite_task",
            workflow_run_id=job_id,
            payload={"project_slug": stuck.slug, "premise": None},
            _job_id=job_id,
            _expires=_dt.timedelta(days=SELF_HEAL_JOB_EXPIRES_DAYS),
        )
        if job is None:
            return None
    return job_id


async def _requeue_repair(
    pool: "ArqRedis",
    stuck: StuckProject,
) -> str | None:
    """Enqueue a project repair job for blocked production chapters."""
    autowrite_job_id = _autowrite_heal_job_id(stuck.slug)
    if await _heal_job_exists(pool, autowrite_job_id):
        if not await _clear_stale_heal_job_if_needed(pool, autowrite_job_id):
            logger.info(
                "self-heal: skipped repair slug=%s — autowrite job already owns project",
                stuck.slug,
            )
            return None
        logger.info(
            "self-heal: cleared stale autowrite owner before repair slug=%s",
            stuck.slug,
        )

    job_id = _repair_heal_job_id(stuck.slug)
    job = await pool.enqueue_job(
        "run_project_repair_task",
        workflow_run_id=job_id,
        payload={
            "project_slug": stuck.slug,
            "requested_by": "worker_self_heal",
            "include_pending_rewrite_tasks": True,
            "pending_rewrite_task_limit": 10,
        },
        _job_id=job_id,
        _expires=_dt.timedelta(days=SELF_HEAL_JOB_EXPIRES_DAYS),
    )
    if job is None:
        job_key = f"arq:job:{job_id}"
        in_progress_key = f"arq:in-progress:{job_id}"
        result_key = f"arq:result:{job_id}"
        retry_key = f"arq:retry:{job_id}"
        if not await _clear_stale_heal_job_if_needed(pool, job_id):
            in_flight = await pool.exists(job_key, in_progress_key)
            if in_flight:
                return None
            await pool.delete(result_key, retry_key)
        job = await pool.enqueue_job(
            "run_project_repair_task",
            workflow_run_id=job_id,
            payload={
                "project_slug": stuck.slug,
                "requested_by": "worker_self_heal",
                "include_pending_rewrite_tasks": True,
                "pending_rewrite_task_limit": 10,
            },
            _job_id=job_id,
            _expires=_dt.timedelta(days=SELF_HEAL_JOB_EXPIRES_DAYS),
        )
        if job is None:
            return None
    return job_id


async def _requeue_project_pipeline(
    pool: "ArqRedis",
    stuck: StuckProject,
) -> str | None:
    """Enqueue project pipeline continuation for planned chapters without drafts."""
    repair_job_id = _repair_heal_job_id(stuck.slug)
    if await _heal_job_exists(pool, repair_job_id):
        if not await _clear_stale_heal_job_if_needed(pool, repair_job_id):
            logger.info(
                "self-heal: skipped project pipeline slug=%s — repair job already owns project",
                stuck.slug,
            )
            return None
        logger.info(
            "self-heal: cleared stale repair owner before project pipeline slug=%s",
            stuck.slug,
        )

    autowrite_job_id = _autowrite_heal_job_id(stuck.slug)
    if await _heal_job_exists(pool, autowrite_job_id):
        if not await _clear_stale_heal_job_if_needed(pool, autowrite_job_id):
            logger.info(
                "self-heal: skipped project pipeline slug=%s — autowrite job already owns project",
                stuck.slug,
            )
            return None
        logger.info(
            "self-heal: cleared stale autowrite owner before project pipeline slug=%s",
            stuck.slug,
        )

    job_id = _project_pipeline_heal_job_id(stuck.slug)
    job = await pool.enqueue_job(
        "run_project_pipeline_task",
        workflow_run_id=job_id,
        payload={"project_slug": stuck.slug},
        _job_id=job_id,
        _expires=_dt.timedelta(days=SELF_HEAL_JOB_EXPIRES_DAYS),
    )
    if job is None:
        job_key = f"arq:job:{job_id}"
        in_progress_key = f"arq:in-progress:{job_id}"
        result_key = f"arq:result:{job_id}"
        retry_key = f"arq:retry:{job_id}"
        if not await _clear_stale_heal_job_if_needed(pool, job_id):
            in_flight = await pool.exists(job_key, in_progress_key)
            if in_flight:
                return None
            await pool.delete(result_key, retry_key)
        job = await pool.enqueue_job(
            "run_project_pipeline_task",
            workflow_run_id=job_id,
            payload={"project_slug": stuck.slug},
            _job_id=job_id,
            _expires=_dt.timedelta(days=SELF_HEAL_JOB_EXPIRES_DAYS),
        )
        if job is None:
            return None
    return job_id


async def _requeue_stuck_project(
    pool: "ArqRedis",
    stuck: StuckProject,
) -> str | None:
    if stuck.heal_kind == "repair":
        return await _requeue_repair(pool, stuck)
    if stuck.heal_kind == "project_pipeline":
        return await _requeue_project_pipeline(pool, stuck)
    return await _requeue_autowrite(pool, stuck)


def _coalesce_stuck_projects_for_enqueue(
    stuck_list: list[StuckProject],
) -> list[StuckProject]:
    """Keep one self-heal owner per project, preferring the narrowest owner."""

    chosen: dict[str, StuckProject] = {}
    order: list[str] = []
    priority = {"autowrite": 1, "project_pipeline": 2, "repair": 3}
    for stuck in stuck_list:
        if stuck.slug not in chosen:
            chosen[stuck.slug] = stuck
            order.append(stuck.slug)
            continue
        if priority.get(stuck.heal_kind, 0) > priority.get(
            chosen[stuck.slug].heal_kind,
            0,
        ):
            chosen[stuck.slug] = stuck
    return [chosen[slug] for slug in order]


async def _stale_in_progress_job(pool: "ArqRedis", job_id: str) -> bool:
    in_progress_key = f"arq:in-progress:{job_id}"
    try:
        in_flight = await pool.exists(in_progress_key)
    except Exception:  # noqa: BLE001
        logger.exception(
            "self-heal: failed to inspect ARQ in-progress key %s",
            in_progress_key,
        )
        return False
    if not in_flight:
        return False

    try:
        score = await pool.zscore("arq:queue", job_id)
    except AttributeError:
        return False
    except Exception:  # noqa: BLE001
        logger.exception("self-heal: failed to inspect ARQ queue score for %s", job_id)
        return False
    if score is None:
        return False
    try:
        score_ms = float(score)
    except (TypeError, ValueError):
        return False
    cutoff_ms = (time.time() - STALE_ARQ_IN_PROGRESS_GRACE_SECONDS) * 1000
    return score_ms < cutoff_ms


async def _safe_zrem(pool: "ArqRedis", key: str, member: str) -> None:
    try:
        zrem = getattr(pool, "zrem")
    except AttributeError:
        return
    try:
        await zrem(key, member)
    except Exception:  # noqa: BLE001
        logger.exception("self-heal: failed to remove stale ARQ queue member %s", member)


async def _try_acquire_heal_lock(
    redis: Any | None,
    worker_id: str,
    ttl_seconds: int = SELF_HEAL_LOCK_TTL_SECONDS,
) -> bool:
    """Attempt to claim the singleton self-heal lock via ``SET NX EX``.

    Returns ``True`` if this worker now holds the lock, ``False`` if
    another worker already does (and this one should skip self-heal).
    A ``None`` redis client short-circuits to ``True`` so tests and
    single-worker environments still run.
    """
    if redis is None:
        return True
    try:
        acquired = await redis.set(
            SELF_HEAL_LOCK_KEY,
            worker_id,
            nx=True,
            ex=ttl_seconds,
        )
    except Exception:  # noqa: BLE001 — lock is advisory; fall back to running
        logger.exception("self-heal: lock acquisition failed — running anyway")
        return True
    return bool(acquired)


async def heal_stuck_projects(
    settings: AppSettings,
    startup_cutoff: _dt.datetime | None = None,
    *,
    redis: Any | None = None,
    worker_id: str | None = None,
) -> list[dict[str, Any]]:
    """Scan for stuck projects and re-queue their autowrite task.

    When multiple worker containers boot concurrently they all hit this
    function; the Redis ``SET NX`` lock ensures only one actually runs
    the scan+enqueue. Pass a ``redis`` client to enable the lock — when
    omitted the caller is assumed to be a single-writer context (CLI,
    test, etc.) and the lock is skipped.

    Returns a list of ``{slug, task_id, reason, stuck_at_chapter}`` dicts
    describing what was requeued. Called at worker startup.

    Errors encountered while scanning a single project are logged and
    skipped — self-heal must never block worker startup.
    """
    from arq.connections import create_pool  # noqa: PLC0415

    dispatched: list[dict[str, Any]] = []

    effective_worker_id = worker_id or str(uuid.uuid4())
    if not await _try_acquire_heal_lock(redis, effective_worker_id):
        logger.info(
            "self-heal: another worker holds the boot lock — skipping scan",
        )
        await _wait_for_scan_done(redis)
        return dispatched

    pool: "ArqRedis | None" = None
    try:
        if redis is not None:
            try:
                await redis.delete(SELF_HEAL_SCAN_DONE_KEY)
            except Exception:  # noqa: BLE001
                logger.exception("self-heal: failed to clear stale scan-done marker")

        async with get_server_session() as session:
            reaped = await reap_orphan_workflow_runs(
                session,
                startup_cutoff=startup_cutoff,
            )
            if reaped:
                await session.commit()
                logger.info(
                    "self-heal: reaped %d orphan workflow run(s)",
                    reaped,
                )
            stuck_list = await find_stuck_projects(session)

        if not stuck_list:
            logger.info("self-heal: no stuck projects found")
            return dispatched
        stuck_list = _coalesce_stuck_projects_for_enqueue(stuck_list)

        pool = await create_pool(_arq_redis_settings(settings))
        for stuck in stuck_list:
            try:
                async with get_server_session() as session:
                    if await _has_active_pipeline_run(session, stuck.project_id):
                        logger.info(
                            "self-heal: skipped slug=%s — active workflow appeared before enqueue",
                            stuck.slug,
                        )
                        continue
                    if await _clear_auto_resumable_generation_gate_pause(
                        session,
                        stuck.project_id,
                    ):
                        await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "self-heal: failed to recheck active workflow for slug=%s",
                    stuck.slug,
                )
                continue
            try:
                task_id = await _requeue_stuck_project(pool, stuck)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "self-heal: failed to enqueue slug=%s reason=%s: %s",
                    stuck.slug,
                    stuck.reason,
                    exc,
                )
                continue
            if task_id is None:
                logger.info(
                    "self-heal: skipped slug=%s — %s job already queued",
                    stuck.slug,
                    stuck.heal_kind,
                )
                continue
            dispatched.append(
                {
                    "slug": stuck.slug,
                    "task_id": task_id,
                    "reason": stuck.reason,
                    "heal_kind": stuck.heal_kind,
                    "stuck_at_chapter": stuck.stuck_at_chapter,
                    "chapters_total": stuck.chapters_total,
                    "chapters_with_draft": stuck.chapters_with_draft,
                }
            )
            logger.info(
                "self-heal: re-queued slug=%s kind=%s reason=%s stuck_at=%s task=%s",
                stuck.slug,
                stuck.heal_kind,
                stuck.reason,
                stuck.stuck_at_chapter,
                task_id,
            )
    finally:
        if pool is not None:
            await pool.aclose()

        # Signal to the web service that worker self-heal has finished its
        # startup scan. Without this marker web's ``auto_resume_zombies``
        # runs the moment Redis becomes reachable — before worker has had
        # time to populate ``arq:job:autowrite:heal:*`` — and treats its
        # empty result as "no owner" → spawns competing threads that
        # collide on row-locks with the heal jobs we're about to enqueue.
        if redis is not None:
            try:
                await redis.set(
                    SELF_HEAL_SCAN_DONE_KEY,
                    str(int(_dt.datetime.now(tz=_dt.timezone.utc).timestamp())),
                    ex=SELF_HEAL_SCAN_DONE_TTL_SECONDS,
                )
            except Exception:  # noqa: BLE001 — marker is advisory
                logger.exception("self-heal: failed to publish scan-done marker")

    return dispatched


async def _wait_for_scan_done(
    redis: Any | None,
    timeout_seconds: int = SELF_HEAL_LOCK_TTL_SECONDS,
) -> None:
    if redis is None:
        return
    deadline = time.monotonic() + max(1, timeout_seconds)
    while time.monotonic() < deadline:
        try:
            if await redis.get(SELF_HEAL_SCAN_DONE_KEY):
                return
        except Exception:  # noqa: BLE001
            logger.exception("self-heal: failed while waiting for scan-done marker")
            return
        await asyncio.sleep(0.5)
    logger.warning("self-heal: timed out waiting for scan-done marker")
