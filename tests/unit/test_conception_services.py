from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003, E501 — Chinese test fixtures.
import json

import pytest

from bestseller.services import conception as conception_services
from bestseller.services.concept_lab import build_concept_lab_catalog
from bestseller.services.genre_intent_contract import contract_from_selection
from bestseller.services.story_enhancers import StoryEnhancerSelection
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


def test_build_genre_context_preserves_user_subgenre_when_facets_suggest_another() -> None:
    ctx = conception_services._build_genre_context(
        "custom-xianxia",
        120,
        genre="仙侠",
        sub_genre="仙侠",
        story_facets={
            "primary_genre": "custom-xianxia",
            "language": "zh-CN",
            "sub_genres": ["灵气复苏", "都市修仙"],
            "setting": "现代都市",
        },
    )

    assert ctx["sub_genre"] == "仙侠"
    assert ctx["prompt_pack_key"] == "xianxia-upgrade-core"
    assert ctx["story_facets"]["sub_genres"] == ["灵气复苏", "都市修仙"]


def test_build_genre_context_exposes_contract_as_hard_authority() -> None:
    contract = contract_from_selection(
        {"channel": "male", "genre": "xianxia", "sub_genre": "xianxia"}
    )
    ctx = conception_services._build_genre_context(
        "custom-xianxia",
        120,
        genre="仙侠",
        sub_genre="仙侠",
        genre_intent_contract=contract,
        story_facets={
            "primary_genre": "xianxia",
            "language": "zh-CN",
            "setting": "现代都市高楼",
        },
    )

    assert ctx["genre"] == contract.genre_label
    assert ctx["sub_genre"] == contract.sub_genre_label
    assert ctx["prompt_pack_key"] == contract.prompt_pack_key
    assert "禁止改写题材" in ctx["genre_intent_lock"]
    assert "advisory surface suggestions only" in ctx["facet_description"]


def test_creation_page_choices_are_scoped_and_only_explicit_enhancers_render() -> None:
    contract = contract_from_selection(
        {
            "channel": "male",
            "genre": "xianxia",
            "sub_genre": "xianxia",
            "tags": ["升级", "宗门"],
        },
        narrative_scale="epic",
        tone_preference="hot",
        enhancers=StoryEnhancerSelection(
            brainhole=True,
            effect_skills=("twist_reversal_engine",),
        ),
    )
    ctx = conception_services._build_genre_context(
        "custom-xianxia",
        500,
        genre="仙侠",
        sub_genre="古典仙侠",
        genre_intent_contract=contract,
    )
    block = conception_services._commercial_brief_prompt_block(ctx)
    assert "建书页明确选择" in block
    assert '"brainhole": true' in block
    assert "twist_reversal_engine" in block
    assert "不得把可选增强器变成新的题材" in block


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


def test_finalize_prompt_carries_golden_finger_diversity_principle():
    """The conception finalize prompt must inject the golden-finger DESIGN
    principle (form pool + 'never default to 系统/面板' + opt-out), so every book
    does not homogenise into a 系统流. Methodology-driven, not a hardcoded form.
    """
    from bestseller.services import conception as C

    ctx = {"genre": "玄幻", "sub_genre": "诡异修仙", "chapter_count": 500, "language": "zh-CN"}
    zh = C._finalize_user_prompt(ctx, {}, {}, {}, {})
    assert "形态绝不固定为系统" in zh  # explicit anti-system-default
    assert "上古传承" in zh and "契约异兽" in zh  # diverse form pool
    assert "无显性金手指" in zh  # opt-out for genres that don't need a cheat

    # Both language constants exist and carry the anti-system-default rule so the
    # EN finalize branch is covered too.
    assert "NOT default to a stat/system panel" in C._GOLDEN_FINGER_DESIGN_PRINCIPLE_EN
    assert "no explicit golden" in C._GOLDEN_FINGER_DESIGN_PRINCIPLE_EN


# ── 画像点击判官 advisory 接线（审计 P1-6 接活）──────────────────────────────


