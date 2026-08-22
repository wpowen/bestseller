"""勾了「无代价」的书，不许每章再被要求「给能力加代价」。

2026-08-22 用户定罪（《书院笔仙》）：用户原话——

    「整体的故事设定感觉主角智商不在线啊，有这样的神器了不得藏着掖着，
      还能被发现？」

机制在 `advantage_cost_missing`：主角优势一出现、章里没有代价词，就报
一条 finding，修复提示是

    「给能力增加冷却、疼痛、暴露、资源消耗或道德代价。」

这条判据**每章都跑，完全不认 cost_style**。而这本书建书时勾的是
`cost_style = "minimal"`（纯爽、不要自损代价）。于是框架在系统性地
奖励「能力被暴露」——主角当然显得不会藏。

这是本项目定罪过的老形状（memory motif-police-self-contradiction：
「命令写代价再因代价杀书」）的镜像版：**用户说不要代价，门禁要求有代价**。

⚠️ 只关掉 minimal 档的这一条。其余档位、其余判据一律不动。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002 — 中文标点是刻意的。
from bestseller.services.fanqie_long_ranking_gate import (
    evaluate_fanqie_long_ranking_gate,
)

# 一章里有能力展示、没有任何代价词。
_ABILITY_NO_COST = (
    "他抬手一挥，整条街的灯笼同时亮起。围观的人群发出惊呼，"
    "赵管事的脸色白了一层。他收回手，转身走进巷子，脚步没有停。"
    "身后有人喊他的名字，他没有回头。这一手能力他练了三年，今天第一次用出来。"
    "巷口的老槐树下站着一个人，正等着他。"
) * 3


def _codes(cost_style: str) -> set[str]:
    report = evaluate_fanqie_long_ranking_gate(
        {1: _ABILITY_NO_COST, 2: _ABILITY_NO_COST},
        project_slug="t",
        cost_style=cost_style,
    )
    return {f.code for f in report.findings}


def test_minimal_cost_book_is_not_told_to_add_a_price_to_the_ability() -> None:
    assert "advantage_cost_missing" not in _codes("minimal")


def test_default_cost_style_still_gets_the_finding() -> None:
    """没勾『无代价』的书行为不变。"""

    assert "advantage_cost_missing" in _codes("standard")


def test_other_findings_are_untouched_by_the_cost_style() -> None:
    """只关这一条，别的判据一律不动。"""

    minimal = _codes("minimal") | {"advantage_cost_missing"}
    assert minimal >= _codes("standard")
