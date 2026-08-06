"""纯爽 (cost_style) must be an ACTIONABLE directive at conception time.

Field failure (2026-07-24, custom-xuanhuan-1784899694): the user ticked 纯爽
on the create form. The value survived the whole plumbing chain — payload →
story_enhancers → genre_intent_contract.explicit_enhancers →
ctx["genre_intent_contract"] — and even appeared inside conception prompts…
as the opaque JSON token ``"cost_style": "minimal"`` in the 【建书页明确选择】
blob, with no explanation of what "minimal" means. The only code that
TRANSLATES the value into instructions (`ideology_kernel` cost directives)
runs at PLANNING time — which the book never reached, because conception
invented a 随机系统收税 mechanic anyway and the logline gate hard-killed it on
cost_integrity (硬伤 1.0). Net effect: the user opted out of forced costs, the
generator stuffed one in, the gate rejected the book for exactly that cost,
and the user reported "选了纯爽但没有那种".

Contract pinned here: wherever the user's cost_style reaches a generation
prompt, it must arrive as the translated directive, not a bare enum token —
in the conception intent block AND in the concept tournament's builders.
``standard`` stays byte-identical (the no-op contract).
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services.ideology_kernel import cost_style_directive


pytestmark = pytest.mark.unit


_MINIMAL_CONTRACT = {
    "genre_label": "玄幻",
    "sub_genre_label": "玄幻",
    "channel_key": "male",
    "explicit_enhancers": {"cost_style": "minimal"},
}


class TestDirectiveExport:
    def test_minimal_directive_is_actionable_chinese(self) -> None:
        text = cost_style_directive("minimal", is_en=False)

        assert "极简" in text
        # 2026-08-02: minimal is a pacing preference, not a cost allowlist.
        assert "爽点前置" in text
        assert "长期失能" in text
        assert "白名单" not in text

    def test_minimal_actually_constrains_cost_not_only_pacing(self) -> None:
        """The switch's whole promise is "金手指是否向主角本人收费".

        2026-08-05: the directive had been reduced to a pure pacing preference
        and ended by handing the decision back to the model. The book created
        under it invented a 破碗 cost ledger as its CORE mechanic and wrote
        "本作主角必须有代价" into its own writing profile.

        The assertion is on direction, not on wording: an allowlist of permitted
        cost categories is what flattened a cultivation world into a logistics
        operation, so this must never re-introduce one.
        """

        text = cost_style_directive("minimal", is_en=False)

        assert "不会削弱主角" in text, "using the advantage must not diminish the hero"
        assert "越难用" in text, "escalating price for reuse must be ruled out"
        assert "不是卖点" in text or "引擎" in text, "cost must not become the book's engine"


class TestDirectiveNamesNoMotifVocabulary:
    """A prohibition that spells out its own motif plants that motif.

    2026-08-03 《雾街债主》 came out of two prohibitions that named the very
    words they banned. My first attempt at repairing the minimal directive
    (2026-08-06) reintroduced exactly that defect — it wrote 「债」 and 「账本」
    into a prompt that reaches every concept generation — and was caught by
    ``test_prompt_pollution_sweep``. This keeps the lesson attached to the
    directive itself, so the next rewrite trips here first.
    """

    _BANNED = ("债", "账", "欠", "寿", "失忆", "记忆", "亲情", "尸", "遗体", "殡仪")

    @pytest.mark.parametrize("cost_style", ["minimal", "external", "standard"])
    def test_no_cost_directive_names_a_motif_word(self, cost_style: str) -> None:
        text = cost_style_directive(cost_style, is_en=False)
        for token in self._BANNED:
            assert token not in text, f"代价指令又点名母题词：{token}"

    def test_planner_rules_name_no_motif_word(self) -> None:
        from bestseller.services.planner import _source_bound_cost_rules

        class _FakeProject:
            metadata_json = {"story_enhancers": {"cost_style": "minimal"}}

        blob = " ".join(_source_bound_cost_rules(_FakeProject(), is_en=False))
        for token in self._BANNED:
            assert token not in blob, f"规划期代价规则又点名母题词：{token}"

    def test_minimal_does_not_delegate_the_decision_back_to_the_model(self) -> None:
        """The exact escape hatch that made the switch a no-op.

        Any phrasing that defers "whether this book has costs" to the book's own
        design re-opens the hole, however it is worded.
        """

        text = cost_style_directive("minimal", is_en=False)
        english = cost_style_directive("minimal", is_en=True)

        assert "由这本书自己的设定决定" not in text
        assert "自己决定" not in text
        assert "this book's own design" not in english

    def test_minimal_leaves_the_world_free_to_be_dangerous(self) -> None:
        """Constraining the hero's bill must not sterilise the setting.

        Without this, "no cost" collapses into "no stakes" and the genre loses
        the hard rules it needs.
        """

        text = cost_style_directive("minimal", is_en=False)
        assert "世界" in text and ("危险" in text or "硬规则" in text)

    def test_external_directive_exists(self) -> None:
        assert "外置" in cost_style_directive("external", is_en=False)

    def test_standard_is_empty_noop(self) -> None:
        assert cost_style_directive("standard", is_en=False) == ""
        assert cost_style_directive("", is_en=False) == ""


class TestConceptionIntentBlock:
    def test_minimal_renders_the_translated_directive(self) -> None:
        from bestseller.services.conception import _creation_intent_prompt_block

        block = _creation_intent_prompt_block(
            {"genre_intent_contract": _MINIMAL_CONTRACT, "language": "zh-CN"}
        )

        assert '"cost_style": "minimal"' in block or '"minimal"' in block
        assert "代价风格=极简" in block, (
            "the enum token alone teaches the model nothing — the block must "
            "carry the translated directive"
        )

    def test_standard_block_carries_no_cost_directive(self) -> None:
        from bestseller.services.conception import _creation_intent_prompt_block

        contract = {
            "genre_label": "玄幻",
            "sub_genre_label": "玄幻",
            "channel_key": "male",
            "explicit_enhancers": {"cost_style": "standard", "brainhole": True},
        }
        block = _creation_intent_prompt_block(
            {"genre_intent_contract": contract, "language": "zh-CN"}
        )

        assert "代价风格=" not in block

    def test_empty_selection_still_renders_nothing(self) -> None:
        """The no-selection contract must survive: default form → no block."""

        from bestseller.services.conception import _creation_intent_prompt_block

        contract = {
            "genre_label": "玄幻",
            "sub_genre_label": "玄幻",
            "explicit_enhancers": {"cost_style": "standard"},
        }
        block = _creation_intent_prompt_block(
            {"genre_intent_contract": contract, "language": "zh-CN"}
        )

        assert block == ""


class TestTournamentBuilders:
    def test_kernel_prompt_carries_minimal_directive(self) -> None:
        from bestseller.services.concept_tournament import (
            _build_engine_kernel_messages,
        )

        system, user = _build_engine_kernel_messages(
            genre="玄幻",
            sub_genre="玄幻",
            lane="资源分配",
            chapter_count=50,
            cost_style="minimal",
        )

        assert "极简" in system + user

    def test_repair_prompt_keeps_the_directive(self) -> None:
        """Same regression shape as the audience anchor: repair must not
        strip user constraints from the rebuilt prompt."""

        from bestseller.services.concept_tournament import (
            _build_engine_kernel_repair_messages,
        )

        system, user = _build_engine_kernel_repair_messages(
            genre="玄幻",
            sub_genre="玄幻",
            lane="资源分配",
            chapter_count=50,
            seed_concept="x",
            card={},
            missing_fields=["mechanism"],
            cost_style="minimal",
        )

        assert "极简" in system + user

    def test_kernel_standard_prompt_is_unchanged(self) -> None:
        from bestseller.services.concept_tournament import (
            _build_engine_kernel_messages,
        )

        with_default = _build_engine_kernel_messages(
            genre="玄幻", sub_genre="玄幻", lane="资源分配", chapter_count=50
        )
        with_standard = _build_engine_kernel_messages(
            genre="玄幻",
            sub_genre="玄幻",
            lane="资源分配",
            chapter_count=50,
            cost_style="standard",
        )

        assert with_default == with_standard

    def test_candidate_call_site_injects_the_directive(self) -> None:
        """The candidate stage has three interchangeable builders behind
        ``_candidate_message_builder``; the injection sits at the single call
        site so every mode gets it."""

        from bestseller.services import concept_tournament

        source = inspect.getsource(concept_tournament.run_concept_tournament)
        idx = source.index("build_candidate_messages(")
        region = source[idx : idx + 900]

        assert "cost_style" in region, (
            "candidate generation must see the cost-style directive too"
        )


class TestConceptionForwardsToTournament:
    def test_call_site_reads_contract_and_forwards(self) -> None:
        from bestseller.services import conception as conception_services

        source = inspect.getsource(conception_services.run_conception_pipeline)
        idx = source.index("run_concept_tournament(")
        region = source[idx : idx + 1200]

        assert "cost_style=" in region, (
            "conception must forward the user's cost_style into the "
            "tournament — planning-time translation is too late for a book "
            "that dies at the conception gates"
        )


class TestPlannerAndKernelStayInStep:
    """The same switch is translated in two places; they must not drift.

    ``ideology_kernel._cost_style_directive`` feeds conception and the ideology
    prompt; ``planner._source_bound_cost_rules`` feeds source-bound planning.
    On 2026-08-02 both were relaxed together, and on 2026-08-05 both had to be
    repaired together — the second one was only found because the first was.
    A drift here means the book is planned under a different cost contract than
    it was conceived under.
    """

    @staticmethod
    def _planner_rules(cost_style: str, *, is_en: bool = False) -> list[str]:
        from bestseller.services.planner import _source_bound_cost_rules

        class _FakeProject:
            metadata_json = {"story_enhancers": {"cost_style": cost_style}}

        return _source_bound_cost_rules(_FakeProject(), is_en=is_en)

    def test_planner_minimal_rules_forbid_billing_the_hero(self) -> None:
        rules = " ".join(self._planner_rules("minimal"))

        assert "不会削弱主角" in rules
        assert "不是卖点" in rules or "引擎" in rules
        # The pacing half of the contract survives alongside the constraint.
        assert "爽点前置" in rules

    def test_planner_minimal_does_not_delegate_the_decision(self) -> None:
        rules = " ".join(self._planner_rules("minimal"))
        assert "自己决定" not in rules
        assert "由这本书自己的设定决定" not in rules

    def test_planner_minimal_enumerates_no_allowlist(self) -> None:
        """The 2026-08-02 lesson: never re-introduce permitted-category menus."""

        rules = " ".join(self._planner_rules("minimal"))
        for banned in ("只能是", "仅限", "白名单", "只允许"):
            assert banned not in rules

    def test_external_still_shields_the_protagonist(self) -> None:
        rules = " ".join(self._planner_rules("external"))
        assert "世界" in rules or "对手" in rules

    def test_standard_branch_is_unchanged(self) -> None:
        """standard is the untouched default — a regression here hits every book."""

        rules = self._planner_rules("standard")
        assert rules == ["出现代价时，它应当由当下使用核心机制产生，且影响在之后仍然可见。"]


class TestGateDoesNotPunishTheChosenStyle:
    """Fixing the generator without teaching the gates just inverts the bug.

    This repo has already lived the forward version: a book was ordered to write
    a cost and then hard-killed by a gate for having one. With the generator now
    correctly withholding costs for 爽文无代价, ``emotion_cost_free_win`` would
    have fired on every such book — the same self-harm loop, mirrored.
    """

    @staticmethod
    def _kernel_with_cost_free_win() -> dict[str, object]:
        return {
            "emotion_chain": [
                {
                    "chapter_range": "1-3",
                    "target_emotion": "爽",
                    "payoff_or_aftereffect": "主角当众翻盘，赢得满堂彩",
                    "callback": "",
                }
            ],
            "ending_texture_contract": {
                "ending_type": "HE",
                "core_wish_fulfilled": "主角登顶",
                "irreversible_cost_retained": "",
            },
        }

    def _finding_codes(self, cost_style: str) -> list[str]:
        from bestseller.services.whole_book_quality_gate import (
            _emotion_quality_metrics_and_findings,
        )

        _, findings = _emotion_quality_metrics_and_findings(
            self._kernel_with_cost_free_win(),
            (),
            cost_style=cost_style,
        )
        return [f.code for f in findings]

    def test_standard_still_flags_a_cost_free_win(self) -> None:
        """The check must keep working for books that never opted out."""

        assert "emotion_cost_free_win" in self._finding_codes("standard")

    @pytest.mark.parametrize("cost_style", ["minimal", "external"])
    def test_opted_out_books_are_not_flagged(self, cost_style: str) -> None:
        assert "emotion_cost_free_win" not in self._finding_codes(cost_style)

    def test_default_argument_keeps_existing_callers_identical(self) -> None:
        from bestseller.services.whole_book_quality_gate import (
            _emotion_quality_metrics_and_findings,
        )

        _, findings = _emotion_quality_metrics_and_findings(
            self._kernel_with_cost_free_win(), ()
        )
        assert "emotion_cost_free_win" in [f.code for f in findings]

    def test_pipeline_actually_forwards_the_book_cost_style(self) -> None:
        """A parameter that is never passed is a fix that does not exist."""

        from bestseller.services import pipelines

        source = inspect.getsource(
            pipelines._enforce_whole_book_quality_gate_after_chapter
        )
        assert "cost_style=resolve_cost_style(" in source
