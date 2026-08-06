"""Single source of truth for operator run/pause/stop intent.

The bug this replaces
---------------------
"Stop" used to be a flag inside ``projects.metadata``. The web process wrote it;
the pipeline worker rewrote that whole JSONB block from its own in-memory copy
at the next checkpoint and erased it. Self-heal then saw a book with no stop
marker, decided it was stalled, and requeued it — the book came back about a
minute after being stopped.

The shape of the fix, borrowed from DeterminFlow's task control model:

* **Intent lives where nothing else rewrites it.** Its own table, read-only to
  the pipeline.
* **Auto-recovery re-reads intent inside the transaction, immediately before
  acting.** Never from a value captured earlier; ``auto_recovery_is_permitted``
  is the one predicate every recovery path calls.
* **The intent to resume is itself persisted** (``resume_pending``). A crash
  between "we decided to resume" and "the job is queued" would otherwise lose
  the resume with nothing left on disk to show it was ever wanted.
* **In-memory derivatives carry a generation.** ``command_serial`` bumps on
  every command, so a timer or queued job created before the latest command can
  detect that it is stale instead of acting on a superseded decision.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import BookProductionControlModel

logger = logging.getLogger(__name__)

__all__ = [
    "ProductionControlState",
    "ProductionIntent",
    "auto_recovery_is_permitted",
    "claim_resume_pending",
    "halted_project_ids",
    "load_control_state",
    "mark_resume_pending",
    "request_pause",
    "request_run",
    "request_stop",
]


class ProductionIntent(str, Enum):
    """What the operator wants this book to be doing."""

    RUN = "run"
    PAUSE = "pause"
    STOP = "stop"

    @property
    def halts_production(self) -> bool:
        return self is not ProductionIntent.RUN


#: Default when a book has no control row at all — books created before this
#: table existed, and freshly created books, are runnable.
_DEFAULT_INTENT: Final[ProductionIntent] = ProductionIntent.RUN


@dataclass(frozen=True)
class ProductionControlState:
    """Immutable snapshot of one book's operator intent."""

    project_id: UUID
    intent: ProductionIntent = _DEFAULT_INTENT
    reason: str | None = None
    requested_by: str | None = None
    requested_at: _dt.datetime | None = None
    command_serial: int = 0
    resume_pending: bool = False
    resume_pending_at: _dt.datetime | None = None
    detail: Mapping[str, Any] | None = None

    @property
    def halted(self) -> bool:
        return self.intent.halts_production

    def to_payload(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "reason": self.reason,
            "requested_by": self.requested_by,
            "requested_at": (
                self.requested_at.isoformat() if self.requested_at else None
            ),
            "command_serial": self.command_serial,
            "resume_pending": self.resume_pending,
        }


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _state_from_row(
    project_id: UUID, row: BookProductionControlModel | None
) -> ProductionControlState:
    if row is None:
        return ProductionControlState(project_id=project_id)
    try:
        intent = ProductionIntent(str(row.desired_state))
    except ValueError:
        # An unrecognised state must fail safe: treat it as halted rather than
        # letting a typo or a future enum value silently authorise auto-resume.
        logger.warning(
            "unknown production intent %r for project %s; treating as stop",
            row.desired_state,
            project_id,
        )
        intent = ProductionIntent.STOP
    return ProductionControlState(
        project_id=project_id,
        intent=intent,
        reason=row.state_reason,
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        command_serial=int(row.command_serial or 0),
        resume_pending=bool(row.resume_pending),
        resume_pending_at=row.resume_pending_at,
        detail=dict(row.detail_json or {}),
    )


