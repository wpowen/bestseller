"""设定/逻辑框架层测试（2026-07-08 用户终审"不知道在讲啥/没逻辑/没爽感"根治）。

构思是纯 LLM 自由发挥 + 一堆合规闸门，机制/代价/数字全是为过闸门"凑"的，
不是从世界规律"推"出来的。四把刀：造世前置 → 机制因果账 → 机制极性闸门 →
跨产物事实台账。全部 fail-open。
"""

from __future__ import annotations

import asyncio

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit

_CTX = {
    "genre": "都市异能",
    "sub_genre": "规则怪谈",
    "description": "记录诡异规则的观察员发现规则清单越来越像他自己写的",
    "chapter_count": 10,
    "language": "zh-CN",
}


# ── ① 造世前置：_derive_conception_world_model ───────────────────────────


def _real_world_model():
    """真实 WorldModel(用 fallback_world_model 零成本构造,非手搓 fake,防 schema 漂移)。"""

    from bestseller.domain.world_model import world_model_from_dict
    from bestseller.services.world_model_deriver import fallback_world_model

    payload = fallback_world_model(premise="记录诡异规则的人发现规则是他自己写的", genre="规则怪谈")
    return world_model_from_dict(payload)


def test_derive_conception_world_model_returns_payload(monkeypatch) -> None:
    async def fake_derive_world_model(*a, **k):
        return _real_world_model()

    monkeypatch.setattr(
        "bestseller.services.world_model_deriver.derive_world_model", fake_derive_world_model
    )
    payload, model, ids = asyncio.run(
        conception._derive_conception_world_model(
            None, object(), premise="测试前提", ctx=dict(_CTX)
        )
    )
    assert model is not None
    assert payload.get("world_laws")
    assert ids == []


def test_derive_conception_world_model_fails_open(monkeypatch) -> None:
    async def fake_derive_world_model(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "bestseller.services.world_model_deriver.derive_world_model", fake_derive_world_model
    )
    payload, model, ids = asyncio.run(
        conception._derive_conception_world_model(
            None, object(), premise="测试前提", ctx=dict(_CTX)
        )
    )
    assert payload == {}
    assert model is None
    assert ids == []


# ── ② 机制因果账：_audit_mechanism_causality ─────────────────────────────


def _writing_profile(golden_finger: str = "读懂规则漏洞并反手当护身符") -> dict:
    return {"character": {"golden_finger": golden_finger, "growth_curve": ""}}


def test_audit_mechanism_causality_updates_premise_and_notes(monkeypatch) -> None:
    fixed = {
        "premise": "修正后的前提，代价溯源到记忆规律",
        "golden_finger": "修正后的金手指",
        "mechanism_causality_notes": ["代价从记忆规律来,原21天无来源已删除"],
    }

    async def fake_llm_call_json(*a, **k):
        return fixed, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    new_premise, new_profile, ids, notes = asyncio.run(
        conception._audit_mechanism_causality(
            None, object(),
            premise="原前提", writing_profile=_writing_profile(),
            world_model=_real_world_model(), ctx=dict(_CTX), is_en=False,
        )
    )
    assert new_premise == fixed["premise"]
    assert new_profile["character"]["golden_finger"] == fixed["golden_finger"]
    assert notes == fixed["mechanism_causality_notes"]


def test_audit_mechanism_causality_skips_without_world_model() -> None:
    new_premise, new_profile, ids, notes = asyncio.run(
        conception._audit_mechanism_causality(
            None, object(),
            premise="原前提", writing_profile=_writing_profile(),
            world_model=None, ctx=dict(_CTX), is_en=False,
        )
    )
    assert new_premise == "原前提"
    assert ids == [] and notes == []


def test_audit_mechanism_causality_fails_open_on_garbage(monkeypatch) -> None:
    async def fake_llm_call_json(*a, **k):
        return {"garbage": True}, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    new_premise, new_profile, ids, notes = asyncio.run(
        conception._audit_mechanism_causality(
            None, object(),
            premise="原前提", writing_profile=_writing_profile(),
            world_model=_real_world_model(), ctx=dict(_CTX), is_en=False,
        )
    )
    assert new_premise == "原前提"
    assert notes == []


# ── ③ 机制极性闸门 ────────────────────────────────────────────────────────


def test_golden_finger_optout_violation_fires_outside_whitelist() -> None:
    msg = conception._detect_golden_finger_optout_violation(
        golden_finger="无显性金手指，优势在直觉", ctx=dict(_CTX)
    )
    assert msg is not None
    assert "豁免资格" in msg


def test_golden_finger_optout_allowed_for_whitelisted_genre() -> None:
    ctx = dict(_CTX, genre="纯武侠", sub_genre="江湖恩怨")
    msg = conception._detect_golden_finger_optout_violation(
        golden_finger="无显性金手指，优势在谋略", ctx=ctx
    )
    assert msg is None


