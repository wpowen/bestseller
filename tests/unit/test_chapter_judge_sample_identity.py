"""Independent draws keep stable slots and drain before propagating execution stops."""

import asyncio
from contextlib import asynccontextmanager
import json

import pytest

from bestseller.infra.db import session as db_session
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.llm import LLMCompletionResult, bind_execution_operation
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


class NonterminalBusyError(ExecutionBudgetError):
    terminal = False


def completion():
    return LLMCompletionResult(
        content=json.dumps(
            {
                "pass": True,
                "overall_score": 0.95,
                "dimension_scores": {"decision_intelligence": 0.95},
                "blocking_issues": [],
                "rewrite_plan": {"scope": "chapter"},
            }
        ),
        provider="mock",
        model_name="mock-critic",
    )


def install_pool(monkeypatch, *, error=None, pending=None, closed=None, exit_errors=None):
    @asynccontextmanager
    async def pool():
        if error is not None:
            raise error
        try:
            class OwnedSession:
                async def commit(self):
                    pass

                async def rollback(self):
                    pass

            yield OwnedSession()
        except BaseException as draw_error:
            if exit_errors is not None:
                exit_errors.append(draw_error)
            raise
        finally:
            if closed is not None:
                closed.append(bool(pending))

    monkeypatch.setattr(db_session, "get_server_session", pool)


async def run(samples):
    with bind_execution_operation("review.same-body", sample_slot=0):
        return await judge.judge_chapter_commercial_quality_stable(
            object(),
            load_settings(env={}),
            chapter_number=80,
            content_md="同一份章节正文。",
            samples=samples,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("samples", [1, 3])
@pytest.mark.parametrize("pool_ready", [False, True])
async def test_identical_draws_use_distinct_stable_slots(monkeypatch, samples, pool_ready):
    install_pool(monkeypatch, error=None if pool_ready else RuntimeError("pool uninitialized"))
    calls, pending = [], set()
    legacy = {0: "quarantined_charged"}

    async def complete(session, settings, request):
        assert request.sample_slot not in legacy
        key = (request.system_prompt, request.user_prompt, request.sample_slot)
        calls.append(request)
        if key in pending:
            raise ExecutionBudgetError("execution_operation_inflight")
        pending.add(key)
        try:
            await asyncio.sleep(0)
            return completion()
        finally:
            pending.remove(key)

    monkeypatch.setattr(judge, "complete_text", complete)
    for _ in range(2):
        await run(samples)
    expected_slots = [f"chapter-quality.independent-v2:{index}" for index in range(samples)]
    assert [r.sample_slot for r in calls] == expected_slots * 2
    assert len({(r.system_prompt, r.user_prompt) for r in calls}) == 1
    assert not pending
    assert legacy == {0: "quarantined_charged"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ExecutionBudgetError("execution_operation_inflight"),
        RuntimeError("paid call failed"),
        NonterminalBusyError("execution_operation_inflight"),
    ],
)
async def test_paid_error_drains_siblings_without_fallback(monkeypatch, error):
    pending, finished, closed, exit_errors = set(), set(), [], []
    install_pool(monkeypatch, pending=pending, closed=closed, exit_errors=exit_errors)
    calls = []

    async def complete(session, settings, request):
        index = len(calls)
        calls.append(request)
        pending.add(index)
        try:
            if index == 1:
                raise error
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return completion()
        finally:
            pending.remove(index)
            finished.add(index)

    monkeypatch.setattr(judge, "complete_text", complete)
    with pytest.raises(type(error)) as caught:
        await run(3)
    assert caught.value is error
    assert len(calls) == 3
    assert finished == {0, 1, 2}
    assert exit_errors == []
    assert closed == [False, False, False]


@pytest.mark.asyncio
async def test_typed_stop_during_pool_setup_never_starts_a_draw(monkeypatch):
    stop = ExecutionBudgetError("execution_ledger_unavailable")
    install_pool(monkeypatch, error=stop)
    calls = []

    async def complete(*args):
        calls.append(args)
        return completion()

    monkeypatch.setattr(judge, "complete_text", complete)
    with pytest.raises(ExecutionBudgetError) as caught:
        await run(3)
    assert caught.value is stop
    assert not calls


@pytest.mark.asyncio
async def test_caller_cancellation_drains_before_pool_closes(monkeypatch):
    pending, closed = set(), []
    install_pool(monkeypatch, pending=pending, closed=closed)
    all_started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def complete(session, settings, request):
        index = len(calls)
        calls.append(request)
        pending.add(index)
        if len(calls) == 3:
            all_started.set()
        try:
            await release.wait()
        finally:
            await asyncio.sleep(0)
            pending.remove(index)

    monkeypatch.setattr(judge, "complete_text", complete)
    task = asyncio.create_task(run(3))
    await asyncio.wait_for(all_started.wait(), 1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert not closed
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 3
    assert not pending
    assert closed == [False, False, False]
