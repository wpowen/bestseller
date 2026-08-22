"""DB 里还有活跃的流水线时，resume 必须拒绝，不能制造第二个执行器。

2026-08-22 真机：书停在 `machine_repair_required`（任务卡片 = incomplete），
我点了 resume，结果 **两个 project_pipeline + 两个 chapter_pipeline 同时
running**，几分钟后

    asyncpg.exceptions.DeadlockDetectedError: deadlock detected
    DETAIL: Process 44538 waits for ShareLock on transaction 2041602;
            blocked by process 43362.

整本书失败在第 22 章。

根因是「同一事实住两地」：

* web 的任务卡片（内存 + 落盘）说 `incomplete`
* `workflow_runs` 表说 `running`

`resume_autowrite_task` 的忙碌判据只看前者
（`task.status in ("running", "queued")`），于是放行。

修法：resume 前按**事实源**对账——DB 里有活跃 workflow_run 就拒绝，
并把它报给用户，而不是静默地起第二个执行器。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002 — 中文标点是刻意的。
import inspect

from bestseller.web import server


def test_resume_checks_the_database_not_only_the_card() -> None:
    """恢复路径必须查 workflow_runs，而不是只信内存卡片的状态。"""

    source = inspect.getsource(server)
    anchor = source.index("task_manager.resume_autowrite_task(")
    window = source[max(0, anchor - 3000) : anchor]
    assert "_has_live_pipeline_run" in window, (
        "resume 前必须按事实源对账：DB 里还有活跃流水线时不能再起一个执行器"
    )


def test_the_liveness_probe_looks_at_running_workflow_rows() -> None:
    probe = inspect.getsource(server._has_live_pipeline_run_async)
    assert "WorkflowRunModel" in probe
    assert "running" in probe


def test_probe_failure_does_not_block_resume() -> None:
    """查不到就放行——探针本身不该变成新的卡死来源。"""

    probe = inspect.getsource(server._has_live_pipeline_run_async)
    assert "except Exception" in probe
    assert "return False" in probe
