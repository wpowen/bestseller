from __future__ import annotations

import pytest

from bestseller.services.word_targets import (
    allocate_book_word_targets,
    authoritative_book_word_targets,
    word_target_policy,
)
from bestseller.settings import load_settings


def test_book_allocator_sums_exactly_and_stays_in_bounds() -> None:
    policy = word_target_policy(load_settings())
    targets = allocate_book_word_targets(26_003, 10, policy=policy)
    assert len(targets) == 10
    assert sum(targets) == 26_003
    assert all(policy.chapter_min <= value <= policy.chapter_max for value in targets)


def test_book_allocator_weighted_remainder_is_deterministic() -> None:
    kwargs = {"chapter_min": 2_000, "chapter_max": 3_000, "weights": [1, 2, 1, 1]}
    first = allocate_book_word_targets(10_005, 4, **kwargs)
    second = allocate_book_word_targets(10_005, 4, **kwargs)
    assert first == second
    assert sum(first) == 10_005
    assert first[1] >= first[0]


def test_book_allocator_rejects_infeasible_total() -> None:
    with pytest.raises(ValueError, match="bounds"):
        allocate_book_word_targets(7_999, 4, chapter_min=2_000, chapter_max=3_000)


def test_book_allocator_redistributes_after_weighted_chapter_hits_cap() -> None:
    targets = allocate_book_word_targets(
        9_500,
        4,
        chapter_min=2_000,
        chapter_max=3_000,
        weights=[10, 0, 0, 0],
    )
    assert targets[0] == 3_000
    assert sum(targets) == 9_500


def test_authoritative_project_total_widens_legacy_chapter_bounds() -> None:
    project = type(
        "LegacyProject",
        (),
        {
            "target_word_count": 80_000,
            "target_chapters": 12,
            "metadata_json": {},
        },
    )()
    targets = authoritative_book_word_targets(project, load_settings())
    assert len(targets) == 12
    assert sum(targets) == 80_000
    assert max(targets) > word_target_policy(load_settings()).chapter_max
