from __future__ import annotations

import pytest

from bestseller.services import writing_presets as preset_services
from bestseller.services.writing_profile import resolve_writing_profile


pytestmark = pytest.mark.unit


def test_writing_preset_catalog_contains_rich_platform_genre_and_length_presets() -> None:
    catalog = preset_services.load_writing_preset_catalog()

    assert catalog.chapter_word_policy.min == 5000
    assert len(catalog.platform_presets) >= 7
    assert len(catalog.genre_presets) >= 27
    assert len(catalog.length_presets) >= 9


def test_infer_genre_preset_matches_apocalypse_supply_chain_keywords() -> None:
    preset = preset_services.infer_genre_preset("末日科幻", "重生囤货")

    assert preset is not None
    assert preset.key == "apocalypse-supply"
    assert preset.prompt_pack_key == "apocalypse-supply-chain"


def test_validate_longform_scope_rejects_total_word_count_below_minimum() -> None:
    with pytest.raises(ValueError, match="低于最低要求 5000 字"):
        preset_services.validate_longform_scope(4999, 1)


def test_validate_longform_scope_accepts_total_at_minimum() -> None:
    preset_services.validate_longform_scope(5000, 1)


def test_validate_longform_scope_rejects_average_chapter_word_count_below_floor() -> None:
    with pytest.raises(ValueError, match="每章最低 5000 字"):
        preset_services.validate_longform_scope(12000, 4)


def test_resolve_writing_profile_keeps_genre_presets_framework_level_only() -> None:
    profile = resolve_writing_profile(
        {"market": {"platform_target": "起点中文网"}},
        genre="仙侠升级",
        sub_genre="宗门逆袭",
        audience="升级成长向读者",
    )

    assert profile.market.platform_target == "起点中文网"
    assert profile.market.prompt_pack_key == "xianxia-upgrade-core"
    assert profile.market.selling_points == []
    assert not profile.character.protagonist_archetype
    assert not profile.character.golden_finger
    assert profile.world.rule_hardness == "hard"


def test_writing_preset_catalog_exposes_super_serial_length_stages() -> None:
    catalog = preset_services.load_writing_preset_catalog()

    assert any(item.key == "million-stage" for item in catalog.length_presets)
    assert any(item.key == "super-serial-unit" for item in catalog.length_presets)


def test_hot_genre_presets_are_sorted_by_trend_score() -> None:
    hot = preset_services.list_hot_genre_presets(limit=5)

    assert len(hot) == 5
    assert hot[0].trend_score >= hot[-1].trend_score
    assert hot[0].trend_score >= 90
    assert all(item.trend_summary for item in hot)
    assert any(item.key == "apocalypse-supply" for item in hot)


@pytest.mark.parametrize(
    "genre_key, expected_prompt_pack",
    [
        ("horror-tycoon", "suspense-mystery"),
        ("mecha-warfare", "scifi-starwar"),
        ("blacktech-techtree", "scifi-starwar"),
        ("game-retro-nostalgia", "game-esport"),
        ("exorcist-detective", "suspense-mystery"),
    ],
)
def test_editor_recommended_genre_presets_are_registered(
    genre_key: str, expected_prompt_pack: str
) -> None:
    preset = preset_services.get_genre_preset(genre_key)

    assert preset is not None, f"Missing genre preset: {genre_key}"
    assert preset.prompt_pack_key == expected_prompt_pack
    assert preset.language == "zh-CN"
    assert preset.target_word_options, "target_word_options must not be empty"
    assert preset.target_chapter_options, "target_chapter_options must not be empty"
    overrides = preset.writing_profile_overrides
    assert "market" in overrides and "character" in overrides
    assert overrides["market"].get("selling_points"), "selling_points required"


def test_infer_genre_preset_matches_horror_tycoon_keywords() -> None:
    preset = preset_services.infer_genre_preset("惊悚灵异", "诡豪神豪")

    assert preset is not None
    assert preset.key == "horror-tycoon"


def test_infer_genre_preset_matches_mecha_warfare_keywords() -> None:
    preset = preset_services.infer_genre_preset("科幻冒险", "机甲战争")

    assert preset is not None
    assert preset.key == "mecha-warfare"
