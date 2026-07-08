"""职业现实校验层测试（2026-07-08 用户终审"32岁十年夜班账不平"根治）。

设定层是全书事实底座——年龄/资历/职级/职权边界在构思落地后污染所有下游。
本层 = prompt 硬要求(三道账) + LLM 审计闭环(_audit_cast_reality, fail-open)。
"""

from __future__ import annotations

import asyncio

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit

_CTX = {
    "genre": "都市异能",
    "sub_genre": "规则怪谈",
    "description": "急诊医生能闻到将死之人的气味",
    "chapter_count": 10,
    "language": "zh-CN",
}


# ── prompt 层:人设生成必须带三道账硬要求 ─────────────────────────────────


def test_character_prompt_requires_career_reality_ledger() -> None:
    prompt = conception._character_user_prompt(dict(_CTX))
    assert "career_reality_ledger" in prompt
    assert "profession_boundary" in prompt
    assert "protagonist_age" in prompt
    assert "职业现实硬要求" in prompt
    assert "年龄账" in prompt and "职级账" in prompt and "边界账" in prompt
    # 真机反例必须在 prompt 里作为负样本出现
    assert "32岁干了十年夜班急诊" in prompt


# ── enforcement 层:审计闭环 ──────────────────────────────────────────────


def _proposal() -> dict:
    return {
        "protagonist_archetype": "冷静的急诊医生",
        "protagonist_name": "纪蘅",
        "protagonist_age": 32,
        "protagonist_profession": "急诊科医生",
        "career_reality_ledger": "32岁,干了十年夜班",
        "key_characters": [],
    }


def test_audit_returns_corrected_proposal(monkeypatch) -> None:
    corrected = dict(_proposal())
    corrected["protagonist_age"] = 38
    corrected["career_reality_ledger"] = "23岁本科毕业+3年规培26岁独立值班+12年执业=38岁"
    corrected["reality_audit_notes"] = ["32岁十年夜班账不平,年龄改为38"]

    async def fake_llm_call_json(*a, **k):
        return corrected, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    out, ids = asyncio.run(
        conception._audit_cast_reality(
            None, object(), character_proposal=_proposal(), ctx=dict(_CTX), is_en=False
        )
    )
    assert out["protagonist_age"] == 38
    assert out["reality_audit_notes"]


def test_audit_fails_open_on_broken_output(monkeypatch) -> None:
    async def fake_llm_call_json(*a, **k):
        return {"garbage": True}, []  # 缺 protagonist_archetype = 结构损坏

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    original = _proposal()
    out, _ = asyncio.run(
        conception._audit_cast_reality(
            None, object(), character_proposal=original, ctx=dict(_CTX), is_en=False
        )
    )
    assert out == original  # fail-open 原样放行


def test_audit_skips_empty_proposal() -> None:
    out, ids = asyncio.run(
        conception._audit_cast_reality(
            None, object(), character_proposal={}, ctx=dict(_CTX), is_en=False
        )
    )
    assert out == {} and ids == []
