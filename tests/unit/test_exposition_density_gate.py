from __future__ import annotations

import pytest

from bestseller.services.exposition_density_gate import (
    check_exposition_density,
    render_exposition_density_block,
)

pytestmark = pytest.mark.unit


_DUMP_CHAPTER = (
    "茅山术法分为内丹、外咒、罗盘三大门类，自唐代以来便有传承。\n"
    "据说三族指的是南茅山、北出马仙、东钱家三派，三百年前曾立下盟约。\n"
    "原来青囊秘卷中记载，凡入镜者，必须以血为引，方能脱离镜局。\n"
    "事实上，林家先祖林远山三百年前封印了第一面困魂镜，立下'三族之内，必有一人还'的誓言。\n"
    "传说罗盘的用法是根据二十四山方位推演吉凶，需要配合天干地支使用。\n"
    "工作原理上，阴阳眼可以看穿鬼物真身，但每次开眼都需要消耗心头精血。\n"
)

_ACTION_CHAPTER = (
    "林渊握紧罗盘，盯着楼道尽头的镜子。\n"
    "“你看到那个东西了？”孙九斤压低声音。\n"
    "“看到了。”林渊低头。\n"
    "他抬手，从青囊中抽出一枚黄符。\n"
    "符纸在指尖燃起。\n"
    "“走。”\n"
    "两人沿着楼梯向上。\n"
    "镜面映出他们的影子，但影子比他们慢了半拍。\n"
)


def test_check_exposition_dump_chapter_critical() -> None:
    report = check_exposition_density(_DUMP_CHAPTER, chapter_position=2)

    assert not report.passed
    assert report.finding.severity == "critical"
    assert report.finding.code == "EXPOSITION_DUMP"
    assert report.finding.exposition_ratio > 0.5


def test_check_exposition_action_chapter_passes() -> None:
    report = check_exposition_density(_ACTION_CHAPTER, chapter_position=2)

    assert report.passed
    assert report.finding.code == "EXPOSITION_OK"


def test_check_exposition_empty_text() -> None:
    report = check_exposition_density("", chapter_position=1)
    assert report.passed
    assert report.finding.exposition_ratio == 0


def test_check_exposition_late_chapter_lenient() -> None:
    """Same exposition load in chapter 50 should be info only, not critical."""

    moderate = (
        "据说茅山术法分内丹、外咒两类。\n"
        "“你听说过吗？”\n"
        "“听过。”\n"
        "林渊抬手，符纸燃起。\n"
    )

    report = check_exposition_density(moderate, chapter_position=50)
    assert report.finding.severity != "critical"


def test_check_exposition_heavy_dump_phrase_amplifies_weight() -> None:
    text = (
        "茅山术法分为三类。\n"
        "现在，让我们走进客栈。\n"
        "李四要了一壶酒。\n"
    )
    # Heavy phrase ('茅山术法分为') should be flagged even with light exposition elsewhere
    report = check_exposition_density(text, chapter_position=1)
    assert report.finding.exposition_ratio > 0


def test_check_exposition_detects_dump_runs() -> None:
    """Three consecutive exposition paragraphs trigger info-dump detection."""

    text = (
        "据说三族曾立下盟约。\n"
        "传说罗盘用法极为复杂。\n"
        "原来青囊秘卷记载着秘法。\n"
        "“走吧。”林渊低声。\n"
    )
    report = check_exposition_density(text, chapter_position=1)
    assert report.finding.info_dump_runs >= 1


def test_check_exposition_worst_excerpts_populated() -> None:
    report = check_exposition_density(_DUMP_CHAPTER, chapter_position=1)
    assert report.finding.worst_excerpts
    assert any("分为" in e or "传说" in e or "据说" in e for e in report.finding.worst_excerpts)


def test_render_exposition_density_block_critical() -> None:
    report = check_exposition_density(_DUMP_CHAPTER, chapter_position=2)
    block = report.to_prompt_block(language="zh-CN")

    assert "铺垫密度门" in block
    assert "%" in block


def test_render_exposition_density_block_passing_empty() -> None:
    report = check_exposition_density(_ACTION_CHAPTER, chapter_position=2)
    assert report.to_prompt_block() == ""


def test_render_prewrite_block_for_early_chapter() -> None:
    report = check_exposition_density(_DUMP_CHAPTER, chapter_position=1)
    block = render_exposition_density_block(report)

    assert "铺垫节制" in block
    assert "25%" in block


def test_render_prewrite_block_handles_none() -> None:
    assert render_exposition_density_block(None) == ""


def test_check_exposition_flashback_ratio() -> None:
    text = (
        "三十年前，林家辉补过那面镜子。\n"
        "二十三年前，林正淳第一次进入十七栋。\n"
        "三年前，他再次入镜，至今未归。\n"
        "今夜，林渊收到了一封信。\n"
    )
    report = check_exposition_density(text, chapter_position=1)
    assert report.finding.flashback_ratio > 0


def test_check_exposition_dialogue_exempt() -> None:
    """A chapter that is mostly dialogue should not be flagged."""

    text = (
        '“茅山三派指南茅、北出马、东钱家。”孙九斤说。\n'
        '“我知道。”林渊回答。\n'
        '“你父亲也知道？”\n'
        '“知道得比我多。”\n'
    )
    report = check_exposition_density(text, chapter_position=1)
    assert report.passed