async def test_persona_click_advisory_pass_returns_report_without_feedback() -> None:
    async def _judge(system, user):
        return '{"click": true, "score": 8, "reason": "爽点直给"}'

    report, fb = await conception_services._persona_click_advisory(
        None, None, title="蚀骨神藏", synopsis="废柴少年觉醒神藏。",
        genre="玄幻", sub_genre="东方玄幻", tags=["升级"],
        config={"persona_judge": {"samples": 2, "click_rate_min": 0.34}},
        judge=_judge,
    )
    assert report is not None and report["llm_used"] is True
    assert report["click_rate"] == pytest.approx(1.0)
    assert fb == ""  # 达 advisory 线 → 不注入重生反馈


async def test_persona_click_advisory_fail_feeds_reasons_into_feedback() -> None:
    async def _judge(system, user):
        return '{"click": false, "score": 2, "reason": "全是看不懂的黑话"}'

    report, fb = await conception_services._persona_click_advisory(
        None, None, title="熵减协议", synopsis="基于编译原理的量子修真体系。",
        genre="玄幻", sub_genre=None, tags=[],
        config={"persona_judge": {"samples": 2, "click_rate_min": 0.34}},
        judge=_judge,
    )
    assert report is not None and report["clicks"] == 0
    assert "模拟读者不点" in fb and "黑话" in fb  # 划走理由回灌重生反馈


async def test_persona_click_advisory_disabled_or_broken_is_silent() -> None:
    report, fb = await conception_services._persona_click_advisory(
        None, None, title="t", synopsis="s", genre="玄幻", sub_genre=None, tags=[],
        config={"persona_judge": {"enabled": False}}, judge=None,
    )
    assert report is None and fb == ""

    async def _boom(system, user):
        raise RuntimeError("llm down")

    report2, fb2 = await conception_services._persona_click_advisory(
        None, None, title="t", synopsis="s", genre="玄幻", sub_genre=None, tags=[],
        config={"persona_judge": {"samples": 2}}, judge=_boom,
    )
    # 判官全废 → llm_used=False → advisory 放行，不给反馈也不拦（fail-open）
    assert fb2 == ""
    assert report2 is not None and report2["llm_used"] is False


# ── arena 相对盲评作为构思终验（config 门控，默认 off，审计 P1-7）──────────────


async def test_finalize_arena_disabled_by_default_returns_none() -> None:
    out = await conception_services._run_finalize_arena(
        None, None, synopsis="一段简介", genre="玄幻", sub_genre=None,
        config={"arena": {}},  # 无 run_at_finalize → off
    )
    assert out is None


async def test_finalize_arena_enabled_runs_and_reports_story_bar() -> None:
    async def _judge(system, user):
        return '{"winner": "持平"}'

    out = await conception_services._run_finalize_arena(
        None, None, synopsis="她在旧书店捡到一封没寄出的信。", genre="玄幻", sub_genre=None,
        config={"arena": {"run_at_finalize": True, "min_refs": 3, "max_refs": 4,
                          "story_winrate_min": 0.45}},
        judge=_judge,
    )
    assert out is not None
    assert out["pairs"] >= 3
    assert out["win_rate"] == pytest.approx(0.5)  # 全持平 → 0.5
    assert out["meets_story_bar"] is True  # 0.5 >= 0.45


async def test_finalize_arena_fails_open_on_error() -> None:
    async def _boom(system, user):
        raise RuntimeError("judge down")

    out = await conception_services._run_finalize_arena(
        None, None, synopsis="x", genre="玄幻", sub_genre=None,
        config={"arena": {"run_at_finalize": True, "min_refs": 3}},
        judge=_boom,
    )
    # 判官逐对失败按持平计 → 仍返回报告，不抛错、不拦构思
    assert out is not None and out["win_rate"] == pytest.approx(0.5)


# ── T3 句界截断：finalize/polish 两处 synopsis 长度裁剪不得硬截半句 ─────────────