def test_golden_finger_optout_no_violation_without_optout_phrase() -> None:
    msg = conception._detect_golden_finger_optout_violation(
        golden_finger="能读懂规则漏洞", ctx=dict(_CTX)
    )
    assert msg is None


def test_golden_finger_polarity_violation_fires_on_cost_only_mechanism() -> None:
    msg = conception._detect_golden_finger_polarity_violation(
        golden_finger="每次使用都要付出代价，永久丢失一段记忆，不断消耗",
        growth_curve="",
        synopsis="他是一个普通职员，日常生活如常展开。",
        premise="没有任何时间压力或关系利害的平铺叙述。",
    )
    assert msg is not None
    assert "机制极性缺失" in msg


def test_golden_finger_polarity_no_violation_with_progression_signal() -> None:
    msg = conception._detect_golden_finger_polarity_violation(
        golden_finger="每次使用都有代价，但能力越用越强，最终问鼎顶点",
        growth_curve="从入门到巅峰，一步步登顶",
        synopsis="平铺简介。",
        premise="平铺前提。",
    )
    assert msg is None


def test_golden_finger_polarity_exempt_when_dramatic_tension_present() -> None:
    # "凌晨"命中 _EMBODIED_EMOTION_SIGNALS 的"迫近"类别 → 戏剧张力型豁免
    msg = conception._detect_golden_finger_polarity_violation(
        golden_finger="每次使用都要付出代价，永久丢失一段记忆",
        growth_curve="",
        synopsis="凌晨三点，他必须做出选择。",
        premise="平铺前提。",
    )
    assert msg is None


def test_golden_finger_polarity_no_violation_without_cost_words() -> None:
    msg = conception._detect_golden_finger_polarity_violation(
        golden_finger="", growth_curve="", synopsis="", premise="",
    )
    assert msg is None


def test_polish_golden_finger_mechanism_applies_fix(monkeypatch) -> None:
    fixed = {"golden_finger": "修正后有获得感的金手指", "growth_curve": "越来越强"}

    async def fake_llm_call_json(*a, **k):
        return fixed, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    gf, gc, ids = asyncio.run(
        conception._polish_golden_finger_mechanism(
            None, object(),
            golden_finger="只有代价", growth_curve="",
            violations=["[机制极性缺失] ..."], ctx=dict(_CTX), is_en=False,
        )
    )
    assert gf == fixed["golden_finger"]
    assert gc == fixed["growth_curve"]


def test_polish_golden_finger_mechanism_fails_open(monkeypatch) -> None:
    async def fake_llm_call_json(*a, **k):
        return {}, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    gf, gc, ids = asyncio.run(
        conception._polish_golden_finger_mechanism(
            None, object(),
            golden_finger="原始金手指", growth_curve="原始曲线",
            violations=["x"], ctx=dict(_CTX), is_en=False,
        )
    )
    assert gf == "原始金手指"
    assert gc == "原始曲线"


# ── ④ 跨产物事实台账 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("三十二", 32),
        ("二十七", 27),
        ("十", 10),
        ("十二", 12),
        ("五十", 50),
        ("27", 27),
        ("三", 3),
        ("不是数字", None),
    ],
)
def test_cn_age_to_int(text: str, expected: int | None) -> None:
    assert conception._cn_age_to_int(text) == expected


def test_extract_role_tags_covers_root_and_tail() -> None:
    tags = conception._extract_role_tags("外卖骑手，无学历与执业年限要求")
    assert "外卖" in tags
    assert "骑手" in tags


def test_extract_role_tags_strips_parenthetical() -> None:
    tags = conception._extract_role_tags("急诊科主治医师（持证）")
    assert any("医师" in t or "科主" in t for t in tags)


def test_extract_cast_age_roster_reads_protagonist_and_cast() -> None:
    proposal = {
        "protagonist_name": "戚漠",
        "protagonist_age": 28,
        "protagonist_profession": "民间签约异常事件观察员",
        "key_characters": [
            {"name": "裴瓷", "age_profession": "27岁外卖骑手，无学历要求"},
            {"name": "无年龄角色", "age_profession": "档案员"},
        ],
    }
    roster = conception._extract_cast_age_roster(proposal)
    names = {r[0]: r[1] for r in roster}
    assert names["戚漠"] == 28
    assert names["裴瓷"] == 27
    assert "无年龄角色" not in names


