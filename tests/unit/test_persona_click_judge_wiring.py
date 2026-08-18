"""T1 wiring pins for the persona click judge inside conception.py.

审计发现 commit 5ad76ed（画像点击判官）此前滞留在未合并分支，从未进 main。
cherry-pick 回收后，``_persona_click_advisory``/``run_persona_click_judge`` 本身的
行为已由 test_conception_services.py 与 test_persona_click_judge.py 充分覆盖
（pass/fail/disabled/exception 四态）。本文件只补两个未覆盖、且真实存在过回归
风险的缝隙：

1. 判官模型路由必须走标准 per-role 解析（critic/standard tier），不能被后续
   改动悄悄换成硬编码的模型 key——历史上 DEEPSEEK_API_KEY 已 402，若判官被
   接到死模型键上会全员 fail-open 静默失效，且没有测试会发现。
2. finalize 内的初评/终评两次调用 + block_below→AppealBarNotMetError 拦截链
   是内联在 ~4000 行 ``run_conception_pipeline`` 里的控制流，无法在不引入
   整段管线 mock 基建的前提下端到端跑（本仓已有先例：test_web_server.py 对
   run_conception_pipeline 整体打桩而非真跑）。这里用与本仓既有惯例一致的
   源码结构断言（例如 test_finalize_prompt_carries_golden_finger_diversity_
   principle 同款手法）钉住该控制流的关键锚点，防止被静默删除或短路。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import conception as conception_services
from bestseller.services.persona_click_judge import _make_default_judge


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_judge_uses_critic_standard_tier_not_hardcoded_model(monkeypatch) -> None:
    """默认判官必须走 logical_role='critic' 的标准角色解析，不能硬编码具体
    provider/model（尤其不能是已知 402 的 deepseek 死键）。"""

    captured: dict[str, object] = {}

    async def _fake_complete_text(session, settings, request):
        captured["logical_role"] = request.logical_role
        captured["model_tier"] = request.model_tier
        captured["model_override"] = getattr(request, "model_override", None)

        class _Completion:
            content = '{"click": true, "score": 7, "reason": "ok"}'
            llm_run_id = None

        return _Completion()

    monkeypatch.setattr(
        "bestseller.services.llm.complete_text", _fake_complete_text
    )

    judge = _make_default_judge(session=None, settings=None)
    raw = await judge("system prompt", "user prompt")

    assert '"click": true' in raw
    assert captured["logical_role"] == "critic"
    # 标准 tier 解析走角色默认 model，不允许 strong-tier override 指向硬编码值。
    assert captured["model_tier"] == "standard"
    assert captured.get("model_override") in (None, "")
    # 显式反证：判官请求路径不得出现硬编码的 deepseek 模型串（那是已知 402 的死键，
    # arena 终验才允许显式用它，判官主链路不行）。
    source = inspect.getsource(_make_default_judge)
    assert "deepseek" not in source.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_persona_click_judge_without_injected_judge_does_not_crash(monkeypatch) -> None:
    """回归钉子：cherry-pick 落地时发现 ``_make_default_judge`` 的
    ``fallback_response=""`` 违反 ``LLMCompletionRequest`` 的 ``min_length=1``
    约束，导致真实（未注入 fake judge）路径在构造请求阶段就抛
    ``pydantic.ValidationError``——被逐采样 try/except 吞掉后，判官在生产环境
    100% 静默失效（``llm_used`` 恒为 False），且没有任何既有测试会发现，因为
    所有 cherry-pick 带来的测试都显式注入了 ``judge=``。这里不注入 judge，
    直接跑默认路径，钉住"不 crash 且能拿到真实点击结果"。
    """

    async def _fake_complete_text(session, settings, request):
        class _Completion:
            content = '{"click": true, "score": 8, "reason": "有反差"}'
            llm_run_id = None

        return _Completion()

    monkeypatch.setattr(
        "bestseller.services.llm.complete_text", _fake_complete_text
    )

    from bestseller.services.persona_click_judge import run_persona_click_judge

    report = await run_persona_click_judge(
        None, None, title="蚀骨神藏", synopsis="废柴少年觉醒神藏。", genre="玄幻",
        config={"persona_judge": {"samples": 2, "click_rate_min": 0.34}},
        # judge 未注入 → 强制走 _make_default_judge 真实路径
    )
    assert report.llm_used is True
    assert report.samples == 2
    assert report.clicks == 2


@pytest.mark.unit
def test_finalize_wires_persona_advisory_at_initial_and_terminal_eval() -> None:
    """结构断言：finalize 的 appeal 重生块必须在初评（重生循环前）与终评
    （循环体内对每轮 best 再评一次）各调用一次 ``_persona_click_advisory``，
    且结果必须落进 story_appeal_report["persona_judge"]。防止未来重构时静默
    丢掉终评那一次（终评拿到的是重生后的新简介，不是初评时的旧简介——两次
    调用不是重复代码，是两个不同输入）。

    2026-07-24 更新：终评从"循环后 ``if attempts > 0`` 再调一次"移进循环体
    末尾。原因见 test_conception_appeal_persona_veto.py——画像判官持一票否决
    权却不参与循环续跑条件，导致数值门达标的书 attempts=0 直接被毙。判决现在
    要驱动循环，就必须每轮刷新，循环后那次调用因此变成冗余并被删除。调用点
    数量仍是 2，位置变了。
    """

    source = inspect.getsource(conception_services.run_conception_pipeline)

    call_count = source.count("_persona_click_advisory(")
    assert call_count == 2, (
        f"expected exactly 2 call sites (initial + in-loop re-eval), found {call_count}"
    )
    assert 'story_appeal_report["persona_judge"] = _persona_report' in source


@pytest.mark.unit
def test_finalize_wires_persona_block_below_into_shared_appeal_bar_error() -> None:
    """结构断言：``persona_judge.block_below=true`` 且判官判"不点"时，必须
    通过与绝对分门共享的 ``_appeal_block_below`` 变量汇入同一条
    ``AppealBarNotMetError`` 拦截链，而不是另开一条独立的拦截路径（那样 web
    层只捕获一种异常的假设就会失效）。
    """

    source = inspect.getsource(conception_services.run_conception_pipeline)

    # block_below 判定统一走 story_appeal.persona_hard_veto（含 fail-open 语义），
    # 不再在管线里手抄 _pj_cfg.get("block_below") —— 同一判定此前存在两份副本
    # （循环续跑用一份、硬拦用另一份），正是判决没能驱动重生的原因之一。
    assert "persona_hard_veto(_persona_report, _appeal_cfg)" in source
    assert "_appeal_block_below = True" in source
    # 2026-08-18 起异常先落变量再 raise（为挂 conception_log 带出 async 帧），
    # 锁的是「抛的是共享错误类型且带全过程日志」，不是字面 raise 形态。
    assert "AppealBarNotMetError(" in source
    assert "raise _appeal_exc" in source
    assert "_appeal_exc.conception_log" in source
    assert "blocked_by=tuple(_by)" in source


@pytest.mark.unit
def test_persona_judge_config_present_in_story_appeal_yaml() -> None:
    from bestseller.services.story_appeal import load_story_appeal_config

    cfg = load_story_appeal_config()
    pj = cfg.get("persona_judge")
    assert isinstance(pj, dict)
    assert pj.get("enabled") is True
    assert pj.get("samples", 0) >= 1
    assert 0 < pj.get("click_rate_min", 0) <= 1
    # 2026-07-17 真机校准完成后置 true：试点书证实选项全接线的情况下频道包装仍会
    # 跑偏(男频出文青书名/悬疑承诺),包装层需要画像判官真实否决权(用户拍板)。
    assert pj.get("block_below") is True