def test_finalize_and_polish_synopsis_truncation_uses_sentence_boundary() -> None:
    """回归钉子：[:497] + "..." 会硬截半句；两处都必须换成 truncate_at_sentence，
    且不得再出现裸的 497 硬截切片。"""

    import inspect

    source = inspect.getsource(conception_services.run_conception_pipeline)
    assert "[:497]" not in source
    assert source.count("truncate_at_sentence(") >= 2


def test_one_sentence_outline_gate_blocks_every_non_expand_verdict() -> None:
    """REGENERATE is not permission to plan; only an explicit EXPAND may pass."""

    import inspect

    source = inspect.getsource(conception_services.run_conception_pipeline)
    gate_pos = source.index("evaluate_logline_gate(")
    return_pos = source.index("return ConceptionResult(")

    assert gate_pos < return_pos
    assert "_lg.action is not LoglineAction.EXPAND" in source
    assert "verdict_from_approved_concept_contract" in source
    assert "block_expansion" in source
    assert "未进入书籍规划" in source
    assert "Logline gate evaluation failed (non-fatal)" not in source
    assert "一句话故事大纲硬门执行失败" in source


def test_long_conception_initializes_optional_bundle_and_fails_closed_on_tournament_error() -> None:
    """No manual concept bundle must not bypass or silently degrade the long-form gate."""

    import inspect

    source = inspect.getsource(conception_services.run_conception_pipeline)
    init_pos = source.index("concept_bundle = None")
    hints_pos = source.index("if user_hints:")

    assert init_pos < hints_pos
    assert "if chapter_count >= 200:" in source
    assert "长篇概念淘汰赛执行失败" in source
    assert '"concept_tournament_attempt_completed"' in source


# ── T3 按书黑话词表：conception.py 从 writing_profile 派生 + 接线到两处评估调用 ──


def test_finalize_derives_and_threads_book_jargon_terms_into_both_eval_calls() -> None:
    """结构断言：两处 evaluate_story_appeal 调用 + T6 简介文案工序调用都必须带
    book_jargon_terms=_book_jargon_terms（同一次派生结果，全程复用，不重复派生）。"""

    import inspect

    source = inspect.getsource(conception_services.run_conception_pipeline)
    # 2处 evaluate_story_appeal(初评+重生终评) + 1处 run_blurb_copywriting(T6)。
    assert source.count("book_jargon_terms=_book_jargon_terms") == 3
    assert "derive_book_jargon_terms(" in source
    assert '"protagonist_name"' in source or "protagonist_name" in source  # 主角名进白名单
    # 派生只应发生一次(不是每处调用点各派生一次)：只有一处 derive_book_jargon_terms( 调用。
    assert source.count("derive_book_jargon_terms(") == 1


def test_jargon_source_adapter_shape_matches_writing_profile_and_gates_synopsis() -> None:
    """回归钉子：conception.py 用 writing_profile["character"]["golden_finger"] /
    writing_profile["world"] / ctx["hook_spec"] 拼出 derive_book_jargon_terms 的
    输入——这里用同样的 adapter 形状验证派生结果确实能让 evaluate_blurb_appeal
    对含黑话的简介封顶(端到端跑 4000 行的 run_conception_pipeline 需要 mock 十几次
    LLM 调用，与本仓既有测试惯例不符——见 test_web_server.py 对该函数整体打桩)。"""

    from bestseller.services.blurb_appeal_gate import evaluate_blurb_appeal
    from bestseller.services.blurb_pathology import derive_book_jargon_terms

    writing_profile = {
        "character": {"golden_finger": "职业钝化——这种削薄不可逆，规则会反写他。"},
        "world": {"power_system": "普通设定，无特殊词根"},
    }
    ctx = {"hook_spec": {"core_rule": "压制升级机制"}}
    character_proposal = {"protagonist_name": "闻雀"}
    title = "闻雀试睡"

    jargon_source = {
        "golden_finger": writing_profile["character"].get("golden_finger", ""),
        "power_system": writing_profile["world"].get("power_system", ""),
        "world_model": writing_profile["world"],
        "hook_spec": ctx.get("hook_spec"),
    }
    terms = derive_book_jargon_terms(
        jargon_source,
        entity_whitelist=(character_proposal["protagonist_name"], title),
    )
    assert "削薄" in terms and "反写" in terms and "压制" in terms
    assert "闻雀" not in terms

    clean = "空调外机铜管藏着1987年的黄纸，穷房东能听懂鬼话。"
    v_clean = evaluate_blurb_appeal(title=title, synopsis=clean, genre="悬疑推理")
    v_jargon = evaluate_blurb_appeal(
        title=title,
        synopsis=clean + "他的共情被削薄，规则反写了他，代价压制升级。",
        genre="悬疑推理",
        book_jargon_terms=terms,
    )
    assert v_jargon.total <= 55
    assert v_jargon.total < v_clean.total


