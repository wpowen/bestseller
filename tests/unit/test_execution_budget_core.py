from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db.models import ExecutionBudgetModel, ExecutionOperationModel
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger
from bestseller.settings import BudgetSettings

pytestmark = pytest.mark.unit


def test_execution_limits_are_registered_and_finite():
    from bestseller.settings import load_settings
    settings = load_settings(env={
        "BESTSELLER__BUDGET__EXECUTION_MAX_CALLS": "3",
        "BESTSELLER__BUDGET__EXECUTION_MAX_TOKENS": "12345",
        "BESTSELLER__BUDGET__EXECUTION_DEADLINE_SECONDS": "90",
    })
    assert settings.budget.execution_max_calls == 3
    assert settings.budget.execution_max_tokens == 12345
    assert settings.budget.execution_deadline_seconds == 90
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        BudgetSettings(execution_max_calls=0)


@pytest.fixture
async def ledger_db(tmp_path):
    path = tmp_path / "execution_budget_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    from bestseller.infra.db.models import ExecutionAttemptModel

    async with engine.begin() as connection:
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
            await connection.run_sync(model.__table__.create)
    yield engine
    await engine.dispose()


def ledger(engine, scope="project:synthetic", **limits):
    return ExecutionLedger(
        engine,
        scope,
        BudgetSettings(
            **{
                "execution_max_calls": 2,
                "execution_max_tokens": 1000,
                "execution_deadline_seconds": 60,
                **limits,
            }
        ),
    )


@pytest.mark.asyncio
async def test_reservations_survive_new_run_and_outer_rollback(ledger_db):
    first = ledger(ledger_db)
    op = await first.claim("one", "input")
    attempt = await first.reserve(op, "model", "payload", 700)
    await first.settle(attempt, "rejected", actual_tokens=0)
    await first.finish(op, {"content": "saved"})
    second = ledger(ledger_db)  # another process/run; same stable scope
    op2 = await second.claim("two", "input2")
    attempt2 = await second.reserve(op2, "model", "payload", 700)
    await second.settle(attempt2, "completed", actual_tokens=20)
    await second.finish(op2, {"content": "saved2"})
    op3 = await second.claim("three", "input3")
    with pytest.raises(ExecutionBudgetError, match="execution_budget_exhausted"):
        await second.reserve(op3, "model", "payload", 1)
    async with async_sessionmaker(ledger_db)() as session:
        row = await session.get(ExecutionBudgetModel, first.scope_id)
        assert row.calls_used == 2 and row.tokens_charged == 20


@pytest.mark.asyncio
async def test_completed_operation_reused_without_new_send(ledger_db):
    first = ledger(ledger_db)
    op = await first.claim("same", "hash")
    attempt = await first.reserve(op, "model", "hash", 40)
    result = {"content": "result", "input_tokens": 5, "output_tokens": 8}
    await first.settle(attempt, "completed", actual_tokens=13)
    await first.finish(op, result)
    restored = await ledger(ledger_db).claim("same", "hash")
    assert restored.cached_result == result
    async with async_sessionmaker(ledger_db)() as session:
        assert (await session.get(ExecutionBudgetModel, first.scope_id)).calls_used == 1


@pytest.mark.asyncio
async def test_unknown_outcome_never_free_retry_or_fallback(ledger_db):
    first = ledger(ledger_db)
    op = await first.claim("same", "hash")
    attempt = await first.reserve(op, "model", "hash", 900)
    await first.settle(attempt, "unknown")
    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
        await ledger(ledger_db).claim("same", "hash")
    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
        await ledger(ledger_db).claim("different", "different")
    async with async_sessionmaker(ledger_db)() as session:
        assert (await session.get(ExecutionBudgetModel, first.scope_id)).tokens_charged == 900


@pytest.mark.asyncio
async def test_concurrent_claim_and_budget_are_atomic(ledger_db):
    left, right = ledger(ledger_db, execution_max_calls=1), ledger(ledger_db, execution_max_calls=1)
    # A single operation lease excludes a second owner.
    op = await left.claim("same", "hash")
    with pytest.raises(ExecutionBudgetError, match="execution_operation_inflight"):
        await right.claim("same", "hash")
    other = await right.claim("other", "otherhash")
    outcomes = await asyncio.gather(
        left.reserve(op, "m", "h", 100),
        right.reserve(other, "m", "h", 100),
        return_exceptions=True,
    )
    assert sum(not isinstance(x, Exception) for x in outcomes) == 1
    assert any(isinstance(x, ExecutionBudgetError) for x in outcomes)


