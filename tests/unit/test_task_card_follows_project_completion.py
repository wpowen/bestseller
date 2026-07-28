"""任务卡必须承认项目已经完结。

任务状态由 ARQ job + 最后一次 emit 的 stage 推导。autowrite 任务在把书移交给
自愈时就结束了，此后书完结了却**没有任何东西再往那个任务上 emit**，于是任务卡
永远冻结在移交那一刻。

真机取证（2026-07-28，urban-power-reversal-1785201018）：项目
``status=completed``、debt 如实记在 ``[1,2,3]``，界面上那张卡却仍写着
``project_repair_requires_machine_repair``——用户看到的是「任务都失败了」，
而书其实已经写完。

任务卡和项目跑在**两个进程**里（任务在 web 进程内存，闭环判定在 worker），
所以 worker 改不到它。修在读取侧：项目才是「书写完没有」的事实源，序列化任务
时就地采信它，且复用已有的 ``_apply_project_titles_to_tasks`` 那次查询，不另
开一趟 DB 往返。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.web import server

pytestmark = pytest.mark.unit


class TestCompletionIsReadFromTheProject:
    def test_enrichment_selects_project_status(self) -> None:
        source = inspect.getsource(server._apply_project_titles_to_tasks)
        assert "ProjectModel.status" in source, (
            "任务卡富化必须同时取项目状态——项目是书完结与否的事实源"
        )

    def test_enrichment_hands_the_statuses_to_the_settler(self) -> None:
        """取到状态还不够，必须真的用它去改写任务卡。"""

        source = inspect.getsource(server._apply_project_titles_to_tasks)
        assert "_settle_task_cards_from_project_status(" in source

    def test_settling_runs_even_when_titles_are_missing(self) -> None:
        """标题为空会提前 return——完结改写必须排在它之前。"""

        source = inspect.getsource(server._apply_project_titles_to_tasks)
        assert source.index("_settle_task_cards_from_project_status(") < source.index(
            "if not titles:"
        )

    def test_reuses_the_existing_query(self) -> None:
        """别为此多开一趟 DB 往返——同一次 select 里带上就行。"""

        source = inspect.getsource(server._apply_project_titles_to_tasks)
        assert source.count("await sess.execute(") == 1


class TestOnlyCompletionIsOverridden:
    """只有「已完结」这一个事实可以覆盖任务卡，其余状态仍由任务自己说了算。"""

    def test_running_task_on_an_unfinished_project_is_untouched(self) -> None:
        tasks = [{"task_id": "t1", "project_slug": "s", "status": "running"}]
        server._settle_task_cards_from_project_status(
            tasks, {"s": "writing"}
        )
        assert tasks[0]["status"] == "running"

    def test_failed_task_on_an_unfinished_project_is_untouched(self) -> None:
        tasks = [{"task_id": "t1", "project_slug": "s", "status": "failed"}]
        server._settle_task_cards_from_project_status(tasks, {"s": "revising"})
        assert tasks[0]["status"] == "failed"

    def test_stale_card_on_a_completed_project_becomes_completed(self) -> None:
        """THE field case。"""

        tasks = [
            {
                "task_id": "t1",
                "project_slug": "s",
                "status": "incomplete",
                "error": "project_repair_requires_machine_repair",
            }
        ]
        server._settle_task_cards_from_project_status(tasks, {"s": "completed"})
        assert tasks[0]["status"] == "completed"
        assert not tasks[0]["error"], "书写完了就不该再挂着修复错误"

    def test_conception_failure_is_not_resurrected(self) -> None:
        """构思期失败的任务没有项目行，绝不能被误判成完结。"""

        tasks = [{"task_id": "t1", "project_slug": "", "status": "failed"}]
        server._settle_task_cards_from_project_status(tasks, {"s": "completed"})
        assert tasks[0]["status"] == "failed"

    def test_unknown_slug_is_left_alone(self) -> None:
        tasks = [{"task_id": "t1", "project_slug": "other", "status": "incomplete"}]
        server._settle_task_cards_from_project_status(tasks, {"s": "completed"})
        assert tasks[0]["status"] == "incomplete"

    def test_already_completed_card_stays_completed(self) -> None:
        tasks = [{"task_id": "t1", "project_slug": "s", "status": "completed"}]
        server._settle_task_cards_from_project_status(tasks, {"s": "completed"})
        assert tasks[0]["status"] == "completed"
