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


def test_simile_count_excludes_noun_compounds() -> None:
    # 画像/头像/偶像/像样/想像 这类名词性「像」不是明喻，堆再多也不该触发。
    base = (
        "祠堂正墙挂着祖宗画像，画像下摆着一排遗像。他掏出手机换了个头像，"
        "屏幕上的图像有些发虚，录像还在转。柜顶那尊佛像落了灰，"
        "神龛里的塑像缺了只手。这屋子收拾得不像样，他想像不出从前的模样。"
        "偶像剧在电视里放着，影像忽明忽暗。"
    )
    text = base * 10  # 名词性「像」密度远超阈值，真明喻为 0
    report = detect(text, language="zh-CN", chapter_number=5)
    assert not [s for s in report.spans if s.category == "simile_overrun"], (
        "名词复合词里的「像」被计入明喻数 —— 误伤词剔除失效"
    )


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


def test_verb_tic_family_aggregate_detected() -> None:
    """词族聚合口径(2026-07-08):单词各3-4次躲过单词阈值,但家族合计≥6且
    ≥15/万字仍是读者一眼识别的AI腔(真机用户:"老是出现撞烫")。"""
    from bestseller.services.ai_flavor.detector import detect

    base = (
        "他撞开门,掌心烫了一下。钻进走廊时,他攥紧了钥匙。"
        "风从窗缝里爬进来。他又撞上桌角,水杯烫手,他把手指攥得发白。"
        "一条影子钻过灯光,顺着墙根爬。"
    )  # 撞×2 烫×2 钻×2 攥×2 爬×2 = 家族10次
    text = base * 12  # ~1300字 → 家族120次? no: base*12 → 每词24次,家族120,密度高
    # 用低密度版本更贴真机: 家族7次落在正常长度里
    filler = "他把报告放回桌上,顺手关了台灯。走廊尽头的电梯还亮着。" * 40
    text = base + filler  # 家族10次 / ~1300字 ≈ 77/万字? filler 40*26=1040+108 → 密度 87/万字
    report = detect(text, language="zh-CN", chapter_number=2)
    fam = [s for s in report.spans if s.category == "verb_tic_spam"]
    assert fam, "词族聚合复读必须被检出"
    assert "词族" in fam[0].why or "×" in fam[0].why


def test_verb_tic_triggers_deslop(tmp_path) -> None:
    """闭环:verb_tic_spam 检出后必须路由 deslop 整段重写(此前不在触发集,检出也不清)。"""
    from bestseller.services.ai_flavor_gate import (
        AiFlavorGateConfig,
        DESLOP_DISCOURSE_CATEGORIES,
        needs_deslop_revise,
        run_ai_flavor_gate,
    )

    assert "verb_tic_spam" in DESLOP_DISCOURSE_CATEGORIES
    base = (
        "他撞开门,掌心烫了一下。钻进走廊时,他攥紧了钥匙。"
        "风从窗缝里爬进来。他又撞上桌角,水杯烫手,他把手指攥得发白。"
        "一条影子钻过灯光,顺着墙根爬。"
    )
    filler = "他把报告放回桌上,顺手关了台灯。走廊尽头的电梯还亮着。" * 40
    outcome = run_ai_flavor_gate(
        chapter_number=2,
        content_md=base + filler,
        language="zh-CN",
        config=AiFlavorGateConfig(),
        project_output_dir=tmp_path,
    )
    assert needs_deslop_revise(outcome) is True
