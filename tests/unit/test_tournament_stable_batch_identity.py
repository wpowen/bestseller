"""Independent tournament draws retain stable paid identities and execution errors."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.services import concept_tournament as ct, llm
from bestseller.services.budget import (
    ExecutionBudgetError,
    ExecutionOperationBusyError,
    bind_execution_scope,
)
from bestseller.services.prompt_source_policy import AdoptedSourceUnavailable

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(type_, compiler, **kw):
    return "JSON"


async def test_factory_inherits_stable_indices_without_changing_callback_or_prompt(monkeypatch):
    requests = []

    async def complete(session, settings, request):
        requests.append(request)
        await asyncio.sleep(0)
        return SimpleNamespace(content="{}", llm_run_id=None)

    monkeypatch.setattr(llm, "complete_text", complete)
    factory = await ct._default_generator(None, None, template="unrelated-observatory")

    async def operation(index, item):
        assert item == "same input"
        return await factory("original system", "original user")

    await ct._run_tournament_batches(["same input"] * 4, operation, concurrency=2, stage="pool")
    assert sorted(r.sample_slot for r in requests) == [0, 1, 2, 3]
    assert len({(r.system_prompt, r.user_prompt, r.prompt_version) for r in requests}) == 1
    await factory("original system", "original user")
    assert requests[-1].sample_slot is None  # no context leaks into later calls


async def test_nested_and_failed_batches_restore_parent_slot(monkeypatch):
    slots = []

    async def complete(session, settings, request):
        slots.append(request.sample_slot)
        return SimpleNamespace(content="{}", llm_run_id=None)

    monkeypatch.setattr(llm, "complete_text", complete)
    factory = await ct._default_generator(None, None, template="nested-observatory")

    async def child(index, item):
        await factory("s", "u")
        raise ValueError("parse failure")

    async def parent(index, item):
        if index == 1:
            with pytest.raises(ValueError, match="parse failure"):
                await ct._run_tournament_batches([0], child, concurrency=1, stage="inner")
            await factory("s", "u")

    await ct._run_tournament_batches([0, 1], parent, concurrency=1, stage="outer")
    await factory("s", "u")
    assert slots == [0, 1, None]


@pytest.mark.parametrize("error", [
    ExecutionOperationBusyError("execution_operation_inflight"),
    ExecutionBudgetError("execution_result_unknown"),
    AdoptedSourceUnavailable("missing adopted source"),
    SQLAlchemyError("database unavailable"),
    OSError("receipt unavailable"),
])
async def test_execution_failure_waits_for_sibling_result_without_cancelling(error):
    started, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancelled = []

    async def operation(index, item):
        if index == 0:
            await started.wait()
            raise error
        started.set()
        try:
            await release.wait()
            settled.set()
            return "retained paid result"
        except asyncio.CancelledError:
            cancelled.append(index)
            raise

    task = asyncio.create_task(ct._run_tournament_batches(
        [0, 1], operation, concurrency=2, stage="pool"
    ))
    await started.wait()
    # Yield to the failing lane and its gather handler, then let its peer finish.
    for _ in range(4):
        await asyncio.sleep(0)
    release.set()
    with pytest.raises(type(error)) as caught:
        await task
    assert caught.value is error
    assert settled.is_set() and cancelled == []


async def test_execution_failure_does_not_start_queued_lanes():
    calls = []

    async def operation(index, item):
        calls.append(index)
        raise ExecutionBudgetError("execution_paused")

    with pytest.raises(ExecutionBudgetError, match="execution_paused"):
        await ct._run_tournament_batches([0, 1, 2], operation, concurrency=1, stage="pool")
    assert calls == [0]


async def test_recorded_execution_error_takes_priority_over_concurrent_parse_error():
    release, settled = asyncio.Event(), asyncio.Event()
    all_started = asyncio.Event()
    started = 0
    execution_error = ExecutionBudgetError("execution_result_unknown")

    async def operation(index, item):
        nonlocal started
        started += 1
        if started == 3:
            all_started.set()
        await all_started.wait()
        if index == 0:
            raise ValueError("ordinary parse error arrived first")
        if index == 1:
            raise execution_error
        await release.wait()
        settled.set()
        return "paid peer result"

    task = asyncio.create_task(ct._run_tournament_batches(
        [0, 1, 2], operation, concurrency=3, stage="pool"
    ))
    for _ in range(5):
        await asyncio.sleep(0)
    release.set()
    with pytest.raises(ExecutionBudgetError) as caught:
        await task
    assert caught.value is execution_error
    assert settled.is_set()


@pytest.mark.parametrize("error", [
    ExecutionOperationBusyError("execution_operation_inflight"),
    ExecutionBudgetError("execution_source_changed"),
    AdoptedSourceUnavailable("missing source"),
    SQLAlchemyError("db unavailable"),
    OSError("receipt unavailable"),
])
async def test_real_tournament_entry_does_not_convert_execution_failure_to_no_winner(monkeypatch, error):
    calls = []

    async def factory(*args, **kwargs):
        async def generate(system, user):
            calls.append(kwargs["template"])
            raise error
        return generate

    monkeypatch.setattr(ct, "_default_generator", factory)
    with pytest.raises(type(error)) as caught:
        await ct.run_concept_tournament(
            None, SimpleNamespace(pipeline=SimpleNamespace(concept_tournament_batch_concurrency=1)),
            genre="天文探险", sub_genre="轨道观测", chapter_count=50,
            config={"candidate_prompt_mode": "engine_first", "n_candidates": 2,
                    "raw_idea_pool_multiplier": 1, "raw_idea_generation_batch_size": 2},
        )
    assert caught.value is error
    assert calls == ["concept_tournament_raw_idea_pool"]


class _GatewaySession:
    """ORM facade; operation/attempt accounting uses actual isolated SQLite."""

    def __init__(self, engine):
        self.bind = engine

    async def commit(self):
        pass

    async def flush(self):
        pass

    def add(self, value):
        pass

    def in_nested_transaction(self):
        return False


async def test_real_gateway_reuses_legacy_zero_and_distinguishes_parallel_draws(tmp_path, monkeypatch):
    from bestseller.infra.db.models import (
        ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, LlmRunModel,
    )
    from bestseller.settings import BudgetSettings, load_settings

    path = tmp_path / "tournament_identity_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel, LlmRunModel):
            await conn.run_sync(model.__table__.create)
    settings = load_settings(env={})
    settings.llm.mock = False
    settings.llm.planner.model = "openai/synthetic"
    settings.llm.planner.rate_limit_fallback_model = None
    settings.budget = BudgetSettings(execution_max_calls=10, execution_max_tokens=1_000_000,
                                     execution_deadline_seconds=60)
    sends = []

    async def send(request, role):
        sends.append((request.sample_slot, request.system_prompt, request.user_prompt))
        await asyncio.sleep(0)
        return "paid response", 10, 5, "stop", None, None

    monkeypatch.setattr(llm, "_call_litellm", send)
    scope = "conception:synthetic-existing-task"
    factory = await ct._default_generator(_GatewaySession(engine), settings, template="pool_test")

    async def operation(index, item):
        return await factory("unchanged system", "unchanged user")

    try:
        with bind_execution_scope(scope), llm.bind_execution_operation("unchanged-initial-namespace"):
            # The pre-fix request omitted slot; bound LLM identity supplied zero.
            original = await factory("unchanged system", "unchanged user")
            values = await ct._run_tournament_batches([12] * 4, operation, concurrency=2, stage="pool")
        assert values[0][0] == original[0] == "paid response"
        assert sorted(s[0] for s in sends) == [0, 1, 2, 3]
        async with async_sessionmaker(engine)() as session:
            from sqlalchemy import select
            operations = (await session.scalars(select(ExecutionOperationModel).where(
                ExecutionOperationModel.status == "completed"
            ))).all()
            assert len(operations) == 4
            assert len({op.input_hash for op in operations}) == 1
            assert all(op.status == "completed" for op in operations)
            budget = await session.get(ExecutionBudgetModel, scope)
            assert budget.calls_used == 4 and budget.tokens_charged == 60
        # A resumed identical cohort reuses all four paid results, with no new sends.
        with bind_execution_scope(scope), llm.bind_execution_operation("unchanged-initial-namespace"):
            restored = await ct._run_tournament_batches([12] * 4, operation, concurrency=2, stage="pool")
        assert [value[0] for value in restored] == [value[0] for value in values]
        assert len(sends) == 4
    finally:
        await engine.dispose()
