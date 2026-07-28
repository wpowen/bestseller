"""点了停止，自愈就必须放手。

停止一个内存任务只做一件事：在 **web 进程的内存里**给任务对象打个
``cancel_requested`` 标记。而让书继续跑下去的是**自愈**，它只读数据库——它根本
看不见那个标记，于是一分钟内又把书捞起来，新工作流照跑。

真机取证（2026-07-28，urban-power-reversal-1785211231）：

    project_repair   cancelled  worker_self_heal  13:36 → 13:47   ← 停止确实生效了
    chapter_pipeline running    worker_self_heal  13:39 → 13:49   ← 但书又被捞起来了

项目元数据里 cancel / paused / delete 标记**全是 null**。用户点一次停一次，
书转头又活——这就是「反反复复停不掉」。

第二个洞：``request_cancel`` 开头是
``if task.status not in ("running","queued"): return False``——任务卡一旦显示
``incomplete``（ARQ job 已消失，而书还在被自愈驱动），停止按钮**完全不做事**。
偏偏那正是用户最想按它的时候。

判据：停止是一个关于**书**的决定，不是关于某一行任务记录的决定。它必须落到
自愈能看见的地方。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.web import server

pytestmark = pytest.mark.unit


class TestStopReachesTheDatabase:
    def test_cancelling_an_in_memory_task_also_stops_the_project(self) -> None:
        source = inspect.getsource(server._request_visible_task_cancel)
        assert "_cancel_project_task" in source, (
            "停止内存任务时必须同时按项目停——自愈只读 DB，看不见内存标记"
        )

    def test_a_cancellable_task_stops_its_project_before_returning(self) -> None:
        """原实现找到内存任务就 return，永远走不到暂停项目那一步。

        提前 return 本身不是错——已完成的任务确实该直接 ``not_running``，把它的
        项目暂停反而会把一本正常完结的书按住。要钉的是：**可取消**的那条路径上，
        项目停止必须发生在返回成功之前。
        """

        source = inspect.getsource(server._request_visible_task_cancel)
        branch = source[source.index("task_manager.request_cancel(") :]
        stop_at = branch.index("_cancel_project_task")
        success_return = branch.index('return "cancel_requested"')
        assert stop_at < success_return, "报告已停止之前，必须真的把项目停掉"

    def test_a_finished_task_does_not_pause_its_project(self) -> None:
        source = inspect.getsource(server._request_visible_task_cancel)
        branch = source[source.index("task_manager.request_cancel(") :]
        assert branch.index('return "not_running"') < branch.index(
            "_cancel_project_task"
        ), "已完结的书不该被停止按钮按住"


class TestStopWorksOnAStaleCard:
    def test_request_cancel_is_not_gated_on_a_live_job(self) -> None:
        """任务卡显示 incomplete 时书往往仍被自愈驱动，那时更需要停止生效。"""

        source = inspect.getsource(server.WebTaskManager.request_cancel)
        assert 'not in ("running", "queued")' not in source, (
            "停止不能因为 ARQ job 已消失就拒绝执行——书还在跑"
        )


class TestPausingIsWhatSelfHealHonours:
    def test_self_heal_skips_paused_projects(self) -> None:
        """这条是上面所有修复成立的前提：暂停确实能挡住自愈。"""

        from bestseller.worker import self_heal

        source = inspect.getsource(self_heal)
        assert "production_paused" in source or "PAUSED" in source, (
            "自愈必须尊重暂停标记，否则停止在任何层面都无意义"
        )
