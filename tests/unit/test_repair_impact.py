"""Unit tests for continuation-readiness decoupling (services.repair_impact)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from bestseller.services.repair_impact import (
    BlockedChapter,
    ContinuationReadiness,
    compute_continuation_readiness,
    decide_continuation_readiness,
    project_has_structural_block,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Pure decision
# ---------------------------------------------------------------------------


def test_no_blocked_chapters_can_continue() -> None:
    readiness = decide_continuation_readiness([], next_chapter=5)
    assert isinstance(readiness, ContinuationReadiness)
    assert readiness.can_continue is True
    assert readiness.blocking_chapters == ()
    assert readiness.local_blocked_chapters == ()
    assert readiness.next_chapter == 5


def test_only_local_blocks_can_continue() -> None:
    readiness = decide_continuation_readiness(
        [
            BlockedChapter(chapter_number=1, is_structural=False),
            BlockedChapter(chapter_number=3, is_structural=False),
        ],
        next_chapter=12,
    )
    assert readiness.can_continue is True
    assert readiness.blocking_chapters == ()
    assert readiness.local_blocked_chapters == (1, 3)


def test_any_structural_block_stops_continuation() -> None:
    readiness = decide_continuation_readiness(
        [
            BlockedChapter(chapter_number=2, is_structural=False),
            BlockedChapter(chapter_number=4, is_structural=True),
        ],
        next_chapter=9,
    )
    assert readiness.can_continue is False
    assert readiness.blocking_chapters == (4,)
    assert readiness.local_blocked_chapters == (2,)
    assert "structural" in readiness.reason


def test_blocking_chapters_are_sorted_and_deduped() -> None:
    readiness = decide_continuation_readiness(
        [
            BlockedChapter(chapter_number=7, is_structural=True),
            BlockedChapter(chapter_number=3, is_structural=True),
            BlockedChapter(chapter_number=7, is_structural=True),
        ]
    )
    assert readiness.blocking_chapters == (3, 7)


# ---------------------------------------------------------------------------
# Async aggregation against a fake session
# ---------------------------------------------------------------------------


@dataclass
class _FakeChapter:
    project_id: Any
    chapter_number: int
    production_state: str = "blocked"
    metadata_json: dict[str, Any] = field(default_factory=dict)


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self):  # noqa: ANN204
        return iter(self._rows)


class _FakeSession:
    def __init__(self, chapters: list[_FakeChapter]) -> None:
        self.chapters = chapters

    async def scalars(self, stmt: Any) -> _FakeScalars:
        # Only blocked chapters for the queried project are returned; the real
        # query filters on production_state == "blocked", which the fixtures
        # already honor.
        return _FakeScalars(
            [c for c in self.chapters if c.production_state == "blocked"]
        )


@pytest.mark.asyncio
async def test_compute_readiness_local_only_continues() -> None:
    session = _FakeSession(
        [
            _FakeChapter(
                project_id="p1",
                chapter_number=1,
                metadata_json={"qimao_opening_gate_blocked": True},
            ),
        ]
    )
    readiness = await compute_continuation_readiness(session, "p1", next_chapter=2)
    assert readiness.can_continue is True
    assert readiness.local_blocked_chapters == (1,)
    assert readiness.blocking_chapters == ()
    assert await project_has_structural_block(session, "p1") is False


@pytest.mark.asyncio
async def test_compute_readiness_structural_blocks() -> None:
    session = _FakeSession(
        [
            _FakeChapter(
                project_id="p1",
                chapter_number=4,
                metadata_json={
                    "blocked_by_material_referential_integrity_gate": True
                },
            ),
        ]
    )
    readiness = await compute_continuation_readiness(session, "p1")
    assert readiness.can_continue is False
    assert readiness.blocking_chapters == (4,)
    assert await project_has_structural_block(session, "p1") is True


@pytest.mark.asyncio
async def test_compute_readiness_mixed_blocks_uses_gate_registry() -> None:
    session = _FakeSession(
        [
            _FakeChapter(
                project_id="p1",
                chapter_number=1,
                metadata_json={"qimao_opening_gate_blocked": True},
            ),
            _FakeChapter(
                project_id="p1",
                chapter_number=6,
                metadata_json={
                    "blocked_by_write_safety_gate": True,
                    "write_safety_block_code": "character_resurrection",
                },
            ),
            _FakeChapter(
                project_id="p1",
                chapter_number=8,
                metadata_json={
                    "blocked_by_write_safety_gate": True,
                    "write_safety_block_code": "block_low",
                },
            ),
        ]
    )
    readiness = await compute_continuation_readiness(session, "p1")
    # ch6 is a canon regression (structural); ch1 opening + ch8 length are local.
    assert readiness.can_continue is False
    assert readiness.blocking_chapters == (6,)
    assert readiness.local_blocked_chapters == (1, 8)
