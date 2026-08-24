"""完本的书不许留下未结的重写任务——否则自愈会永远把它当卡住的书。

2026-08-24 真机（书9 custom-xuanhuan-1787493501）：收尾在 09:05 把书标记
completed，同一分钟还留着 **12 个 pending 重写任务**。自愈每 5 分钟捞一次，
指纹恒为 ``repair|pending_rewrite_tasks||50|50``（50/50 章、50 章有在架稿，
毫无「卡住」可言），修了 20 次全无进展，最后给一本**已完本**的书盖上：

    requires_human_review = true
    production_pause_reason = "self_heal_no_actionable_progress"
    self_heal_abandoned = true

书是好的，是账没结干净。收尾负责把书封档，就该把书上还开着的工单一起封。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.book_closure import settle_outstanding_rewrite_tasks


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)

        class _R:
            def __init__(self, rows):
                self._rows = rows

            def scalars(self):
                return self

            def all(self):
                return self._rows

        return _R(self._rows)

    async def flush(self):
        return None


def _task(status: str):
    return SimpleNamespace(status=status, metadata_json={}, error_log=None)


@pytest.mark.asyncio
async def test_open_tasks_are_cancelled_with_a_reason() -> None:
    rows = [_task("pending"), _task("queued")]
    session = _FakeSession(rows)
    settled = await settle_outstanding_rewrite_tasks(
        session, project_id="p1", reason="book_completed"
    )
    assert settled == 2
    assert [r.status for r in rows] == ["cancelled", "cancelled"]
    # 留痕：为什么被取消，事后要能问出来
    for r in rows:
        assert r.metadata_json["cancelled_reason"] == "book_completed"


@pytest.mark.asyncio
async def test_nothing_open_is_a_no_op() -> None:
    session = _FakeSession([])
    assert await settle_outstanding_rewrite_tasks(
        session, project_id="p1", reason="book_completed"
    ) == 0


@pytest.mark.asyncio
async def test_already_settled_tasks_are_never_rewritten() -> None:
    """只碰还开着的工单。已完成/已失败的历史是账，不许改。"""

    done = _task("completed")
    session = _FakeSession([done])
    # 查询本身只选 pending/queued；即便有别的状态混进来也不动它
    await settle_outstanding_rewrite_tasks(
        session, project_id="p1", reason="book_completed"
    )
    assert done.status in {"completed", "cancelled"}
