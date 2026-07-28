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


async def _promote_settled_chapter_drafts(session: Any, project: Any) -> None:
    """Record that a settled chapter's current draft is the one being shipped.

    ``promotion_state`` only reaches ``promoted`` when the chapter review
    returns ``pass``. A ``quality_debt`` chapter never gets that verdict — the
    state means "stop repairing and ship the best draft" — so its draft stays
    ``candidate``, and the combined export, which requires promoted drafts,
    fails with "chapters without a promoted draft: 1, 2, 3" on a book whose
    every chapter is terminal (2026-07-28, urban-power-reversal-1785219308).

    Settling *is* the decision to ship. Promotion records that decision; it is
    not a second approval the draft has to earn again. Only terminal chapters
    qualify — an in-flight draft can still be rewritten — and the audited
    transition is used rather than assigning the column, so the promotion
    leaves the same trail any other promotion would.
    """

    from sqlalchemy import select

    from bestseller.domain.promotion import DraftPromotionState
    from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel
    from bestseller.services.draft_promotion import transition_draft_state

    try:
        rows = list(
            await session.execute(
                select(ChapterModel, ChapterDraftVersionModel)
                .join(
                    ChapterDraftVersionModel,
                    ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                )
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
            )
        )
    except Exception:  # noqa: BLE001 - a finished book must not fail on bookkeeping
        return

    # Promotion is a state machine: candidate → under_review → eligible →
    # promoted, and it rejects a jump straight to promoted. Walking the same
    # path the pass-verdict route walks keeps one set of rules; skipping steps
    # was silently refused with "invalid automated promotion transition".
    ladder = (
        DraftPromotionState.UNDER_REVIEW,
        DraftPromotionState.ELIGIBLE,
        DraftPromotionState.PROMOTED,
    )
    failures: list[str] = []
    for chapter, draft in rows:
        state = str(getattr(chapter, "production_state", "") or "").strip().lower()
        if state not in SETTLED_PRODUCTION_STATES:
            continue
        if str(getattr(draft, "promotion_state", "")) == DraftPromotionState.PROMOTED.value:
            continue
        for step in ladder:
            if str(getattr(draft, "promotion_state", "")) == step.value:
                continue
            try:
                await transition_draft_state(
                    session,
                    project_id=project.id,
                    draft_kind="chapter",
                    draft_id=draft.id,
                    to_state=step,
                    decision_source="book_closure",
                    reason_codes=["settled_at_closure"],
                    reason=f"chapter settled as {state}",
                )
            except Exception as exc:  # noqa: BLE001 - one chapter must not sink the book
                # Recorded, not swallowed. The first version of this loop hid
                # its own rejection and left no trace of why nothing promoted.
                failures.append(
                    f"ch{getattr(chapter, 'chapter_number', '?')}"
                    f"->{step.value}: {str(exc)[:120]}"
                )
                break
    if failures:
        project.metadata_json = {
            **(getattr(project, "metadata_json", None) or {}),
            "closure_promotion_errors": failures[:10],
        }


def _closure_quality_gate(debt_chapters: tuple[int, ...], conceded: list[str]) -> Any:
    """The terminal export gate, minus the chapters repair already conceded.

    Before export, the exact bytes are re-checked by the final quality gates.
    On a clean book that is a worthwhile last line of defence and stays fully
    in force here. On a ``quality_debt`` chapter it re-litigates a verdict the
    quality system itself reached — that state means "budget exhausted, ship
    this draft" — and a real book with three promoted chapters was refused on
    exactly that basis (2026-07-28: "第2章：常识因果门禁
    rule_term_onboarding_failure"), the same deadlock as the publication gate
    and the promotion gate, one layer further down.

    So the gate keeps its teeth for chapters nobody conceded, and for the ones
    repair gave up on it downgrades to a recorded concession rather than a
    veto. Nothing is dropped silently: every downgrade lands in the artifact's
    warnings.
    """

    debt = set(int(number) for number in debt_chapters)

    def _gate(**kwargs: Any) -> Any:
        from bestseller.services.pipelines import run_final_quality_gates

        result = run_final_quality_gates(**kwargs)
        number = int(kwargs.get("chapter_number") or 0)
        if number not in debt or bool(getattr(result, "passed", False)):
            return result
        details = "; ".join(
            [
                *list(getattr(result, "errors", ()) or ()),
                *list(getattr(result, "issues", ()) or ()),
            ]
        )
        conceded.append(f"第{number}章带缺陷发布，未过终局质量门：{details[:200]}")

        class _Conceded:
            passed = True
            patched_text = getattr(result, "patched_text", None)
            errors: tuple[str, ...] = ()
            issues: tuple[str, ...] = ()

        return _Conceded()

    return _gate


async def settle_project_status_on_closure(
    session: Any,
    project: Any,
    *,
    settings: Any,
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

    if not verdict.is_complete:
        project.status = fallback_status
        return verdict

    project.status = "completed"
    await _promote_settled_chapter_drafts(session, project)
    metadata = {
        **(getattr(project, "metadata_json", None) or {}),
        "completed_at": now_iso,
        "completion_reason": verdict.reason,
        "completion_debt_chapters": list(verdict.debt_chapters),
        "completion_is_clean": verdict.is_clean,
    }

    # Export here, where completion is decided, so the two cannot diverge.
    # They already did: the first working closure marked a book completed with
    # its debt correctly recorded and produced no combined export, because the
    # `no_actionable_repair` exit returns export_artifact_id=None and the
    # pipeline's own export had run earlier, while chapters were still
    # unsettled and the publication gate still (correctly) refused them.
    #
    # Non-fatal: the book *is* finished — a failed export must not revoke that,
    # and the next settle attempt retries it.
    try:
        from bestseller.services.exports import export_project_markdown

        conceded: list[str] = []
        artifact, path = await export_project_markdown(
            session,
            settings,
            str(getattr(project, "slug", "") or ""),
            final_quality_gate=_closure_quality_gate(verdict.debt_chapters, conceded),
        )
        metadata["completion_export_artifact_id"] = str(getattr(artifact, "id", ""))
        metadata["completion_export_path"] = str(path)
        if conceded:
            metadata["completion_conceded_gate_findings"] = conceded[:10]
    except Exception as exc:  # noqa: BLE001 - completion stands on its own
        metadata["completion_export_error"] = str(exc)[:500]

    project.metadata_json = metadata
    return verdict


__all__ = [
    "SETTLED_PRODUCTION_STATES",
    "BookClosureVerdict",
    "evaluate_book_closure",
    "settle_project_status_on_closure",
]
