from __future__ import annotations

import json

import pytest

from bestseller.services import conception as conception_services
from bestseller.services.writing_presets import get_platform_preset


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


def test_qimao_platform_preset_carries_regeneration_contract() -> None:
    preset = get_platform_preset("七猫小说")

    assert preset is not None
    market = preset.writing_profile_overrides["market"]
    serialization = preset.writing_profile_overrides["serialization"]
    assert market["platform_target"] == "七猫小说"
    assert "第一页" in market["reader_promise"]
    assert "普通日常" in market["opening_contract"]
    assert market["hook_deadline_words"] == 600
    assert "第1章立冲突" in serialization["first_three_chapter_goal"]


def test_ensure_complete_profile_applies_qimao_platform_preset() -> None:
    profile = conception_services._ensure_complete_profile(
        {},
        {
            "genre": "都市",
            "sub_genre": "都市逆袭",
            "language": "zh-CN",
            "default_platform": "七猫小说",
            "existing_overrides": {"market": {"platform_target": "七猫小说"}},
        },
        {},
        {},
        {},
    )

    assert profile["market"]["platform_target"] == "七猫小说"
    assert "普通日常" in profile["market"]["opening_contract"]
    assert profile["market"]["hook_deadline_words"] == 600
    assert "第1章立冲突" in profile["serialization"]["first_three_chapter_goal"]


def test_qimao_conception_prompt_includes_regeneration_contract() -> None:
    ctx = {
        "genre": "都市",
        "sub_genre": "都市逆袭",
        "description": "主角被诬陷后抓住一次翻身机会。",
        "language": "zh-CN",
        "chapter_count": 120,
        "recommended_platforms": ["七猫小说"],
        "recommended_audiences": ["移动端追读读者"],
        "trend_keywords": ["逆袭", "反打"],
        "trend_score": 80,
        "trend_summary": "强冲突开篇。",
        "default_platform": "七猫小说",
        "existing_overrides": {"market": {"platform_target": "七猫小说"}},
        "editor_rejection_reasons": "代入感较弱，故事的叙述较为平淡。",
    }

    prompt = conception_services._commercial_positioning_user_prompt(ctx)

    assert "七猫再生成合同" in prompt
    assert "这不是润色任务" in prompt
    assert "weak_immersion" in prompt


def test_conception_sanitizes_family_loss_default_motifs() -> None:
    payload = {
        "premise": "主角因为父亲失踪踏上修行路。",
        "writing_profile": {
            "character": {
                "protagonist_core_drive": "查清父母失踪真相并继承秘密。",
            },
            "market": {
                "hook_keywords": ["父亲失踪", "升级"],
            },
        },
    }

    sanitized = conception_services._sanitize_forbidden_default_motifs(payload, is_en=False)
    text = json.dumps(sanitized, ensure_ascii=False)

    assert "父亲失踪" not in text
    assert "父母失踪" not in text
    assert "由本书题材核心机制触发的具体危机与选择代价" in text


def test_conception_prompts_ban_fixed_family_loss_motivation() -> None:
    ctx = {
        "genre": "悬疑",
        "sub_genre": "规则悬疑",
        "description": "主角被卷入一座有规则的医院。",
        "language": "zh-CN",
        "chapter_count": 120,
        "recommended_platforms": ["番茄小说"],
        "recommended_audiences": ["移动端追读读者"],
        "trend_keywords": ["规则", "反转"],
        "trend_score": 85,
        "trend_summary": "强规则与强钩子。",
        "default_platform": "番茄小说",
        "existing_overrides": {},
    }

    prompt = conception_services._character_user_prompt(ctx)

    assert "默认动机禁用" in prompt
    assert "动态生成" in prompt
    assert "父母失踪" not in prompt
