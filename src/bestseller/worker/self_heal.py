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

import datetime as _dt
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from sqlalchemy import and_, func, select, update

if TYPE_CHECKING:  # pragma: no cover — import only for type hints
    from arq.connections import ArqRedis

from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.infra.db.session import get_server_session
from bestseller.settings import AppSettings

ORPHAN_WORKFLOW_TIMEOUT_SECONDS = 30 * 60  # 30 minutes without heartbeat

# Anything active + older than this at worker startup is, by definition,
# a ghost from a prior container that died before updating its row. The
# grace window accounts for rare cases where a job was just enqueued but
# the row write hasn't reached the replica yet.
STARTUP_GRACE_SECONDS = 60

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

logger = logging.getLogger(__name__)


_ACTIVE_STATUSES = frozenset(
    {
        WorkflowStatus.PENDING.value,
        WorkflowStatus.QUEUED.value,
        WorkflowStatus.RUNNING.value,
    }
)

_PIPELINE_WORKFLOW_TYPES = frozenset({"autowrite_pipeline", "project_pipeline"})


@dataclass(frozen=True)
class StuckProject:
    project_id: Any
    slug: str
    reason: str
    stuck_at_chapter: int | None
    chapters_total: int
    chapters_with_draft: int


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

    # Use the MORE PERMISSIVE of the two cutoffs so any row older than
    # either is reaped.
    effective_cutoff = heartbeat_cutoff
    if startup_cutoff is not None and startup_cutoff > effective_cutoff:
        effective_cutoff = startup_cutoff

    result = await session.execute(
        update(WorkflowRunModel)
        .where(
            WorkflowRunModel.status.in_(_ACTIVE_STATUSES),
            WorkflowRunModel.updated_at < effective_cutoff,
        )
        .values(
            status=WorkflowStatus.FAILED.value,
            error_message="reaped by self-heal (abandoned by prior worker)",
        )
    )
    return int(result.rowcount or 0)


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
        active = await session.scalar(
            select(WorkflowRunModel.id)
            .where(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.workflow_type.in_(_PIPELINE_WORKFLOW_TYPES),
                WorkflowRunModel.status.in_(_ACTIVE_STATUSES),
            )
            .limit(1)
        )
        if active is not None:
            continue

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

        if explicit_stuck_ch is not None:
            stuck.append(
                StuckProject(
                    project_id=project.id,
                    slug=project.slug,
                    reason="explicit_stuck_marker",
                    stuck_at_chapter=int(explicit_stuck_ch),
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
                )
            )
            continue

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
            stuck.append(
                StuckProject(
                    project_id=project.id,
                    slug=project.slug,
                    reason="blocked_chapters",
                    stuck_at_chapter=None,
                    chapters_total=int(chapters_total),
                    chapters_with_draft=int(chapters_with_draft),
                )
            )
            continue

        # Under-target: volumes never got planned past a certain point.
        # Only trigger for projects still in a writing state — a project
        # the user explicitly finished or abandoned (``completed`` /
        # ``archived``) should not be auto-resumed.
        target_chapters = int(getattr(project, "target_chapters", 0) or 0)
        status = (getattr(project, "status", None) or "").lower()
        under_target_status = status in {"writing", "planning", "revising", "drafting", ""}
        if (
            target_chapters > 0
            and chapters_total < target_chapters
            and under_target_status
        ):
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


async def _requeue_autowrite(
    pool: "ArqRedis",
    stuck: StuckProject,
) -> str | None:
    """Enqueue a fresh ``run_autowrite_task`` for a stuck project.

    Returns the job id on success, or ``None`` if ARQ already has a
    pending/running job for the same slug (deterministic dedup).
    """
    job_id = _autowrite_heal_job_id(stuck.slug)
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
        return dispatched

    pool: "ArqRedis | None" = None
    try:
        async with get_server_session() as session:
            reaped = await reap_orphan_workflow_runs(
                session, startup_cutoff=startup_cutoff,
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

        pool = await create_pool(_arq_redis_settings(settings))
        for stuck in stuck_list:
            try:
                task_id = await _requeue_autowrite(pool, stuck)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "self-heal: failed to enqueue slug=%s reason=%s: %s",
                    stuck.slug, stuck.reason, exc,
                )
                continue
            if task_id is None:
                logger.info(
                    "self-heal: skipped slug=%s — autowrite job already queued",
                    stuck.slug,
                )
                continue
            dispatched.append(
                {
                    "slug": stuck.slug,
                    "task_id": task_id,
                    "reason": stuck.reason,
                    "stuck_at_chapter": stuck.stuck_at_chapter,
                    "chapters_total": stuck.chapters_total,
                    "chapters_with_draft": stuck.chapters_with_draft,
                }
            )
            logger.info(
                "self-heal: re-queued slug=%s reason=%s stuck_at=%s task=%s",
                stuck.slug, stuck.reason, stuck.stuck_at_chapter, task_id,
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
