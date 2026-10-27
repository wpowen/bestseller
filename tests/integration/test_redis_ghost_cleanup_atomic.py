"""Run only against an explicitly supplied disposable loopback Redis.

Every job/key written here has a fresh isolated prefix; no worker is started.
"""

import asyncio
import os
import pickle
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

from arq.connections import ArqRedis
import pytest
import pytest_asyncio

from bestseller.worker import self_heal as sh

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def isolated_job():
    url = os.environ.get("BESTSELLER_RACE_TEST_REDIS_URL")
    if not url:
        pytest.skip("requires an explicitly created disposable loopback Redis")
    assert urlsplit(url).hostname in {"127.0.0.1", "localhost", "::1"}
    job_id = f"codex-race-test:{uuid4().hex}"
    keys = [f"arq:{kind}:{job_id}" for kind in ("job", "in-progress", "result", "retry")]
    client = ArqRedis.from_url(url, decode_responses=False)
    job_ids = [job_id]
    await client.ping()
    try:
        yield SimpleNamespace(redis=client, job_id=job_id, keys=keys, job_ids=job_ids)
    finally:
        for owned_id in job_ids:
            await client.delete(
                *(f"arq:{kind}:{owned_id}" for kind in ("job", "in-progress", "result", "retry"))
            )
            await client.zrem("arq:queue", owned_id)
        await client.aclose()


class BeforeCleanup:
    """Inject a second client's transition just before the actual Lua command."""

    def __init__(self, redis, transition):
        self.redis = redis
        self.transition = transition

    def __getattr__(self, name):
        return getattr(self.redis, name)

    async def eval(self, script, numkeys, *args):
        if "bestseller-ghost-cleanup-v1" in script:
            await self.transition()
        return await self.redis.eval(script, numkeys, *args)

    async def delete(self, *keys):
        if self.transition is not None:
            await self.transition()
        return await self.redis.delete(*keys)


async def test_clears_unqueued_bare_job(isolated_job):
    j = isolated_job
    await j.redis.set(j.keys[0], b"old-payload", ex=60)
    assert await sh._clear_stale_heal_job_if_needed(j.redis, j.job_id)
    assert await j.redis.exists(*j.keys) == 0


async def test_terminal_result_and_retry_are_reclaimable(isolated_job):
    j = isolated_job
    await j.redis.mset({j.keys[2]: b"old-result", j.keys[3]: b"1"})
    assert await sh._clear_stale_heal_job_if_needed(j.redis, j.job_id)
    assert await j.redis.exists(*j.keys) == 0


async def test_old_queue_score_does_not_authorize_deleting_active_lock(isolated_job):
    j = isolated_job
    await j.redis.mset({j.keys[0]: b"live-payload", j.keys[1]: b"1", j.keys[3]: b"1"})
    await j.redis.zadd("arq:queue", {j.job_id: 1})
    assert not await sh._clear_stale_heal_job_if_needed(j.redis, j.job_id)
    assert await j.redis.get(j.keys[1]) == b"1"
    assert await j.redis.zscore("arq:queue", j.job_id) == 1


@pytest.mark.parametrize("transition_kind", ["queue", "start", "replace", "result", "retry"])
async def test_changed_owner_is_preserved_at_atomic_boundary(isolated_job, transition_kind):
    j = isolated_job
    await j.redis.set(j.keys[0], b"old-payload", ex=60)

    async def transition():
        if transition_kind == "queue":
            await j.redis.zadd("arq:queue", {j.job_id: time.time() * 1000 + 10000})
        elif transition_kind == "start":
            await j.redis.set(j.keys[1], b"new-executor", ex=60)
        else:
            index = {"replace": 0, "result": 2, "retry": 3}[transition_kind]
            await j.redis.set(j.keys[index], b"new-owner-state", ex=60)

    assert not await sh._clear_stale_heal_job_if_needed(
        BeforeCleanup(j.redis, transition), j.job_id
    )
    assert await j.redis.get(j.keys[0]) is not None
    if transition_kind == "start":
        assert await j.redis.get(j.keys[1]) == b"new-executor"
    elif transition_kind in {"replace", "result", "retry"}:
        index = {"replace": 0, "result": 2, "retry": 3}[transition_kind]
        assert await j.redis.get(j.keys[index]) == b"new-owner-state"


async def test_same_payload_with_new_lease_is_not_an_old_orphan(isolated_job):
    j = isolated_job
    payload = pickle.dumps({"t": int((time.time() - 10000) * 1000)})
    await j.redis.set(j.keys[0], payload, px=10000)
    await j.redis.set(j.keys[3], b"1")

    async def renew():
        await j.redis.pexpire(j.keys[0], 30000)

    assert not await sh._clear_stale_heal_job_if_needed(BeforeCleanup(j.redis, renew), j.job_id)
    assert await j.redis.get(j.keys[0]) == payload


async def test_two_reclaimers_only_one_deletes(isolated_job):
    j = isolated_job
    await j.redis.set(j.keys[0], b"old-payload", ex=60)
    results = await asyncio.gather(
        *(sh._clear_stale_heal_job_if_needed(j.redis, j.job_id) for _ in range(2))
    )
    assert sorted(results) == [False, True]


async def test_dead_lease_expires_before_orphan_is_reclaimed(isolated_job):
    j = isolated_job
    payload = pickle.dumps({"t": int((time.time() - 10000) * 1000)})
    await j.redis.mset({j.keys[0]: payload, j.keys[3]: b"1"})
    await j.redis.set(j.keys[1], b"1", px=30)
    assert not await sh._clear_stale_heal_job_if_needed(j.redis, j.job_id)
    for _ in range(100):
        if not await j.redis.exists(j.keys[1]):
            break
        await asyncio.sleep(0.005)
    assert not await j.redis.exists(j.keys[1])
    assert await sh._clear_stale_heal_job_if_needed(j.redis, j.job_id)


async def test_lua_failure_does_not_fall_back_to_unconditional_delete(isolated_job):
    j = isolated_job
    await j.redis.set(j.keys[0], b"old-payload", ex=60)

    class Unavailable(BeforeCleanup):
        async def eval(self, *args):
            raise ConnectionError("injected script transport failure")

    assert not await sh._clear_stale_heal_job_if_needed(Unavailable(j.redis, None), j.job_id)
    assert await j.redis.get(j.keys[0]) == b"old-payload"


@pytest.mark.parametrize("kind", ["autowrite", "repair", "project_pipeline", "outline_replan"])
async def test_real_arq_requeues_terminal_debris_once_without_a_worker(isolated_job, kind):
    j = isolated_job
    owner_ids = {
        "autowrite": sh._autowrite_heal_job_id,
        "repair": sh._repair_heal_job_id,
        "project_pipeline": sh._project_pipeline_heal_job_id,
        "outline_replan": sh._outline_replan_heal_job_id,
    }
    job_id = owner_ids[kind](j.job_id)
    j.job_ids.append(job_id)
    await j.redis.set(f"arq:result:{job_id}", b"old-terminal-result")
    stuck = sh.StuckProject(
        project_id="offline-only",
        slug=j.job_id,
        reason="missing_drafts",
        stuck_at_chapter=2,
        chapters_total=3,
        chapters_with_draft=1,
        heal_kind=kind,
    )
    assert await sh._requeue_stuck_project(j.redis, stuck) == job_id
    payload = await j.redis.get(f"arq:job:{job_id}")
    assert payload is not None
    assert await j.redis.zscore("arq:queue", job_id) is not None
    assert await sh._requeue_stuck_project(j.redis, stuck) is None
    assert await j.redis.get(f"arq:job:{job_id}") == payload
