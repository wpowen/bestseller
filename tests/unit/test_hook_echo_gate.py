from __future__ import annotations

import pytest

from bestseller.services.hook_echo_gate import (
    check_hook_echo,
    extract_hook_tokens,
    render_hook_echo_block,
)

pytestmark = pytest.mark.unit


_PREV_CHAPTER = (
    "夜色如墨，山风扑过。\n"
    "他握紧剑柄，心中暗想：今夜若不退，便是死路一条。\n"
    "“你当真敢杀我？”那人冷冷一笑。\n"
    "他不答，只是出剑。剑光如电。\n"
    "下一刻，门外脚步声响起，名单还在他怀中。\n"
    "突然，墙后传来一声低咳——竟是他以为已死之人。\n"
    "未完——\n"
)


def test_extract_hook_tokens_finds_suspense_words() -> None:
    tokens = extract_hook_tokens(_PREV_CHAPTER)

    assert "突然" in tokens
    assert "下一刻" in tokens
    assert "竟是" in tokens or "竟然" in tokens or "竟敢" in tokens


def test_extract_hook_tokens_finds_cliffhanger_phrases() -> None:
    tokens = extract_hook_tokens(_PREV_CHAPTER)

    assert "门外" in tokens or "脚步声" in tokens


def test_extract_hook_tokens_handles_empty_text() -> None:
    assert extract_hook_tokens("") == []


def test_extract_hook_tokens_filters_noisy_dialogue_fragments() -> None:
    text = (
        "林渊走出大堂，“布局的人是谁？”\n"
        "墙角有一只手带着泥水。那声音说话很轻。\n"
        "“渊娃子，你在吗？”门外忽然响起敲门声。\n"
    )

    tokens = extract_hook_tokens(text)

    assert "布局的人是谁" in tokens
    assert "你在吗" in tokens
    assert "带着" not in tokens
    assert "墙角有" not in tokens
    assert "林渊走出大堂" not in tokens


def test_check_hook_echo_chapter_one_always_passes() -> None:
    report = check_hook_echo(
        prev_chapter_text="",
        current_chapter_text="",
        current_chapter_position=1,
    )

    assert report.passed
    assert report.finding.code == "HOOK_ECHO_OK"


def test_check_hook_echo_full_coverage_passes() -> None:
    # Current chapter echoes most suspense + cliffhanger tokens
    current = (
        "他听见门外的脚步声越来越近。"
        "下一刻，那扇门被推开，竟是他失踪三年的师兄。"
        "突然之间，名单从怀里掉了出来。"
        "他不答，只是后退一步。"
        "他冷冷一笑，"
        "“你当真敢杀我？”这句话他听了很多次了，"
        "却没想到，今夜真的有人敢。"
    )

    report = check_hook_echo(
        prev_chapter_text=_PREV_CHAPTER,
        current_chapter_text=current,
        current_chapter_position=2,
        prev_chapter_position=1,
    )

    assert report.passed
    assert report.coverage >= 0.5


@pytest.mark.parametrize(
    ("prev", "current", "expected"),
    [
        ("倒计时已经开始。", "他知道时间在倒着走，最后期限逼到眼前。", "倒计时"),
        ("门外忽然传来脚步声。", "门口的足音越来越近。", "门外"),
        ("那份名单还在他怀里。", "他翻开账册，看见第一行名字已经变红。", "名单"),
        ("有人开始敲门。", "叩门声三短一长，像催命符。", "敲门"),
        ("真相就在镜后。", "他终于摸到谜底，却发现答案比谎言更冷。", "真相"),
    ],
)
def test_check_hook_echo_matches_semantic_synonyms(
    prev: str,
    current: str,
    expected: str,
) -> None:
    report = check_hook_echo(
        prev_chapter_text=prev,
        current_chapter_text=current,
        current_chapter_position=2,
        prev_chapter_position=1,
    )

    assert expected in report.finding.matched_tokens
    assert report.passed


def test_check_hook_echo_zero_coverage_critical_for_early_chapter() -> None:
    # Current chapter opens a fresh narrative branch — no echo
    current = (
        "三日后，清晨。\n"
        "李四走进客栈，要了一壶酒。\n"
        "店小二殷勤地擦着桌子，看着今天又是好天气。\n"
    )

    report = check_hook_echo(
        prev_chapter_text=_PREV_CHAPTER,
        current_chapter_text=current,
        current_chapter_position=2,
        prev_chapter_position=1,
    )

    assert not report.passed
    assert report.finding.severity == "critical"
    assert report.finding.code == "HOOK_ECHO_MISSING"
    assert report.coverage == 0.0
    assert report.finding.missed_tokens


def test_check_hook_echo_late_chapter_only_warns() -> None:
    """Past early chapters, low echo is informational, not critical."""

    current = "三日后，李四走进客栈。"
    report = check_hook_echo(
        prev_chapter_text=_PREV_CHAPTER,
        current_chapter_text=current,
        current_chapter_position=50,
        prev_chapter_position=49,
        early_chapter_threshold=10,
    )

    assert report.finding.severity in ("high", "info")


def test_check_hook_echo_partial_coverage_high_severity() -> None:
    # Echoes only 1-2 of many — between floor and target
    current = "他想起昨夜的脚步声，仍心有余悸。"
    report = check_hook_echo(
        prev_chapter_text=_PREV_CHAPTER,
        current_chapter_text=current,
        current_chapter_position=3,
        prev_chapter_position=2,
    )

    assert report.finding.severity in ("critical", "high")
    assert 0 < report.coverage < 0.65


def test_check_hook_echo_no_prev_tokens_passes() -> None:
    report = check_hook_echo(
        prev_chapter_text="一段平平无奇的开场。",
        current_chapter_text="另一段平淡的内容。",
        current_chapter_position=2,
        prev_chapter_position=1,
    )

    assert report.passed


def test_render_block_zh_includes_missed_tokens() -> None:
    report = check_hook_echo(
        prev_chapter_text=_PREV_CHAPTER,
        current_chapter_text="三日后，清晨。李四走进客栈。",
        current_chapter_position=2,
        prev_chapter_position=1,
    )

    block = report.to_prompt_block(language="zh-CN")

    assert "钩子回环" in block
    assert "第 1 章" in block
    assert "漏掉" in block or "上一章" in block


def test_render_block_passing_returns_empty() -> None:
    report = check_hook_echo(
        prev_chapter_text="",
        current_chapter_text="",
        current_chapter_position=1,
    )

    assert report.to_prompt_block() == ""


def test_render_hook_echo_block_for_prewrite() -> None:
    report = check_hook_echo(
        prev_chapter_text=_PREV_CHAPTER,
        current_chapter_text="",
        current_chapter_position=2,
        prev_chapter_position=1,
    )
    block = render_hook_echo_block(report)

    assert "钩子回环" in block
    assert "上一章" in block


def test_render_hook_echo_block_handles_none() -> None:
    assert render_hook_echo_block(None) == ""