@pytest.mark.asyncio
async def test_deadline_and_expired_inflight_do_not_replenish(ledger_db):
    first = ledger(ledger_db)
    op = await first.claim("one", "hash")
    await first.reserve(op, "m", "h", 500)
    async with async_sessionmaker(ledger_db)() as session, session.begin():
        row = await session.get(ExecutionOperationModel, op.key)
        row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
        await ledger(ledger_db).claim("one", "hash")
    fresh = ledger(ledger_db, scope="project:deadline")
    await fresh.claim("op", "hash")
    async with async_sessionmaker(ledger_db)() as session, session.begin():
        row = await session.get(ExecutionBudgetModel, fresh.scope_id)
        row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ExecutionBudgetError, match="execution_deadline_exhausted"):
        await ledger(ledger_db, scope="project:deadline").claim("another", "hash")


@pytest.mark.asyncio
async def test_missing_schema_fails_closed_without_network(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'missing_test.sqlite3'}")
    try:
        with pytest.raises(ExecutionBudgetError, match="execution_ledger_unavailable"):
            await ledger(engine).claim("op", "h")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_effective_override_input_hash_changes():
    from bestseller.services.llm import LLMCompletionRequest, _effective_input_hash
    from bestseller.settings import LLMRoleSettings

    base = LLMCompletionRequest(
        logical_role="writer",
        system_prompt="placeholder",
        user_prompt="placeholder",
        fallback_response="unused",
        messages_override=[{"role": "user", "content": "first"}],
    )
    other = base.model_copy(update={"messages_override": [{"role": "user", "content": "second"}]})
    role = LLMRoleSettings(
        model="openai/synthetic", temperature=0.3, max_tokens=128, timeout_seconds=2
    )
    assert _effective_input_hash(base, role) != _effective_input_hash(other, role)


class GatewaySession:
    """Workflow facade; paid ledger uses a real separate SQLite transaction."""

    def __init__(self, engine):
        self.bind, self.added = engine, []

    async def commit(self):
        pass

    async def flush(self):
        pass

    async def rollback(self):
        self.added.clear()

    def add(self, value):
        self.added.append(value)

    def in_nested_transaction(self):
        return False


def gateway_settings(**budget):
    from bestseller.settings import load_settings

    base = load_settings(env={})
    role = base.llm.planner.model_copy(
        update={"model": "openai/synthetic", "max_tokens": 128, "rate_limit_fallback_model": None}
    )
    retry = base.llm.retry.model_copy(
        update={
            "rate_limit_max_attempts": 10,
            "rate_limit_wait_min_seconds": 0.0,
            "rate_limit_wait_max_seconds": 0.0,
        }
    )
    return base.model_copy(
        update={
            "llm": base.llm.model_copy(
                update={
                    "mock": False,
                    "planner": role,
                    "retry": retry,
                }
            ),
            "budget": BudgetSettings(
                execution_max_calls=budget.get("calls", 2),
                execution_max_tokens=100_000,
                execution_deadline_seconds=60,
            ),
        }
    )


def gateway_request(**overrides):
    from bestseller.services.llm import LLMCompletionRequest

    return LLMCompletionRequest(
        logical_role="planner",
        system_prompt="system",
        user_prompt="user",
        fallback_response="must not become novel content",
        prompt_template="execution_budget_test",
        **overrides,
    )


class Provider429(Exception):
    status_code = 429


@pytest.mark.asyncio
async def test_production_gateway_charges_every_retry_and_never_static_fallback(
    ledger_db, monkeypatch
):
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope

    sends = []

    async def send(request, role):
        sends.append(role.model)
        raise Provider429("synthetic rejection")

    monkeypatch.setattr(llm, "_call_litellm", send)
    with bind_execution_scope("synthetic:gateway"):
        with pytest.raises(ExecutionBudgetError, match="execution_budget_exhausted"):
            await llm.complete_text(
                GatewaySession(ledger_db), gateway_settings(calls=2), gateway_request()
            )
    assert sends == ["openai/synthetic", "openai/synthetic"]
    # New run, new request object and new scope binding cannot refill persisted quota.
    with bind_execution_scope("synthetic:gateway"):
        with pytest.raises(ExecutionBudgetError):
            await llm.complete_text(
                GatewaySession(ledger_db), gateway_settings(calls=200), gateway_request()
            )
    assert len(sends) == 2


