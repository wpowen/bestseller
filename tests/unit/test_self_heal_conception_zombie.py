"""Conception workflow rows must not stay ``running`` forever.

Field state (2026-07-24): two ``conception_initial`` rows sat at
``status=running`` for 7h and 1h respectively while every worker reported
``j_ongoing=0``. Both books had in fact already FAILED at the appeal gate — the
web layer only closes the row on the success path, so any blocked or crashed
conception leaks a permanent zombie.

Why these rows matter rather than being cosmetic: ``workflow_runs`` carries
``uq_conception_workflow_idempotency`` UNIQUE (workflow_type, idempotency_key)
for conception types, so a stale running row keyed on an attempt_id blocks
RETRYING that same creation attempt.

Why conception needs its own reaping rule instead of joining the existing sets:

* It runs in the **web** process and never heartbeats — ``updated_at`` stays
  equal to ``created_at`` for its whole life. Any heartbeat-based rule would
  therefore measure total age, not liveness.
* It must NOT join ``_STARTUP_ONLY_REAPABLE_WORKFLOW_TYPES``: that rule fires
  at worker boot with only ``STARTUP_GRACE_SECONDS`` (5s) of slack, so a
  worker-only restart would mark a perfectly healthy in-flight conception as
  failed.

Hence a dedicated ceiling on total age, keyed on ``created_at``.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from bestseller.worker.self_heal import (
    ORPHAN_CONCEPTION_WORKFLOW_TIMEOUT_SECONDS,
    STARTUP_GRACE_SECONDS,
    reap_orphan_workflow_runs,
)

from test_self_heal import _FakeSession, _FakeWorkflowRun


pytestmark = pytest.mark.unit


@pytest.fixture
def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _conception_run(
    *,
    created_at: _dt.datetime,
    workflow_type: str = "conception_initial",
    status: str = "running",
) -> Any:
    # Conception rows are project-less (the project row is created only after
    # conception succeeds) and never advance updated_at.
    return _FakeWorkflowRun(
        id=uuid4(),
        project_id=None,
        workflow_type=workflow_type,
        status=status,
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.asyncio
async def test_reaps_conception_row_past_the_age_ceiling(now: _dt.datetime) -> None:
    run = _conception_run(
        created_at=now
        - _dt.timedelta(seconds=ORPHAN_CONCEPTION_WORKFLOW_TIMEOUT_SECONDS + 60)
    )
    session = _FakeSession(projects=[], runs=[run], chapters=[], drafts=[])

    reaped = await reap_orphan_workflow_runs(session)

    assert reaped == 1
    assert run.status == "failed"


@pytest.mark.asyncio
async def test_reaps_conception_revision_rows_too(now: _dt.datetime) -> None:
    run = _conception_run(
        created_at=now
        - _dt.timedelta(seconds=ORPHAN_CONCEPTION_WORKFLOW_TIMEOUT_SECONDS + 60),
        workflow_type="conception_revision",
    )
    session = _FakeSession(projects=[], runs=[run], chapters=[], drafts=[])

    assert await reap_orphan_workflow_runs(session) == 1
    assert run.status == "failed"


@pytest.mark.asyncio
async def test_keeps_a_live_conception_that_is_merely_slow(now: _dt.datetime) -> None:
    """A normal conception runs ~20-30 min with zero row updates. Reaping it
    mid-flight would fail a book that is actively being written."""

    run = _conception_run(created_at=now - _dt.timedelta(minutes=30))
    session = _FakeSession(projects=[], runs=[run], chapters=[], drafts=[])

    assert await reap_orphan_workflow_runs(session) == 0
    assert run.status == "running"


@pytest.mark.asyncio
async def test_worker_boot_does_not_reap_a_young_conception(now: _dt.datetime) -> None:
    """THE false-positive guard: a worker-only restart must not kill an
    in-flight conception owned by the still-running web process.

    This is why conception is excluded from the startup_cutoff rules, whose
    grace window is only STARTUP_GRACE_SECONDS.
    """

    run = _conception_run(created_at=now - _dt.timedelta(minutes=10))
    session = _FakeSession(projects=[], runs=[run], chapters=[], drafts=[])

    reaped = await reap_orphan_workflow_runs(
        session,
        startup_cutoff=now - _dt.timedelta(seconds=STARTUP_GRACE_SECONDS),
    )

    assert reaped == 0
    assert run.status == "running"


@pytest.mark.asyncio
async def test_already_finished_conception_rows_are_left_alone(
    now: _dt.datetime,
) -> None:
    old = now - _dt.timedelta(
        seconds=ORPHAN_CONCEPTION_WORKFLOW_TIMEOUT_SECONDS + 600
    )
    done = _conception_run(created_at=old, status="completed")
    failed = _conception_run(created_at=old, status="failed")
    session = _FakeSession(projects=[], runs=[done, failed], chapters=[], drafts=[])

    assert await reap_orphan_workflow_runs(session) == 0
    assert done.status == "completed"
    assert failed.status == "failed"


def test_conception_ceiling_sits_above_a_realistic_conception() -> None:
    """Guard the constant itself: a normal conception is ~20-30 min and a
    known-stuck one sat 1.5h, so the ceiling must clear both with margin."""

    assert ORPHAN_CONCEPTION_WORKFLOW_TIMEOUT_SECONDS >= 2 * 60 * 60
