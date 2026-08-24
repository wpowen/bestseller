"""Copy-layer AI flavour, measured on real output.

Every fixture below is verbatim from custom-xuanhuan-1785980083. The prose
detector scores all of them 0.0 — good and bad alike — which is why this
detector exists.
"""

from __future__ import annotations

import pytest

from bestseller.services.copy_flavor import detect_copy_flavor

pytestmark = pytest.mark.unit


# Real logline. Concrete, situated, hooks on a person — must stay clean.
_GOOD_LOGLINE = (
    "十三岁少年被灵根碑判了死路，却瞄上一个刚欺负过他的杂役师兄——"
    "只要打赢，对方练了多年的功法就归他。"
)

# Real reader_promise. Talks to an editor, not a reader.
_BAD_PROMISE = (
    "看他一个连外门都进不去的杂役，靠一场场正面对拳把一座山的传承一层层撸进自己身体——"
    "每章有人倒下、每章有新本事当场就用，下一个被点名的是谁永远吊着你的追读。"
)

# Real selling points.
_BAD_SELLING_POINTS = [
    "赢了拿功夫、功夫当场用：极简代价掠夺式成长，每三到五章一次正面撂倒换一门功法，爽点兑现零延迟",
    "对手池同步升档：功法池与威胁池永远咬合升级",
    "极简代价、不掉链子：赢了拿功法，越用越强，绝不反向惩罚主角",
]


class TestCatchesTheRealDefects:
    def test_reader_promise_is_flagged(self) -> None:
        report = detect_copy_flavor(_BAD_PROMISE)

        assert not report.clean
        categories = {s.category for s in report.spans}
        assert "meta_cadence" in categories, "「每章…」是规划语言"
        assert "trade_jargon" in categories, "「追读」是平台黑话"

    @pytest.mark.parametrize("point", _BAD_SELLING_POINTS)
    def test_each_selling_point_is_flagged(self, point: str) -> None:
        assert not detect_copy_flavor(point).clean

    def test_framework_directive_leak_is_caught(self) -> None:
        """The worst family: an internal instruction reaching the shop window.

        「极简代价」「绝不反向惩罚主角」 are phrasings from the cost_style
        directive itself. Making the setting land must not put the setting's
        own wording on the cover.
        """

        report = detect_copy_flavor(_BAD_SELLING_POINTS[2])

        assert any(s.category == "framework_leak" for s in report.spans)

    def test_every_verdict_quotes_its_evidence(self) -> None:
        for span in detect_copy_flavor(_BAD_PROMISE).spans:
            assert span.matched, "无证据的判定无法核实"
            assert span.why


class TestDirectiveVoice:
    """The family the word-lists cannot see.

    Every fixture is a real ``reader_promise`` from ``writing_presets`` that was
    reaching the listing subtitle. The first one carries no jargon at all — it
    scored 0.0 before this family existed — and is still obviously not copy.
    """

    @pytest.mark.parametrize(
        "directive",
        [
            "主角成长路径、体系升级、势力扩张和更大世界必须持续兑现。",
            "开篇快速亮出主角差异化优势、当前利益、即时危险和连载钩子，持续维持强追读。",
            "第一页给压力和问题，前三章给主角优势与小爽点，前一万字证明可持续追读循环。",
            "每章都要有比赛结果、技术反差、直播反馈或职业推进，不允许无效日常堆积。",
            "每章必须推进线索、扩大不安或揭开一层旧案关系，不能只靠氛围空转。",
        ],
    )
    def test_generator_orders_are_flagged(self, directive: str) -> None:
        assert any(
            span.category == "directive_voice"
            for span in detect_copy_flavor(directive).spans
        )

    @pytest.mark.parametrize(
        "narrative",
        [
            "他必须在三天内赶到雁门关，否则母亲活不过这个冬天。",
            "她证明了自己不是废物，代价是再也回不去。",
            "全宗门都以为他废了，只有他自己知道，那道断掉的经脉里藏着什么。",
        ],
    )
    def test_a_modal_inside_a_story_is_not_a_directive(self, narrative: str) -> None:
        """「必须」「证明」 belong to characters too.

        The discriminator is the addressee, not the verb: a directive commands
        the manuscript's own units (开篇/前三章/章末), a story commands a person.
        """

        assert detect_copy_flavor(narrative).clean


