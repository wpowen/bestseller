"""Unit tests for chapter-sequence gap helpers in ``services.consistency``.

These guard the resume-safe behavior of the autowrite pipeline: stuck
projects often have a discontiguous ``ChapterModel`` set, and the
pipeline must be able to work on the contiguous 1..N prefix without
erroring on the deferred tail.
"""

from __future__ import annotations

import pytest

from bestseller.services.consistency import (
    contiguous_prefix_max,
    detect_chapter_sequence_gaps,
)

pytestmark = pytest.mark.unit


def test_detect_gaps_returns_empty_for_contiguous() -> None:
    assert detect_chapter_sequence_gaps([1, 2, 3, 4, 5]) == []


def test_detect_gaps_returns_internal_holes() -> None:
    assert detect_chapter_sequence_gaps([1, 2, 3, 5, 6]) == [4]


def test_detect_gaps_no_starting_anchor() -> None:
    # Gap detector is min-anchored, not 1-anchored — tail-only sets have no gap
    assert detect_chapter_sequence_gaps([100, 101, 102]) == []


def test_detect_gaps_empty_input() -> None:
    assert detect_chapter_sequence_gaps([]) == []


def test_contiguous_prefix_all_present() -> None:
    assert contiguous_prefix_max([1, 2, 3, 4, 5]) == 5


def test_contiguous_prefix_with_tail_gap() -> None:
    # Matches the observed stuck-project shape: 1..50 plus 101..150
    nums = list(range(1, 51)) + list(range(101, 151))
    assert contiguous_prefix_max(nums) == 50


def test_contiguous_prefix_missing_one() -> None:
    """Missing chapter 1 means there's no usable prefix at all."""
    assert contiguous_prefix_max([2, 3, 4]) is None


def test_contiguous_prefix_single_chapter_one() -> None:
    assert contiguous_prefix_max([1]) == 1


def test_contiguous_prefix_empty() -> None:
    assert contiguous_prefix_max([]) is None


def test_contiguous_prefix_unordered_input() -> None:
    """Helper must not assume sorted input."""
    assert contiguous_prefix_max([3, 1, 2, 5]) == 3


def test_contiguous_prefix_duplicates_tolerated() -> None:
    assert contiguous_prefix_max([1, 1, 2, 2, 3]) == 3
