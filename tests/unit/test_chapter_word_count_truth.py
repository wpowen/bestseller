from __future__ import annotations

import pytest

from bestseller.services.chapter_word_count_truth import (
    WORD_COUNT_METADATA_MISMATCH,
    check_word_count_metadata_truth,
    measure_chapter_body_zh_chars,
)

pytestmark = pytest.mark.unit


def test_measure_ignores_frontmatter() -> None:
    text = "---\nword_count: 9999\n---\n林渊抬头。"
    assert measure_chapter_body_zh_chars(text) == 4


def test_metadata_mismatch_critical() -> None:
    body = "林" * 500
    report = check_word_count_metadata_truth(
        body,
        stored_word_count=5800,
        draft_word_count=5600,
    )
    assert report.finding.code == WORD_COUNT_METADATA_MISMATCH
    assert report.finding.severity == "critical"


def test_metadata_ok_when_aligned() -> None:
    body = "林" * 3600
    report = check_word_count_metadata_truth(
        body,
        stored_word_count=3600,
    )
    assert report.passed
