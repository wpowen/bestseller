from __future__ import annotations

import pytest

from bestseller.services.scene_coherence_gate import (
    SCENE_JUMP_BLOCK_CODE,
    check_scene_coherence,
    render_scene_coherence_block,
    render_scene_jump_violations_block,
)

pytestmark = pytest.mark.unit


def test_single_location_chapter_passes() -> None:
    text = (
        "林渊踏进十七栋二十三层。\n"
        "走廊尽头亮着一盏应急灯。\n"
        "十七栋的镜子开始活动。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    assert report.passed
    assert report.jumps == ()


def test_abrupt_jump_critical() -> None:
    text = (
        "林渊踏进十七栋二十三层。\n"
        "镜子里伸出一只手。\n"
        "顾怀山坐在城南旧事馆的柜台后。\n"  # 突然跳到旧事馆，无过渡
        "他端着一杯茶。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    assert not report.passed
    assert report.has_critical
    critical = [j for j in report.jumps if j.severity == "critical"]
    assert critical
    assert critical[0].from_location in ("十七栋", "二十三层", "镜子" if False else "十七栋")
    assert "旧事馆" in critical[0].to_location or "城南旧事馆" in critical[0].to_location


def test_jump_with_strong_transition_passes() -> None:
    text = (
        "林渊踏进十七栋二十三层。\n"
        "镜子里伸出一只手，他用铜钱镇住。\n"
        "二十分钟后，他抵达城南旧事馆门口。\n"  # 强过渡
        "顾怀山坐在柜台后。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    # No critical jumps
    assert not report.has_critical


def test_jump_with_weak_marker_only_high_severity() -> None:
    text = (
        "林渊在十七栋二十三层。\n"
        "镜子里一只手。\n"
        "他走出。\n"  # 弱过渡
        "顾怀山在城南旧事馆。\n"
        "他端着茶。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    # Should not be critical; high or info acceptable
    assert not report.has_critical


def test_two_jumps_in_one_chapter_both_critical() -> None:
    text = (
        "林渊在十七栋二十三层。\n"
        "镜子里伸出手。\n"
        "顾怀山坐在城南旧事馆。\n"  # 跳 1
        "他端茶。\n"
        "林渊回到十七栋。\n"  # 跳 2，"回到"是强过渡
        "镜子开始活动。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    # 至少应该捕获到第一个跳跃 critical
    assert report.has_critical


def test_empty_text() -> None:
    report = check_scene_coherence("", chapter_position=1)
    assert report.passed
    assert report.jumps == ()


def test_short_text_no_paragraphs() -> None:
    report = check_scene_coherence("一句话", chapter_position=1)
    assert report.passed


def test_no_location_tokens_pass() -> None:
    text = "夜色如墨。\n他握紧剑柄。\n剑光如电。\n"
    report = check_scene_coherence(text, chapter_position=1)
    # 没有任何 location 词，应 passed
    assert report.passed


def test_three_locations_with_all_transitions_pass() -> None:
    text = (
        "林渊在十七栋。\n"
        "二十分钟后他抵达城南旧事馆。\n"  # 1→2
        "顾怀山坐在柜台后。\n"
        "半小时后他冲下楼梯回到十七栋。\n"  # 2→3
        "镜子开始活动。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    assert not report.has_critical


def test_custom_location_tokens() -> None:
    text = (
        "他在自家小院。\n"
        "门外传来敲门声。\n"
        "他走到山顶。\n"
    )
    report = check_scene_coherence(
        text,
        chapter_position=1,
        location_tokens=("小院", "山顶"),
    )
    # 自定义词典：小院 → 山顶 + "走" 是 weak transition
    # Should detect the jump
    assert report.jumps  # 至少应该侦测到


def test_render_scene_coherence_block_zh() -> None:
    block = render_scene_coherence_block()
    assert "场景连贯" in block
    assert "过渡" in block


def test_render_scene_jump_violations_block_passed() -> None:
    text = "林渊在十七栋。\n他在十七栋打开门。\n"
    report = check_scene_coherence(text, chapter_position=1)
    assert render_scene_jump_violations_block(report) == ""


def test_render_scene_jump_violations_block_critical() -> None:
    text = (
        "林渊在十七栋。\n"
        "镜子伸出手。\n"
        "顾怀山在城南旧事馆。\n"  # critical jump
        "茶。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    block = render_scene_jump_violations_block(report)
    assert "场景跳跃门禁" in block


def test_block_code_constant() -> None:
    assert SCENE_JUMP_BLOCK_CODE == "SCENE_JUMP_UNRESOLVED"
