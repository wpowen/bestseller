"""Unit tests for the Facet Review Blender service."""

from __future__ import annotations

import pytest

from bestseller.domain.facets import StoryFacets
from bestseller.services.facet_review_blender import (
    blend_review_weights,
    build_facet_review_profile,
)
from bestseller.services.genre_review_profiles import GenreReviewProfile, GenreReviewWeights


class TestBlendReviewWeights:
    """Tests for dynamic review weight blending."""

    def test_default_weights_when_no_config(self) -> None:
        """Facets with no matching weight configs should return defaults."""
        facets = StoryFacets(
            primary_genre="unknown-genre",
            language="zh-CN",
            tone="nonexistent-tone",
            narrative_drive="nonexistent-drive",
        )
        weights = blend_review_weights(facets)
        assert isinstance(weights, GenreReviewWeights)
        # Should be near baseline 1.0 since no deltas applied
        assert weights.conflict == 1.0
        assert weights.emotion == 1.0

    def test_progression_drive_boosts_conflict(self) -> None:
        facets = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="epic",
            narrative_drive="progression",
        )
        weights = blend_review_weights(facets)
        # Progression should boost conflict (delta +0.3)
        assert weights.conflict > 1.0
        assert weights.hook_strength > 1.0

    def test_cozy_tone_reduces_conflict(self) -> None:
        facets = StoryFacets(
            primary_genre="fantasy",
            language="en",
            tone="cozy",
            narrative_drive="base-building",
        )
        weights = blend_review_weights(facets)
        # Cozy should reduce conflict (delta -0.3)
        assert weights.conflict < 1.0
        # Cozy should boost emotion (delta +0.3)
        assert weights.emotion > 1.0

    def test_dark_tone_boosts_conflict(self) -> None:
        facets = StoryFacets(
            primary_genre="thriller",
            language="en",
            tone="dark",
            narrative_drive="mystery",
        )
        weights = blend_review_weights(facets)
        # Dark + mystery should boost hooks
        assert weights.hook_strength > 1.0

    def test_relationship_drive_boosts_emotion(self) -> None:
        facets = StoryFacets(
            primary_genre="romance",
            language="en",
            tone="lighthearted",
            narrative_drive="relationship",
        )
        weights = blend_review_weights(facets)
        assert weights.emotion > 1.0
        assert weights.emotional_movement > 1.0
        assert weights.dialogue > 1.0

    def test_weights_are_clamped(self) -> None:
        """Even with extreme deltas, weights should be within bounds."""
        facets = StoryFacets(
            primary_genre="romance",
            language="en",
            tone="cozy",
            narrative_drive="relationship",
            relationship_mode="slow-burn",
        )
        weights = blend_review_weights(facets)
        for field_name in GenreReviewWeights.model_fields:
            value = getattr(weights, field_name)
            assert 0.2 <= value <= 2.5, f"{field_name} = {value} out of bounds"

    def test_combination_produces_unique_profile(self) -> None:
        """Different facet combinations should produce different weights."""
        facets_action = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="epic",
            narrative_drive="progression",
        )
        facets_cozy = StoryFacets(
            primary_genre="fantasy",
            language="en",
            tone="cozy",
            narrative_drive="relationship",
        )
        weights_action = blend_review_weights(facets_action)
        weights_cozy = blend_review_weights(facets_cozy)

        # These should be meaningfully different
        assert weights_action.conflict != weights_cozy.conflict
        assert weights_action.emotion != weights_cozy.emotion


class TestBuildFacetReviewProfile:
    """Tests for building complete GenreReviewProfile from facets."""

    def test_returns_valid_profile(self) -> None:
        facets = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="dark",
            narrative_drive="progression",
        )
        profile = build_facet_review_profile(facets)
        assert isinstance(profile, GenreReviewProfile)
        assert "facet-blended" in profile.category_key
        assert "xianxia" in profile.name.lower()

    def test_profile_has_blended_weights(self) -> None:
        facets = StoryFacets(
            primary_genre="romance",
            language="en",
            tone="cozy",
            narrative_drive="relationship",
        )
        profile = build_facet_review_profile(facets)
        # Should have the blended weights, not default 1.0
        assert profile.scene_weights.emotion > 1.0
        assert profile.scene_weights.conflict < 1.0
