"""Guard: the logline gate's story-logic rubric must reach the concept kernel.

Root cause (2026-07-21/22, three consecutive real book creations failed): the
pre-planning logline gate hard-kills on ``protagonist_rationality`` /
``cost_integrity`` / ``causal_coherence`` — story-level properties that are
baked into the concept kernel's ``current_goal`` / ``opening_crisis`` /
``failure_cost`` fields. The kernel generator never saw that rubric (the
tournament's own judge passes 人物决策 at a 7.0 floor; the gate kills 主角决策
智力 at 3.0), and the gate's rescue loop only rewrites the logline *sentence*
while being forbidden from inventing story facts — so a kernel that bakes an
irrational opening is unrescuable downstream. Third instance of the
生成端↔判官端不同源 disease, after plain_language and the cliché list.
"""

from __future__ import annotations

import pytest

from bestseller.services import concept_tournament as ct
from bestseller.services.logline_gate import (
    _FIX_DIRECTIVES,
    _STORY_LOGIC_AXES,
    render_story_logic_writer_rules,
)

pytestmark = pytest.mark.unit


class TestContractSingleSource:
    def test_covers_the_three_book_killing_axes(self) -> None:
        assert set(_STORY_LOGIC_AXES) == {
            "protagonist_rationality",
            "cost_integrity",
            "causal_coherence",
        }

    @pytest.mark.parametrize("axis", _STORY_LOGIC_AXES)
    def test_writer_rules_quote_the_judge_directives_verbatim(self, axis: str) -> None:
        """Paraphrased copies drift; drift is what killed the books."""

        assert _FIX_DIRECTIVES[axis] in render_story_logic_writer_rules()

    def test_rules_are_framed_as_a_gate_warning(self) -> None:
        assert "一票否决" in render_story_logic_writer_rules()


class TestKernelGeneratorSeesTheContract:
    def test_kernel_prompt_carries_the_story_logic_rules(self) -> None:
        _system, user = ct._build_engine_kernel_messages(
            genre="玄幻",
            sub_genre="玄幻",
            lane="action-progression",
            chapter_count=50,
        )
        assert "故事逻辑硬门" in user
        assert "核验、求助、停止、撤退" in user

    def test_rules_ride_along_regardless_of_ban_list(self) -> None:
        """The cliché block is conditional on ``banned``; the story-logic
        contract must not be — every kernel bakes these fields."""

        _system, user = ct._build_engine_kernel_messages(
            genre="都市",
            sub_genre="都市",
            lane="纯题材直觉",
            chapter_count=50,
            banned=(),
        )
        assert "故事逻辑硬门" in user
