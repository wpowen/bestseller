"""Regression test for the ``accept_on_stall`` resume-skip bug.

Background — production incident, 2026-04-17
--------------------------------------------
On 2026-04-17 several active projects (superhero-fiction-1776147970,
romantasy-1776330993, female-no-cp-1776303225, xianxia-upgrade-1776137730,
superhero-fiction-1776301343) exhibited permanent holes in the middle of
the book — e.g. chapter 154 of *The Witness Protocol* existed as a row in
``chapters`` with ``status=revision`` but had **zero** ``chapter_draft_versions``
rows.  Chapters 153 and 155+ around it were ``complete``; the writer had
silently skipped 154.

Root cause
~~~~~~~~~~
``run_project_pipeline`` filters out chapters treated as "done" on resume
(``pipelines.py``).  With the global setting ``accept_on_stall=True`` the
filter lumped *every* REVISION chapter into the done bucket — including
those that had never been fully drafted.  The real sequence for ch 154::

    16:06:34  scene 1 drafted → review flagged rewrite →
              chapter.status := REVISION, rewrite_task queued
    16:06:34  (same tx) scene 2 drafted → review flagged rewrite →
              another rewrite_task queued
    16:09:40  worker crashed / was reaped before the rewrite tasks ran
              and before scene 3 was ever drafted
    resume    chapter in REVISION with 0 chapter drafts; filter dropped
              it; writer proceeded to ch 155 leaving a hole

The fix keeps draftless-REVISION chapters in ``pending_chapters`` so the
writer can finish assembling them on the next run, while still skipping
chapters that *were* fully drafted and only stalled in the rewrite loop
(the case ``accept_on_stall`` was designed for).

What this test pins
~~~~~~~~~~~~~~~~~~~
``_select_pending_chapters_for_resume`` must:

1. Keep a REVISION chapter with zero drafts in the pending list.
2. Skip a REVISION chapter that has >=1 draft (original stall semantics).
3. Always skip COMPLETE chapters.
4. When ``resume_enabled=False``, never skip anything.
5. Surface draftless REVISION chapter numbers so the caller can log them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from bestseller.domain.enums import ChapterStatus
from bestseller.services.pipelines import _select_pending_chapters_for_resume

pytestmark = pytest.mark.unit


@dataclass
class _FakeChapter:
    chapter_number: int
    status: str
    id: UUID = field(default_factory=uuid4)


class _FakeScalarResult:
    """Mimics ``session.scalars(...)`` result — iterable + ``all()``."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)

    def all(self) -> list[Any]:
        return list(self._items)


class _FakeSession:
    """Session stub that returns a preconfigured draft-owning chapter id set.

    We don't evaluate the SQL at all — the helper's contract is "pass the
    REVISION chapter ids into the query and trust the return value".  The
    fake returns whatever ids the test declared as ``drafted``.
    """

    def __init__(self, drafted_chapter_ids: set[UUID]) -> None:
        self._drafted = drafted_chapter_ids
        self.select_called_with: list[Any] = []

    async def scalars(self, stmt: Any) -> _FakeScalarResult:
        # We don't introspect the SQL — just return every drafted id.
        # The helper filters downstream via set membership, so returning
        # the universe is safe.
        return _FakeScalarResult(list(self._drafted))


def _make_chapters() -> list[_FakeChapter]:
    return [
        _FakeChapter(chapter_number=1, status=ChapterStatus.COMPLETE.value),
        _FakeChapter(chapter_number=2, status=ChapterStatus.COMPLETE.value),
        _FakeChapter(chapter_number=3, status=ChapterStatus.REVISION.value),  # drafted
        _FakeChapter(chapter_number=4, status=ChapterStatus.REVISION.value),  # DRAFTLESS — the bug
        _FakeChapter(chapter_number=5, status=ChapterStatus.DRAFTING.value),
        _FakeChapter(chapter_number=6, status=ChapterStatus.PLANNED.value),
    ]


