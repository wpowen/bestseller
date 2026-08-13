"""L1 unit tests for the strict front-of-cascade 一句话故事大纲 gate (v3).

Guards: story intelligence is a hard prerequisite for planning.  Besides click appeal, the
gate must veto irrational protagonist behaviour, arbitrary/avoidable costs, broken causality,
genre drift, and concepts that cannot sustain a serial.  Judge failure is fail-closed because
"no supporting one-sentence outline" must never silently materialise a project.
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
def test_config_loads_story_intelligence_axes_and_hard_blocking():
    cfg = load_logline_gate_config()
    assert set(AXIS_KEYS) <= set(cfg["axes"].keys())
    assert {
        "protagonist_rationality",
        "causal_coherence",
        "cost_integrity",
        "genre_fidelity",
        "serial_sustainability",
    } <= set(CORE_AXES)
    assert len(SUPPORT_AXES) == 3
    # 严格：reject < pass，且 overall_floor 已设。
    assert cfg["reject_floor"] < cfg["pass_floor"]
    assert cfg["overall_floor"] >= 3.5
    # 2026-07-25 起为 advisory —— 实测毙掉 3/3 真实爆款，veto 权收回待校准。
    # 判定逻辑本身(轴/权重/裁决)未变，仍照常评分与驱动回炉。
    assert cfg["block_expansion"] is False
    assert cfg["require_llm"] is True


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
@pytest.mark.parametrize(
    ("axis", "expected_text"),
    [
        ("protagonist_rationality", "正常人"),
        ("causal_coherence", "因果"),
        ("cost_integrity", "代价"),
        ("genre_fidelity", "题材"),
        ("serial_sustainability", "长篇"),
    ],
)
def test_story_intelligence_hard_failures_reject(axis: str, expected_text: str):
    verdict = decide_logline_action(_scores(**{axis: 1.5}))

    assert verdict.action is LoglineAction.REJECT
    assert verdict.weakest_axis == axis
    assert any(expected_text in text for text in (*verdict.reasons, *verdict.fix_directives))


@pytest.mark.unit
def test_predictable_arc_regenerates():
    # 2026-07-25: pass_floor 3.5 → 3.0（判官锚点里 3 就是"合格"，旧值高于
    # 判官自己的合格线）。本例改用 2.8 —— 意图不变（可预测的走向要回炉），
    # 只是把探针挪到重新标定后的门槛之下。
    v = decide_logline_action(_scores(unpredictability=2.8))
    assert v.action is LoglineAction.REGENERATE
    assert any("一眼望到头" in r for r in v.reasons)


@pytest.mark.unit
def test_axis_at_the_judges_own_pass_anchor_is_not_regenerated():
    # 判官锚点：3=合格。合格就不该被判回炉——这正是旧 pass_floor=3.5 的病：
    # 它把判官认定合格的稿子全部打回，而且是 9 条轴同时如此。
    v = decide_logline_action(_scores(unpredictability=3.0))
    assert v.action is LoglineAction.EXPAND


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
                motivation_credibility=3.5, unpredictability=3.5,
                protagonist_rationality=3.5, causal_coherence=3.5,
                cost_integrity=3.5, genre_fidelity=3.5,
                serial_sustainability=3.5)
    )
    assert v.action is LoglineAction.REGENERATE
    assert any("加权总分" in r for r in v.reasons)


@pytest.mark.unit
def test_reject_precedence_over_regenerate():
    v = decide_logline_action(_scores(motivation_credibility=2.0, contrast_irony=3.0))
    assert v.action is LoglineAction.REJECT


@pytest.mark.unit
def test_missing_hard_axis_fails_closed():
    v = decide_logline_action({"contrast_irony": 4.0})

    assert v.action is LoglineAction.REJECT
    assert v.weakest_axis in set(CORE_AXES) - {"contrast_irony"}


@pytest.mark.unit
def test_disabled_gate_is_noop_passthrough():
    cfg = {"logline_gate": {"enabled": False}}
    v = asyncio.run(evaluate_logline_gate(None, None, logline="任何卖点", config=cfg))
    assert v.action is LoglineAction.EXPAND and not v.llm_used


@pytest.mark.unit
def test_llm_failure_is_fail_closed_before_planning(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("no LLM creds")

    monkeypatch.setattr(lg, "_run_reader_judge", _boom)
    v = asyncio.run(evaluate_logline_gate(None, None, logline="殡仪馆夜班工…", genre="都市"))
    assert v.action is LoglineAction.REJECT
    assert not v.llm_used
    assert any("判官不可用" in reason for reason in v.reasons)


@pytest.mark.unit
def test_transient_judge_failure_retries_then_succeeds(monkeypatch):
    # A rate-limited / transient judge failure is infrastructure, not a bad
    # story. It must be retried, not fail-closed on the first hiccup (which
    # killed viable books whenever a concurrent run saturated the model API).
    calls = {"n": 0}

    async def _flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("rate limited")
        return {key: 8.5 for key in AXIS_KEYS}

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(lg, "_run_reader_judge", _flaky)
    monkeypatch.setattr(lg.asyncio, "sleep", _no_sleep)
    v = asyncio.run(
        evaluate_logline_gate(None, None, logline="少年靠一门只能借别人死气的秘术在宗门里换命向上", genre="仙侠")
    )
    assert calls["n"] == 3
    assert v.llm_used is True
    assert v.action is LoglineAction.EXPAND


@pytest.mark.unit
def test_zero_fallback_response_is_not_misreported_as_real_judge(monkeypatch):
    async def _fallback_payload(*_args, **_kwargs):
        return {key: 0.0 for key in AXIS_KEYS}

    monkeypatch.setattr(lg, "_run_reader_judge", _fallback_payload)
    verdict = asyncio.run(
        evaluate_logline_gate(None, None, logline="一个尚未被真实判官审查的故事核")
    )

    assert verdict.action is LoglineAction.REJECT
    assert verdict.llm_used is False
    assert verdict.weakest_axis == "judge_availability"


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
def test_block_expansion_flag_is_advisory_pending_calibration():
    # 实测这道门毙掉 3/3 真实爆款(斗破1.59/完美1.98/诡秘2.93,满分5)，
    # 故降为 advisory：照常评分与驱动回炉，但不再一票否决。
    # 恢复条件见 scripts/logline_gate_calibration.py 与
    # tests/unit/test_logline_gate_calibration_contract.py。
    cfg = load_logline_gate_config()
    assert cfg["block_expansion"] is False


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
            '"protagonist_rationality":4.2,"causal_coherence":4.1,'
            '"cost_integrity":4.0,"genre_fidelity":4.3,'
            '"serial_sustainability":4.0,'
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


# ---------------------------------------------------------------------------
# REGENERATE verdict consumption (2026-07-16). The gate's own semantics say
# regenerate = "偏弱但可修 → 回炉重写卖点（有界）", and its fix_directives are
# written as rewrite instructions — yet conception treated ANY non-EXPAND as
# instant task death, so the directives were never consumed and creation kept
# failing on salvageable concepts (real run: overall 4.38, regenerate → dead).
# ---------------------------------------------------------------------------
import asyncio as _asyncio

from bestseller.services.logline_gate import LoglineAction, LoglineGateVerdict


def _verdict(action: LoglineAction, overall: float = 4.0) -> LoglineGateVerdict:
    return LoglineGateVerdict(
        action=action,
        scores={},
        overall=overall,
        reasons=("[不可预测|硬伤 3.0] 套路可一眼望到头",),
        fix_directives=("埋一个读者预判不到的反转",),
    )


def _run_rescue(first, *, rewrites, rejudges, max_attempts=2):
    from bestseller.services.conception import _logline_regen_rescue

    calls = {"rewrite": 0, "judge": 0}

    async def rewrite_fn(logline, verdict):
        calls["rewrite"] += 1
        if isinstance(rewrites, Exception):
            raise rewrites
        return rewrites[calls["rewrite"] - 1]

    async def judge_fn(logline):
        calls["judge"] += 1
        return rejudges[calls["judge"] - 1]

    verdict, logline, attempts = _asyncio.run(
        _logline_regen_rescue(
            verdict=first,
            logline="旧的一句话",
            max_attempts=max_attempts,
            rewrite_fn=rewrite_fn,
            judge_fn=judge_fn,
        )
    )
    return verdict, logline, attempts, calls


def test_reject_verdict_also_earns_repair_attempts() -> None:
    """契约变更 2026-08-10：reject 不再等于零尝试。

    此前 reject 被定义为「根本性硬伤，直接毙」，于是被拒的一句话大纲连一次修复都
    没有就把整本书杀掉——而同一份裁决里明明带着专为回炉而写的 ``fix_directives``。
    真机《搓背》：overall 3.2，两条整改方向生成了、一次都没被消费，书没建成。
    产出修复指令却拒绝执行它们的，不是硬门，是漏掉的回路。
    """

    first = _verdict(LoglineAction.REJECT, overall=2.0)
    verdict, logline, attempts, calls = _run_rescue(
        first,
        rewrites=["按整改方向重写的一句话"],
        rejudges=[_verdict(LoglineAction.EXPAND, overall=4.8)],
    )
    assert verdict.action is LoglineAction.EXPAND
    assert logline == "按整改方向重写的一句话"
    assert attempts == 1
    assert calls == {"rewrite": 1, "judge": 1}


def test_reject_that_cannot_be_repaired_still_blocks() -> None:
    """修复挣来的是尝试机会，不是通行证：预算耗尽仍然拦截。"""

    first = _verdict(LoglineAction.REJECT, overall=2.0)
    verdict, _, attempts, _ = _run_rescue(
        first,
        rewrites=["改一", "改二"],
        rejudges=[
            _verdict(LoglineAction.REJECT, overall=1.5),
            _verdict(LoglineAction.REJECT, overall=1.8),
        ],
        max_attempts=2,
    )
    assert verdict.action is LoglineAction.REJECT
    assert attempts == 2
    # keep-best：绝不发布比原稿更差的版本
    assert verdict.overall == 2.0


def test_regenerate_verdict_gets_rescued_to_expand() -> None:
    first = _verdict(LoglineAction.REGENERATE, overall=4.4)
    verdict, logline, attempts, _ = _run_rescue(
        first,
        rewrites=["新的一句话：不可逆行动+反制"],
        rejudges=[_verdict(LoglineAction.EXPAND, overall=4.9)],
    )
    assert verdict.action is LoglineAction.EXPAND
    assert logline == "新的一句话：不可逆行动+反制"
    assert attempts == 1


def test_rescue_exhausts_budget_then_blocks_with_best_verdict() -> None:
    first = _verdict(LoglineAction.REGENERATE, overall=4.0)
    verdict, logline, attempts, calls = _run_rescue(
        first,
        rewrites=["改一", "改二"],
        rejudges=[
            _verdict(LoglineAction.REGENERATE, overall=4.5),
            _verdict(LoglineAction.REGENERATE, overall=4.2),
        ],
        max_attempts=2,
    )
    assert verdict.action is LoglineAction.REGENERATE
    assert attempts == 2 and calls["rewrite"] == 2
    # keep-best: the 4.5 verdict (and its logline) wins over both 4.0 and 4.2
    assert verdict.overall == 4.5
    assert logline == "改一"


def test_rescue_fails_closed_when_rewrite_llm_errors() -> None:
    first = _verdict(LoglineAction.REGENERATE, overall=4.0)
    verdict, logline, attempts, _ = _run_rescue(
        first, rewrites=RuntimeError("llm down"), rejudges=[]
    )
    assert verdict is first
    assert logline == "旧的一句话"


def test_mid_loop_reject_keeps_trying_within_budget() -> None:
    """改写把结果改坏（复判为 reject）时，不再立刻收手。

    reject 现在是可修裁决，所以继续用完预算是一致的行为；keep-best 保证最终返回的
    仍是见过的最好那版，改坏的稿子不会被发布。
    """

    first = _verdict(LoglineAction.REGENERATE, overall=4.0)
    verdict, logline, attempts, calls = _run_rescue(
        first,
        rewrites=["改一", "改二"],
        rejudges=[
            _verdict(LoglineAction.REJECT, overall=1.0),
            _verdict(LoglineAction.REJECT, overall=1.2),
        ],
        max_attempts=2,
    )
    assert calls["rewrite"] == 2
    assert attempts == 2
    assert verdict.action is LoglineAction.REGENERATE
    assert verdict.overall == 4.0
    assert logline == "旧的一句话"


@pytest.mark.unit
def test_advisory_gate_records_that_it_did_not_block() -> None:
    """报错归因必须看「这次有没有否决权」，不能看「裁决是不是 expand」。

    真机 2026-08-10《搓背》：杀书的是**文案**硬门（读者画像 0/3，因为冠军简介被
    人名误报丢弃、回退成一句话的 v0），但 web 层只要看到 logline 裁决≠expand 就把
    失败标题写成「一句话故事大纲不成立」，正文却贴着简介的整改意见。这条 advisory
    的门自 2026-07-25 起就没有否决权（实测毙掉 3/3 真实爆款），它的裁决是备注不是
    原因。归因错了，排查方向就整个跑偏——本次会话就先去查了那道门。
    """

    cfg = load_logline_gate_config(None)
    assert cfg["block_expansion"] is False, (
        "若要恢复否决权，必须同步更新 web 层的失败归因分支"
    )


# ── 定罪句式确定性降级（2026-08-12：天煞孤星出口终于有了句式检查）──────────


from bestseller.services.logline_gate import downgrade_for_condemned_structures


def _structure_verdict(action: LoglineAction) -> LoglineGateVerdict:
    return LoglineGateVerdict(
        action=action, scores={}, overall=4.0,
        reasons=("原判理由",), fix_directives=("原整改",),
    )


def test_condemned_structure_downgrades_expand_to_regenerate() -> None:
    verdict = downgrade_for_condemned_structures(
        _structure_verdict(LoglineAction.EXPAND),
        "末世黑雨落在谁身上，谁就说出一个秘密。",
    )
    assert verdict.action is LoglineAction.REGENERATE
    assert verdict.weakest_axis == "condemned_structure"
    assert any("定罪句式" in r for r in verdict.reasons)
    # 原判理由与整改保留（复活循环靠 fix_directives 重写）
    assert "原判理由" in verdict.reasons
    assert len(verdict.fix_directives) == 2


def test_clean_logline_passes_through_unchanged() -> None:
    original = _structure_verdict(LoglineAction.EXPAND)
    verdict = downgrade_for_condemned_structures(
        original, "昔日天骄被诬陷入狱七年，出狱那天整个世界猛然惊觉。"
    )
    assert verdict is original


def test_non_expand_verdicts_are_not_touched() -> None:
    """已经是 REJECT/REGENERATE 的原判不动——降级只堵放行口。"""

    original = _structure_verdict(LoglineAction.REJECT)
    verdict = downgrade_for_condemned_structures(
        original, "黑雨落在谁身上，谁就说出秘密。"
    )
    assert verdict is original


def test_conception_wires_downgrade_at_both_exits() -> None:
    """接线断言：初判和复活循环复审都必须过定罪句式降级——
    复审不接=带病稿借复活循环回魂（本分支已修过一次同形状漏洞）。"""

    import inspect

    from bestseller.services import conception

    src = inspect.getsource(conception)
    assert src.count("downgrade_for_condemned_structures(") >= 2, (
        "conception 必须在初判与复审两处调用定罪句式降级"
    )
