"""冠军简介不得换掉主角（正典人名一致性）。

真机 2026-08-06（custom-xuanhuan-1786023406）：T6 文案工序的冠军简介直接覆盖
``synopsis``，覆盖前只过「禁用母题词消毒」和「句界截断」两道。冠军把正典主角
「纪蛰」换成了凭空的「沈落」，而 ``creation_protagonist_name`` /
``protagonist.name`` / ``premise`` / ``dramatic_question`` 全都还是纪蛰——**唯一
对外见光的那份文案，主角名是错的**，且没有任何一道校验发现。

判据要求两个条件同时成立，把误报压到最低：
  (1) 正典主角名在简介里没出现，且
  (2) 简介里出现了正典文本也没有的人名
只满足 (1) 是合法的无名/第一人称写法；只满足 (2) 是正常引入配角。
"""

from __future__ import annotations

import pytest

from bestseller.services.blurb_pathology import champion_swaps_protagonist


# 真机原文（截断到判据相关部分）。
_PREMISE = (
    "万蛊宗外门杂役纪蛰被人嘲笑连虫子都不如，偷偷把同门倒掉的废虫卵揣进破陶碗"
    "喂那只没人要的瘦蚁。蚁后啃开他手腕第一条虫脉那夜，师兄养的金蚕蛊突然绕着"
    "他的破碗打转。"
)
_CHAMPION_BAD = (
    "沈落是万蛊宗外门杂役，喂虫少年，旁人连废虫卵都不让他碰。师兄孙坤的金蚕蛊"
    "嫌他脏，绕道三尺；执事钱荃年年卡他进度。"
)


def test_real_regression_champion_swapped_protagonist() -> None:
    rogue = champion_swaps_protagonist(
        _CHAMPION_BAD, canon_text=_PREMISE, protagonist_name="纪蛰"
    )
    assert rogue, "换掉主角的冠军必须被判违规"


def test_canon_protagonist_present_allows_new_side_cast() -> None:
    # 主角名在场 → 简介引入配角是正常文案行为，不该拦。
    champion = (
        "纪蛰是万蛊宗外门杂役，喂虫少年。师兄孙坤的金蚕蛊嫌他脏，绕道三尺；"
        "执事钱荃年年卡他进度。"
    )
    assert (
        champion_swaps_protagonist(
            champion, canon_text=_PREMISE, protagonist_name="纪蛰"
        )
        is None
    )


def test_nameless_third_person_blurb_allowed() -> None:
    # 不点名主角是合法写法（第一/第三人称简介），只要没有换人就放行。
    champion = (
        "他是万蛊宗外门杂役，旁人连废虫卵都不让他碰。今夜，破碗里那只瘦蚁"
        "胀成了拳头大的黑甲虫王。"
    )
    assert (
        champion_swaps_protagonist(
            champion, canon_text=_PREMISE, protagonist_name="纪蛰"
        )
        is None
    )


def test_canon_text_itself_never_violates() -> None:
    # no-op 契约：正典喂回自己必须放行，否则判据本身就是坏的。
    assert (
        champion_swaps_protagonist(
            _PREMISE, canon_text=_PREMISE, protagonist_name="纪蛰"
        )
        is None
    )


def test_missing_protagonist_name_fails_open() -> None:
    # 正典自己就没有主角名时无从判断——宁可漏过，不可误伤。
    assert (
        champion_swaps_protagonist(
            _CHAMPION_BAD, canon_text=_PREMISE, protagonist_name=""
        )
        is None
    )


def test_empty_champion_fails_open() -> None:
    assert (
        champion_swaps_protagonist("", canon_text=_PREMISE, protagonist_name="纪蛰")
        is None
    )


def test_result_records_rejection_for_audit() -> None:
    # 拒绝必须留痕：静默回退 v0 会让这个缺陷再次隐形。
    from bestseller.services.blurb_copywriter import BlurbCopywritingResult

    r = BlurbCopywritingResult(champion="x", champion_strategy="scene_hook")
    assert r.canon_name_rejected is False
    r.canon_name_rejected = True
    r.canon_name_rogue = ["沈落"]
    d = r.to_dict()
    assert d["canon_name_rejected"] is True
    assert d["canon_name_rogue"] == ["沈落"]


# ── 抽取噪声护栏（2026-08-10 真机） ──────────────────────────────────────
#
# 《搓背》建书当场死：简介里的「师傅和这位道士」被姓氏正则切出人名「傅和这」，
# 本函数据此判定「冠军换掉了主角」，**整份文案冠军被丢弃、回退到只有一句话的
# v0**，读者画像 0/3 会点，logline 门随即拒绝，书没建成。
#
# 人名抽取器是 top-100 姓氏正则扫正文，output_validator 自己的注释就写明它误报率
# 高、所以在章节层只发 warn。本函数却是破坏性的，必须自带护栏。


_CANON = "烬镇唯一一家破澡堂的搓背工汤圆，十七八岁，专给来客搓背谋生。" * 3


def test_master_craftsman_compound_is_not_a_name() -> None:
    """真机原句：『师傅』的『傅』不是姓。搓背/工匠题材里这个词必然高频。"""

    assert (
        champion_swaps_protagonist(
            "汤家老号的师傅和这位游方道士一掌定生死，搓出藏宝图第一笔。" + _CANON[:40],
            canon_text=_CANON,
            protagonist_name="汤圆",
        )
        is None
    )


@pytest.mark.parametrize(
    "text",
    ["老师傅和这块搓背巾", "太傅和这道旨意", "师父和这门手艺", "掌柜和这本账"],
)
def test_other_title_compounds_are_not_names(text: str) -> None:
    assert (
        champion_swaps_protagonist(
            text + _CANON[:40], canon_text=_CANON, protagonist_name="汤圆"
        )
        is None
    )


def test_a_real_swap_still_gets_caught() -> None:
    """真被换掉的主角是简介的主语，通篇复现。"""

    rogue = champion_swaps_protagonist(
        "少年傅青云走进澡堂。傅青云不知道，这一搓要了他的命。",
        canon_text=_CANON,
        protagonist_name="汤圆",
    )
    assert rogue == ("傅青云",)


def test_no_frequency_floor_here() -> None:
    """别再给这里加频次下限。

    第一版护栏加了「至少出现 2 次」（理由：真主角是主语、会通篇复现）。听着合理，
    但上面那条真实回归用例里冒名主角只出现 1 次，频次门直接让本函数失效——是既有
    测试当场拦下了这次改坏。

    ⚠️ 顺带记录一个既有弱点：那条回归用例里，冒名主角「沈落」其实**从未被抽取
    出来**（姓氏正则给出的是 孙坤 / 钱荃年 这些配角与 artifact），它能通过纯属
    连坐。也就是说本函数至今没有真正检出过它设立时要防的那个改名。要根治得换掉
    人名抽取方式，不是调这里的阈值。
    """

    rogue = champion_swaps_protagonist(
        _CHAMPION_BAD, canon_text=_PREMISE, protagonist_name="纪蛰"
    )
    assert rogue, "冠军换主角的场景必须仍被判违规"
    assert "沈落" not in rogue, "如果哪天真抽出来了，请把上面的说明一并更新"
