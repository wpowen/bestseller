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
        assert "具身动词禁止复读" in block
        assert "烫" in block
        assert "通感与陌生化比喻" in block

    def test_english_renders_empty_so_callers_can_concatenate_blindly(self) -> None:
        assert render_anti_ai_voice_discipline(language="en-US") == ""

    def test_scene_scope_states_a_per_scene_budget(self) -> None:
        block = render_anti_ai_voice_discipline(language="zh-CN", scope="scene")
        assert "同一个词全场最多 2 次" in block

    def test_chapter_scope_scales_the_budget_to_the_text_the_model_holds(self) -> None:
        """A chapter is 2-3 scenes. Telling a chapter rewriter '全场最多 2 次' would
        either be ignored or over-constrain; the budget must name the chapter."""

        block = render_anti_ai_voice_discipline(language="zh-CN", scope="chapter")
        assert "同一个词全章最多 4 次" in block
        assert "全场" not in block

    def test_verb_lexicon_stays_in_sync_with_the_detector_that_judges_it(self) -> None:
        """The writer is judged by `_detect_verb_tic_spam`. If the detector's
        lexicon drifts from the prompt's, prose is punished for a rule it was
        never told."""

        from bestseller.services.ai_flavor.detector import _VERB_TIC_LEXICON_ZH

        block = render_anti_ai_voice_discipline(language="zh-CN")
        missing = [verb for verb in _VERB_TIC_LEXICON_ZH if verb not in block]
        assert not missing, f"detector flags {missing} but the prompt never bans them"


class TestAllProsePathsCarryTheDiscipline:
    """The regression that started it all: rules present in one path, absent in
    the two that ship most of the prose."""

    @pytest.mark.parametrize(
        "rule",
        ["不要结论先行/总分总", "具身动词禁止复读"],
    )
    def test_scene_rewrite_system_prompt_carries_rule(self, rule: str) -> None:
        from _prose_prompt_fixtures import build_scene_rewrite_system_prompt

        assert rule in build_scene_rewrite_system_prompt()

    @pytest.mark.parametrize(
        "rule",
        ["不要结论先行/总分总", "具身动词禁止复读"],
    )
    def test_chapter_rewrite_system_prompt_carries_rule(self, rule: str) -> None:
        from _prose_prompt_fixtures import build_chapter_rewrite_system_prompt

        assert rule in build_chapter_rewrite_system_prompt()

    def test_chapter_rewrite_uses_chapter_scoped_verb_budget(self) -> None:
        """A per-scene budget stated to a chapter rewriter is the scope bug that
        let per-scene compliance multiply into chapter-level tics."""

        from _prose_prompt_fixtures import build_chapter_rewrite_system_prompt

        assert "同一个词全章最多 4 次" in build_chapter_rewrite_system_prompt()

    def test_english_rewrite_prompts_are_unaffected(self) -> None:
        from _prose_prompt_fixtures import build_scene_rewrite_system_prompt

        assert "具身动词禁止复读" not in build_scene_rewrite_system_prompt(language="en-US")
