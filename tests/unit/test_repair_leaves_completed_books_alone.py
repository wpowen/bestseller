"""写完的书，修复不该再动它。

``run_project_repair`` 不看项目是不是已经 ``completed``。于是每跑一轮它都会
新建草稿、把已结算的章重新打开，完结状态随之被推翻——书永远静不下来。

真机取证（2026-07-28，urban-power-reversal-1785219308）。修复前：

    status=completed  三章 promoted  三章 quality_debt

跑一次修复之后：

    status=revising   ch1 promoted / ch2 candidate(新建 v5) / ch3 blocked(重开)

修复没有改进任何东西，只是把一本已完结的书拆回半成品。谁触发的不重要——自愈
会做同样的事，这正是「反反复复停不下来」的来源。

判据：``completed`` 是终态。修复对终态项目必须是空操作，而不是又一轮重写。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import repair

pytestmark = pytest.mark.unit


class TestCompletedProjectsAreSkipped:
    def test_repair_checks_the_project_status_not_the_workflow_status(self) -> None:
        """第一版断言只找 ``COMPLETED`` 字样，被 ``WorkflowStatus.COMPLETED``
        白白喂饱了——那是工作流状态，与项目是否写完无关。"""

        source = inspect.getsource(repair.run_project_repair)
        assert "ProjectStatus.COMPLETED.value" in source, (
            "修复必须先看**项目**是否已完结——否则它会把完结的书拆回去"
        )

    def test_the_check_precedes_chapter_work(self) -> None:
        """必须在建草稿之前判断，事后回滚已经晚了。"""

        source = inspect.getsource(repair.run_project_repair)
        completed_at = source.index("ProjectStatus.COMPLETED.value")
        first_chapter_work = source.index("run_chapter_pipeline")
        assert completed_at < first_chapter_work

    def test_it_returns_a_result_rather_than_raising(self) -> None:
        """跳过是正常路径，不是错误。"""

        source = inspect.getsource(repair.run_project_repair)
        head = source[: source.index("run_chapter_pipeline")]
        assert "return ProjectRepairResult" in head


class TestUnfinishedProjectsStillGetRepaired:
    def test_the_guard_is_keyed_on_completion_only(self) -> None:
        """revising / writing / needs_replan 仍然要修——这才是修复存在的理由。"""

        source = inspect.getsource(repair.run_project_repair)
        guard_region = source[: source.index("run_chapter_pipeline")]
        for still_repairable in ("REVISING", "WRITING", "NEEDS_REPLAN"):
            assert f"ProjectStatus.{still_repairable}.value ==" not in guard_region
