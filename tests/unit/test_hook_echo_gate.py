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


def test_semantic_overlap_rescue_for_parallel_action_echo() -> None:
    """Regression for 青囊 ch1→ch2 false-negative (2026-05-23).

    Original parallel-action echo:
    - ch1 ends "镜中林渊忽然睁开了眼"
    - ch2 opens "镜中的林渊睁眼时，真正的林渊先把自己的眼睛闭上"

    Token bag-of-words misses this (no shared nouns), but semantic
    groups overlap on {mirror_action, eye_action, protagonist_self}.
    The gate must NOT flag this as HOOK_ECHO_MISSING.
    """

    prev = (
        "镜面深处，那七张脸让开一道缝。\n"
        "第八张脸正在长成他。\n"
        "门外的假王建业用父亲林正淳的声音笑了一下。\n"
        "“渊，开门。”\n"
        "林渊盯着镜子里的第八张脸，慢慢把铜钱按进镜框缺口。\n"
        "“先查张建军。”\n"
        "镜中那张快要长全的林渊，忽然睁开了眼。"
    )
    curr = (
        "镜中的“林渊”睁眼时，真正的林渊先把自己的眼睛闭上。\n"
        "阴阳眼最忌硬看。"
    )
    report = check_hook_echo(
        prev_chapter_text=prev,
        current_chapter_text=curr,
        prev_chapter_position=1,
        current_chapter_position=2,
    )
    assert report.finding.severity != "critical", (
        f"parallel-action semantic echo must not be flagged critical; "
        f"got {report.finding.severity} detail={report.finding.detail}"
    )


def test_extract_hook_tokens_prefers_concrete_hooks_over_connectors() -> None:
    tokens = extract_hook_tokens(_PREV_CHAPTER)

    assert "突然" not in tokens
    assert "下一刻" not in tokens
    assert "名单" in tokens
    assert "门外" in tokens or "脚步声" in tokens


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
        ("王建业的尸体手里攥着回执镜片。", "死者指缝里的碎镜像一枚凭证。", "回执镜片"),
        ("小雨为什么能活到现在？", "那个女孩被镜债保住命，不是因为好运。", "小雨为什么能活到现在"),
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


def test_extract_hook_tokens_drops_low_signal_connectors_when_domain_hooks_exist() -> None:
    text = (
        "然而下一刻，门外突然响起脚步声。"
        "林渊按住铜钱，青囊秘卷发烫，三短一长之后，"
        "镜里有人问：你放的？有没有一面旧镜子？"
        "王建业终于认账，账页开始入账。"
    )

    tokens = extract_hook_tokens(text)

    assert "然而" not in tokens
    assert "突然" not in tokens
    assert "青囊" in tokens
    assert "有没有一面旧镜子" in tokens


def test_extract_hook_tokens_ignores_mid_chapter_questions() -> None:
    text = (
        "“你承认那孩子死在你的车轮下吗？”林渊问。\n"
        "两人随后继续查账，铜钱压住镜框。\n"
        + ("账页翻动。" * 160)
        + "\n门外响起敲门声。"
    )

    tokens = extract_hook_tokens(text)

    assert "你承认那孩子死在你的车轮下吗" not in tokens
    assert "敲门" in tokens


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
