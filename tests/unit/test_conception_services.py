from __future__ import annotations

import json

import pytest

from bestseller.services import conception as conception_services


pytestmark = pytest.mark.unit


def test_ensure_complete_profile_uses_english_defaults_for_english_projects() -> None:
    profile = conception_services._ensure_complete_profile(
        {},
        {
            "genre": "Fantasy",
            "sub_genre": "Epic Fantasy",
            "language": "en-US",
            "existing_overrides": {},
        },
        {},
        {},
        {},
    )

    assert profile["serialization"]["opening_mandate"].startswith("Reveal the protagonist edge")
    assert profile["serialization"]["chapter_ending_rule"].startswith("Every chapter ends")
    assert "前3章" not in profile["serialization"]["opening_mandate"]


def test_build_fallback_final_uses_english_premise_and_profile_defaults() -> None:
    payload = json.loads(
        conception_services._build_fallback_final(
            {
                "genre": "Fantasy",
                "sub_genre": "Epic Fantasy",
                "description": "A hunted archivist steals the ledger that can expose a dead dynasty.",
                "language": "en-US",
            },
            {},
            {},
            {},
        )
    )

    assert payload["premise"].startswith("A Fantasy (Epic Fantasy) novel:")
    assert payload["writing_profile"]["serialization"]["chapter_ending_rule"].startswith(
        "Every chapter"
    )
    assert "基于" not in payload["premise"]


def test_build_genre_context_sanitizes_story_content_overrides() -> None:
    ctx = conception_services._build_genre_context("apocalypse-supply", 120)

    market = ctx["existing_overrides"].get("market", {})
    character = ctx["existing_overrides"].get("character", {})

    assert market.get("pacing_profile") == "fast"
    assert "reader_promise" not in market
    assert "trope_keywords" not in market
    assert character == {}


def test_apply_commercial_brief_merges_market_and_style_signals() -> None:
    profile = {
        "market": {
            "platform_target": "番茄小说",
            "selling_points": ["原有卖点"],
        },
        "style": {
            "reference_works": ["旧参考"],
            "custom_rules": ["已有规则"],
        },
    }
    brief = {
        "platform_target": "起点中文网",
        "reader_promise": "每章都有即时爽点和更大危机。",
        "selling_points": ["原有卖点", "升级反杀"],
        "trope_keywords": ["重生囤货"],
        "hook_keywords": ["倒计时"],
        "benchmark_works": ["全球高武"],
        "taboo_topics": ["拖沓开局"],
        "commercial_rationale": "优先保证前三章留存。",
    }

    merged = conception_services._apply_commercial_brief_to_profile(profile, brief)

    assert merged["market"]["platform_target"] == "番茄小说"
    assert merged["market"]["reader_promise"] == "每章都有即时爽点和更大危机。"
    assert merged["market"]["selling_points"] == ["原有卖点", "升级反杀"]
    assert merged["market"]["trope_keywords"] == ["重生囤货"]
    assert merged["style"]["reference_works"] == ["旧参考", "全球高武"]
    assert "拖沓开局" in merged["style"]["taboo_topics"]
    assert "优先保证前三章留存。" in merged["style"]["custom_rules"]
