"""SQLite admission at the real Web/architect request boundary; never send.

Only the completion collaborator is replaced with native ledger reservation
followed by a deliberate stop. No model response, paid receipt or credit is
invented. Owner leasing is an explicit local seam; budget admission is real.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path

from gateway_ledger_support import gateway_ledger as gateway_ledger
import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db import models as m
from bestseller.services import llm, story_architect
from bestseller.services import production_strategy as strategy
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger
from bestseller.services.execution_authorization import authorize_provider_completion
from bestseller.services.single_dispatch_authorization import authorization_id, register_grant
from bestseller.settings import load_settings
from bestseller.web import server
from support.database_safety import require_test_engine_url

pytestmark = pytest.mark.unit
MODEL = "openai/MiniMax-M3"
ENDPOINT = "https://api.minimaxi.com/v1"


@pytest.fixture
def offline_web_owner(monkeypatch):
    @asynccontextmanager
    async def owned(_settings, slug):
        assert slug
        yield

    monkeypatch.setattr("bestseller.services.web_pipeline_owner.web_pipeline_owner", owned)


async def ledger_state(engine, scope):
    async with AsyncSession(engine) as session:
        row = await session.get(m.ExecutionBudgetModel, scope)
        attempts = await session.scalar(select(func.count()).select_from(m.ExecutionAttemptModel))
        return row, attempts


@pytest.mark.parametrize("profile", [None, "complete", "research"])
@pytest.mark.parametrize("chapters,stop", [(500, False), (4, False), (500, True)])
def test_real_web_architect_request_obeys_native_remaining_budget(
    gateway_ledger,
    offline_web_owner,
    monkeypatch,
    tmp_path,
    profile,
    chapters,
    stop,
):
    engine = gateway_ledger
    require_test_engine_url(engine.url)
    assert Path(engine.url.database).name.endswith("_test.sqlite3")
    settings = load_settings(env={})
    settings.budget.execution_max_calls = 64
    settings.budget.execution_max_tokens = 1_000_000
    scope = "architect-entry-test"

    async def initialize():
        metadata = MetaData()
        table = m.WorkflowRunModel.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type, column.server_default = JSON(), None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
        await ExecutionLedger(engine, scope, settings.budget).check()
        row, _ = await ledger_state(engine, scope)
        return deepcopy(row.metadata_json)

    original_metadata = asyncio.run(initialize())
    manager = server.WebTaskManager(persist_path=tmp_path / "headroom-tasks.json")
    captured = []
    monkeypatch.setattr(
        manager, "create_autowrite_task", lambda payload: captured.append(payload) or {}
    )
    manager.create_quickstart_task(
        {
            "genre_key": "realistic-life",
            "chapter_count": chapters,
            "production_profile": profile,
            "stop_after_conception": stop,
        }
    )
    payload = deepcopy(captured[0])
    if profile is None:
        payload.pop("production_profile", None)
        payload["user_hints"].pop("production_profile", None)
    # This test starts at the architect boundary, without a synthetic paid WF.
    payload.pop("creation_intent_contract", None)
    task_id = "architect-native-entry-test"
    manager._tasks[task_id] = server.WebTaskState(
        task_id=task_id,
        task_type="autowrite",
        status="running",
        payload=payload,
        created_at="2026-10-26T00:00:00+00:00",
        updated_at="2026-10-26T00:00:00+00:00",
    )
    seen, stopped = [], []

    @asynccontextmanager
    async def sessions(_settings):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    async def reserve_without_send(session, config, request):
        assert request.prompt_template == "story_architect_v1"
        assert strategy.stream_conception_enabled() is False
        role = config.llm.planner
        before = llm._effective_input_payload(request, role)
        effective = strategy.apply_production_stream_settings(
            request,
            role,
            ambient_complete=strategy.stream_conception_enabled(),
        )
        assert llm._effective_input_payload(request, effective) == before
        seen.append(deepcopy(request))
        ledger = ExecutionLedger(engine, scope, config.budget)
        digest = llm._effective_input_hash(request, effective)
        claim = await ledger.claim(request.operation_id, digest)
        await ledger.reserve(claim, effective.model, digest, 20_000)
        raise ExecutionBudgetError("offline_architect_admitted_no_send")

    async def no_conception(*args, **kwargs):
        pytest.fail("Must stop at native architect reservation, before conception")

    def no_provider(*args, **kwargs):
        pytest.fail("No provider/SDK execution is permitted")

    monkeypatch.setattr(server, "session_scope", sessions)
    monkeypatch.setattr(server, "load_settings", lambda: settings)
    monkeypatch.setattr(
        manager, "_stop_execution_task", lambda _tid, error: stopped.append(error.code)
    )
    monkeypatch.setattr(story_architect, "complete_text", reserve_without_send)
    monkeypatch.setattr(llm, "_call_litellm", no_provider)
    monkeypatch.setattr("bestseller.services.conception.run_conception_pipeline", no_conception)
    manager._run_autowrite_worker(task_id, payload)

    denied = chapters == 500 and not stop and profile is not None
    assert stopped == [
        "execution_downstream_budget_reserved" if denied else "offline_architect_admitted_no_send"
    ]
    assert len(seen) == 1
    assert seen[0].metadata["agent"] == "story_architect"
    assert payload["user_hints"].get("production_profile") == profile
    row, attempts = asyncio.run(ledger_state(engine, scope))
    assert (row.max_calls, row.max_tokens) == (64, 1_000_000)
    assert (row.calls_used, row.tokens_charged, attempts) == (
        (0, 0, 0) if denied else (1, 20_000, 1)
    )
    assert row.metadata_json == original_metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile,stop", [(None, False), ("complete", False), ("research", False), ("complete", True)]
)
@pytest.mark.parametrize("axis", ["calls", "tokens"])
async def test_nested_architect_cannot_loosen_outer_native_reserve(
    gateway_ledger,
    profile,
    stop,
    axis,
):
    # Isolate each dimension using original, fixed test-scope ceilings. The
    # other ceiling is sufficient even for the outer 500-chapter reserve.
    budget = load_settings(env={}).budget.model_copy(
        update={
            "execution_max_calls": 64 if axis == "calls" else 10_000,
            "execution_max_tokens": 10_000_000 if axis == "calls" else 1_000_000,
        }
    )
    ledger = ExecutionLedger(gateway_ledger, "architect-nested-test", budget)
    claim = await ledger.claim("nested-architect", "same-original-input")
    with strategy.protect_downstream("complete", 500):
        with pytest.raises(ExecutionBudgetError, match="execution_downstream_budget_reserved"):
            with strategy.protect_architect_headroom(profile, 4, stop_after_conception=stop):
                assert strategy.stream_conception_enabled()
                await ledger.reserve(claim, "offline", "same-original-input", 1000)
        # The exception exits the inner context; outer admission remains strict.
        with pytest.raises(ExecutionBudgetError, match="execution_downstream_budget_reserved"):
            await ledger.reserve(claim, "offline", "same-original-input", 1000)
        assert strategy.stream_conception_enabled()
        row, attempts = await ledger_state(gateway_ledger, ledger.scope_id)
        assert (row.calls_used, row.tokens_charged, attempts) == (0, 0, 0)
    assert not strategy.stream_conception_enabled()
    await ledger.reserve(claim, "offline", "same-original-input", 1000)
    row, attempts = await ledger_state(gateway_ledger, ledger.scope_id)
    assert (row.calls_used, row.tokens_charged, attempts) == (1, 1000, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("calls,tokens", [(26, 900_000), (1, 970_000)])
async def test_guard_uses_remaining_not_original_ceiling_and_restores_context(
    gateway_ledger,
    calls,
    tokens,
):
    ledger = ExecutionLedger(
        gateway_ledger, "architect-remaining-test", load_settings(env={}).budget
    )
    await ledger.check()
    async with ledger.sessions() as session:
        row = await session.get(m.ExecutionBudgetModel, ledger.scope_id)
        row.max_calls, row.max_tokens = 64, 1_000_000
        row.calls_used, row.tokens_charged = calls, tokens
        await session.commit()
    claim = await ledger.claim("new-architect", "exact-input")
    with strategy.protect_downstream("complete", 4):
        assert strategy.stream_conception_enabled()
        with pytest.raises(ExecutionBudgetError, match="execution_downstream_budget_reserved"):
            with strategy.protect_architect_headroom("research", 4):
                assert strategy.stream_conception_enabled()  # no _PROFILE write
                await ledger.reserve(claim, "offline", "exact-input", 1000)
        assert strategy.stream_conception_enabled()
    assert not strategy.stream_conception_enabled()
    # Context reset restores the original remaining credit, without a new grant.
    await ledger.reserve(claim, "offline", "exact-input", 1000)
    row, attempts = await ledger_state(gateway_ledger, ledger.scope_id)
    assert (row.calls_used, row.tokens_charged, attempts) == (calls + 1, tokens + 1000, 1)
    assert (row.max_calls, row.max_tokens) == (64, 1_000_000)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile,stop", [(None, False), ("", False), ("complete", True)])
async def test_legacy_or_conception_only_does_not_reserve_body_credit(
    gateway_ledger, profile, stop
):
    ledger = ExecutionLedger(gateway_ledger, "architect-legacy-test", load_settings(env={}).budget)
    claim = await ledger.claim("legacy-architect", "same-input")
    with strategy.protect_architect_headroom(profile, 500, stop_after_conception=stop):
        assert not strategy.stream_conception_enabled()
        await ledger.reserve(claim, "offline", "same-input", 1000)
    row, attempts = await ledger_state(gateway_ledger, ledger.scope_id)
    assert attempts == row.calls_used == 1


@pytest.mark.asyncio
async def test_cached_native_operation_remains_reusable_without_reservation(gateway_ledger):
    ledger = ExecutionLedger(gateway_ledger, "architect-cached-test", load_settings(env={}).budget)
    original = await ledger.claim("retained-local-preview", "same-input")
    # An actual local operation cache; no fabricated model response/paid receipt.
    result = {"local_preview": "retained exact architect input", "provider_calls": 0}
    await ledger.finish(original, result)
    before, count = await ledger_state(gateway_ledger, ledger.scope_id)
    with strategy.protect_architect_headroom("complete", 500):
        cached = await ledger.claim("retained-local-preview", "same-input")
        assert cached.cached_result == result
    after, attempts = await ledger_state(gateway_ledger, ledger.scope_id)
    assert (after.calls_used, after.tokens_charged, attempts) == (
        before.calls_used,
        before.tokens_charged,
        count,
    )


@pytest.mark.asyncio
async def test_completion_bound_route_keeps_real_provider_and_single_dispatch_grants(
    gateway_ledger,
):
    ledger = ExecutionLedger(
        gateway_ledger, "architect-completion-test", load_settings(env={}).budget
    )
    await ledger.check()
    async with ledger.sessions() as session:
        row = await session.get(m.ExecutionBudgetModel, ledger.scope_id)
        row.max_calls, row.max_tokens = 64, 1_000_000
        await session.commit()
        databases = (await session.execute(text("PRAGMA database_list"))).all()
        assert Path(next(r[2] for r in databases if r[1] == "main")) == Path(
            gateway_ledger.url.database
        )
    await authorize_provider_completion(
        gateway_ledger,
        scope_id=ledger.scope_id,
        idempotency_key="local-native-approval",
        actor="isolated-test-operator",
        authorization_ref="isolated-explicit-completion-approval",
        objective="offline architect admission only",
        expected_max_calls=64,
        expected_max_tokens=1_000_000,
    )
    claim = await ledger.claim("architect", "exact-input")
    binding = dict(
        scope_id=ledger.scope_id,
        operation_key=claim.key,
        input_hash="exact-input",
        model=MODEL,
        endpoint=ENDPOINT,
        workflow_id="local-owned-workflow",
        user_message_id="local-native-approval",
        prior_workflow_id="local-prior-workflow",
        prior_journal_sha256="a" * 64,
        template="story_architect_v1",
        version_prefix="1.0",
        authority_evidence={"source": "isolated explicit one-send fixture"},
    )
    grant = authorization_id(binding["user_message_id"], binding["prior_workflow_id"])
    await register_grant(ledger, grant_id=grant, binding=binding)
    kwargs = dict(
        endpoint=ENDPOINT,
        workflow_id=binding["workflow_id"],
        template="story_architect_v1",
        version="1.0",
    )
    with strategy.protect_architect_headroom("complete", 500):
        with pytest.raises(ExecutionBudgetError, match="single_dispatch_authorization_missing"):
            await ledger.reserve(claim, MODEL, "exact-input", 20_000, **kwargs)
        with pytest.raises(ExecutionBudgetError, match="execution_provider_not_authorized"):
            await ledger.reserve(
                claim, "other-model", "exact-input", 20_000, single_dispatch_id=grant, **kwargs
            )
        attempt = await ledger.reserve(
            claim, MODEL, "exact-input", 20_000, single_dispatch_id=grant, **kwargs
        )
        with pytest.raises(ExecutionBudgetError, match="single_dispatch_already_spent"):
            await ledger.reserve(
                claim, MODEL, "exact-input", 20_000, single_dispatch_id=grant, **kwargs
            )
    row, attempts = await ledger_state(gateway_ledger, ledger.scope_id)
    assert (row.max_calls, row.max_tokens) == (64, 1_000_000)
    assert (row.calls_used, row.tokens_charged, attempts) == (1, 20_000, 1)
    assert row.metadata_json["single_dispatch_grants"][grant]["used_attempt_id"] == attempt
