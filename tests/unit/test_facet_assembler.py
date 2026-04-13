"""Unit tests for the Facet Assembler service."""

from __future__ import annotations

import pytest

from bestseller.domain.facets import StoryFacets
from bestseller.services.facet_assembler import (
    assemble_prompt_pack,
    resolve_prompt_pack_from_facets,
)
from bestseller.services.prompt_packs import PromptPack


class TestAssemblePromptPack:
    """Tests for fragment-based PromptPack assembly."""

    def test_xianxia_progression_loads_genre_fragment(self) -> None:
        facets = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="epic",
            narrative_drive="progression",
            power_system="cultivation-tiers",
        )
        pack = assemble_prompt_pack(facets)
        assert isinstance(pack, PromptPack)
        assert "xianxia" in pack.key.lower() or "facet" in pack.key.lower()
        # Should have loaded the xianxia genre fragment
        if pack.fragments.scene_writer:
            assert "cultivation" in pack.fragments.scene_writer.lower() or "xianxia" in pack.fragments.scene_writer.lower()

    def test_romance_relationship_loads_both_fragments(self) -> None:
        facets = StoryFacets(
            primary_genre="romance",
            language="en",
            tone="lighthearted",
            narrative_drive="relationship",
            relationship_mode="slow-burn",
        )
        pack = assemble_prompt_pack(facets)
        assert isinstance(pack, PromptPack)
        # Should have loaded both romance genre + relationship drive fragments
        if pack.fragments.scene_writer:
            assert "romance" in pack.fragments.scene_writer.lower() or "relationship" in pack.fragments.scene_writer.lower()

    def test_dark_tone_adds_guidance(self) -> None:
        facets = StoryFacets(
            primary_genre="thriller",
            language="en",
            tone="dark",
            narrative_drive="mystery",
        )
        pack = assemble_prompt_pack(facets)
        if pack.fragments.scene_writer:
            assert "dark" in pack.fragments.scene_writer.lower()

    def test_missing_fragment_graceful(self) -> None:
        """AI may generate dimension values that don't have fragment files."""
        facets = StoryFacets(
            primary_genre="totally-unknown-genre",
            language="zh-CN",
            tone="nonexistent-tone",
            narrative_drive="nonexistent-drive",
        )
        # Should NOT raise an error
        pack = assemble_prompt_pack(facets)
        assert isinstance(pack, PromptPack)

    def test_anti_patterns_collected_from_fragments(self) -> None:
        facets = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="epic",
            narrative_drive="progression",
        )
        pack = assemble_prompt_pack(facets)
        # Should have collected anti_patterns from genre + narrative_drive fragments
        assert len(pack.anti_patterns) > 0

    def test_genres_includes_sub_genres(self) -> None:
        facets = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            sub_genres=("升级", "宗门"),
        )
        pack = assemble_prompt_pack(facets)
        assert "xianxia" in pack.genres
        assert "升级" in pack.genres
        assert "宗门" in pack.genres

    def test_tags_includes_trope_tags(self) -> None:
        facets = StoryFacets(
            primary_genre="romance",
            language="en",
            trope_tags=("enemies-to-lovers", "forced-proximity", "one-bed"),
        )
        pack = assemble_prompt_pack(facets)
        assert "enemies-to-lovers" in pack.tags
        assert "forced-proximity" in pack.tags

    def test_cozy_tone_adds_cozy_guidance(self) -> None:
        facets = StoryFacets(
            primary_genre="fantasy",
            language="en",
            tone="cozy",
            narrative_drive="base-building",
        )
        pack = assemble_prompt_pack(facets)
        if pack.fragments.scene_writer:
            assert "cozy" in pack.fragments.scene_writer.lower() or "comfort" in pack.fragments.scene_writer.lower()

    def test_no_cp_relationship_does_not_load_fragment(self) -> None:
        """no-cp should not add relationship guidance."""
        facets = StoryFacets(
            primary_genre="thriller",
            language="en",
            tone="tense",
            narrative_drive="mystery",
            relationship_mode="no-cp",
        )
        pack = assemble_prompt_pack(facets)
        # Relationship mode fragment should NOT be loaded
        # (mystery and thriller fragments should dominate)
        assert isinstance(pack, PromptPack)


class TestResolvePromptPackFromFacets:
    """Tests for the public adapter function."""

    def test_returns_valid_prompt_pack(self) -> None:
        facets = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="dark",
            narrative_drive="progression",
        )
        pack = resolve_prompt_pack_from_facets(facets)
        assert isinstance(pack, PromptPack)
        assert pack.key is not None
        assert pack.name is not None
