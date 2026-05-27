from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.chapter_length_gate import (
    CHAPTER_BELOW_TARGET_BLOCK_CODE,
    CHAPTER_LENGTH_BLOCK_HIGH_CODE,
    CHAPTER_TOO_SHORT_BLOCK_CODE,
    DEFAULT_HARD_MAX_ZH_CHARS,
    DEFAULT_HARD_FLOOR_ZH_CHARS,
    DEFAULT_SOFT_WARNING_ZH_CHARS,
    check_chapter_length,
    count_zh_chars,
    render_chapter_length_block,
    render_chapter_length_violation_block,
)

pytestmark = pytest.mark.unit


def _make_text(zh_chars: int) -> str:
    """Return a string containing exactly *zh_chars* Chinese characters."""

    # Use a stable repeating pattern so the count is deterministic.
    base = "林渊抬头看着十七栋的灯光夜风扑面而来"
    out: list[str] = []
    while sum(1 for c in "".join(out) if "一" <= c <= "鿿") < zh_chars:
        out.append(base)
    text = "".join(out)
    # Trim to exactly zh_chars
    zh_only = [c for c in text if "一" <= c <= "鿿"]
    return "".join(zh_only[:zh_chars])


def test_count_zh_chars_ignores_non_cjk() -> None:
    assert count_zh_chars("") == 0
    assert count_zh_chars("Hello") == 0
    assert count_zh_chars("123 abc !@#") == 0
    assert count_zh_chars("林渊") == 2
    assert count_zh_chars("林渊 (lin yuan) — 30 years old") == 2
    # 4 CJK chars: 第 章 林 渊 (the digit "1" doesn't count)
    assert count_zh_chars("第1章 林渊") == 4


def test_short_chapter_triggers_critical() -> None:
    text = _make_text(1300)
    report = check_chapter_length(text, chapter_position=1)
    assert report.has_critical
    assert report.finding.code == CHAPTER_TOO_SHORT_BLOCK_CODE
    assert report.finding.zh_char_count == 1300


def test_below_target_triggers_high() -> None:
    text = _make_text(2200)
    report = check_chapter_length(text, chapter_position=1)
    assert not report.has_critical
    assert report.finding.severity == "high"
    assert report.finding.code == CHAPTER_BELOW_TARGET_BLOCK_CODE


def test_meeting_target_passes() -> None:
    text = _make_text(2600)
    report = check_chapter_length(text, chapter_position=1)
    assert report.passed
    assert report.finding.severity == "info"


def test_over_hard_max_triggers_critical() -> None:
    text = _make_text(DEFAULT_HARD_MAX_ZH_CHARS + 1)
    report = check_chapter_length(text, chapter_position=1)
    assert report.has_critical
    assert report.finding.code == CHAPTER_LENGTH_BLOCK_HIGH_CODE
    assert report.finding.hard_max == DEFAULT_HARD_MAX_ZH_CHARS


def test_real_qingnang_ch1_count_reads_full_chapter() -> None:
    text = Path("output/exorcist-detective-1778051012/chapter-001.md").read_text(
        encoding="utf-8"
    )
    assert count_zh_chars(text) == 5172


def test_custom_thresholds_respected() -> None:
    text = _make_text(1800)
    # Floor 1500 → 1800 is above floor, but below target 3000 → high.
    report = check_chapter_length(
        text,
        chapter_position=1,
        hard_floor=1500,
        soft_warning=3000,
    )
    assert report.finding.severity == "high"
    # Floor 2000 → 1800 below floor → critical.
    report = check_chapter_length(
        text,
        chapter_position=1,
        hard_floor=2000,
        soft_warning=3000,
    )
    assert report.has_critical


def test_render_block_includes_thresholds() -> None:
    block = render_chapter_length_block()
    assert str(DEFAULT_HARD_FLOOR_ZH_CHARS) in block
    assert str(DEFAULT_SOFT_WARNING_ZH_CHARS) in block
    assert str(DEFAULT_HARD_MAX_ZH_CHARS) in block
    assert "章节体量门" in block


def test_render_violation_block_silent_when_passing() -> None:
    text = _make_text(3000)
    report = check_chapter_length(text, chapter_position=1)
    assert render_chapter_length_violation_block(report) == ""


def test_render_violation_block_states_gap() -> None:
    text = _make_text(1500)
    report = check_chapter_length(text, chapter_position=1)
    block = render_chapter_length_violation_block(report)
    assert "1500" in block
    assert "扩写" in block


def test_render_over_max_violation_block_states_shrink() -> None:
    text = _make_text(3600)
    report = check_chapter_length(text, chapter_position=1)
    block = render_chapter_length_violation_block(report)
    assert "3600" in block
    assert "删减" in block


def test_evaluate_retention_safety_chapter_too_short_triggers_repair() -> None:
    """Critical short chapter must add CHAPTER_TOO_SHORT to auto_repair codes."""

    from bestseller.services.retention_safety_gate import (
        AUTO_REPAIR_RETENTION_CODES,
        evaluate_retention_safety,
    )

    assert CHAPTER_TOO_SHORT_BLOCK_CODE in AUTO_REPAIR_RETENTION_CODES
    assert CHAPTER_LENGTH_BLOCK_HIGH_CODE in AUTO_REPAIR_RETENTION_CODES

    text = _make_text(1200)  # well below default floor of 2000
    report = evaluate_retention_safety(
        chapter_position=1,
        chapter_text=text,
        skip_signature=True,
        skip_hook_echo=True,
        skip_exposition=True,
    )
    assert not report.passed
    assert CHAPTER_TOO_SHORT_BLOCK_CODE in report.auto_repair_codes


def test_evaluate_retention_safety_chapter_too_long_triggers_repair() -> None:
    from bestseller.services.retention_safety_gate import evaluate_retention_safety

    text = _make_text(3600)
    report = evaluate_retention_safety(
        chapter_position=1,
        chapter_text=text,
        skip_signature=True,
        skip_hook_echo=True,
        skip_exposition=True,
    )
    assert not report.passed
    assert CHAPTER_LENGTH_BLOCK_HIGH_CODE in report.auto_repair_codes
