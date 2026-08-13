"""跨批次事件查重的 hook 通道（真机 2026-08-07 custom-xianxia-1786104488）。

台账一直同时携带 goal 和 hook，但 `_outline_duplicate_event_findings` 历史上
只比 goal-vs-goal——批次 4-6 拿着 ch3 的钩子当上下文，又把近乎同一句
（雷音入体→灶眼再烫一层→温知晚批注）规划成 ch4 的章末钩子，直接漏过查重。
本文件钉住双通道语义。
"""

from __future__ import annotations

from bestseller.services.planner import _outline_duplicate_event_findings

# 真机原文（截前 80 字，与台账截断口径一致）。
_CH3_HOOK = (
    "归野的左手腕旧烫痕在雷音入体那一刻突然发痒的同时，灶眼底下又往深处烫下去"
    "一层——下一层顶出来的不是姑父的魂魄碎屑，而是温知晚藏在旧账里的一句完整"
)
_CH4_HOOK = (
    "灶眼在雷音入体那一刻往深处再烫下去一层，下一层不再是姑父魂魄碎屑，"
    "而是温知晚藏在旧账里的一句完整批注——批注指向阴司销号名单。"
)

_LEDGER = [{
    "chapter_number": 3,
    "goal": "纪釜第一次把雷音炒进蛋炒饭",
    "hook": _CH3_HOOK,
    "line": f"ch3 | 纪釜第一次把雷音炒进蛋炒饭 | {_CH3_HOOK}",
}]


def test_real_regression_hook_duplicate_is_caught() -> None:
    chapters = [{
        "chapter_number": 4,
        "chapter_goal": "钱荃上门验灶",  # goal 与台账完全不同——旧逻辑就是这么漏的
        "hook_description": _CH4_HOOK,
    }]
    findings = _outline_duplicate_event_findings(chapters, _LEDGER, threshold=0.35)
    assert len(findings) == 1
    assert findings[0]["field"] == "hook"
    assert findings[0]["ledger_chapter_number"] == 3


def test_goal_channel_still_works() -> None:
    chapters = [{
        "chapter_number": 4,
        "chapter_goal": "纪釜第一次把雷音炒进蛋炒饭",
        "hook_description": "完全不同的新钩子：巷口来了个收保护费的",
    }]
    findings = _outline_duplicate_event_findings(chapters, _LEDGER, threshold=0.35)
    assert len(findings) == 1
    assert findings[0]["field"] == "goal"


def test_fresh_chapter_passes() -> None:
    chapters = [{
        "chapter_number": 4,
        "chapter_goal": "钱荃上门验灶，纪釜临场应对",
        "hook_description": "验灶木牌落地的同时，巷口油纸伞下有人记下了摊位的方位。",
    }]
    assert _outline_duplicate_event_findings(chapters, _LEDGER, threshold=0.35) == []


def test_zero_threshold_and_empty_ledger_are_noop() -> None:
    chapters = [{"chapter_number": 4, "hook_description": _CH4_HOOK}]
    assert _outline_duplicate_event_findings(chapters, _LEDGER, threshold=0) == []
    assert _outline_duplicate_event_findings(chapters, [], threshold=0.35) == []