@pytest.mark.asyncio
async def test_production_timeout_stops_without_retry_or_model_fallback(ledger_db, monkeypatch):
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope

    sends = []

    async def send(request, role):
        # The provider was reached: a response timeout has uncertain billing.
        transport_sends = llm._transport_send_count.get()
        assert transport_sends is not None
        transport_sends[0] += 1
        sends.append(1)
        raise TimeoutError("synthetic outcome unknown")

    monkeypatch.setattr(llm, "_call_litellm", send)
    with bind_execution_scope("synthetic:timeout"):
        with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
            await llm.complete_text(
                GatewaySession(ledger_db), gateway_settings(), gateway_request()
            )
    assert len(sends) == 1

    async with async_sessionmaker(ledger_db)() as session:
        row = await session.get(ExecutionBudgetModel, "synthetic:timeout")
        assert row.status == "unknown"
        assert row.calls_used == 1 and row.tokens_charged > 0


@pytest.mark.asyncio
async def test_production_saved_result_reused_after_workflow_rollback(ledger_db, monkeypatch):
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope

    sends = []

    async def send(request, role):
        sends.append(1)
        return "durable result", 10, 5, "stop", None, None

    monkeypatch.setattr(llm, "_call_litellm", send)
    session = GatewaySession(ledger_db)
    with bind_execution_scope("synthetic:recovery"):
        original = await llm.complete_text(session, gateway_settings(), gateway_request(operation_id="stable-result"))
    await session.rollback()
    with bind_execution_scope("synthetic:recovery"):
        restored = await llm.complete_text(
            GatewaySession(ledger_db), gateway_settings(), gateway_request(operation_id="stable-result")
        )
    assert original.content == restored.content == "durable result"
    assert len(sends) == 1 and restored.metadata["reused_operation_key"]


@pytest.mark.asyncio
async def test_production_fallback_is_a_separate_reserved_send(ledger_db, monkeypatch):
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope

    sends = []

    async def send(request, role):
        sends.append(role.model)
        if role.model == "openai/synthetic":
            raise Provider429("synthetic rejection")
        return "fallback provider result", 3, 4, "stop", None, None

    monkeypatch.setattr(llm, "_call_litellm", send)
    settings = gateway_settings()
    settings.llm.planner.rate_limit_fallback_model = "openai/synthetic-fallback"
    llm._rate_limit_fallback_until.clear()
    with bind_execution_scope("synthetic:fallback"):
        result = await llm.complete_text(GatewaySession(ledger_db), settings, gateway_request())
    assert sends == ["openai/synthetic", "openai/synthetic-fallback"]
    assert len(result.metadata["execution_receipt"]["attempts"]) == 2
    async with async_sessionmaker(ledger_db)() as session:
        budget = await session.get(ExecutionBudgetModel, "synthetic:fallback")
        assert budget.calls_used == 2 and budget.tokens_charged == 7
    llm._rate_limit_fallback_until.clear()


