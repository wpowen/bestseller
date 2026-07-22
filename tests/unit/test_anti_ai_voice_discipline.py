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

    @pytest.mark.parametrize(
        "rule",
        ["不要结论先行/总分总", "身体反应不是情绪的默认替身"],
    )
    def test_chapter_first_system_prompt_carries_rule(self, rule: str) -> None:
        """The generation path this guard originally missed.

        ``build_chapter_first_draft_prompts`` shipped a *hand-rolled* paraphrase
        of these rules instead of importing the source, so every fix landed here
        only if someone remembered to edit a second string literal. Under
        chapter_hybrid this path writes the whole book.
        """

        from _prose_prompt_fixtures import build_chapter_first_system_prompt

        assert rule in build_chapter_first_system_prompt()

    def test_chapter_first_uses_chapter_scoped_verb_budget(self) -> None:
        from _prose_prompt_fixtures import build_chapter_first_system_prompt

        prompt = build_chapter_first_system_prompt()
        assert "同一个高冲击具身动词全章最多 4 次" in prompt
        assert "全场" not in prompt

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
