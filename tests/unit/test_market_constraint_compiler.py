from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from bestseller.domain.fanqie_market import (
    FanqieCategoryProfile,
    FanqieCompetitorProfile,
    FanqieCraftProfile,
    FanqieMarketAnalysisBundle,
    FanqieRankingSnapshot,
)
from bestseller.services.market_constraint_compiler import (
    compile_chapter_constraints,
    render_chapter_constraints_block,
)

pytestmark = pytest.mark.unit


def _bundle(
    *,
    hook_patterns: list[str] | None = None,
    saturated_hook: str = "title_as_mechanism",
) -> FanqieMarketAnalysisBundle:
    """Build a deterministic test bundle."""

    snapshot = FanqieRankingSnapshot(
        source="fanqiehub-test",
        board_type="reading",
        category="都市脑洞",
        data_date=date(2026, 5, 20),
        fetched_at=datetime.now(UTC),
        books=[],
    )

    # Build competitors so saturated_hook appears in 7/10 -> saturated
    competitors: list[FanqieCompetitorProfile] = []
    for idx in range(10):
        hooks = ([saturated_hook] if idx < 7 else [])
        hooks += [f"unique_hook_{idx}"]
        competitors.append(
            FanqieCompetitorProfile(
                source_book_id=f"book-{idx}",
                title=f"测试书{idx}",
                rank=idx + 1,
                hook_patterns=hooks,
                structure_patterns=["repeatable_mechanism_loop"] if idx < 6 else ["case_to_action"],
            )
        )

    category = FanqieCategoryProfile(
        category="都市脑洞",
        data_date=date(2026, 5, 20),
        sample_size=10,
        dominant_settings=["县城神豪"],
        hook_patterns=hook_patterns or [
            saturated_hook,
            "opening_crisis_first",
            "identity_reversal",
            "ticking_clock",
            "public_exposure",
        ],
        structure_patterns=["repeatable_mechanism_loop", "case_to_action"],
        payoff_patterns=["public_exposure_payoff", "money_payoff"],
        style_guidelines=["platform_fast_reading"],
        safety_notes=["禁止复刻书名"],
        confidence=0.7,
    )

    craft = FanqieCraftProfile(
        category="都市脑洞",
        allowed_style_principles=["短句推进"],
        disallowed_copy_targets=["No exact prose imitation"],
        hook_rules=["Open with visible pressure"],
        pacing_rules=["每章给一个钩子、代价、或反转"],
        structure_rules=["case pressure -> action -> clue"],
        safety_boundary="只复用类目机制",
        confidence=0.7,
    )

    return FanqieMarketAnalysisBundle(
        snapshot=snapshot,
        competitor_profiles=competitors,
        category_profile=category,
        craft_profile=craft,
    )


def test_compile_constraints_early_band() -> None:
    bundle = _bundle()
    c = compile_chapter_constraints(bundle, chapter_position=1)

    assert c.band == "early"
    assert c.chapter_position == 1
    assert c.must_hit_hooks
    assert c.min_hooks_required >= 1
    assert c.optimal_chapter_length_min > 0
    assert c.optimal_chapter_length_max > c.optimal_chapter_length_min
    assert c.category == "都市脑洞"


def test_compile_constraints_rising_and_steady_bands() -> None:
    bundle = _bundle()
    rising = compile_chapter_constraints(bundle, chapter_position=10)
    steady = compile_chapter_constraints(bundle, chapter_position=60)

    assert rising.band == "rising"
    assert steady.band == "steady"


def test_compile_constraints_suppresses_saturated_hooks() -> None:
    bundle = _bundle(saturated_hook="title_as_mechanism")
    c = compile_chapter_constraints(bundle, chapter_position=2)

    assert "title_as_mechanism" not in c.must_hit_hooks
    assert "title_as_mechanism" in c.saturated_tropes
    assert any("avoid_saturated:title_as_mechanism" in f for f in c.forbidden_patterns)


def test_compile_constraints_handles_missing_bundle() -> None:
    c = compile_chapter_constraints(None, chapter_position=1)

    assert c.band == "early"
    assert c.confidence == 0.0
    assert c.optimal_chapter_length_min > 0


def test_compile_constraints_target_length_overrides_band_default() -> None:
    bundle = _bundle()
    c = compile_chapter_constraints(bundle, chapter_position=1, target_length=4000)

    assert c.optimal_chapter_length_min < 4000 < c.optimal_chapter_length_max


def test_compile_constraints_rejects_invalid_chapter_position() -> None:
    bundle = _bundle()
    with pytest.raises(ValueError):
        compile_chapter_constraints(bundle, chapter_position=0)


def test_render_block_includes_required_hooks() -> None:
    bundle = _bundle()
    c = compile_chapter_constraints(bundle, chapter_position=1)

    block = render_chapter_constraints_block(c)

    assert "市场硬约束" in block
    assert "钩子" in block
    assert "都市脑洞" in block


def test_render_block_supports_english() -> None:
    bundle = _bundle()
    c = compile_chapter_constraints(bundle, chapter_position=2)

    block = render_chapter_constraints_block(c, language="en")

    assert "Market Hard Constraints" in block


def test_render_block_handles_none() -> None:
    assert render_chapter_constraints_block(None) == ""


def test_compile_constraints_extra_safety_notes_appended() -> None:
    bundle = _bundle()
    c = compile_chapter_constraints(
        bundle,
        chapter_position=5,
        extra_safety_notes=["不出现 brand X", "保留心理描写"],
    )

    assert "不出现 brand X" in c.safety_boundary
    assert "保留心理描写" in c.safety_boundary
