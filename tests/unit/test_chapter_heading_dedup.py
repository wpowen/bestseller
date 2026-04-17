"""Regression tests for the duplicate-chapter-heading export bug.

Prevents a regression of the bug where ``chapter-001.md`` on disk began with::

    # 第1章：风眼露锋

    # 第1章：风眼露锋

    正文…

because the eager disk-sync path in ``persist_chapter_draft`` unconditionally
re-prepended a heading that ``render_chapter_draft_markdown`` had already added.
"""

from __future__ import annotations

import pytest

from bestseller.services.drafts import _has_leading_chapter_heading

pytestmark = pytest.mark.unit


def test_detects_cjk_heading_on_first_line() -> None:
    assert _has_leading_chapter_heading("# 第1章：风眼露锋\n\n正文开始。", 1) is True


def test_detects_cjk_heading_with_leading_blank_lines() -> None:
    assert _has_leading_chapter_heading("\n\n# 第1章：风眼露锋\n正文。", 1) is True


def test_detects_bare_cjk_chapter_prefix_without_subtitle() -> None:
    assert _has_leading_chapter_heading("# 第42章\n正文。", 42) is True


def test_detects_english_heading() -> None:
    assert _has_leading_chapter_heading("# Chapter 3: The Turn\n\nPose.", 3) is True


def test_english_heading_case_insensitive() -> None:
    assert _has_leading_chapter_heading("# CHAPTER 3: Foo", 3) is True


def test_rejects_wrong_chapter_number() -> None:
    assert _has_leading_chapter_heading("# 第2章：xx\n正文", 1) is False


def test_rejects_empty_content() -> None:
    assert _has_leading_chapter_heading("", 1) is False


def test_rejects_content_without_heading() -> None:
    assert _has_leading_chapter_heading("正文直接开始，没有标题行。", 1) is False


def test_rejects_body_mentioning_chapter_in_prose() -> None:
    # A prose mention of "第1章" in the first paragraph should not be treated
    # as a heading — it must carry a leading '#' marker.
    assert _has_leading_chapter_heading("在第1章，她站在门口。", 1) is False
