from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003, E501 — Chinese test fixtures.
import json

import pytest

from bestseller.services import conception as conception_services
from bestseller.services.concept_lab import build_concept_lab_catalog
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


def test_build_genre_context_synthesizes_custom_picker_key() -> None:
    # Regression: the free taxonomy picker yields a synthetic key like
    # custom-xuanhuan absent from the 62-card registry. _build_genre_context
    # must synthesise (not raise "Unknown genre_key") so book creation/conception
    # doesn't crash. Threaded genre/sub_genre keep full fidelity.
    ctx = conception_services._build_genre_context(
        "custom-apocalypse", 120, genre="末世", sub_genre="天灾囤货"
    )
    assert ctx["genre_key"] == "custom-apocalypse"
    assert ctx["genre"] == "末世"
    assert ctx["sub_genre"] == "天灾囤货"

    # Even without threaded genre/sub_genre it derives from the canonical key.
    ctx2 = conception_services._build_genre_context("custom-xuanhuan", 120)
    assert ctx2["genre"] == "玄幻"


def test_synthesize_genre_preset_and_get_preset_fallback() -> None:
    from bestseller.services.writing_presets import (
        get_genre_preset,
        synthesize_genre_preset,
    )

    p = synthesize_genre_preset("custom-apocalypse", genre="末世", sub_genre="天灾囤货")
    assert p.genre == "末世" and p.sub_genre == "天灾囤货"
    assert p.prompt_pack_key == "apocalypse-supply-chain"
    # get_genre_preset returns a best-effort preset for custom-* keys (not None).
    assert get_genre_preset("custom-xuanhuan") is not None
    assert get_genre_preset("nonexistent-key") is None


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


def test_commercial_brief_prompt_includes_concept_lab_contract() -> None:
    bundle = build_concept_lab_catalog("apocalypse-supply", count=1).bundles[0]

    block = conception_services._commercial_brief_prompt_block(
        {
            "language": "zh-CN",
            "concept_lab": bundle.model_dump(mode="json"),
        }
    )

    assert "已选脑洞组合合同" in block
    assert bundle.reader_promise in block
    assert "per_chapter_contract" in block


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


# --- P2: LLM platform-title revision wiring (2026-06-03) --------------------

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def _revision_title_profile() -> dict:
    return {
        "language": "zh-CN",
        "primary_title": "宗门案卷规则破局录",
        "primary_category": "玄幻",
        "secondary_category": "修真·复仇·宗门权谋",
        "tags": ["复仇", "宗门", "逆袭"],
        "logline": "废柴弟子被逐出宗门，靠一缕残魂逆命登天，向背叛者复仇。",
        "reader_promise": "越被打压越爽的逆袭复仇",
        "main_characters": [{"name": "萧烬", "identity": "被逐废柴弟子"}],
    }


def _weak_candidate(
    title: str, decision: str = "revise", pattern: str = "当前主书名校准"
) -> dict:
    return {
        "title": title,
        "pattern": pattern,
        "title_evaluation": {"decision": decision, "feedback": {}},
    }


def test_maybe_revise_adopts_valid_llm_revision(monkeypatch) -> None:
    settings = SimpleNamespace(
        generation=SimpleNamespace(title_llm_revision_enabled=True)
    )

    async def fake_complete_text(session, settings, request):
        return SimpleNamespace(content="《逆命焚骨录》", llm_run_id=None)

    monkeypatch.setattr(conception_services, "complete_text", fake_complete_text)
    title, was_revised, _ = asyncio.run(
        conception_services._maybe_revise_platform_title(
            None,
            settings,
            title_profile=_revision_title_profile(),
            primary_candidate=_weak_candidate("宗门案卷规则破局录"),
            target_platform="qimao",
            workflow_title="宗门案卷规则破局录",
        )
    )
    assert was_revised
    assert title == "逆命焚骨录"


def test_maybe_revise_skips_clean_ip_name(monkeypatch) -> None:
    calls = {"n": 0}

    async def fake_complete_text(session, settings, request):
        calls["n"] += 1
        return SimpleNamespace(content="改写了", llm_run_id=None)

    monkeypatch.setattr(conception_services, "complete_text", fake_complete_text)
    settings = SimpleNamespace(
        generation=SimpleNamespace(title_llm_revision_enabled=True)
    )
    title, was_revised, _ = asyncio.run(
        conception_services._maybe_revise_platform_title(
            None,
            settings,
            title_profile={**_revision_title_profile(), "primary_title": "烬骨登天录"},
            primary_candidate=_weak_candidate("烬骨登天录"),
            target_platform="qimao",
            workflow_title="烬骨登天录",
        )
    )
    assert not was_revised
    assert title == "烬骨登天录"
    assert calls["n"] == 0  # clean IP name must not trigger an LLM call


