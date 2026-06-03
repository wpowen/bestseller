from __future__ import annotations

import pytest

from bestseller.services.anti_meta_gate import check_anti_meta_gate
from bestseller.services.scene_beat_planner import build_scene_beat_sheet
from bestseller.services.scene_beat_renderer import render_scene_beat_sheet_block
from bestseller.services.show_dont_tell_gate import check_show_dont_tell_gate

pytestmark = pytest.mark.unit


def test_scene_beat_sheet_is_deterministic_and_renders_camera_prompt() -> None:
    kwargs = dict(
        chapter_number=1,
        scene_number=1,
        scene_title="矿镇灰井",
        scene_type="reveal",
        time_label="黄昏",
        participants=["林烬", "苏晚照"],
        chapter_goal="林烬在矿镇灰井中被压迫，意外感应道种，命运开始转向。",
        story_purpose="让林烬做出一个看似自杀的选择，并产生具体后果。",
        emotion_purpose="压抑转向决断",
        entry_state={"location": "矿镇灰井底部", "sound": "井口钢钎声"},
        exit_state={"visible": "井壁渗出一线暗红火光"},
        word_target=900,
    )
    first = build_scene_beat_sheet(**kwargs)
    second = build_scene_beat_sheet(**kwargs)
    assert first == second
    assert first.beats[-1].beat_type == "cliff"
    assert first.beats[-1].ending_format == "reveal"

    block = render_scene_beat_sheet_block(first, language="zh-CN")
    assert "本场镜头脚本" in block
    assert "看得到的事情" in block
    assert "收尾铁律" in block
    assert "钩子" not in block
    assert "卖点" not in block
    assert "承诺" not in block


def test_anti_meta_gate_blocks_design_language_and_summary_ending() -> None:
    text = (
        "林烬抬头看着井口。\n"
        "这一章里，他面对的不只是对手，还有旧秩序塞进他体内的恐惧。\n"
        "章末余波并未平息，多方势力都在重新估算他的威胁等级。"
    )
    report = check_anti_meta_gate(text, chapter_position=1)
    assert not report.passed
    assert any(f.severity == "block" and f.term == "这一章" for f in report.findings)
    assert any(f.term == "多方势力" for f in report.findings)
    assert not report.ending_passed


def test_anti_meta_gate_protects_dialogue_and_physical_aftermath() -> None:
    text = (
        "陆沉跟上他的步伐：“那接下来——”\n"
        "“回杂役峰。”宁尘打断他。\n"
        "灵压的余波掀飞碎石，井壁裂开一道黑缝。"
    )
    report = check_anti_meta_gate(text, chapter_position=1)

    terms = {finding.term for finding in report.findings}
    assert "接下来" not in terms
    assert "余波" not in terms
    assert report.passed


@pytest.mark.parametrize(
    "ending",
    [
        "账页上的墨迹在蠕动。\n\n新增了一条记录。不是小雨的名字。是陈默。",
        "日期显示，今天凌晨两点十六分。比那条消息早一分钟。门外，有脚步声正在靠近。",
        "镜面裂开一道缝，从缝隙里，伸出一只青白的手。那只手的无名指上，戴着一枚戒指。",
        "然后它开口了。“你父亲也在这儿。”",
    ],
)
def test_anti_meta_gate_accepts_visible_in_scene_endings(ending: str) -> None:
    report = check_anti_meta_gate(ending, chapter_position=1)

    assert report.ending_passed
    assert report.passed


def test_show_dont_tell_gate_flags_telling_patterns() -> None:
    text = (
        "他知道这很蠢，也很贵，但不这么做就永远翻不了身。"
        "压抑的恐惧涌上心头。"
        "林烬和顾行舟的关系开始变化。"
    )
    report = check_show_dont_tell_gate(text, chapter_position=1)
    codes = {finding.code for finding in report.findings}
    assert "SHOW_DONT_TELL_MOTIVE_EXPLANATION" in codes
    assert "SHOW_DONT_TELL_EMOTION_NAMING" in codes
    assert "SHOW_DONT_TELL_RELATIONSHIP_LABEL" in codes


def test_show_dont_tell_gate_protects_dialogue_explanations() -> None:
    text = "017说：“他知道你要用证人，所以他在清场。”林鸢把名单翻到最后一页。"
    report = check_show_dont_tell_gate(text, chapter_position=1)

    assert not report.findings


def test_show_dont_tell_gate_allows_visible_seeing_action() -> None:
    text = "林烬把账册推过去，让对方看清楚：“你不在第七行。”"
    report = check_show_dont_tell_gate(text, chapter_position=1)

    assert not report.findings
