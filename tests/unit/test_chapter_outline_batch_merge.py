"""Regression tests for progressive CHAPTER_OUTLINE_BATCH merging.

The current volume replan must replace the unwritten tail, not merely dedupe
by ``chapter_number``. Otherwise stale future chapters remain in the merged
batch and later materialization keeps corrupt planned rows alive.
"""

from __future__ import annotations

import pytest

from bestseller.services import pipelines as pipeline_services


pytestmark = pytest.mark.unit


def test_merge_keeps_prefix_and_replaces_current_tail() -> None:
    existing = [
        {"chapter_number": 149, "volume_number": 3, "title": "old-149"},
        {"chapter_number": 150, "volume_number": 3, "title": "old-150"},
        {"chapter_number": 201, "volume_number": 4, "title": "stale-201"},
        {"chapter_number": 202, "volume_number": 4, "title": "stale-202"},
    ]
    incoming = [
        {"chapter_number": 151, "volume_number": 4, "title": "fresh-151"},
        {"chapter_number": 152, "volume_number": 4, "title": "fresh-152"},
    ]

    merged = pipeline_services._merge_progressive_outline_batch(existing, incoming)

    assert [(ch["chapter_number"], ch["title"]) for ch in merged] == [
        (149, "old-149"),
        (150, "old-150"),
        (151, "fresh-151"),
        (152, "fresh-152"),
    ]


def test_merge_still_dedups_last_write_wins_within_incoming_slice() -> None:
    existing = [{"chapter_number": 150, "title": "old-150"}]
    incoming = [
        {"chapter_number": 151, "title": "draft-151"},
        {"chapter_number": 151, "title": "final-151"},
        {"chapter_number": 152, "title": "final-152"},
    ]

    merged = pipeline_services._merge_progressive_outline_batch(existing, incoming)

    assert [ch["chapter_number"] for ch in merged] == [150, 151, 152]
    title_by_no = {ch["chapter_number"]: ch["title"] for ch in merged}
    assert title_by_no[151] == "final-151"


def test_merge_ignores_invalid_entries() -> None:
    existing = [{"chapter_number": 149, "title": "ok"}]
    incoming = [
        {"chapter_number": None, "title": "bad"},
        "garbage",
        {"chapter_number": 151, "title": "good"},
    ]

    merged = pipeline_services._merge_progressive_outline_batch(existing, incoming)

    assert [ch["chapter_number"] for ch in merged] == [149, 151]
