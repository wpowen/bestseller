"""Regression tests for SAVEPOINT isolation around best-effort DB work.

These tests pin down the contract added in the 2026-04-09 fix for the
PendingRollbackError cascade that crashed novel ``suspense-detective-1775662651``
on chapter 2:

* The "extra enhancement" code paths that *swallow* DB errors (continuity
  snapshot loads, voice drift checks, periodic consistency checks, ...) MUST
  wrap their work in ``session.begin_nested()`` so that an internal failure
  rolls back to a savepoint and leaves the outer transaction usable for the
  next chapter, instead of poisoning the shared session into an asyncpg
  ``ERROR`` state.

The tests use a fake :class:`AsyncSession` that emulates the savepoint
contract — we don't need a real Postgres to verify the call shape.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from bestseller.services import context as context_services


pytestmark = pytest.mark.unit


class FakeSavepoint:
    """Async context manager that mirrors SA's ``begin_nested`` semantics.

    On enter, it remembers the parent session was healthy. On exception inside
    the ``async with`` body, it transitions the session back to a usable state
    (the equivalent of ``ROLLBACK TO SAVEPOINT`` followed by SA clearing the
    in-error flag), so the outer transaction can continue.
    """

    def __init__(self, session: "FakeSession") -> None:
        self._session = session
        self.entered = False
        self.exited_with_exception = False
        self.released = False

    async def __aenter__(self) -> "FakeSavepoint":
        self.entered = True
        self._session.savepoint_depth += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._session.savepoint_depth -= 1
        if exc_type is not None:
            # Emulate ROLLBACK TO SAVEPOINT — the session is healthy again.
            self.exited_with_exception = True
            self._session.poisoned = False
        else:
            self.released = True
        # Re-raise any exception so the wrapper's outer ``except`` can handle it.
        return False


class FakeSession:
    """Minimal AsyncSession stand-in that tracks transaction health."""

    def __init__(self) -> None:
        self.poisoned = False
        self.savepoint_depth = 0
        self.savepoints: list[FakeSavepoint] = []
        self.execute_calls: int = 0

    def begin_nested(self) -> FakeSavepoint:
        sp = FakeSavepoint(self)
        self.savepoints.append(sp)
        return sp

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        if self.poisoned:
            raise RuntimeError(
                "PendingRollbackError-equivalent: outer session is poisoned"
            )
        self.execute_calls += 1
        return None


@pytest.mark.asyncio
async def test_safe_load_previous_snapshot_uses_savepoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wrapper must enter ``begin_nested`` before calling the loader."""

    session = FakeSession()
    seen_depths: list[int] = []

    async def fake_loader(*args: Any, **kwargs: Any) -> None:
        seen_depths.append(session.savepoint_depth)
        return None

    monkeypatch.setattr(
        context_services,
        "load_previous_chapter_snapshot",
        fake_loader,
    )

    result = await context_services._safe_load_previous_snapshot(
        session,  # type: ignore[arg-type]
        project_id=uuid4(),
        current_chapter_number=2,
    )

    assert result is None
    assert seen_depths == [1], "loader must run inside an active savepoint"
    assert len(session.savepoints) == 1
    assert session.savepoints[0].released is True
    assert session.savepoints[0].exited_with_exception is False


@pytest.mark.asyncio
async def test_safe_load_previous_snapshot_recovers_from_loader_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the loader raises (e.g. missing table), the wrapper must:

    1. swallow the exception (return ``None``),
    2. rely on the savepoint to roll back the failed query, and
    3. leave the outer session usable for subsequent queries.
    """

    session = FakeSession()

    async def boom(*args: Any, **kwargs: Any) -> None:
        # Simulate the asyncpg UndefinedTableError that triggered the original
        # incident — and *poison* the session the way asyncpg does on error.
        session.poisoned = True
        raise RuntimeError(
            'relation "chapter_state_snapshots" does not exist'
        )

    monkeypatch.setattr(
        context_services,
        "load_previous_chapter_snapshot",
        boom,
    )

    result = await context_services._safe_load_previous_snapshot(
        session,  # type: ignore[arg-type]
        project_id=uuid4(),
        current_chapter_number=2,
    )

    # 1. exception was swallowed
    assert result is None
    # 2. savepoint was rolled back
    assert len(session.savepoints) == 1
    assert session.savepoints[0].entered is True
    assert session.savepoints[0].exited_with_exception is True
    assert session.savepoints[0].released is False
    # 3. outer session is usable again — this is the regression we care about.
    #    Without ``begin_nested``, ``session.poisoned`` would still be True
    #    here and the next query would raise PendingRollbackError.
    assert session.poisoned is False
    await session.execute("SELECT 1")
    assert session.execute_calls == 1


@pytest.mark.asyncio
async def test_safe_load_previous_snapshot_short_circuits_on_chapter_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chapter 1 returns ``None`` from the loader without touching the DB.

    The wrapper still opens a savepoint (cheap, no-op when no query runs)
    and exits cleanly.  This pins the contract that the wrapper does not
    add a special case for chapter 1.
    """

    session = FakeSession()

    async def fake_loader(
        _session: Any,
        *,
        project_id: Any,
        current_chapter_number: int,
    ) -> None:
        # Mirror real loader behavior: chapter 1 → no DB query → return None.
        if current_chapter_number <= 1:
            return None
        raise AssertionError("loader should not query for chapter 1")

    monkeypatch.setattr(
        context_services,
        "load_previous_chapter_snapshot",
        fake_loader,
    )

    result = await context_services._safe_load_previous_snapshot(
        session,  # type: ignore[arg-type]
        project_id=uuid4(),
        current_chapter_number=1,
    )

    assert result is None
    assert len(session.savepoints) == 1
    assert session.savepoints[0].released is True
    assert session.poisoned is False