@pytest.mark.asyncio
async def test_production_backoff_observes_cancel_before_second_send(ledger_db, monkeypatch):
    from sqlalchemy import select

    from bestseller.infra.db.models import ExecutionAttemptModel
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope
    from bestseller.services.progress_context import TaskCancelledError, set_cancellation_checker

    cancelled, sent = asyncio.Event(), asyncio.Event()
    sends = []

    async def send(request, role):
        # Simulate the guarded physical send, then a definite provider 429.
        transport_sends = llm._transport_send_count.get()
        assert transport_sends is not None
        transport_sends[0] += 1
        sends.append(1)
        sent.set()
        raise Provider429("synthetic rejection")

    async def check():
        if cancelled.is_set():
            raise TaskCancelledError()

    async def stop():
        await sent.wait()
        await asyncio.sleep(0.05)
        cancelled.set()

    monkeypatch.setattr(llm, "_call_litellm", send)
    settings = gateway_settings()
    settings.llm.retry.rate_limit_wait_min_seconds = 5
    settings.llm.retry.rate_limit_wait_max_seconds = 5
    stop_task = asyncio.create_task(stop())
    set_cancellation_checker(check)
    try:
        with bind_execution_scope("synthetic:cancel"):
            with pytest.raises(TaskCancelledError):
                await llm.complete_text(GatewaySession(ledger_db), settings, gateway_request())
    finally:
        set_cancellation_checker(None)
    await stop_task
    assert len(sends) == 1
    async with async_sessionmaker(ledger_db)() as session:
        row = await session.get(ExecutionBudgetModel, "synthetic:cancel")
        assert row.status == "active"
        assert (row.calls_used, row.tokens_charged) == (1, 0)
        limits = (row.max_calls, row.max_tokens, row.deadline_at)
        attempts = (await session.scalars(select(ExecutionAttemptModel).where(
            ExecutionAttemptModel.scope_id == "synthetic:cancel",
        ))).all()
        assert len(attempts) == 1 and attempts[0].status == "rejected"

    # An explicit operator resume may run a stable new operation on the same
    # ledger. Neither the known 429 nor the pause restores consumed call credit.
    cancelled.clear()

    async def resumed_send(request, role):
        transport_sends = llm._transport_send_count.get()
        assert transport_sends is not None
        transport_sends[0] += 1
        sends.append(2)
        return "resumed result", 3, 4, "stop", None, None

    monkeypatch.setattr(llm, "_call_litellm", resumed_send)
    resumed_request = gateway_request(operation_id="explicit-resume-after-backoff-pause")
    with bind_execution_scope("synthetic:cancel"):
        resumed = await llm.complete_text(GatewaySession(ledger_db), settings, resumed_request)
    with bind_execution_scope("synthetic:cancel"):
        replayed = await llm.complete_text(GatewaySession(ledger_db), settings, resumed_request)
    assert resumed.content == replayed.content == "resumed result"
    assert sends == [1, 2]
    async with async_sessionmaker(ledger_db)() as session:
        row = await session.get(ExecutionBudgetModel, "synthetic:cancel")
        assert row.status == "active"
        assert (row.calls_used, row.tokens_charged) == (2, 7)
        assert (row.max_calls, row.max_tokens, row.deadline_at) == limits


@pytest.mark.asyncio
async def test_gateway_cannot_send_without_ledger_even_with_valid_workflow(monkeypatch):
    from uuid import uuid4

    from bestseller.services import llm

    async def forbidden(*args, **kwargs):
        pytest.fail("unreserved provider send")

    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    with pytest.raises(ExecutionBudgetError, match="execution_ledger_unavailable"):
        await llm.complete_text(
            GatewaySession(None), gateway_settings(), gateway_request(workflow_run_id=uuid4())
        )


@pytest.mark.asyncio
async def test_real_sqlite_workflow_transaction_does_not_lock_result_commit(ledger_db, monkeypatch):
    from sqlalchemy import JSON, ForeignKeyConstraint, MetaData
    from sqlalchemy.dialects.postgresql import JSONB

    from bestseller.infra.db.models import LlmRunModel
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope

    # Only the existing telemetry JSON type needs a portable test projection.
    table = LlmRunModel.__table__.to_metadata(MetaData())
    for column in table.columns:
        if isinstance(column.type, JSONB):
            column.type = JSON()
    for constraint in list(table.constraints):
        if isinstance(constraint, ForeignKeyConstraint):
            table.constraints.remove(constraint)
    async with ledger_db.begin() as connection:
        await connection.run_sync(table.create)
    sends = []

    async def send(request, role):
        sends.append(1)
        return "real session response", 10, 5, "stop", None, None

    monkeypatch.setattr(llm, "_call_litellm", send)
    async with async_sessionmaker(ledger_db, expire_on_commit=False)() as session:
        with bind_execution_scope("synthetic:real-session"):
            result = await asyncio.wait_for(
                llm.complete_text(session, gateway_settings(), gateway_request(operation_id="sqlite-result")), 3
            )
        assert result.content == "real session response"
        await session.rollback()
    async with async_sessionmaker(ledger_db, expire_on_commit=False)() as session:
        with bind_execution_scope("synthetic:real-session"):
            restored = await llm.complete_text(session, gateway_settings(), gateway_request(operation_id="sqlite-result"))
        assert restored.llm_run_id is not None
        assert await session.get(LlmRunModel, restored.llm_run_id) is not None
        await session.commit()
    async with async_sessionmaker(ledger_db, expire_on_commit=False)() as session:
        with bind_execution_scope("synthetic:real-session"):
            again = await llm.complete_text(session, gateway_settings(), gateway_request(operation_id="sqlite-result"))
        assert again.llm_run_id == restored.llm_run_id
        from sqlalchemy import select, func
        assert await session.scalar(select(func.count()).select_from(LlmRunModel)) == 1
    assert restored.content == result.content and len(sends) == 1