def test_detect_cross_artifact_age_mismatch_catches_real_calibration_case() -> None:
    # 真机原案例：简介"三十二岁的外卖员" vs 人设裴瓷 27 岁外卖骑手
    roster = [("裴瓷", 27, "27岁外卖骑手，无学历与执业年限要求")]
    text = "三十二岁的外卖员没忍住，第四层开门时她已经认不出自己的手。"
    mismatches = conception._detect_cross_artifact_age_mismatches(text, roster)
    assert mismatches
    assert "32" in mismatches[0] and "27" in mismatches[0]


def test_detect_cross_artifact_age_mismatch_silent_when_consistent() -> None:
    roster = [("裴瓷", 27, "27岁外卖骑手")]
    text = "二十七岁的外卖骑手在雨里跑单。"
    assert conception._detect_cross_artifact_age_mismatches(text, roster) == []


def test_detect_cross_artifact_age_mismatch_ignores_unrelated_numbers() -> None:
    roster = [("裴瓷", 27, "外卖骑手")]
    # "21天"不含"岁"，不应被当成年龄提及
    text = "他必须在21天内查明真相。"
    assert conception._detect_cross_artifact_age_mismatches(text, roster) == []


def test_detect_cross_artifact_age_mismatch_empty_inputs() -> None:
    assert conception._detect_cross_artifact_age_mismatches("", [("a", 1, "b")]) == []
    assert conception._detect_cross_artifact_age_mismatches("三十二岁", []) == []


def test_reconcile_cross_artifact_facts_noop_without_mismatches() -> None:
    premise, synopsis, ids = asyncio.run(
        conception._reconcile_cross_artifact_facts(
            None, object(),
            premise="P", synopsis="S", mismatches=[], ctx=dict(_CTX),
        )
    )
    assert (premise, synopsis, ids) == ("P", "S", [])


def test_reconcile_cross_artifact_facts_applies_fix(monkeypatch) -> None:
    fixed = {"premise": "修正后前提", "synopsis": "修正后简介"}

    async def fake_llm_call_json(*a, **k):
        return fixed, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    premise, synopsis, ids = asyncio.run(
        conception._reconcile_cross_artifact_facts(
            None, object(),
            premise="原前提", synopsis="三十二岁的外卖员",
            mismatches=["文中冲突"], ctx=dict(_CTX),
        )
    )
    assert premise == fixed["premise"]
    assert synopsis == fixed["synopsis"]


def test_reconcile_cross_artifact_facts_fails_open_on_garbage(monkeypatch) -> None:
    async def fake_llm_call_json(*a, **k):
        return {"premise": ""}, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    premise, synopsis, ids = asyncio.run(
        conception._reconcile_cross_artifact_facts(
            None, object(),
            premise="原前提", synopsis="原简介",
            mismatches=["x"], ctx=dict(_CTX),
        )
    )
    assert premise == "原前提"
    assert synopsis == "原简介"


# ── Phase 5：模板回声防御 ─────────────────────────────────────────────────


def test_golden_finger_design_principle_bans_literal_copy() -> None:
    assert "禁止原样照抄" in conception._GOLDEN_FINGER_DESIGN_PRINCIPLE
    assert "verbatim" in conception._GOLDEN_FINGER_DESIGN_PRINCIPLE_EN


def test_genre_cliche_baseline_resolves_for_rule_horror() -> None:
    entries = conception._genre_cliche_baseline("都市异能", "规则怪谈")
    assert entries
    assert any("鬼" in e.get("premise", "") for e in entries)


def test_genre_cliche_baseline_empty_for_unmapped_genre() -> None:
    entries = conception._genre_cliche_baseline("不存在的题材", None)
    assert entries == []


def test_attach_mechanism_dedup_backfills_baseline_on_cold_start(monkeypatch) -> None:
    async def fake_recent_core_mechanisms(*a, **k):
        return []

    monkeypatch.setattr(conception, "_recent_core_mechanisms", fake_recent_core_mechanisms)
    ctx = dict(_CTX)
    settings = type("S", (), {"pipeline": type("P", (), {"enable_conception_mechanism_dedup": True})()})()
    asyncio.run(conception._attach_mechanism_dedup(None, settings, ctx))
    assert ctx["avoid_mechanisms"]  # 冷启动仍非空(静态底牌补位)


def test_attach_mechanism_dedup_prefers_real_history_over_baseline(monkeypatch) -> None:
    real_entry = {"title": "旧书", "golden_finger": "真实机制", "premise": "p", "trope_keywords": []}

    async def fake_recent_core_mechanisms(*a, **k):
        return [real_entry]

    monkeypatch.setattr(conception, "_recent_core_mechanisms", fake_recent_core_mechanisms)
    ctx = dict(_CTX)
    settings = type("S", (), {"pipeline": type("P", (), {"enable_conception_mechanism_dedup": True})()})()
    asyncio.run(conception._attach_mechanism_dedup(None, settings, ctx))
    assert ctx["avoid_mechanisms"][0] == real_entry
