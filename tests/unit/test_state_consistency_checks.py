"""状态一致性新检查的单元测试：朝代/时代错置 + 跨章倒计时倒流。

配套 2026-07-03 的回写机制审计：
* 朝代一致性此前完全无保障（古代书出现手机零检测）；
* 倒计时只有章内检查，跨章"还剩三天→还剩五天"无人拦。
等级倒退(_check_power_tier_regression)与死者复活(_check_resurrection)
审计确认已存在且已接线，不在此重复测试。
"""
from __future__ import annotations

import pytest

from bestseller.services.common_sense_gate import evaluate_common_sense_gate
from bestseller.services.contradiction import (
    _countdown_amount,
    extract_countdown_mentions,
)

pytestmark = pytest.mark.unit


# ── 朝代/时代错置 ────────────────────────────────────────────────────────────


def _era_findings(report):
    return [f for f in report.findings if f.code == "era_anachronism"]


def test_ancient_setting_flags_modern_tokens_as_blocking() -> None:
    report = evaluate_common_sense_gate(
        "李慕白掏出手机看了一眼，微信上师门的消息还没回。"
        "他把手机塞回袖袋，翻身上马。",
        genre="武侠",
        sub_genre="古典仙侠",
        chapter_number=5,
    )
    findings = _era_findings(report)
    assert findings and findings[0].severity == "medium"
    assert "手机" in findings[0].evidence["tokens"]
    assert report.passed is False


def test_single_stray_token_is_advisory_only() -> None:
    report = evaluate_common_sense_gate(
        "殿外传来嗡鸣，仿佛后世传说中的飞机掠过夜空。",
        genre="历史",
        sub_genre="王朝争霸",
        chapter_number=12,
    )
    findings = _era_findings(report)
    assert findings and findings[0].severity == "low"
    # low 不入 blocking 集合——单个词可能是比喻，不应触发重写。
    assert all(f.severity not in {"high", "medium"} for f in findings)


def test_modern_and_timetravel_genres_are_exempt() -> None:
    for genre, sub in (
        ("都市修仙", "灵气复苏"),
        ("仙侠", "穿越古代"),
        ("历史", "系统流"),
    ):
        report = evaluate_common_sense_gate(
            "他掏出手机刷了刷朋友圈，顺手叫了辆出租车。",
            genre=genre,
            sub_genre=sub,
            chapter_number=3,
        )
        assert not _era_findings(report), f"{genre}/{sub} 不应触发时代错置"


def test_republican_setting_flags_digital_but_not_car() -> None:
    report = evaluate_common_sense_gate(
        "沈探长坐进汽车后座，掏出手机拨了个视频过去。",
        genre="民国",
        sub_genre="民俗悬疑",
        chapter_number=2,
    )
    findings = _era_findings(report)
    assert findings
    tokens = findings[0].evidence["tokens"]
    assert "手机" in tokens
    assert "汽车" not in tokens  # 民国允许汽车


def test_clean_ancient_chapter_passes() -> None:
    report = evaluate_common_sense_gate(
        "他把剑放回鞘中，沿着官道向北，入夜前赶到了驿站。",
        genre="武侠",
        chapter_number=4,
    )
    assert not _era_findings(report)


# ── 倒计时提取与倒流判定 ─────────────────────────────────────────────────────


def test_countdown_amount_parses_zh_numerals() -> None:
    assert _countdown_amount("三") == 3
    assert _countdown_amount("两") == 2
    assert _countdown_amount("十") == 10
    assert _countdown_amount("十五") == 15
    assert _countdown_amount("三十") == 30
    assert _countdown_amount("7") == 7
    assert _countdown_amount("三百") is None  # 大数不比较


def test_extract_countdown_mentions_units_and_order() -> None:
    text = "她说还剩三天。到了城门他才发现只剩两个时辰，最后仅剩30分钟。"
    mentions = extract_countdown_mentions(text)
    assert mentions == [("day", 3), ("shichen", 2), ("minute", 30)]


def test_extract_countdown_ignores_huge_numbers() -> None:
    assert extract_countdown_mentions("这一族还剩99天寿数。") == [("day", 99)]
    assert extract_countdown_mentions("矿脉还剩五百天产量。") == []
