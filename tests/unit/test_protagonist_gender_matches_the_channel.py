"""男频选项出了女主角（2026-08-26 真机 custom-xuanhuan-1787749718）。

用户勾的是**男频**，频道钢印也确实盖住了结构化字段：

    channel_key / audience_orientation = "male"
    cast_spec.gender                   = "male"
    identity_manifest                  = ('阿茸', 'male', '他')

而构思正文通篇是「她」——premise 11 次、synopsis 13 次，「他」**0 次**。
**结构化说男、散文写女**，而此前的身份门只比对**名字**，性别一路绿灯。

这是同一个病的第三种形态：

  1. 主角改名        规范名 陈韭  → 正文 陈韭菜
  2. 一本书两个主角   story_spine 顾澜 → premise 纪潮
  3. 本例            声明 male    → 正文 她

三次都是「结构化说一套、散文写另一套，中间没有比对」。用户看到的
「我选的男频，怎么主角变成女的了」就是这条。

判据与名字检查同源：声明性别的代词在第三人称总数里占不到半数即判不一致；
提及数太少时不下结论（一句话里一两个代词，比例没有意义）；没有声明性别则
静默跳过，不凭空造违规。
"""

from __future__ import annotations

import pytest

from bestseller.services.book_design import (
    _check_protagonist_pronoun,
    declared_protagonist_gender,
    protagonist_pronoun_coverage,
)

pytestmark = pytest.mark.unit


def _meta(gender: str, premise: str, synopsis: str = "", logline: str = "") -> dict:
    return {
        "identity_manifest": [
            {"name": "阿茸", "role": "protagonist", "gender": gender}
        ],
        "premise": premise,
        "synopsis": synopsis,
        "logline": logline,
    }


class TestTheRealBook:
    def test_declared_male_with_all_female_prose_is_detected(self):
        """★真机形状。"""
        meta = _meta(
            "male",
            "她没跑，把崽兜进皮兜抱回寨。她每喂大一寸崽，能站住的林子就多一块。她把干粮塞进兜里。",
            "她抱回了王。她顺手把上辈子那点贫嘴带了过来。",
            "她被派去送死那夜。",
        )
        ok, gender, hits, total = _check_protagonist_pronoun(meta)
        assert ok is False
        assert gender == "male"
        assert hits == 0 and total >= 6

    def test_vacuity_the_name_check_alone_would_have_passed_this_book(self):
        """空转检验：名字一致、性别不一致——只比对名字的门抓不到。"""
        from bestseller.services.book_design import _canonical_name_coverage

        meta = _meta(
            "male",
            "阿茸没跑。她把崽抱回寨。她每喂大一寸。她笑了。她赢了。她走了。她回头看了一眼。",
        )
        meta["creation_protagonist_name"] = "阿茸"
        present, populated = _canonical_name_coverage(meta, "阿茸")
        assert present * 2 >= populated, "名字检查会放行"
        assert _check_protagonist_pronoun(meta)[0] is False, "性别检查必须抓到"


class TestConsistentBooksPass:
    def test_male_declaration_with_male_prose(self):
        meta = _meta("male", "他没跑，他把刀捡起来。他每赢一次，寨子就多认他一分。", "他抱回了王。他没说话。", "他被派去送死。")
        assert _check_protagonist_pronoun(meta)[0] is True

    def test_female_declaration_with_female_prose(self):
        meta = _meta("female", "她没跑。她把崽抱回。她每喂大一寸。", "她抱回了王。她笑了。", "她被派去。")
        assert _check_protagonist_pronoun(meta)[0] is True

    def test_a_female_supporting_character_does_not_trip_it(self):
        """有女配很正常——只要声明性别的代词仍占多数就不判。"""
        meta = _meta("male", "他救了她三次。他把她背出火场。他替她挡了一刀。他没回头。", "他赢了。", "他出手。")
        ok, _g, hits, total = _check_protagonist_pronoun(meta)
        assert ok is True
        assert hits * 2 > total


class TestFailsQuietlyWhenItCannotTell:
    def test_too_few_mentions_is_not_a_verdict(self):
        assert _check_protagonist_pronoun(_meta("male", "他在雨里站着。"))[0] is True

    def test_no_declared_gender_is_skipped(self):
        meta = {"cast_spec": {"protagonist": {"name": "x"}}, "premise": "她走了。"}
        assert declared_protagonist_gender(meta) == ""
        assert _check_protagonist_pronoun(meta)[0] is True

    def test_empty_prose_is_skipped(self):
        assert protagonist_pronoun_coverage({"premise": ""}, "male") == (0, 0)

    def test_cast_spec_is_the_fallback_source(self):
        meta = {"cast_spec": {"protagonist": {"gender": "female"}}}
        assert declared_protagonist_gender(meta) == "female"


class TestTheIssueIsEmitted:
    def test_the_gate_emits_a_distinct_code(self):
        import inspect

        from bestseller.services import book_design

        src = inspect.getsource(book_design)
        assert '"protagonist_gender_mismatch"' in src
        assert "_check_protagonist_pronoun(" in src
