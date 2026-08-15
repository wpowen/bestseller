"""时刻切片套娃检测（2026-08-15《端盘画神》定罪）。

病灶：「X的那一瞬→Y」顶针接力 + 「半分里/一寸里」量词切片续步——把一个动作
切成多个瞬间推进，是模型注水最便宜的句法（扩写轮病变均值 +6.8/次、62% 恶化；
ch38 一轮 4→41 处；ch25 发布稿 61 处 13.2/千字）。

语料定罪（.distillation_private 抽样 1335 章 / 544 万字）：
* 「的那一瞬(间)」：99.6% 章 0 命中，全语料最大 1.14/千字；
* 「量词+里」（半分里/一寸里/半步里…）：544 万字 0 命中。
→ 基础档 per_1k ≥1.2（越过人类全语料最大值）、升级档 ≥3.0 转 moment_slice_train。
两档都进 DESLOP_DISCOURSE_CATEGORIES（人类基线即零，不存在温和合法区）；
计分不封顶（uncapped）但无生杀权——只挣重生和留痕（2026-08-15 铁律）。

真书 ground truth：ch1/ch14/ch40 = 0.0/千字必须绿；ch25 = 13.2 必须红。
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect
from bestseller.services.ai_flavor_gate import DESLOP_DISCOURSE_CATEGORIES
from bestseller.services.deslop_revise import (
    _EXTRA_SELF_CHECK,
    _badness_components_for_test,
    _moment_slice_rate,
)


def _cats(text: str) -> list[str]:
    return [s.category for s in detect(text, language="zh").spans]


# 干净叙述填充：把片段撑过 min_chars=1200 而不引入其他检测器命中
PAD = (
    "他把窗关上，回头看了一眼灶台，锅里的水已经开了，白汽顶得锅盖啪啪作响。"
    "院子外面有人挑着担子经过，扁担吱呀吱呀，声音由近及远。"
    "她把抹布搭在盆沿上，转身去添柴，柴堆边上的老猫抬了抬眼皮，没有动。"
) * 8


def _sliced_sentence(i: int) -> str:
    verbs = ["退", "移", "抬", "转", "缩", "站", "擦", "压"]
    v = verbs[i % len(verbs)]
    return f"{v}的那一瞬她看见灰往桌角走。"


def test_slice_chain_flags_base_band() -> None:
    # 4 处「的那一瞬」/约 1580 字 ≈ 2.5/千字 → 基础档 moment_slice（<3.0 不升级）
    text = PAD + "".join(_sliced_sentence(i) for i in range(4)) + PAD
    cats = _cats(text)
    assert "moment_slice" in cats
    assert "moment_slice_train" not in cats


def test_heavy_chain_escalates_to_train() -> None:
    # 25 处 ≈ 7/千字 → 升级档 moment_slice_train（对齐 ch25 量级）
    text = PAD + "".join(_sliced_sentence(i) for i in range(25)) + PAD
    cats = _cats(text)
    assert "moment_slice_train" in cats
    assert "moment_slice" not in cats  # escalate 替换 category，不双报


def test_quantifier_slice_flags() -> None:
    # 「量词+里」接力（人类 544 万字 0 命中）
    chain = (
        "半分里老妪把灯往门框里探了一寸。一寸里灯光爬上她的袖口。"
        "半步里她听见抹布抖了一声。半寸里那道纹回到木缝里。"
    )
    text = PAD + chain + PAD
    assert "moment_slice" in _cats(text)


def test_single_legit_moment_is_exempt() -> None:
    # 人类会偶用一次「的那一瞬间」（0.4% 章命中）：单次不该报
    text = PAD + "門倒下的那一瞬间，他终于看清了里面的人。" + PAD
    assert "moment_slice" not in _cats(text)
    assert "moment_slice_train" not in _cats(text)


def test_short_fragment_exempt_by_min_chars() -> None:
    # min_chars=1200 护栏：短卡片不计速率（量级失明老坑）
    text = "退的那一瞬她看见灰。移的那一瞬灰走了。接住的那一瞬她僵住。"
    assert "moment_slice" not in _cats(text)
    assert "moment_slice_train" not in _cats(text)


def test_clean_prose_no_hit() -> None:
    assert "moment_slice" not in _cats(PAD)


def test_both_bands_in_deslop_trigger_set() -> None:
    assert "moment_slice" in DESLOP_DISCOURSE_CATEGORIES
    assert "moment_slice_train" in DESLOP_DISCOURSE_CATEGORIES


def test_self_check_covers_moment_slice() -> None:
    assert "时刻切片" in _EXTRA_SELF_CHECK
    assert "14 条" in _EXTRA_SELF_CHECK


def test_moment_slice_rate_dialogue_masked() -> None:
    # 对白里的「那一瞬」不计入叙述密度
    body = PAD + "".join(_sliced_sentence(i) for i in range(6))
    with_dialogue = body + "「就在你回头的那一瞬我看见了。」" * 3
    assert _moment_slice_rate(with_dialogue) <= _moment_slice_rate(body) + 0.01


def test_badness_rewards_removing_slices() -> None:
    """分水岭合同：聚合 span 只加 1，密度项必须让去切片的改稿真正胜出。

    43 处切片被并链后，badness 必须显著下降——否则 keep-better 会照收
    保留全部病的重写稿（ch25 12→31 发布即此病）。
    """

    diseased = PAD + "".join(_sliced_sentence(i) for i in range(25)) + PAD
    cleaned = PAD + "她退开半步，看见灰落到桌角，随即被老妪拢进袖中。" + PAD
    assert _badness_components_for_test(diseased) > _badness_components_for_test(cleaned) + 5.0


def test_no_op_guard_detector_actually_ran() -> None:
    """no-op 检查：确认规则文件真的被加载（防己方 yaml/json 断线静默空过）。"""

    heavy = PAD + "".join(_sliced_sentence(i) for i in range(25)) + PAD
    report = detect(heavy, language="zh")
    hit = [s for s in report.spans if s.category.startswith("moment_slice")]
    assert hit, "moment_slice 规则未加载——检查 patterns_zh.json discourse_rules"
    assert hit[0].hit_count >= 20