@pytest.mark.asyncio
async def test_migration_matches_models_and_round_trips_sqlite(tmp_path):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    path = Path("migrations/versions/0043_execution_budget_ledger.py")
    spec = importlib.util.spec_from_file_location("budget_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'migration_test.sqlite3'}")
    try:
        async with engine.begin() as connection:

            def check(sync):
                ctx = MigrationContext.configure(sync)
                with Operations.context(ctx):
                    migration.upgrade()
                    from bestseller.infra.db.models import ExecutionAttemptModel

                    for model in (
                        ExecutionBudgetModel,
                        ExecutionOperationModel,
                        ExecutionAttemptModel,
                    ):
                        actual = {x["name"] for x in inspect(sync).get_columns(model.__tablename__)}
                        assert actual == {x.name for x in model.__table__.columns}
                    migration.downgrade()
                    assert not set(inspect(sync).get_table_names()) & {
                        "execution_budgets",
                        "execution_operations",
                        "execution_attempts",
                    }

            await connection.run_sync(check)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_timeout_mentioning_quota_stays_unknown(ledger_db, monkeypatch):
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope

    sends = []

    async def send(*args):
        # The provider was reached: a response timeout has uncertain billing.
        transport_sends = llm._transport_send_count.get()
        assert transport_sends is not None
        transport_sends[0] += 1
        sends.append(1)
        raise TimeoutError("quota exceeded while waiting for response")

    monkeypatch.setattr(llm, "_call_litellm", send)
    with bind_execution_scope("synthetic:quota-text"):
        with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
            await llm.complete_text(
                GatewaySession(ledger_db), gateway_settings(), gateway_request()
            )
    assert len(sends) == 1
    async with async_sessionmaker(ledger_db)() as session:
        row = await session.get(ExecutionBudgetModel, "synthetic:quota-text")
        assert row.status == "unknown" and row.tokens_charged > 0


@pytest.mark.asyncio
async def test_independent_samples_differ_but_irrelevant_metadata_does_not_break_recovery(
    ledger_db, monkeypatch
):
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope

    sends = []

    async def send(*args):
        sends.append(1)
        return f"sample {len(sends)}", 3, 4, "stop", None, None

    monkeypatch.setattr(llm, "_call_litellm", send)
    with bind_execution_scope("synthetic:samples"):
        one = await llm.complete_text(
            GatewaySession(ledger_db),
            gateway_settings(),
            gateway_request(operation_id="sample-one", metadata={"trace": "old"}),
        )
        two = await llm.complete_text(
            GatewaySession(ledger_db),
            gateway_settings(),
            gateway_request(operation_id="sample-two", metadata={"trace": "old"}),
        )
    with bind_execution_scope("synthetic:samples"):
        restored = await llm.complete_text(
            GatewaySession(ledger_db),
            gateway_settings(),
            gateway_request(operation_id="sample-one", metadata={"trace": "new"}),
        )
    assert one.content != two.content and restored.content == one.content and len(sends) == 2


