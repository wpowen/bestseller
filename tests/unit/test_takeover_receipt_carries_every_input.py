"""回执必须自带判据的**全部输入**，否则它只是在复述结论。

2026-08-24：`challenger_takes_current` 有五个输入，而拒绝时的回执只记了其中一个
（`preserved_current_quality_gate_outcome`）。后果是每次出现「在架稿明明 blocked，
挑战者为什么还是没上架」，都只能离线把整条判据重算一遍——我为这一个盲点连查三轮，
而且第三轮的重算里把 `has_duplicate_findings` 假设成了 False，得出的
「修复没生效」根本站不住：真机上它可能正是否决的那一条。

这条测试把「判据参数」与「回执字段」钉在一起：以后谁给判据加一个输入而忘了记，
它就会红。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import inspect

import pytest

from bestseller.services import reviews as review_services

pytestmark = pytest.mark.unit

#: 判据参数 → 回执字段。参数名换了、或新增了输入，都必须在这里登记。
_INPUT_TO_RECEIPT_KEY = {
    "challenger_blocked": "quality_gate_rejected_current_promotion",
    "incumbent_gate_outcome": "preserved_current_quality_gate_outcome",
    "incumbent_audit_failed": "preserved_current_deterministic_audit_failed",
    "has_duplicate_findings": "takeover_had_duplicate_findings",
    "violation_codes": "takeover_violation_codes",
    "deterministic_audit_failed": "deterministic_audit",
}


def test_every_decision_input_has_a_receipt_field() -> None:
    params = set(inspect.signature(review_services.challenger_takes_current).parameters)
    missing = sorted(params - set(_INPUT_TO_RECEIPT_KEY))
    assert not missing, f"判据新增了输入却没登记回执字段：{missing}"


def test_the_rewrite_path_actually_writes_those_fields() -> None:
    source = inspect.getsource(review_services.rewrite_chapter_from_task)
    absent = sorted(
        key for key in _INPUT_TO_RECEIPT_KEY.values() if f'"{key}"' not in source
    )
    assert not absent, f"这些回执字段没有被写入：{absent}"


def test_the_decision_itself_is_recorded() -> None:
    """结论也要记——否则无法区分「判据说不换」与「判据没跑到」。"""

    source = inspect.getsource(review_services.rewrite_chapter_from_task)
    assert '"took_current_decision"' in source


def test_the_receipt_is_written_on_the_rejection_path() -> None:
    """被拒那条路径才是需要解释的那条；别只在成功路径记。"""

    source = inspect.getsource(review_services.rewrite_chapter_from_task)
    reject = source.find('"quality_gate_rejected_current_promotion": True')
    assert reject != -1
    tail = source[reject : reject + 1600]
    for key in (
        "preserved_current_quality_gate_outcome",
        "preserved_current_deterministic_audit_failed",
        "takeover_had_duplicate_findings",
        "took_current_decision",
    ):
        assert key in tail, f"拒绝路径的回执缺 {key}"
