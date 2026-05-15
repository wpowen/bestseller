"""Unit tests for ``quality_levers.chapter_position_profiles``."""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers.chapter_position_profiles import (
    detect_chapter_positions,
    load_chapter_position_profiles,
    render_chapter_position_block,
)


pytestmark = pytest.mark.unit


def test_load_chapter_position_profiles_returns_first_chapter_profile() -> None:
    config = load_chapter_position_profiles()

    assert "first_chapter" in config.profiles
    first = config.profiles["first_chapter"]
    assert first.hard_gates  # non-empty
    assert any("protagonist_spotlight" in gate for gate in first.hard_gates)
    assert any("visible_conflict" in gate for gate in first.hard_gates)
    assert first.must_achieve
    assert first.rewrite_priority_order


def test_load_chapter_position_profiles_loads_sensitive_windows() -> None:
    config = load_chapter_position_profiles()
    windows = config.sensitive_anti_patterns.windows

    ids = {window.window_id for window in windows}
    assert {
        "opening_window",
        "golden_three_window",
        "extended_opening_window",
    } <= ids

    opening_window = next(
        w for w in windows if w.window_id == "opening_window"
    )
    banned_ids = {pattern.pattern_id for pattern in opening_window.banned}
    assert "psychological_dumping" in banned_ids
    assert "cold_protagonist" in banned_ids
    assert "no_payoff_in_ch1" in banned_ids


def test_sensitive_windows_for_chapter_matches_range() -> None:
    config = load_chapter_position_profiles()

    chapter1 = config.sensitive_anti_patterns.windows_for_chapter(1)
    chapter5 = config.sensitive_anti_patterns.windows_for_chapter(5)
    chapter11 = config.sensitive_anti_patterns.windows_for_chapter(11)

    chapter1_ids = {w.window_id for w in chapter1}
    chapter5_ids = {w.window_id for w in chapter5}
    chapter11_ids = {w.window_id for w in chapter11}

    assert "opening_window" in chapter1_ids
    assert "golden_three_window" in chapter1_ids
    assert "extended_opening_window" in chapter1_ids
    # 5 is past opening_window (1-3) but within extended (1-10)
    assert "opening_window" not in chapter5_ids
    assert "extended_opening_window" in chapter5_ids
    # 11 is past everything
    assert not chapter11_ids


def test_detect_chapter_positions_handles_book_first_chapter() -> None:
    positions = detect_chapter_positions(chapter_number=1)
    assert positions == ("first_chapter",)


def test_detect_chapter_positions_volume_opener_only_when_volume_gt_1() -> None:
    # Volume 1 chapter 1 is treated as first_chapter (not volume_opener)
    positions = detect_chapter_positions(
        chapter_number=1,
        volume_number=1,
        is_first_chapter_of_volume=True,
    )
    assert "first_chapter" in positions
    assert "volume_opener" not in positions

    # Volume 2 chapter 1 of a new volume is volume_opener
    positions = detect_chapter_positions(
        chapter_number=31,
        volume_number=2,
        is_first_chapter_of_volume=True,
    )
    assert "volume_opener" in positions


def test_detect_chapter_positions_multi_label() -> None:
    positions = detect_chapter_positions(
        chapter_number=1,
        contains_first_villain_reveal=True,
        is_first_unit_case=True,
    )
    assert "first_chapter" in positions
    assert "first_villain_reveal" in positions
    assert "first_unit_case_chapter" in positions


def test_render_chapter_position_block_first_chapter_includes_hard_gates() -> None:
    block = render_chapter_position_block(
        positions=("first_chapter",),
        chapter_number=1,
    )

    assert "章节位置档案" in block
    assert "first_chapter" in block
    assert "硬指标" in block


def test_render_chapter_position_block_includes_window_for_ch1() -> None:
    block = render_chapter_position_block(
        positions=("first_chapter",),
        chapter_number=1,
    )

    assert "opening_window" in block
    assert "golden_three_window" in block


def test_render_chapter_position_block_empty_for_normal_chapter() -> None:
    # No positions tagged, chapter past all sensitive windows
    assert render_chapter_position_block(positions=(), chapter_number=50) == ""


def test_render_chapter_position_block_ignores_unknown_position() -> None:
    block = render_chapter_position_block(
        positions=("totally_unknown_position",),
        chapter_number=50,
    )
    assert block == ""
