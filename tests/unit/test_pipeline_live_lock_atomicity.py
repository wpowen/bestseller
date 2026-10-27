"""Isolated Lua ownership regressions; never use application Redis settings."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.worker import tasks


@asynccontextmanager
async def isolated_redis(decode_responses=False):
    port = os.getenv("BESTSELLER_TEST_LOCK_REDIS_PORT")
    if port:
        from redis.asyncio import Redis

        # Explicit opt-in to the isolated container's loopback-only random port.
        assert 1024 < int(port) < 65536
        redis = Redis(host="127.0.0.1", port=int(port), decode_responses=decode_responses)
    else:
        fakeredis = pytest.importorskip("fakeredis.aioredis")
        pytest.importorskip("lupa")
        redis = fakeredis.FakeRedis(decode_responses=decode_responses)
    try:
        yield redis
    finally:
        await redis.aclose()


class OwnerSwitch:
    """Put a successor at the last client-side boundary before mutation.

    Old code observes old-owner via GET then mutates new-owner. Lua sees the
    successor before its atomic comparison and must leave it alone.
    """

    def __init__(self, redis):
        self.redis = redis

    async def get(self, key):
        observed = await self.redis.get(key)
        await self.redis.set(key, "successor", ex=120)
        return observed

    async def eval(self, script, numkeys, key, *args):
        await self.redis.set(key, "successor", ex=120)
        return await self.redis.eval(script, numkeys, key, *args)

    def __getattr__(self, name):
        return getattr(self.redis, name)


@pytest.mark.parametrize("operation", ["_release_pipeline_live", "_renew_pipeline_live_once"])
@pytest.mark.parametrize("decode_responses", [False, True])
def test_successor_survives_owner_change_at_mutation_boundary(operation, decode_responses):
    async def run():
        async with isolated_redis(decode_responses) as redis:
            slug = f"lock-regression:{uuid4().hex}"
            key = tasks._pipeline_live_key(slug)
            try:
                assert await tasks._acquire_pipeline_live(redis, slug, "old-owner") == "acquired"
                await getattr(tasks, operation)(OwnerSwitch(redis), slug, "old-owner")
                owner = await redis.get(key)
                assert owner in (b"successor", "successor")
                assert 110 <= await redis.ttl(key) <= 120
            finally:
                await redis.delete(key)

    asyncio.run(run())


@pytest.mark.parametrize("decode_responses", [False, True])
def test_owned_missing_and_stale_leases(decode_responses):
    async def run():
        async with isolated_redis(decode_responses) as redis:
            slug = f"lock-regression:{uuid4().hex}"
            key = tasks._pipeline_live_key(slug)
            old, new = uuid4().hex, uuid4().hex
            try:
                await tasks._release_pipeline_live(redis, slug, old)
                await tasks._renew_pipeline_live_once(redis, slug, old)
                assert not await redis.exists(key)
                assert await tasks._acquire_pipeline_live(redis, slug, old) == "acquired"
                assert await tasks._acquire_pipeline_live(redis, slug, new) == "held_by_other"
                await redis.expire(key, 120)
                await tasks._renew_pipeline_live_once(redis, slug, old)
                assert 290 <= await redis.ttl(key) <= tasks._PIPELINE_LIVE_TTL_SECONDS
                # Deterministic lease loss; no timing sleeps or production keys.
                await redis.delete(key)
                assert await tasks._acquire_pipeline_live(redis, slug, new) == "acquired"
                await redis.expire(key, 120)
                await tasks._renew_pipeline_live_once(redis, slug, old)
                await tasks._release_pipeline_live(redis, slug, old)
                assert await redis.get(key) in (new, new.encode())
                assert 110 <= await redis.ttl(key) <= 120
                await tasks._release_pipeline_live(redis, slug, new)
                assert not await redis.exists(key)
            finally:
                await redis.delete(key)

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["_release_pipeline_live", "_renew_pipeline_live_once"])
@pytest.mark.parametrize("error", [ConnectionError("offline"), RuntimeError("EVAL denied")])
def test_eval_failure_never_falls_back_to_client_side_writes(operation, error):
    class EvalUnavailable:
        get = AsyncMock(return_value="owner")
        delete = AsyncMock()
        expire = AsyncMock()
        eval = AsyncMock(side_effect=error)

    redis = EvalUnavailable()
    asyncio.run(getattr(tasks, operation)(redis, "lock-regression", "owner"))
    redis.eval.assert_awaited_once()
    redis.get.assert_not_awaited()
    redis.delete.assert_not_awaited()
    redis.expire.assert_not_awaited()


@pytest.mark.parametrize("entry_name", ["run_autowrite_task", "run_project_pipeline_task"])
def test_each_entry_uses_a_fresh_lease_and_passes_it_to_guard(monkeypatch, entry_name):
    """Exercise lock wiring inside entry; ledger decorator is outside this unit."""
    from uuid import UUID

    acquired, guarded = [], []

    async def acquire(redis, slug, token):
        acquired.append(token)
        return "acquired"

    class StopAtGuardError(Exception):
        pass

    @asynccontextmanager
    async def guard(redis, slug, token, state):
        guarded.append((token, state))
        raise StopAtGuardError
        yield  # pragma: no cover -- async context-manager shape

    reporter = AsyncMock()
    monkeypatch.setattr(tasks, "RedisProgressReporter", lambda *args: reporter)
    monkeypatch.setattr(tasks, "set_ambient", lambda *args: None)
    monkeypatch.setattr(tasks, "set_cancellation_checker", lambda *args: None)
    monkeypatch.setattr(tasks, "make_sync_callback", lambda *args: None)
    for name in (
        "_skip_archived_project_if_needed",
        "_skip_halted_project_if_needed",
        "_skip_outline_replan_project_if_needed",
    ):
        monkeypatch.setattr(tasks, name, AsyncMock(return_value=None))
    monkeypatch.setattr(tasks, "_acquire_pipeline_live", acquire)
    monkeypatch.setattr(tasks, "_pipeline_live_guard", guard)
    job_id = "project-pipeline:heal:lock-regression"

    async def run():
        for _ in range(2):
            with pytest.raises(StopAtGuardError):
                await getattr(tasks, entry_name).__wrapped__(
                    {"redis": object(), "job_id": job_id},
                    job_id,
                    {"project_slug": "lock-regression"},
                )

    asyncio.run(run())
    assert acquired[0] != acquired[1]
    assert all(token != job_id and UUID(token).version == 4 for token in acquired)
    assert guarded == [(token, "acquired") for token in acquired]
    assert all(call.args[1]["workflow_run_id"] == job_id for call in reporter.emit.await_args_list)
