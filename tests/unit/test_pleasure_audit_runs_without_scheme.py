"""爽点分布审计不得因为「没有 hype_scheme」整个 no-op（2026-08-16 定罪）。

真机现象：三本书 84% 的章没有爽点、连续无爽点章远超阈值（max_consecutive_gaps=3），
`PleasureDistributionAudit` 却**一次都没报过**。

根因：守卫开得太宽——

    scheme = invariants.hype_scheme ...
    if scheme is None or scheme.is_empty:
        return []          # ← 整个审计 no-op

而 taxonomy 建的书 scheme 恒空（见 `_synthesized_hype_block` 的修复），
于是这条审计对**当前默认建书路径上的每一本书**都从未工作。

实际只有喜剧密度检查需要 scheme（读 `comedic_beat_density_target`）：
* `PLEASURE_HYPE_GAP` —— 判据是「分类器返回 None 且落库 hype_type 为空」，纯文本
* `PLEASURE_HYPE_HOGS_ENDING` —— 判据是尾段分类 vs 全文分类，纯文本

兄弟审计 `SetupPayoffTrackerAudit` 正是特意做成不依赖 scheme 的
（见 `build_full_audit` 的 docstring），这里对齐它。

⚠️ 这是本轮反复出现的同一元病的第三次发作：**能力存在，但被一道过宽的守卫
挡在我们的书之外**（前两次：爽点配方拿不到、爽点盖戳只活在场景装配路径）。
"""

from __future__ import annotations

import inspect

from bestseller.services.audit_loop import PleasureDistributionAudit


def _scan_source() -> str:
    return inspect.getsource(PleasureDistributionAudit.scan)


def test_scan_does_not_bail_out_on_empty_scheme() -> None:
    """空 scheme 不得导致整个 scan 提前返回。"""

    src = _scan_source()
    assert "scheme_available" in src, "应把 scheme 缺席降级为局部开关，而不是整体 no-op"
    # 旧的整体短路形状不得复活
    assert "if scheme is None or scheme.is_empty:\n            return []" not in src, (
        "整体 no-op 守卫复活了——taxonomy 路径的书会再次全程不被审计"
    )


def test_comedic_check_is_the_only_scheme_dependent_branch() -> None:
    """scheme 只应服务喜剧密度检查；gap 与 hogs 必须与它解耦。"""

    src = _scan_source()
    # scheme 的实际读取只有 comedic_beat_density_target 一处
    assert src.count("scheme.comedic_beat_density_target") == 1
    assert "scheme_available and total_chapters" in src, (
        "喜剧密度检查必须自带 scheme_available 守卫"
    )


def test_gap_and_hogs_codes_still_declared() -> None:
    """两条纯文本判据的 code 仍在（防止修复时误删）。"""

    assert PleasureDistributionAudit.code_gap == "PLEASURE_HYPE_GAP"
    assert PleasureDistributionAudit.code_hogs_ending == "PLEASURE_HYPE_HOGS_ENDING"
