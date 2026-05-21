from __future__ import annotations

from datetime import date

from pydantic import ValidationError
import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.domain.fanqie_market import (
    FanqieCategoryProfile,
    FanqieCraftProfile,
    FanqieRankingBook,
    FanqieRankingSnapshot,
)

pytestmark = pytest.mark.unit


def test_ranking_snapshot_sorts_books_and_exposes_top_titles() -> None:
    snapshot = FanqieRankingSnapshot(
        category="都市高武",
        board_type="reading",
        data_date=date(2026, 5, 20),
        books=[
            FanqieRankingBook(source_book_id="b2", title="第二本", rank=2),
            FanqieRankingBook(source_book_id="b1", title="第一本", rank=1),
        ],
    )

    assert snapshot.sample_size == 2
    assert snapshot.top_titles == ["第一本", "第二本"]


def test_ranking_book_rejects_invalid_rank() -> None:
    with pytest.raises(ValidationError, match="rank"):
        FanqieRankingBook(source_book_id="bad", title="坏数据", rank=0)


def test_category_profile_coerces_list_fields() -> None:
    profile = FanqieCategoryProfile(
        category="玄幻脑洞",
        data_date=date(2026, 5, 20),
        sample_size=3,
        dominant_settings="游戏化, 长生流\uff0c国家上交",
        confidence=0.82,
    )

    assert profile.dominant_settings == ["游戏化", "长生流", "国家上交"]
    assert profile.confidence == 0.82


def test_craft_profile_prompt_card_excludes_source_ids() -> None:
    profile = FanqieCraftProfile(
        category="都市脑洞",
        source_profile_ids=["profile-a"],
        allowed_style_principles=["短句推进", "高频对话"],
        disallowed_copy_targets=["禁止复刻具体书名、角色名、专属设定"],
        hook_rules=["首章先给可见危机"],
        pacing_rules=["每章一个反馈"],
        structure_rules=["设定必须服务反击循环"],
        sentence_style="短句, 少铺陈",
        paragraph_style="2-4 行一段",
        dialogue_ratio_hint=0.48,
        safety_boundary="只复用抽象结构和节奏, 不仿写在榜作者具体文风。",
    )

    card = profile.to_prompt_card()

    assert "source_profile_ids" not in card
    assert card["category"] == "都市脑洞"
    assert card["disallowed_copy_targets"]


def test_fanqie_market_artifact_types_exist() -> None:
    assert ArtifactType.FANQIE_MARKET_PROFILE == "fanqie_market_profile"
    assert ArtifactType.FANQIE_CRAFT_PROFILE == "fanqie_craft_profile"
    assert ArtifactType.FANQIE_ENTRY_CONTRACT == "fanqie_entry_contract"
    assert (
        ArtifactType.FANQIE_LONG_RANKING_READINESS
        == "fanqie_long_ranking_readiness"
    )
