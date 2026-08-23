"""确定性审计的否决权必须是**比较**出来的，不能是各自对绝对线。

2026-08-24 部署重写死锁修复后真机复查：第 19/20/21/22/23/26 章的在架稿最新
质量报告全是 `blocks_write=true`，挑战者却仍被拒，回执写着
`hold:quality_gate_rejected`。查任务元数据，唯一的否决理由是一条
`ENDING_HOOK_MISSING`（deterministic_audit.passed=false）。

那条检测器是子串词表 + 两条窄正则（`_HOOK_TERMS`，注释里已为误报打过两次
补丁），同一个检查上两份稿多半一起挂。让它绝对否决，等于把「谁更好」重新
退化成「各自达没达绝对线」—— 正是这条修复本来要消灭的形状，章节继续冻结
在一份同样不合格的旧稿上。

改：两边都不合格时，确定性审计只有在**挑战者比在架差**时才否决。
重复内容仍然绝对否决（那是真正的不可用，与在架稿好坏无关）。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import pytest

from bestseller.services.reviews import challenger_takes_current

pytestmark = pytest.mark.unit


def _decide(**over: object) -> bool:
    base = {
        "challenger_blocked": True,
        "incumbent_gate_outcome": "blocked",
        "has_duplicate_findings": False,
        "deterministic_audit_failed": False,
        "violation_codes": (),
        "incumbent_audit_failed": False,
    }
    base.update(over)
    return challenger_takes_current(**base)  # type: ignore[arg-type]


def test_both_fail_the_same_deterministic_audit_so_the_newer_takes_over() -> None:
    """真机形状：在架稿 blocked，两份稿都挂在同一条 ENDING_HOOK_MISSING 上。"""

    assert _decide(deterministic_audit_failed=True, incumbent_audit_failed=True) is True


def test_a_challenger_that_is_worse_is_still_rejected() -> None:
    """在架稿过了同一把审计而挑战者没过 = 挑战者更差，原样否决。"""

    assert _decide(deterministic_audit_failed=True, incumbent_audit_failed=False) is False


def test_a_clean_incumbent_is_still_protected() -> None:
    """在架稿自己是干净的：不合格的挑战者一律不许顶掉它（原有保护逐字保留）。"""

    assert _decide(incumbent_gate_outcome="ok") is False
    assert (
        _decide(
            incumbent_gate_outcome="ok",
            deterministic_audit_failed=True,
            incumbent_audit_failed=True,
        )
        is False
    )


def test_duplicate_content_is_absolute() -> None:
    """重复内容与在架稿好坏无关，任何情况下都不上位。"""

    assert _decide(has_duplicate_findings=True, incumbent_audit_failed=True) is False


def test_ai_flavor_regression_stays_absolute() -> None:
    """AI 味回归是真正做过「比在架差」比较的信号，保持绝对否决。"""

    assert (
        _decide(violation_codes=("AI_FLAVOR_REGRESSION",), incumbent_audit_failed=True)
        is False
    )


def test_default_keeps_the_old_absolute_behaviour() -> None:
    """不传 incumbent_audit_failed（评估失败/老调用方）时退回旧行为，不放宽。"""

    assert (
        challenger_takes_current(
            challenger_blocked=True,
            incumbent_gate_outcome="blocked",
            has_duplicate_findings=False,
            deterministic_audit_failed=True,
        )
        is False
    )
