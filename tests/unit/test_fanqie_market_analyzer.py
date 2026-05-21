from __future__ import annotations

from datetime import date

import pytest

from bestseller.domain.fanqie_market import FanqieRankingBook, FanqieRankingSnapshot
from bestseller.services.fanqie_market_analyzer import (
    build_category_profile,
    build_competitor_profile,
    build_competitor_profiles,
    build_craft_profile,
)

pytestmark = pytest.mark.unit


def _snapshot() -> FanqieRankingSnapshot:
    return FanqieRankingSnapshot(
        category="都市脑洞",
        board_type="reading",
        data_date=date(2026, 5, 20),
        books=[
            FanqieRankingBook(
                source_book_id="b1",
                title="每天六千万, 只能在县城花?",
                author="凤失凰",
                category="都市脑洞",
                rank=1,
                reader_count=920000,
                tags=["系统", "都市", "爽文"],
                intro="主角每天到账巨额资金, 必须在县城完成消费和反击循环。",
            ),
            FanqieRankingBook(
                source_book_id="b2",
                title="跳楼未遂, 我靠破案系统征服警花",
                author="我也不想的啊",
                category="都市脑洞",
                rank=2,
                reader_count=610000,
                tags=["破案", "系统", "警花"],
                intro="危机开局后获得破案系统, 用证据推动一次次真相曝光。",
            ),
        ],
    )


def test_build_competitor_profile_extracts_commercial_signals() -> None:
    profile = build_competitor_profile(_snapshot().books[0])

    assert profile.source_book_id == "b1"
    assert "resource_wish" in profile.premise_signals
    assert "repeatable_mechanism_loop" in profile.structure_patterns
    assert profile.anti_copy_constraints
    assert profile.confidence > 0.7


def test_build_category_profile_aggregates_snapshot_patterns() -> None:
    snapshot = _snapshot()
    profiles = build_competitor_profiles(snapshot)
    category = build_category_profile(snapshot, profiles)

    assert category.sample_size == 2
    assert category.reader_heat_stats["max"] == 920000
    assert "repeatable_mechanism_loop" in category.structure_patterns
    assert "system_holder" in category.protagonist_archetypes
    assert category.confidence > 0.5


def test_build_craft_profile_is_prompt_ready_and_anonymous() -> None:
    category = build_category_profile(_snapshot())
    craft = build_craft_profile(category)
    card = craft.to_prompt_card()

    assert craft.source_profile_ids == ["b1", "b2"]
    assert "No exact living-author prose imitation." in craft.disallowed_copy_targets
    assert "source_profile_ids" not in card
    assert card["hook_rules"]
