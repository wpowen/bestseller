"""Finite grant must govern native claim/reserve and survive every re-entry."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from bestseller.infra.db import models as m
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger, bind_execution_scope
from bestseller.services.retry_ledger import project_execution_scope
from bestseller.settings import BudgetSettings

from .conftest import CALLS, ENDPOINT, KEY, MODEL, TOKENS, diagnostic_summary
from .test_grant import assert_difference

pytestmark = pytest.mark.unit


async def test_grant_caps_survive_default_config_and_unrelated_projects_keep_defaults(case):
    grant = await case.active()
    first = case.ledger(grant)
    await first.check()
    await case.complete(first, "first")
    restarted = case.ledger(grant)
    await restarted.check()
    row = await case.snapshot(grant["scope_id"])
    assert (row["max_calls"], row["max_tokens"]) == (CALLS, TOKENS)
    assert row["deadline_at"] == grant["deadline_at"]
    assert (row["calls_used"], row["tokens_charged"]) == (1, 10)
    unrelated = ExecutionLedger(case.engine, f"project:{uuid4()}", BudgetSettings())
    await unrelated.check()
    other = await case.snapshot(unrelated.scope_id)
    assert (other["max_calls"], other["max_tokens"]) == (64, 1_000_000)
    assert KEY not in other["metadata"]
    assert BudgetSettings().execution_max_calls == 64
    assert BudgetSettings().execution_max_tokens == 1_000_000
    assert await case.snapshot(grant["scope_id"]) == row


async def test_materialized_project_workflows_and_resume_share_original_ledger(case):
    grant = await case.active()
    with bind_execution_scope(grant["scope_id"]):
        await case.complete(case.ledger(grant), "conception", tokens=11)
    project_id = uuid4()
    async with case.sessions.begin() as session:
        session.add(m.ProjectModel(
            id=project_id, slug=f"new-book-{project_id.hex}", title="Offline normal creation",
            genre="family", target_word_count=8000, target_chapters=4,
            metadata_json={"execution_scope_id": grant["scope_id"]},
        ))
    for stage in ("plan", "draft", "review", "rewrite", "knowledge", "resume"):
        async with case.sessions() as session:
            project = await session.get(m.ProjectModel, project_id)
            scope = project_execution_scope(project)
        assert scope == grant["scope_id"]
        with bind_execution_scope(scope):
            ledger = ExecutionLedger(case.engine, scope, BudgetSettings())
            await case.complete(ledger, f"{stage}:{uuid4()}", tokens=11)
    row = await case.snapshot(grant["scope_id"])
    assert (row["calls_used"], row["tokens_charged"]) == (7, 77)
    assert (row["max_calls"], row["max_tokens"]) == (CALLS, TOKENS)
    assert row["deadline_at"] == grant["deadline_at"]
    assert await case.count(m.ExecutionBudgetModel) == 1
    assert await case.count(m.ExecutionAttemptModel) == 7


async def test_native_400th_reservation_allowed_401st_rejected_without_network(case):
    grant = await case.active()
    ledger = case.ledger(grant)
    # These are native local claims/reservations, not synthetic attempt rows or SDK sends.
    for index in range(CALLS):
        await case.complete(ledger, f"physical-attempt-slot-{index}", tokens=1)
    before = await case.snapshot(grant["scope_id"])
    assert (before["calls_used"], before["tokens_charged"]) == (CALLS, CALLS)
    assert await case.count(m.ExecutionAttemptModel) == CALLS
    claim = await ledger.claim("attempt-401", "input-401")
    with pytest.raises(ExecutionBudgetError, match="execution_budget_exhausted"):
        await ledger.reserve(claim, MODEL, "input-401", 1, endpoint=ENDPOINT)
    after = await case.snapshot(grant["scope_id"])
    assert (after["calls_used"], after["tokens_charged"]) == (CALLS, CALLS)
    assert after["status"] == "exhausted"
    assert after["deadline_at"] == before["deadline_at"]
    assert await case.count(m.ExecutionAttemptModel) == CALLS


@pytest.mark.parametrize("used,requested_tokens", [(TOKENS - 1, 2), (TOKENS, 1)])
async def test_token_ceiling_rejects_before_native_attempt_insert(case, used, requested_tokens):
    grant = await case.active()
    ledger = case.ledger(grant)
    await case.complete(ledger, "charged-original-tokens", tokens=used)
    claim = await ledger.claim("over-token-cap", "hash")
    with pytest.raises(ExecutionBudgetError, match="execution_budget_exhausted"):
        await ledger.reserve(claim, MODEL, "hash", requested_tokens, endpoint=ENDPOINT)
    row = await case.snapshot(grant["scope_id"])
    assert (row["calls_used"], row["tokens_charged"]) == (1, used)
    assert await case.count(m.ExecutionAttemptModel) == 1


async def test_exact_token_boundary_allowed_but_held_reservation_also_counts(case):
    grant = await case.active()
    ledger = case.ledger(grant)
    first = await ledger.claim("held", "input-held")
    await ledger.reserve(first, MODEL, "input-held", TOKENS - 1, endpoint=ENDPOINT)
    second = await ledger.claim("last-token", "input-last")
    await ledger.reserve(second, MODEL, "input-last", 1, endpoint=ENDPOINT)
    third = await ledger.claim("overflow", "input-overflow")
    with pytest.raises(ExecutionBudgetError, match="execution_budget_exhausted"):
        await ledger.reserve(third, MODEL, "input-overflow", 1, endpoint=ENDPOINT)
    row = await case.snapshot(grant["scope_id"])
    assert (row["calls_used"], row["tokens_charged"]) == (2, TOKENS)
    assert await case.count(m.ExecutionAttemptModel) == 2


@pytest.mark.parametrize("field,value", [
    ("model", "openai/another-model"),
    ("endpoint", "https://other-provider.invalid/v1"),
    ("endpoint", None),
])
async def test_native_reservation_rechecks_model_and_endpoint(case, field, value, request):
    grant = await case.active()
    ledger = case.ledger(grant)
    claim = await ledger.claim("wrong-route", "input")
    before = await case.snapshot(grant["scope_id"])
    route = {"model": MODEL, "endpoint": ENDPOINT, field: value}
    with pytest.raises(ExecutionBudgetError) as raised:
        await ledger.reserve(claim, route["model"], "input", 10, endpoint=route["endpoint"])
    assert raised.value.code == "execution_new_book_route_mismatch"
    difference = assert_difference(raised.value, f"request.{field}", request)
    expected = {"model": MODEL, "endpoint": ENDPOINT}[field]
    assert difference["expected"] == diagnostic_summary(expected)
    assert difference["actual"] == diagnostic_summary(value)
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionAttemptModel) == 0


async def test_unknown_preserves_charge_and_rejects_reconsume_reentry_and_other_operation(case):
    grant = await case.active()
    ledger = case.ledger(grant)
    claim = await ledger.claim("uncertain", "input")
    attempt = await ledger.reserve(claim, MODEL, "input", 12345, endpoint=ENDPOINT)
    await ledger.settle(attempt, "unknown")
    before = await case.snapshot(grant["scope_id"])
    assert before["status"] == "unknown"
    assert (before["calls_used"], before["tokens_charged"]) == (1, 12345)
    for identity in ("uncertain", "different"):
        with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
            await case.ledger(grant).claim(identity, "input")
    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
        await case.consume(grant)
    assert await case.prepare() == grant
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1
    assert await case.count(m.ExecutionAttemptModel) == 1


async def test_cached_native_result_does_not_consume_a_new_call(case):
    grant = await case.active()
    claim, _, result = await case.complete(case.ledger(grant), "cached")
    before = await case.snapshot(grant["scope_id"])
    async with case.sessions() as session:
        native = await session.get(m.ExecutionOperationModel, claim.key)
        digest = native.input_hash
    replay = await case.ledger(grant).claim("cached", digest)
    assert replay.cached_result == result
    assert replay.key == claim.key
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionAttemptModel) == 1


async def test_native_transport_retry_consumes_another_call_in_same_grant(case):
    grant = await case.active()
    ledger = case.ledger(grant)
    claim = await ledger.claim("retry", "input")
    first = await ledger.reserve(claim, MODEL, "input", 100, endpoint=ENDPOINT)
    await ledger.settle(first, "rejected", actual_tokens=0)
    second = await ledger.reserve(claim, MODEL, "input", 100, endpoint=ENDPOINT)
    row = await case.snapshot(grant["scope_id"])
    assert first != second
    assert (row["calls_used"], row["tokens_charged"]) == (2, 100)
    assert await case.count(m.ExecutionAttemptModel) == 2


@pytest.mark.parametrize("field", ["max_calls", "max_tokens", "deadline_at", "grant_missing"])
async def test_corrupt_native_caps_deadline_or_missing_journal_fail_closed(case, field):
    grant = await case.active()
    async with case.sessions.begin() as session:
        row = await session.get(m.ExecutionBudgetModel, grant["scope_id"])
        if field == "deadline_at":
            row.deadline_at += timedelta(seconds=1)
        elif field == "grant_missing":
            metadata = deepcopy(row.metadata_json)
            metadata.pop(KEY)
            row.metadata_json = metadata
        else:
            setattr(row, field, getattr(row, field) + 1)
    before = await case.snapshot(grant["scope_id"])
    with pytest.raises(ExecutionBudgetError, match="execution_new_book_binding_invalid"):
        await case.ledger(grant).claim("corrupted-grant", "input")
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionOperationModel) == 0
    assert await case.count(m.ExecutionAttemptModel) == 0


async def test_copying_grant_metadata_to_other_native_scope_cannot_transfer_authority(case):
    grant = await case.active()
    another = ExecutionLedger(case.engine, f"project:{uuid4()}", BudgetSettings())
    await another.check()
    async with case.sessions.begin() as session:
        original = await session.get(m.ExecutionBudgetModel, grant["scope_id"])
        other = await session.get(m.ExecutionBudgetModel, another.scope_id)
        other.metadata_json = deepcopy(original.metadata_json)
        other.max_calls, other.max_tokens = original.max_calls, original.max_tokens
        other.deadline_at = original.deadline_at
    before = await case.snapshot(another.scope_id)
    with pytest.raises(ExecutionBudgetError, match="execution_new_book_binding_invalid"):
        await another.claim("cross-scope", "input")
    assert await case.snapshot(another.scope_id) == before
    assert await case.count(m.ExecutionAttemptModel) == 0


async def test_native_claim_stops_at_creation_deadline_after_delayed_consumption(case, clock):
    grant = await case.prepare()
    clock.advance(hours=23)
    await case.consume(grant)
    await case.complete(case.ledger(grant), "before-deadline")
    clock.advance(hours=1)
    with pytest.raises(ExecutionBudgetError, match="execution_deadline_exhausted"):
        await case.ledger(grant).claim("expired", "input")
    row = await case.snapshot(grant["scope_id"])
    assert row["deadline_at"] == grant["deadline_at"]
    assert (row["calls_used"], row["tokens_charged"]) == (1, 10)
    assert await case.count(m.ExecutionAttemptModel) == 1


async def test_consumption_does_not_replenish_exhausted_or_cancelled_scope(case):
    grant = await case.active()
    await case.complete(case.ledger(grant), "already-charged")
    for state, code in (
        ("exhausted", "execution_budget_exhausted"),
        ("cancelled", "execution_cancelled"),
    ):
        async with case.sessions.begin() as session:
            row = await session.get(m.ExecutionBudgetModel, grant["scope_id"])
            row.status = state
        before = await case.snapshot(grant["scope_id"])
        with pytest.raises(ExecutionBudgetError, match=code):
            await case.consume(grant)
        assert await case.prepare() == grant
        assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1


async def test_prepared_binding_survives_unrelated_outer_rollback(case):
    async with case.sessions() as outer:
        await outer.execute(select(m.ExecutionBudgetModel.scope_id))
        grant = await case.prepare()
        await outer.rollback()
    row = await case.snapshot(grant["scope_id"])
    assert row["status"] == "awaiting_submission"
    assert await case.consume(grant) == grant
