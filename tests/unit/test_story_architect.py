"""Unit tests for the Story Architect Agent."""

from __future__ import annotations

import json

import pytest

from bestseller.domain.facets import StoryFacets
from bestseller.services.concept_lab import build_concept_lab_catalog
from bestseller.services.story_architect import (
    _build_system_prompt,
    _build_user_prompt,
    _render_persona_spine_lock,
    _fallback_facets,
    _parse_architect_output,
)


class TestParseArchitectOutput:
    """Tests for parsing LLM output into StoryFacets."""

    def test_parses_clean_json(self) -> None:
        raw = json.dumps({
            "sub_genres": ["升级", "宗门"],
            "setting": "被时间裂缝侵蚀的废弃仙域",
            "tone": "bittersweet",
            "power_system": "cultivation-tiers",
            "relationship_mode": "no-cp",
            "narrative_drive": "mystery",
            "emotional_register": "intellectual",
            "trope_tags": ["遗迹探索", "上古传承", "师徒羁绊"],
            "platform_style": "qidian",
            "gender_channel": "male",
        })
        facets = _parse_architect_output(raw, "xianxia", "zh-CN")
        assert facets.primary_genre == "xianxia"
        assert facets.language == "zh-CN"
        assert facets.tone == "bittersweet"
        assert facets.narrative_drive == "mystery"
        assert "遗迹探索" in facets.trope_tags
        assert facets.generation_source == "ai"

    def test_parses_json_with_markdown_fences(self) -> None:
        raw = "```json\n" + json.dumps({
            "sub_genres": ["dark", "romance"],
            "setting": "Gothic mansion",
            "tone": "dark",
            "power_system": None,
            "relationship_mode": "enemies-to-lovers",
            "narrative_drive": "relationship",
            "emotional_register": "romantic-tension",
            "trope_tags": ["morally-grey", "forced-proximity"],
            "platform_style": "kindle-unlimited",
            "gender_channel": "female",
        }) + "\n```"
        facets = _parse_architect_output(raw, "dark-romance", "en")
        assert facets.primary_genre == "dark-romance"
        assert facets.tone == "dark"
        assert facets.relationship_mode == "enemies-to-lovers"

    def test_parses_json_with_extra_text(self) -> None:
        raw = "Here's your story genome:\n\n" + json.dumps({
            "sub_genres": ["cozy", "magic"],
            "setting": "A magical bakery",
            "tone": "cozy",
            "power_system": "magic-system",
            "relationship_mode": "found-family",
            "narrative_drive": "base-building",
            "emotional_register": "cozy",
            "trope_tags": ["crafting", "found-family"],
            "platform_style": "kindle-unlimited",
            "gender_channel": "neutral",
        }) + "\n\nHope this helps!"
        facets = _parse_architect_output(raw, "cozy-fantasy", "en")
        assert facets.tone == "cozy"
        assert facets.narrative_drive == "base-building"

    def test_raises_on_no_json(self) -> None:
        with pytest.raises(ValueError, match="No JSON"):
            _parse_architect_output("Just some text with no JSON", "xianxia", "zh-CN")

    def test_handles_missing_optional_fields(self) -> None:
        raw = json.dumps({
            "sub_genres": ["mystery"],
            "setting": "A dark city",
            "tone": "tense",
        })
        facets = _parse_architect_output(raw, "thriller", "en")
        assert facets.primary_genre == "thriller"
        assert facets.tone == "tense"
        # Missing fields should use defaults
        assert facets.narrative_drive == "progression"
        assert facets.relationship_mode == "no-cp"

    def test_sub_genres_capped_at_3(self) -> None:
        raw = json.dumps({
            "sub_genres": ["a", "b", "c", "d", "e"],
            "setting": "test",
            "tone": "dark",
            "trope_tags": [],
        })
        facets = _parse_architect_output(raw, "test", "en")
        assert len(facets.sub_genres) <= 3

    def test_trope_tags_capped_at_8(self) -> None:
        raw = json.dumps({
            "sub_genres": ["a"],
            "setting": "test",
            "tone": "dark",
            "trope_tags": ["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9", "t10"],
        })
        facets = _parse_architect_output(raw, "test", "en")
        assert len(facets.trope_tags) <= 8


class TestFallbackFacets:
    """Tests for the fallback mechanism when AI is unavailable."""

    def test_fallback_returns_legacy_facets(self) -> None:
        facets = _fallback_facets("xianxia-upgrade", "zh-CN")
        assert facets.primary_genre == "xianxia"
        assert facets.tone == "epic"
        assert facets.generation_source == "legacy"

    def test_fallback_for_unknown_key_returns_minimal(self) -> None:
        facets = _fallback_facets("completely-unknown-key", "en")
        assert facets.primary_genre == "completely-unknown-key"
        assert facets.language == "en"
        assert facets.generation_source == "legacy"

    def test_fallback_overrides_language(self) -> None:
        # xianxia-upgrade is zh-CN in legacy, but if we ask for en it should adapt
        facets = _fallback_facets("xianxia-upgrade", "en")
        assert facets.language == "en"


