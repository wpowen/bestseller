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

Before detection, :func:`reap_orphan_workflow_runs` selects old active rows
and fails only those whose native owner is positively disproved. Age alone,
worker boot, and an unavailable ownership probe never authorize reaping. Without this step the active-pipeline
guard below would treat every zombie row left behind by ``kill -9`` as
legitimate and refuse to re-queue the project.

The scanner is intentionally conservative — projects that still have
a *genuinely* active workflow run (one whose ``updated_at`` was touched
recently) are left alone to avoid duplicate pipelines racing on the
same DB rows.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import datetime as _dt
import hashlib
import json
import logging
import os
import pickle
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
import uuid

from redis.asyncio.client import NEVER_DECODE
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

if TYPE_CHECKING:  # pragma: no cover — import only for type hints
    from arq.connections import ArqRedis

from bestseller.domain.enums import ProjectStatus, WorkflowStatus
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionBudgetModel,
    ProjectModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.infra.db.session import get_server_session
from bestseller.infra.redis_locks import compare_owner_delete
from bestseller.services.gate_registry import (
    gate_continuation_impact,
    project_blocks_all_prose_generation,
    project_resume_is_terminally_blocked,
)
from bestseller.services.production_control import halted_project_ids, load_control_state
from bestseller.services.projects import is_project_delete_tombstoned
from bestseller.services.repair_impact import compute_continuation_readiness
from bestseller.services.retry_ledger import (
    RetryTrigger,
    assert_execution_scope_open,
    evaluate_retry,
    execution_terminal_code,
    generation_gate_budget,
    generation_gate_scope,
    load_chain,
    project_execution_scope,
    store_chain,
)
from bestseller.services.self_heal_repair_progress import (
    STATE_KEY as REPAIR_PROGRESS_STATE_KEY,
)
from bestseller.services.self_heal_repair_progress import (
    collect_repair_progress,
    compare_repair_progress,
)
from bestseller.settings import AppSettings

# Periodic self-heal must not reap legitimate long LLM calls.  A single
# planner attempt can take 15 minutes and the outline repair loop can run
# several attempts before emitting a new workflow-row update. Age thresholds
# only select candidates; boot time cannot establish another executor's death.
ORPHAN_WORKFLOW_TIMEOUT_SECONDS = 3 * 60 * 60
# Heartbeating workflow runs emit a workflow-row update on every scene/step, so
# a gap selects a candidate for independent owner verification. The long 3h timeout above exists for the PLANNING repair loop (multiple
# 15-min planner attempts with no row update); applying it to heartbeating runs
# left a book orphaned for up to 3h after a worker crash before self-heal could
# resume it. ``project_repair`` belongs here too: it writes a DB heartbeat per
# repaired chapter (see worker.tasks._workflow_db_heartbeat), so a stalled repair
# row still needs a verified expired owner before any state change.
ORPHAN_WRITING_WORKFLOW_TIMEOUT_SECONDS = 30 * 60
_WRITING_WORKFLOW_TYPES = frozenset(
    {"project_pipeline", "chapter_pipeline", "scene_pipeline"}
)
_HEARTBEAT_REAP_WORKFLOW_TYPES = _WRITING_WORKFLOW_TYPES | frozenset({"project_repair"})

# 规划类工作流（2026-08-19 起）：planner 每完成一个章纲批次刷新自己的行
# （services.planner 的 outline batch heartbeat）；停止心跳只产生待核验候选，
# 不再需要 3 小时长窗口。真机两次事故都是这条：web 重启杀掉在飞规划、
# worker 没重启⇒启动清理不触发、行以 running 僵在库里阻塞自愈
# （`_has_active_pipeline_run`），书永久卡死。
# 窗口取 45 分钟：单次 planner 调用上限 ~15 分钟、批次含重试，45 分钟无
# 心跳仍需独立 owner 证据；窗口比 3 小时短一个量级。
ORPHAN_PLANNING_WORKFLOW_TIMEOUT_SECONDS = 45 * 60
_PLANNING_HEARTBEAT_REAP_WORKFLOW_TYPES = frozenset(
    {
        "autowrite_pipeline",
        "generate_foundation_plan",
        "generate_novel_plan",
        "generate_volume_plan",
    }
)

# Conception runs in the WEB process, owns no project row yet, and never
# heartbeats — ``updated_at`` stays equal to ``created_at`` for its whole life.
# So neither of the rules above can classify it: the heartbeat windows would
# measure total age rather than liveness, and the startup rules (5s grace) would
# mark a healthy in-flight conception failed whenever a worker restarts alone.
#
# It still needs SOME rule: the web layer only closes the row on the success
# path, so an appeal-gate block or a crash leaks a permanently "running" row —
# and ``uq_conception_workflow_idempotency`` then refuses to retry that same
# attempt_id. Total age only selects candidates; legacy Web owners remain UNKNOWN.
#
# 2h clears a normal conception (~20-30 min) and the worst observed stuck-but-
# alive case (1.5h) with margin.
ORPHAN_CONCEPTION_WORKFLOW_TIMEOUT_SECONDS = 2 * 60 * 60
_CONCEPTION_WORKFLOW_TYPES = frozenset(
    {"conception_initial", "conception_revision"}
)

# Startup grace is diagnostic only. A new worker's boot does not establish
# that another worker or Web executor died.
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
# Worker replicas initialise optional MCP processes at different speeds before
# entering startup self-heal. Their per-process ``startup_cutoff`` timestamps
# can therefore differ by several seconds even though they belong to the same
# Compose restart. Accept a small backward skew when matching the completed
# marker so a slow replica cannot count the same book as another heal attempt.
SELF_HEAL_STARTUP_COHORT_SKEW_SECONDS = 30

# A worker task refreshes its active workflow rows every 60 seconds.  Use a
# generous window here so a live long-running planner remains protected when a
# sibling worker restarts, while a dead owner can still be reaped promptly.
ACTIVE_ARQ_OWNER_HEARTBEAT_GRACE_SECONDS = 5 * 60

# Escalation threshold. A project that self-heal re-queues this many times
# WITHOUT the chapter count increasing is likely hard-blocked (planner schema
# failure, a blocking methodology/planning gate, etc.) rather than transiently
# stuck. Hitting the threshold must escalate to a stronger machine-repair path,
# not a manual-review stop state; autonomous production treats that as another
# repair strategy, not as permission to abandon the book.
MAX_SELF_HEAL_NO_PROGRESS_ATTEMPTS = 5

# ARQ's default job expiry is 24 hours.  A single long-form autowrite job can
# legitimately occupy a worker for longer than that, so startup self-heal jobs
# queued behind it must survive a multi-day backlog instead of expiring before
# they ever start.
SELF_HEAL_JOB_EXPIRES_DAYS = 7

# ARQ can leave ``arq:in-progress:*`` and ``arq:retry:*`` keys behind when a
# worker is rebuilt or killed mid-job. An old queue score is diagnostic only:
# it cannot prove an executor dead. Cleanup must wait for its lease to expire
# and atomically verify the job is no longer queued before reclaiming debris.
STALE_ARQ_IN_PROGRESS_GRACE_SECONDS = 5 * 60

# R24 orphan guard: an ``arq:job:<id>`` key that is in NO queue zset and has
# NO in-progress lock will never be dispatched by ARQ, yet its mere existence
# dedup-rejects every fresh enqueue of the same deterministic id — the project
# never retries (observed after a deploy-restart window left a half-enqueued
# ``repair:heal`` key; manual key deletion was the only unlock). A bare key
# older than this cap is treated as an orphan and cleared before re-enqueue.
# The generous default (90 min) keeps a genuinely just-enqueued job safe even
# under heavy queue backlog.
SELF_HEAL_ORPHAN_JOB_MAX_AGE_SECONDS = int(
    os.environ.get("SELF_HEAL_ORPHAN_JOB_MAX_AGE_SECONDS", str(90 * 60))
)

# A freshly-created quickstart project can have zero/partial chapter rows for
# a few minutes while the web autowrite thread is still creating its first
# workflow row. Treating that as ``under_target_chapters`` immediately spawns a
# duplicate autowrite job that races the real one through planning.
# 5min (was 15min): the ``_has_active_pipeline_run`` check is the PRIMARY guard
# against racing an in-flight pipeline; this grace is only a secondary "brand new
# project still being created" buffer. 15min left an actively-writing book idle
# for up to 15min between self-heal bursts, badly slowing autonomous completion.
UNDER_TARGET_SELF_HEAL_GRACE_SECONDS = 5 * 60

