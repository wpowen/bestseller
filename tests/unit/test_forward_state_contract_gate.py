from __future__ import annotations

from bestseller.services.forward_state_contract_gate import evaluate_forward_state_contract


def test_blocks_missing_forward_promises() -> None:
    verdict = evaluate_forward_state_contract("# Event State Ledger\n", current_chapter=10)

    assert verdict.verdict == "blocked"
    assert "FORWARD_STATE_MISSING" in {finding.code for finding in verdict.findings}


def test_blocks_forward_promise_without_named_entity() -> None:
    ledger = """
# Event State Ledger

## Forward Promises (N+1..N+5)
| 章 | 下一章只能怎么续 | 禁止回滚 |
| --- | --- | --- |
| 第 11 章 | 必须承接上一章结果 | 不得回滚 |
| 第 12 章 | 必须承接上一章结果 | 不得回滚 |
| 第 13 章 | 必须承接上一章结果 | 不得回滚 |
"""

    verdict = evaluate_forward_state_contract(ledger, current_chapter=10)

    assert verdict.verdict == "blocked"
    assert "FORWARD_STATE_MISSING" in {finding.code for finding in verdict.findings}


def test_passes_forward_promises_through_n_plus_three() -> None:
    ledger = """
# Event State Ledger

## Forward Promises (N+1..N+5)
| 章 | 下一章只能怎么续 | 禁止回滚 |
| --- | --- | --- |
| 第 11 章 | 林渊必须从303门牌和罗盘继续查陈默账印 | 不得跳去新怪谈 |
| 第 12 章 | 陈默手机只能作为镜眼物证继续推进 | 不得写成已安全 |
| 第 13 章 | 王建业回执必须压到苏婉宁审讯室 | 不得无条件脱身 |
"""

    verdict = evaluate_forward_state_contract(ledger, current_chapter=10)

    assert verdict.passed is True