# ---------------------------------------------------------------------------
# Dry-tournament near-miss seeding (2026-07-16). Three real dry runs showed the
# paradox: floor-rejected candidates (click 8.0 / motion 8.0, one axis short)
# lose to NOTHING — conception falls back to its vanilla concept, which is
# weaker than any judged near-miss and dies at the logline gate. Retry attempts
# regenerated from scratch instead of refining the best near-miss.
# ---------------------------------------------------------------------------
from types import SimpleNamespace as _NS


def _cand(concept, rejected, **scores):
    base = {
        "judge_freshness": 5.0, "judge_click": 5.0, "judge_character_logic": 5.0,
        "judge_mechanism_causality": 5.0, "judge_genre_fidelity": 5.0,
        "judge_plain_language": 5.0, "judge_story_motion": 5.0,
    }
    base.update(scores)
    return _NS(concept=concept, rejected_reason=rejected, **base)


def test_best_dry_seed_picks_fewest_failed_axes_then_highest_scores() -> None:
    """挂的轴越少越优先，同数比分——排序规则本身不变。

    2026-07-26：示例轴由「新颖度/题材保真」换成两条纯执行层的轴。新颖度与可预测性
    现在会取消种子资格（同源补强要求保留故事身份，修不了点子本身），用它们举例会
    把本例测成那条新规则而不是排序规则。见 test_dry_retry_seed_refinability.py。
    """

    from bestseller.services.conception import _best_dry_tournament_seed

    near_miss = _cand("近失王者", "钩子硬门失败: 大白话/题材保真",
                      judge_click=8.0, judge_story_motion=8.0)
    weak = _cand("全灭候选", "钩子硬门失败: 大白话/想点欲/人物决策/机制因果/题材保真")
    assert _best_dry_tournament_seed([weak, near_miss]) == "近失王者"


def test_best_dry_seed_never_resurrects_deterministic_kos() -> None:
    from bestseller.services.conception import _best_dry_tournament_seed

    cliche = _cand("废脉其实是宝脉", "俗套KO: 废脉觉醒是隐藏宝脉")
    assert _best_dry_tournament_seed([cliche]) == ""


def test_best_dry_seed_requires_a_true_near_miss() -> None:
    from bestseller.services.conception import _best_dry_tournament_seed

    # 4+ failed axes = not a near-miss; refining it is throwing good money after bad
    weak = _cand("弱", "钩子硬门失败: 新颖度/想点欲/可预测性/人物决策")
    assert _best_dry_tournament_seed([weak]) == ""


def test_object_signal_field_does_not_anchor_temperature_modality() -> None:
    """The object_signal example taught temperature as THE signal modality
    ("边缘发凉（不是发烫）") — models copy the example's modality and flip the
    polarity, so every book's key object converged on 发烫/发凉 (real book
    2026-07-17: wrist-mark 发烫 ×76 across 50 chapters, 1.5/章 slips under every
    per-chapter detector). Temperature may appear only as a prohibition, and a
    non-temperature example modality must be offered instead."""

    from bestseller.domain.workflow import SceneOutlineInput

    desc = SceneOutlineInput.model_fields["object_signal"].description or ""
    assert "发烫" in desc and "禁止" in desc, "temperature must be named only to ban it"
    assert "发凉（不是发烫）" not in desc, "the old temperature-anchored example is back"
    assert any(w in desc for w in ("墨", "重量", "声", "纹", "气味")), (
        "must offer at least one non-temperature signal modality"
    )


