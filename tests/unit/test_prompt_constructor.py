"""Unit tests for L3 PromptConstructor.

Covers:
    * choose_opening_archetype (preassigned + rotation fallback)
    * choose_cliffhanger_type (policy-respecting)
    * build_diversity_constraints (hot vocab + ban phrases)
    * build_prior_chapter_tail (truncation)
    * build_chapter_prompt (end-to-end stitching)
    * rebuild_with_feedback (immutable feedback swap)
    * PromptPlan.render (empty-section skipping, stable order)
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.diversity_budget import DiversityBudget
from bestseller.services.invariants import (
    CliffhangerPolicy,
    CliffhangerType,
    OpeningArchetype,
    seed_invariants,
)
from bestseller.services.prompt_constructor import (
    DEFAULT_PRIOR_CHAPTER_TAIL_CHARS,
    PromptPlan,
    build_anti_slop_footer,
    build_chapter_prompt,
    build_diversity_constraints,
    build_invariants_section,
    build_methodology_inject,
    build_prior_chapter_tail,
    choose_cliffhanger_type,
    choose_opening_archetype,
    rebuild_with_feedback,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _invariants(language: str = "zh-CN", **overrides):
    kwargs = dict(
        project_id=uuid4(),
        language=language,
        words_per_chapter=SimpleNamespace(min=5000, target=6400, max=7500),
        pov="close_third",
    )
    inv = seed_invariants(**kwargs)
    if overrides:
        from dataclasses import replace as _r
        inv = _r(inv, **overrides)
    return inv


# ---------------------------------------------------------------------------
# choose_opening_archetype.
# ---------------------------------------------------------------------------


class TestChooseOpeningArchetype:
    def test_preassigned_wins_over_budget_suggestion(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_opening(1, OpeningArchetype.HUMILIATION)
        pick = choose_opening_archetype(
            budget,
            pool=[OpeningArchetype.CRISIS, OpeningArchetype.HUMILIATION],
            preassigned=OpeningArchetype.HUMILIATION,
        )
        assert pick == OpeningArchetype.HUMILIATION

    def test_avoids_recent_archetypes(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_opening(1, OpeningArchetype.HUMILIATION)
        budget.register_opening(2, OpeningArchetype.CRISIS)
        pool = [
            OpeningArchetype.HUMILIATION,
            OpeningArchetype.CRISIS,
            OpeningArchetype.ENCOUNTER,
        ]
        pick = choose_opening_archetype(budget, pool=pool, no_repeat_within=3)
        assert pick == OpeningArchetype.ENCOUNTER


# ---------------------------------------------------------------------------
# choose_cliffhanger_type.
# ---------------------------------------------------------------------------


class TestChooseCliffhangerType:
    def test_default_policy_picks_unused(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_cliffhanger(1, CliffhangerType.REVELATION)
        pick = choose_cliffhanger_type(budget)
        assert pick != CliffhangerType.REVELATION

    def test_custom_policy_respected(self) -> None:
        policy = CliffhangerPolicy(
            no_repeat_within=2,
            allowed_types=(CliffhangerType.DECISION, CliffhangerType.POWER_SHIFT),
        )
        budget = DiversityBudget(project_id=uuid4())
        budget.register_cliffhanger(1, CliffhangerType.DECISION)
        assert (
            choose_cliffhanger_type(budget, policy=policy)
            == CliffhangerType.POWER_SHIFT
        )


# ---------------------------------------------------------------------------
# build_invariants_section.
# ---------------------------------------------------------------------------


class TestBuildInvariantsSection:
    def test_includes_language_pov_envelope(self) -> None:
        inv = _invariants("zh-CN")
        section = build_invariants_section(inv)
        assert "zh-CN" in section
        assert "close_third" in section
        # Length envelope reflected as min-max.
        assert "5000" in section or "5,000" in section
        assert "7500" in section or "7,500" in section


# ---------------------------------------------------------------------------
# build_diversity_constraints.
# ---------------------------------------------------------------------------


class TestBuildDiversityConstraints:
    def test_empty_inputs_yield_empty_string(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        inv = _invariants("zh-CN")
        assert (
            build_diversity_constraints(inv, budget)
            == ""
        )

    def test_includes_assigned_opening_and_cliffhanger_zh(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        inv = _invariants("zh-CN")
        text = build_diversity_constraints(
            inv,
            budget,
            assigned_opening=OpeningArchetype.HUMILIATION,
            assigned_cliffhanger=CliffhangerType.REVELATION,
        )
        assert "开篇" in text
        assert "屈辱" in text
        assert "章末悬念" in text
        assert "revelation" in text

    def test_includes_assigned_opening_en(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        inv = _invariants("en")
        text = build_diversity_constraints(
            inv,
            budget,
            assigned_opening=OpeningArchetype.CRISIS,
        )
        assert "CRISIS" in text

    def test_hot_vocab_appears_when_available(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_vocab(1, "shard shard shard stone", "en")
        budget.register_vocab(2, "shard shard stone", "en")
        budget.register_vocab(3, "shard stone stone", "en")
        inv = _invariants("en")
        text = build_diversity_constraints(
            inv,
            budget,
            hot_vocab_window=5,
            hot_vocab_top_n=5,
            hot_vocab_min_count=3,
        )
        assert "shard" in text
        assert "Banned" in text or "banned" in text.lower()

    def test_banned_phrases_rendered(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        inv = _invariants(
            "zh-CN",
            banned_formulaic_phrases=("三百年前被轻视", "瞪大了眼睛"),
        )
        text = build_diversity_constraints(
            inv,
            budget,
            assigned_opening=OpeningArchetype.CRISIS,
        )
        assert "三百年前被轻视" in text
        assert "瞪大了眼睛" in text


# ---------------------------------------------------------------------------
# build_methodology_inject.
# ---------------------------------------------------------------------------


class TestBuildMethodologyInject:
    def test_empty_fragments_yield_empty(self) -> None:
        inv = _invariants("zh-CN", forced_methodology_fragments=())
        assert build_methodology_inject(inv) == ""

    def test_fragments_stitched_with_header_zh(self) -> None:
        inv = _invariants(
            "zh-CN",
            forced_methodology_fragments=("情感演出三步法：观察→反应→动作。", ""),
        )
        text = build_methodology_inject(inv)
        assert "强制创作方法论" in text
        assert "情感演出三步法" in text

    def test_fragments_stitched_with_header_en(self) -> None:
        inv = _invariants(
            "en",
            forced_methodology_fragments=("Show sensory detail before naming the emotion.",),
        )
        text = build_methodology_inject(inv)
        assert "MANDATORY METHODOLOGY" in text
        assert "sensory" in text


# ---------------------------------------------------------------------------
# build_prior_chapter_tail.
# ---------------------------------------------------------------------------


class TestBuildPriorChapterTail:
    def test_short_text_passes_through(self) -> None:
        text = "他推开门走出去。"
        out = build_prior_chapter_tail(text, max_chars=100)
        assert "他推开门走出去。" in out
        assert "前一章结尾原文" in out

    def test_truncates_long_text_to_tail(self) -> None:
        text = "A" * 1000 + "ZZZ_TAIL"
        out = build_prior_chapter_tail(text, max_chars=10)
        assert out.endswith("ZZZ_TAIL")
        assert "A" * 20 not in out  # not including earlier content

    def test_empty_input_returns_empty(self) -> None:
        assert build_prior_chapter_tail(None) == ""
        assert build_prior_chapter_tail("") == ""

    def test_zero_or_negative_max_chars_returns_empty(self) -> None:
        assert build_prior_chapter_tail("long text", max_chars=0) == ""
        assert build_prior_chapter_tail("long text", max_chars=-5) == ""


# ---------------------------------------------------------------------------
# build_anti_slop_footer.
# ---------------------------------------------------------------------------


class TestBuildAntiSlopFooter:
    def test_chinese_language(self) -> None:
        out = build_anti_slop_footer("zh-CN")
        assert "禁止项" in out

    def test_english_language(self) -> None:
        out = build_anti_slop_footer("en")
        assert "DO NOT" in out


# ---------------------------------------------------------------------------
# build_chapter_prompt.
# ---------------------------------------------------------------------------


class TestBuildChapterPrompt:
    def test_happy_path_renders_all_sections(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        inv = _invariants("zh-CN")
        plan = build_chapter_prompt(
            inv,
            budget,
            chapter_no=2,
            system="你是畅销小说作家。",
            bible_slice="【角色】主角：林奚。",
            scene_spec="【本章任务】主角初入宗门。",
            prior_chapter_text="他闭上双眼，深吸一口气。",
            preassigned_opening=OpeningArchetype.CRISIS,
        )
        assert isinstance(plan, PromptPlan)
        assert plan.assigned_opening == OpeningArchetype.CRISIS
        assert plan.assigned_cliffhanger is not None
        rendered = plan.render()
        # System first, footer last.
        assert rendered.startswith("你是畅销小说作家")
        assert "禁止项" in rendered
        # All supplied sections present.
        assert "主角：林奚" in rendered
        assert "本章任务" in rendered
        assert "前一章结尾" in rendered
        # Diversity section included.
        assert "开篇" in rendered and "危机" in rendered

    def test_empty_sections_skipped_in_render(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        inv = _invariants("en")
        plan = build_chapter_prompt(
            inv,
            budget,
            chapter_no=1,
            # system, bible_slice, scene_spec, prior_chapter_text all empty
            preassigned_opening=OpeningArchetype.CRISIS,
        )
        rendered = plan.render()
        # No double-empty blank lines between sections.
        assert "\n\n\n" not in rendered
        # Invariants section always renders; diversity + footer always render.
        assert "不变量" in rendered or "language" in rendered.lower() or "en" in rendered
        assert "DO NOT" in rendered or "禁止项" in rendered


# ---------------------------------------------------------------------------
# rebuild_with_feedback.
# ---------------------------------------------------------------------------


class TestRebuildWithFeedback:
    def test_empty_feedback_clears_block(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        inv = _invariants("zh-CN")
        plan = build_chapter_prompt(
            inv,
            budget,
            system="系统指令",
            preassigned_opening=OpeningArchetype.CRISIS,
        )
        plan2 = rebuild_with_feedback(plan, "修改建议：字数不足")
        assert plan2.feedback_block == "修改建议：字数不足"
        # Rendered output now contains the feedback at the end.
        rendered = plan2.render()
        assert rendered.rstrip().endswith("修改建议：字数不足")

        plan3 = rebuild_with_feedback(plan2, "")
        assert plan3.feedback_block == ""
        assert "修改建议" not in plan3.render()

    def test_is_immutable_on_original(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        inv = _invariants("zh-CN")
        plan = build_chapter_prompt(
            inv, budget, system="S", preassigned_opening=OpeningArchetype.CRISIS
        )
        plan2 = rebuild_with_feedback(plan, "new feedback")
        assert plan.feedback_block == ""
        assert plan2.feedback_block == "new feedback"


# ---------------------------------------------------------------------------
# PromptPlan render ordering.
# ---------------------------------------------------------------------------


class TestPromptPlanRender:
    def test_render_order(self) -> None:
        plan = PromptPlan(
            system="SYSTEM",
            invariants_section="INV",
            bible_slice="BIBLE",
            methodology_inject="METHOD",
            diversity_constraints="DIVERSITY",
            prior_chapter_tail="TAIL",
            scene_spec="SCENE",
            anti_slop_footer="FOOTER",
            feedback_block="FEEDBACK",
        )
        out = plan.render()
        idx = [
            out.index("SYSTEM"),
            out.index("INV"),
            out.index("BIBLE"),
            out.index("METHOD"),
            out.index("DIVERSITY"),
            out.index("TAIL"),
            out.index("SCENE"),
            out.index("FOOTER"),
            out.index("FEEDBACK"),
        ]
        assert idx == sorted(idx)

    def test_render_skips_empty_sections(self) -> None:
        plan = PromptPlan(
            system="SYS",
            bible_slice="",
            anti_slop_footer="FOOT",
        )
        out = plan.render()
        assert out == "SYS\n\nFOOT"


# ---------------------------------------------------------------------------
# Sanity: Default constant values.
# ---------------------------------------------------------------------------


def test_default_prior_chapter_tail_chars() -> None:
    assert DEFAULT_PRIOR_CHAPTER_TAIL_CHARS == 800
