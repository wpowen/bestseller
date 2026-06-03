"""Continuation readiness — decouple chapter repair from forward writing.

A book that has one or more *blocked* chapters used to stop writing new
chapters entirely: ``worker.self_heal`` routed every project with a blocked
chapter to repair-first, and the pipeline write-gate refused all writes while
any project-wide pause flag was set. That coupling is correct only when the
blocked chapter's defect would *poison the new chapter* — i.e. when it corrupts
the canon/continuity snapshot that later chapters inherit.

This module makes that distinction explicit and framework-wide:

* A **structural** block (wrong fact, dead-character regression, timeline
  drift, broken material referential integrity) corrupts downstream snapshots,
  so continuation must wait for the repair.
* A **local** block (opening tension, length, dialogue pairing, AI-flavor,
  style signature) is confined to the failing chapter's own prose. Repairing it
  never changes anything a later chapter depends on, so new-chapter writing may
  proceed **in parallel** with the repair.

The per-gate classification lives in :mod:`bestseller.services.gate_registry`
(``chapter_block_is_structural`` / ``continuation_impact``) — the single source
of truth. This module only aggregates blocked chapters into a project-level
decision. Both the scheduler (``worker.self_heal``) and the pipeline write-gate
consume :func:`compute_continuation_readiness`.

Motivating regression: 青囊不语问阴阳 looped ch1's ``qimao_opening_gate``
(a *local* opening-tension check) forever while every later chapter waited.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from bestseller.infra.db.models import ChapterModel
from bestseller.services.gate_registry import chapter_block_is_structural

__all__ = [
    "BlockedChapter",
    "ContinuationReadiness",
    "decide_continuation_readiness",
    "load_blocked_chapters",
    "compute_continuation_readiness",
    "project_has_structural_block",
]


@dataclass(frozen=True)
class BlockedChapter:
    """A chapter in ``production_state == 'blocked'`` and its impact class."""

    chapter_number: int
    is_structural: bool


@dataclass(frozen=True)
class ContinuationReadiness:
    """Whether a project may write new chapters in parallel with repair.

    ``can_continue`` is ``True`` exactly when no *structural* block exists.
    Local-quality blocks are surfaced in ``local_blocked_chapters`` so callers
    can still dispatch their (independent, lower-priority) repair, but they do
    not gate forward writing.
    """

    can_continue: bool
    next_chapter: int | None
    blocking_chapters: tuple[int, ...]
    local_blocked_chapters: tuple[int, ...]
    reason: str


def decide_continuation_readiness(
    blocked_chapters: Sequence[BlockedChapter],
    *,
    next_chapter: int | None = None,
) -> ContinuationReadiness:
    """Pure decision: may forward writing proceed given these blocked chapters?

    Every blocked chapter already has a draft (``production_state`` only becomes
    ``"blocked"`` after a draft exists), so it always precedes the writing
    frontier. A structural block therefore always sits *upstream* of the next
    chapter and poisons it; a local block never does. Hence the rule reduces to
    "continue iff there is no structural block".
    """

    structural = sorted(
        {c.chapter_number for c in blocked_chapters if c.is_structural}
    )
    local = sorted(
        {c.chapter_number for c in blocked_chapters if not c.is_structural}
    )

    can_continue = not structural
    if not blocked_chapters:
        reason = "no blocked chapters"
    elif can_continue:
        reason = (
            f"{len(local)} local-quality block(s) at {local} repaired in "
            "parallel; no structural block gates forward writing"
        )
    else:
        reason = (
            f"structural block at chapter(s) {structural} corrupts the "
            "downstream snapshot — continuation must wait for repair"
        )

    return ContinuationReadiness(
        can_continue=can_continue,
        next_chapter=next_chapter,
        blocking_chapters=tuple(structural),
        local_blocked_chapters=tuple(local),
        reason=reason,
    )


async def load_blocked_chapters(
    session: Any,
    project_id: Any,
) -> list[BlockedChapter]:
    """Load blocked chapters for a project, classified by continuation impact."""

    rows = await session.scalars(
        select(ChapterModel).where(
            ChapterModel.project_id == project_id,
            ChapterModel.production_state == "blocked",
        )
    )
    blocked: list[BlockedChapter] = []
    for chapter in rows:
        blocked.append(
            BlockedChapter(
                chapter_number=int(getattr(chapter, "chapter_number", 0) or 0),
                is_structural=chapter_block_is_structural(
                    getattr(chapter, "metadata_json", None)
                ),
            )
        )
    return blocked


async def compute_continuation_readiness(
    session: Any,
    project_id: Any,
    *,
    next_chapter: int | None = None,
) -> ContinuationReadiness:
    """Project-level continuation readiness from its blocked chapters."""

    blocked = await load_blocked_chapters(session, project_id)
    return decide_continuation_readiness(blocked, next_chapter=next_chapter)


async def project_has_structural_block(session: Any, project_id: Any) -> bool:
    """True when at least one blocked chapter is structural (gates writing)."""

    readiness = await compute_continuation_readiness(session, project_id)
    return not readiness.can_continue
