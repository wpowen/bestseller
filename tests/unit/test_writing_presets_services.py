from __future__ import annotations

import pytest

from bestseller.services import writing_presets as preset_services
from bestseller.services.writing_profile import resolve_writing_profile


pytestmark = pytest.mark.unit


def test_writing_preset_catalog_contains_rich_platform_genre_and_length_presets() -> None:
    catalog = preset_services.load_writing_preset_catalog()

    assert catalog.chapter_word_policy.min == 5000
    assert len(catalog.platform_presets) >= 7
    assert len(catalog.genre_presets) >= 22
    assert len(catalog.length_presets) >= 9


def test_infer_genre_preset_matches_apocalypse_supply_chain_keywords() -> None:
    preset = preset_services.infer_genre_preset("末日科幻", "重生囤货")

    assert preset is not None
    assert preset.key == "apocalypse-supply"
    assert preset.prompt_pack_key == "apocalypse-supply-chain"


def test_validate_longform_scope_rejects_average_chapter_word_count_below_floor() -> None:
    with pytest.raises(ValueError, match="每章最低 5000 字"):
        preset_services.validate_longform_scope(12000, 4)


def test_resolve_writing_profile_merges_platform_and_genre_presets() -> None:
    profile = resolve_writing_profile(
        {"market": {"platform_target": "起点中文网"}},
        genre="仙侠升级",
        sub_genre="宗门逆袭",
        audience="升级成长向读者",
    )

    assert profile.market.platform_target == "起点中文网"
    assert profile.market.prompt_pack_key == "xianxia-upgrade-core"
    assert "境界升级" in profile.market.selling_points
    assert "升级突破" in profile.market.selling_points
    assert "世界地图扩张" in profile.market.selling_points
    assert profile.character.protagonist_archetype == "逆袭型成长主角"


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