def test_maybe_revise_respects_disable_flag(monkeypatch) -> None:
    calls = {"n": 0}

    async def fake_complete_text(session, settings, request):
        calls["n"] += 1
        return SimpleNamespace(content="改写了", llm_run_id=None)

    monkeypatch.setattr(conception_services, "complete_text", fake_complete_text)
    settings = SimpleNamespace(
        generation=SimpleNamespace(title_llm_revision_enabled=False)
    )
    title, was_revised, _ = asyncio.run(
        conception_services._maybe_revise_platform_title(
            None,
            settings,
            title_profile=_revision_title_profile(),
            primary_candidate=_weak_candidate("宗门案卷规则破局录"),
            target_platform="qimao",
            workflow_title="宗门案卷规则破局录",
        )
    )
    assert not was_revised
    assert title == "宗门案卷规则破局录"
    assert calls["n"] == 0


# ── Cross-book naming de-dup (Front A: stop recurring 陆沉/宁尘) ───────────


def test_naming_constraint_block_zh_lists_cliche_and_avoid_names() -> None:
    block = conception_services._naming_constraint_block(
        {"avoid_names": ["陈屿", "谢迟"]}, is_en=False
    )
    # static blocklist surfaces the worst offenders
    assert "陆沉" in block
    assert "宁尘" in block
    # dynamic cross-book list is injected
    assert "陈屿" in block and "谢迟" in block
    assert "硬约束" in block


def test_naming_constraint_block_en_only_emits_with_avoid_names() -> None:
    assert conception_services._naming_constraint_block({}, is_en=True) == ""
    block = conception_services._naming_constraint_block(
        {"avoid_names": ["Marcus Cole", "Rowan Ashford"]}, is_en=True
    )
    assert "Marcus Cole" in block and "Rowan Ashford" in block
    # the Chinese cliché list must not leak into English prompts
    assert "陆沉" not in block


def test_character_user_prompt_embeds_naming_constraints() -> None:
    ctx = {
        "genre": "都市修真",
        "sub_genre": "修仙2.0",
        "description": "灵气复苏的都市修真。",
        "chapter_count": 300,
        "avoid_names": ["陆沉"],
    }
    prompt = conception_services._character_user_prompt(ctx)
    assert "命名去重" in prompt
    assert "陆沉" in prompt


@pytest.mark.asyncio
async def test_recent_cast_names_strips_qualifiers_and_dedups() -> None:
    class _FakeScalarResult(list):
        pass

    class _FakeSession:
        async def scalars(self, _stmt: object) -> _FakeScalarResult:
            return _FakeScalarResult(
                [
                    "Rowan Ashford (18th daughter)",
                    "Rowan Ashford",  # duplicate after stripping qualifier
                    "陆沉",
                    "沈青崖（心魔）",
                    "沈青崖",  # duplicate
                    "",  # skipped
                    None,  # skipped
                    "这个名字实在是太长了根本不应该被当作人名收录进去啊真的很长",  # >24 chars, skipped
                ]
            )

    names = await conception_services._recent_cast_names(_FakeSession())  # type: ignore[arg-type]
    assert names == ["Rowan Ashford", "陆沉", "沈青崖"]


@pytest.mark.asyncio
async def test_recent_cast_names_is_failure_safe() -> None:
    class _BoomSession:
        async def scalars(self, _stmt: object) -> object:
            raise RuntimeError("db down")

    assert await conception_services._recent_cast_names(_BoomSession()) == []  # type: ignore[arg-type]


async def test_polish_blurb_synopsis_rewrites_and_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """聚焦简介打磨：成功路径返回重写正文；LLM 异常时 fail-open 返回原简介。"""
    from uuid import uuid4

    from bestseller.services.llm import LLMCompletionResult

    async def fake_ok(session, settings, request):
        # 复刻 conception 重生：返回一段更强的点击简介
        return LLMCompletionResult(
            content="退婚那天他被裁了，房贷手术费同时砸下来——这一次他要拿回所有人欠他的体面。",
            provider="fake", model_name="fake", llm_run_id=uuid4(),
        )

    monkeypatch.setattr(conception_services, "complete_text", fake_ok)
    syn, rid = await conception_services._polish_blurb_synopsis(
        None, None, synopsis="旧的长设定简介……", feedback="补首句钩+情绪前置",
        genre="现实", sub_genre="现实百态", is_en=False, language="zh-CN",
    )
    assert "退婚" in syn and "旧的长设定" not in syn
    assert rid is not None

    async def fake_raise(session, settings, request):
        raise RuntimeError("llm down")

    monkeypatch.setattr(conception_services, "complete_text", fake_raise)
    syn2, rid2 = await conception_services._polish_blurb_synopsis(
        None, None, synopsis="原简介保留", feedback="x",
        genre="现实", sub_genre="现实百态", is_en=False, language="zh-CN",
    )
    assert syn2 == "原简介保留"  # fail-open
    assert rid2 is None
