"""委托型僵尸任务卡对账（2026-08-18 定罪）。

真机链路：重启把委托给 worker 的自愈 job 杀掉（arq ``max retries 1
exceeded``），job 永不回写卡片——卡片停在 running，
``has_active_task_for_project`` 永远拒绝删书。cancel 返回 ok 但卡不动，
用户看到的是「停不掉也删不掉」。

判据（同 2026-07-28 家族）：卡片不是事实源。执行活不活要问 DB
（running workflow）和 ARQ 队列（heal job key）；两边都死才许判卡死，
任何一边活着（含探测失败 fail-safe）都不动。只动委托型卡——in-process
跑者的执行体是 web 线程，DB/队列探测看不见它。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.web import server
from bestseller.web.server import WebTaskManager, WebTaskState

pytestmark = pytest.mark.unit


def _make_manager_with_task(*, delegated: bool, status: str = "running") -> WebTaskManager:
    mgr = WebTaskManager(persist_path=None)
    task = WebTaskState(
        task_id="t-1",
        task_type="autowrite",
        status=status,
        created_at="2026-08-18T00:00:00+00:00",
        updated_at="2026-08-18T00:00:00+00:00",
        project_slug="slug-x",
    )
    if delegated:
        task.record_event("delegated_to_worker_self_heal", {"reason": "test"})
    task.record_event("planning_outline_attempt_started", {})
    with mgr._lock:
        mgr._tasks[task.task_id] = task
    return mgr


def test_zombie_delegated_card_is_failed_when_ground_truth_dead():
    mgr = _make_manager_with_task(delegated=True)
    n = mgr.reconcile_delegated_zombie_tasks(
        "slug-x", db_workflow_active=False, heal_job_active=False
    )
    assert n == 1
    assert not mgr.has_active_task_for_project("slug-x"), "对账后删除不再被僵尸卡挡住"


def test_live_ground_truth_protects_the_card():
    for kwargs in (
        {"db_workflow_active": True, "heal_job_active": False},
        {"db_workflow_active": False, "heal_job_active": True},
    ):
        mgr = _make_manager_with_task(delegated=True)
        assert mgr.reconcile_delegated_zombie_tasks("slug-x", **kwargs) == 0
        assert mgr.has_active_task_for_project("slug-x")


def test_in_process_card_never_reconciled():
    # 没有 delegated 事件 = 执行体在 web 自己线程里，DB/队列看不见它——不许动。
    mgr = _make_manager_with_task(delegated=False)
    n = mgr.reconcile_delegated_zombie_tasks(
        "slug-x", db_workflow_active=False, heal_job_active=False
    )
    assert n == 0
    assert mgr.has_active_task_for_project("slug-x")


def test_delete_path_reconciles_before_refusing():
    src = inspect.getsource(server._delete_project_full)
    assert "reconcile_delegated_zombie_tasks" in src, "删除前必须对账僵尸卡"
    # fail-safe：探测异常必须按活着处理
    assert "heal_job_active = True" in src
    assert "db_workflow_active = True" in src
