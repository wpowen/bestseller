from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from bestseller.services.timeline_consistency_gate import (
    TIMELINE_INCONSISTENT_BLOCK_CODE,
    TimelineCanon,
    TimelineFact,
    check_timeline_consistency,
    load_timeline_canon,
    render_timeline_canon_block,
    render_timeline_violations_block,
)

pytestmark = pytest.mark.unit


def _qingnang_canon() -> TimelineCanon:
    return TimelineCanon(
        present_year=2025,
        protagonist_name="林渊",
        protagonist_current_age=30,
        events=(
            TimelineFact(
                event_id="lin_zhengchun_first",
                label="林正淳第一次进十七栋",
                years_ago=23,
                year_name="戊子年",
                protagonist_age_at_event=7,
                subjects=("林正淳", "林渊"),
            ),
            TimelineFact(
                event_id="lin_zhengchun_re_entry",
                label="林正淳再次入镜",
                years_ago=3,
                year_name=None,
                protagonist_age_at_event=27,
                subjects=("林正淳",),
            ),
            TimelineFact(
                event_id="lin_jiahui_repair",
                label="林家辉补镜",
                years_ago=30,
                year_name=None,
                protagonist_age_at_event=None,
                subjects=("林家辉",),
            ),
            TimelineFact(
                event_id="lin_yuanshan_seal",
                label="林远山封镜",
                years_ago=300,
                year_name=None,
                protagonist_age_at_event=None,
                subjects=("林远山",),
            ),
        ),
        forbidden_anchors=(17, 10, 5, 50),
        locked_names={"father": "林正淳", "grandfather": "林家辉"},
    )


def test_check_empty_text_passes() -> None:
    report = check_timeline_consistency("", chapter_position=1, canon=_qingnang_canon())
    assert report.passed
    assert report.violations == ()


def test_check_no_canon_passes() -> None:
    report = check_timeline_consistency(
        "二十三年前父亲走进十七栋", chapter_position=1, canon=None
    )
    assert report.passed


def test_check_canonical_anchors_pass() -> None:
    text = (
        "戊子年。二十三年前。父亲林正淳第一次失踪那一年。\n"
        "今夜林渊三十岁。\n"
    )
    report = check_timeline_consistency(text, chapter_position=1, canon=_qingnang_canon())
    assert report.passed


def test_forbidden_anchor_critical() -> None:
    text = "父亲十七年前在十七栋留下痕迹。"
    report = check_timeline_consistency(text, chapter_position=1, canon=_qingnang_canon())
    assert not report.passed
    codes = [v.code for v in report.violations]
    assert "FORBIDDEN_ANCHOR" in codes
    assert any(v.severity == "critical" for v in report.violations)


def test_subject_anchor_mismatch_critical() -> None:
    text = "林正淳五年前第一次走进十七栋。"
    # 5 年前 is forbidden but also wrong for lin_zhengchun_first (23 年前)
    report = check_timeline_consistency(text, chapter_position=1, canon=_qingnang_canon())
    assert not report.passed
    codes = {v.code for v in report.violations}
    assert "FORBIDDEN_ANCHOR" in codes or "SUBJECT_ANCHOR_MISMATCH" in codes


def test_age_year_mismatch_critical() -> None:
    text = "林渊看见七岁那年的自己。那是十七年前。"
    # 30 - 17 = 13, not 7
    report = check_timeline_consistency(text, chapter_position=1, canon=_qingnang_canon())
    assert not report.passed
    codes = {v.code for v in report.violations}
    assert "AGE_YEAR_MISMATCH" in codes or "FORBIDDEN_ANCHOR" in codes


def test_age_year_consistent_passes() -> None:
    text = "林渊看见七岁那年的自己。那是二十三年前。"
    # 30 - 23 = 7 ✓
    report = check_timeline_consistency(text, chapter_position=1, canon=_qingnang_canon())
    age_year_codes = {v.code for v in report.violations if v.code == "AGE_YEAR_MISMATCH"}
    assert not age_year_codes


def test_three_event_anchors_pass() -> None:
    text = (
        "林正淳二十三年前第一次进十七栋。\n"
        "他三年前再次入镜，至今未归。\n"
        "林家辉三十年前补过那面困魂镜。\n"
        "林远山三百年前封镜，三族契约由此立。\n"
    )
    report = check_timeline_consistency(text, chapter_position=1, canon=_qingnang_canon())
    assert report.passed


def test_internal_contradiction_high() -> None:
    text = (
        "林正淳二十三年前进十七栋。\n"
        "另一段又说：林正淳七年前进十七栋。\n"
    )
    report = check_timeline_consistency(text, chapter_position=1, canon=_qingnang_canon())
    codes = {v.code for v in report.violations}
    assert "INTERNAL_CONTRADICTION" in codes or "SUBJECT_ANCHOR_MISMATCH" in codes


def test_render_canon_block_zh() -> None:
    block = render_timeline_canon_block(_qingnang_canon())
    # 2026-05-23: header upgraded to "白名单" framing (LLM-first whitelist)
    assert "白名单" in block
    assert "23 年前" in block
    assert "17 年前" in block  # in forbidden list
    assert "戊子年" in block
    assert "30 岁" in block or "年龄" in block
    # Whitelist must explicitly enumerate allowed anchors
    assert "唯一允许" in block
    # Age formula must be present
    assert "30-23=7" in block or "30-3=27" in block


def test_render_canon_block_handles_none() -> None:
    assert render_timeline_canon_block(None) == ""


def test_render_violations_block() -> None:
    text = "父亲十七年前留在十七栋。"
    report = check_timeline_consistency(text, chapter_position=1, canon=_qingnang_canon())
    block = render_timeline_violations_block(report)
    assert "时间线门禁" in block
    assert "FORBIDDEN_ANCHOR" in block or "十七" in block


def test_render_violations_block_passed_empty() -> None:
    text = "今夜风高月黑。"
    report = check_timeline_consistency(text, chapter_position=1, canon=_qingnang_canon())
    assert render_timeline_violations_block(report) == ""


def test_load_canon_from_real_file() -> None:
    """Smoke-test loading the actual timeline-canon.md we built earlier."""

    path = Path(
        "output/exorcist-detective-1778051012/story-bible/timeline-canon.md"
    )
    if not path.exists():
        pytest.skip("timeline-canon.md not present (book-specific)")
    canon = load_timeline_canon(path)
    assert canon is not None
    assert canon.protagonist_current_age == 30
    assert 23 in canon.allowed_years_ago
    assert 3 in canon.allowed_years_ago
    assert 17 in canon.forbidden_anchors


def test_load_canon_returns_none_on_missing() -> None:
    assert load_timeline_canon("/nonexistent/path") is None


def test_block_code_constant() -> None:
    assert TIMELINE_INCONSISTENT_BLOCK_CODE == "TIMELINE_INCONSISTENT"
