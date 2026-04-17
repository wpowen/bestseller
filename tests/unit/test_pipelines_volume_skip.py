"""Regression tests for ``_count_written_chapters_in_volume``.

This helper backs the Phase B volume-skip guard: if every chapter in a volume
is already in a written status, the pipeline must not re-run
``generate_volume_plan`` — which is what re-seeded chapter numbers globally
and produced the 200-chapter gap on xianxia-upgrade-1776137730.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from bestseller.services import pipelines as pipeline_services


pytestmark = pytest.mark.unit


@dataclass
class _FakeScalarResult:
    value: Any

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:  # unused
        return self.value


class _FakeSession:
    """Minimal async session stub: records the select statement and returns a
    preset count from ``scalar``."""

    def __init__(self, count_value: int | None) -> None:
        self._count = count_value
        self.last_stmt: Any = None

    async def scalar(self, stmt: Any) -> Any:
        self.last_stmt = stmt
        return self._count


@pytest.mark.asyncio
async def test_returns_zero_when_scalar_is_none() -> None:
    session = _FakeSession(count_value=None)
    n = await pipeline_services._count_written_chapters_in_volume(
        session, project_id="proj-1", volume_number=3,
    )
    assert n == 0


@pytest.mark.asyncio
async def test_returns_integer_count() -> None:
    session = _FakeSession(count_value=50)
    n = await pipeline_services._count_written_chapters_in_volume(
        session, project_id="proj-1", volume_number=2,
    )
    assert n == 50


@pytest.mark.asyncio
async def test_coerces_non_int_scalar_to_int() -> None:
    session = _FakeSession(count_value="42")
    n = await pipeline_services._count_written_chapters_in_volume(
        session, project_id="proj-1", volume_number=1,
    )
    assert n == 42


def test_written_statuses_exclude_planned_and_outlining() -> None:
    """Planned/outlining must NOT be counted as 'already written'."""
    statuses = pipeline_services._WRITTEN_CHAPTER_STATUSES
    assert "drafting" in statuses
    assert "review" in statuses
    assert "revision" in statuses
    assert "complete" in statuses
    assert "planned" not in statuses
    assert "outlining" not in statuses
