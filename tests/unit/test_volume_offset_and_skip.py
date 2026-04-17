"""Regression tests for the DB-backed offset + skip-guard helpers.

The Phase B skip guard and chapter_number offset must be derived *only* from
the ``chapters``/``volumes`` tables — never from VOLUME_PLAN targets. VOLUME_PLAN
drift was the root cause of the 200-chapter gap on ``xianxia-upgrade-1776137730``
(vol 4 got ch 351+ after only 150 had been drafted, because ``target_sum``
of drifted earlier volumes was 350).

These tests stub ``AsyncSession.scalar`` to mirror SQL behaviour without a
real DB, keeping the unit-test boundary tight around the helper logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from bestseller.services import pipelines as pipeline_services
from bestseller.services import planner as planner_services


pytestmark = pytest.mark.unit


@dataclass
class _ScalarQueue:
    """Return one preset scalar value per call, in order."""

    values: list[Any]
    calls: list[Any] = field(default_factory=list)

    async def scalar(self, stmt: Any) -> Any:
        self.calls.append(stmt)
        if not self.values:
            raise AssertionError("scalar() called more times than values provided")
        return self.values.pop(0)


# ---------------------------------------------------------------------------
# _next_chapter_number_for_volume — planner.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offset_uses_prior_volume_max_when_present() -> None:
    """If vols < N have drafted chapters, offset = max(those) + 1."""
    # First scalar: max chapter_number in prior volumes → 150.
    session = _ScalarQueue(values=[150])
    offset = await planner_services._next_chapter_number_for_volume(
        session, project_id=uuid4(), volume_number=4,
    )
    assert offset == 151
    # Only the prior-volume query was issued — project-wide fallback unused.
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_offset_falls_back_to_project_max_when_no_prior_volume() -> None:
    """When no chapters exist in vols < N, fall back to project-wide max."""
    # prior_max=0 (None → 0) forces the fallback; any_max=80 → 81.
    session = _ScalarQueue(values=[None, 80])
    offset = await planner_services._next_chapter_number_for_volume(
        session, project_id=uuid4(), volume_number=1,
    )
    assert offset == 81
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_offset_is_one_on_empty_project() -> None:
    session = _ScalarQueue(values=[None, None])
    offset = await planner_services._next_chapter_number_for_volume(
        session, project_id=uuid4(), volume_number=1,
    )
    assert offset == 1


@pytest.mark.asyncio
async def test_offset_never_depends_on_volume_plan_targets() -> None:
    """Even if caller has drifted targets (350), offset tracks DB (151)."""
    session = _ScalarQueue(values=[150])
    offset = await planner_services._next_chapter_number_for_volume(
        session, project_id=uuid4(), volume_number=4,
    )
    assert offset == 151  # NOT 351 — would have been the bug.


# ---------------------------------------------------------------------------
# _volume_fully_written — pipelines.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fully_written_returns_false_on_empty_volume() -> None:
    """A volume with 0 chapters in DB must NOT be treated as 'done'."""
    session = _ScalarQueue(values=[0])  # total=0
    done, written, total = await pipeline_services._volume_fully_written(
        session, project_id=uuid4(), volume_number=7,
    )
    assert done is False
    assert total == 0
    # No point querying written_count when total is zero.
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_fully_written_when_all_chapters_in_written_status() -> None:
    session = _ScalarQueue(values=[50, 50])  # total=50, written=50
    done, written, total = await pipeline_services._volume_fully_written(
        session, project_id=uuid4(), volume_number=3,
    )
    assert done is True
    assert written == 50
    assert total == 50


@pytest.mark.asyncio
async def test_partial_progress_is_not_skipped() -> None:
    """49/50 chapters written must not flip the skip guard."""
    session = _ScalarQueue(values=[50, 49])
    done, written, total = await pipeline_services._volume_fully_written(
        session, project_id=uuid4(), volume_number=3,
    )
    assert done is False
    assert written == 49
    assert total == 50


@pytest.mark.asyncio
async def test_skip_decision_independent_of_plan_target() -> None:
    """If DB has 30 chapters all written, skip — even if plan target said 50.

    This is the anti-drift property: skip depends on DB coverage, not plan.
    """
    session = _ScalarQueue(values=[30, 30])
    done, _written, _total = await pipeline_services._volume_fully_written(
        session, project_id=uuid4(), volume_number=3,
    )
    assert done is True
