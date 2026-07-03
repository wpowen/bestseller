from __future__ import annotations

import pytest

from bestseller.services.scene_coherence_gate import (
    SCENE_JUMP_BLOCK_CODE,
    check_scene_coherence,
    render_scene_coherence_block,
    render_scene_jump_violations_block,
)

pytestmark = pytest.mark.unit

# 夹具统一使用通用地点簇（医院/警局等）——默认地点组已去单书化
# （原十七栋/旧事馆是某本书的私有地图），测试跟随通用组。


def test_flashback_reference_does_not_trigger_jump() -> None:
    """回忆里提到另一个地点不得算作场景跳跃。

    段落"这张脸他在医院见过"只是主角对过去的回忆，人仍然在警局。
    """

    text = (
        "林渊到警局门口时，夜色已经贴近子时。\n\n"
        "他抬眼看对方，没接他的手。\n\n"
        "这张脸他在医院见过。那时她拦住他，问过一句很怪的话。\n\n"
        "审讯室的灯亮着。\n\n"
        "他走进审讯室，指尖开始发凉。"
    )
    report = check_scene_coherence(text, chapter_position=1)
    assert not report.has_critical, (
        f"flashback reference must not trip scene jump; got jumps: {report.jumps}"
    )


def test_single_location_chapter_passes() -> None:
    text = (
        "林渊踏进医院住院部。\n"
        "病房尽头亮着一盏应急灯。\n"
        "病房里的仪器开始报警。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    assert report.passed
    assert report.jumps == ()


def test_abrupt_jump_critical() -> None:
    text = (
        "林渊踏进医院病房。\n"
        "帘子后伸出一只手。\n"
        "顾怀山坐在警局的值班台后。\n"  # 突然跳到警局，无过渡
        "他端着一杯茶。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    assert not report.passed
    assert report.has_critical
    critical = [j for j in report.jumps if j.severity == "critical"]
    assert critical
    # 门禁以组首 token 作为组标签汇报（医院组=太平间、警局组=派出所）。
    assert critical[0].from_location in ("医院", "病房", "太平间")
    assert critical[0].to_location in ("警局", "派出所", "审讯室")


def test_jump_with_strong_transition_passes() -> None:
    text = (
        "林渊踏进医院病房。\n"
        "帘子后伸出一只手，他用铜钱镇住。\n"
        "二十分钟后，他抵达派出所门口。\n"  # 强过渡
        "顾怀山坐在值班台后。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    # No critical jumps
    assert not report.has_critical


def test_flashback_location_reference_does_not_count_as_scene_jump() -> None:
    text = (
        "林渊踏进医院病房，掌心的铜钱被冷汗浸透。\n"
        "他想起三十年前爷爷在派出所做笔录，纸页上只留下半枚血印。\n"
        "病房里的应急灯又闪了一下，玻璃重新映出那张脸。\n"
    )

    report = check_scene_coherence(text, chapter_position=1)

    assert report.passed
    assert report.jumps == ()


def test_that_time_location_reference_does_not_count_as_scene_jump() -> None:
    text = (
        "病房玻璃慢慢起雾，雾里出现一个女人的脸。\n"
        "这张脸他在警局见过。那时她拦住他，问过一句怪话。\n"
        "现在，她贴在病房玻璃里，嘴唇翕动。\n"
        "护士推门进来，林渊收回视线。\n"
    )

    report = check_scene_coherence(text, chapter_position=1)

    assert report.passed
    assert report.jumps == ()


def test_phone_and_route_location_mentions_do_not_count_as_scene_jump() -> None:
    text = (
        "林渊接到的电话是下午三点打来的。打电话的人叫王老板，"
        "做旧货生意的，摆了十几年摊子。\n"
        "电话里王老板的声音发紧。他说医院有单生意，问林渊接不接。\n"
        "医院离他住的地方不算远，骑车十五分钟。\n"
        "“你在医院楼下等我。”林渊挂断电话。\n"
        "那家医院在老城区的边缘，六年前因为一桩命案被封过一层楼。\n"
        "王老板站在医院对面的巷子口抽烟。"
    )

    report = check_scene_coherence(text, chapter_position=1)

    assert report.has_critical is False


def test_item_source_and_direction_mentions_do_not_count_as_scene_jump() -> None:
    text = (
        "“你在哪？”\n"
        "“医院。三楼。”\n"
        "林渊问：“王老板，那面镜子你从哪买的？”\n"
        "“老孙头的铺子。三千块。”\n"
        "电话断了。\n"
        "林渊站在铺门口，罗盘指针没有指向南方——它正对着医院的方向。\n"
        "二十三岁的林渊冲进雨里。\n"
        "电梯门开的时候，轿厢里的灯在响。"
    )

    report = check_scene_coherence(text, chapter_position=1)

    assert report.has_critical is False


def test_jump_with_weak_marker_only_high_severity() -> None:
    text = (
        "林渊在医院病房。\n"
        "帘子后一只手。\n"
        "他走出。\n"  # 弱过渡
        "顾怀山在警局。\n"
        "他端着茶。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    # Should not be critical; high or info acceptable
    assert not report.has_critical


def test_two_jumps_in_one_chapter_both_critical() -> None:
    text = (
        "林渊在医院病房。\n"
        "帘子后伸出手。\n"
        "顾怀山坐在警局。\n"  # 跳 1
        "他端茶。\n"
        "林渊回到医院。\n"  # 跳 2，"回到"是强过渡
        "仪器开始报警。\n"
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
        "林渊在医院。\n"
        "二十分钟后他抵达派出所。\n"  # 1→2
        "顾怀山坐在值班台后。\n"
        "半小时后他冲下楼梯回到医院。\n"  # 2→3
        "仪器开始报警。\n"
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
    text = "林渊在医院。\n他在医院打开门。\n"
    report = check_scene_coherence(text, chapter_position=1)
    assert render_scene_jump_violations_block(report) == ""


def test_render_scene_jump_violations_block_critical() -> None:
    text = (
        "林渊在医院。\n"
        "帘子伸出手。\n"
        "顾怀山在警局。\n"  # critical jump
        "茶。\n"
    )
    report = check_scene_coherence(text, chapter_position=1)
    block = render_scene_jump_violations_block(report)
    assert "场景跳跃门禁" in block


def test_block_code_constant() -> None:
    assert SCENE_JUMP_BLOCK_CODE == "SCENE_JUMP_UNRESOLVED"
