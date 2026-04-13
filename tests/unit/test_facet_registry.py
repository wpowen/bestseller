"""Unit tests for the Facet Registry service."""

from __future__ import annotations

import pytest

from bestseller.domain.facets import FacetDimensionsCatalog, StoryFacets
from bestseller.services.facet_registry import (
    expand_legacy_preset,
    expand_legacy_preset_with_variation,
    get_dimensions_summary_for_ai,
    get_trend_data_for_genre,
    load_facet_dimensions,
    validate_story_facets,
)


class TestLoadFacetDimensions:
    """Tests for dimension catalog loading."""

    def test_loads_catalog_successfully(self) -> None:
        catalog = load_facet_dimensions()
        assert isinstance(catalog, FacetDimensionsCatalog)
        assert len(catalog.dimensions) > 0

    def test_has_required_dimensions(self) -> None:
        catalog = load_facet_dimensions()
        dimension_names = {d.name for d in catalog.dimensions}
        expected = {
            "primary_genre", "tone", "narrative_drive",
            "power_system", "relationship_mode", "emotional_register",
            "trope_tags", "gender_channel", "platform_style",
        }
        assert expected.issubset(dimension_names)

    def test_primary_genre_has_values(self) -> None:
        catalog = load_facet_dimensions()
        genre_dim = catalog.get_dimension("primary_genre")
        assert genre_dim is not None
        assert len(genre_dim.values) > 10  # Should have many genres

    def test_get_valid_keys_returns_set(self) -> None:
        catalog = load_facet_dimensions()
        tone_keys = catalog.get_valid_keys("tone")
        assert "dark" in tone_keys
        assert "cozy" in tone_keys
        assert "epic" in tone_keys

    def test_get_hot_tags(self) -> None:
        catalog = load_facet_dimensions()
        hot_tags = catalog.get_hot_tags("trope_tags", top_n=5)
        assert len(hot_tags) == 5
        # Hot tags should be ordered by heat_score descending

    def test_get_dimension_returns_none_for_unknown(self) -> None:
        catalog = load_facet_dimensions()
        assert catalog.get_dimension("nonexistent") is None


class TestValidateStoryFacets:
    """Tests for StoryFacets validation."""

    def test_valid_facets_no_warnings(self) -> None:
        facets = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="dark",
            narrative_drive="progression",
        )
        warnings = validate_story_facets(facets)
        assert len(warnings) == 0

    def test_unknown_genre_produces_warning(self) -> None:
        facets = StoryFacets(
            primary_genre="totally-made-up-genre",
            language="zh-CN",
        )
        warnings = validate_story_facets(facets)
        assert any("primary_genre" in w for w in warnings)

    def test_unknown_tone_produces_warning(self) -> None:
        facets = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="nonexistent-tone",
        )
        warnings = validate_story_facets(facets)
        assert any("tone" in w for w in warnings)


class TestExpandLegacyPreset:
    """Tests for legacy preset expansion."""

    def test_expands_known_preset(self) -> None:
        facets = expand_legacy_preset("xianxia-upgrade")
        assert facets is not None
        assert facets.primary_genre == "xianxia"
        assert facets.tone == "epic"
        assert facets.narrative_drive == "progression"
        assert facets.power_system == "cultivation-tiers"
        assert facets.generation_source == "legacy"

    def test_returns_none_for_unknown_preset(self) -> None:
        result = expand_legacy_preset("nonexistent-preset-key")
        assert result is None

    def test_dark_romance_expansion(self) -> None:
        facets = expand_legacy_preset("dark-romance")
        assert facets is not None
        assert facets.primary_genre == "dark-romance"
        assert facets.tone == "dark"
        assert facets.relationship_mode == "enemies-to-lovers"
        assert "morally-grey" in facets.trope_tags

    def test_cozy_fantasy_expansion(self) -> None:
        facets = expand_legacy_preset("cozy-fantasy")
        assert facets is not None
        assert facets.tone == "cozy"
        assert facets.narrative_drive == "base-building"
        assert facets.relationship_mode == "found-family"

    def test_litrpg_expansion(self) -> None:
        facets = expand_legacy_preset("litrpg-progression")
        assert facets is not None
        assert facets.power_system == "litrpg-stats"
        assert facets.narrative_drive == "progression"
        assert "isekai" in facets.trope_tags or "dungeon-core" in facets.trope_tags

    def test_variation_produces_different_tags(self) -> None:
        """Multiple variations should not all be identical."""
        results = set()
        for _ in range(10):
            facets = expand_legacy_preset_with_variation("xianxia-upgrade")
            assert facets is not None
            results.add(facets.trope_tags)
        # With 10 attempts, we should get at least 2 different tag sets
        assert len(results) >= 2

    def test_variation_preserves_core_fields(self) -> None:
        facets = expand_legacy_preset_with_variation("xianxia-upgrade")
        assert facets is not None
        assert facets.primary_genre == "xianxia"
        assert facets.tone == "epic"
        assert facets.narrative_drive == "progression"
        assert facets.generation_source == "legacy"


class TestGetTrendData:
    """Tests for trend data extraction."""

    def test_known_genre_returns_data(self) -> None:
        data = get_trend_data_for_genre("xianxia-upgrade")
        assert isinstance(data, dict)
        assert "trend_keywords" in data
        assert "trend_score" in data

    def test_unknown_genre_returns_defaults(self) -> None:
        data = get_trend_data_for_genre("nonexistent-genre")
        assert data["trend_keywords"] == []
        assert data["trend_score"] == 0


class TestGetDimensionsSummaryForAI:
    """Tests for AI-facing dimensions summary."""

    def test_returns_non_empty_string(self) -> None:
        summary = get_dimensions_summary_for_ai("zh-CN")
        assert len(summary) > 100
        assert "tone" in summary
        assert "narrative_drive" in summary

    def test_english_language_works(self) -> None:
        summary = get_dimensions_summary_for_ai("en")
        assert len(summary) > 100