@pytest.mark.asyncio
async def test_failed_settlement_freezes_scope_even_after_operation_changes(ledger_db, monkeypatch):
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope

    sends = []

    async def send(*args):
        sends.append(1)
        return "unsettled result", 3, 4, "stop", None, None

    async def fail_settle(*args, **kwargs):
        raise ExecutionBudgetError("execution_ledger_unavailable")

    monkeypatch.setattr(llm, "_call_litellm", send)
    monkeypatch.setattr(ExecutionLedger, "settle", fail_settle)
    with bind_execution_scope("synthetic:settlement"):
        with pytest.raises(ExecutionBudgetError, match="execution_ledger_unavailable"):
            await llm.complete_text(
                GatewaySession(ledger_db), gateway_settings(), gateway_request()
            )
    with bind_execution_scope("synthetic:settlement"):
        with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
            await llm.complete_text(
                GatewaySession(ledger_db),
                gateway_settings(),
                gateway_request(operation_id="different"),
            )
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_expired_budget_can_read_already_paid_result(ledger_db):
    first = ledger(ledger_db)
    op = await first.claim("paid", "h")
    attempt = await first.reserve(op, "m", "h", 100)
    await first.settle(attempt, "completed", actual_tokens=5)
    await first.finish(op, {"content": "paid"})
    async with async_sessionmaker(ledger_db)() as session, session.begin():
        row = await session.get(ExecutionBudgetModel, first.scope_id)
        row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    cached = await ledger(ledger_db).claim("paid", "h")
    assert cached.cached_result == {"content": "paid"}
    with pytest.raises(ExecutionBudgetError, match="execution_deadline_exhausted"):
        await ledger(ledger_db).claim("new", "h")


@pytest.mark.asyncio
async def test_hidden_sdk_retry_blocked_before_second_http_send(ledger_db, monkeypatch):
    import httpx

    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope

    wire_sends = []

    async def sdk(request, role):
        wire = httpx.Request(
            "POST", "https://synthetic.invalid/v1/chat/completions", json={"messages": []}
        )
        await llm._guard_provider_http_send(wire)
        wire_sends.append(1)
        await llm._guard_provider_http_send(wire)  # opaque SDK retry, forbidden
        wire_sends.append(1)

    monkeypatch.setattr(llm, "_call_litellm", sdk)
    with bind_execution_scope("synthetic:hidden-sdk"):
        with pytest.raises(ExecutionBudgetError):
            await llm.complete_text(
                GatewaySession(ledger_db), gateway_settings(), gateway_request()
            )
    assert len(wire_sends) == 1


@pytest.mark.asyncio
async def test_nested_workflow_transaction_fails_before_any_send(ledger_db, monkeypatch):
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope

    async def forbidden(*args):
        pytest.fail("must not send from unreleasable transaction")

    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    async with async_sessionmaker(ledger_db)() as session, session.begin():
        await session.execute(
            ExecutionBudgetModel.__table__.insert().values(
                scope_id="outer",
                max_calls=1,
                max_tokens=100,
                calls_used=0,
                tokens_charged=0,
                deadline_at=datetime.now(timezone.utc),
                status="active",
                metadata_json={},
            )
        )
        async with session.begin_nested():
            with bind_execution_scope("synthetic:nested"):
                with pytest.raises(
                    ExecutionBudgetError, match="execution_transaction_not_releasable"
                ):
                    await llm.complete_text(session, gateway_settings(), gateway_request())


@pytest.mark.asyncio
async def test_actual_send_builder_disables_hidden_retries_and_hashes_override(
    ledger_db, monkeypatch
):
    import hashlib
    import json
    from types import SimpleNamespace
    import httpx
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope

    received = []

    async def handle(request):
        received.append(request.content)
        return httpx.Response(200, json={"ok": True})

    async def sdk(**kwargs):
        assert kwargs["num_retries"] == kwargs["max_retries"] == 0
        assert kwargs["drop_params"] is False
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            event_hooks={"request": [llm._guard_provider_http_send]},
        ) as client:
            await client.post(
                "https://synthetic.invalid/completions",
                json={
                    "model": kwargs["model"],
                    "messages": kwargs["messages"],
                },
            )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="sdk response", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    monkeypatch.setattr(llm, "_get_litellm", lambda: SimpleNamespace(acompletion=sdk))
    monkeypatch.setattr(llm, "_ensure_shared_litellm_http_client", lambda: None)
    with bind_execution_scope("synthetic:wire-builder"):
        first = await llm.complete_text(
            GatewaySession(ledger_db),
            gateway_settings(),
            gateway_request(messages_override=[{"role": "user", "content": "first actual body"}]),
        )
        second = await llm.complete_text(
            GatewaySession(ledger_db),
            gateway_settings(),
            gateway_request(messages_override=[{"role": "user", "content": "second actual body"}]),
        )
    assert (
        len(received) == 2
        and json.loads(received[0])["messages"][0]["content"] == "first actual body"
    )
    assert (
        first.metadata["effective_input_receipt"]["hash"]
        != second.metadata["effective_input_receipt"]["hash"]
    )
    assert (
        first.metadata["execution_receipt"]["attempts"][0]["wire_body_hash"]
        == hashlib.sha256(received[0]).hexdigest()
    )


