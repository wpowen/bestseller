"""职业现实校验层测试（2026-07-08 用户终审"32 岁十年夜班账不平"根治）。

设定层是全书事实底座——年龄/资历/职级/职权边界在构思落地后污染所有下游。
本层 = prompt 事实要求 + LLM 审计闭环(_audit_cast_reality, fail-open)。

2026-09-10（W1）：审计权限收敛。旧实现把"越界必须先给制度性理由…否则改剧情"
写成硬禁令，且审计返回的整份提案会被原样采用——人物选择因此可以被改写成办手续。
现约定：审计**只核客观职业事实**，越权/违规/抗命是合法的人物选择；只有事实字段
的差异可以自动采纳，创作选择一律保留原文并只做记录。
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


# ── prompt 层：人设生成必须带职业事实要求，但不得要求制度许可 ──────────────


def test_character_prompt_requires_career_reality_ledger() -> None:
    prompt = conception._character_user_prompt(dict(_CTX))
    assert "career_reality_ledger" in prompt
    assert "profession_boundary" in prompt
    assert "protagonist_age" in prompt
    assert "职业现实硬要求" in prompt
    assert "年龄账" in prompt and "职级账" in prompt and "边界账" in prompt
    # 真机反例必须在 prompt 里作为负样本出现
    assert "32岁干了十年夜班急诊" in prompt


def test_character_prompt_no_longer_demands_institutional_permission() -> None:
    """旧禁令必须撤除：不得把人物选择降级为办手续。"""

    prompt = conception._character_user_prompt(dict(_CTX))
    assert "越界必须先给制度性理由" not in prompt
    assert "否则改剧情" not in prompt
    # 新框架必须写明：缺制度许可 ≠ 行动不可能
    assert "缺少制度许可不等于行动不可能" in prompt
    assert "办手续" in prompt


def test_character_prompt_en_states_action_is_not_gated_on_permission() -> None:
    prompt = conception._character_user_prompt_en(dict(_CTX))
    assert "does NOT make an action impossible" in prompt


# ── enforcement 层：审计闭环 ──────────────────────────────────────────────


def _proposal() -> dict:
    return {
        "protagonist_archetype": "冷静的急诊医生",
        "protagonist_name": "纪蘅",
        "protagonist_age": 32,
        "protagonist_profession": "急诊科医生",
        "career_reality_ledger": "32岁,干了十年夜班",
        "key_characters": [],
    }


def _resistance_proposal() -> dict:
    """反抗型故事：行动本身就是创作选择，缺制度许可不代表不可能。"""

    return {
        "protagonist_archetype": "门派弃徒",
        "protagonist_name": "沈砚",
        "protagonist_age": 21,
        "protagonist_profession": "被逐出师门的散修",
        "career_reality_ledger": "本世界12岁引气、17岁筑基；不套现实资历",
        "profession_boundary": "无行会管辖；夺回师门名下法器会招致追捕",
        "protagonist_core_drive": "夺回被师门抢走的法器",
        "golden_finger": "能听见法器残响",
        "conflict_forces": [{"name": "师门", "force_type": "faction", "active_volumes": [1]}],
        "key_characters": [
            {"name": "沈砚", "role": "protagonist", "age_profession": "21岁散修"},
            {"name": "守拙", "role": "mentor", "age_profession": "不知年岁的守山人"},
        ],
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


# ── W1：审计只核事实，不得改写故事选择 ────────────────────────────────────


def test_malicious_audit_cannot_swap_story_choices(monkeypatch) -> None:
    """审计返回整套换主角/换冲突提案 → 不得直接采用。"""

    original = _resistance_proposal()
    hijacked = dict(original)
    hijacked["protagonist_archetype"] = "体制内公务员"
    hijacked["protagonist_core_drive"] = "按流程申请批文"
    hijacked["golden_finger"] = "无"
    hijacked["conflict_forces"] = [{"name": "审批流程", "force_type": "systemic"}]
    hijacked["reality_audit_notes"] = ["改为体制内身份以便叙事合规"]

    async def fake_llm_call_json(*a, **k):
        return hijacked, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    out, _ = asyncio.run(
        conception._audit_cast_reality(
            None, object(), character_proposal=original, ctx=dict(_CTX), is_en=False
        )
    )

    assert out["protagonist_archetype"] == "门派弃徒"
    assert out["protagonist_core_drive"] == "夺回被师门抢走的法器"
    assert out["golden_finger"] == "能听见法器残响"
    assert out["conflict_forces"] == original["conflict_forces"]
    assert out["reality_audit_rejected"], "被拒的故事选择必须留痕"


def test_audit_keeps_character_roster_and_roles(monkeypatch) -> None:
    """角色名单/定位属于创作选择：变更即拒绝，只允许同角色的事实子字段。"""

    original = _resistance_proposal()
    swapped = dict(original)
    swapped["key_characters"] = [
        {"name": "审批员", "role": "protagonist", "age_profession": "体制内 30 岁"},
    ]

    async def fake_llm_call_json(*a, **k):
        return swapped, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    out, _ = asyncio.run(
        conception._audit_cast_reality(
            None, object(), character_proposal=original, ctx=dict(_CTX), is_en=False
        )
    )

    assert [c["name"] for c in out["key_characters"]] == ["沈砚", "守拙"]
    assert out["key_characters"][0]["role"] == "protagonist"
    assert "key_characters" in out["reality_audit_rejected"]


def test_audit_adopts_minimal_factual_correction(monkeypatch) -> None:
    """事实字段（年龄/账目/边界）可以就地修正，且不触碰创作选择。"""

    original = _resistance_proposal()
    corrected = dict(original)
    corrected["protagonist_age"] = 20
    corrected["career_reality_ledger"] = "本世界12岁引气+5年筑基=17岁；20岁散修"
    corrected["profession_boundary"] = "无行会管辖；夺回法器会招致师门追捕"

    async def fake_llm_call_json(*a, **k):
        return corrected, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    out, _ = asyncio.run(
        conception._audit_cast_reality(
            None, object(), character_proposal=original, ctx=dict(_CTX), is_en=False
        )
    )

    assert out["protagonist_age"] == 20
    assert out["protagonist_core_drive"] == original["protagonist_core_drive"]
    assert out["golden_finger"] == original["golden_finger"]
    assert "protagonist_age" in out["reality_audit_adopted"]
    assert "protagonist_core_drive" not in out.get("reality_audit_adopted", [])


def test_audit_prompt_defers_to_user_intent_and_world_rules(monkeypatch) -> None:
    """审计指令必须以用户意图/作品世界为准，并禁止把行动改写成办手续。"""

    captured: dict = {}

    async def fake_llm_call_json(*a, **k):
        captured.update(k)
        return _proposal(), []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    asyncio.run(
        conception._audit_cast_reality(
            None, object(), character_proposal=_proposal(), ctx=dict(_CTX), is_en=False
        )
    )

    prompt = captured["user_prompt"]
    assert _CTX["description"] in prompt  # 用户原始意图必须进入审计
    assert "用户" in prompt and "作品" in prompt
    assert "缺少制度许可不等于行动不可能" in prompt
    assert "办手续" in prompt
    assert "虚构职业" in prompt and "现实资历" in prompt


def test_audit_guard_applies_regardless_of_language(monkeypatch) -> None:
    """英文入口/英文语言受同一采用守卫约束。"""

    original = _resistance_proposal()
    hijacked = dict(original)
    hijacked["protagonist_archetype"] = "civil servant"

    async def fake_llm_call_json(*a, **k):
        return hijacked, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    ctx = dict(_CTX)
    ctx["language"] = "en-US"
    out, _ = asyncio.run(
        conception._audit_cast_reality(
            None, object(), character_proposal=original, ctx=ctx, is_en=True
        )
    )

    assert out["protagonist_archetype"] == "门派弃徒"
    assert out["reality_audit_rejected"]
