"""Guards for the 反AI腔 discipline single source.

Root cause this file exists to prevent (2026-07-15): the discipline lived as a
string literal in the writer prompt only. `scene_rewrite` / `chapter_rewrite`
never had it, yet produced 76% of shipped prose — so every anti-AI fix since
2026-07-04 reached only a quarter of the output. These tests assert the rules
reach *every* prose-producing path.
"""

from __future__ import annotations

import pytest

from bestseller.services.anti_ai_voice_discipline import render_anti_ai_voice_discipline


class TestRenderer:
    def test_zh_block_carries_the_user_facing_complaints(self) -> None:
        block = render_anti_ai_voice_discipline(language="zh-CN")
        assert "不要结论先行/总分总" in block
        assert "高冲击具身动词" in block
        assert "通感与陌生化比喻" in block
        assert "身体反应不是情绪的默认替身" in block
        assert "人物对本场物件作出的选择" in block

    def test_english_renders_empty_so_callers_can_concatenate_blindly(self) -> None:
        assert render_anti_ai_voice_discipline(language="en-US") == ""

    def test_negated_definition_frame_is_banned_with_a_replacement(self) -> None:
        """Measured regression (2026-07-20 prompt A/B).

        The chapter-first prompt used to ban 不是X而是Y in a hand-rolled literal.
        Replacing that literal with this source dropped the ban — the arm without
        it produced 3 negated_definition hits in a case where the arm with it
        produced 0. The ban belongs here so every path keeps it; it is stated as
        a paired rewrite because bare prohibitions prime what they forbid.
        """

        block = render_anti_ai_voice_discipline(language="zh-CN")
        assert "对举式定义" in block
        assert "拆成两个独立短句" in block

    def test_scene_scope_states_a_per_scene_budget(self) -> None:
        block = render_anti_ai_voice_discipline(language="zh-CN", scope="scene")
        assert "同一个高冲击具身动词全场最多 2 次" in block

    def test_chapter_scope_scales_the_budget_to_the_text_the_model_holds(self) -> None:
        """A chapter is 2-3 scenes. Telling a chapter rewriter '全场最多 2 次' would
        either be ignored or over-constrain; the budget must name the chapter."""

        block = render_anti_ai_voice_discipline(language="zh-CN", scope="chapter")
        assert "同一个高冲击具身动词全章最多 4 次" in block
        assert "全场" not in block

    def test_detector_lexicon_is_not_injected_into_generation_prompt(self) -> None:
        """Blacklist-only prompting primed the exact diction it tried to ban."""

        from bestseller.services.ai_flavor.detector import _VERB_TIC_LEXICON_ZH

        block = render_anti_ai_voice_discipline(language="zh-CN")
        leaked = [verb for verb in _VERB_TIC_LEXICON_ZH if f"{verb}、" in block]
        assert not leaked, f"detector lexicon leaked into generation prompt: {leaked}"