class TestBuildPrompts:
    """Tests for prompt building functions."""

    def test_system_prompt_chinese(self) -> None:
        prompt = _build_system_prompt("zh-CN")
        assert "故事建筑师" in prompt
        assert "JSON" in prompt

    def test_system_prompt_english(self) -> None:
        prompt = _build_system_prompt("en")
        assert "Story Architect" in prompt
        assert "JSON" in prompt

    def test_user_prompt_includes_genre(self) -> None:
        prompt = _build_user_prompt(
            primary_genre="xianxia",
            language="zh-CN",
            user_hints=None,
            existing_facets=[],
            trend_data={"trend_keywords": ["灵气复苏"], "trend_summary": "hot"},
            dimensions_summary="test dimensions",
        )
        assert "xianxia" in prompt
        assert "zh-CN" in prompt

    def test_user_prompt_includes_existing_facets(self) -> None:
        existing = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="epic",
            narrative_drive="progression",
            setting="荒古矿脉的签到少年",
            trope_tags=("废柴逆袭", "秘境"),
        )
        prompt = _build_user_prompt(
            primary_genre="xianxia",
            language="zh-CN",
            user_hints=None,
            existing_facets=[existing],
            trend_data={},
            dimensions_summary="test",
        )
        assert "differentiate" in prompt.lower() or "MUST" in prompt
        # Peers are shown by SURFACE (setting + trope combo), so the new book
        # differentiates the skin — NOT by copying/avoiding tone or drive.
        assert "荒古矿脉的签到少年" in prompt
        assert "废柴逆袭" in prompt

    def test_peer_section_tells_model_to_share_spine_not_differ_on_it(self) -> None:
        # Regression (cross-book contamination): the old Section-2 header
        # "MUST differentiate from these" + printing tone/drive pushed each new
        # same-genre book OFF-genre. The peer section must now scope
        # differentiation to the SURFACE and affirm spine-sharing.
        existing = StoryFacets(
            primary_genre="xianxia", language="zh-CN", tone="epic",
            narrative_drive="progression", setting="旧书设定",
            trope_tags=("废柴逆袭",),
        )
        prompt = _build_user_prompt(
            primary_genre="xianxia", language="zh-CN", user_hints=None,
            existing_facets=[existing], trend_data={}, dimensions_summary="t",
        )
        assert "SURFACE" in prompt
        assert "spine" in prompt.lower()
        # must NOT instruct differing on tone/drive
        assert "MUST differentiate from these" not in prompt

    def test_user_prompt_includes_user_hints(self) -> None:
        prompt = _build_user_prompt(
            primary_genre="romance",
            language="en",
            user_hints={"mood": "lighthearted", "avoid": "dark themes"},
            existing_facets=[],
            trend_data={},
            dimensions_summary="test",
        )
        assert "lighthearted" in prompt
        assert "dark themes" in prompt

    def test_user_prompt_renders_concept_lab_contract(self) -> None:
        bundle = build_concept_lab_catalog("apocalypse-supply", count=1).bundles[0]

        prompt = _build_user_prompt(
            primary_genre="apocalypse-supply",
            language="zh-CN",
            user_hints={
                "concept_lab": bundle.model_dump(mode="json"),
                "mood": "高压破局",
            },
            existing_facets=[],
            trend_data={},
            dimensions_summary="test",
        )

        assert "Selected Concept Lab Contract" in prompt
        assert "已选脑洞组合合同" in prompt
        assert bundle.reader_promise in prompt
        assert "per_chapter_contract" in prompt
        assert "高压破局" in prompt


class TestPersonaSpineLock:
    """The genre SPINE LOCK keeps differentiation from subverting the genre's
    load-bearing 爽点 spine (regression: 仙侠升级 drifted to 克系/无金手指)."""

    def test_male_channel_spine_lock_mandates_payoff_and_forbids_literary_drift(self) -> None:
        block = _render_persona_spine_lock("仙侠升级", None, "zh-CN")
        assert "SPINE LOCK" in block
        assert "男频" in block
        # MUST keep the power-fantasy payoff spine
        assert "金手指" in block
        # MUST forbid exactly the drift we observed (no golden-finger / literary tone)
        assert "power_system=null" in block
        assert ("文艺" in block or "道德灰色" in block or "melancholic" in block)
        # differentiation is surface-only, not spine-breaking
        assert "表皮" in block

    def test_female_channel_spine_is_emotional_not_power_fantasy(self) -> None:
        block = _render_persona_spine_lock("现代言情", {"audience_orientation": "女频"}, "zh-CN")
        assert "女频" in block
        # female spine is emotional payoff, not 金手指 power-fantasy
        assert ("情感" in block or "重生" in block or "打脸渣" in block or "偏爱" in block)

    def test_spine_lock_wired_into_user_prompt(self) -> None:
        prompt = _build_user_prompt(
            primary_genre="仙侠升级",
            language="zh-CN",
            user_hints=None,
            existing_facets=[],
            trend_data={},
            dimensions_summary="test",
        )
        assert "SPINE LOCK" in prompt

    def test_system_prompt_constraints_are_surface_only(self) -> None:
        zh = _build_system_prompt("zh-CN")
        # the drift-driving wording "核心维度上做非主流选择" must be gone
        assert "核心维度上做非主流选择" not in zh
        assert "表皮" in zh  # 反套路 now scoped to surface
        en = _build_system_prompt("en")
        assert "SURFACE only" in en


