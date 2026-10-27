"""Real SQLite gateway entry rejects unreleasable transactions before payment."""
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_llm_execution_receipt_binding import receipt_db, paid_gateway  # noqa: F401
from test_execution_budget_core import gateway_settings, gateway_request

from bestseller.infra.db.models import ProjectModel, ExecutionAttemptModel, LlmRunModel
from bestseller.services import llm
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize('nested', [False, True])
async def test_managed_context_cannot_close_before_paid_return(receipt_db, paid_gateway, nested):  # noqa: F811
    factory = async_sessionmaker(receipt_db, expire_on_commit=False)
    @asynccontextmanager
    async def optional_nested(s):
        if nested:
            async with s.begin_nested():
                yield
        else:
            yield
    async with factory() as s, s.begin():
        p = ProjectModel(slug='independent-transaction-story',title='山间邮局',genre='general-fiction',
            target_chapters=4,target_word_count=8000,metadata_json={})
        s.add(p)
        await s.flush()
        with bind_execution_scope('test:managed-paid-boundary'):
            async with optional_nested(s):
                with pytest.raises(ExecutionBudgetError) as error:
                    await llm.complete_text(s,gateway_settings(),gateway_request(operation_id='managed-call'))
                assert error.value.code == 'execution_transaction_not_releasable'
                # The rejected gateway did not close or roll back caller work.
                assert await s.get(ProjectModel,p.id) is p
                p.title = '邮局重开'
                await s.flush()
    async with factory() as s:
        assert (await s.get(ProjectModel,p.id)).title == '邮局重开'
        assert await s.scalar(select(func.count()).select_from(ExecutionAttemptModel)) == 0
        assert await s.scalar(select(func.count()).select_from(LlmRunModel)) == 0
    assert paid_gateway[1] == []


async def test_failed_checkpoint_cannot_pay_then_swallow_source_failure(receipt_db, paid_gateway, monkeypatch):  # noqa: F811
    async with async_sessionmaker(receipt_db,expire_on_commit=False)() as s:
        async def broken_commit():
            raise RuntimeError('local checkpoint unavailable')
        monkeypatch.setattr(s,'commit',broken_commit)
        with bind_execution_scope('test:checkpoint-failure'):
            with pytest.raises(ExecutionBudgetError) as error:
                await llm.complete_text(s,gateway_settings(),gateway_request(operation_id='failed-source'))
        assert error.value.code == 'execution_transaction_not_releasable'
        assert isinstance(error.value.__cause__,RuntimeError)
        assert await s.scalar(select(func.count()).select_from(ExecutionAttemptModel)) == 0
    assert paid_gateway[1] == []


async def test_autobegin_can_checkpoint_continue_and_reuse_paid_result(receipt_db, paid_gateway):  # noqa: F811
    factory = async_sessionmaker(receipt_db,expire_on_commit=False)
    with bind_execution_scope('test:autobegin-paid-boundary'):
        async with factory() as s:
            assert await s.scalar(select(1)) == 1
            a = await llm.complete_text(s,gateway_settings(),gateway_request(operation_id='one-result'))
            assert await s.scalar(select(2)) == 2
            await s.commit()
        async with factory() as s:
            b = await llm.complete_text(s,gateway_settings(),gateway_request(operation_id='one-result'))
            await s.commit()
            assert await s.scalar(select(func.count()).select_from(ExecutionAttemptModel)) == 1
    assert a.content == b.content == 'retained paid result'
    assert len(paid_gateway[1]) == 1
