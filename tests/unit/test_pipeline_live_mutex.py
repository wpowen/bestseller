"""项目级管线活体互斥锁：同一本书任一时刻只许一条 project 管线在跑。

2026-08-23 真机：docker rebuild 杀掉跑书中的 worker 后，「卷纲完成的继续
推进」与「self-heal 补救」同秒各拉起一条 project_pipeline——两条管线在
worker-1/worker-2 上同时写第 43 章（55 秒 4 个草稿版本、双 writer 调用）。
既有防线全部失守，各有结构性原因：
- API 层的 active-run 检查 + start 锁只护 API 入口，worker 侧入队不经过；
- self-heal 的所有权检查只枚举 heal 系 job id，看不见其他来源的管线；
- workflow_runs 行的心跳新鲜度不可信——项目级批量心跳会把同项目**所有**
  活跃行一起刷新，僵尸行被活进程「保鲜」；
- arq abort 从未生效（worker 未开 allow_abort_jobs，abort 集合里躺着一堆
  历史书的僵尸条目）。

修=redis 活体互斥：SET NX + TTL + 持锁进程续租。锁和**进程**绑定而不是和
数据库行绑定——进程死了 TTL 到期自动让位，僵尸行骗不了它；redis 不可用时
降级放行（宁可重复也不阻塞合法工作，与 start 锁同哲学）。
"""

from __future__ import annotations

# ruff: noqa: RUF002, RUF003 — 中文标点是刻意的。
import asyncio

from bestseller.worker import tasks as worker_tasks


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)

    async def expire(self, key, ttl):
        if key in self.store:
            self.ttls[key] = ttl
            return True
        return False

    async def eval(self, script, numkeys, key, owner, *args):
        # Compatibility for these lifecycle tests. Actual Lua execution and
        # interleavings are covered by test_pipeline_live_lock_atomicity.py.
        assert numkeys == 1
        if self.store.get(key) != owner:
            return 0
        if "'DEL'" in script:
            await self.delete(key)
            return 1
        if "'EXPIRE'" in script:
            return int(await self.expire(key, int(args[0])))
        raise NotImplementedError(script)


class ExplodingRedis:
    def __getattr__(self, name):
        async def _boom(*a, **k):
            raise ConnectionError("redis down")

        return _boom


def test_first_acquirer_wins_second_is_refused() -> None:
    async def run():
        redis = FakeRedis()
        first = await worker_tasks._acquire_pipeline_live(redis, "book-a", "run-1")
        second = await worker_tasks._acquire_pipeline_live(redis, "book-a", "run-2")
        return first, second

    first, second = asyncio.run(run())
    assert first == "acquired"
    assert second == "held_by_other"


def test_different_projects_do_not_contend() -> None:
    async def run():
        redis = FakeRedis()
        a = await worker_tasks._acquire_pipeline_live(redis, "book-a", "run-1")
        b = await worker_tasks._acquire_pipeline_live(redis, "book-b", "run-2")
        return a, b

    assert asyncio.run(run()) == ("acquired", "acquired")


def test_release_only_removes_own_token() -> None:
    async def run():
        redis = FakeRedis()
        await worker_tasks._acquire_pipeline_live(redis, "book-a", "run-1")
        # 他人持锁时释放必须是 no-op（防止迟到的旧进程踢掉新任锁主）。
        await worker_tasks._release_pipeline_live(redis, "book-a", "run-2")
        still = await worker_tasks._acquire_pipeline_live(redis, "book-a", "run-3")
        await worker_tasks._release_pipeline_live(redis, "book-a", "run-1")
        freed = await worker_tasks._acquire_pipeline_live(redis, "book-a", "run-3")
        return still, freed

    still, freed = asyncio.run(run())
    assert still == "held_by_other"
    assert freed == "acquired"


def test_redis_down_degrades_open() -> None:
    # 与 API start 锁同哲学：基础设施抖动不得阻塞合法工作。
    async def run():
        return await worker_tasks._acquire_pipeline_live(
            ExplodingRedis(), "book-a", "run-1"
        )

    assert asyncio.run(run()) == "degraded"


