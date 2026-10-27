"""Task display follows fresh native delivery, preserving historical failures.

Synthetic reader results exercise projection only, not physical delivery proof.
Lifecycle completed alone cannot certify a finished book.
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.web import server

pytestmark = pytest.mark.unit


class TestCompletionIsReadFromTheProject:
    def test_enrichment_selects_project_status(self) -> None:
        source = inspect.getsource(server._apply_project_titles_to_tasks)
        assert "select(ProjectModel)" in source
        assert "_read_project_delivery_status(sess, row)" in source

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
        assert source.count("await sess.scalars(") == 1


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
        # Explicit synthetic native-reader seam; status alone cannot settle.
        server._settle_task_cards_from_project_status(tasks, {"s": "completed"},
            {"s": {"status": "verified", "is_complete": True}})
        assert tasks[0]["status"] == "completed"
        assert not tasks[0]["error"], "书写完了就不该再挂着修复错误"
        assert tasks[0]["current_stage"] == "project_closed"
        assert tasks[0]["task_history"][0]["error"] == "project_repair_requires_machine_repair"

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
