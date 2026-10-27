"""Status labels count progress; they never authorize adoption or delivery."""

from types import SimpleNamespace

import pytest

from bestseller.services.book_closure import (
    SETTLED_PRODUCTION_STATES,
    _promote_settled_chapter_drafts,
    evaluate_book_closure,
)

pytestmark = pytest.mark.unit


def chapter(number, state="ok", status="complete"):
    return SimpleNamespace(chapter_number=number, production_state=state, status=status)


@pytest.mark.parametrize("state", sorted(SETTLED_PRODUCTION_STATES))
@pytest.mark.parametrize("status", ["planned", "revision", "complete"])
def test_labels_without_exact_adoption_and_knowledge_never_complete(state, status):
    result = evaluate_book_closure([chapter(1, state, status)], expected_chapters=1)
    assert not result.is_complete and not result.is_clean and not result.body_complete


@pytest.mark.parametrize(
    ("rows", "target", "reason", "unsettled"),
    [
        ([], 4, "no_chapters", (1, 2, 3, 4)),
        ([chapter(1), chapter(3)], 3, "chapters_unsettled", (2,)),
        ([chapter(1), chapter(1)], 1, "duplicate_chapters", (1,)),
        ([chapter(1), chapter(2)], 1, "unexpected_chapters", (2,)),
        ([chapter(0)], 1, "unexpected_chapters", (0, 1)),
        ([chapter(1, "blocked")], 1, "chapters_unsettled", (1,)),
        ([chapter(1, "pending")], 1, "chapters_unsettled", (1,)),
    ],
)
def test_census_rejects_missing_duplicate_unplanned_and_unsettled(rows, target, reason, unsettled):
    result = evaluate_book_closure(rows, expected_chapters=target)
    assert not result.is_complete and result.reason == reason
    assert result.unsettled_chapters == unsettled


@pytest.mark.parametrize("target", [None, 0, -1, "invalid"])
def test_missing_plan_cannot_be_inferred_from_existing_rows(target):
    result = evaluate_book_closure([chapter(1)], expected_chapters=target)
    assert not result.is_complete and result.reason == "chapter_target_missing"


def test_progress_and_debt_are_retained_without_completion_authority():
    result = evaluate_book_closure([chapter(1), chapter(2, " Quality_Debt ")], expected_chapters=2)
    assert result.settled_chapters == 2
    assert result.debt_chapters == (2,)
    assert result.reason == "adoption_knowledge_required"
    assert not result.is_complete


async def test_legacy_promotion_api_cannot_promote():
    with pytest.raises(ValueError, match="book_closure_promotion_removed"):
        await _promote_settled_chapter_drafts(None, SimpleNamespace())
