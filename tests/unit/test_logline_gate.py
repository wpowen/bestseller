"""L1 unit tests for the strict front-of-cascade 一句话卖点 reader-perspective gate (v2).

Guards: the 7-axis / two-tier decision logic (4 hard 命门 + 3 weighted 增益维), that the
research-validated failure modes (无反差 irony / 得不偿失 motivation / 一眼望到头
predictability) map to the right action + actionable fix, the weighted overall_floor catches
"all-mediocre" loglines, config loads from the single source, disabled = no-op pass-through,
and LLM failure is fail-open (lean-pass).
"""

from __future__ import annotations

import asyncio

import pytest

from bestseller.services import logline_gate as lg
from bestseller.services.logline_gate import (
    AXIS_KEYS,
    CORE_AXES,
    SUPPORT_AXES,
    LoglineAction,
    decide_logline_action,
    evaluate_logline_gate,
    load_logline_gate_config,
)

# ruff: noqa: RUF001, RUF003


def _scores(**overrides):
    """All axes default strong (4.0); override specific ones."""
    base = {k: 4.0 for k in AXIS_KEYS}
    base.update(overrides)
    return base


@pytest.mark.unit
def test_config_loads_seven_axes_and_strict_floors():
    cfg = load_logline_gate_config()
    assert set(AXIS_KEYS) <= set(cfg["axes"].keys())
    assert len(CORE_AXES) == 4 and len(SUPPORT_AXES) == 3
    # 严格：reject < pass，且 overall_floor 已设。
    assert cfg["reject_floor"] < cfg["pass_floor"]
    assert cfg["overall_floor"] >= 3.5


@pytest.mark.unit
def test_all_axes_strong_expands():
    v = decide_logline_action(_scores())
    assert v.action is LoglineAction.EXPAND
    assert v.should_expand and not v.reasons
    assert v.overall >= 3.6


@pytest.mark.unit
def test_no_contrast_irony_regenerates_with_fix():
    # 反差张力是核心命门；四平八稳 → 回炉。
    v = decide_logline_action(_scores(contrast_irony=2.8))
    assert v.action is LoglineAction.REGENERATE
    assert v.weakest_axis == "contrast_irony"
    assert any("反差" in r for r in v.reasons)
    assert any("irony" in f or "反差" in f for f in v.fix_directives)


@pytest.mark.unit
def test_fatal_motivation_rejects_no_expand():
    # 核心命门 < reject_floor(2.5) → 根本性硬伤，不予扩充。
    v = decide_logline_action(_scores(motivation_credibility=2.0))
    assert v.action is LoglineAction.REJECT
    assert not v.should_expand
    assert any("得不偿失" in r for r in v.reasons)


@pytest.mark.unit
def test_predictable_arc_regenerates():
    v = decide_logline_action(_scores(unpredictability=3.0))
    assert v.action is LoglineAction.REGENERATE
    assert any("一眼望到头" in r for r in v.reasons)


@pytest.mark.unit
def test_single_weak_support_is_compensated_not_blocked():
    # 单条增益维偏弱(具体可视 3.2)但核心强、总分仍高 → 不因一条增益维回炉。
    v = decide_logline_action(_scores(concrete_picture=3.2))
    assert v.action is LoglineAction.EXPAND  # 被其它高分补偿，overall 仍 ≥ 3.6


@pytest.mark.unit
def test_all_mediocre_caught_by_overall_floor():
    # 核心都卡在 pass_floor 边缘、增益维偏低 → 加权总分 < overall_floor → 回炉。
    v = decide_logline_action(
        _scores(payoff_promise=2.8, differentiation=2.8, concrete_picture=2.8,
                contrast_irony=3.5, click_hook=3.5,
                motivation_credibility=3.5, unpredictability=3.5)
    )
    assert v.action is LoglineAction.REGENERATE
    assert any("加权总分" in r for r in v.reasons)


@pytest.mark.unit
def test_reject_precedence_over_regenerate():
    v = decide_logline_action(_scores(motivation_credibility=2.0, contrast_irony=3.0))
    assert v.action is LoglineAction.REJECT