@pytest.mark.asyncio
async def test_draftless_revision_chapter_stays_pending() -> None:
    """The whole point: ch 4 (REVISION, 0 drafts) must NOT be skipped."""
    chapters = _make_chapters()
    ch3_drafted = {chapters[2].id}  # only ch 3 has a chapter draft
    session = _FakeSession(drafted_chapter_ids=ch3_drafted)

    pending, draftless = await _select_pending_chapters_for_resume(
        session,  # type: ignore[arg-type]
        chapters,  # type: ignore[arg-type]
        resume_enabled=True,
        accept_on_stall=True,
    )

    pending_numbers = {ch.chapter_number for ch in pending}
    assert 4 in pending_numbers, (
        "Draftless REVISION chapter must remain pending — otherwise the "
        "writer silently skips it and leaves a permanent hole in the book."
    )
    assert 3 not in pending_numbers, (
        "Drafted REVISION chapter should skip on resume (accept_on_stall "
        "semantics — take the current best draft as-is)."
    )
    assert {1, 2} & pending_numbers == set(), "COMPLETE chapters must skip."
    assert {5, 6} <= pending_numbers, "DRAFTING / PLANNED chapters must stay pending."
    assert draftless == [4]


@pytest.mark.asyncio
async def test_drafted_revision_chapter_skipped_when_accept_on_stall() -> None:
    """Original accept_on_stall semantics still hold for drafted REVISION."""
    chapters = [
        _FakeChapter(chapter_number=1, status=ChapterStatus.REVISION.value),
    ]
    drafted = {chapters[0].id}
    session = _FakeSession(drafted_chapter_ids=drafted)

    pending, draftless = await _select_pending_chapters_for_resume(
        session,  # type: ignore[arg-type]
        chapters,  # type: ignore[arg-type]
        resume_enabled=True,
        accept_on_stall=True,
    )

    assert pending == []
    assert draftless == []


@pytest.mark.asyncio
async def test_revision_always_pending_when_accept_on_stall_disabled() -> None:
    """Without accept_on_stall even drafted REVISION chapters re-run."""
    chapters = [
        _FakeChapter(chapter_number=1, status=ChapterStatus.REVISION.value),
    ]
    drafted = {chapters[0].id}
    session = _FakeSession(drafted_chapter_ids=drafted)

    pending, draftless = await _select_pending_chapters_for_resume(
        session,  # type: ignore[arg-type]
        chapters,  # type: ignore[arg-type]
        resume_enabled=True,
        accept_on_stall=False,
    )

    assert [ch.chapter_number for ch in pending] == [1]
    # accept_on_stall=False → no drafted-id lookup → every REVISION is
    # flagged as draftless here (the caller uses the list only for
    # logging so this is fine; the important invariant is that they
    # stayed in `pending`).
    assert draftless == [1]


@pytest.mark.asyncio
async def test_resume_disabled_returns_every_chapter() -> None:
    """resume_enabled=False short-circuits the whole filter."""
    chapters = _make_chapters()
    session = _FakeSession(drafted_chapter_ids=set())

    pending, draftless = await _select_pending_chapters_for_resume(
        session,  # type: ignore[arg-type]
        chapters,  # type: ignore[arg-type]
        resume_enabled=False,
        accept_on_stall=True,
    )

    assert [ch.chapter_number for ch in pending] == [1, 2, 3, 4, 5, 6]
    assert draftless == []


@pytest.mark.asyncio
async def test_reproduces_witness_protocol_ch_154_incident() -> None:
    """Exact shape observed in prod on 2026-04-17.

    * ch 152, 153 complete (full drafts)
    * ch 154 revision, **no chapter draft** (pending rewrite tasks, scene 3
      never drafted) — the hole
    * ch 155, 156 complete (writer sailed past 154)
    """
    chapters = [
        _FakeChapter(chapter_number=152, status=ChapterStatus.COMPLETE.value),
        _FakeChapter(chapter_number=153, status=ChapterStatus.COMPLETE.value),
        _FakeChapter(chapter_number=154, status=ChapterStatus.REVISION.value),
        _FakeChapter(chapter_number=155, status=ChapterStatus.COMPLETE.value),
        _FakeChapter(chapter_number=156, status=ChapterStatus.COMPLETE.value),
    ]
    session = _FakeSession(drafted_chapter_ids=set())  # ch 154 has no drafts

    pending, draftless = await _select_pending_chapters_for_resume(
        session,  # type: ignore[arg-type]
        chapters,  # type: ignore[arg-type]
        resume_enabled=True,
        accept_on_stall=True,
    )

    assert [ch.chapter_number for ch in pending] == [154], (
        "Post-fix, only the holey chapter 154 must remain pending on resume. "
        "Before the fix this returned [] and ch 154 stayed a permanent hole."
    )
    assert draftless == [154]
