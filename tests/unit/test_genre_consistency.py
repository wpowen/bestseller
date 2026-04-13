from __future__ import annotations

import pytest

from bestseller.services.genre_consistency import (
    GENRE_PROFILES,
    GenreConsistencyProfile,
    build_genre_constraint_block,
    extract_stat_block,
    get_cultivation_tier_index,
    get_genre_profile,
    validate_litrpg_skill_inventory,
    validate_litrpg_stats,
    validate_xianxia_progression,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# GenreConsistencyProfile
# ---------------------------------------------------------------------------

def test_profile_is_frozen() -> None:
    profile = GenreConsistencyProfile(progression_system="test")
    with pytest.raises(AttributeError):
        profile.progression_system = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# get_genre_profile
# ---------------------------------------------------------------------------

def test_get_xianxia_profile() -> None:
    profile = get_genre_profile("xianxia")
    assert profile is not None
    assert profile.progression_system == "cultivation_tiers"
    assert len(profile.tier_names) > 0


def test_get_litrpg_profile() -> None:
    profile = get_genre_profile("litrpg")
    assert profile is not None
    assert profile.progression_system == "stat_block"


def test_get_wuxia_profile() -> None:
    profile = get_genre_profile("wuxia")
    assert profile is not None
    assert profile.progression_system == "martial_arts_tiers"


def test_get_profile_by_sub_genre() -> None:
    profile = get_genre_profile("fantasy", sub_genre="xianxia-upgrade")
    assert profile is not None
    assert profile.progression_system == "cultivation_tiers"


def test_get_profile_partial_match() -> None:
    profile = get_genre_profile("cultivation-fantasy")
    assert profile is not None


def test_get_profile_unknown_genre_returns_none() -> None:
    assert get_genre_profile("romance") is None


# ---------------------------------------------------------------------------
# Xianxia cultivation validation
# ---------------------------------------------------------------------------

def test_cultivation_tier_index() -> None:
    tiers = ("炼气", "筑基", "金丹", "元婴", "化神")
    assert get_cultivation_tier_index("炼气期", tiers) == 0
    assert get_cultivation_tier_index("金丹境", tiers) == 2
    assert get_cultivation_tier_index("unknown", tiers) == -1


def test_xianxia_progression_valid() -> None:
    profile = GENRE_PROFILES["xianxia"]
    warnings = validate_xianxia_progression(
        "沈渊", "金丹期", "筑基后期", profile.tier_names,
    )
    assert len(warnings) == 0


def test_xianxia_progression_regression_detected() -> None:
    profile = GENRE_PROFILES["xianxia"]
    warnings = validate_xianxia_progression(
        "沈渊", "炼气初期", "金丹中期", profile.tier_names,
    )
    assert len(warnings) == 1
    assert "修为回退" in warnings[0]


def test_xianxia_progression_unknown_tiers_ignored() -> None:
    profile = GENRE_PROFILES["xianxia"]
    warnings = validate_xianxia_progression(
        "沈渊", "unknown_tier", "also_unknown", profile.tier_names,
    )
    assert len(warnings) == 0


def test_xianxia_same_level_no_warning() -> None:
    profile = GENRE_PROFILES["xianxia"]
    warnings = validate_xianxia_progression(
        "沈渊", "金丹中期", "金丹初期", profile.tier_names,
    )
    assert len(warnings) == 0  # Same tier, no regression


# ---------------------------------------------------------------------------
# LitRPG stat validation
# ---------------------------------------------------------------------------

def test_extract_stat_block() -> None:
    text = "Level: 5\nSTR: 12\nVIT: 8\nAGI: 15"
    stats = extract_stat_block(text)
    assert stats["LEVEL"] == 5
    assert stats["STR"] == 12
    assert stats["VIT"] == 8
    assert stats["AGI"] == 15


def test_extract_stat_block_chinese_colons() -> None:
    text = "STR：20\nHP：150"
    stats = extract_stat_block(text)
    assert stats["STR"] == 20
    assert stats["HP"] == 150


def test_extract_stat_block_empty_text() -> None:
    assert extract_stat_block("") == {}


def test_validate_litrpg_stats_progression() -> None:
    current = {"STR": 15, "VIT": 10}
    previous = {"STR": 12, "VIT": 8}
    warnings = validate_litrpg_stats(current, previous, "Theron")
    assert len(warnings) == 0


def test_validate_litrpg_stats_regression() -> None:
    current = {"STR": 10, "VIT": 12}
    previous = {"STR": 15, "VIT": 8}
    warnings = validate_litrpg_stats(current, previous, "Theron")
    assert len(warnings) == 1
    assert "STR" in warnings[0]
    assert "数值回退" in warnings[0]


def test_validate_litrpg_stats_new_stat_no_warning() -> None:
    current = {"STR": 10, "LCK": 5}
    previous = {"STR": 8}
    warnings = validate_litrpg_stats(current, previous)
    assert len(warnings) == 0


# ---------------------------------------------------------------------------
# LitRPG skill inventory
# ---------------------------------------------------------------------------

def test_skill_inventory_valid() -> None:
    current = ["Fireball", "Shield", "Heal"]
    previous = ["Fireball", "Shield"]
    warnings = validate_litrpg_skill_inventory(current, previous, "Vael")
    assert len(warnings) == 0


def test_skill_inventory_removal_detected() -> None:
    current = ["Fireball"]
    previous = ["Fireball", "Shield", "Heal"]
    warnings = validate_litrpg_skill_inventory(current, previous, "Vael")
    assert len(warnings) == 1
    assert "技能消失" in warnings[0]
    assert "Shield" in warnings[0] or "Heal" in warnings[0]


# ---------------------------------------------------------------------------
# build_genre_constraint_block
# ---------------------------------------------------------------------------

def test_genre_constraint_block_xianxia_zh() -> None:
    profile = GENRE_PROFILES["xianxia"]
    states = {
        "沈渊": {"cultivation_level": "金丹中期"},
        "李师妹": {"cultivation_level": "筑基后期"},
    }
    block = build_genre_constraint_block(profile, states, language="zh-CN")
    assert "修仙境界约束" in block
    assert "沈渊" in block
    assert "金丹中期" in block
    assert "只能提升" in block


def test_genre_constraint_block_litrpg_en() -> None:
    profile = GENRE_PROFILES["litrpg"]
    states = {
        "Theron": {"stats": {"STR": 15, "VIT": 10}},
    }
    block = build_genre_constraint_block(profile, states, language="en")
    assert "LITRPG" in block
    assert "STR=15" in block


def test_genre_constraint_block_empty_states() -> None:
    profile = GENRE_PROFILES["xianxia"]
    assert build_genre_constraint_block(profile, {}) == ""


def test_genre_constraint_block_xianxia_en() -> None:
    profile = GENRE_PROFILES["xianxia"]
    states = {"Chen Wei": {"cultivation_level": "Core Formation"}}
    block = build_genre_constraint_block(profile, states, language="en")
    assert "CULTIVATION TIER CONSTRAINTS" in block
    assert "Chen Wei" in block