async def load_control_state(
    session: AsyncSession,
    project_id: UUID,
    *,
    for_update: bool = False,
) -> ProductionControlState:
    """Read current operator intent.

    ``for_update`` takes a row lock so a recovery path can read-and-act without
    a concurrent stop slipping in between. Recovery paths should use it.
    """

    stmt = select(BookProductionControlModel).where(
        BookProductionControlModel.project_id == project_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    # Tolerate sessions that cannot answer (test doubles, sessions stubbed by a
    # caller that does not model this table). "Cannot determine intent" must
    # mean *runnable*: the halt is separately enforced at the worker entry point
    # and in the self-heal sweep, so failing open here costs a late stop at
    # worst, while failing closed would strand every book behind a stub.
    if result is None:
        return ProductionControlState(project_id=project_id)
    row = result.scalar_one_or_none()
    return _state_from_row(project_id, row)


async def halted_project_ids(session: AsyncSession) -> frozenset[UUID]:
    """Every project the operator has paused or stopped.

    Sweep-shaped helper for self-heal: one indexed query instead of a lookup per
    project. Callers that are about to *act* on a single book should still
    re-read that book's state with ``for_update=True`` — this set is a filter,
    not a lock.
    """

    try:
        # ``scalars`` rather than ``execute``: this is a read, and routing it
        # through the write-shaped path is both less honest and, for callers
        # that wrap the session, easy to mistake for a mutation.
        rows = await session.scalars(
            select(BookProductionControlModel.project_id).where(
                BookProductionControlModel.desired_state != ProductionIntent.RUN.value
            )
        )
        return frozenset(rows.all())
    except Exception:
        # A sweep that cannot read intent must not stall every book on the
        # platform. Enforcement still happens at the worker entry point, which
        # reads this per project immediately before running work.
        logger.warning(
            "could not load halted project ids; self-heal will rely on the "
            "per-task guard for this sweep",
            exc_info=True,
        )
        return frozenset()


async def _upsert(
    session: AsyncSession,
    project_id: UUID,
    *,
    intent: ProductionIntent,
    reason: str | None,
    actor: str | None,
    resume_pending: bool,
    detail: Mapping[str, Any] | None,
) -> ProductionControlState:
    stmt = (
        select(BookProductionControlModel)
        .where(BookProductionControlModel.project_id == project_id)
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    now = _now()
    if row is None:
        row = BookProductionControlModel(
            project_id=project_id,
            desired_state=intent.value,
            state_reason=reason,
            requested_by=actor,
            requested_at=now,
            command_serial=1,
            resume_pending=resume_pending,
            resume_pending_at=now if resume_pending else None,
            detail_json=dict(detail or {}),
        )
        session.add(row)
    else:
        row.desired_state = intent.value
        row.state_reason = reason
        row.requested_by = actor
        row.requested_at = now
        # Every command invalidates in-flight derivatives.
        row.command_serial = int(row.command_serial or 0) + 1
        row.resume_pending = resume_pending
        row.resume_pending_at = now if resume_pending else None
        if detail is not None:
            row.detail_json = dict(detail)
    await session.flush()
    return _state_from_row(project_id, row)


async def request_stop(
    session: AsyncSession,
    project_id: UUID,
    *,
    actor: str | None = None,
    reason: str | None = None,
    detail: Mapping[str, Any] | None = None,
) -> ProductionControlState:
    """Stop this book. Clears any pending resume so it cannot be revived."""

    state = await _upsert(
        session,
        project_id,
        intent=ProductionIntent.STOP,
        reason=reason,
        actor=actor,
        resume_pending=False,
        detail=detail,
    )
    logger.info(
        "production control: STOP project=%s actor=%s reason=%s serial=%d",
        project_id,
        actor,
        reason,
        state.command_serial,
    )
    return state


async def request_pause(
    session: AsyncSession,
    project_id: UUID,
    *,
    actor: str | None = None,
    reason: str | None = None,
    detail: Mapping[str, Any] | None = None,
) -> ProductionControlState:
    """Pause this book. Like stop for auto-recovery purposes, but reversible."""

    state = await _upsert(
        session,
        project_id,
        intent=ProductionIntent.PAUSE,
        reason=reason,
        actor=actor,
        resume_pending=False,
        detail=detail,
    )
    logger.info(
        "production control: PAUSE project=%s actor=%s reason=%s serial=%d",
        project_id,
        actor,
        reason,
        state.command_serial,
    )
    return state


async def request_run(
    session: AsyncSession,
    project_id: UUID,
    *,
    actor: str | None = None,
    reason: str | None = None,
    detail: Mapping[str, Any] | None = None,
) -> ProductionControlState:
    """Clear a halt. Does not itself queue work — see ``mark_resume_pending``."""

    state = await _upsert(
        session,
        project_id,
        intent=ProductionIntent.RUN,
        reason=reason,
        actor=actor,
        resume_pending=False,
        detail=detail,
    )
    logger.info(
        "production control: RUN project=%s actor=%s reason=%s serial=%d",
        project_id,
        actor,
        reason,
        state.command_serial,
    )
    return state


async def mark_resume_pending(
    session: AsyncSession,
    project_id: UUID,
    *,
    actor: str | None = None,
    reason: str | None = None,
) -> ProductionControlState:
    """Record a durable intent to resume, before queueing the actual work.

    Persist this *first*, then dispatch. If the process dies in between, the
    pending flag is still on disk and startup recovery can honour it. Refuses
    to arm a resume for a book the operator halted.
    """

    stmt = (
        select(BookProductionControlModel)
        .where(BookProductionControlModel.project_id == project_id)
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    current = _state_from_row(project_id, row)
    if current.halted:
        logger.info(
            "production control: refusing resume intent for halted project=%s "
            "(intent=%s)",
            project_id,
            current.intent.value,
        )
        return current

    now = _now()
    if row is None:
        row = BookProductionControlModel(
            project_id=project_id,
            desired_state=ProductionIntent.RUN.value,
            state_reason=reason,
            requested_by=actor,
            requested_at=now,
            command_serial=1,
            resume_pending=True,
            resume_pending_at=now,
            detail_json={},
        )
        session.add(row)
    else:
        row.resume_pending = True
        row.resume_pending_at = now
        if reason:
            row.state_reason = reason
        if actor:
            row.requested_by = actor
    await session.flush()
    return _state_from_row(project_id, row)


async def claim_resume_pending(
    session: AsyncSession,
    project_id: UUID,
    *,
    expected_command_serial: int | None = None,
) -> bool:
    """Consume a pending resume exactly once.

    Returns ``True`` only for the caller that actually claimed it, so two
    workers racing on the same book cannot both dispatch. When
    ``expected_command_serial`` is supplied it acts as a compare-and-swap: a
    caller holding a stale view (a command landed after it made its decision)
    loses the claim and must re-read.
    """

    stmt = (
        select(BookProductionControlModel)
        .where(BookProductionControlModel.project_id == project_id)
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None or not row.resume_pending:
        return False

    state = _state_from_row(project_id, row)
    if state.halted:
        # A stop landed after the resume was armed. Disarm it and refuse.
        row.resume_pending = False
        row.resume_pending_at = None
        await session.flush()
        logger.info(
            "production control: discarding resume intent for halted project=%s",
            project_id,
        )
        return False

    if (
        expected_command_serial is not None
        and int(row.command_serial or 0) != int(expected_command_serial)
    ):
        logger.info(
            "production control: stale resume claim for project=%s "
            "(expected serial=%s, actual=%s)",
            project_id,
            expected_command_serial,
            row.command_serial,
        )
        return False

    row.resume_pending = False
    row.resume_pending_at = None
    await session.flush()
    return True


def auto_recovery_is_permitted(state: ProductionControlState) -> bool:
    """The single predicate every automatic recovery path must consult.

    Call it with a state read *inside the acting transaction* (ideally with
    ``for_update=True``). A value fetched earlier in the sweep is exactly the
    stale read that let stopped books come back.
    """

    return not state.halted
