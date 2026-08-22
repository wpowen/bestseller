"""心跳批量 UPDATE 必须 SKIP LOCKED——否则与长事务互相持锁成死锁环。

2026-08-23 真机定罪：`_touch_workflow_run_heartbeat` 的项目级批量心跳
`UPDATE workflow_runs SET updated_at=now() WHERE project_id=... AND status IN
(active)` 按扫描序逐行加锁、实测等锁 18 秒；heal 的长事务反向持锁，postgres
报 deadlock detected，把 heal 任务整个杀掉（arq 再补一刀
`KeyError: job_tasks`）。

修法不是重试而是拆环：心跳只为「防自愈把活跃 workflow 当孤儿收走」而刷
updated_at——**一行正被别的事务 UPDATE 时，它的 updated_at 本来就会被那个
事务刷新**，心跳跳过它零损失。所以两条 UPDATE 都改经
`SELECT id ... FOR UPDATE SKIP LOCKED` 子查询，本事务从此不等任何行锁，
在锁图里成为叶子，无法再参与死锁环。

断言编译后的 postgres SQL（真实产物），不是源码字符串。
"""

from __future__ import annotations

# ruff: noqa: RUF002 — 中文标点是刻意的。
import datetime as _dt

from sqlalchemy.dialects import postgresql

from bestseller.worker import tasks as worker_tasks


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_project_heartbeat_update_locks_with_skip_locked() -> None:
    stmt = worker_tasks._project_heartbeat_stmt(
        project_slug="some-book",
        active_since=_dt.datetime(2026, 8, 23, tzinfo=_dt.UTC),
    )
    sql = _compiled(stmt)
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "UPDATE workflow_runs" in sql


def test_single_run_heartbeat_update_locks_with_skip_locked() -> None:
    import uuid

    stmt = worker_tasks._single_run_heartbeat_stmt(uuid.uuid4())
    sql = _compiled(stmt)
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "UPDATE workflow_runs" in sql


def test_project_heartbeat_still_scopes_to_active_rows() -> None:
    """SKIP LOCKED 不许换来范围放宽：仍只摸本项目、活跃状态、心跳类型。"""

    stmt = worker_tasks._project_heartbeat_stmt(
        project_slug="some-book",
        active_since=None,
    )
    sql = _compiled(stmt)
    assert "project_id" in sql
    assert "workflow_type IN" in sql
    assert "status IN" in sql