def test_renew_extends_only_while_owned() -> None:
    async def run():
        redis = FakeRedis()
        await worker_tasks._acquire_pipeline_live(redis, "book-a", "run-1")
        key = worker_tasks._pipeline_live_key("book-a")
        redis.ttls[key] = 1  # 模拟即将过期
        await worker_tasks._renew_pipeline_live_once(redis, "book-a", "run-1")
        extended = redis.ttls[key]
        # 锁主换人后旧进程的续租必须不生效。
        redis.store[key] = "run-9"
        redis.ttls[key] = 1
        await worker_tasks._renew_pipeline_live_once(redis, "book-a", "run-1")
        return extended, redis.ttls[key]

    extended, not_extended = asyncio.run(run())
    assert extended == worker_tasks._PIPELINE_LIVE_TTL_SECONDS
    assert not_extended == 1


def test_both_worker_entries_consult_the_mutex() -> None:
    """接线钉：两个 project 管线入口必须都过 _acquire_pipeline_live。

    不是源码字符串断言——monkeypatch 掉锁函数让它报 held_by_other，
    驱动真入口，断言任务体在锁处短路返回 skipped_duplicate_pipeline。
    """

    calls: list[str] = []

    async def _held(redis, slug, run_id):
        calls.append(slug)
        return "held_by_other"

    async def run(entry):
        import unittest.mock as mock

        class _Reporter:
            def __init__(self, *a, **k): ...
            async def emit(self, *a, **k): ...

        with (
            mock.patch.object(worker_tasks, "get_server_session", _isolated_worker_session),
            mock.patch.object(worker_tasks, "_acquire_pipeline_live", _held),
            mock.patch.object(worker_tasks, "RedisProgressReporter", _Reporter),
            mock.patch.object(worker_tasks, "set_ambient", lambda *_: None),
            mock.patch.object(
                worker_tasks, "make_sync_callback", lambda *_: (lambda *a, **k: None)
            ),
            mock.patch.object(
                worker_tasks,
                "_skip_archived_project_if_needed",
                mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                worker_tasks,
                "_skip_halted_project_if_needed",
                mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                worker_tasks,
                "_skip_outline_replan_project_if_needed",
                mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(worker_tasks, "get_settings", lambda: None),
        ):
            return await entry(
                {"redis": FakeRedis()},
                "run-x",
                {"project_slug": "book-a"},
            )

    for entry in (
        worker_tasks.run_autowrite_task,
        worker_tasks.run_project_pipeline_task,
    ):
        result = asyncio.run(run(entry))
        assert result["status"] == "skipped_duplicate_pipeline", entry.__name__
    assert calls == ["book-a", "book-a"]


from contextlib import asynccontextmanager


@asynccontextmanager
async def _isolated_worker_session():
    """Native preflight tables instead of bypassing the decorated worker guard."""
    from datetime import datetime,timedelta,timezone
    from uuid import uuid4
    from sqlalchemy import JSON,MetaData
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from sqlalchemy.schema import CreateTable
    from bestseller.infra.db.models import ProjectModel,BookProductionControlModel,ExecutionBudgetModel
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    def ddl(c):
        for model in (ProjectModel,BookProductionControlModel,ExecutionBudgetModel):
            table=model.__table__.to_metadata(MetaData())
            for col in table.c:
                if isinstance(col.type,JSONB):col.type=JSON()
                if col.server_default and '::jsonb' in str(col.server_default.arg):col.server_default=None
            c.execute(CreateTable(table,include_foreign_key_constraints=[]))
    try:
        async with engine.begin() as c:await c.run_sync(ddl)
        factory=async_sessionmaker(engine,expire_on_commit=False)
        async with factory() as s:
            p=ProjectModel(id=uuid4(),slug='book-a',title='test',genre='test',target_chapters=4,target_word_count=8000,metadata_json={})
            s.add_all([p,BookProductionControlModel(project_id=p.id,desired_state='run',command_serial=1),
                ExecutionBudgetModel(scope_id=f'project:{p.id}',status='active',max_calls=20,max_tokens=10000,
                    calls_used=0,tokens_charged=0,deadline_at=datetime.now(timezone.utc)+timedelta(days=1),metadata_json={})])
            await s.commit()
            yield s
            await s.commit()
    finally:await engine.dispose()
