"""Paid review layers cannot invent a shorter deadline than their gateway."""
import asyncio
from contextlib import asynccontextmanager

import pytest

from bestseller.infra.db import session as db_session
from bestseller.services import chapter_llm_quality_judge as judge, reviews
from bestseller.services.budget import ExecutionBudgetError
from bestseller.domain.llm_quality_judge import quality_judge_result_from_mapping
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_serialized_paid_cohort_uses_each_native_deadline(monkeypatch):
    """Exercise the real no-server-pool path used by a one-shot web task."""
    @asynccontextmanager
    async def unavailable_pool():
        raise RuntimeError("Database not initialized")
        yield  # pragma: no cover

    monkeypatch.setattr(db_session, 'get_server_session', unavailable_pool)
    real_wait_for = asyncio.wait_for
    observed, settled, calls = [], [], []

    async def bounded(awaitable, timeout, **kwargs):
        observed.append(timeout)
        if timeout == 240:
            awaitable.close()
            pytest.fail('aggregate review timer cancels an already paid native call')
        return await real_wait_for(awaitable, timeout=timeout, **kwargs)

    async def sample(session, settings, **kwargs):
        slot = kwargs['sample_slot']
        calls.append(slot)
        async def native():
            await asyncio.sleep(.02)
            settled.append(slot)
            return quality_judge_result_from_mapping({'pass': True, 'overall_score': .9,
                'dimension_scores': {}, 'issues': [], 'rewrite_plan': {'scope': 'none'}},
                scope='chapter', min_overall=.8)
        return await asyncio.wait_for(native(), timeout=.1)

    monkeypatch.setattr(asyncio, 'wait_for', bounded)
    monkeypatch.setattr(judge, 'judge_chapter_commercial_quality', sample)
    result = await reviews._await_optional_chapter_review_llm(
        judge.judge_chapter_commercial_quality_stable(object(), load_settings(env={}),
            chapter_number=22, content_md='独立山野故事。', samples=3),
        label='chapter_llm_commercial_judge', chapter_number=22)
    assert result.infrastructure_failure is False
    assert result.overall_score == .9
    assert calls == settled == [f'chapter-quality.independent-v2:{i}' for i in range(3)]
    assert observed == [.1] * 3


async def test_gateway_unknown_is_not_downgraded_or_retried():
    calls = []
    async def native():
        calls.append('one physical gateway outcome')
        raise ExecutionBudgetError('execution_result_unknown', 'same-scope')
    with pytest.raises(ExecutionBudgetError) as error:
        await reviews._await_optional_chapter_review_llm(native(),
            label='chapter_review', chapter_number=3)
    assert error.value.code == 'execution_result_unknown'
    assert calls == ['one physical gateway outcome']


async def test_explicit_operator_cancellation_still_propagates():
    started, stopped = asyncio.Event(), asyncio.Event()
    async def native():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    task = asyncio.create_task(reviews._await_optional_chapter_review_llm(native(),
        label='chapter_review', chapter_number=4))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


async def test_explicit_ancillary_timer_remains_bounded():
    with pytest.raises(TimeoutError, match='unpaid_diagnostic timed out'):
        await reviews._await_optional_chapter_review_llm(asyncio.Event().wait(),
            label='unpaid_diagnostic', chapter_number=9, timeout_seconds=.001)
