"""L1 tests for the simile-overrun / illogical-synaesthesia detector.

真机病灶（《我在命馆收诡当租客》ch1）:
- 首句「门板上三下闷响，湿得像有人拿额头在撞。」——把"响"（听觉）说成"湿"
  （触觉/水分），再直接接明喻，是跨模态属性错配的通感病句，冷读者第 10 字即卡。
- 全章 2821 字堆了 23 处明喻标记（81/万字），远超正常散文（~0-10/万字）。

明喻限额规则（cinematic_pov 第 9 条，commit 7907c17）靠 prompt 约束，MiniMax-M3
服从性差，管线此前没有确定性明喻/通感检测器兜底。本检测器把"限额"从"劝模型"变成
"验模型"，findings 经 deslop_revise 闭环喂回写手重写。
"""

from __future__ import annotations

import pytest

from bestseller.services.ai_flavor.detector import detect

pytestmark = pytest.mark.unit


def test_simile_overrun_flags_saturated_chapter() -> None:
    # ~2800 字里 20+ 处明喻标记，密度与真机 ch1 同级。
    base = (
        "他抬头，那张脸白得像纸，眼神冷得像冰。桌上的灯像一只疲惫的眼睛，"
        "光晕仿佛在呼吸。账本厚得如同砖头，纸页黄得像隔夜的姜。他的手指"
        "抖得好似风里的叶子，指节涩得像生锈的铰链。门外的风宛如一条湿绳，"
        "缠上来，冷得如同铁。他心里那点火苗好像随时会灭，念头乱得像一团线。"
    )
    text = base * 8  # ~1000 字，过 800 字下限，明喻密度远超阈值
    report = detect(text, language="zh-CN", chapter_number=1)
    spans = [s for s in report.spans if s.category == "simile_overrun"]
    assert spans, "高密度明喻堆叠必须被检出"
    assert "明喻" in spans[0].why or "比喻" in spans[0].why


def test_simile_overrun_ignores_moderate_prose() -> None:
    # 3-4 处明喻散落在近 3000 字里 —— 正常文笔，不该触发。
    base = (
        "他推门进屋，把伞收好靠在墙边。桌上的饭菜凉了，母亲坐在灯下补衣服，"
        "针脚细得像发丝。他说今天加了班，母亲点点头，把碗推过去。窗外落着小雨，"
        "巷子里有人骑车经过，铃铛响了两声。他吃完饭，帮着把桌子擦干净，"
        "才说出白天发生的事。母亲听着，没打断，手里的线一抽一抽。"
    )
    text = base * 6  # ~3000 字，仅 6 处明喻，密度 ~20/万字
    report = detect(text, language="zh-CN", chapter_number=2)
    assert not [s for s in report.spans if s.category == "simile_overrun"]


def test_synaesthesia_mismatch_flags_wet_sound() -> None:
    # 把听觉（响/声）说成有水分（湿/潮/黏）再接明喻 —— 物理不通的通感病句。
    text = (
        "门板上三下闷响，湿得像有人拿额头在撞。他站起身，屋里静下来。"
        "远处又传来一声，潮得如同泡了水的鼓面。"
    )
    report = detect(text, language="zh-CN", chapter_number=1)
    syn = [s for s in report.spans if s.category == "synaesthesia_mismatch"]
    assert syn, "把声音说成'湿/潮'的跨模态病句必须被检出"
    assert "响" in syn[0].matched_text or "声" in syn[0].matched_text


def test_synaesthesia_mismatch_allows_conventional() -> None:
    # 温度/味觉修饰声音（冷冷的声音 / 甜嗓子）是汉语惯常通感，不该误伤。
    text = (
        "她的声音冷得像冰，一字一顿。他嗓子甜得像含了糖，逗得孩子直笑。"
        "风声很大，屋里却暖。"
    )
    report = detect(text, language="zh-CN", chapter_number=3)
    assert not [
        s for s in report.spans if s.category == "synaesthesia_mismatch"
    ]


def test_synaesthesia_mismatch_is_not_advisory_capped() -> None:
    """通感病句是真实错误，不进 advisory 结构性上限 —— 多处出现能推高分数。"""
    from bestseller.services.ai_flavor.detector import _score
    from bestseller.services.ai_flavor.types import AiFlavorSpan

    spans = tuple(
        AiFlavorSpan(
            start=i,
            end=i + 2,
            matched_text="闷响湿",
            rule_id="zh.synaesthesia.wet_sound",
            category="synaesthesia_mismatch",
            severity="warn",
            suggestions=(),
            sentence_span=(i, i + 2),
            why="通感病句",
            remove_sentence_on_block=False,
        )
        for i in range(0, 20, 2)
    )
    # 10 个 warn × 4 分 = 40，若被 24 上限截断则 <=24。未截断证明不在 cap 集。
    assert _score(spans) > 24.0


def test_synaesthesia_triggers_deslop_at_low_score(tmp_path) -> None:
    """闭环保证：通感病句分数低（advisory）也必须路由到整段 deslop 重写——
    span patcher 删不掉这类语病，只有整段重写能改。"""
    from bestseller.services.ai_flavor_gate import (
        AiFlavorGateConfig,
        needs_deslop_revise,
        run_ai_flavor_gate,
    )

    bad = (
        "门板上三下闷响，湿得像有人拿额头在撞。他站起身，屋里静下来。"
        "袖口里那方黄纸压着盆底，砖缝里渗上来的冷气顺着腕骨往上爬。"
    )
    outcome = run_ai_flavor_gate(
        chapter_number=1,
        content_md=bad,
        language="zh-CN",
        config=AiFlavorGateConfig(),
        project_output_dir=tmp_path,
    )
    assert outcome.decision != "block"  # 分数低 / advisory
    assert needs_deslop_revise(outcome) is True