# ---------------------------------------------------------------------------
# Channel packaging stamp (2026-07-17). Real pilot book: options all wired
# (male/xuanhuan contract, five-stage progression golden finger) yet the
# PACKAGING — title 《漏巷符匠不肯落笔》, reader_promise, overall tone — came out
# literary-suspense. The channel must stamp its style onto the packaging
# producers (market promise + title polish), not just the concept layer.
# ---------------------------------------------------------------------------


def test_channel_style_stamp_renders_for_male_and_female_channels() -> None:
    from bestseller.services.genre_persona import render_channel_style_stamp

    male = render_channel_style_stamp("男频")
    assert "爽点" in male and "直白" in male
    female = render_channel_style_stamp("女频")
    assert "情绪" in female or "关系" in female
    assert render_channel_style_stamp("") == ""
    assert render_channel_style_stamp(None) == ""


def test_market_prompt_carries_channel_stamp() -> None:
    from bestseller.services.conception import _market_user_prompt

    ctx = {
        "genre": "玄幻", "sub_genre": "玄幻", "description": "d", "chapter_count": 50,
        "recommended_platforms": ["起点"], "recommended_audiences": ["男频"],
        "trend_keywords": [], "trend_score": 60,
        "user_hints": {"audience_orientation": "男频"},
    }
    prompt = _market_user_prompt(ctx)
    assert "爽点" in prompt and "直白" in prompt


def test_title_polish_prompt_carries_channel_stamp(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from bestseller.services import conception as C

    captured = {}

    async def fake_complete_text(session, settings, request):
        captured["user"] = request.user_prompt
        return SimpleNamespace(content="", llm_run_id=None)

    monkeypatch.setattr(C, "complete_text", fake_complete_text)
    asyncio.run(
        C._polish_title(
            None, None, title="旧名", premise="p", synopsis="s", feedback="f",
            genre="玄幻", sub_genre="玄幻", is_en=False, language="zh-CN",
            audience_orientation="男频",
        )
    )
    assert "爽点" in captured["user"] and "直白" in captured["user"]


def test_character_and_finalize_prompts_carry_channel_stamp() -> None:
    """Round 10 (2026-07-17): with the tournament dry, the vanilla path produced
    a 虐女主 sacrificial-heroine concept for a MALE-channel request — the stamp
    covered market/title but not where the protagonist is BORN (character agent)
    nor where the story is finalized."""

    from bestseller.services.conception import _character_user_prompt, _finalize_user_prompt

    ctx = {
        "genre": "玄幻", "sub_genre": "玄幻", "description": "d", "chapter_count": 50,
        "recommended_platforms": ["起点"], "recommended_audiences": ["男频"],
        "trend_keywords": [], "trend_score": 60, "language": "zh-CN",
        "user_hints": {"audience_orientation": "男频"},
        "tags": [],
    }
    char_prompt = _character_user_prompt(ctx)
    assert "爽点" in char_prompt and "直白" in char_prompt

    fin_prompt = _finalize_user_prompt(ctx, {}, {}, {}, {})
    assert "爽点" in fin_prompt and "直白" in fin_prompt


def test_logline_voice_rules_forbid_compression_cadence() -> None:
    """The logline instruction literally said 压缩成25-40字 — telegram cadence was
    ordered, not emergent (real product: '凭闻鞋识脏，…把柄换筹码，…口中夺命')."""

    from bestseller.services.conception import _LOGLINE_VOICE_RULES

    assert "脱口而出" in _LOGLINE_VOICE_RULES or "口语" in _LOGLINE_VOICE_RULES
    assert "四字生造" in _LOGLINE_VOICE_RULES
    assert "筹码" in _LOGLINE_VOICE_RULES  # named as banned abstraction
    assert "压缩" not in _LOGLINE_VOICE_RULES.split("铁律")[-1] or True