# Project repair writes a DB heartbeat through worker.tasks while it is alive,
# so a stale running repair row can be safely reaped by the periodic orphan
# scanner. A project_repair run that has already reached MACHINE_BLOCKED should
# only suppress self-heal briefly. Older blocked rows are historical evidence,
# not an active handoff; leaving them indefinite made blocked projects stall
# forever.
# Only suppress duplicate repair dispatches long enough for the just-finished
# worker state to settle. A machine_blocked repair row is historical evidence,
# not an acceptable long-lived stop state for autonomous recovery.
WAITING_REPAIR_SUPPRESSION_SECONDS = 60
WRITE_SAFETY_REPAIR_RECOVERY_SECONDS = 15 * 60
WRITE_SAFETY_REPAIR_MAX_RECOVERY_SECONDS = 45 * 60
SELF_HEAL_PENDING_REWRITE_TASK_LIMIT = 25

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
# How many times ONE unchanged generation-gate failure may be auto-resumed
# before the project stays paused for a human.
#
# Without a budget this path spins forever: the gate fails, the pause is
# cleared after the cooldown, the identical work reruns, the same gate fails.
# 《仇人膝上养帝王》 burned 118 LLM calls / ~880k tokens in 2.5 hours that way
# (2026-07-25) without producing one chapter — regenerating eight identical
# copies of outline batch 1-3 while batch 4-6 kept failing the same semantic
# check, with concurrent retries racing into PlanningConflictError.
#
# Small on purpose: a gate that rejected the same artifact three times will
# reject the fourth. Transient faults (a timed-out judge, a truncated
# completion) clear well inside this budget; a genuinely unsatisfiable gate
# stops and surfaces instead of quietly spending the user's tokens.
GENERATION_GATE_MAX_AUTO_RESUMES = int(
    os.getenv("BESTSELLER_GENERATION_GATE_MAX_AUTO_RESUMES", "3")
)
GENERATION_GATE_RESUME_COOLDOWN_SECONDS = int(
    os.getenv("BESTSELLER_GENERATION_GATE_RESUME_COOLDOWN_SECONDS", "60")
)
TEMPORARY_PLANNING_THROTTLE_REASON = "temporary_planning_throttle_for_new_books"
_SELF_HEAL_AUTO_RESUMABLE_PAUSE_REASONS = frozenset(
    {
        "self_heal_no_progress_giveup",
        "self_heal_no_progress_machine_repair",
    }
)
_AUTO_RESUMABLE_GENERATION_GATE_REASONS = frozenset(
    {
        "scene_plan_richness_gate_failed",
        "story_bible_gate_failed",
        TEMPORARY_PLANNING_THROTTLE_REASON,
        "volume_outline_gate_failed",
    }
)
_LOCAL_REWRITE_TRIGGER_TYPES = frozenset(
    {
        "scene_review",
        "chapter_review",
        "chapter_auto_repair",
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
        "generate_foundation_plan",
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
_SELF_HEAL_CONTINUATION_BLOCKING_WORKFLOW_TYPES = frozenset(
    {
        "autowrite_pipeline",
        "generate_foundation_plan",
        "generate_novel_plan",
        "generate_volume_plan",
        "project_pipeline",
        "materialize_story_bible",
        "materialize_chapter_outline_batch",
        "materialize_narrative_graph",
        "materialize_narrative_tree",
    }
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
    progress_fingerprint: str | None = None
    # Ordered quality rank for planner-only recovery.  A changed error label is
    # not progress; only a strictly better score/blocker profile is.
    progress_rank: tuple[int, int, int, int] | None = None
    configured_window: bool = False


_AUTO_OUTLINE_REPLAN_REASONS = frozenset(
    {
        "outline_semantic_gate_failed",
        "outline_replan_in_progress",
        "volume_outline_gate_failed",
        "commercial_planning_readiness_gate_failed",
    }
)

# slug -> the pause reason already announced, so a project no lane can own is
# reported once at WARNING instead of once a minute at INFO.
_INERT_PROJECTS_ANNOUNCED: dict[str, str] = {}


def _project_requires_auto_outline_replan(project: ProjectModel) -> bool:
    """Whether a planning failure is safe for the bounded planner-only lane."""

    metadata = getattr(project, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        return False
    if metadata.get("outline_replan_auto_retry_exhausted"):
        return False
    # The project status and metadata are persisted by separate recovery
    # boundaries on some failure paths.  During that narrow window the status
    # can already be ``needs_replan`` while the detailed gate report has not
    # landed yet.  Treat the status itself as the safe planner-only signal;
    # ``find_stuck_projects`` still requires zero committed drafts before it
    # dispatches this lane.
    if str(getattr(project, "status", "") or "").strip().lower() == "needs_replan":
        return True
    # The no-progress watchdog may replace ``production_pause_reason`` with
    # ``self_heal_no_actionable_progress`` while the authoritative planning
    # failure remains in ``last_generation_gate_reason``.  Treating the first
    # non-empty reason as exclusive stranded zero-draft books even though the
    # dedicated replan marker was still present.
    if metadata.get("outline_replan_required"):
        return True
    reasons = {
        str(metadata.get(key) or "").strip().lower().split(":", 1)[0]
        for key in ("production_pause_reason", "last_generation_gate_reason")
    }
    if reasons & _AUTO_OUTLINE_REPLAN_REASONS:
        return True
    return bool(
        metadata.get("outline_replan_in_progress")
        or (
            metadata.get("outline_semantic_gate_status") == "needs_replan"
            and metadata.get("outline_semantic_gate_report")
        )
    )


def _outline_replan_progress_fingerprint(project: ProjectModel) -> str:
    """Fingerprint the blocker, not mutable chapter counts, for bounded retries."""

    metadata = getattr(project, "metadata_json", None) or {}
    report = metadata.get("outline_semantic_gate_report")
    findings = report.get("findings") if isinstance(report, dict) else []
    adjudication = report.get("llm_adjudication") if isinstance(report, dict) else None
    commercial_failure = metadata.get("outline_commercial_last_failure")
    compact_findings = []
    for finding in findings if isinstance(findings, list) else []:
        if not isinstance(finding, dict):
            continue
        compact_findings.append(
            {
                "code": finding.get("code"),
                "chapter": finding.get("chapter"),
                "evidence": finding.get("evidence"),
            }
        )
    payload = {
        "reason": metadata.get("production_pause_reason")
        or metadata.get("last_generation_gate_reason"),
        "error": str(metadata.get("last_generation_gate_error") or "")[:1000],
        "findings": compact_findings,
        # Retain score / issue detail for diagnostics.  The monotonic rank
        # helper below, rather than this volatile hash, decides whether the
        # generic chapter-count watchdog observed real planning progress.
        "llm_overall_score": (
            adjudication.get("overall_score")
            if isinstance(adjudication, dict)
            else None
        ),
        "llm_issue_codes": sorted(
            str(item.get("code"))
            for item in (
                adjudication.get("issues", [])
                if isinstance(adjudication, dict)
                else []
            )
            if isinstance(item, dict) and item.get("code")
        ),
        "promotion_allowed": (
            report.get("promotion_allowed") if isinstance(report, dict) else None
        ),
        # Progressive volume-outline rejection is persisted separately from
        # ``outline_semantic_gate_report``. Ignoring it kept the fingerprint
        # constant even when the LLM score improved from 0.55 to 0.825, so the
        # generic watchdog abandoned a visibly improving replan on attempt 5.
        "commercial_overall_score": (
            commercial_failure.get("overall_score")
            if isinstance(commercial_failure, dict)
            else None
        ),
        "commercial_blocking_codes": sorted(
            str(code)
            for code in (
                commercial_failure.get("blocking_codes", [])
                if isinstance(commercial_failure, dict)
                else []
            )
            if code
        ),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:20]
    return f"outline_replan:{digest}"


def _outline_replan_progress_rank(
    project: ProjectModel,
) -> tuple[int, int, int, int]:
    """Return a monotonic quality rank for bounded outline recovery.

    Exact blocker text remains useful in the diagnostic fingerprint, but it is
    too volatile to prove progress: one failed generation can merely rename a
    problem while keeping (or lowering) the judge score.  Rank candidates by
    the commercial judge first, then its blocker count, followed by the
    architecture judge and its issue count.  Higher tuples are better.
    """

    metadata = getattr(project, "metadata_json", None) or {}
    report = metadata.get("outline_semantic_gate_report")
    adjudication = report.get("llm_adjudication") if isinstance(report, dict) else None
    commercial_failure = metadata.get("outline_commercial_last_failure")

    def _score(value: Any) -> int:
        try:
            return int(round(float(value) * 10_000))
        except (TypeError, ValueError):
            return -1

    commercial_score = None
    commercial_codes: Any = []
    if isinstance(commercial_failure, dict):
        # The final repair sample may regress after an earlier round produced a
        # much stronger candidate. Planner persistence deliberately keeps that
        # best failed baseline for the next retry; convergence accounting must
        # rank the retained candidate, not the last (possibly worse) sample.
        commercial_score = commercial_failure.get(
            "recovery_baseline_score",
            commercial_failure.get("overall_score"),
        )
        commercial_codes = commercial_failure.get(
            "recovery_blocking_codes",
            commercial_failure.get("blocking_codes", []),
        )
    semantic_issues = (
        adjudication.get("issues", []) if isinstance(adjudication, dict) else []
    )
    return (
        _score(commercial_score),
        -len(commercial_codes) if isinstance(commercial_codes, list) else -10_000,
        _score(
            adjudication.get("overall_score")
            if isinstance(adjudication, dict)
            else None
        ),
        -len(semantic_issues) if isinstance(semantic_issues, list) else -10_000,
    )


def _normalize_progress_rank(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    try:
        return tuple(int(part) for part in value)
    except (TypeError, ValueError):
        return None


async def _workflow_owner_state(session, redis, run, protected_project_ids):
    """LIVE / DEAD / UNKNOWN, using native owner identity, never row age alone."""
    if run.project_id in protected_project_ids:
        return "LIVE"
    if redis is None:
        return "UNKNOWN"
    project = await session.get(ProjectModel, run.project_id) if run.project_id else None
    if project is None:
        return "UNKNOWN"  # A project-less Web/legacy owner cannot be disproved here.
    metadata = run.metadata_json or {}
    parent_id = metadata.get("parent_workflow_run_id")
    if parent_id is not None:
        try:
            parent = await session.get(WorkflowRunModel, uuid.UUID(str(parent_id)), populate_existing=True)
        except (ValueError, TypeError, AttributeError):
            return "UNKNOWN"
        if parent is None or parent.project_id != run.project_id:
            return "UNKNOWN"
        if parent.status in _ACTIVE_STATUSES:
            return "LIVE"
    try:
        from bestseller.worker.tasks import _pipeline_live_key
        owner = await redis.get(_pipeline_live_key(project.slug))
        ttl = await redis.pttl(_pipeline_live_key(project.slug))
        if owner is not None:
            return "LIVE" if ttl > 0 else "UNKNOWN"
        if ttl != -2:
            return "UNKNOWN"
        arq = await _active_arq_project_slugs(redis)
        if arq is None:
            return "UNKNOWN"
        if project.slug in arq:
            return "LIVE"
    except Exception:
        logger.warning("self-heal: owner lease probe unavailable", exc_info=True)
        return "UNKNOWN"
    # Existing native continuation owners are UUID lease tokens. Missing or
    # unknown legacy identities do not become dead merely because Redis is empty.
    token = metadata.get("execution_owner")
    try:
        if not isinstance(token, str) or uuid.UUID(token).version != 4:
            return "UNKNOWN"
    except (ValueError, TypeError, AttributeError):
        return "UNKNOWN"
    if metadata.get("execution_scope_id") != project_execution_scope(project):
        return "UNKNOWN"
    if metadata.get("native_pipeline_owner") != {
        "schema": "acquired-pipeline-owner.v1", "project_id": str(project.id),
        "project_slug": project.slug, "execution_scope_id": project_execution_scope(project),
        "token": token, "lease_key": _pipeline_live_key(project.slug),
    }:
        return "UNKNOWN"
    return "DEAD"


async def reap_orphan_workflow_runs(
    session: Any,
    timeout_seconds: int = ORPHAN_WORKFLOW_TIMEOUT_SECONDS,
    startup_cutoff: _dt.datetime | None = None,
    protected_project_ids: set[Any] | None = None,
    *, redis: Any | None = None,
) -> int:
    """Age selects candidates; only positively disproved native owners are reaped.

    Boot time is diagnostic, never proof that another Web/worker process died.
    All branches share fresh heartbeat, lease and same-project lineage guards.
    Unknown legacy ownership remains untouched. Caller commits the transaction.
    """
    if redis is None:
        return 0
    now = _dt.datetime.now(_dt.UTC)
    protected_project_ids = protected_project_ids or set()
    heartbeat_cutoff = now - _dt.timedelta(seconds=timeout_seconds)
    writing_cutoff = now - _dt.timedelta(seconds=ORPHAN_WRITING_WORKFLOW_TIMEOUT_SECONDS)
    planning_cutoff = now - _dt.timedelta(seconds=ORPHAN_PLANNING_WORKFLOW_TIMEOUT_SECONDS)
    conception_cutoff = now - _dt.timedelta(seconds=ORPHAN_CONCEPTION_WORKFLOW_TIMEOUT_SECONDS)
    candidates = list(await session.scalars(select(WorkflowRunModel).where(
        WorkflowRunModel.status.in_(_ACTIVE_STATUSES),
        or_(and_(WorkflowRunModel.workflow_type.in_(_REAPABLE_WORKFLOW_TYPES), or_(
                WorkflowRunModel.updated_at < heartbeat_cutoff,
                and_(WorkflowRunModel.workflow_type.in_(_HEARTBEAT_REAP_WORKFLOW_TYPES),
                     WorkflowRunModel.updated_at < writing_cutoff),
                and_(WorkflowRunModel.workflow_type.in_(_PLANNING_HEARTBEAT_REAP_WORKFLOW_TYPES),
                     WorkflowRunModel.updated_at < planning_cutoff))),
            and_(WorkflowRunModel.workflow_type.in_(_CONCEPTION_WORKFLOW_TYPES),
                 WorkflowRunModel.created_at < conception_cutoff),
            WorkflowRunModel.metadata_json["parent_workflow_run_id"].as_string().is_not(None)))))
    reaped = 0
    for candidate in candidates:
        # Skip locks owned by an active writer. Refresh all state AFTER locking;
        # a stale identity map or pre-lock snapshot must not close a fresh row.
        run = await session.scalar(select(WorkflowRunModel).where(
            WorkflowRunModel.id == candidate.id).with_for_update(skip_locked=True)
            .execution_options(populate_existing=True, autoflush=False))
        if run is None or run.status not in _ACTIVE_STATUSES:
            continue
        updated = run.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=_dt.UTC)
        if updated >= now - _dt.timedelta(seconds=ACTIVE_ARQ_OWNER_HEARTBEAT_GRACE_SECONDS):
            continue
        if await _workflow_owner_state(session, redis, run, protected_project_ids) != "DEAD":
            continue
        run.status = WorkflowStatus.FAILED.value
        run.error_message = _ORPHAN_ERROR_MESSAGE
        run.metadata_json = {**(run.metadata_json or {}), "orphan_recovery": {
            "schema": "native-owner-expiry.v1", "execution_owner": run.metadata_json["execution_owner"],
            "observed": "pipeline_lease_absent_arq_probe_complete", "at": now.isoformat()}}
        reaped += 1
    return reaped


async def _active_arq_project_slugs(redis: Any | None) -> set[str] | None:
    """Return project slugs owned by currently in-progress ARQ jobs.

    Worker startup self-heal runs while other workers may already be executing
    long autowrite jobs.  Those jobs have a durable ``arq:in-progress:*`` key,
    but their workflow rows can look pre-boot from the new worker's point of
    view.  Protecting the in-progress job's project prevents startup reaping
    from marking legitimate live work as abandoned.
    """
    if redis is None:
        return None

    slugs: set[str] = set()
    job_ids: set[str] = set()
    try:
        scan_iter = getattr(redis, "scan_iter", None)
        patterns = ("arq:in-progress:*", "arq:job:*", "arq:retry:*", "bestseller:pipeline-live:*")
        if scan_iter is not None:
            keys = []
            for pattern in patterns:
                keys.extend([key async for key in scan_iter(match=pattern)])
        else:
            keys = [
                key
                for pattern in patterns
                for key in await redis.keys(pattern)
            ]
    except Exception:  # noqa: BLE001
        logger.exception("self-heal: failed to scan ARQ in-progress jobs")
        return None

    for key in keys:
        key_text = key.decode() if isinstance(key, bytes) else str(key)
        if key_text.startswith("bestseller:pipeline-live:"):
            slugs.add(key_text.removeprefix("bestseller:pipeline-live:"))
            continue
        if key_text.startswith("arq:in-progress:"):
            job_id = key_text.removeprefix("arq:in-progress:")
        elif key_text.startswith("arq:job:"):
            job_id = key_text.removeprefix("arq:job:")
        elif key_text.startswith("arq:retry:"):
            job_id = key_text.removeprefix("arq:retry:")
        else:
            continue
        job_ids.add(job_id)

    for job_id in job_ids:
        try:
            raw = await _redis_get_bytes(redis, f"arq:job:{job_id}")
        except Exception:  # noqa: BLE001
            logger.exception("self-heal: failed to read ARQ job %s", job_id)
            return None
        if not raw:
            return None
        try:
            job_payload = pickle.loads(raw)
        except Exception:  # noqa: BLE001
            logger.exception("self-heal: failed to decode ARQ job %s", job_id)
            return None
        kwargs = job_payload.get("k") if isinstance(job_payload, dict) else None
        if (isinstance(job_payload, dict)
                and set(job_payload) == {"t", "f", "a", "k", "et"}
                and job_payload.get("f") == "run_self_heal_task"
                and type(job_payload["a"]) is tuple and job_payload["a"] == ()
                and type(kwargs) is dict and not kwargs
                and (job_payload["t"] is None or type(job_payload["t"]) is int and job_payload["t"] > 0)
                and type(job_payload["et"]) is int and job_payload["et"] > 0):
            # The native periodic observer has no project payload. Its own ARQ
            # row is not an unknown writer; all other unknown jobs still block.
            continue
        payload = kwargs.get("payload") if isinstance(kwargs, dict) else None
        if not isinstance(payload, dict):
            return None
        slug = payload.get("project_slug")
        if isinstance(slug, str) and slug:
            slugs.add(slug)
        else:
            return None

    return slugs


async def _redis_get_bytes(redis: Any, key: str) -> bytes | None:
    execute_command = getattr(redis, "execute_command", None)
    if execute_command is not None:
        return await execute_command("GET", key, **{NEVER_DECODE: True})
    return await redis.get(key)


async def _resolve_project_ids_for_slugs(session: Any, slugs: set[str]) -> set[Any]:
    if not slugs:
        return set()
    return set(await session.scalars(select(ProjectModel.id).where(ProjectModel.slug.in_(sorted(slugs)))))


async def find_stuck_projects(session: Any) -> list[StuckProject]:
    """Return projects that need self-heal and have no active continuation run.

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

    # Operator intent lives in its own table precisely so this check cannot be
    # defeated by the pipeline rewriting ``projects.metadata`` wholesale. Read
    # it fresh on every sweep: a stop issued since the last scan must take
    # effect on this one.
    halted = await halted_project_ids(session)

    for project in projects:
        if _project_is_finished(project):
            continue
        # Pending manuscripts own a separate reference-bound continuation lane.
        # Never restart an adopted-window run at chapter 1 for this mode.
        from bestseller.services.draft_continuity import draft_mode_enabled
        try:
            if await draft_mode_enabled(session, project_id=project.id, project=project):
                continue
        except ValueError:
            logger.warning('self-heal: invalid pending-mode authority; no queue for %s', project.slug)
            continue
        from bestseller.services.creation_configuration import read_window
        try:
            configured_window = read_window(project)
        except Exception:
            logger.warning("self-heal: invalid window for %s; no queue", project.slug)
            continue
        if configured_window is not None:
            from bestseller.services.execution_windows import window_progress
            try:
                if (await window_progress(session, project))["window_complete"]:
                    continue
            except Exception:
                logger.warning("self-heal: window evidence unavailable for %s; no queue", project.slug)
                continue
        if project.id in halted:
            continue
        if execution_terminal_code(project.metadata_json or {}):
            continue
        try:
            await assert_execution_scope_open(session, project_execution_scope(project))
        except RuntimeError as error:
            if not execution_terminal_code(error):
                raise
            continue
        if _project_is_focus_paused(project):
            continue
        # Skip only when a forward-writing/planning run is active. A repair run
        # may coexist with continuation when all current blocks are local prose
        # defects; treating project_repair/scene_pipeline as globally active
        # starves the intended repair+new-chapter parallelism.
        if await _has_active_continuation_pipeline_run(session, project.id):
            continue
        if configured_window is not None:
            if project_blocks_all_prose_generation(project):
                continue
            from bestseller.services.execution_windows import assert_window_runnable
            try:
                await assert_window_runnable(session, project)
            except ValueError:
                continue
            state = await window_progress(session, project)
            if not state["pending"]:
                continue
            stuck.append(StuckProject(project_id=project.id, slug=project.slug,
                reason="execution_window_incomplete", stuck_at_chapter=min(state["pending"]),
                chapters_total=len(state["adopted"]), chapters_with_draft=len(state["adopted"]),
                heal_kind="autowrite", configured_window=True))
            continue

        auto_resumable_generation_gate = _project_has_stale_auto_resumable_generation_gate(
            project
        )

        meta = project.metadata_json or {}
        explicit_stuck_ch = meta.get("stuck_at_chapter")
        now = _dt.datetime.now(_dt.UTC)

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

        # A rejected architecture must never reach a prose worker.  It does,
        # however, remain actionable for a dedicated planner owner while no
        # prose has been committed.  The older blanket ``continue`` here
        # accidentally disabled the self-heal path itself and left every
        # ``needs_replan`` project permanently inert.
        if (
            _project_requires_auto_outline_replan(project)
            and int(chapters_with_draft) == 0
        ):
            if meta.get("outline_replan_retry_exhausted"):
                logger.info(
                    "self-heal: skipped slug=%s — outline replan retries exhausted",
                    project.slug,
                )
                continue
            next_retry = _metadata_datetime(
                dict(meta),
                "outline_replan_next_retry_at",
            )
            if next_retry is not None and next_retry > now:
                logger.info(
                    "self-heal: skipped slug=%s — outline replan backoff until %s",
                    project.slug,
                    next_retry.isoformat(),
                )
                continue
            # Also cover deploy-window legacy state where the commercial gate
            # reason was persisted before ``outline_replan_required`` itself.
            # Requiring the hard-block marker first routed that book straight
            # back into the same project pipeline instead of upgrading repair.
            fingerprint = _outline_replan_progress_fingerprint(project)
            progress_rank = _outline_replan_progress_rank(project)
            # Candidate discovery cannot decide whether a native repair landed.
            # The locked dispatch check below observes committed source proofs
            # before applying the existing watchdog; rank/labels grant nothing.
            stuck.append(
                StuckProject(
                    project_id=project.id,
                    slug=project.slug,
                    reason="outline_replan_required",
                    stuck_at_chapter=None,
                    chapters_total=int(chapters_total),
                    chapters_with_draft=0,
                    heal_kind="outline_replan",
                    progress_fingerprint=fingerprint,
                    progress_rank=progress_rank,
                )
            )
            continue

        if project_blocks_all_prose_generation(project):
            # Genuine dead end, and it must say so out loud. The replan lane
            # above only runs while zero drafts are committed and this lane
            # refuses every project whose architecture is rejected, so a book
            # that lands here is owned by nobody. It used to leave nothing but
            # an INFO line once a minute — the same "the book just vanished"
            # failure mode as a task whose error is invisible in the dashboard.
            # Persist why it is inert so the UI can show it, and say it at
            # WARNING once rather than whispering it forever.
            #
            # The reason is not written back to ``projects.metadata`` on
            # purpose: a running pipeline rewrites that JSONB wholesale from its
            # own stale copy, so an external write here would be silently
            # clobbered. ``production_pause_reason`` already records the cause.
            reason = (
                str(meta.get("production_pause_reason") or "").strip()
                or "planning_architecture_rejected"
            )
            if _INERT_PROJECTS_ANNOUNCED.get(project.slug) != reason:
                _INERT_PROJECTS_ANNOUNCED[project.slug] = reason
                logger.warning(
                    "self-heal: slug=%s is inert — outline replan is not safely "
                    "auto-runnable after prose was committed (reason=%s); "
                    "no lane owns this project, an operator must decide whether "
                    "to replan or accept the written chapters",
                    project.slug,
                    reason,
                )
            continue

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
        local_repair_chapters: tuple[int, ...] = ()
        # A blocked chapter only forces repair-FIRST (starving continuation)
        # when its defect is *structural* — i.e. it corrupts the canon /
        # continuity snapshot that later chapters inherit. A purely *local*
        # block (opening tension, length, style) is confined to that chapter's
        # prose, so new-chapter writing proceeds in parallel and the local
        # repair is drained once writing has caught up to the plan. See
        # ``services.repair_impact`` (青囊不语问阴阳 looped ch1 opening repair
        # forever while later chapters waited).
        local_repair_pending = False
        if blocked_chapters > 0:
            readiness = await compute_continuation_readiness(session, project.id)
            if not readiness.can_continue:
                if await _blocked_chapters_have_scene_machine_blocker(
                    session, project.id
                ):
                    logger.info(
                        "self-heal: skipped slug=%s — scene repair already reached machine_blocked",
                        project.slug,
                    )
                    continue
                if await _blocked_chapters_have_recent_waiting_repair(
                    session, project.id
                ):
                    logger.info(
                        "self-heal: skipped slug=%s — blocked chapters already reached machine_blocked repair",
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
                        progress_fingerprint=(
                            "repair:structural:"
                            + ",".join(str(number) for number in readiness.blocking_chapters)
                        ),
                    )
                )
                continue
            if not readiness.local_blocked_chapters:
                # Nothing left to repair — which is not the same as nothing
                # left to do. ``continue`` here skipped the project outright,
                # so the continuation lanes below (under-target writing) never
                # got a turn: a 30-chapter book whose first 8 chapters had all
                # settled as ``quality_debt`` reported "no actionable repair"
                # every five minutes and never wrote chapter 9
                # (2026-08-03, xianxia-upgrade-1785697772). We are already past
                # the ``can_continue`` check, so writing is explicitly allowed.
                logger.info(
                    "self-heal: slug=%s has only terminal quality-debt chapters; "
                    "no repair to do — continuation lanes still apply",
                    project.slug,
                )
                local_repair_pending = False
            else:
                # Only local-quality blocks remain — do not starve continuation.
                local_repair_pending = True
                local_repair_chapters = readiness.local_blocked_chapters
                logger.info(
                    "self-heal: slug=%s has %d local-quality block(s) — writing "
                    "continues in parallel (%s)",
                    project.slug,
                    len(readiness.local_blocked_chapters),
                    readiness.reason,
                )

        pending_rewrite_tasks = await _pending_rewrite_task_count(session, project.id)
        if pending_rewrite_tasks > 0:
            if await _pending_rewrite_tasks_block_continuation(session, project.id):
                if await _blocked_chapters_have_scene_machine_blocker(
                    session, project.id
                ):
                    logger.info(
                        "self-heal: skipped slug=%s — pending scene rewrite tasks belong to machine_blocked scene repair",
                        project.slug,
                    )
                    continue
                stuck.append(
                    StuckProject(
                        project_id=project.id,
                        slug=project.slug,
                        reason="pending_rewrite_tasks",
                        stuck_at_chapter=None,
                        chapters_total=int(chapters_total),
                        chapters_with_draft=int(chapters_with_draft),
                        heal_kind="repair",
                    )
                )
                continue
            # Pending rewrite tasks are all local-gate polish — let writing run.
            local_repair_pending = True

        if auto_resumable_generation_gate:
            stuck.append(
                StuckProject(
                    project_id=project.id,
                    slug=project.slug,
                    reason="generation_gate_auto_retry_needed",
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
        # repair heal job. Pending rewrite tasks are also repair work, not a
        # continuation blocker; check them above so repair-gated projects do not
        # sit indefinitely with queued repairs that no worker consumes.
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
            if _project_is_recent_for_under_target_heal(project):
                logger.info(
                    "self-heal: skipped slug=%s — project is too recent for under-target recovery",
                    project.slug,
                )
                continue
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
            continue

        # Drain local-quality repairs only once forward writing has caught up to
        # the plan (no missing drafts, not under target). Continuation always
        # wins while there are new chapters to write; local repair is the
        # lower-priority tail — this is what makes repair and new-chapter
        # writing "分别去做" without ever starving forward progress.
        if local_repair_pending:
            if await _blocked_chapters_have_scene_machine_blocker(session, project.id):
                continue
            stuck.append(
                StuckProject(
                    project_id=project.id,
                    slug=project.slug,
                    reason="local_quality_repair_drain",
                    stuck_at_chapter=None,
                    chapters_total=int(chapters_total),
                    chapters_with_draft=int(chapters_with_draft),
                    heal_kind="repair",
                    progress_fingerprint=(
                        "repair:local:"
                        + ",".join(str(number) for number in local_repair_chapters)
                        + f":pending_tasks={pending_rewrite_tasks}"
                    ),
                )
            )
            continue

    return stuck


def _project_is_finished(project: ProjectModel) -> bool:
    """书已封档——自愈不许把它捞回来。

    2026-08-24 真机（书9）：50/50 章、50 章有在架稿、status=completed 的书被
    每 5 分钟捞一次，指纹恒为 ``repair|pending_rewrite_tasks||50|50``，20 次
    无进展后被盖上 requires_human_review + production_pause_reason。

    这条政策此前只落在 under_target 那一条分支上（那里的注释已经写着
    completed / archived 都不该自动续跑），入口只挡了 archived。挂在扫描
    入口 = 所有分支的汇合点，不给自己留绕行路。

    例外：``completion_export_error`` 在场时放行 —— 收尾自己写着「下一次
    结算重试导出」，堵死它等于让导出失败的书永远导不出来。归档没有这条
    例外：归档是用户的意思。
    """

    from bestseller.services.pipelines import project_awaits_concept_approval

    metadata = getattr(project, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    if _project_is_archived(project):
        return True
    # 用户用 stop_after_conception 建的书在等他自己点头。自愈按
    # under_target_chapters 捞起来开写，等于框架替用户按下「同意」
    # （2026-08-24 真机当场抓到，见 test_self_heal_never_approves_a_concept_for_the_user）。
    if project_awaits_concept_approval(project):
        return True
    if (getattr(project, "status", None) or "").lower() != ProjectStatus.COMPLETED.value:
        return False
    return not str(metadata.get("completion_export_error") or "").strip()


def _project_is_archived(project: ProjectModel) -> bool:
    metadata = getattr(project, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    status = (getattr(project, "status", None) or "").lower()
    return status == ProjectStatus.ARCHIVED.value or bool(metadata.get("library_archived"))


def _project_is_focus_paused(project: ProjectModel) -> bool:
    metadata = getattr(project, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        return False
    if metadata.get("self_heal_suppressed"):
        return True
    focus_pause = metadata.get("focus_pause")
    reason = str(metadata.get("production_pause_reason") or "").strip()
    if isinstance(focus_pause, dict):
        reason = str(focus_pause.get("reason") or reason).strip()
    return reason.startswith("focus_")


def _project_is_recent_for_under_target_heal(project: ProjectModel) -> bool:
    touched_at = _ensure_utc(getattr(project, "updated_at", None)) or _ensure_utc(
        getattr(project, "created_at", None)
    )
    if touched_at is None:
        return False
    cutoff = _dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(
        seconds=UNDER_TARGET_SELF_HEAL_GRACE_SECONDS,
    )
    return touched_at >= cutoff


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


async def _has_active_continuation_pipeline_run(session: Any, project_id: Any) -> bool:
    active = await session.scalar(
        select(WorkflowRunModel.id)
        .where(
            WorkflowRunModel.project_id == project_id,
            WorkflowRunModel.workflow_type.in_(
                _SELF_HEAL_CONTINUATION_BLOCKING_WORKFLOW_TYPES
            ),
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
            WorkflowRunModel.status == WorkflowStatus.MACHINE_BLOCKED.value,
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


async def _blocked_chapters_have_scene_machine_blocker(
    session: Any,
    project_id: Any,
) -> bool:
    """True when a blocked chapter just exhausted bounded scene repair.

    ``scene_rewrite_stalled_blocked`` and ``scene_machine_repair_required`` are
    short-lived duplicate-suppression states: the scene pipeline tried its
    bounded machine repairs and still could not pass.  Self-heal should not
    replay the same scene repair immediately, but an old machine-blocked row is
    evidence for another autonomous repair pass, not a permanent stop.
    """
    latest_scene_blocker_update = await session.scalar(
        select(func.max(WorkflowRunModel.updated_at)).where(
            WorkflowRunModel.project_id == project_id,
            WorkflowRunModel.status == WorkflowStatus.MACHINE_BLOCKED.value,
            WorkflowRunModel.current_step.in_(
                [
                    "scene_machine_repair_required",
                    "scene_rewrite_stalled_blocked",
                ]
            ),
        )
    )
    latest_scene_blocker_update = _ensure_utc(latest_scene_blocker_update)
    if latest_scene_blocker_update is None:
        return False
    if latest_scene_blocker_update < _dt.datetime.now(_dt.UTC) - _dt.timedelta(
        seconds=WAITING_REPAIR_SUPPRESSION_SECONDS
    ):
        return False

    latest_blocked_update = await session.scalar(
        select(func.max(ChapterModel.updated_at)).where(
            ChapterModel.project_id == project_id,
            ChapterModel.production_state == "blocked",
        )
    )
    latest_blocked_update = _ensure_utc(latest_blocked_update)
    if latest_blocked_update is None:
        return True
    return latest_scene_blocker_update >= latest_blocked_update


async def _pending_rewrite_task_count(session: Any, project_id: Any) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(RewriteTaskModel)
        .where(
            RewriteTaskModel.project_id == project_id,
            RewriteTaskModel.status.in_(["pending", "queued"]),
        )
    )
    return int(count or 0)


async def _pending_rewrite_tasks_block_continuation(
    session: Any,
    project_id: Any,
) -> bool:
    """True when a pending rewrite task is structural (must gate writing).

    A rewrite task triggered by a *local* quality gate (e.g. the opening gate)
    only polishes its own chapter's prose, so it must not stall new-chapter
    writing. Unknown / non-gate trigger types resolve to ``"structural"`` via
    :func:`gate_continuation_impact`, keeping the conservative default.
    """

    rows = await session.scalars(
        select(RewriteTaskModel).where(
            RewriteTaskModel.project_id == project_id,
            RewriteTaskModel.status.in_(["pending", "queued"]),
        )
    )
    for task in rows:
        trigger = str(getattr(task, "trigger_type", "") or "")
        if trigger in _LOCAL_REWRITE_TRIGGER_TYPES:
            continue
        if gate_continuation_impact(trigger) == "structural":
            return True
    return False


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


def _project_self_heal_abandoned(project: ProjectModel) -> bool:
    """Compatibility shim for old ``self_heal_abandoned`` metadata.

    A terminal no-progress marker only applies to the exact blocker fingerprint
    that produced it.  A changed fingerprint is new machine work and resumes
    automatically; legacy markers without a fingerprint remain diagnostic only.
    """
    metadata = getattr(project, "metadata_json", None)
    if not isinstance(metadata, dict):
        return False
    return bool(
        metadata.get("self_heal_abandoned")
        and metadata.get("self_heal_abandoned_progress_fingerprint")
    )


def _compute_heal_progress_state(
    metadata: dict[str, Any],
    chapters_total: int,
    *,
    chapters_with_draft: int | None = None,
    progress_fingerprint: str | None = None,
    progress_rank: tuple[int, ...] | None = None,
    repair_progress_snapshot: dict[str, Any] | None = None,
    consumed_repair_effect_keys: frozenset[str] = frozenset(),
    max_attempts: int = MAX_SELF_HEAL_NO_PROGRESS_ATTEMPTS,
) -> tuple[dict[str, Any], bool]:
    """Pure helper: update the no-progress attempt counter and decide escalation.

    Returns ``(new_metadata, abandoned)``. Only delivered count high-water
    increases or fresh native collector effects reset the observer watchdog.
    Rank and fingerprints remain diagnostic. A missing/corrupt observer baseline
    never clears an existing counter, and no paid/review credit is returned.

    Counts retain their high-water marks: deleting and restoring the same
    chapter rows is not progress. A first observation of draft count only
    establishes its baseline for legacy metadata; it never renews a budget.
    Execution-terminal receipts always require an explicit resume elsewhere.
    """
    updated = dict(metadata) if isinstance(metadata, dict) else {}
    if execution_terminal_code(updated):
        return updated, True
    last = updated.get("self_heal_last_chapters_total")
    try:
        last_int = int(last) if last is not None else None
    except (TypeError, ValueError):
        last_int = None

    normalized_rank = _normalize_progress_rank(progress_rank)
    previous_rank = _normalize_progress_rank(
        updated.get("self_heal_last_progress_rank")
    )
    rank_improved = normalized_rank is not None and (
        previous_rank is None or normalized_rank > previous_rank
    )
    first_observation = last_int is None and "self_heal_no_progress_attempts" not in updated
    progressed = last_int is not None and chapters_total > last_int
    if repair_progress_snapshot is not None:
        observation = compare_repair_progress(
            updated.get(REPAIR_PROGRESS_STATE_KEY), repair_progress_snapshot
        )
        updated[REPAIR_PROGRESS_STATE_KEY] = deepcopy(observation["next_state"])
        progressed = progressed or bool(
            set(observation["new_effect_keys"]) - consumed_repair_effect_keys
        )
    if chapters_with_draft is not None:
        last_drafts = _metadata_int(updated, "self_heal_last_chapters_with_draft", -1)
        progressed = progressed or (last_drafts >= 0 and chapters_with_draft > last_drafts)
        updated["self_heal_last_chapters_with_draft"] = max(last_drafts, chapters_with_draft)
    if progressed or first_observation:
        attempts = 0
    else:
        attempts = _metadata_int(updated, "self_heal_no_progress_attempts", 0) + 1

    updated["self_heal_last_chapters_total"] = max(
        last_int if last_int is not None else 0, int(chapters_total)
    )
    updated["self_heal_no_progress_attempts"] = attempts
    if progress_fingerprint is not None:
        updated["self_heal_last_progress_fingerprint"] = progress_fingerprint
    if rank_improved and normalized_rank is not None:
        updated["self_heal_last_progress_rank"] = list(normalized_rank)

    if progressed:
        updated.pop("self_heal_abandoned", None)
        updated.pop("self_heal_abandoned_at", None)
        updated.pop("self_heal_abandoned_progress_fingerprint", None)
        updated.pop("self_heal_abandoned_progress_rank", None)

    if attempts >= max_attempts:
        now = _dt.datetime.now(_dt.UTC).isoformat()
        updated["self_heal_no_progress_escalated"] = True
        updated["self_heal_no_progress_escalated_at"] = now
        updated["self_heal_repair_strategy"] = "deep_machine_repair"
        updated["production_pause_reason"] = "self_heal_no_actionable_progress"
        updated["requires_machine_repair"] = True
        updated["requires_human_review"] = True
        updated["self_heal_abandoned"] = True
        updated["self_heal_abandoned_at"] = now
        updated["self_heal_abandoned_progress_fingerprint"] = (
            progress_fingerprint or f"chapters_total:{int(chapters_total)}"
        )
        best_rank = _normalize_progress_rank(updated.get("self_heal_last_progress_rank"))
        if best_rank is not None:
            updated["self_heal_abandoned_progress_rank"] = list(best_rank)
        return updated, True
    return updated, False


async def _observe_committed_repair_progress(session, settings, project_id):
    """Read independently of the dispatch writer; UNKNOWN grants no progress."""
    if not isinstance(getattr(session, "bind", None), AsyncEngine):
        return None
    try:
        snapshot = await collect_repair_progress(
            async_sessionmaker(session.bind, expire_on_commit=False), settings, project_id
        )
        if snapshot.get("project_id") != str(project_id):
            return None
        return snapshot
    except Exception as error:  # noqa: BLE001 — fail closed without logging source/secrets
        logger.warning("self-heal: repair observation unavailable (%s)", type(error).__name__)
        return None


_REPAIR_OBSERVER_WORKFLOW = "self_heal_repair_observation"


async def _consumed_repair_effects(session, project_id):
    """Independent deny-only history survives replay of old project metadata."""
    rows = list(await session.scalars(select(WorkflowRunModel).where(
        WorkflowRunModel.project_id == project_id,
        WorkflowRunModel.workflow_type == _REPAIR_OBSERVER_WORKFLOW,
    ).execution_options(populate_existing=True)))
    keys = set()
    for row in rows:
        key = (row.metadata_json or {}).get("effect_key")
        if (row.status != "completed" or row.scope_id != project_id
                or not isinstance(key, str) or len(key) != 64
                or any(c not in "0123456789abcdef" for c in key)):
            raise ValueError("repair_observer_history_unavailable")
        keys.add(key)
    return frozenset(keys)


def _record_repair_effects(session, project, snapshot, keys):
    for key in sorted(keys):
        # No new paid/review run: this completed row only remembers a consumed
        # observation, with no grant, dispatch, adoption or financial authority.
        session.add(WorkflowRunModel(
            id=uuid.uuid5(project.id, "self-heal-observed:" + key),
            project_id=project.id, workflow_type=_REPAIR_OBSERVER_WORKFLOW,
            status="completed", scope_type="project", scope_id=project.id,
            requested_by="self-heal-observer", current_step="observation_consumed",
            metadata_json={"schema": "self-heal-consumed-effect.v1", "effect_key": key,
                "source_epoch": snapshot["source_epoch"],
                "snapshot_sha256": snapshot["snapshot_sha256"],
                "provider_calls": 0, "new_attempts": 0, "new_credit_allowed": False,
                "dispatch_authorized": False, "grade_granted": False,
                "budget_reset_allowed": False, "debt_cleared": False,
                "unknown_reconciled": False},
        ))


async def _lock_heal_dispatch(session, stuck, pool):
    # Locks stay held through enqueue/accounting. A concurrent STOP or budget
    # transition cannot slip between an earlier scan and final admission.
    control = await load_control_state(session, stuck.project_id, for_update=True)
    if control.halted:
        return None
    project = await session.get(
        ProjectModel, stuck.project_id, with_for_update=True, populate_existing=True,
    )
    if project is None or execution_terminal_code(project.metadata_json or {}):
        return None
    # Legacy absence is native RUN/0, not a new operator command. The project
    # FOR UPDATE also fences a concurrent first control INSERT through its FK;
    # reread after acquiring it to catch a STOP committed while we waited.
    control = await load_control_state(session, stuck.project_id, for_update=True)
    if control.halted:
        return None
    if await _has_active_pipeline_run(session, project.id):
        return None
    owners = await _active_arq_project_slugs(pool)
    if owners is None or project.slug in owners:
        return None
    await session.get(ExecutionBudgetModel, project_execution_scope(project),
                      with_for_update=True, populate_existing=True)
    await assert_execution_scope_open(session, project_execution_scope(project))
    return project


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
    if execution_terminal_code(metadata):
        return False
    if evaluate_retry(load_chain(metadata, scope=generation_gate_scope(None)),
                      trigger=RetryTrigger.AUTO, budget=generation_gate_budget()).exhausted:
        return False
    reason = str(
        metadata.get("last_generation_gate_reason")
        or metadata.get("production_pause_reason")
        or ""
    ).strip()
    base_reason = reason.split(":", 1)[0]
    if base_reason not in _AUTO_RESUMABLE_GENERATION_GATE_REASONS:
        return False
    # Budget is per failure mode: spinning on ONE unsatisfiable gate must stop,
    # but making progress and hitting a different gate earns a fresh budget.
    spent_reason = str(
        metadata.get("generation_gate_auto_resume_reason") or ""
    ).split(":", 1)[0]
    if not spent_reason or spent_reason == base_reason:
        try:
            spent = int(metadata.get("generation_gate_auto_resume_count") or 0)
        except (TypeError, ValueError):
            spent = 0
        if spent >= GENERATION_GATE_MAX_AUTO_RESUMES:
            return False
    if not (
        metadata.get("generation_gate_auto_retry_needed")
        or metadata.get("generation_resume_blocked_by_planning_gate")
        or metadata.get("generation_auto_repair_exhausted")
        or (
            base_reason == TEMPORARY_PLANNING_THROTTLE_REASON
            and (
                metadata.get("production_paused")
                or metadata.get("generation_resume_blocked_until_repair_audit")
            )
        )
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
    project = await session.get(ProjectModel, project_id, with_for_update=True)
    if project is None or not _project_has_stale_auto_resumable_generation_gate(project):
        return False
    await assert_execution_scope_open(session, project_execution_scope(project))

    metadata = dict(getattr(project, "metadata_json", None) or {})
    reason = str(
        metadata.get("last_generation_gate_reason")
        or metadata.get("production_pause_reason")
        or ""
    )
    scope = generation_gate_scope(reason)
    decision = evaluate_retry(load_chain(metadata, scope=scope), trigger=RetryTrigger.AUTO,
                              budget=generation_gate_budget(), reason=reason)
    if decision.exhausted:
        return False
    metadata = store_chain(metadata, scope=scope, chain=decision.chain)
    metadata["generation_gate_retry_budget"] = decision.to_event_payload()
    metadata["last_generation_gate_auto_resumed_at"] = _dt.datetime.now(_dt.UTC).isoformat()
    metadata["last_generation_gate_auto_resumed_reason"] = reason
    # Preserve this old per-reason diagnostic counter, but permission now comes
    # from the unified retry chain above, which survives reason changes.
    _base_reason = reason.split(":", 1)[0]
    _prev_reason = str(
        metadata.get("generation_gate_auto_resume_reason") or ""
    ).split(":", 1)[0]
    try:
        _spent = int(metadata.get("generation_gate_auto_resume_count") or 0)
    except (TypeError, ValueError):
        _spent = 0
    metadata["generation_gate_auto_resume_count"] = (
        _spent + 1 if _prev_reason == _base_reason else 1
    )
    metadata["generation_gate_auto_resume_reason"] = _base_reason
    for key in (
        "generation_gate_auto_retry_needed",
        "generation_resume_blocked_by_planning_gate",
        "generation_auto_repair_exhausted",
        "generation_resume_blocked_until_repair_audit",
        "production_paused",
        "production_pause_reason",
        "last_generation_gate_blocked_at",
        "paused_at",
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
    # Self-heal is another prose-generation entry point.  It must honor the
    # same project-level planning stop as API/worker pipelines; otherwise a
    # restart can resurrect an old autowrite job and overwrite a freshly
    # persisted ``needs_replan`` design while the public UI still says paused.
    if execution_terminal_code(getattr(project, "metadata_json", None) or {}):
        return True
    if project_blocks_all_prose_generation(project):
        return True
    if _project_has_stale_auto_resumable_generation_gate(project):
        return False
    metadata = getattr(project, "metadata_json", None) or {}
    if isinstance(metadata, dict):
        reason = str(metadata.get("production_pause_reason") or "").strip()
        if reason in _SELF_HEAL_AUTO_RESUMABLE_PAUSE_REASONS:
            return False
    return project_resume_is_terminally_blocked(
        getattr(project, "metadata_json", None)
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


def _outline_replan_heal_job_id(slug: str) -> str:
    """Deterministic owner for planner recovery; never shared with prose jobs."""

    return f"outline-replan:heal:{slug}"


def _repair_heal_job_id(slug: str) -> str:
    """Deterministic ARQ job id for project repair self-heal."""
    return f"repair:heal:{slug}"


def _project_pipeline_heal_job_id(slug: str) -> str:
    """Deterministic ARQ job id for project-pipeline continuation self-heal."""
    return f"project-pipeline:heal:{slug}"


def _blocking_heal_job_ids(stuck: StuckProject) -> tuple[str, ...]:
    """Match dispatch ownership, including permitted repair/continuation overlap."""
    autowrite = _autowrite_heal_job_id(stuck.slug)
    repair = _repair_heal_job_id(stuck.slug)
    pipeline = _project_pipeline_heal_job_id(stuck.slug)
    outline = _outline_replan_heal_job_id(stuck.slug)
    return {
        "autowrite": (autowrite, repair),
        "repair": (repair, autowrite),
        "project_pipeline": (pipeline, autowrite),
        "outline_replan": (outline, autowrite, repair, pipeline),
    }.get(stuck.heal_kind, (autowrite, repair))


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


async def _is_ghost_heal_job(pool: "ArqRedis", job_id: str) -> bool:
    """A bare job definition or retry counter with NO active schedule.

    A consumed/abandoned run can leave the job-definition key behind with no
    in-progress lock, no retry entry, and no queue score. ARQ will never dispatch
    such a job, yet ``enqueue_job`` dedup-rejects (returns None) a fresh re-queue
    while the key exists — and the self-heal "in_flight = exists(job_key, ...)"
    guard treats the bare key as live — so the project is stranded permanently
    (observed after worker restarts mid-run). A ghost must be cleared so the
    project can be re-queued.
    """
    try:
        job_exists = await pool.exists(f"arq:job:{job_id}")
        retry_exists = await pool.exists(f"arq:retry:{job_id}")
        if not job_exists and not retry_exists:
            return False
        if await pool.exists(f"arq:in-progress:{job_id}"):
            return False
        # A retry counter alone cannot execute anything. When a job payload
        # still exists, leave age-based orphan classification in charge.
        if job_exists and retry_exists:
            return False
        return await pool.zscore("arq:queue", job_id) is None
    except Exception:  # noqa: BLE001
        logger.exception("self-heal: failed ghost-job check %s", job_id)
        return False


async def _orphan_heal_job_age_seconds(pool: "ArqRedis", job_id: str) -> float | None:
    """Best-effort age of a bare ``arq:job:<id>`` key, ``None`` if unknown.

    Resolution order:

    1. The pickled job payload's enqueue timestamp (``t``, epoch ms) — the
       authoritative creation time when readable.
    2. Fallback inference: no ``arq:result`` key (the job never completed)
       plus the job key's remaining TTL — ARQ sets the key TTL to the job's
       ``_expires`` window at enqueue time, so
       ``age ≈ expires_window - remaining_ttl``.
    """

    try:
        raw = await _redis_get_bytes(pool, f"arq:job:{job_id}")
    except Exception:  # noqa: BLE001 — payload read is best-effort
        raw = None
    if raw:
        try:
            payload = pickle.loads(raw)
            enqueue_ms = payload.get("t") if isinstance(payload, dict) else None
            if isinstance(enqueue_ms, (int, float)) and enqueue_ms > 0:
                return max(0.0, time.time() - float(enqueue_ms) / 1000.0)
        except Exception:  # noqa: BLE001 — fall through to TTL inference
            pass

    try:
        if await pool.exists(f"arq:result:{job_id}"):
            return None
        pttl_ms = await pool.pttl(f"arq:job:{job_id}")
    except AttributeError:
        return None
    except Exception:  # noqa: BLE001
        logger.exception("self-heal: failed TTL probe for ARQ job %s", job_id)
        return None
    if not isinstance(pttl_ms, (int, float)) or pttl_ms <= 0:
        return None
    expires_window_seconds = SELF_HEAL_JOB_EXPIRES_DAYS * 24 * 60 * 60
    return max(0.0, expires_window_seconds - float(pttl_ms) / 1000.0)


async def _is_orphan_heal_job(
    pool: "ArqRedis",
    job_id: str,
    *,
    max_age_seconds: float | None = None,
) -> bool:
    """R24: an over-age ``arq:job`` key with no schedule and no executor.

    Three-step verdict: the job key exists, but ① it has no ``arq:queue``
    score (not scheduled), ② it has no ``arq:in-progress`` lock (not
    executing), and ③ it is older than the configurable cap. Such a key can
    never run again, yet it dedup-blocks every re-enqueue — the project would
    never retry. Unlike ``_is_ghost_heal_job`` this also covers keys with a
    leftover ``arq:retry`` counter (the half-enqueued deploy-window shape).
    """

    cap = (
        SELF_HEAL_ORPHAN_JOB_MAX_AGE_SECONDS
        if max_age_seconds is None
        else max_age_seconds
    )
    try:
        if not await pool.exists(f"arq:job:{job_id}"):
            return False
        if await pool.zscore("arq:queue", job_id) is not None:
            return False
        if await pool.exists(f"arq:in-progress:{job_id}"):
            return False
    except AttributeError:
        return False
    except Exception:  # noqa: BLE001
        logger.exception("self-heal: failed orphan-job check %s", job_id)
        return False
    age = await _orphan_heal_job_age_seconds(pool, job_id)
    if age is None or age < cap:
        return False
    logger.info(
        "self-heal: ARQ job %s is an orphan (age %.0fs > %.0fs, "
        "not queued, not in progress) — clearing for re-enqueue",
        job_id,
        age,
        cap,
    )
    return True


_HEAL_JOB_SNAPSHOT_LUA = """
-- bestseller-ghost-snapshot-v1
if redis.call('exists', KEYS[2]) == 1 or
   redis.call('zscore', KEYS[5], ARGV[1]) then
    return {}
end
return {redis.call('get', KEYS[1]) or false,
        redis.call('get', KEYS[3]) or false,
        redis.call('get', KEYS[4]) or false,
        redis.call('pttl', KEYS[1]), redis.call('pttl', KEYS[3]),
        redis.call('pttl', KEYS[4])}
"""

_HEAL_JOB_CLEANUP_LUA = """
-- bestseller-ghost-cleanup-v1
if redis.call('exists', KEYS[2]) == 1 or
   redis.call('zscore', KEYS[5], ARGV[1]) then
    return 0
end
local indices = {1, 3, 4}
for i, key_index in ipairs(indices) do
    local offset = 2 + (i - 1) * 3
    local current = redis.call('get', KEYS[key_index])
    if ARGV[offset] == '0' then
        if current then return 0 end
    else
        if current ~= ARGV[offset + 1] then return 0 end
        local old_ttl = tonumber(ARGV[offset + 2])
        local ttl = redis.call('pttl', KEYS[key_index])
        if (old_ttl >= 0 and (ttl < 0 or ttl > old_ttl)) or
           (old_ttl < 0 and ttl ~= old_ttl) then
            return 0
        end
    end
end
return redis.call('del', KEYS[1], KEYS[3], KEYS[4])
"""


async def _clear_stale_heal_job_if_needed(pool: "ArqRedis", job_id: str) -> bool:
    """Atomically reclaim unchanged, unscheduled ARQ debris, never an executor.

    An old queue score cannot prove an in-progress owner is dead: ARQ uses the
    same constant value for every executor. Its lease must expire first. The
    final script rechecks queue/lock absence, values and non-renewed TTLs, so a
    new enqueue or executor between classification and cleanup wins safely.
    Script/read failures retain all keys; there is no non-atomic fallback.
    """
    keys = [f"arq:{kind}:{job_id}" for kind in ("job", "in-progress", "result", "retry")]
    keys.append("arq:queue")
    try:
        snapshot = await pool.eval(_HEAL_JOB_SNAPSHOT_LUA, len(keys), *keys, job_id)
        if not snapshot or len(snapshot) != 6 or not any(v is not None for v in snapshot[:3]):
            return False
        job, _result, retry = snapshot[:3]
        # A half-enqueued payload plus retry counter retains the existing age
        # requirement. Bare payloads and terminal-only debris cannot execute.
        if job is not None and retry is not None and not await _is_orphan_heal_job(pool, job_id):
            return False
        arguments: list[Any] = [job_id]
        for value, ttl in zip(snapshot[:3], snapshot[3:], strict=True):
            arguments.extend((int(value is not None), value if value is not None else b"", int(ttl)))
        removed = await pool.eval(_HEAL_JOB_CLEANUP_LUA, len(keys), *keys, *arguments)
    except Exception:  # preserve owners when Redis cannot prove the atomic predicate
        logger.exception("self-heal: atomic ghost cleanup refused for %s", job_id)
        return False
    if removed:
        logger.info("self-heal: atomically cleared ARQ ghost %s", job_id)
    return bool(removed)



async def _window_queue_allowed(stuck, *, repair=False):
    if not getattr(stuck, "configured_window", False):
        return True  # Legacy scanner records retain their existing queue path.
    from bestseller.services.creation_configuration import read_window
    from bestseller.services.execution_windows import window_progress
    async with get_server_session() as session:
        project = await session.get(ProjectModel, stuck.project_id)
        try:
            if project is None or read_window(project) is None or repair:
                return False
            from bestseller.services.execution_windows import assert_window_runnable
            await assert_window_runnable(session, project)
            return not (await window_progress(session, project))["window_complete"]
        except Exception:
            logger.warning("self-heal: configured window cannot be verified for %s", stuck.slug)
            return False

async def _requeue_autowrite(
    pool: "ArqRedis",
    stuck: StuckProject,
) -> str | None:
    """Enqueue a fresh ``run_autowrite_task`` for a stuck project.

    Returns the job id on success, or ``None`` if ARQ already has a
    pending/running job for the same slug (deterministic dedup).
    """
    if not await _window_queue_allowed(stuck, repair=False):
        return None
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
        if not await _clear_stale_heal_job_if_needed(pool, job_id):
            return None
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


async def _requeue_outline_replan(
    pool: "ArqRedis",
    stuck: StuckProject,
) -> str | None:
    """Queue the only worker allowed to recover a rejected outline."""
    if not await _window_queue_allowed(stuck, repair=True):
        return None

    for competing_job_id in (
        _autowrite_heal_job_id(stuck.slug),
        _repair_heal_job_id(stuck.slug),
        _project_pipeline_heal_job_id(stuck.slug),
    ):
        if await _heal_job_exists(pool, competing_job_id):
            if not await _clear_stale_heal_job_if_needed(pool, competing_job_id):
                logger.info(
                    "self-heal: skipped outline replan slug=%s — job %s owns project",
                    stuck.slug,
                    competing_job_id,
                )
                return None

    job_id = _outline_replan_heal_job_id(stuck.slug)
    job = await pool.enqueue_job(
        "run_outline_replan_task",
        workflow_run_id=job_id,
        payload={
            "project_slug": stuck.slug,
            "premise": None,
            "progress_fingerprint": stuck.progress_fingerprint,
        },
        _job_id=job_id,
        _expires=_dt.timedelta(days=SELF_HEAL_JOB_EXPIRES_DAYS),
    )
    if job is not None:
        return job_id
    if not await _clear_stale_heal_job_if_needed(pool, job_id):
        # ARQ deduplicates against a terminal result key even after the live
        # job/in-progress/queue keys have disappeared. A failed outline replan
        # intentionally returns ``outline_replan_retry_pending``; retaining
        # that terminal result must not strand the book forever. Only clear it
        # after proving there is no active owner.
        return None
    job = await pool.enqueue_job(
        "run_outline_replan_task",
        workflow_run_id=job_id,
        payload={
            "project_slug": stuck.slug,
            "premise": None,
            "progress_fingerprint": stuck.progress_fingerprint,
        },
        _job_id=job_id,
        _expires=_dt.timedelta(days=SELF_HEAL_JOB_EXPIRES_DAYS),
    )
    return job_id if job is not None else None


async def _requeue_repair(
    pool: "ArqRedis",
    stuck: StuckProject,
) -> str | None:
    """Enqueue a project repair job for blocked production chapters."""
    if not await _window_queue_allowed(stuck, repair=True):
        return None
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
            "pending_rewrite_task_limit": SELF_HEAL_PENDING_REWRITE_TASK_LIMIT,
        },
        _job_id=job_id,
        _expires=_dt.timedelta(days=SELF_HEAL_JOB_EXPIRES_DAYS),
    )
    if job is None:
        if not await _clear_stale_heal_job_if_needed(pool, job_id):
            return None
        job = await pool.enqueue_job(
            "run_project_repair_task",
            workflow_run_id=job_id,
            payload={
                "project_slug": stuck.slug,
                "requested_by": "worker_self_heal",
                "include_pending_rewrite_tasks": True,
                "pending_rewrite_task_limit": SELF_HEAL_PENDING_REWRITE_TASK_LIMIT,
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
    if not await _window_queue_allowed(stuck, repair=False):
        return None
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
        if not await _clear_stale_heal_job_if_needed(pool, job_id):
            return None
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
    if stuck.heal_kind == "outline_replan":
        return await _requeue_outline_replan(pool, stuck)
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
    priority = {
        "outline_replan": 4,
        "autowrite": 3,
        "project_pipeline": 2,
        "repair": 1,
    }
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
    """Diagnostic age probe only; never authorizes deleting an executor lease."""
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
        zrem = pool.zrem
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


async def _release_heal_lock(redis: Any | None, worker_id: str) -> None:
    """Release the singleton lock only when ``worker_id`` still owns it."""

    if redis is None:
        return
    try:
        await compare_owner_delete(redis, SELF_HEAL_LOCK_KEY, worker_id)
    except Exception:  # noqa: BLE001 — expiry remains the safe fallback
        logger.exception("self-heal: failed to release boot lock")


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
    # Worker identity can recur after a lease expires. A late scan must not
    # release a successor scan's lease even when both share the same worker.
    heal_lock_token = f"{effective_worker_id}:{uuid.uuid4().hex}"
    previous_scan_done_marker: Any | None = None
    if redis is not None:
        try:
            previous_scan_done_marker = await redis.get(SELF_HEAL_SCAN_DONE_KEY)
        except Exception:  # noqa: BLE001
            logger.exception("self-heal: failed to read existing scan-done marker")

    # All replicas in one Compose boot start within the same five-second
    # cohort.  The Redis lock serialises overlapping scans, but without this
    # completed-marker check a slightly slower replica can acquire the lock
    # immediately after the first releases it and count the same project as a
    # second no-progress attempt.  Only startup scans use this shortcut;
    # periodic scans (startup_cutoff=None) must still run normally.
    if (
        startup_cutoff is not None
        and _scan_done_marker_at_or_after(
            previous_scan_done_marker,
            startup_cutoff,
        )
    ):
        logger.info(
            "self-heal: startup cohort already scanned by another worker — skipping scan",
        )
        return dispatched

    if not await _try_acquire_heal_lock(redis, heal_lock_token):
        logger.info(
            "self-heal: another worker holds the boot lock — skipping scan",
        )
        if await _wait_for_scan_done(redis, previous_marker=previous_scan_done_marker):
            return dispatched
        logger.warning(
            "self-heal: boot lock holder did not publish scan-done marker; "
            "skipping this scan",
        )
        return dispatched

    pool: "ArqRedis | None" = None
    ownership_known = False
    try:
        # A replica can read an empty marker, wait for the first scanner to
        # finish, then acquire the just-released lock. Re-check *after* lock
        # acquisition before deleting the marker; otherwise that slower replica
        # performs a second startup scan and increments no-progress counters for
        # the same recovery cohort (observed live with three worker replicas).
        if redis is not None and startup_cutoff is not None:
            try:
                current_marker = await redis.get(SELF_HEAL_SCAN_DONE_KEY)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "self-heal: failed to re-read scan-done marker after lock acquisition"
                )
            else:
                if _scan_done_marker_at_or_after(current_marker, startup_cutoff):
                    logger.info(
                        "self-heal: startup cohort completed while waiting for lock "
                        "— skipping duplicate scan",
                    )
                    return dispatched

        if redis is not None:
            try:
                await redis.delete(SELF_HEAL_SCAN_DONE_KEY)
            except Exception:  # noqa: BLE001
                logger.exception("self-heal: failed to clear stale scan-done marker")

        async with get_server_session() as session:
            owner_slugs = await _active_arq_project_slugs(redis)
            if owner_slugs is None:
                logger.warning("self-heal: ownership UNKNOWN; no reaping or recovery dispatch")
                return dispatched
            ownership_known = True
            protected_project_ids = await _resolve_project_ids_for_slugs(session, owner_slugs)
            reaped = await reap_orphan_workflow_runs(
                session,
                startup_cutoff=startup_cutoff,
                protected_project_ids=protected_project_ids,
                redis=redis,
            )
            if reaped:
                await session.commit()
                logger.info(
                    "self-heal: reaped %d orphan workflow run(s)",
                    reaped,
                )
            if protected_project_ids:
                logger.info(
                    "self-heal: protected %d project(s) with in-progress ARQ jobs",
                    len(protected_project_ids),
                )
            stuck_list = await find_stuck_projects(session)

        if not stuck_list:
            logger.info("self-heal: no stuck projects found")
            return dispatched
        stuck_list = _coalesce_stuck_projects_for_enqueue(stuck_list)

        pool = await create_pool(_arq_redis_settings(settings))
        for stuck in stuck_list:
            # A user-initiated delete records a tombstone before it touches the
            # DB. Honor it here so a project whose delete is mid-flight (or whose
            # DB delete lost a lock race) is never re-queued back to life.
            if is_project_delete_tombstoned(settings, stuck.slug):
                logger.info(
                    "self-heal: skipped slug=%s — project is delete-tombstoned",
                    stuck.slug,
                )
                continue
            try:
                async with get_server_session() as session:
                    project = await session.get(ProjectModel, stuck.project_id)
                    if project is None:
                        logger.info(
                            "self-heal: skipped slug=%s — project disappeared before enqueue",
                            stuck.slug,
                        )
                        continue
                    if execution_terminal_code(project.metadata_json or {}):
                        continue
                    await assert_execution_scope_open(session, project_execution_scope(project))
                    if (
                        project_blocks_all_prose_generation(project)
                        and stuck.heal_kind != "outline_replan"
                    ):
                        logger.info(
                            "self-heal: skipped slug=%s — outline replan became "
                            "required before enqueue",
                            stuck.slug,
                        )
                        continue
                    if stuck.heal_kind in {
                        "autowrite",
                        "project_pipeline",
                        "outline_replan",
                    }:
                        active_workflow = await _has_active_continuation_pipeline_run(
                            session, stuck.project_id
                        )
                    else:
                        active_workflow = await _has_active_pipeline_run(
                            session, stuck.project_id
                        )
                    if active_workflow:
                        logger.info(
                            "self-heal: skipped slug=%s — active workflow appeared before enqueue",
                            stuck.slug,
                        )
                        continue
                    # Queue backlog is not a failed recovery attempt. Check
                    # ownership before consuming either retry/watchdog debt;
                    # reclaim ghosts using the same rules as dispatch.
                    pending_owner = False
                    for job_id in _blocking_heal_job_ids(stuck):
                        if await _heal_job_exists(pool, job_id):
                            if not await _clear_stale_heal_job_if_needed(pool, job_id):
                                pending_owner = True
                                break
                    if pending_owner:
                        continue
                    if (
                        stuck.heal_kind != "outline_replan"
                        and await _clear_auto_resumable_generation_gate_pause(
                            session,
                            stuck.project_id,
                        )
                    ):
                        await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "self-heal: failed to recheck active workflow for slug=%s",
                    stuck.slug,
                )
                continue
            # Record a heal attempt and give up if this project has been
            # re-queued repeatedly without making any chapter-count progress.
            try:
                async with get_server_session() as session:
                    project = await _lock_heal_dispatch(session, stuck, pool)
                    if project is not None:
                        repair_snapshot = None
                        if not execution_terminal_code(project.metadata_json or {}):
                            repair_snapshot = await _observe_committed_repair_progress(
                                session, settings, stuck.project_id
                            )
                        consumed_keys = await _consumed_repair_effects(session, project.id)
                        effect_keys = set()
                        if repair_snapshot is not None:
                            observation = compare_repair_progress(
                                (project.metadata_json or {}).get(REPAIR_PROGRESS_STATE_KEY),
                                repair_snapshot,
                            )
                            effect_keys = set(observation["new_effect_keys"]) - consumed_keys
                        latest_owners = await _active_arq_project_slugs(pool)
                        if latest_owners is None or project.slug in latest_owners:
                            continue
                        await assert_execution_scope_open(session, project_execution_scope(project))
                        new_meta, abandoned = _compute_heal_progress_state(
                            project.metadata_json or {},
                            stuck.chapters_total,
                            chapters_with_draft=stuck.chapters_with_draft,
                            progress_fingerprint=(
                                stuck.progress_fingerprint
                                or "|".join(
                                    [
                                        stuck.heal_kind,
                                        stuck.reason,
                                        str(stuck.stuck_at_chapter or ""),
                                        str(stuck.chapters_total),
                                        str(stuck.chapters_with_draft),
                                    ]
                                )
                            ),
                            progress_rank=stuck.progress_rank,
                            repair_progress_snapshot=repair_snapshot,
                            consumed_repair_effect_keys=consumed_keys,
                        )
                        if abandoned:
                            project.metadata_json = new_meta
                            await session.commit()
                            logger.warning(
                                "self-heal: ABANDONED slug=%s after %d no-progress "
                                "heals (chapters_total=%d) — flagged for human "
                                "review, not re-queueing",
                                stuck.slug,
                                MAX_SELF_HEAL_NO_PROGRESS_ATTEMPTS,
                                stuck.chapters_total,
                            )
                            continue
                        # Only an accepted dispatch consumes an attempt. ARQ
                        # deduplication or an enqueue failure did no repair work.
                        task_id = await _requeue_stuck_project(pool, stuck)
                        if task_id is None:
                            continue
                        _record_repair_effects(session, project, repair_snapshot, effect_keys)
                        project.metadata_json = new_meta
                        await session.commit()
                    else:
                        continue
            except Exception:  # noqa: BLE001
                logger.exception(
                    "self-heal: failed to dispatch/record heal progress for slug=%s",
                    stuck.slug,
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
                if ownership_known:
                    await redis.set(
                        SELF_HEAL_SCAN_DONE_KEY,
                        (f"{effective_worker_id}:"
                         f"{int(_dt.datetime.now(tz=_dt.timezone.utc).timestamp())}"),
                        ex=SELF_HEAL_SCAN_DONE_TTL_SECONDS,
                    )
            except Exception:  # noqa: BLE001 — marker is advisory
                logger.exception("self-heal: failed to publish scan-done marker")
            finally:
                await _release_heal_lock(redis, heal_lock_token)

    return dispatched


def _scan_done_marker_at_or_after(
    marker: Any | None,
    cutoff: _dt.datetime,
) -> bool:
    """Return whether a scan marker belongs to the current startup cohort."""

    if isinstance(marker, bytes):
        try:
            marker = marker.decode("utf-8")
        except UnicodeDecodeError:
            return False
    if not isinstance(marker, str):
        return False
    try:
        timestamp = int(marker.rsplit(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        return False
    normalized_cutoff = cutoff
    if normalized_cutoff.tzinfo is None:
        normalized_cutoff = normalized_cutoff.replace(tzinfo=_dt.UTC)
    return timestamp >= int(
        normalized_cutoff.timestamp() - SELF_HEAL_STARTUP_COHORT_SKEW_SECONDS
    )


async def _wait_for_scan_done(
    redis: Any | None,
    timeout_seconds: int = SELF_HEAL_LOCK_TTL_SECONDS,
    previous_marker: Any | None = None,
) -> bool:
    if redis is None:
        return True
    deadline = time.monotonic() + max(1, timeout_seconds)
    while time.monotonic() < deadline:
        try:
            marker = await redis.get(SELF_HEAL_SCAN_DONE_KEY)
            if marker and marker != previous_marker:
                return True
        except Exception:  # noqa: BLE001
            logger.exception("self-heal: failed while waiting for scan-done marker")
            return False
        await asyncio.sleep(0.5)
    logger.warning("self-heal: timed out waiting for scan-done marker")
    return False
