"""Test-only real ledger + offline wire support for legacy gateway probes.

Run with the existing cached aiosqlite environment documented in the WP5
phase2 verification handoff; no production DB or model endpoint is used.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import contextvars
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from bestseller.infra.db.models import (
    ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel,
    LlmRunModel, ProjectModel, BookProductionControlModel,
)
from bestseller.services.budget import ExecutionLedger, bind_execution_scope

_engine = contextvars.ContextVar("legacy_gateway_test_engine", default=None)


class LedgerBoundSession:
    """Keep telemetry observation/failure probes, with actual ledger DB binding."""
    @property
    def bind(self):
        engine = _engine.get()
        if engine is None:
            raise AssertionError("gateway test requires isolated SQLite ledger fixture")
        return engine

    async def scalar(self, statement):
        async with AsyncSession(self.bind) as session:
            return await session.scalar(statement)

    async def get(self, model, key):
        async with AsyncSession(self.bind) as session:
            return await session.get(model, key)

    async def execute(self, statement):
        async with AsyncSession(self.bind) as session:
            return await session.execute(statement)

    def in_nested_transaction(self):
        return False

    async def commit(self):
        return None

    async def rollback(self):
        return None


@pytest.fixture
def gateway_ledger(tmp_path):
    path = tmp_path / "legacy_gateway_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)

    async def create():
        metadata = MetaData()
        for model in (ProjectModel, LlmRunModel, BookProductionControlModel,
                      ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
            table = model.__table__.to_metadata(metadata)
            for column in table.columns:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                    column.server_default = None  # PG ::jsonb is not SQLite SQL
            for constraint in list(table.constraints):
                if isinstance(constraint, ForeignKeyConstraint):
                    table.constraints.remove(constraint)
        async with engine.begin() as connection:
            for table in metadata.tables.values():
                await connection.run_sync(table.create)
            await connection.execute(metadata.tables["projects"].insert(), [
                {"id": uuid4(), "slug": slug, "title": "Synthetic ledger fixture",
                 "language": "zh-CN", "genre": "custom", "target_word_count": 12000,
                 "target_chapters": 4, "status": "active", "project_type": "linear",
                 "metadata": {}, "hype_scheme_json": {}}
                for slug in ("novel", "owned-book", "bad-outline")
            ])
    asyncio.run(create())
    token = _engine.set(engine)
    try:
        with bind_execution_scope(f"test-gateway:{tmp_path.name}"):
            yield engine
    finally:
        _engine.reset(token)
        asyncio.run(engine.dispose())


async def replay_reserved_http_send(kwargs):
    """Exercise the real physical-send guard, never fake its counters/hooks."""
    from bestseller.services import llm
    call = llm._execution_call_var.get()
    assert call is not None and call.attempts, "SDK replay requires a real reserved attempt"
    async def reply(request):
        assert request.url.host == "gateway.test.invalid"
        return httpx.Response(200, json={"offline": True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        await client.post("https://gateway.test.invalid/completions", json={
            key: kwargs[key] for key in ("model", "messages", "tools", "max_tokens") if key in kwargs
        })


@asynccontextmanager
async def reserved_transport_context(role, request):
    """Real pre-reservation for focused private transport/timeout tests.

    These tests intentionally interrupt before a result is durable. Keep that
    reservation charged/unknown instead of inventing free settlement.
    """
    from bestseller.services import llm
    from bestseller.services.budget import current_execution_scope
    from bestseller.settings import BudgetSettings
    ledger = ExecutionLedger(_engine.get(), current_execution_scope(), BudgetSettings())
    input_hash = llm._effective_input_hash(request, role)
    claim = await ledger.claim("private-transport-probe", input_hash)
    attempt = await ledger.reserve(claim, role.model, input_hash, 10000)
    call = llm._ExecutionCall(ledger, claim)
    call.attempts.append({"attempt_id": attempt, "effective_input_hash": input_hash})
    token = llm._execution_call_var.set(call)
    send_token = llm._transport_send_count.set([0])
    try:
        llm._install_physical_transport_guard()
        yield
    finally:
        await ledger.settle(attempt, "unknown")
        llm._transport_send_count.reset(send_token)
        llm._execution_call_var.reset(token)


@asynccontextmanager
async def worker_ledger_session():
    async with AsyncSession(_engine.get(), expire_on_commit=False) as session:
        yield session
        await session.commit()
