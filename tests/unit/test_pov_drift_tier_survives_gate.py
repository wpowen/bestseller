"""POV 分级严重度必须穿过 write_gate（2026-08-20 真机《罚我守坟》定罪）。

真机 21 章里 **8 章整章是第一人称**（ch5/6/8/9/11/12/13/14），
去对白后叙述句含「我」的比例 28.8%–48.6%，而全书声明 close_third、
其余 13 章 0–1.6%。读者眼里就是一本书在「他」和「我」之间来回跳。

检测器**抓到了**：POV_DRIFT 报了 76 次，且 2026-07-08 已经按证据强度分级
（≥6 句 = 整场写错人称 → severity="block"，3-5 句 = 自由间接思维噪声 → warn）。
但 `write_gate` 里一条 `"POV_DRIFT": "audit_only"` **无条件压平**了这个分级，
`resolve_mode(code)` 只看 code 不看这条 violation 自己的 severity。
于是 8 章带病 ship，blocking_codes 里从来只有 LENGTH 系。

又一次「同一事实住两地，后写的赢」：分级修在检测器侧，压平留在门侧。

人类语料校准（.distillation_private 275 篇第三人称章，同一口径）：
40 句抽样里含「我」的句数分布 0→199 / 1→41 / 2→16 / 3→8 / 4→6 / 5→1，
**5 到 9 之间是空的**，9/10/11/12 各 1 篇（多半本来就是混合人称章）。
≥6 这个阈值正落在经验空档里，误报率 1.5%。
"""

from __future__ import annotations

import pytest

from bestseller.services.output_validator import QualityReport, Violation
from bestseller.services.write_gate import filter_blocking

pytestmark = pytest.mark.unit


def _pov(severity: str) -> Violation:
    return Violation(
        code="POV_DRIFT",
        severity=severity,
        location="sample:40:mismatches:45",
        detail="POV declared as 'close_third' but 45/40 …",
        prompt_feedback="…",
    )


def test_strong_pov_drift_blocks():
    report = QualityReport(violations=(_pov("block"),))
    blocking = filter_blocking(report, chapter_no=9)
    assert [v.code for v in blocking] == ["POV_DRIFT"], (
        "整场写错人称是结构性缺陷，不能被 audit_only 压平"
    )


def test_weak_pov_drift_stays_audit_only():
    report = QualityReport(violations=(_pov("warn"),))
    assert filter_blocking(report, chapter_no=9) == ()


def test_other_audit_only_codes_unaffected():
    """只放行检测器已自己分级的码，不得顺手给别的码开门。"""
    v = Violation(
        code="NAMING_OUT_OF_POOL",
        severity="block",
        location="x",
        detail="d",
        prompt_feedback="f",
    )
    assert filter_blocking(QualityReport(violations=(v,)), chapter_no=9) == ()