class TestAllProsePathsCarryTheDiscipline:
    """The regression that started it all: rules present in one path, absent in
    the two that ship most of the prose."""

    @pytest.mark.parametrize(
        "rule",
        ["不要结论先行/总分总", "身体反应不是情绪的默认替身"],
    )
    def test_scene_rewrite_system_prompt_carries_rule(self, rule: str) -> None:
        from _prose_prompt_fixtures import build_scene_rewrite_system_prompt

        assert rule in build_scene_rewrite_system_prompt()

    @pytest.mark.parametrize(
        "rule",
        ["不要结论先行/总分总", "身体反应不是情绪的默认替身"],
    )
    def test_chapter_rewrite_system_prompt_carries_rule(self, rule: str) -> None:
        from _prose_prompt_fixtures import build_chapter_rewrite_system_prompt

        assert rule in build_chapter_rewrite_system_prompt()

    def test_chapter_rewrite_uses_chapter_scoped_verb_budget(self) -> None:
        """A per-scene budget stated to a chapter rewriter is the scope bug that
        let per-scene compliance multiply into chapter-level tics."""

        from _prose_prompt_fixtures import build_chapter_rewrite_system_prompt

        assert "同一个高冲击具身动词全章最多 4 次" in (
            build_chapter_rewrite_system_prompt()
        )

    # ── chapter-first WRITER path: compact discipline (plan A2) ─────────────
    # The rewrite paths above keep the full discipline: they receive an existing
    # draft and are asked to repair named defects, so enumerated rules act as a
    # checklist against concrete text. The chapter-first WRITER path composes
    # from scratch, and there the same enumeration measurably backfired — the
    # dose-response ablation (prose_prompt_profile.py) scored 20,988 chars of
    # instruction at 3.50 blind vs 6.8-7.8 for the 175-633 char band, with the
    # writer switching from storytelling to compliance-form filling.
    #
    # So A2 swapped this path to render_compact_writer_discipline. The rules
    # dropped here did not disappear; they moved behind generation:
    #   身体反应/度量腔/明喻过密 → ai_flavor_gate (embodied_verb_spam,
    #     zh.simile.overrun, measure-tic) + reviews.py A3 repeat penalties
    # See test_compact_discipline_delegates_dropped_rules_to_post_gates below.
    @pytest.mark.parametrize(
        "rule",
        ["不要结论先行", "没做什么", "只输出正文"],
    )
    def test_chapter_first_system_prompt_carries_rule(self, rule: str) -> None:
        """The generation path this guard originally missed.

        ``build_chapter_first_draft_prompts`` shipped a *hand-rolled* paraphrase
        of these rules instead of importing the source, so every fix landed here
        only if someone remembered to edit a second string literal. Under
        chapter_hybrid this path writes the whole book. The import-not-inline
        contract still holds — only the rendered variant changed.
        """

        from _prose_prompt_fixtures import build_chapter_first_system_prompt

        assert rule in build_chapter_first_system_prompt()

    def test_chapter_first_uses_chapter_scoped_verb_budget(self) -> None:
        """Scope still matters: a per-scene cap stated to a whole-chapter writer
        is the bug that let per-scene compliance multiply into chapter tics."""

        from _prose_prompt_fixtures import build_chapter_first_system_prompt

        prompt = build_chapter_first_system_prompt()
        assert "同一个高冲击动词全章别超过 4 次" in prompt
        assert "全场" not in prompt

    def test_chapter_first_discipline_stays_within_the_ablation_band(self) -> None:
        """A2's whole point: the discipline block must stay short.

        Guards against the historical failure mode where each AI-flavor incident
        appended one more rule until the block was back to compliance-form size.
        """

        from bestseller.services.anti_ai_voice_discipline import (
            render_compact_writer_discipline,
        )

        block = render_compact_writer_discipline(language="zh-CN", scope="chapter")
        assert len(block) <= 400, (
            f"compact discipline grew to {len(block)} chars; the dose-response "
            "optimum is the 175-633 char instruction band"
        )
        assert block.count("- ") == 4, "compact discipline is exactly four rules"

    def test_compact_discipline_delegates_dropped_rules_to_post_gates(self) -> None:
        """The rules compact drops must still be enforced somewhere.

        Dropping them from the prompt is only safe because deterministic
        detectors catch them after generation. If those detectors were removed,
        the prompt diet would silently become a quality regression.
        """

        import inspect

        from bestseller.services.ai_flavor import detector as ai_flavor_detector

        source = inspect.getsource(ai_flavor_detector)
        for rule_id in ("zh.tic.embodied_verb_spam", "zh.simile.overrun"):
            assert rule_id in source, (
                f"{rule_id} must stay in the ai_flavor detector: the "
                "chapter-first prompt no longer states this rule, so removing "
                "the detector would drop the concern entirely"
            )

    def test_chapter_first_does_not_enumerate_body_signals(self) -> None:
        """The regression that made this refactor necessary.

        The old hand-rolled constraint read '不要用手腕发烫、指尖发冷、呼吸一滞、
        心口一紧代替…'. The 50-round arena (2026-07-18) showed blacklist-only
        prompting primes the exact diction it bans; the shipped book that prompt
        produced contained 手腕x49 / 烫x58 / 喉结x26 / 指节发白x15.
        """

        from _prose_prompt_fixtures import build_chapter_first_system_prompt

        prompt = build_chapter_first_system_prompt()
        for primed in ("手腕发烫", "指尖发冷", "呼吸一滞", "心口一紧"):
            assert primed not in prompt, f"body-signal lexicon primed in prompt: {primed}"

    def test_english_rewrite_prompts_are_unaffected(self) -> None:
        from _prose_prompt_fixtures import build_scene_rewrite_system_prompt

        assert "身体反应不是情绪的默认替身" not in build_scene_rewrite_system_prompt(
            language="en-US"
        )

    def test_english_chapter_first_is_unaffected(self) -> None:
        from _prose_prompt_fixtures import build_chapter_first_system_prompt

        assert "身体反应不是情绪的默认替身" not in build_chapter_first_system_prompt(
            language="en"
        )
