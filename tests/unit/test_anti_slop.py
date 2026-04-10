from __future__ import annotations

import pytest

from bestseller.services.anti_slop import (
    AiSlopReport,
    detect_ai_slop,
    strip_tier1_slop,
)

pytestmark = pytest.mark.unit


def test_detect_tier1_kill_phrases() -> None:
    text = "他心中五味杂陈，不知道该说什么。空气仿佛凝固了。"
    report = detect_ai_slop(text)
    assert report.has_severe_slop
    assert "心中五味杂陈" in report.tier1_hits
    assert "空气仿佛凝固了" in report.tier1_hits


def test_detect_tier2_cluster_above_threshold() -> None:
    text = (
        "他缓缓站起身，轻轻推开窗户。微微一笑后，默默走了出去。"
        "心头一紧，脑海中闪过一个念头。在这一刻，一切都变了。"
    )
    report = detect_ai_slop(text)
    assert report.has_cluster_slop
    assert report.tier2_cluster_count >= 3


def test_detect_tier2_below_threshold_no_flag() -> None:
    text = "他缓缓站起身，走向窗边。外面下着大雨，街灯昏黄。"
    report = detect_ai_slop(text)
    assert not report.has_cluster_slop


def test_detect_tier3_filler() -> None:
    text = "事实上，这件事从某种程度上来说并不重要。"
    report = detect_ai_slop(text)
    assert "事实上" in report.tier3_hits
    assert "从某种程度上来说" in report.tier3_hits


def test_strip_tier1_removes_offending_sentences() -> None:
    text = (
        "陆衍靠在墙上，看着窗外。\n"
        "他心中五味杂陈，不知该如何面对。\n"
        "手机震了一下，是顾临发来的消息。"
    )
    cleaned = strip_tier1_slop(text)
    assert "心中五味杂陈" not in cleaned
    assert "陆衍靠在墙上" in cleaned
    assert "手机震了一下" in cleaned


def test_strip_tier1_preserves_clean_text() -> None:
    text = "他推开门，走进雨里。街灯在积水里碎成一片。"
    cleaned = strip_tier1_slop(text)
    assert cleaned == text


def test_clean_text_has_zero_slop_score() -> None:
    text = "陆衍把证据袋扔到桌上，转身就走。顾临在身后喊了一声，他没回头。"
    report = detect_ai_slop(text)
    assert report.slop_score == 0.0
    assert not report.has_severe_slop
    assert not report.has_cluster_slop


def test_empty_text_returns_empty_report() -> None:
    report = detect_ai_slop("")
    assert report.slop_score == 0.0
    assert not report.has_severe_slop