@pytest.mark.unit
def test_missing_axis_is_lenient_not_fatal():
    v = decide_logline_action({"contrast_irony": 4.0})  # rest missing → pass_floor
    # 缺失补 pass_floor(3.5) → 核心不触发，但 overall == pass_floor < overall_floor(3.6) → 回炉
    assert v.action in (LoglineAction.EXPAND, LoglineAction.REGENERATE)
    assert v.action is not LoglineAction.REJECT


@pytest.mark.unit
def test_disabled_gate_is_noop_passthrough():
    cfg = {"logline_gate": {"enabled": False}}
    v = asyncio.run(evaluate_logline_gate(None, None, logline="任何卖点", config=cfg))
    assert v.action is LoglineAction.EXPAND and not v.llm_used


@pytest.mark.unit
def test_llm_failure_is_fail_open_lean_pass(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("no LLM creds")

    monkeypatch.setattr(lg, "_run_reader_judge", _boom)
    v = asyncio.run(evaluate_logline_gate(None, None, logline="殡仪馆夜班工…", genre="都市"))
    # fallback_score(3.4) → 核心不破，但 overall=3.4 < overall_floor(3.6) → REGENERATE（非误毙）。
    assert v.action is not LoglineAction.REJECT and not v.llm_used


@pytest.mark.unit
def test_verdict_dict_is_json_serializable_for_persistence():
    # conception 把 verdict.to_dict() 塞进 story_appeal_report 入库 → 必须可 JSON 序列化。
    import json

    v = decide_logline_action(_scores(unpredictability=2.0))
    blob = json.dumps(v.to_dict(), ensure_ascii=False)
    back = json.loads(blob)
    assert back["action"] in {"expand", "regenerate", "reject"}
    assert set(AXIS_KEYS) <= set(back["scores"].keys())
    assert "overall" in back and isinstance(back["overall"], (int, float))


@pytest.mark.unit
def test_block_expansion_flag_loads_default_false():
    cfg = load_logline_gate_config()
    assert cfg["block_expansion"] is False  # 默认 advisory，不硬阻断


@pytest.mark.unit
def test_real_llm_request_is_valid_and_content_parsed(monkeypatch):
    # 契约测试：用真实 LLMCompletionRequest 构造 + 读 completion.content，
    # 守住接入期三个真机 bug（缺 fallback_response / model_key 误名 / 读 .text 而非 .content）。
    import types

    from bestseller.services import llm as llm_mod

    captured = {}

    async def _fake_complete_text(session, settings, request):
        captured["request"] = request  # 必须是合法 LLMCompletionRequest（构造不抛即合法）
        return types.SimpleNamespace(
            content='{"scores":{"contrast_irony":4.2,"click_hook":4.0,'
            '"motivation_credibility":4.1,"unpredictability":4.0,'
            '"payoff_promise":4.0,"differentiation":4.0,"concrete_picture":4.0}}',
            llm_run_id=None,
        )

    monkeypatch.setattr(llm_mod, "complete_text", _fake_complete_text)
    v = asyncio.run(
        evaluate_logline_gate(object(), object(), logline="某个强反差卖点", genre="都市")
    )
    assert v.llm_used  # 真的走了 LLM 分支并解析了 content（非 fallback）
    assert v.scores["contrast_irony"] == 4.2  # content 被正确解析
    assert v.action is LoglineAction.EXPAND
    # 请求带了必填 fallback_response（缺它就是接入期那个 pydantic 校验错）
    assert captured["request"].fallback_response


@pytest.mark.unit
def test_low_llm_scores_block_expansion(monkeypatch):
    async def _weak(*a, **k):
        return {
            "contrast_irony": 2.0, "click_hook": 3.0, "motivation_credibility": 1.8,
            "unpredictability": 2.2, "payoff_promise": 2.0, "differentiation": 2.0,
            "concrete_picture": 3.0,
        }

    monkeypatch.setattr(lg, "_run_reader_judge", _weak)
    v = asyncio.run(evaluate_logline_gate(None, None, logline="折寿查案找凶手", genre="都市"))
    assert v.action is LoglineAction.REJECT  # 动机 1.8 < reject_floor
    assert v.llm_used
