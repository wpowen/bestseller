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
        assert "不削弱主角" in text or "不得打断爽点" in text

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
