"""Native SQLite ledger only; no provider wire or production authority.

Fixtures exercise operator authorization, claim/reserve/settle and closing.
An attempt reservation is ledger bookkeeping, never an actual model call.
"""

# ruff: noqa: F811 -- imported pytest fixture is intentionally injected by name.
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import socket

import pytest
from sqlalchemy import select
from test_execution_budget_core import ledger_db  # noqa: F401

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
)
from bestseller.services import llm
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger
from bestseller.services.execution_authorization import (
    authorize_provider_completion,
    close_provider_completion,
)
from bestseller.settings import BudgetSettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
M3 = "openai/MiniMax-M3"
M31 = "openai/MiniMax-M3.1-Flash"
ENDPOINT = "https://api.minimaxi.com/v1"
SCOPE = "project:offline-completion-m31-four-chapters"


class ProviderWireTripwire(BaseException):
    pass


@pytest.fixture(autouse=True)
def no_provider_wire(monkeypatch):
    def denied(*args, **kwargs):
        raise ProviderWireTripwire("completion authorization fixture forbids provider/network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(llm, "complete_text", denied)


def grant(**changes):
    args = {
        "scope_id": SCOPE,
        "idempotency_key": "offline-human-completion",
        "actor": "user",
        "authorization_ref": "offline-human-exact-four-chapter-instruction",
        "objective": "complete the same four-chapter book and inspect final delivery",
        "expected_max_calls": 2,
        "expected_max_tokens": 20,
    }
    args.update(changes)
    return args


async def setup(engine, *, historical=True):
    ledger = ExecutionLedger(
        engine, SCOPE, BudgetSettings(execution_max_calls=2, execution_max_tokens=20)
    )
    await ledger.check()
    if historical:
        async with ledger.sessions() as session:
            row = await session.get(ExecutionBudgetModel, SCOPE)
            row.calls_used, row.tokens_charged = 41, 12345
            row.deadline_at = datetime.now(UTC) - timedelta(days=1)
            row.status = "exhausted"
            row.metadata_json = {
                "old_usage_evidence": {"retained": True, "paid_calls": 41},
                "operator_authorizations": {"old-finite-allowance": {"retained": True}},
            }
            await session.commit()
    return ledger


async def snapshot(ledger):
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, SCOPE)
        return {
            "max_calls": row.max_calls,
            "max_tokens": row.max_tokens,
            "calls_used": row.calls_used,
            "tokens_charged": row.tokens_charged,
            "deadline_at": row.deadline_at.isoformat(),
            "status": row.status,
            "metadata": deepcopy(row.metadata_json),
        }


async def test_m31_exact_reservations_keep_original_caps_deadline_and_all_usage_history(ledger_db):
    ledger = await setup(ledger_db)
    before = await snapshot(ledger)
    args = grant(model=M31)
    receipt = await authorize_provider_completion(ledger_db, **args)
    assert receipt["request"] == {
        k: v for k, v in args.items() if k not in {"scope_id", "idempotency_key"}
    }
    assert receipt["authorization"]["models"] == [M31]
    assert receipt["authorization"]["endpoint_hosts"] == ["api.minimaxi.com"]
    assert receipt["authorization"]["aggregate_max_calls"] is None
    assert receipt["authorization"]["aggregate_max_tokens"] is None
    assert receipt["authorization"]["aggregate_deadline_at"] is None
    authorized = await snapshot(ledger)
    for field in ("max_calls", "max_tokens", "calls_used", "tokens_charged", "deadline_at"):
        assert authorized[field] == before[field]
    for op, actual in [("first", 17), ("second", 23)]:
        claim = await ledger.claim(op, op)
        attempt = await ledger.reserve(claim, M31, op, 2000000, endpoint=ENDPOINT)
        await ledger.settle(attempt, "completed", actual_tokens=actual)
        await ledger.finish(claim, {"content": "synthetic retained result", "model_name": M31})
    after = await snapshot(ledger)
    assert (after["calls_used"], after["tokens_charged"]) == (43, 12385)
    assert (after["max_calls"], after["max_tokens"], after["deadline_at"]) == (
        before["max_calls"],
        before["max_tokens"],
        before["deadline_at"],
    )
    for key, value in before["metadata"].items():
        assert after["metadata"][key] == value
    assert after["metadata"]["provider_authorizations"][args["idempotency_key"]] == receipt
    async with ledger.sessions() as session:
        attempts = list(
            await session.scalars(
                select(ExecutionAttemptModel).where(ExecutionAttemptModel.scope_id == SCOPE)
            )
        )
        assert len(attempts) == 2 and all(
            a.model_name == M31 and a.status == "completed" for a in attempts
        )


