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


# ── 《子时客》事故回归：量词误报 + 冷读者门 + 情绪占位 ──────────────────────


def test_naming_gate_does_not_flag_measure_word_phrases() -> None:
    from bestseller.services.output_validator import NamingConsistencyCheck

    check = NamingConsistencyCheck()

    class _Inv:
        language = "zh-CN"
        naming_scheme = None

    class _Ctx:
        scope = "chapter"
        chapter_no = 1
        invariants = _Inv()
        allowed_names = frozenset({"殷霁"})

    # "那张铁律" ×2 = 纸上的铁律，不是人名"张铁律"；旧代码会报 rogue name。
    text = "殷霁揭下那张铁律。那张铁律上渗着血。殷霁把它压回柜台。"
    violations = list(check.run(text, _Ctx()))
    flagged = " ".join(v.detail for v in violations)
    assert "张铁律" not in flagged


def test_cold_reader_checklist_injected_for_chapter_one_only() -> None:
    from bestseller.services.chapter_llm_quality_judge import (
        _render_binary_checklist,
    )

    corpus = {"binary_checklist": [{"id": "x", "label": "L", "description": "d"}]}
    ch1 = _render_binary_checklist(corpus, chapter_number=1)
    ch2 = _render_binary_checklist(corpus, chapter_number=2)
    assert "cold_reader_onboarding" in ch1
    assert "冷读者五锚点" in ch1
    assert "cold_reader_onboarding" not in ch2


def test_emotion_placeholder_marked_low_signal() -> None:
    from bestseller.services.methodology_application_gate import (
        _LOW_SIGNAL_EMOTION_MARKERS,
    )

    assert any(
        "保持本章压力递进" in marker for marker in _LOW_SIGNAL_EMOTION_MARKERS
    )


# ── 《黄泉客栈》AI味 tic 回归：具身动词复读检测 ─────────────────────────────


def test_verb_tic_spam_flags_saturated_chapter() -> None:
    from bestseller.services.ai_flavor.detector import detect

    # 模拟真机病灶密度：~1500字里 烫×12 爬×10（>6次且>6/万字）。
    base = (
        "他把手贴上炉壁，烫。指尖烫得发麻，烫意顺着腕骨爬，爬过肘弯，又爬上肩头。"
        "灯芯烫，碗沿烫，连门环都烫。黑纹在皮下爬，爬一寸，停半息，再爬。"
        "他缩手，烫痕在掌心爬成一条线，烫出一个字。夜风爬过窗缝，烫意不退。"
        "他低头，烫红的指腹压住纸角，纸也烫，墨迹爬向边缘。"
    )
    text = base * 8  # ~1900字，密度与真机病灶同级
    report = detect(text, language="zh-CN", chapter_number=2)
    tic_spans = [s for s in report.spans if s.category == "verb_tic_spam"]
    assert tic_spans, "高密度具身动词复读必须被检出"
    assert "烫" in tic_spans[0].why and "爬" in tic_spans[0].why


def test_verb_tic_spam_ignores_normal_prose() -> None:
    from bestseller.services.ai_flavor.detector import detect

    text = (
        "他推门进屋，把伞收好靠在墙边。桌上的饭菜已经凉了，母亲坐在灯下补衣服。"
        "他说今天加了班，母亲点点头，把碗推过去。窗外落着小雨，巷子里有人骑车经过，"
        "铃铛响了两声。他吃完饭，帮着把桌子擦干净，才说出白天发生的事。"
    ) * 6
    report = detect(text, language="zh-CN", chapter_number=3)
    assert not [s for s in report.spans if s.category == "verb_tic_spam"]
