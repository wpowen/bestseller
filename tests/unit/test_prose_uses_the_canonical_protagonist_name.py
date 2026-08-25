"""正文把主角改名了，而没有任何一道门在看（2026-08-25 真机 custom-xuanhuan-1787662679）。

那本书的身份骨架**完全一致**：premise / logline / 快照 / cast_spec /
identity_manifest / 全部章节大纲——352 处全是「陈韭」，零处「陈韭菜」。
而写手在**前两章**把主角写成了「陈韭菜」（24 / 19 次，占该章全部提及的 100%），
第 3–8 章又用回了规范名。

没有任何一道门发现。grep 全仓：正文 / 写手 / 草稿层的代码**没有一处**引用
``creation_protagonist_name`` 或 ``canonical_protagonist``——「正文写的是不是
那个人」从来没被检查过。（开篇门当时报了 ``weak_immersion`` 具名人物过多，
却没报改名；质量报告里 50 处「陈韭菜」全是在**引用正文**。）

位置最差：改名只发生在开头两章，而开头两章正是读者唯一会看的地方。

判据是**支配度**，不是"出现过更长的串"——第一版按「规范名 + 后 1~2 字」直接算
变体，把「陈韭**把**刀」「陈韭**走**出」也判成了改名。改名时后缀每次都一样，
正常句子里每次都不同；支配度是分开它们的唯一可靠信号。

severity 取 medium：本仓库规矩是新检测器只挣重生和留痕，不发杀权
（high 会经 attention → requires_human_review 把书钉死）。
"""

from __future__ import annotations

import pytest

from bestseller.services.consistency import (
    canonical_protagonist_name,
    dominant_name_extension,
)

pytestmark = pytest.mark.unit


class TestTheRealBook:
    def test_a_renamed_chapter_is_detected(self):
        """★真机 ch1：24 次「陈韭菜」，0 次单独用「陈韭」。"""
        text = (
            "陈韭菜在外门炒了三年菜。陈韭菜的肚子咕噜响了一声。"
            "陈韭菜没去端锅。陈韭菜先拿抹布擦桌。"
        )
        variant, hits, share = dominant_name_extension(text, "陈韭")
        assert variant == "陈韭菜"
        assert hits == 4
        assert share == 1.0

    def test_a_clean_chapter_is_not_flagged(self):
        """★真机 ch6/8：用回了规范名。"""
        text = "陈韭把锅端上桌。陈韭没说话。陈韭抬头看了一眼横山。"
        assert dominant_name_extension(text, "陈韭")[0] == ""


class TestDoesNotFireOnOrdinaryProse:
    """误杀比漏网贵：这条会驱动重写，误报等于让好章节白重写一遍。"""

    def test_the_canonical_name_followed_by_different_verbs(self):
        text = "陈韭把刀放下。陈韭走出门。陈韭说了一句话。"
        assert dominant_name_extension(text, "陈韭")[0] == ""

    def test_a_chapter_that_only_uses_pronouns(self):
        """真机 ch4/5 通篇不提名字——合法文风，不该被判违规。"""
        assert dominant_name_extension("他把锅端起来，没说话。", "陈韭")[0] == ""

    def test_an_occasional_nickname_alongside_the_canonical_name(self):
        text = "陈韭菜是他小名。陈韭把锅端起。陈韭没说话。"
        assert dominant_name_extension(text, "陈韭")[0] == ""

    def test_the_name_followed_only_by_punctuation(self):
        assert dominant_name_extension("陈韭。陈韭！陈韭？", "陈韭")[0] == ""

    def test_too_few_mentions_to_conclude(self):
        """两次同样的后缀不足以判定改名——最低命中数守住小样本。"""
        assert dominant_name_extension("陈韭菜来了。陈韭菜走了。", "陈韭")[0] == ""

    def test_empty_inputs(self):
        assert dominant_name_extension("", "陈韭")[0] == ""
        assert dominant_name_extension("陈韭菜陈韭菜陈韭菜", "")[0] == ""


class TestCanonicalNameResolution:
    def test_explicit_creation_name_wins(self):
        assert canonical_protagonist_name({"creation_protagonist_name": "陈韭"}) == "陈韭"

    def test_falls_back_through_the_identity_spine(self):
        assert canonical_protagonist_name(
            {"book_design_snapshot": {"protagonist": {"name": "余白"}}}
        ) == "余白"
        assert canonical_protagonist_name(
            {"cast_spec": {"protagonist": {"name": "姬衡"}}}
        ) == "姬衡"

    def test_no_name_is_not_an_error(self):
        """拿不到规范名时这条检查必须**静默跳过**，不得凭空造违规。"""
        assert canonical_protagonist_name({}) == ""
        assert canonical_protagonist_name(None) == ""


class TestSeverityIsNotAKillSwitch:
    def test_the_finding_is_medium_not_high(self):
        """high 会经 attention → requires_human_review 把书钉死。
        新检测器只挣重生和留痕——2026-08-23 那条「从不通过的门等于永远卡住」
        的教训就写在 project_verdict_from_findings 的 docstring 里。"""
        import inspect

        from bestseller.services import consistency

        src = inspect.getsource(consistency._check_protagonist_name_in_prose)
        assert 'severity="medium"' in src
        assert 'severity="high"' not in src

    def test_vacuity_the_naive_substring_rule_would_have_false_positived(self):
        """空转检验：还原第一版「规范名+后1字即变体」，确认它确实误报。"""
        text = "陈韭把刀放下。陈韭走出门。陈韭说了一句话。"
        naive = {text[i + 2 : i + 3] for i in range(len(text)) if text.startswith("陈韭", i)}
        assert len(naive) == 3, "三个不同后缀——朴素规则会把它们全当成变体"
        assert dominant_name_extension(text, "陈韭")[0] == "", "支配度判据不上当"
