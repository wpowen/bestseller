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


# ── English anti-slop tests ─────────────────────────────────────────────


def test_detect_en_tier1_kill_phrases() -> None:
    text = "It's worth noting that the room fell silent. A tapestry of emotions wove itself across her face."
    report = detect_ai_slop(text, language="en-US")
    assert report.has_severe_slop
    assert "it's worth noting that" in report.tier1_hits
    assert "a tapestry of" in report.tier1_hits


def test_detect_en_tier1_case_insensitive() -> None:
    text = "A TESTAMENT TO her courage, the army held firm."
    report = detect_ai_slop(text, language="en")
    assert report.has_severe_slop
    assert "a testament to" in report.tier1_hits


def test_detect_en_tier2_cluster_above_threshold() -> None:
    text = (
        "Her eyes widened. His jaw clenched. Her breath hitched. "
        "A chill ran down her spine. His heart pounded."
    )
    report = detect_ai_slop(text, language="en-US")
    assert report.has_cluster_slop
    assert report.tier2_cluster_count >= 3


def test_detect_en_tier2_below_threshold_no_flag() -> None:
    text = "She turned and walked into the rain. The lights dimmed."
    report = detect_ai_slop(text, language="en")
    assert not report.has_cluster_slop


def test_detect_en_tier3_filler() -> None:
    text = "Truth be told, it didn't matter. At the end of the day, they were all lost."
    report = detect_ai_slop(text, language="en")
    assert "truth be told" in report.tier3_hits
    assert "at the end of the day" in report.tier3_hits


def test_strip_en_tier1_removes_offending_sentences() -> None:
    text = (
        "Kael stepped through the gate.\n"
        "A tapestry of light and colour flooded the chamber.\n"
        "He reached for the lever and pulled."
    )
    cleaned = strip_tier1_slop(text, language="en-US")
    assert "tapestry of" not in cleaned.lower()
    assert "Kael stepped" in cleaned
    assert "He reached" in cleaned


def test_strip_en_tier1_preserves_clean_text() -> None:
    text = "She drew the blade and lunged. The guard stumbled back, cursing."
    cleaned = strip_tier1_slop(text, language="en")
    assert cleaned == text


def test_en_clean_text_has_zero_slop_score() -> None:
    text = "Elara shoved the ledger into her pack and ran. Behind her, the archive burned."
    report = detect_ai_slop(text, language="en")
    assert report.slop_score == 0.0
    assert not report.has_severe_slop
    assert not report.has_cluster_slop
