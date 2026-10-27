"""Operational scheduling for one owned interpreter, not evaluation policy.

The original independent slot evaluator is unchanged. Commit/rollback each
owned sample before starting the next to avoid Budget->source versus source
lock queues. Only the caller timeout is bounded differently; provider timeouts,
request identity, thresholds, slots and retry allowances stay unchanged.
"""
import asyncio
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def serial_commercial_samples(*,judge_module,review_module,caller_timeout_seconds=300):
    if not 1 <= caller_timeout_seconds <= 600:raise ValueError('bounded_caller_timeout_required')
    original=judge_module.judge_chapter_commercial_quality
    original_wait=review_module._await_optional_chapter_review_llm
    lock=asyncio.Lock();audit={'max_active':0,'started_slots':[],'finished_slots':[],'evaluator_unchanged':True}
    active=0
    async def owned(session,settings,**kwargs):
        nonlocal active
        async with lock:
            active+=1;audit['max_active']=max(audit['max_active'],active)
            slot=kwargs.get('sample_slot');audit['started_slots'].append(slot)
            try:
                value=await original(session,settings,**kwargs)
                await session.commit()
                audit['finished_slots'].append(slot)
                return value
            except BaseException:
                await session.rollback();raise
            finally:active-=1
    async def wait(awaitable,**kwargs):
        if kwargs.get('label')=='chapter_llm_commercial_judge':kwargs['timeout_seconds']=caller_timeout_seconds
        return await original_wait(awaitable,**kwargs)
    with patch.object(judge_module,'judge_chapter_commercial_quality',owned),patch.object(review_module,'_await_optional_chapter_review_llm',wait):
        yield audit
