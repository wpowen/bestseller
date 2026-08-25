"""抽取器两个方向都会失败（2026-08-25 真机）。

它的 docstring 写着「Fails CLOSED on purpose — a wrong name is worse than no
name here, since whatever comes out becomes the snapshot the whole book is
judged against」。同一天的两本真机书证明它**两种失败都会犯**：

  姜燎十九岁，被逐出灶口                    → ''        漏掉真名（fail closed）
  末法乱世，落纸镇上无品级测灵师余白，…       → '末法乱世'  给出错名（fail OPEN）

后者更坏：`creation_protagonist_source` 落成 `original_premise`（权威来源），
快照 / cast_spec / identity_manifest 全被写成一个时代名，整本书会围着
「末法乱世」这个"人"写。而真名「余白」在同一份 metadata 里出现 421 次，
「末法乱世」只有 150 次。

三处根因：

1. **位置是最弱的证据。** 句首那 2–4 个字既可能是主角名，也可能是时代/地点
   状语。而「称谓 + 名字」是强得多的信号，却排在句首规则之后。
2. **称谓靠逐个枚举。** `_NAME_LEADING_TITLES` 里有「医师」「药师」，不可能有
   「测灵师」「温符徒」这些每本书自造的职业。靠往表里加词是打地鼠
   （2026-08-06 定案：词表只许类别级）。
3. **同一判据只挂一条分支。** 句首分支只查前缀，称谓分支查三项，
   「末法乱世」正是从守卫最少的那条溜出去的。

修法：职业后缀**类别**规则锚定优先 → 句首（补齐守卫）→ 称谓表 → 拉丁；
三条分支共用同一个 `_acceptable_protagonist_name`。
"""

from __future__ import annotations

import pytest

from bestseller.services.book_design import (
    _acceptable_protagonist_name,
    _protagonist_name_from_text,
)

pytestmark = pytest.mark.unit


class TestRealBookRegressions:
    def test_an_era_phrase_is_not_the_protagonist(self):
        """★真机：此前返回「末法乱世」并写进快照。"""
        premise = (
            "末法乱世，落纸镇上无品级测灵师余白，"
            "执一支三代单传的判灵笔替学童判灵根定品阶。"
        )
        assert _protagonist_name_from_text(premise) == "余白"

    def test_the_same_name_is_found_in_the_story_spine_shape(self):
        """spine.who 用「·」分隔，句首规则同样够不着真名。"""
        who = "末法乱世·落纸镇上无品级测灵师余白，三代单传判灵笔执笔人。"
        assert _protagonist_name_from_text(who) == "余白"

    def test_a_book_invented_occupation_is_handled_by_category_not_a_word_list(self):
        """2026-08-21 真机的「温符徒温迟」——称谓表里不可能有「温符徒」。"""
        premise = "通灵百家巷里最怂的十九岁温符徒温迟，靠一口百年蒸灵锅守着父亲留下的早市摊。"
        assert _protagonist_name_from_text(premise) == "温迟"


class TestPreExistingFalsePositives:
    """改这个子系统时顺带发现的两条既有误报（不是本次改动引入的）。"""

    def test_a_prepositional_phrase_after_a_state_word_is_not_a_name(self):
        """`_NAME_LEADING_TITLES` 含「重生」，其后是介词短语而非名字。"""
        assert _protagonist_name_from_text("重生为婴儿的他睁开眼。") == ""

    def test_a_time_adverbial_is_not_a_name(self):
        assert _protagonist_name_from_text("三年前，那场大火烧了整条街。") == ""

    def test_a_setting_phrase_opening_is_not_a_name(self):
        assert _protagonist_name_from_text("九幽冥界，少年握剑而立。") == ""


class TestTheGuardDoesNotOverReach:
    """挡掉设定短语不能连真名一起挡——误杀比漏网更贵（它会变成快照）。"""

    def test_a_two_character_name_ending_in_a_place_char_survives(self):
        assert _protagonist_name_from_text("陈山，二十岁的猎户。") == "陈山"

    def test_a_four_character_name_not_ending_in_a_setting_char_survives(self):
        assert _protagonist_name_from_text("欧阳明日，剑宗弃徒。") == "欧阳明日"

    def test_a_numeral_name_without_a_time_unit_survives(self):
        """时间状语判据要求数词**与**时间单位同时在场，所以「白十三」安全。"""
        assert _acceptable_protagonist_name("白十三") is True
        assert _acceptable_protagonist_name("三年前") is False

    def test_an_occupation_word_inside_a_verb_does_not_invent_a_name(self):
        """真机 premise 含「偷师」——职业后缀类不得据此造出人名。"""
        premise = '姜燎十九岁，被青雀酒楼以"偷师厨心方"逐出灶口，在街角支起一口黑锅。'
        assert _protagonist_name_from_text(premise) == ""


class TestOneGuardNotThree:
    def test_every_branch_shares_the_same_guard(self):
        """同一判据挂在一条分支上 = 给自己留一条绕行路（本仓库反复出现的病）。"""
        import inspect

        from bestseller.services import book_design

        src = inspect.getsource(book_design._protagonist_name_from_text)
        assert src.count("_acceptable_protagonist_name(") == 3, (
            "职业锚定 / 句首 / 称谓 三条分支都必须过同一道守卫"
        )

    def test_vacuity_the_old_prefix_only_guard_would_have_passed_the_era_phrase(self):
        """空转检验：还原句首分支的旧守卫，确认它确实放行了「末法乱世」。"""
        from bestseller.services.book_design import _NON_NAME_PREFIXES

        assert not "末法乱世".startswith(_NON_NAME_PREFIXES), (
            "旧守卫只查前缀，「末法乱世」一路畅通——本文件第一条用例正是为它写的"
        )
