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


def test_static_market_signals_exclude_oversaturated_banned_directions() -> None:
    # 2026-07-04: 系统 prompt 禁"规则面板/系统漏洞/卡天道"方向，但静态市场信号
    # 曾把"规则面板"当热度信号回灌进 user prompt，且 fallback 会把 signals[0]
    # 固化为 reader_promise_axis——被禁方向必须在信号源头被过滤。
    signals = agent._static_market_signals(genre="玄幻", sub_genre="修真", genre_key="x")
    for marker in ("规则面板", "系统漏洞", "天道bug"):
        assert not any(marker in s for s in signals)


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
    # Must NOT carry the old hard-contract propagation language.
    assert "必须向下传播" not in block
    assert "命题硬合同" not in block


def test_render_block_empty_for_none() -> None:
    assert render_concept_methodology_block(None) == ""


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
    async def fake_complete_text(session, settings, request):  # noqa: ANN001
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
    assert m.mindset == "反差打脸引擎"


@pytest.mark.asyncio
async def test_select_falls_back_when_llm_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(session, settings, request):  # noqa: ANN001
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
    assert m.audience_orientation == "male"
