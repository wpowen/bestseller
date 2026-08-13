"""简介的生理厌恶意象密度（确定性，零 token）。

2026-08-07 真机 custom-xuanhuan-1786023406：用户读完简介第一反应是「感官上让人
感觉有点恶心」，而当时每一道关都合法通过——标题 83.2、简介 72.5、画像判官 3/3
会点均分 8.67、arena 0.50、LLM 质量判官 0.78、零 blocking。

**问 LLM 不管用**：画像判官给这本书的 aversion 只有 2.0/10，原话「虫蛊味儿冲但
不恶心」。模型不能可靠自述生理反应。改成数触发词立刻分开：

    config/appeal_reference_blurbs.yaml 的 42 条真实爆款简介 → 40 条命中 0，
    另 2 条各只有 1 个（法医秦明「腐」、穿书自救指南「吐」）；
    本书 7 个不同触发词。

阈值取「不同词数 ≥2」：整个爆款语料无一命中（实测 0 误报），本书超线 3.5 倍。
不用密度是因为简介只有一两百字，单个词就能把「每千字」推到 12 以上——短文本上
密度是噪声，词种数才是信号。
"""

from __future__ import annotations

from bestseller.services.blurb_pathology import detect_blurb_pathology


def _aversive(text: str) -> list:
    return [f for f in detect_blurb_pathology(text) if f.code == "AVERSIVE_IMAGERY"]


# 真机原文（对外见光的那份 synopsis）。
_REAL_REGRESSION = (
    "沈落是万蛊宗外门杂役，喂虫少年，旁人连废虫卵都不让他碰。师兄孙坤的金蚕蛊"
    "嫌他脏，绕道三尺。三个月前，他偷偷从臭水沟捡回一把孵不出东西的废虫卵，拿"
    "豁口破陶碗喂一只瘦得皮包骨的野蚁。可今夜不一样——他腕上那条死死闭着的虫脉"
    "黑线，被蚁后啃开一截；破碗里那只没人看一眼的瘦蚁，眨眼胀成拳头大的黑甲虫王。"
)


def test_real_regression_fires_fatal() -> None:
    found = _aversive(_REAL_REGRESSION)
    assert len(found) == 1
    assert found[0].severity == "fatal"
    # 触发词要报出来，否则文案工序不知道该改哪几个。
    for w in ("臭水沟", "虫卵", "啃开", "胀成", "皮包骨"):
        assert w in found[0].detail


def test_single_trigger_is_allowed() -> None:
    # 法医秦明式：一个「腐」不构成反胃，真爆款里就是这么用的。
    assert _aversive("他是法医，每天面对的是尸体腐烂后的真相与人心。") == []


def test_negative_emotion_is_not_aversive() -> None:
    # 恐惧/绝望/痛苦/血战都是正常卖点，词表刻意不收——这条界线是它
    # 不误伤黑暗题材的原因。
    text = (
        "绝望笼罩全城，恐惧在人群中蔓延。他背负血海深仇，在痛苦中挣扎，"
        "面对残酷的杀戮与背叛，只能一步步走向那场必死的血战。"
    )
    assert _aversive(text) == []


def test_real_bestseller_style_blurb_is_clean() -> None:
    text = (
        "三十年河东，三十年河西，莫欺少年穷！萧炎，一个天才少年，"
        "在一夜之间沦为废物，受尽冷眼与嘲讽。直到那枚戒指里的老者睁开眼。"
    )
    assert _aversive(text) == []


def test_horror_setting_without_visceral_triggers_is_clean() -> None:
    # 题材可以恐怖，简介不必反胃。
    text = "夜里十二点，宿舍门外传来敲门声。可这栋楼，三年前就该拆了。"
    assert _aversive(text) == []


def test_empty_text_is_noop() -> None:
    assert _aversive("") == []
    assert _aversive("   ") == []


def test_two_distinct_triggers_is_the_line() -> None:
    one = "他从臭水沟里捡回那只碗。"
    two = "他从臭水沟里捡回那只碗，碗底还沾着虫卵。"
    assert _aversive(one) == []
    assert len(_aversive(two)) == 1


def test_repeating_one_trigger_does_not_cross_the_line() -> None:
    # 判据是「不同词数」，同一个词说三遍仍是一个意象，不该因此毙掉。
    text = "臭水沟。他又回到那条臭水沟。臭水沟的水一年比一年黑。"
    assert _aversive(text) == []


def test_cooking_and_cultivation_verbs_alone_are_not_aversive() -> None:
    # 自闭环 r4 真机误报：案板剖鱼=做菜、青光钻进掌心=金手指觉醒。
    # 身体侵入动词是语境依赖的弱触发，没有强触发（秽物/虫豸/体液）陪同不报。
    text = (
        "搬的当天，他在案板上剖开一条鲤鱼，脊骨里淌出一缕青光。"
        "那光顺着刀口钻进了他掌心。"
    )
    assert _aversive(text) == []


def test_weak_verb_with_strong_noun_still_fires() -> None:
    # 弱触发跟着强触发一起出现时照常计数（真机原病例的形态：蚁后+啃开）。
    text = "蚁后啃开他手腕第一条虫脉那夜，破碗里的瘦蚁开始发抖。"
    assert len(_aversive(text)) == 1


def test_deadline_short_forms_qian_nei_counted() -> None:
    # 自闭环 r4 漏网：「月底前必须交」「一个月内挤走」因只收「之前/之内」而
    # 未触发。单字 前/内 只在期限表达 ±4 字窗口内查，不会全文乱匹配。
    from bestseller.services.blurb_pathology import detect_blurb_pathology as _d

    text = (
        "房租月底前必须交清。隔壁放话一个月内把他挤走。灶台只剩一个时辰的余火。"
    )
    found = [f for f in _d(text) if f.code == "DEADLINE_PILEUP"]
    assert len(found) == 1
    assert found[0].severity == "fatal"
