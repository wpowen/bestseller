"""Decide, without a human, whether a book is finished.

Nothing in the pipeline could reach ``ProjectStatus.COMPLETED``. The only
writer of that value is a manual web endpoint that stamps
``manually_marked_completed``; every automatic path writes ``needs_replan``,
``revising``, ``writing`` or ``paused``. The autowrite pipeline's terminal
assignment is::

    project.status = REVISING if requires_human_review else WRITING

so even a flawless run left the book in ``writing`` — a state that claims work
is in progress when nothing is running. Two real three-chapter books finished
every chapter with zero failed workflows and sat there indefinitely
(2026-07-26).

``writing`` as a resting state is not merely cosmetic: the self-heal sweeps,
the dashboard and the export retry all read project status to decide what
still needs doing, so a finished book keeps presenting itself as unfinished
work forever.

This module supplies the missing predicate. It is deliberately free of any
"wait for a human" outcome: a book either is finished (every planned chapter
settled) or it is not (chapters still missing or still in flight). Quality
debt does not park a book — the repair loop already decided to ship those
chapters, and the export gate records the debt on the artifact.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

# States the repair loop leaves behind when it has stopped working on a
# chapter. Mirrors ``exports.EXPORT_SHIPPABLE_PRODUCTION_STATES`` — a book is
# finished exactly when every chapter is exportable.
SETTLED_PRODUCTION_STATES: Final[frozenset[str]] = frozenset(
    {
        "ok",
        "quality_debt",
        "repair_exhausted",
        "quality_reviewed",
        "needs_human_review",
    }
)

# Debt-carrying settled states, reported so a caller can tell a clean book from
# a shipped-with-defects one without re-deriving the rule.
_DEBT_STATES: Final[frozenset[str]] = SETTLED_PRODUCTION_STATES - {"ok"}


@dataclass(frozen=True)
class BookClosureVerdict:
    """Whether the book is done, and what it is carrying if so."""

    is_complete: bool
    reason: str
    settled_chapters: int
    expected_chapters: int
    debt_chapters: tuple[int, ...]
    unsettled_chapters: tuple[int, ...]

    @property
    def is_clean(self) -> bool:
        """Complete with no chapter shipped on debt."""

        return self.is_complete and not self.debt_chapters


def _chapter_number(chapter: Any) -> int:
    try:
        return int(getattr(chapter, "chapter_number", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _production_state(chapter: Any) -> str:
    return str(getattr(chapter, "production_state", "") or "").strip().lower()


def evaluate_book_closure(
    chapters: Iterable[Any],
    *,
    expected_chapters: int | None = None,
) -> BookClosureVerdict:
    """Return whether every planned chapter has settled.

    ``expected_chapters`` is the book's plan (``projects.target_chapters``).
    When it is unknown or non-positive the chapter rows themselves define the
    book — a project that never recorded a target must still be able to finish
    rather than being stranded by missing metadata.

    A book with no chapters at all is never complete; that is a book which has
    not started, not one that has ended.
    """

    rows = [chapter for chapter in chapters if chapter is not None]
    numbers = sorted({_chapter_number(row) for row in rows if _chapter_number(row) > 0})

    try:
        planned = int(expected_chapters or 0)
    except (TypeError, ValueError):
        planned = 0
    planned = planned if planned > 0 else len(numbers)

    if not numbers or planned <= 0:
        return BookClosureVerdict(
            is_complete=False,
            reason="no_chapters",
            settled_chapters=0,
            expected_chapters=max(planned, 0),
            debt_chapters=(),
            unsettled_chapters=(),
        )

    state_by_number: dict[int, str] = {}
    for row in rows:
        number = _chapter_number(row)
        if number > 0:
            state_by_number[number] = _production_state(row)

    unsettled: list[int] = []
    debt: list[int] = []
    settled = 0
    for number in range(1, planned + 1):
        state = state_by_number.get(number)
        if state is None:
            unsettled.append(number)
            continue
        if state not in SETTLED_PRODUCTION_STATES:
            unsettled.append(number)
            continue
        settled += 1
        if state in _DEBT_STATES:
            debt.append(number)

    if unsettled:
        return BookClosureVerdict(
            is_complete=False,
            reason="chapters_unsettled",
            settled_chapters=settled,
            expected_chapters=planned,
            debt_chapters=tuple(debt),
            unsettled_chapters=tuple(unsettled),
        )

    return BookClosureVerdict(
        is_complete=True,
        reason="all_chapters_settled" if not debt else "all_chapters_settled_with_debt",
        settled_chapters=settled,
        expected_chapters=planned,
        debt_chapters=tuple(debt),
        unsettled_chapters=(),
    )


async def settle_project_status_on_closure(
    session: Any,
    project: Any,
    *,
    fallback_status: str,
    now_iso: str,
) -> BookClosureVerdict:
    """Stamp COMPLETED when the book has finished; otherwise leave the caller's
    status. Returns the verdict so the caller can also settle its workflow row.

    Both terminal paths must call this. ``run_project_pipeline`` and
    ``run_project_repair`` each carry their own copy of

        project.status = REVISING if requires_human_review else WRITING

    and a live run (2026-07-28, urban-power-reversal-1785201018) exited through
    the *repair* copy: all three chapters settled, the pipeline copy had already
    run minutes earlier while chapters were still in flight, and the book
    finished in ``revising`` with no export. Patching one copy fixes nothing —
    the last writer wins, and which one that is depends on where the run
    happens to end.
    """

    from sqlalchemy import select

    from bestseller.infra.db.models import ChapterModel

    try:
        chapters = list(
            (
                await session.execute(
                    select(ChapterModel).where(ChapterModel.project_id == project.id)
                )
            )
            .scalars()
            .all()
        )
        verdict = evaluate_book_closure(
            chapters,
            expected_chapters=int(getattr(project, "target_chapters", 0) or 0),
        )
    except Exception:  # noqa: BLE001 - closure must never abort a finished run
        verdict = evaluate_book_closure([], expected_chapters=0)

    if verdict.is_complete:
        project.status = "completed"
        project.metadata_json = {
            **(getattr(project, "metadata_json", None) or {}),
            "completed_at": now_iso,
            "completion_reason": verdict.reason,
            "completion_debt_chapters": list(verdict.debt_chapters),
            "completion_is_clean": verdict.is_clean,
        }
    else:
        project.status = fallback_status
    return verdict


__all__ = [
    "SETTLED_PRODUCTION_STATES",
    "BookClosureVerdict",
    "evaluate_book_closure",
    "settle_project_status_on_closure",
]
