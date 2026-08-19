"""去水改进不许被 all-or-nothing 扔掉（2026-08-19 受控实验定罪）。

真机《替嫁夜…》16 章成稿 AI 味几乎零改善(negated_definition 19、
车轱辘 23)，但受控实验证明 deslop 本身有效：ch13 命中 12→4
（negated_definition 5→1）。差距在**采纳条件**——旧逻辑只有
`decision != "block"` 才采纳，残留仍超阈值就把整份改进丢弃、
用回脏原稿。「越脏的章越改不动」，与「注水在保护自己」
「computed-then-discarded」同族。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import pipelines

pytestmark = pytest.mark.unit


def test_partial_improvement_is_kept():
    src = inspect.getsource(pipelines)
    assert "_af_improved" in src, "必须比较改稿与原稿的残留分数"
    assert (
        'if _recheck.decision != "block" or _af_improved:' in src
    ), "清干净收，更干净也收——不许 all-or-nothing"


def test_still_blocking_improvement_updates_outcome():
    """采纳改进后 outcome 必须一并更新，否则下游按旧分数走修复=对账错。"""
    src = inspect.getsource(pipelines)
    idx = src.find("_af_improved")
    window = src[idx : idx + 900]
    assert "ai_flavor_outcome = _recheck" in window
    # 仍 block 的情况要留痕可查
    assert "kept as improvement (still blocking)" in window


def test_block_routing_survives():
    # 改进被采纳不等于放行：仍 block 的章照样进机器修复
    src = inspect.getsource(pipelines)
    assert 'if ai_flavor_outcome.decision == "block" or needs_debt_leak_ai_flavor(' in src
