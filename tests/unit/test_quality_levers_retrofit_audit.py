"""Unit tests for the quality retrofit audit triage layer."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality_levers_retrofit_audit import audit_one_chapter

pytestmark = pytest.mark.unit


def _write_chapter(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "chapter-001.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_single_ai_voice_hit_is_medium_when_chapter_has_pressure(
    tmp_path: Path,
) -> None:
    pressure = (
        "门闩猛地扣住。墙上的裂纹逼近符纸边缘。"
        "他攥紧铜钱，退路被黑影堵住。"
    )
    text = pressure * 70 + "他意识到这不是普通的阴债。"

    row = audit_one_chapter(
        "demo", 1, _write_chapter(tmp_path, text), platform="framework"
    )

    assert row.banned_pattern_hits == 1
    assert row.priority == "medium"


def test_severe_pulse_gap_stays_high_priority(tmp_path: Path) -> None:
    text = "他走进屋里，看见桌上一张纸。" * 180

    row = audit_one_chapter(
        "demo", 1, _write_chapter(tmp_path, text), platform="framework"
    )

    assert row.pulse_passed is False
    assert row.priority == "high"


def test_english_chapter_uses_language_aware_counts(tmp_path: Path) -> None:
    text = "danger moved fast. " * 700

    row = audit_one_chapter(
        "demo",
        1,
        _write_chapter(tmp_path, text),
        platform="tomato",
        language="en-US",
    )

    assert row.language == "en-US"
    assert row.platform == "tomato"
    assert row.audit_validity == "language_aware"
    assert row.char_count == 2100
    assert row.count_unit == "english_words"
    assert row.word_count_passed is True
    assert row.rhythm_applicable is False
    assert row.rhythm_passed is True
    assert row.priority == "ok"
