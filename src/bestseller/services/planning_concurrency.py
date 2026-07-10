"""Planning concurrency guards — prevent zombie parallel planners.

When multiple ``generate_volume_plan`` / ``generate_novel_plan`` runs are
started for the same project (heal-job retries, double-click, progressive
rescue), they thrash shared outline artifacts and leave ``running`` workflows
forever. This module cancels *stale* (dead-heartbeat) active planning runs
before a new one claims the project.

It never cancels a genuinely live sibling — that is a real concurrency
conflict, and ``cancel_stale_planning_workflows`` raises ``PlanningConflictError``
instead so the caller can refuse the new request rather than stomping a
running one. It also never touches writing workflows (``autowrite_pipeline`` /
``project_pipeline`` / ``chapter_pipeline`` / ``scene_pipeline``) — an earlier
version of this guard included ``autowrite_pipeline`` in the cancellable set,
which meant re-planning volume N+1 could mark an in-progress writing run
FAILED while the worker kept writing chapters underneath it (status/reality
divergence, not an actual cancellation).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import datetime as _dt
import logging
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db.models import WorkflowRunModel

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Workflow types that must not run concurrently per project. Deliberately
# excludes writing workflows — those are guarded separately by
# ``assert_no_active_writing_pipeline``.
PLANNING_WORKFLOW_TYPES: frozenset[str] = frozenset(
    {
        "generate_novel_plan",
        "generate_volume_plan",
        "generate_foundation_plan",
    }
)

_ACTIVE = frozenset(
    {
        WorkflowStatus.PENDING.value,
        WorkflowStatus.QUEUED.value,
        WorkflowStatus.RUNNING.value,
    }
)

# A planning step is a single long LLM call (up to ~15 min) that only touches
# its WorkflowRun row at step boundaries, and the outline repair loop can run
# several such steps with no intervening row update. This mirrors the
# rationale behind ``worker.self_heal.ORPHAN_WORKFLOW_TIMEOUT_SECONDS`` (kept
# as an independent constant here — ``services/`` must not import
# ``worker/``; ``worker/`` already imports ``services/`` and the reverse
# risks a cycle).
PLANNING_STALE_AFTER_SECONDS = 3 * 60 * 60


class PlanningConflictError(RuntimeError):
    """Raised when a sibling planning workflow is genuinely still active.

    Distinguishes "safe to cancel" (stale, dead heartbeat) from "must not
    touch" (still running) so a burst of concurrent planning requests refuses
    the newcomer instead of silently failing the live run.
    """

    def __init__(self, workflow_run_id: UUID, workflow_type: str) -> None:
        self.workflow_run_id = workflow_run_id
        self.workflow_type = workflow_type
        super().__init__(
            f"Planning workflow {workflow_type} (run={workflow_run_id}) is still "
            "active for this project. Wait for it to finish or cancel it first."
        )


async def run_in_isolated_session(
    parent_session: AsyncSession,
    operation: Callable[[AsyncSession], Awaitable[_T]],
    *,
    session_factory: Callable[[], Any] | None = None,
) -> _T:
    """Run one concurrent planning lane in its own transaction boundary.

    ``AsyncSession`` is stateful and cannot service concurrent flush/commit/
    rollback operations. Conception's Round-1 LLM calls persist telemetry, so
    each parallel lane must own a distinct session even though the provider
    requests themselves are independent.

    ``session_factory`` is injectable for focused tests. Production derives a
    lightweight factory from the parent's existing async bind, preserving the
    same engine/pool without sharing transaction state.
    """

    if session_factory is None:
        bind = getattr(parent_session, "bind", None)
        if not isinstance(bind, AsyncEngine):
            raise RuntimeError(
                "Isolated planning sessions require an AsyncEngine bind; "
                "an AsyncConnection or unknown bind could share transaction state."
            )
        session_factory = async_sessionmaker(bind, expire_on_commit=False)

    async with session_factory() as lane_session:
        try:
            result = await operation(lane_session)
            await lane_session.commit()
            return result
        except BaseException:
            await lane_session.rollback()
            raise


def _is_stale(updated_at: _dt.datetime | None, cutoff: _dt.datetime) -> bool:
    if updated_at is None:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=_dt.UTC)
    return updated_at < cutoff


async def cancel_stale_planning_workflows(
    session: AsyncSession,
    project_id: UUID,
    *,
    keep_workflow_run_id: UUID | None = None,
    reason: str = "superseded by newer planning run",
    stale_after_seconds: int = PLANNING_STALE_AFTER_SECONDS,
) -> int:
    """Mark abandoned (stale) planning workflows for this project as failed.

    Rows within ``stale_after_seconds`` of their last heartbeat are left
    untouched, and a live conflicting row raises ``PlanningConflictError``
    instead of being cancelled — callers must either propagate that (abort
    the new request) or explicitly swallow it for best-effort sweeps.

    Returns the number of rows cancelled. Never raises for infra-level
    failures the caller wraps in its own try/except; only raises
    ``PlanningConflictError`` for a genuine live conflict.
    """

    now = _dt.datetime.now(_dt.UTC)
    cutoff = now - _dt.timedelta(seconds=stale_after_seconds)

    stmt = select(WorkflowRunModel).where(
        WorkflowRunModel.project_id == project_id,
        WorkflowRunModel.workflow_type.in_(PLANNING_WORKFLOW_TYPES),
        WorkflowRunModel.status.in_(_ACTIVE),
    )
    if keep_workflow_run_id is not None:
        stmt = stmt.where(WorkflowRunModel.id != keep_workflow_run_id)
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return 0

    stale_ids: list[UUID] = []
    for row in rows:
        if _is_stale(getattr(row, "updated_at", None), cutoff):
            stale_ids.append(row.id)
        else:
            raise PlanningConflictError(row.id, row.workflow_type)

    message = f"[planning_concurrency] {reason}"
    await session.execute(
        update(WorkflowRunModel)
        .where(WorkflowRunModel.id.in_(stale_ids))
        .values(
            status=WorkflowStatus.FAILED.value,
            current_step="cancelled_stale",
            error_message=message,
        )
    )
    await session.flush()
    logger.warning(
        "Cancelled %d stale planning workflow(s) for project_id=%s keep=%s reason=%s",
        len(stale_ids),
        project_id,
        keep_workflow_run_id,
        reason,
    )
    return len(stale_ids)


async def assert_no_active_writing_pipeline(
    session: AsyncSession,
    project_id: UUID,
) -> WorkflowRunModel | None:
    """Return an active writing pipeline row if one exists (caller may 409).

    Used by top-level planning (``generate_novel_plan`` /
    ``generate_foundation_plan``) to refuse re-planning BookSpec/WorldSpec/
    CastSpec while a writer is actively mid-flight on this project — even
    before any chapter is committed. Deliberately NOT used by
    ``generate_volume_plan``: planning volume N+1 while volume N is being
    written is the normal progressive-planning happy path, not a conflict.
    """

    return await session.scalar(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.project_id == project_id,
            WorkflowRunModel.workflow_type.in_(
                {
                    "project_pipeline",
                    "autowrite_pipeline",
                    "chapter_pipeline",
                }
            ),
            WorkflowRunModel.status.in_(_ACTIVE),
        )
        .limit(1)
    )


__all__ = [
    "PLANNING_STALE_AFTER_SECONDS",
    "PLANNING_WORKFLOW_TYPES",
    "PlanningConflictError",
    "assert_no_active_writing_pipeline",
    "cancel_stale_planning_workflows",
    "run_in_isolated_session",
]
