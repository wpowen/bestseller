"""规划僵尸行收尸（2026-08-19 真机两次事故）。

病理：web 重启杀掉在飞规划 → generate_volume_plan 行以 running 僵在库里
→ worker **没重启**所以启动清理规则不触发 → planning 类此前只有 3 小时
长窗口（因为它们从不写心跳，updated_at 停在开跑那刻）→ 僵尸行让
`_has_active_pipeline_run` 判定「有活跃流程」，自愈跳过不救 → 书永久
卡死（1787068477 手工清行、1787158026 复发）。

修：①planner 每完成一个章纲批次刷新自己的工作流行（心跳）
    ②规划类纳入 45 分钟中窗口收尸（停跳即死；单次 planner 调用 ~15 分钟
      +重试，45 分钟可安全判死）
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import planner
from bestseller.worker import self_heal

pytestmark = pytest.mark.unit


def test_outline_batches_heartbeat_their_workflow_row():
    src = inspect.getsource(planner._generate_volume_outline_batched)
    assert "sa_update(WorkflowRunModel)" in src, "章纲批次必须刷新工作流行"
    assert "updated_at=datetime.now(UTC)" in src
    # best-effort：心跳失败绝不能中断规划
    hb = src[src.find("outline batch heartbeat") - 800 : src.find("outline batch heartbeat")]
    assert "except Exception" in hb


def test_planning_types_reap_on_medium_window():
    assert self_heal.ORPHAN_PLANNING_WORKFLOW_TIMEOUT_SECONDS == 45 * 60
    types = self_heal._PLANNING_HEARTBEAT_REAP_WORKFLOW_TYPES
    assert "generate_volume_plan" in types, "真机僵尸行正是这个类型"
    assert "generate_foundation_plan" in types
    assert "autowrite_pipeline" in types
    # 窗口必须显著短于旧的 3 小时孤儿超时，又长于单次 planner 调用
    assert 15 * 60 < self_heal.ORPHAN_PLANNING_WORKFLOW_TIMEOUT_SECONDS < 3 * 60 * 60


def test_reaper_query_uses_planning_cutoff():
    src = inspect.getsource(self_heal)
    assert "planning_cutoff = now - _dt.timedelta(" in src
    assert "_PLANNING_HEARTBEAT_REAP_WORKFLOW_TYPES),\n            WorkflowRunModel.updated_at < planning_cutoff," in src


def test_planning_types_are_reapable_and_blocking():
    # 收尸集必须是可收尸类型的子集，否则条件永不命中（静默失效）
    assert self_heal._PLANNING_HEARTBEAT_REAP_WORKFLOW_TYPES <= self_heal._REAPABLE_WORKFLOW_TYPES
    # 且它们确实是阻塞自愈的类型——这正是收尸的动机
    assert self_heal._PLANNING_HEARTBEAT_REAP_WORKFLOW_TYPES <= self_heal._SELF_HEAL_BLOCKING_WORKFLOW_TYPES