async def test_new_m31_authority_retains_real_completed_native_history(ledger_db):
    ledger = await setup(ledger_db, historical=False)
    claim = await ledger.claim("original-paid-operation", "original-hash")
    attempt = await ledger.reserve(claim, M3, "original-hash", 10)
    retained = {"content": "offline retained historical result", "model_name": M3}
    await ledger.settle(attempt, "completed", actual_tokens=7)
    await ledger.finish(claim, retained)
    before = await snapshot(ledger)
    await authorize_provider_completion(ledger_db, **grant(model=M31))
    after = await snapshot(ledger)
    for field in ("calls_used", "tokens_charged", "max_calls", "max_tokens", "deadline_at"):
        assert after[field] == before[field]
    async with ledger.sessions() as session:
        old_op = await session.get(ExecutionOperationModel, claim.key)
        old_attempt = await session.get(ExecutionAttemptModel, attempt)
        assert old_op.status == "completed" and old_op.result_json == retained
        assert old_attempt.status == "completed" and old_attempt.model_name == M3
        assert old_attempt.actual_tokens == 7 and old_attempt.reserved_tokens == 10
        assert len(list(await session.scalars(select(ExecutionAttemptModel)))) == 1


@pytest.mark.parametrize(
    "model,endpoint",
    [
        (M3, ENDPOINT),
        ("minimax/MiniMax-M3.1-Flash", ENDPOINT),
        ("openai/other", ENDPOINT),
        (M31, "https://other.example/v1"),
        (M31, "https://api.minimaxi.com.evil.example/v1"),
        (M31, "http://api.minimaxi.com/v1"),
        (M31, None),
    ],
)
async def test_m31_authority_never_allows_another_model_or_host(ledger_db, model, endpoint):
    ledger = await setup(ledger_db)
    await authorize_provider_completion(ledger_db, **grant(model=M31))
    before = await snapshot(ledger)
    claim = await ledger.claim("refused", "hash")
    with pytest.raises(ExecutionBudgetError, match="execution_provider_not_authorized"):
        await ledger.reserve(claim, model, "hash", 5, endpoint=endpoint)
    assert await snapshot(ledger) == before
    async with ledger.sessions() as session:
        assert not list(
            await session.scalars(
                select(ExecutionAttemptModel).where(ExecutionAttemptModel.scope_id == SCOPE)
            )
        )


@pytest.mark.parametrize(
    "model",
    [
        "openai/other",
        "openai/MiniMax-M3.1",
        "minimax/MiniMax-M3.1-Flash",
        "openai/minimax-m3.1-flash",
        "openai/MiniMax-M3.1-Flash ",
        "",
        False,
    ],
)
async def test_authorize_rejects_every_nonwhitelisted_explicit_model_without_mutation(
    ledger_db, model
):
    ledger = await setup(ledger_db)
    before = await snapshot(ledger)
    with pytest.raises(ValueError, match="supported MiniMax model"):
        await authorize_provider_completion(ledger_db, **grant(model=model))
    assert await snapshot(ledger) == before


async def test_explicit_m31_idempotence_and_model_change_conflict_are_exact(ledger_db):
    ledger = await setup(ledger_db)
    args = grant(model=M31)
    first = await authorize_provider_completion(ledger_db, **args)
    before = await snapshot(ledger)
    assert await authorize_provider_completion(ledger_db, **args) == first
    for replacement in (M3, None):
        with pytest.raises(ValueError, match="already bound to different action"):
            await authorize_provider_completion(ledger_db, **grant(model=replacement))
    assert await snapshot(ledger) == before


async def test_legacy_omitted_and_none_models_keep_the_original_m3_request_shape(ledger_db):
    ledger = await setup(ledger_db)
    args = grant()
    first = await authorize_provider_completion(ledger_db, **args)
    assert "model" not in first["request"]
    assert first["request"] == {
        k: v for k, v in args.items() if k not in {"scope_id", "idempotency_key"}
    }
    assert first["authorization"]["models"] == [M3]
    assert await authorize_provider_completion(ledger_db, **grant(model=None)) == first
    with pytest.raises(ValueError, match="already bound to different action"):
        await authorize_provider_completion(ledger_db, **grant(model=M3))
    claim = await ledger.claim("legacy", "hash")
    attempt = await ledger.reserve(claim, M3, "hash", 99, endpoint=ENDPOINT)
    assert attempt


