"""爽点存活兜底（2026-08-19 真机：8 章修订丢 6 个爽点=75%）。

prompt 层的保全块是**软**约束：去水规则要求删「同一件事写几遍」，
而爽点三拍（施动/见证/结算）正好长成那样，模型照删不误。所以采纳
判据这一层必须硬——删掉本章爽点的改稿，除非 AI 味改善巨大，否则
不予采纳。宁可带点 AI 味，不要一章读完什么都没兑现。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import deslop_revise

pytestmark = pytest.mark.unit

_WITH_PAYOFF = (
    "他一步不退。" * 30
    + "满堂宾客看着长老僵住，脸色铁青，这一记当众打脸来得又快又狠，"
    "谁都没想到废柴会赢。他把令牌拍在案上，无人再敢出声。"
)
_NO_PAYOFF = "他把水烧开，倒进壶里，看着窗外的雨。" * 40


def test_probe_reads_payoff_like_the_stamp_does():
    assert deslop_revise._hype_survives(_WITH_PAYOFF, "zh-CN") is True
    assert deslop_revise._hype_survives(_NO_PAYOFF, "zh-CN") is False


def test_probe_fails_open():
    # 探针异常绝不能中断修订（返回 True＝不拦）
    src = inspect.getsource(deslop_revise._hype_survives)
    assert "except Exception" in src and "return True" in src


def test_acceptance_guard_wired_with_big_win_escape():
    src = inspect.getsource(deslop_revise)
    assert "_hype_lost" in src, "采纳判据必须含爽点存活"
    # 只在原稿本来有爽点、且本章挂了合同时才判
    assert "_hype_survives(content, language) and not _hype_survives(" in src
    assert "hype_preservation_block" in src
    # 巨大改善的逃生阀：AI 味降幅 ≥40% 仍可采纳
    assert "_after_bad <= _before_bad * 0.6" in src
