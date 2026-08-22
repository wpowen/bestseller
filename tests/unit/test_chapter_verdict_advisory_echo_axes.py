"""章级审稿的「回声代理轴」不许再持有否决权——pass 必须可达。

2026-08-22 定罪链（全库 197 份审稿报告 pass = **0**）：

章级 verdict 要求 `overall >= 0.75 且零 high/medium finding`，而所谓
「判官结构分」根本不是 LLM 打的——是**手写关键词公式**：

    hook    = 0.24 + 尾部信号*0.52 + 0.08*(尾部有"？")
              + 0.08*(有"必须"/"立刻") + 0.12*(有"下一步"/"新的不确定性")
    main    = 0.24 + max(coverage*0.6, keyword_score(章目标文字回声))*0.62
    subplot = 0.24 + max(过渡信号*0.5, keyword_score(节拍摘要回声))*0.62
    contract_alignment = 契约文字在正文中的子串匹配率

这些公式**奖励把策划词逐字贴进正文，惩罚戏剧化**——writer 把「爽」写成
一场反杀（正确写法）得 0 分。真机分布：contract_alignment 0.27 / hook
0.28 / subplot 0.29 / continuity 0.53，每条都稳定产出 high/medium
blocking finding → pass 结构性不可达 → 提升状态机恒死 → 每章 670-1115
次 LLM 调用花在**不可能收敛**的重写循环上（九项管道修复只让这些分涨了
0.02-0.03，重写不会增加关键词回声）。

同族旧案：裸字「门」使 91% 钩子为幻影；场景 emotion/hook 打分惩罚
show-don't-tell；关键词回声恒低。修法沿用框架自己的先例——场景层的
`scene_verdict_advisory_axes`（craft 轴 advisory，结构缺陷才阻断），
章层是漏掉的另一半：

* 回声代理轴（contract_alignment / main_plot / subplot / ending_hook /
  volume_mission / continuity）→ **advisory**：仍产 finding、仍进
  rewrite_instructions，但不否决 verdict。
* 阻断力保留给真缺陷：duplication / output_hygiene / common_sense，
  以及下游的 **LLM critic 多数票 rewrite**（那才是真判官，原样保留）。
* overall 门槛改为**核心轴 overall**（goal/coverage/coherence/style，
  真机均值 0.82-0.92——可达但不免费，重复惩罚照扣）。

⚠️ 顺带更正一条记忆：「章审判官 4 次生成 5 维分数完全相同」曾被解读为
判官稳定——真相是这些分是确定性公式，当然相同。
"""

from __future__ import annotations

# ruff: noqa: RUF002 — 中文标点是刻意的。
from types import SimpleNamespace

from bestseller.services.reviews import (
    _CHAPTER_ECHO_PROXY_AXES,
    resolve_chapter_verdict,
)


def _f(category: str, severity: str) -> SimpleNamespace:
    return SimpleNamespace(category=category, severity=severity)


def test_echo_axes_are_exactly_the_keyword_formula_ones() -> None:
    assert _CHAPTER_ECHO_PROXY_AXES == frozenset(
        {
            "contract_alignment",
            "main_plot_progression",
            "subplot_progression",
            "ending_hook_effectiveness",
            "volume_mission_alignment",
            "continuity",
        }
    )


def test_real_machine_shape_now_passes() -> None:
    """真机常态：核心轴 0.82-0.92 全绿，回声轴全红——这样的章必须能 pass。"""

    findings = [
        _f("contract_alignment", "high"),
        _f("main_plot_progression", "high"),
        _f("subplot_progression", "high"),
        _f("ending_hook_effectiveness", "medium"),
        _f("volume_mission_alignment", "medium"),
        _f("continuity", "medium"),
    ]
    verdict, blocking = resolve_chapter_verdict(
        core_overall=0.85,
        threshold=0.75,
        findings=findings,
        is_opening_chapter=False,
        advisory_echo_axes=True,
    )
    assert verdict == "pass"
    assert blocking == []


def test_real_defects_still_block() -> None:
    """重复 / 卫生 / 常识是真缺陷，advisory 模式下照样否决。"""

    for category in ("duplication", "output_hygiene", "common_sense"):
        verdict, blocking = resolve_chapter_verdict(
            core_overall=0.9,
            threshold=0.75,
            findings=[_f(category, "medium")],
            is_opening_chapter=False,
            advisory_echo_axes=True,
        )
        assert verdict == "rewrite", category
        assert len(blocking) == 1


def test_core_axis_failures_still_block() -> None:
    """核心轴（goal/coverage/coherence）低分不是回声问题，保留否决权。"""

    verdict, _ = resolve_chapter_verdict(
        core_overall=0.9,
        threshold=0.75,
        findings=[_f("coherence", "high")],
        is_opening_chapter=False,
        advisory_echo_axes=True,
    )
    assert verdict == "rewrite"


def test_core_overall_below_threshold_blocks() -> None:
    """pass 不免费：核心 overall 不到线照样重写（重复惩罚也扣在这里）。"""

    verdict, _ = resolve_chapter_verdict(
        core_overall=0.60,
        threshold=0.75,
        findings=[],
        is_opening_chapter=False,
        advisory_echo_axes=True,
    )
    assert verdict == "rewrite"


def test_flag_off_restores_legacy_behaviour() -> None:
    """开关关掉 = 逐字回到旧行为，回声 finding 重新持有否决权。"""

    verdict, blocking = resolve_chapter_verdict(
        core_overall=0.85,
        threshold=0.75,
        findings=[_f("contract_alignment", "high")],
        is_opening_chapter=False,
        advisory_echo_axes=False,
    )
    assert verdict == "rewrite"
    assert len(blocking) == 1


def test_low_severity_findings_never_block_either_way() -> None:
    for flag in (True, False):
        verdict, blocking = resolve_chapter_verdict(
            core_overall=0.85,
            threshold=0.75,
            findings=[_f("coverage", "low")],
            is_opening_chapter=False,
            advisory_echo_axes=flag,
        )
        assert verdict == "pass"
        assert blocking == []


def test_opening_chapter_keeps_its_own_contract() -> None:
    """开篇章（1-3）本就豁免推进轴、不看 overall——advisory 集合并入其豁免。"""

    verdict, blocking = resolve_chapter_verdict(
        core_overall=0.0,
        threshold=0.75,
        findings=[_f("ending_hook_effectiveness", "high")],
        is_opening_chapter=True,
        advisory_echo_axes=True,
    )
    assert verdict == "pass"
    assert blocking == []
