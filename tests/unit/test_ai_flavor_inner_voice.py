"""L1 tests for the first-person inner-voice absence detector (advisory).

真机病灶（E1/E2/E3 盲评，记忆 pov-inner-voice-readability-gap）:
- 对标《诡秘之主》盲评全败的根因不是文笔而是可读性——主角念头全被转成
  生理症状，内心标记 3 章全 0 / 叙述层问号 0（对标 6 处 / 15 个）。
- cinematic_pov 第 8 条"POV 内心授权"靠 prompt，M3 大 prompt 服从性差，
  管线没有确定性检测器兜底。

口径（scratchpad metrics.py FP_INNER，E3 真机验证过的量尺）:
- 第一人称叙述层没有「心道」标记，内心声音=盘算/自问句式
  （我得/我不能/难道/万一/要不要/说不定…）+ 叙述层问号。
- 只对第一人称章生效（叙述层「我」达阈值）；对白里的「我」不算叙述层。
- 全章命中 <2 → 一个 advisory (info) span，soft 不阻断、不触发 deslop。
"""

from __future__ import annotations

import pytest

from bestseller.services.ai_flavor.detector import detect

pytestmark = pytest.mark.unit


def _fp_base_flat() -> str:
    # 第一人称、动作流水账、零内心声音零问号 —— E1 病灶的合成复现。
    return (
        "我推开门，把伞靠在墙边。屋里的灯还亮着，我走到桌前坐下，"
        "翻开那本账册。纸页发脆，我一页一页往后翻。窗外的雨没停，"
        "我起身把窗关严，又回到桌前。我把最后一页抄完，合上账册，"
        "吹灭了灯。我在黑暗里坐了一会儿，听着雨点砸在瓦上。"
    )


def test_inner_voice_absence_flags_flat_first_person() -> None:
    text = _fp_base_flat() * 15  # ~1600 字，第一人称，全章零盘算零问号
    report = detect(text, language="zh-CN", chapter_number=1)
    spans = [s for s in report.spans if s.category == "inner_voice_absence"]
    assert spans, "第一人称全章无自问/盘算必须给 advisory 提示"
    assert spans[0].severity == "info"
    assert "内心" in spans[0].why or "盘算" in spans[0].why or "自问" in spans[0].why


def test_inner_voice_present_no_flag() -> None:
    # 同样的第一人称流水账，但补了 2 处盘算/自问 —— 达标不该触发。
    text = (
        _fp_base_flat() * 15
        + "我得把这页藏好，万一让人看见，账就赖不掉了。"
        + "难道他早就知道我会来？"
    )
    report = detect(text, language="zh-CN", chapter_number=1)
    assert not [s for s in report.spans if s.category == "inner_voice_absence"]


def test_narration_questions_count_as_inner_voice() -> None:
    # 叙述层问号（非对白）也是内心声音的口径之一。
    text = (
        _fp_base_flat() * 15
        + "我盯着那行字看了很久。这笔账是谁记的？为什么偏偏记在最后一页？"
    )
    report = detect(text, language="zh-CN", chapter_number=1)
    assert not [s for s in report.spans if s.category == "inner_voice_absence"]


def test_third_person_chapter_not_flagged() -> None:
    # 第三人称章不适用本口径（有"心道"标记体系，另行评判）。
    base = (
        "陈砚推开门，把伞靠在墙边。屋里的灯还亮着，他走到桌前坐下，"
        "翻开那本账册。纸页发脆，他一页一页往后翻。窗外的雨没停，"
        "他起身把窗关严，又回到桌前。他把最后一页抄完，合上账册。"
    )
    report = detect(base * 15, language="zh-CN", chapter_number=1)
    assert not [s for s in report.spans if s.category == "inner_voice_absence"]


def test_dialogue_wo_does_not_make_first_person() -> None:
    # 「我」只出现在对白里 —— 叙述层仍是第三人称，不该触发。
    base = (
        "陈砚推开门，把伞靠在墙边。“我今天不去了。”他说。屋里的灯还亮着，"
        "他走到桌前坐下。“我把账抄完了，我明天一早送去，我说话算话。”"
        "他合上账册，吹灭了灯，在黑暗里坐了一会儿。"
    )
    report = detect(base * 15, language="zh-CN", chapter_number=1)
    assert not [s for s in report.spans if s.category == "inner_voice_absence"]


def test_short_fragment_not_flagged() -> None:
    # 长度下限守卫：场景片段/短卡不评全章口径。
    report = detect(_fp_base_flat(), language="zh-CN", chapter_number=1)
    assert not [s for s in report.spans if s.category == "inner_voice_absence"]


def test_inner_voice_absence_is_soft_advisory() -> None:
    """soft 保证：单独一个缺失提示不阻断、不触发 deslop 重写。"""
    from bestseller.services.ai_flavor_gate import DESLOP_DISCOURSE_CATEGORIES
    from bestseller.services.ai_flavor.detector import _score
    from bestseller.services.ai_flavor.types import AiFlavorSpan

    assert "inner_voice_absence" not in DESLOP_DISCOURSE_CATEGORIES
    span = AiFlavorSpan(
        start=0,
        end=1,
        matched_text="我",
        rule_id="zh.inner_voice.absence",
        category="inner_voice_absence",
        severity="info",
        suggestions=(),
        sentence_span=(0, 1),
        why="内心声音缺失",
        remove_sentence_on_block=False,
    )
    assert _score((span,)) <= 24.0  # advisory cap 内，永不独自推过 block 线
