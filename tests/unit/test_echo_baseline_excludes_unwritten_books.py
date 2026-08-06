"""Cross-book echo must compare against books that actually exist as prose.

2026-08-06: a book died at the conception pollution gate with
``collisions=["东方玄幻·构思中"]`` — the colliding "previous book" was a
0-chapter husk still carrying its conception placeholder title, abandoned
minutes earlier and deleted minutes later. The library was otherwise empty, so
the only thing this book plagiarised was a story that had never been written.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from bestseller.services.conception import _recent_core_mechanisms

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Row(tuple):
    """Mimics the 5-tuple the query now selects."""


def _project_row(title: str, project_id: Any) -> tuple:
    return (
        title,
        "东方玄幻",
        "东方玄幻",
        {
            "premise": "少年借他人一息灵机逆袭",
            "writing_profile": {
                "character": {"golden_finger": "借灵机"},
                "market": {"trope_keywords": ["血脉觉醒", "废柴逆袭"]},
            },
        },
        project_id,
    )


class _FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def all(self) -> list[tuple]:
        return self._rows


class _FakeSession:
    """Returns project rows for the first query, chapter owners for the second."""

    def __init__(self, project_rows: list[tuple], written_ids: list[Any]) -> None:
        self._project_rows = project_rows
        self._written = [(pid,) for pid in written_ids]
        self._calls = 0

    async def execute(self, _stmt: Any) -> _FakeResult:
        self._calls += 1
        if self._calls == 1:
            return _FakeResult(self._project_rows)
        return _FakeResult(self._written)


async def test_unwritten_book_is_not_prior_art() -> None:
    husk_id = uuid4()
    session = _FakeSession([_project_row("东方玄幻·构思中", husk_id)], written_ids=[])

    entries = await _recent_core_mechanisms(
        session, genre="东方玄幻", sub_genre="东方玄幻"
    )

    assert entries == [], "a 0-chapter husk must not count as a previous book"


async def test_written_book_is_still_prior_art() -> None:
    """The de-dup must keep working for books that really exist."""

    real_id = uuid4()
    session = _FakeSession([_project_row("真实旧书", real_id)], written_ids=[real_id])

    entries = await _recent_core_mechanisms(
        session, genre="东方玄幻", sub_genre="东方玄幻"
    )

    assert len(entries) == 1
    assert entries[0].get("title") == "真实旧书"


async def test_mixed_library_keeps_only_the_written_one() -> None:
    husk_id, real_id = uuid4(), uuid4()
    session = _FakeSession(
        [_project_row("东方玄幻·构思中", husk_id), _project_row("真实旧书", real_id)],
        written_ids=[real_id],
    )

    entries = await _recent_core_mechanisms(
        session, genre="东方玄幻", sub_genre="东方玄幻"
    )

    assert [e.get("title") for e in entries] == ["真实旧书"]


async def test_chapter_lookup_failure_fails_open() -> None:
    """Losing the ability to tell must not silently disable cross-book de-dup."""

    real_id = uuid4()

    class _ExplodingSession(_FakeSession):
        async def execute(self, stmt: Any) -> _FakeResult:
            self._calls += 1
            if self._calls == 1:
                return _FakeResult(self._project_rows)
            raise RuntimeError("chapter lookup unavailable")

    session = _ExplodingSession([_project_row("旧书", real_id)], written_ids=[])
    entries = await _recent_core_mechanisms(
        session, genre="东方玄幻", sub_genre="东方玄幻"
    )

    assert len(entries) == 1, "must fall back to the previous behaviour, not to empty"
