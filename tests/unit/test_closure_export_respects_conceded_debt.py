"""终局导出门不该重审修复循环已经让步的章。

导出前会对**导出的确切字节**再跑一次质量门。对全 ok 的书这是有价值的最后一道
检查。但对 ``quality_debt`` 的章，它在重审一个**质量系统自己做过的裁决**——
那个状态的字面意思就是「预算耗尽，决定发布这份稿」。

真机取证（2026-07-28，urban-power-reversal-1785219308）：三章 promoted、
项目 completed，导出仍被挡：

    第2章：常识因果门禁 rule_term_onboarding_failure：前三章规则术语密度过高…

第 2 章正是 ``quality_debt``。修复循环已经承认修不动了，导出门却要求它达标——
和之前的发布门、提升门是同一个死锁，只是又下沉了一层。

判据不是「关掉这道门」，而是**它只否决修复循环还没让步的东西**：
- 章是 ok  → 门照常否决（干净的书仍受最后一道字节级检查保护）
- 章带 debt → findings 记进产物 warnings，不阻断
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import book_closure

pytestmark = pytest.mark.unit


class TestConcededChaptersDoNotVetoTheExport:
    def test_closure_passes_its_own_gate_to_the_export(self) -> None:
        source = inspect.getsource(book_closure.settle_project_status_on_closure)
        assert "final_quality_gate=" in source, (
            "闭环导出必须带上自己的门，否则默认门会重审已让步的章"
        )

    def test_the_gate_knows_which_chapters_carry_debt(self) -> None:
        source = inspect.getsource(book_closure._closure_quality_gate)
        assert "debt_chapters" in source


class TestCleanChaptersAreStillChecked:
    def test_a_clean_chapter_still_runs_the_real_gate(self) -> None:
        """全 ok 的书不该因为这条修复而失去最后一道检查。"""

        source = inspect.getsource(book_closure._closure_quality_gate)
        assert "run_final_quality_gates" in source

    def test_only_debt_chapters_are_downgraded(self) -> None:
        source = inspect.getsource(book_closure._closure_quality_gate)
        assert "in debt_chapters" in source or "in set(debt_chapters)" in source


class TestConcessionsAreRecorded:
    def test_a_downgraded_finding_is_not_silently_dropped(self) -> None:
        """让步必须留痕——否则就是偷偷发布一本没过门的书。"""

        source = inspect.getsource(book_closure._closure_quality_gate)
        assert "conceded" in source or "warning" in source.lower()
