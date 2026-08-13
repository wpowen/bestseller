"""简介不得发明已批准构思里没有的硬事实（2026-08-10）。

真机 custom-xianxia-1786282198《废脉炉子天天骂我》。生成 prompt 第⑧条明令
「只许使用【故事脊柱】【故事核】里已有的人物、物品、数字」，模型发明了三样，
而全链没有一处在检查：

    「而他娘，就是被这宗门以枯竭为由丢进那片星云，再没回来」  ← 构思里没有母亲
    「天亮前，器炼堂主事就要撞见这只邪器」                    ← 构思里没有期限

那段简介跑完全部现有尺子：病理检测器零发现、AI 味 0.0。它们测的是别的维度，
没有一把在问「这句话的依据在哪」。用户读到的「逻辑不通」就是这个。

负对照是同期另一本真机书《灵根废我用烂账翻盘》，它的简介是合法接地的——用它
钉住零误报，并钉住 canon 必须包含 hook_card（只用 premise 会误杀它）。
"""

from __future__ import annotations

import json

import pytest

from bestseller.services.blurb_pathology import (
    champion_swaps_protagonist,
    detect_ungrounded_blurb_claims,
)

pytestmark = pytest.mark.unit

# ── 真机正样本：发明了母亲 + 期限 + 死亡 ──────────────────────────────────
BAD_BLURB = (
    "宗门判他器脉枯竭，他却拿破炉子喂神器残片。每一块废铁喂进去，炉子就吐一门失传"
    "口诀、蹦出一只会骂街的器灵——器灵越多炉子越饿，嘶吼着把他往废矿星云更深处拽。"
    "而他娘，就是被这宗门以“枯竭”为由丢进那片星云，再没回来。天亮前，器炼堂主事"
    "就要撞见这只邪器，逐他出门。"
)
BAD_CANON = (
    "沈烬，十七岁，万器废渊最外层一座青铜小宗门的烧炉杂役，被宗门长辈断定器脉枯竭、"
    "这辈子炼不出东西。他那口没人要的破炉子专吃被人丢弃的神器残片，每喂一块就吐一门"
    "不讲理的新口诀，再养出一只会骂人的器灵。器灵越多炉子越饿，饿了就朝废矿星云更深"
    "处嘶吼，逼着他越走越远去捡更破的器。"
)

# ── 真机负对照：接地良好的简介 ───────────────────────────────────────────
GOOD_BLURB = (
    "丹田空了三年，灵米都领不齐，他靠一张烂账本活到今天。"
    "子时井口出水，是沈潮生唯一的命。师弟攥着废丹等他张嘴，记名师姐白芍的账本上还"
    "挂着三斤青果的旧债。桶没到手，三张欠条先压下来：他得用一颗废丹堵师弟的嘴，"
    "用一颗青果封白芍的账，再赶在天亮前把泥封原样糊回去。"
)
GOOD_PREMISE = (
    "沈潮生，剑宗外门没入册的杂役，灵根下品，连下等灵米都领不齐。每三天一次的井口"
    "窗口是他唯一的灵气来源。他靠歪丹哄师弟、用青果封嘴、拿丹渣换命，每一桶水背后"
    "都是一张新欠条。白芍的账本把每一笔烂账都记得清清楚楚。"
)
GOOD_HOOK_CARD = {
    "decision_proof": (
        "眼下必须完成：今夜子时，趁值夜执事换班，把哑井这桶浑汤全部打上来；"
        "赶在天亮前把泥封原样糊回去。"
    ),
    "opening_event": "今夜子时就是这一轮井水出水的时间窗口。",
}
GOOD_CANON = GOOD_PREMISE + "\n" + json.dumps(GOOD_HOOK_CARD, ensure_ascii=False)


def _codes(blurb: str, canon: str) -> set[str]:
    return {f.code for f in detect_ungrounded_blurb_claims(blurb, canon_text=canon)}


# ── 正样本 ───────────────────────────────────────────────────────────────


