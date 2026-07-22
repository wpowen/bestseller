"""Guards for the 视角体验 discipline and its survival through compaction.

Two root causes this file exists to prevent:

1. **Inner voice never reached the chapter writer** (2026-07, 诡秘之主 benchmark).
   The POV character had zero on-page thoughts across three chapters; blind
   readers lost on clarity/breath/retention while craft scored fine. The
   authorisation lived only in the ``PROSE_SCENE`` compile, which the
   chapter-first path does not call.
2. **The prompt compactor could silently delete load-bearing rules.** Its
   protected-marker list covered the slop blacklist and the story spine but not
   the anti-AI or POV disciplines, which survived only by an accidental
   substring hit on "内心".
"""

from __future__ import annotations

import pytest

from bestseller.services.pov_experience_discipline import render_pov_experience_block
from bestseller.services.quality_levers.prose_prompt_fusion import (
    render_chapter_position_prose_block,
    render_prose_prompt_fusion_block,
)

pytestmark = pytest.mark.unit


class TestRenderer:
    def test_zh_block_authorises_inner_voice(self) -> None:
        block = render_pov_experience_block(language="zh-CN")
        assert "内心导航" in block
        assert "他自己的嗓音" in block

    def test_zh_block_carries_mechanism_landing(self) -> None:
        """B2 机制落地律: 100% single-add win rate, 2026-06-29 increment validation."""

        block = render_pov_experience_block(language="zh-CN")
        assert "机制落地" in block
        assert "一句大白话" in block

    def test_english_renders_empty_so_callers_can_concatenate_blindly(self) -> None:
        assert render_pov_experience_block(language="en-US") == ""

    def test_scene_scope_states_a_per_scene_floor(self) -> None:
        block = render_pov_experience_block(language="zh-CN", scope="scene")
        assert "每场至少 3 处" in block

    def test_chapter_scope_scales_the_floor_to_the_text_the_model_holds(self) -> None:
        """A per-scene floor of 3 read against a whole chapter would authorise a
        third of the intended inner-voice density."""

        block = render_pov_experience_block(language="zh-CN", scope="chapter")
        assert "全章至少 8 处" in block
        assert "每场" not in block

    def test_simile_budget_is_not_restated_here(self) -> None:
        """anti_ai_voice_discipline owns the simile cap. Two blocks naming two
        different per-chapter numbers (<=3 vs <=4) teaches the model the cap is
        negotiable."""

        block = render_pov_experience_block(language="zh-CN", scope="chapter")
        assert "明喻" not in block
        assert "比喻" not in block


class TestChapterPositionBlock:
    def test_opening_gets_the_blind_judge_validated_hook_law(self) -> None:
        block = render_chapter_position_prose_block(position="opening")
        assert "开篇炸点律" in block

    @pytest.mark.parametrize("position", ["early", "midgame", "climax", "endgame"])
    def test_non_opening_gets_the_retention_law(self, position: str) -> None:
        block = render_chapter_position_prose_block(position=position)
        assert "中段持续追读律" in block
        assert "不可逆推进" in block

    def test_unknown_position_renders_empty(self) -> None:
        assert render_chapter_position_prose_block(position=None) == ""
        assert render_chapter_position_prose_block(position="nonsense") == ""

    def test_english_renders_empty(self) -> None:
        assert render_chapter_position_prose_block(language="en", position="opening") == ""

    @pytest.mark.parametrize("position", ["opening", "midgame"])
    def test_position_block_excludes_the_base_fusion_rules(self, position: str) -> None:
        """The chapter path already gets these from anti_ai_voice_discipline.
        Shipping both spends budget teaching one rule twice."""

        block = render_chapter_position_prose_block(position=position)
        assert "去AI腔铁律" not in block
        assert "不要结论先行" not in block

    def test_scene_path_renderer_still_carries_the_base_block(self) -> None:
        """No-op guard: the scene path must not change behaviour."""

        block = render_prose_prompt_fusion_block(position="opening")
        assert "开篇炸点律" in block
        assert "去AI腔铁律" in block


class TestCompactorProtectsLoadBearingRules:
    """A 10k cap that strips these is a silent quality regression: the prompt
    still looks complete, and the prose quietly reverts to AI voice."""

    @pytest.mark.parametrize(
        "marker",
        ["反AI腔", "语体与用词", "视角与体验", "开篇炸点律", "中段持续追读律"],
    )
    def test_marker_is_registered_as_protected(self, marker: str) -> None:
        from bestseller.services.prompt_compactor import _PROTECTED_SECTION_MARKERS

        assert marker in _PROTECTED_SECTION_MARKERS

    @pytest.mark.parametrize(
        ("renderer", "kwargs"),
        [
            (render_pov_experience_block, {"scope": "chapter"}),
            (render_chapter_position_prose_block, {"position": "opening"}),
            (render_chapter_position_prose_block, {"position": "midgame"}),
        ],
    )
    def test_block_head_is_recognised_by_the_protection_check(
        self, renderer, kwargs
    ) -> None:
        """Protection matches the section's first 80 chars only, so a header
        rename can silently unprotect a block."""

        from bestseller.services.prompt_compactor import _section_is_protected

        block = renderer(language="zh-CN", **kwargs)
        assert block, "renderer produced nothing to protect"
        assert _section_is_protected(block)

    def test_anti_ai_discipline_head_is_recognised(self) -> None:
        from bestseller.services.anti_ai_voice_discipline import (
            render_anti_ai_voice_discipline,
        )
        from bestseller.services.prompt_compactor import _section_is_protected

        block = render_anti_ai_voice_discipline(language="zh-CN", scope="chapter")
        assert _section_is_protected(block)
