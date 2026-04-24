"""Unit tests for :func:`pipelines.maybe_persist_opening_archetype` (C4).

The helper writes the L3 ``PromptConstructor``'s chosen opening archetype
onto the ``ChapterModel`` row the first time a scene of that chapter is
generated.  Without this step the pick only lives in in-memory diversity
budget state and cross-project novelty audits cannot see it.

The helper must be:

1. **Idempotent** — once ``chapter.opening_archetype`` is set, every later
   call for the same chapter is a no-op (the first scene "wins").
2. **Non-fatal** — a DB hiccup or a flush failure must not propagate; the
   scene generation pipeline cannot be held hostage by an audit trail write.
3. **Enum-aware** — accepts both an ``OpeningArchetype`` member (via
   ``.value``) and a bare string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from bestseller.services.invariants import OpeningArchetype
from bestseller.services.pipelines import maybe_persist_opening_archetype


pytestmark = pytest.mark.unit


# ── Test doubles ───────────────────────────────────────────────────────


@dataclass
class FakeChapter:
    """Minimal stand-in for ``ChapterModel``.

    Only the one attribute the helper touches is modelled.
    """

    opening_archetype: str | None = None


@dataclass
class FakeSession:
    flush_calls: int = 0
    flush_error: Exception | None = None
    # Every value flushed during this session's lifetime — lets tests
    # verify the exact string written on each successful call.
    observed_values: list[str] = field(default_factory=list)

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_error is not None:
            raise self.flush_error


# ── Happy path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persists_enum_value_on_first_call() -> None:
    chapter = FakeChapter()
    session = FakeSession()

    did_write = await maybe_persist_opening_archetype(
        session,
        chapter=chapter,
        assigned_opening=OpeningArchetype.HUMILIATION,
        chapter_number=1,
    )

    assert did_write is True
    assert chapter.opening_archetype == "humiliation"
    assert session.flush_calls == 1


@pytest.mark.asyncio
async def test_accepts_bare_string_opening() -> None:
    """Callers occasionally pass raw strings (e.g. from yaml) — accept them."""
    chapter = FakeChapter()
    session = FakeSession()

    did_write = await maybe_persist_opening_archetype(
        session,
        chapter=chapter,
        assigned_opening="crisis",
        chapter_number=5,
    )

    assert did_write is True
    assert chapter.opening_archetype == "crisis"
    assert session.flush_calls == 1


# ── Idempotency ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_call_is_noop_when_already_set() -> None:
    """First scene of a chapter wins the archetype — later scenes must not
    clobber the persisted value, even with a different opening pick."""
    chapter = FakeChapter(opening_archetype="humiliation")  # already set
    session = FakeSession()

    did_write = await maybe_persist_opening_archetype(
        session,
        chapter=chapter,
        assigned_opening=OpeningArchetype.CRISIS,  # different pick
        chapter_number=1,
    )

    assert did_write is False
    assert chapter.opening_archetype == "humiliation"  # unchanged
    assert session.flush_calls == 0  # no flush when idempotent


@pytest.mark.asyncio
async def test_two_back_to_back_calls_only_flush_once() -> None:
    chapter = FakeChapter()
    session = FakeSession()

    first = await maybe_persist_opening_archetype(
        session,
        chapter=chapter,
        assigned_opening=OpeningArchetype.BANISHMENT,
        chapter_number=3,
    )
    second = await maybe_persist_opening_archetype(
        session,
        chapter=chapter,
        assigned_opening=OpeningArchetype.BETRAYAL,
        chapter_number=3,
    )

    assert first is True
    assert second is False
    assert chapter.opening_archetype == "banishment"
    assert session.flush_calls == 1


# ── Missing assigned_opening ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_none_assigned_opening_is_noop() -> None:
    chapter = FakeChapter()
    session = FakeSession()

    did_write = await maybe_persist_opening_archetype(
        session,
        chapter=chapter,
        assigned_opening=None,
        chapter_number=1,
    )

    assert did_write is False
    assert chapter.opening_archetype is None
    assert session.flush_calls == 0


# ── Failure paths: must never propagate ────────────────────────────────


@pytest.mark.asyncio
async def test_flush_failure_is_swallowed() -> None:
    """A transient DB error must not bubble up — scene generation must
    continue even if the audit-trail write fails."""
    chapter = FakeChapter()
    session = FakeSession(flush_error=RuntimeError("db down"))

    did_write = await maybe_persist_opening_archetype(
        session,
        chapter=chapter,
        assigned_opening=OpeningArchetype.ENCOUNTER,
        chapter_number=7,
    )

    # Helper returns False on failure; no exception raised.
    assert did_write is False
    # The in-memory attribute was set optimistically before the flush
    # attempt — this is fine, the row will be rolled back by the outer
    # transaction and the next scene will retry.
    assert chapter.opening_archetype == "encounter"
    assert session.flush_calls == 1


@pytest.mark.asyncio
async def test_broken_chapter_attribute_is_swallowed() -> None:
    """If the chapter proxy throws when we set the attribute (e.g. SQLAlchemy
    detached instance), the helper still returns False rather than bubbling."""

    class BrokenChapter:
        @property
        def opening_archetype(self) -> str | None:  # noqa: D401
            return None

        @opening_archetype.setter
        def opening_archetype(self, value: str) -> None:  # noqa: ARG002
            raise RuntimeError("detached instance")

    session = FakeSession()
    did_write = await maybe_persist_opening_archetype(
        session,
        chapter=BrokenChapter(),
        assigned_opening=OpeningArchetype.SUDDEN_POWER,
        chapter_number=9,
    )

    assert did_write is False
    # Flush must not have been called — setter blew up first.
    assert session.flush_calls == 0


# ── Enum coverage guard ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "opening",
    [
        OpeningArchetype.HUMILIATION,
        OpeningArchetype.CRISIS,
        OpeningArchetype.ENCOUNTER,
        OpeningArchetype.CONTRAST,
        OpeningArchetype.SECRET_REVEAL,
        OpeningArchetype.IDENTITY_FALL,
        OpeningArchetype.BROKEN_ENGAGEMENT,
        OpeningArchetype.BANISHMENT,
        OpeningArchetype.BETRAYAL,
        OpeningArchetype.SUDDEN_POWER,
        OpeningArchetype.RITUAL_INTERRUPTED,
        OpeningArchetype.MUNDANE_DAY,
    ],
)
async def test_every_enum_member_round_trips(opening: OpeningArchetype) -> None:
    """Regression guard: every enum member's ``.value`` is a valid persist
    payload (i.e. the DB column stores exactly what the L3 picker emits)."""
    chapter = FakeChapter()
    session = FakeSession()

    did_write = await maybe_persist_opening_archetype(
        session,
        chapter=chapter,
        assigned_opening=opening,
        chapter_number=1,
    )

    assert did_write is True
    assert chapter.opening_archetype == opening.value


# ── Return value contract ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_return_value_is_bool_not_none() -> None:
    """The helper advertises ``-> bool`` — callers (pipeline metrics, tests)
    rely on the exact ``True/False`` contract instead of truthy/falsy."""
    chapter = FakeChapter()
    session = FakeSession()

    result: Any = await maybe_persist_opening_archetype(
        session,
        chapter=chapter,
        assigned_opening=OpeningArchetype.MUNDANE_DAY,
        chapter_number=2,
    )
    assert result is True  # not just truthy

    result2: Any = await maybe_persist_opening_archetype(
        session,
        chapter=chapter,
        assigned_opening=OpeningArchetype.CRISIS,
        chapter_number=2,
    )
    assert result2 is False  # not just falsy
