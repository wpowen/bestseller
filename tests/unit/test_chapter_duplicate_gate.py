from __future__ import annotations

import pytest

from bestseller.services.chapter_duplicate_gate import (
    CHAPTER_OPENING_DUPLICATE,
    check_chapter_duplicates,
)

pytestmark = pytest.mark.unit


def test_detects_duplicate_opening() -> None:
    opening = "北荒的风总带着矿砂味，吹在脸上像钝刀。" * 20
    prev = opening + "上一章独有结尾。"
    cur = opening + "本章独有中间。"
    report = check_chapter_duplicates(
        chapter_position=2,
        chapter_text=cur,
        prev_chapter_text=prev,
        opening_similarity_threshold=0.82,
    )
    codes = [f.code for f in report.findings]
    assert CHAPTER_OPENING_DUPLICATE in codes