def test_catches_every_invented_fact_in_the_live_bad_blurb() -> None:
    codes = _codes(BAD_BLURB, BAD_CANON)
    assert codes == {
        "BLURB_UNGROUNDED_KIN",
        "BLURB_UNGROUNDED_DEADLINE",
        "BLURB_UNGROUNDED_DEATH",
    }


def test_findings_are_fatal_so_the_candidate_leaves_the_eligible_set() -> None:
    """fatal → ``BlurbCandidate.has_fatal_pathology`` → 踢出 survivors。"""

    findings = detect_ungrounded_blurb_claims(BAD_BLURB, canon_text=BAD_CANON)
    assert findings and all(f.severity == "fatal" for f in findings)


def test_each_finding_names_the_invented_fact_for_the_rewrite() -> None:
    kin = next(
        f
        for f in detect_ungrounded_blurb_claims(BAD_BLURB, canon_text=BAD_CANON)
        if f.code == "BLURB_UNGROUNDED_KIN"
    )
    assert kin.excerpt == "娘"
    # 一件事只报一次：「再没回来」蕴含「没回来」，不得两条都列。
    death = next(
        f
        for f in detect_ungrounded_blurb_claims(BAD_BLURB, canon_text=BAD_CANON)
        if f.code == "BLURB_UNGROUNDED_DEATH"
    )
    assert death.excerpt == "再没回来"


# ── 负对照与误报 ─────────────────────────────────────────────────────────


def test_a_grounded_blurb_is_clean() -> None:
    assert _codes(GOOD_BLURB, GOOD_CANON) == set()


def test_canon_must_include_the_hook_card_or_it_misfires() -> None:
    """这条钉住 canon 的组成，不是钉实现细节。

    「赶在天亮前」不在 premise 里、在 hook_card.decision_proof 里。用 premise 当
    canon 会把一份完全合法的简介判死——校准时实测到的。
    """

    assert "BLURB_UNGROUNDED_DEADLINE" in _codes(GOOD_BLURB, GOOD_PREMISE)
    assert "BLURB_UNGROUNDED_DEADLINE" not in _codes(GOOD_BLURB, GOOD_CANON)


@pytest.mark.parametrize(
    "text",
    [
        "姑娘抱着娘子往老板娘那儿跑，新娘也跟着走。",
        "他娘的，这破炉子又骂人了。",
        "娘娘吩咐下去，明天开炉。",
    ],
)
def test_kin_false_friends_do_not_fire(text: str) -> None:
    """姑娘/娘子/老板娘/新娘/娘娘/他娘的 都不是「母亲」。"""

    canon = "主角在宗门里烧炉子，每天捡废铁，日子清苦但有奔头。" * 4
    assert _codes(text + canon[:20], canon) == set()


def test_kin_present_in_canon_is_grounded() -> None:
    canon = "沈烬的妹妹病重，他必须弄到药，否则撑不过这个冬天。" * 4
    assert _codes("他要救回妹妹。", canon) == set()


def test_paraphrased_deadline_is_grounded() -> None:
    """构思有期限、简介换个说法写同一条 —— 合法，不得报。"""

    canon = "每三天一次的井口窗口是他唯一的灵气来源，错过就得等下一轮。" * 4
    assert "BLURB_UNGROUNDED_DEADLINE" not in _codes("三天之内他必须拿到那桶水。", canon)


def test_fails_open_when_canon_is_too_short_to_judge() -> None:
    assert detect_ungrounded_blurb_claims(BAD_BLURB, canon_text="") == []
    assert detect_ungrounded_blurb_claims(BAD_BLURB, canon_text="很短") == []
    assert detect_ungrounded_blurb_claims("", canon_text=BAD_CANON) == []


# ── 回归：人名换角检测器在真实 canon 下不误报 ─────────────────────────────


def test_protagonist_swap_check_is_clean_on_real_canon() -> None:
    """「废矿星云更深处」会被姓氏正则切出「云更深」，但 canon 里也有同一短语，
    canon 减法把它消掉——这条钉住那个减法，别为了别的目的改坏它。"""

    assert (
        champion_swaps_protagonist(
            BAD_BLURB, canon_text=BAD_CANON, protagonist_name="沈烬"
        )
        is None
    )
