from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.methodology_cards import (
    load_methodology_cards,
    load_methodology_source_set,
    methodology_coverage_summary,
    validate_card_sources,
)
from bestseller.services.methodology_profile import (
    enabled_cards,
    gate_mode_for_card,
    load_methodology_profile,
    load_profile_deck,
    render_methodology_profile_block,
    validate_methodology_profile,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_SOURCE_DIR = REPO_ROOT / "data" / "methodology_sources" / "platform_character_debt"
PLATFORM_MANIFEST = PLATFORM_SOURCE_DIR / "manifest.yaml"
PLATFORM_CARDS = PLATFORM_SOURCE_DIR / "cards.yaml"
PLATFORM_PROFILE = REPO_ROOT / "config" / "methodology_profiles" / "platform_character_debt_v1.yaml"


def test_platform_character_debt_cards_load_with_new_categories() -> None:
    source_set = load_methodology_source_set(PLATFORM_MANIFEST)
    deck = load_methodology_cards(PLATFORM_CARDS)

    assert deck.get_card("platform.anchor_character_gene").category == "character"
    assert deck.get_card("platform.character_debt_ledger").category == "debt"
    assert deck.get_card("platform.relationship_hook_matrix").category == "relationship"
    assert deck.get_card("platform.hype_four_beat").category == "emotion_beat"
    assert validate_card_sources(deck, source_set) == ()


def test_platform_character_debt_coverage_is_complete() -> None:
    source_set = load_methodology_source_set(PLATFORM_MANIFEST)
    deck = load_methodology_cards(PLATFORM_CARDS)

    summary = methodology_coverage_summary(deck, source_set)

    assert summary["source_items"] == 8
    assert summary["verified_sources"] == 8
    assert summary["pending_sources"] == 0
    assert summary["cards"] == 8
    assert summary["covered_source_count"] == 8
    assert summary["uncovered_verified_source_ids"] == []
    assert summary["unknown_source_ids"] == []
    assert summary["verified_source_coverage_ratio"] == pytest.approx(1.0)


def test_platform_character_debt_profile_loads_and_renders_short_blocks() -> None:
    profile = load_methodology_profile(PLATFORM_PROFILE)
    deck = load_profile_deck(profile)

    assert validate_methodology_profile(profile, deck) == ()
    assert len(profile.cards) == 8
    assert gate_mode_for_card(profile, "platform.anchor_character_gene") == "block"
    assert gate_mode_for_card(profile, "platform.character_desire_collision") == "block"
    # 2026-07-04: block→warn（债务同质化结构性根因，台账降为建议级）
    assert gate_mode_for_card(profile, "platform.character_debt_ledger") == "warn"
    assert gate_mode_for_card(profile, "platform.triangle_conflict") == "warn"

    chapter_cards = enabled_cards(profile, deck, stage="planning", scope="chapter")
    chapter_ids = [card.id for card in chapter_cards]
    assert chapter_ids[0] == "platform.anchor_character_gene"
    assert "platform.character_debt_ledger" in chapter_ids
    assert "platform.hype_four_beat" in chapter_ids

    block = render_methodology_profile_block(
        profile,
        deck,
        stage="planning",
        scope="chapter",
        language="zh-CN",
        max_cards=3,
    )

    assert "方法论 profile：platform_character_debt_v1" in block
    assert "platform.anchor_character_gene" in block
    assert "必填合约" in block
    assert block.count("\n- ") == 3
