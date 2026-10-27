"""Tests for Agent ① — concept methodology selection (heat-search → 方法论).

All deterministic: no live network or real LLM. The orchestrator is exercised
with a Noop-equivalent search and a monkeypatched ``complete_text``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bestseller.services import concept_methodology_agent as agent
from bestseller.services.concept_methodology_agent import (
    ConceptMethodology,
    fallback_concept_methodology,
    parse_concept_methodology,
    render_concept_methodology_block,
    select_concept_methodology,
)


def test_fallback_infers_orientation_from_recommended_audiences() -> None:
    m = fallback_concept_methodology(
        genre="玄幻",
        sub_genre="东方玄幻",
        genre_key="xuanhuan-rise",
        audience_orientation="",
        recommended_audiences=["男频世界观向读者", "升级成长向读者"],
        trend_keywords=["规则", "长生"],
    )
    assert m.audience_orientation == "male"
    assert m.source == "static_fallback"
    assert m.mechanism_types  # never empty
    assert m.shuangdian_cadence  # methodology cadence present


def test_explicit_orientation_override_wins() -> None:
    m = fallback_concept_methodology(
        genre="言情",
        sub_genre="现代言情",
        genre_key="romance",
        audience_orientation="female",
        recommended_audiences=["男频连载读者"],  # contradicts override
        trend_keywords=[],
    )
    assert m.audience_orientation == "female"


def test_static_market_signals_resolve_for_known_genre() -> None:
    # 玄幻 maps to xuanhuan-brain.yaml seed profile.
    signals = agent._static_market_signals(genre="玄幻", sub_genre="修真", genre_key="x")
    assert signals  # non-empty offline signals
    assert any("规则" in s or "长生" in s or "修行" in s for s in signals)


def test_realistic_genre_does_not_route_to_urban_brain_profile() -> None:
    assert agent._match_market_profile_key("现实题材", "生活流", "") is None
    assert agent._static_market_signals(
        genre="现实题材", sub_genre="生活流", genre_key="realistic-life"
    ) == []


def test_unknown_genre_does_not_use_profile_key_as_a_cross_genre_guess() -> None:
    assert agent._match_market_profile_key("未知题材", "未知分支", "urban-brain") is None


def test_methodology_keeps_heat_and_generation_sources_separate() -> None:
    fallback = fallback_concept_methodology(
        genre="现实题材",
        sub_genre="生活流",
        genre_key="realistic-life",
        audience_orientation="neutral",
    )
    assert fallback.heat_source == "missing"
    assert fallback.methodology_generation_source == "static_fallback"

    parsed = parse_concept_methodology(
        json.dumps({"mindset": "人物选择与关系变化"}, ensure_ascii=False),
        fallback=fallback,
    )
    assert parsed.heat_source == "missing"
    assert parsed.methodology_generation_source == "llm"
    assert parsed.source == "llm"  # legacy field remains compatible


def test_legacy_llm_payload_infers_generation_source() -> None:
    methodology = ConceptMethodology.model_validate(
        {"source": "llm_filled", "market_signals": ["观察"]}
    )
    assert methodology.methodology_generation_source == "llm"


def test_static_profile_keeps_claims_as_unverified_observations() -> None:
    fallback = fallback_concept_methodology(
        genre="玄幻",
        sub_genre="修真",
        genre_key="xuanhuan",
        audience_orientation="male",
        market_signals=["蓝海赛道", "平台推荐权重更高", "可验证的读者回报"],
    )
    assert fallback.market_signals == [
        "蓝海赛道",
        "平台推荐权重更高",
        "可验证的读者回报",
    ]
    assert "蓝海" not in fallback.reader_promise_axis
    assert "推荐权重" not in " ".join(fallback.design_axes)


@pytest.mark.asyncio
async def test_search_preserves_valid_hit_with_market_language_and_source_url() -> None:
    class SearchClient:
        provider = "test"

        async def search(self, query, max_results):
            return SimpleNamespace(
                hits=[
                    SimpleNamespace(
                        title="爆款观察样本",
                        snippet="含来源的原始观察",
                        url="https://example.test/book/1",
                    )
                ]
            )

        async def close(self):
            return None

    settings = SimpleNamespace(
        pipeline=SimpleNamespace(concept_methodology_heat_search=True)
    )
    signals = await agent._gather_market_heat(
        settings=settings,
        genre="玄幻",
        sub_genre="修真",
        trend_keywords=[],
        search_client=SearchClient(),
    )
    assert signals == [
        "爆款观察样本：含来源的原始观察 [source_url:https://example.test/book/1]"
    ]


def test_methodology_prompt_marks_references_as_suggestions_not_world_facts() -> None:
    prompt = agent.build_methodology_user_prompt(
        genre="现实题材",
        sub_genre="生活流",
        description="普通人的生活选择",
        premise="主角要守住家人的生活安排",
        audience_orientation="neutral",
        market_signals=["人物选择带来关系变化"],
        trend_keywords=[],
        fallback=fallback_concept_methodology(
            genre="现实题材",
            sub_genre="生活流",
            genre_key="realistic-life",
            audience_orientation="neutral",
        ),
    )
    assert "参考" in prompt
    assert "不是世界事实" in prompt
    assert "不能把参考项写成必须存在的设定" in prompt


def test_static_market_signals_preserve_source_observations() -> None:
    # Raw market observations remain available for audit, even when fallback
    # axes avoid saturated markers.
    signals = agent._static_market_signals(genre="玄幻", sub_genre="修真", genre_key="x")
    assert any("规则面板" in signal for signal in signals)


def test_fallback_never_bakes_oversaturated_signal_into_axes() -> None:
    m = fallback_concept_methodology(
        genre="玄幻",
        sub_genre="修真",
        genre_key="xuanhuan",
        audience_orientation="male",
        market_signals=["规则面板", "长生机制"],
    )
    assert "规则面板" not in m.reader_promise_axis
    assert all("规则面板" not in axis for axis in m.design_axes)


def test_render_block_is_soft_not_hard_contract() -> None:
    m = fallback_concept_methodology(
        genre="都市",
        sub_genre="都市异能",
        genre_key="urban",
        audience_orientation="neutral",
        trend_keywords=["反差"],
    )
    block = render_concept_methodology_block(m, language="zh-CN")
    assert "软框架" in block or "参考" in block
    assert "不要照抄" in block or "不要稀释" in block
    assert '"heat_source": "static"' in block
    assert '"methodology_generation_source": "static_fallback"' in block
    # Must NOT carry the old hard-contract propagation language.
    assert "必须向下传播" not in block
    assert "命题硬合同" not in block


def test_render_block_empty_for_none() -> None:
    assert render_concept_methodology_block(None) == ""


def test_methodology_prompt_locks_native_ontology() -> None:
    prompt = agent.build_methodology_user_prompt(
        genre="仙侠",
        sub_genre="仙侠",
        description="古典修行世界",
        premise="主角从宗门底层起步",
        audience_orientation="male",
        market_signals=[],
        trend_keywords=[],
        fallback=fallback_concept_methodology(
            genre="仙侠", sub_genre="仙侠", genre_key="xianxia", audience_orientation="male"
        ),
        allowed_modernity="genre_native",
    )
    assert "题材原生机制" in prompt or "genre-native" in prompt
    assert "不要导入手机" in prompt or "do not import phones" in prompt


def test_parse_methodology_from_llm_json() -> None:
    fb = fallback_concept_methodology(
        genre="玄幻", sub_genre="", genre_key="x", audience_orientation="neutral"
    )
    raw = json.dumps(
        {
            "audience_orientation": "male",
            "mindset": "规则面板生存",
            "mechanism_types": ["信息差", "代价绑定回报"],
            "reader_promise_axis": "步步破局",
            "shuangdian_cadence": ["开篇立威", "章末反转"],
            "design_axes": ["压力升级"],
            "anti_patterns": ["无代价金手指"],
            "rationale": "贴合玄幻读者爽感",
        },
        ensure_ascii=False,
    )
    m = parse_concept_methodology(raw, fallback=fb)
    assert m.source == "llm"
    assert m.mindset == "规则面板生存"
    assert m.audience_orientation == "male"
    assert "信息差" in m.mechanism_types


def test_parse_methodology_garbage_returns_fallback() -> None:
    fb = fallback_concept_methodology(
        genre="玄幻", sub_genre="", genre_key="x", audience_orientation="neutral"
    )
    m = parse_concept_methodology("not json at all", fallback=fb)
    assert m is fb


@pytest.mark.asyncio
async def test_select_uses_llm_output_with_noop_search(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete_text(session, settings, request):
        return SimpleNamespace(
            content=json.dumps(
                {"mindset": "反差打脸引擎", "mechanism_types": ["反转"], "audience_orientation": "neutral"},
                ensure_ascii=False,
            )
        )

    monkeypatch.setattr(agent, "complete_text", fake_complete_text)
    settings = SimpleNamespace(
        pipeline=SimpleNamespace(
            enable_concept_methodology_agent=True, concept_methodology_heat_search=False
        )
    )
    m = await select_concept_methodology(
        None,  # session unused by fake
        settings,  # type: ignore[arg-type]
        genre="玄幻",
        sub_genre="修真",
        genre_key="xuanhuan",
        trend_keywords=["规则"],
    )
    assert isinstance(m, ConceptMethodology)
    assert m.source == "llm"
    assert m.methodology_generation_source == "llm"
    assert m.heat_source == "static"
    assert m.mindset == "反差打脸引擎"


@pytest.mark.asyncio
async def test_select_falls_back_when_llm_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(session, settings, request):
        raise RuntimeError("llm down")

    monkeypatch.setattr(agent, "complete_text", boom)
    settings = SimpleNamespace(
        pipeline=SimpleNamespace(
            enable_concept_methodology_agent=True, concept_methodology_heat_search=False
        )
    )
    m = await select_concept_methodology(
        None,
        settings,  # type: ignore[arg-type]
        genre="玄幻",
        sub_genre="修真",
        genre_key="xuanhuan",
        audience_orientation="male",
    )
    assert m.source == "static_fallback"
    assert m.heat_source == "static"
    assert m.audience_orientation == "male"


@pytest.mark.asyncio
async def test_select_marks_unknown_heat_as_missing_without_cross_genre_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete_text(session, settings, request):
        return SimpleNamespace(content="{}")

    monkeypatch.setattr(agent, "complete_text", fake_complete_text)
    settings = SimpleNamespace(
        pipeline=SimpleNamespace(
            enable_concept_methodology_agent=True, concept_methodology_heat_search=False
        )
    )
    m = await select_concept_methodology(
        None,
        settings,  # type: ignore[arg-type]
        genre="未知题材",
        sub_genre="未知分支",
        genre_key="urban-brain",
    )
    assert m.heat_source == "missing"
    assert m.market_signals == []