@pytest.mark.parametrize("state", ["unknown", "cancelled", "awaiting_submission"])
async def test_explicit_authorization_never_reopens_frozen_scope(ledger_db, state):
    ledger = await setup(ledger_db)
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, SCOPE)
        row.status = state
        await session.commit()
    before = await snapshot(ledger)
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_scope_frozen"):
        await authorize_provider_completion(ledger_db, **grant(model=M31))
    assert await snapshot(ledger) == before


@pytest.mark.parametrize("op_status", ["pending", "unknown"])
async def test_pending_or_unknown_operation_prevents_initial_authorization(ledger_db, op_status):
    ledger = await setup(ledger_db, historical=False)
    claim = await ledger.claim("unsettled", "hash")
    if op_status == "unknown":
        async with ledger.sessions() as session:
            operation = await session.get(ExecutionOperationModel, claim.key)
            operation.status = "unknown"
            await session.commit()
    before = await snapshot(ledger)
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_inflight"):
        await authorize_provider_completion(ledger_db, **grant(model=M31))
    assert await snapshot(ledger) == before


async def test_idempotent_receipt_replay_cannot_unfreeze_a_real_unknown_settlement(ledger_db):
    ledger = await setup(ledger_db)
    receipt = await authorize_provider_completion(ledger_db, **grant(model=M31))
    claim = await ledger.claim("unknown-send", "hash")
    attempt = await ledger.reserve(claim, M31, "hash", 50, endpoint=ENDPOINT)
    await ledger.settle(attempt, "unknown")
    before = await snapshot(ledger)
    assert before["status"] == "unknown"
    assert await authorize_provider_completion(ledger_db, **grant(model=M31)) == receipt
    assert await snapshot(ledger) == before
    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
        await ledger.claim("cannot-continue", "new-hash")


async def test_m31_objective_closes_and_new_objective_needs_new_human_authority(ledger_db):
    ledger = await setup(ledger_db)
    first = await authorize_provider_completion(ledger_db, **grant(model=M31))
    close_args = {"scope_id": SCOPE, "actor": "offline-operator", "evidence_sha256": "a" * 64}
    closed = await close_provider_completion(ledger_db, **close_args)
    assert await close_provider_completion(ledger_db, **close_args) == closed
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_closed"):
        await ledger.claim("after-close", "hash")
    with pytest.raises(ValueError, match="new operator authorization"):
        await authorize_provider_completion(
            ledger_db,
            **grant(
                model=M31, idempotency_key="next", objective="next explicitly authorized objective"
            ),
        )
    fresh = await authorize_provider_completion(
        ledger_db,
        **grant(
            model=M31,
            idempotency_key="next",
            objective="next explicitly authorized objective",
            authorization_ref="new-offline-human-instruction",
        ),
    )
    assert fresh["authorization"]["models"] == [M31]
    await ledger.check()
    second = await close_provider_completion(
        ledger_db, **{**close_args, "evidence_sha256": "b" * 64}
    )
    after = await snapshot(ledger)
    assert after["metadata"]["provider_authorizations"]["offline-human-completion"] == first
    assert after["metadata"]["completion_receipts"][closed["authorization_ref"]] == closed
    assert after["metadata"]["completion_receipts"][second["authorization_ref"]] == second
    assert (
        after["calls_used"],
        after["tokens_charged"],
        after["max_calls"],
        after["max_tokens"],
    ) == (41, 12345, 2, 20)


async def test_m31_objective_cannot_close_while_native_reservation_is_pending(ledger_db):
    ledger = await setup(ledger_db)
    await authorize_provider_completion(ledger_db, **grant(model=M31))
    claim = await ledger.claim("pending", "hash")
    await ledger.reserve(claim, M31, "hash", 50, endpoint=ENDPOINT)
    before = await snapshot(ledger)
    with pytest.raises(ExecutionBudgetError, match="execution_authorization_inflight"):
        await close_provider_completion(
            ledger_db, scope_id=SCOPE, actor="offline-operator", evidence_sha256="a" * 64
        )
    assert await snapshot(ledger) == before