class TestStoryFacetsSimilarity:
    """Tests for the similarity scoring mechanism."""

    def test_identical_facets_score_1(self) -> None:
        facets = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="epic",
            narrative_drive="progression",
            trope_tags=("废柴逆袭", "秘境"),
        )
        assert facets.similarity_score(facets) == pytest.approx(1.0)

    def test_different_facets_score_low(self) -> None:
        facets_a = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="epic",
            narrative_drive="progression",
            relationship_mode="no-cp",
            emotional_register="power-fantasy",
            power_system="cultivation-tiers",
            sub_genres=("升级", "宗门"),
            trope_tags=("废柴逆袭", "秘境", "打脸"),
        )
        facets_b = StoryFacets(
            primary_genre="romance",
            language="en",
            tone="cozy",
            narrative_drive="relationship",
            relationship_mode="enemies-to-lovers",
            emotional_register="romantic-tension",
            power_system=None,
            sub_genres=("contemporary", "slow-burn"),
            trope_tags=("enemies-to-lovers", "forced-proximity", "one-bed"),
        )
        score = facets_a.similarity_score(facets_b)
        assert score < 0.15

    def test_partially_similar_facets(self) -> None:
        facets_a = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="epic",
            narrative_drive="progression",
            trope_tags=("废柴逆袭", "秘境", "打脸"),
        )
        facets_b = StoryFacets(
            primary_genre="xianxia",
            language="zh-CN",
            tone="dark",
            narrative_drive="progression",
            trope_tags=("废柴逆袭", "师徒", "规则怪谈"),
        )
        score = facets_a.similarity_score(facets_b)
        # Should be moderate (same drive, partial tag overlap, different tone)
        assert 0.3 < score < 0.8

    def test_same_genre_shared_spine_distinct_setting_is_not_duplicate(self) -> None:
        """CORE contamination regression: two 男频金手指爽文 that share the genre
        spine (same tone/drive/core payoff tropes) but have distinct concrete
        settings must score BELOW the anti-repetition threshold — otherwise the
        second book gets forced off-genre to look 'different' (the drift bug)."""
        from bestseller.services.story_architect import _MAX_SIMILARITY_THRESHOLD

        book_a = StoryFacets(
            primary_genre="xianxia-upgrade", language="zh-CN",
            tone="lighthearted", narrative_drive="face-slap",
            relationship_mode="no-cp", emotional_register="power-fantasy",
            power_system="sign-in-system",
            sub_genres=("升级", "宗门"),
            setting="破败矿脉小城的扫地少年，每日签到觉醒逆天奖励",
            trope_tags=("废柴逆袭", "金手指", "打脸", "扮猪吃虎", "签到变强"),
        )
        book_b = StoryFacets(
            primary_genre="xianxia-upgrade", language="zh-CN",
            tone="lighthearted", narrative_drive="face-slap",
            relationship_mode="no-cp", emotional_register="power-fantasy",
            power_system="word-slot-system",
            sub_genres=("升级", "宗门"),
            setting="被逐出师门的赘婿，靠词条系统一步步夺回一切",
            trope_tags=("废柴逆袭", "金手指", "打脸", "越级反杀", "词条流"),
        )
        score = book_a.similarity_score(book_b)
        # shared spine + partial trope overlap but distinct setting → NOT a dup
        assert score < _MAX_SIMILARITY_THRESHOLD, score

    def test_same_setting_and_premise_is_flagged_as_duplicate(self) -> None:
        """The detector must still catch a genuinely copied premise."""
        from bestseller.services.story_architect import _MAX_SIMILARITY_THRESHOLD

        base = StoryFacets(
            primary_genre="xianxia-upgrade", language="zh-CN",
            tone="lighthearted", narrative_drive="face-slap",
            power_system="sign-in-system", sub_genres=("升级", "宗门"),
            setting="破败矿脉小城的扫地少年，每日签到觉醒逆天奖励",
            trope_tags=("废柴逆袭", "金手指", "打脸", "签到变强"),
        )
        near_clone = base.model_copy(update={
            "trope_tags": ("废柴逆袭", "金手指", "打脸", "签到暴富"),
        })
        assert base.similarity_score(near_clone) >= _MAX_SIMILARITY_THRESHOLD