class TestDoesNotPunishGoodCopy:
    def test_the_real_logline_stays_clean(self) -> None:
        assert detect_copy_flavor(_GOOD_LOGLINE).clean

    @pytest.mark.parametrize(
        "copy",
        [
            "他一拳打碎了族老的丹田，转身走出祠堂，身后没有一个人敢拦。",
            "全宗门都以为他废了，只有他自己知道，那道断掉的经脉里藏着什么。",
            "从今天起，谁的功法好，谁就得小心自己还能不能留住它。",
        ],
    )
    def test_punchy_genre_copy_is_not_a_defect(self, copy: str) -> None:
        """Copy is allowed to be loud and trashy — that is the genre register."""

        assert detect_copy_flavor(copy).clean

    def test_non_chapter_ordinals_are_allowed(self) -> None:
        """本条原本断言「他在第三章第一次动手。」应当干净，理由是「第三章不是
        cadence 那种毛病」。**cadence 的判断仍然成立**——它确实不是「每N章」。

        但 2026-08-24 用户报「上架资料 AI 味重」，取证发现真机上架简介的结尾
        就是「**第50章**，账本只剩最后几页，他要替真传第一人沈惊鹊还清命里
        最后一笔债」，synopsis 里还有「上一章替他挡刀的人，下一章就得哭着求他
        还人情」——两句都打 0.0 分。

        `detect_copy_flavor` **只有一个消费方**，只跑对外文案
        （reader_promise / selling_points，2026-08-24 起也用于简介核查）。
        在对外文案里，「他在第三章第一次动手」和「第50章账本只剩几页」是同一件
        事：写手在对读者讲稿件的结构。读者买书时还没有章的概念。

        所以这条改为断言**真正该放行的东西**：不带章/卷的序数照旧干净。
        原意图（别把普通序数当 cadence 误杀）完整保留。
        """

        for text in (
            "他在第三层第一次动手。",
            "还完第九笔，账本就合拢了。",
            "第五个债主来的那天下了雨。",
        ):
            assert detect_copy_flavor(text).clean, text

    def test_chapter_references_in_reader_copy_are_flagged(self) -> None:
        """对外文案里报章号 = 生产口吻（2026-08-24 真机上架简介取证）。"""

        assert not detect_copy_flavor("他在第三章第一次动手。").clean
        assert not detect_copy_flavor("第50章，账本只剩最后几页。").clean


class TestScoring:
    def test_worse_copy_scores_higher(self) -> None:
        assert detect_copy_flavor(_BAD_PROMISE).score > detect_copy_flavor(
            _GOOD_LOGLINE
        ).score

    def test_framework_leak_outweighs_a_single_jargon_hit(self) -> None:
        leak = detect_copy_flavor("极简代价掠夺式成长")
        jargon = detect_copy_flavor("这本书的爽点很足")
        assert leak.score > jargon.score

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_input_is_clean(self, value: str | None) -> None:
        assert detect_copy_flavor(value).clean
        assert detect_copy_flavor(value).score == 0.0


class TestGenerationSideFixes:
    """The prompts that produced the flagged copy.

    Two root causes, both visible in the schema the model was handed:
    1. ``selling_points`` was described only as 「卖点1, 卖点2, 卖点3」 — a field
       name with no register, so the model filled the vacuum with whatever it
       had just read, which was the framework's own cost directive.
    2. ``reader_promise`` was literally described as 「一句话追读承诺」 — the
       schema itself used the trade jargon the copy then echoed.
    """

    def test_selling_points_schema_states_the_register(self) -> None:
        from bestseller.services import conception

        import inspect

        source = inspect.getsource(conception._commercial_positioning_user_prompt)
        assert "像跟朋友安利一本书" in source

    def test_reader_promise_schema_bans_trade_jargon(self) -> None:
        from bestseller.services import conception

        import inspect

        source = inspect.getsource(conception._commercial_positioning_user_prompt)
        assert "追读" in source and "不用行业词" in source, (
            "schema 必须显式禁用行业词——它自己写「一句话追读承诺」时，"
            "模型只是照做"
        )

    def test_cost_directive_is_scoped_to_design_not_copy(self) -> None:
        """The leak this caused: 「极简代价、不掉链子」 shipped as a selling point."""

        from bestseller.services.conception import _cost_style_block_for_ctx

        block = _cost_style_block_for_ctx(
            {"genre_intent_contract": {"explicit_enhancers": {"cost_style": "minimal"}}},
            is_en=False,
        )
        assert "不是文案素材" in block
        assert "禁止把它的措辞复述" in block

    def test_scope_line_absent_for_standard_books(self) -> None:
        from bestseller.services.conception import _cost_style_block_for_ctx

        assert (
            _cost_style_block_for_ctx(
                {"genre_intent_contract": {"explicit_enhancers": {"cost_style": "standard"}}},
                is_en=False,
            )
            == ""
        )

    def test_scope_line_names_no_motif_vocabulary(self) -> None:
        from bestseller.services.conception import _cost_style_block_for_ctx

        block = _cost_style_block_for_ctx(
            {"genre_intent_contract": {"explicit_enhancers": {"cost_style": "minimal"}}},
            is_en=False,
        )
        for token in ("债", "账", "欠", "寿", "记忆"):
            assert token not in block
