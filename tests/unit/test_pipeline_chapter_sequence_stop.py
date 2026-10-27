"""Resume must stop a production book at the first uncleared chapter."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.enums import ChapterStatus
from bestseller.services.pipelines import _chapter_requires_sequence_stop

pytestmark = pytest.mark.unit


def _chapter(number: int, *, status: str, production_state: str) -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=number,
        status=status,
        production_state=production_state,
    )


def _result(number: int, *, requires_human_review: bool) -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=number,
        workflow_run_id=uuid4(),
        requires_human_review=requires_human_review,
    )


def test_blocked_chapter_stops_before_later_candidate() -> None:
    settings = SimpleNamespace(quality=SimpleNamespace(draft_mode=False))
    chapters = [
        (_chapter(1, status=ChapterStatus.COMPLETE.value, production_state="ok"), False),
        (_chapter(2, status=ChapterStatus.REVISION.value, production_state="blocked"), True),
        (_chapter(3, status=ChapterStatus.DRAFTING.value, production_state="pending"), False),
    ]

    processed: list[int] = []
    for chapter, review_required in chapters:
        processed.append(chapter.chapter_number)
        if _chapter_requires_sequence_stop(
            chapter,
            _result(chapter.chapter_number, requires_human_review=review_required),
            settings,  # type: ignore[arg-type]
        ):
            break

    assert processed == [1, 2]


def test_formally_complete_chapter_allows_next_chapter() -> None:
    settings = SimpleNamespace(quality=SimpleNamespace(draft_mode=False))
    chapter = _chapter(2, status=ChapterStatus.COMPLETE.value, production_state="ok")

    assert not _chapter_requires_sequence_stop(
        chapter,
        _result(2, requires_human_review=False),
        settings,  # type: ignore[arg-type]
    )


def test_draft_mode_retains_explicit_continue_behavior() -> None:
    settings = SimpleNamespace(quality=SimpleNamespace(draft_mode=True))
    chapter = _chapter(2, status=ChapterStatus.REVISION.value, production_state="blocked")

    assert not _chapter_requires_sequence_stop(
        chapter,
        _result(2, requires_human_review=True),
        settings,  # type: ignore[arg-type]
    )


def test_missing_final_verdict_does_not_override_unfinished_state() -> None:
    settings = SimpleNamespace(quality=SimpleNamespace(draft_mode=False))
    chapter = _chapter(2, status=ChapterStatus.REVISION.value, production_state="blocked")

    assert _chapter_requires_sequence_stop(
        chapter,
        _result(2, requires_human_review=False),
        settings,  # type: ignore[arg-type]
    )
