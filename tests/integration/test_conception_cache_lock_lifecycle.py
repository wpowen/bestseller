"""A paused history reader must not hold financial admission locks."""
import asyncio
from datetime import datetime, timedelta, timezone
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db import models as m
from bestseller.services import conception_continuation as recovery
from bestseller.services import independent_acceptance as acceptance
from bestseller.services.budget import ExecutionLedger, bind_execution_scope
from bestseller.services.llm import LLMCompletionRequest
from bestseller.settings import BudgetSettings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_paused_history_reader_does_not_block_atomic_claim(
    postgresql_test_settings, monkeypatch,
):
    engine = create_async_engine(postgresql_test_settings.database.url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    token = uuid4().hex
    parent = "conception:cache-lock-test-" + token
    scope = recovery.PREFIX + "cache-lock-test:" + token
    deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
    entered, release = asyncio.Event(), asyncio.Event()
    original = recovery._retained_sources
    read_task = None
    try:
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT current_database()")) == "bestseller_test"
            for model in (m.ExecutionBudgetModel, m.ExecutionOperationModel, m.ExecutionAttemptModel):
                await connection.run_sync(lambda c, table=model.__table__: table.create(c, checkfirst=True))
        async with factory() as s:
            ancestor = m.ExecutionBudgetModel(scope_id=parent, max_calls=1, max_tokens=100,
                calls_used=1, tokens_charged=100, deadline_at=deadline-timedelta(hours=2),
                status="unknown", metadata_json={"synthetic_test": token})
            s.add(ancestor)
            await s.flush()
            request = {"scope": scope, "old_scope": parent,
                "old_snapshot": acceptance.snapshot(ancestor), "new_calls": 10,
                "new_tokens": 100000, "model": acceptance.MODEL, "window_seconds": 7200,
                "authorization_ref": "isolated synthetic test only",
                "counter_sha256": acceptance.CONTRACT_SHA256, "cached_results": [],
                "blocked_input_hash": "never-send", "endpoint": "https://offline.invalid/v1"}
            s.add(m.ExecutionBudgetModel(scope_id=scope, max_calls=10, max_tokens=100000,
                calls_used=0, tokens_charged=0, status="active", deadline_at=deadline,
                metadata_json={acceptance.KEY: {"schema": acceptance.SCHEMA,
                    "request": request, "request_sha256": acceptance.digest(request),
                    "submitted_at": datetime.now(timezone.utc).isoformat()}}))
            await s.commit()

        async def paused_scan(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(recovery, "_retained_sources", paused_scan)

        async def read_cache():
            with bind_execution_scope(scope):
                async with factory() as session:
                    return await recovery.adopt_cached(session, LLMCompletionRequest(
                        logical_role="critic", prompt_template="unrelated-current-input",
                        system_prompt="Synthetic source", user_prompt="Synthetic request",
                        fallback_response="No provider permitted"), "synthetic-hash")

        read_task = asyncio.create_task(read_cache())
        await asyncio.wait_for(entered.wait(), 2)
        started = monotonic()
        # Real atomic admission, never reserve or send. The old reader makes
        # this hit the ledger's existing five-second transaction deadline.
        ledger = ExecutionLedger(engine, scope, BudgetSettings())
        claim = await ledger.claim("synthetic-no-provider-operation", "synthetic-new-input")
        assert monotonic() - started < 2
        assert claim.cached_result is None
        async with factory() as s:
            # Ancestor validation must also release locks before slow reads.
            await s.execute(select(m.ExecutionBudgetModel.scope_id).where(
                m.ExecutionBudgetModel.scope_id == parent).with_for_update(nowait=True))
            await s.rollback()
        release.set()
        assert await asyncio.wait_for(read_task, 2) is None
        async with factory() as s:
            b = await s.get(m.ExecutionBudgetModel, scope)
            assert (b.calls_used, b.tokens_charged, b.deadline_at) == (0, 0, deadline)
            assert await s.scalar(select(func.count()).select_from(m.ExecutionAttemptModel).where(
                m.ExecutionAttemptModel.scope_id == scope)) == 0
    finally:
        release.set()
        if read_task is not None:
            await asyncio.gather(read_task, return_exceptions=True)
        async with factory() as s:
            await s.execute(delete(m.ExecutionAttemptModel).where(m.ExecutionAttemptModel.scope_id == scope))
            await s.execute(delete(m.ExecutionOperationModel).where(m.ExecutionOperationModel.scope_id == scope))
            await s.execute(delete(m.ExecutionBudgetModel).where(m.ExecutionBudgetModel.scope_id.in_([scope, parent])))
            await s.commit()
        await engine.dispose()
