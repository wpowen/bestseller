from __future__ import annotations

from bestseller.services.genre_skill_profiles import (
    GENRE_SKILL_PROFILE_METADATA_KEY,
    GENRE_SKILL_PROFILE_VERSION,
    attach_genre_skill_profile,
    genre_skill_profile_from_metadata,
    resolve_genre_skill_profile,
)


def test_resolve_xianxia_profile_aggregates_existing_capabilities() -> None:
    profile = resolve_genre_skill_profile("玄幻", "升级")

    assert profile.version == GENRE_SKILL_PROFILE_VERSION
    assert profile.profile_key == "xianxia-upgrade-core"
    assert profile.prompt_pack_key == "xianxia-upgrade-core"
    assert "base-research-discipline" in profile.research_skill_keys
    assert "xianxia-upgrade" in profile.research_skill_keys
    assert profile.review_profile_key == "action-progression"
    assert profile.threshold_profile_key == "action-progression"
    assert profile.activation.gate_mode == "audit_only"
    assert profile.lineage_policy.selection_owner == "planner"
    assert profile.lineage_policy.downstream_policy == "consume_snapshot"


def test_resolve_suspense_profile_keeps_mystery_strategy_separate() -> None:
    profile = resolve_genre_skill_profile("悬疑", "民俗怪谈")

    assert profile.profile_key == "suspense-mystery"
    assert profile.prompt_pack_key == "suspense-mystery"
    assert "suspense-mystery" in profile.research_skill_keys
    assert profile.review_profile_key == "suspense-mystery"
    assert profile.threshold_profile_key == "suspense-mystery"


def test_resolve_urban_power_profile_routes_to_action_progression() -> None:
    profile = resolve_genre_skill_profile("都市高武", "异能升级")

    assert profile.profile_key == "urban-power-reversal"
    assert profile.prompt_pack_key == "urban-power-reversal"
    assert "urban-power" in profile.research_skill_keys
    assert profile.review_profile_key == "action-progression"
    assert profile.threshold_profile_key == "action-progression"


def test_attach_profile_is_immutable_and_legacy_metadata_stays_empty() -> None:
    legacy_metadata = {"prompt_pack_key": "suspense-mystery"}

    assert genre_skill_profile_from_metadata(legacy_metadata) is None

    profile = resolve_genre_skill_profile("悬疑", "民俗怪谈")
    updated = attach_genre_skill_profile(legacy_metadata, profile)

    assert GENRE_SKILL_PROFILE_METADATA_KEY not in legacy_metadata
    assert updated["prompt_pack_key"] == "suspense-mystery"
    assert updated[GENRE_SKILL_PROFILE_METADATA_KEY]["profile_key"] == "suspense-mystery"
    assert genre_skill_profile_from_metadata(updated) == profile