@pytest.mark.asyncio
async def test_historical_spend_adopted_once_and_missing_usage_not_free(ledger_db):
    from uuid import uuid4
    from sqlalchemy import Table, Column, Uuid, String, Integer, MetaData, JSON
    history = Table("llm_runs", MetaData(),
        Column("id", Uuid, primary_key=True), Column("project_id", Uuid),
        Column("workflow_run_id", Uuid), Column("metadata", JSON),
        Column("provider", String), Column("input_tokens", Integer),
        Column("output_tokens", Integer))
    paid, missing = uuid4(), uuid4()
    async with ledger_db.begin() as connection:
        await connection.run_sync(history.create)
        await connection.execute(history.insert(), [
            {"id": uuid4(), "project_id": paid, "provider": "openai", "input_tokens": 100, "output_tokens": 30},
            {"id": uuid4(), "project_id": missing, "provider": "openai", "input_tokens": None, "output_tokens": None},
        ])
    first = ledger(ledger_db, f"project:{paid}")
    claim = await first.claim("op", "h")
    attempt = await first.reserve(claim, "m", "h", 100)
    await first.settle(attempt, "completed", actual_tokens=10)
    await first.finish(claim, {"content": "saved"})
    await ledger(ledger_db, f"project:{paid}").claim("op", "h")
    async with async_sessionmaker(ledger_db)() as session:
        row = await session.get(ExecutionBudgetModel, f"project:{paid}")
        assert row.calls_used == 2 and row.tokens_charged == 140
    unknown_usage = ledger(ledger_db, f"project:{missing}")
    claim = await unknown_usage.claim("op", "h")
    with pytest.raises(ExecutionBudgetError, match="execution_budget_exhausted"):
        await unknown_usage.reserve(claim, "m", "h", 1)


@pytest.mark.asyncio
async def test_hard_exit_reserved_send_blocks_new_owner_with_different_operation(ledger_db):
    from bestseller.services.budget import bind_execution_scope
    with bind_execution_scope("synthetic:hard-exit"):
        first = ledger(ledger_db, "synthetic:hard-exit")
        claim = await first.claim("old-op", "h")
        await first.reserve(claim, "m", "h", 900)
        # Simulate hard exit: no settle/finally/finish.
    # A new epoch or context exit alone does not establish owner death.
    async with async_sessionmaker(ledger_db)() as session, session.begin():
        row = await session.get(ExecutionOperationModel, claim.key)
        row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    with bind_execution_scope("synthetic:hard-exit"):
        with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
            await ledger(ledger_db, "synthetic:hard-exit").claim("new-op", "different")


@pytest.mark.asyncio
async def test_implicit_sample_cannot_replay_wrong_checkpoint_position(ledger_db, monkeypatch):
    from bestseller.services import llm
    from bestseller.services.budget import bind_execution_scope
    sends = []
    async def send(*args):
        sends.append(1)
        return "result", 1, 1, "stop", None, None
    monkeypatch.setattr(llm, "_call_litellm", send)
    with bind_execution_scope("synthetic:implicit"):
        await llm.complete_text(GatewaySession(ledger_db), gateway_settings(), gateway_request())
    with bind_execution_scope("synthetic:implicit"):
        with pytest.raises(ExecutionBudgetError, match="execution_operation_identity_required"):
            await llm.complete_text(GatewaySession(ledger_db), gateway_settings(), gateway_request())
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_transport_install_failure_cannot_continue_or_send(monkeypatch):
    from bestseller.services import llm
    def unavailable():
        raise OSError("synthetic SDK load failure")
    monkeypatch.setattr(llm, "_litellm_client_by_loop", {})
    monkeypatch.setattr(llm, "_get_litellm", unavailable)
    with pytest.raises(ExecutionBudgetError, match="execution_transport_unavailable"):
        llm._ensure_shared_litellm_http_client()
