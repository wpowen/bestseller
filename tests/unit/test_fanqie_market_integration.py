from __future__ import annotations

from datetime import date

import pytest

from bestseller.domain.fanqie_market import FanqieCategoryProfile, FanqieCraftProfile
from bestseller.services.fanqie_market_integration import (
    render_fanqie_craft_profile_block,
    render_fanqie_market_profile_block,
)

pytestmark = pytest.mark.unit


def test_render_fanqie_market_profile_block_is_compact_and_safe() -> None:
    profile = FanqieCategoryProfile(
        category="都市脑洞",
        data_date=date(2026, 5, 20),
        sample_size=8,
        dominant_settings=["县城神豪", "系统经营"],
        hook_patterns=["title_as_mechanism", "opening_crisis_first"],
        structure_patterns=["repeatable_mechanism_loop"],
        payoff_patterns=["public_exposure_payoff"],
        style_guidelines=["platform_fast_reading"],
        safety_notes=["禁止复刻书名、角色、作者文风。"],
    )

    block = render_fanqie_market_profile_block(profile)

    assert "番茄榜单市场画像" in block
    assert "都市脑洞" in block
    assert "repeatable_mechanism_loop" in block
    assert "禁止复刻" in block


def test_render_fanqie_craft_profile_block_omits_source_profile_ids() -> None:
    profile = FanqieCraftProfile(
        category="悬疑脑洞",
        source_profile_ids=["source-book-1"],
        allowed_style_principles=["短句推进"],
        disallowed_copy_targets=["No exact living-author prose imitation."],
        hook_rules=["Open with visible pressure."],
        pacing_rules=["Every chapter gives a clue, cost, or reversal."],
        structure_rules=["case pressure -> action -> clue -> bigger danger"],
        sentence_style="Lean and concrete.",
        safety_boundary="Use category mechanics only.",
    )

    block = render_fanqie_craft_profile_block(profile, language="en")

    assert "Fanqie Anonymous Market Craft Card" in block
    assert "source-book-1" not in block
    assert "living-author prose imitation" in block
    assert "case pressure" in block
