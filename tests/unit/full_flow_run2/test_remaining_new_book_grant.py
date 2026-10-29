"""Main-run RED: explicit finite v2 grant, native SQLite, no provider calls.

Proposed API: prepare_new_book_budget(..., max_calls=None, max_tokens=None).
Omitting both preserves v1. Supplying both opts into new-book-budget.v2; only
the trusted preparer supplies limits. The normal POST remains selector-only.
This suite does not compute cross-project allowances or reopen RUN1.
"""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import select

from bestseller.infra.db import models as m
from bestseller.services.budget import ExecutionBudgetError, completion_authorization

# Reuse the existing native ORM fixture without depending on collection order
# or mocking any authorization/parser/ledger service boundary.
_spec = spec_from_file_location(
    "remaining_grant_native_fixture",
    Path(__file__).resolve().parents[1] / "full_flow_new_book_budget/conftest.py",
)
assert _spec is not None and _spec.loader is not None
_native = module_from_spec(_spec)
_spec.loader.exec_module(_native)
api, clock, case = _native.api, _native.clock, _native.case
MODEL, ENDPOINT, KEY = _native.MODEL, _native.ENDPOINT, _native.KEY

pytestmark = pytest.mark.unit
V2 = "new-book-budget.v2"
CALLS, TOKENS = 373, 19_744_663
V1_KEYS = {
    "grant_id", "task_id", "scope_id", "payload_sha256", "model", "endpoint",
    "max_calls", "max_tokens", "created_at", "deadline_at",
}


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


async def prepare_remaining(case, **changes):
    arguments = {
        "authorization_id": case.authorization_id + ":run2",
        "authorization_ref": "offline-user-explicit-new-book-remaining-allowance",
        "max_calls": CALLS, "max_tokens": TOKENS,
    }
    arguments.update(changes)
    return await case.prepare(**arguments)


async def retained_rows(case, scope):
    """Snapshot complete original native rows, including Unknown and paid JSON."""
    result = {}
    async with case.sessions() as session:
        for model in (m.ExecutionBudgetModel, m.ExecutionOperationModel, m.ExecutionAttemptModel):
            rows = list(await session.scalars(select(model).where(model.scope_id == scope)))
            result[model.__tablename__] = sorted(
                json.dumps({field.key: getattr(row, field.key)
                            for field in row.__mapper__.column_attrs},
                           sort_keys=True, default=str)
                for row in rows
            )
    return result


def assert_safe_difference(error, request):
    differences = getattr(error, "differences", None)
    assert differences, "denial must identify typed expected/actual operands"
    request.node.user_properties.append(("grant_differences", json.dumps(differences)))
    for difference in differences:
        assert isinstance(difference["field"], str) and difference["field"]
        for side in ("expected", "actual"):
            value = difference[side]
            assert set(value) == {"type", "length", "sha256"}
            assert isinstance(value["type"], str) and value["type"]
            assert value["length"] is None or type(value["length"]) is int
            assert len(value["sha256"]) == 64
            int(value["sha256"], 16)


async def test_legacy_omitted_limits_keep_exact_v1_identity_and_record(case):
    grant = await case.active()
    expected_id = str(uuid5(NAMESPACE_URL, "new-book-budget.v1:" + digest(case.payload)))
    assert set(grant) == V1_KEYS
    assert grant["grant_id"] == grant["task_id"] == expected_id
    assert (grant["max_calls"], grant["max_tokens"]) == (400, 20_000_000)
    before = await retained_rows(case, grant["scope_id"])
    assert await case.prepare() == grant
    assert await case.consume(grant) == grant
    with pytest.raises(ExecutionBudgetError, match="execution_new_book_binding_mismatch"):
        await case.prepare(authorization_id=case.authorization_id + ":different")
    assert await retained_rows(case, grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1


async def test_same_story_new_explicit_grant_preserves_26_paid_and_unknown(case):
    original_payload = deepcopy(case.payload)
    old = await case.active()
    old_ledger = case.ledger(old)
    for index in range(26):
        await case.complete(old_ledger, f"retained-paid-{index}",
                            tokens=6000 if index < 25 else 16_837)
    claim = await old_ledger.claim("retained-unknown", "retained-unknown-input")
    attempt = await old_ledger.reserve(
        claim, MODEL, "retained-unknown-input", 88_500, endpoint=ENDPOINT,
    )
    await old_ledger.settle(attempt, "unknown")
    old_state = await case.snapshot(old["scope_id"])
    assert (old_state["status"], old_state["calls_used"], old_state["tokens_charged"]) == (
        "unknown", 27, 255_337,
    )
    before = await retained_rows(case, old["scope_id"])
    grant = await prepare_remaining(case)
    assert set(grant) == V1_KEYS | {"schema", "authorization_id"}
    assert grant["schema"] == V2
    assert grant["authorization_id"] == case.authorization_id + ":run2"
    assert grant["grant_id"] == grant["task_id"] != old["task_id"]
    assert grant["scope_id"] == "conception:" + grant["task_id"] != old["scope_id"]
    assert grant["payload_sha256"] == old["payload_sha256"] == digest(original_payload)
    assert (grant["max_calls"], grant["max_tokens"]) == (CALLS, TOKENS)
    pending = await case.snapshot(grant["scope_id"])
    assert pending["status"] == "awaiting_submission"
    assert (pending["calls_used"], pending["tokens_charged"]) == (0, 0)
    assert pending["metadata"]["policy"] == pending["metadata"][KEY]["schema"] == V2
    assert pending["metadata"][KEY]["binding"] == grant
    assert await case.consume(grant) == grant
    await case.ledger(grant).check()  # Ordinary config must not replace v2 with default caps.
    current = await case.snapshot(grant["scope_id"])
    assert (current["max_calls"], current["max_tokens"]) == (CALLS, TOKENS)
    assert await retained_rows(case, old["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 2
    assert await case.count(m.ExecutionAttemptModel) == 27
    assert case.payload == original_payload


async def test_same_authorization_prepare_and_post_are_idempotent_after_spend(case, clock):
    grant = await prepare_remaining(case)
    before = await case.snapshot(grant["scope_id"])
    clock.advance(hours=1)
    assert await prepare_remaining(case) == grant
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.consume(grant) == grant
    await case.complete(case.ledger(grant), "run2-paid", tokens=17)
    spent = await retained_rows(case, grant["scope_id"])
    clock.advance(hours=1)
    assert await prepare_remaining(case) == grant
    assert await case.consume(grant) == grant
    assert await retained_rows(case, grant["scope_id"]) == spent
    assert await case.count(m.ExecutionBudgetModel) == 1
    assert await case.count(m.ExecutionAttemptModel) == 1


async def test_normal_locator_only_consume_and_legacy_v1_do_not_mutate_each_other(case):
    legacy = await case.active()
    legacy_before = await retained_rows(case, legacy["scope_id"])
    grant = await prepare_remaining(case)
    post = case.post(grant)
    assert set(post) == set(case.payload) | {_native.SELECTOR}
    assert {key: value for key, value in post.items() if key != _native.SELECTOR} == case.payload
    assert not {"max_calls", "max_tokens", "authorization_id", "new_book_budget_grant"} & set(post)
    assert await case.api.consume_new_book_budget(
        case.engine, payload=post, model=MODEL, endpoint=ENDPOINT,
    ) == grant
    assert await retained_rows(case, legacy["scope_id"]) == legacy_before
    current_before = await retained_rows(case, grant["scope_id"])
    assert await case.consume(legacy) == legacy
    assert await case.prepare() == legacy
    assert set(legacy) == V1_KEYS
    assert (legacy["max_calls"], legacy["max_tokens"]) == (400, 20_000_000)
    assert await retained_rows(case, legacy["scope_id"]) == legacy_before
    assert await retained_rows(case, grant["scope_id"]) == current_before
    assert await case.count(m.ExecutionBudgetModel) == 2
    assert await case.count(m.ExecutionOperationModel) == 0
    assert await case.count(m.ExecutionAttemptModel) == 0


async def test_distinct_explicit_v2_authorizations_same_payload_and_limits_are_distinct(case):
    first = await prepare_remaining(case)
    before = await retained_rows(case, first["scope_id"])
    second = await prepare_remaining(case, authorization_id=case.authorization_id + ":another")
    assert first["task_id"] != second["task_id"]
    assert first["payload_sha256"] == second["payload_sha256"]
    assert await retained_rows(case, first["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 2


@pytest.mark.parametrize("field,value", [
    ("max_calls", 372), ("max_tokens", TOKENS - 1),
    ("actor", "different-operator"), ("authorization_ref", "different-decision"),
    ("model", "openai/different-model"), ("endpoint", "https://different.invalid/v1"),
    ("payload", {"concept_seed": "different complete payload"}),
])
async def test_same_authorization_cannot_rebind_inputs_route_or_limits(case, field, value, request):
    grant = await prepare_remaining(case)
    before = await retained_rows(case, grant["scope_id"])
    with pytest.raises(ExecutionBudgetError) as caught:
        await prepare_remaining(case, **{field: value})
    assert caught.value.code == "execution_new_book_binding_mismatch"
    assert_safe_difference(caught.value, request)
    assert await retained_rows(case, grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1


@pytest.mark.parametrize("field,value", [
    ("max_calls", True), ("max_calls", False), ("max_calls", -1), ("max_calls", 0),
    ("max_calls", 401), ("max_calls", 373.0), ("max_calls", "373"), ("max_calls", None),
    ("max_tokens", True), ("max_tokens", False), ("max_tokens", -1), ("max_tokens", 0),
    ("max_tokens", 20_000_001), ("max_tokens", float(TOKENS)),
    ("max_tokens", str(TOKENS)), ("max_tokens", None),
])
async def test_invalid_trusted_limits_fail_before_native_rows(case, field, value, request):
    with pytest.raises(ExecutionBudgetError) as caught:
        await prepare_remaining(case, **{field: value})
    assert caught.value.code == "execution_new_book_grant_invalid"
    assert_safe_difference(caught.value, request)
    assert await case.count(m.ExecutionBudgetModel) == 0
    assert await case.count(m.ExecutionOperationModel) == 0
    assert await case.count(m.ExecutionAttemptModel) == 0


@pytest.mark.parametrize("limits", [(1, 1), (400, 20_000_000)])
async def test_exact_positive_limit_endpoints_are_supported(case, limits):
    grant = await prepare_remaining(case, max_calls=limits[0], max_tokens=limits[1])
    assert (grant["max_calls"], grant["max_tokens"]) == limits
    assert await case.consume(grant) == grant


@pytest.mark.parametrize("field,value", [
    ("max_calls", 400), ("max_tokens", 20_000_000),
    ("authorization_id", "client-created-authorization"),
])
async def test_client_post_cannot_change_grant_limits_or_authorization(case, field, value, request):
    grant = await prepare_remaining(case)
    before = await retained_rows(case, grant["scope_id"])
    with pytest.raises(ExecutionBudgetError) as caught:
        await case.consume(grant, payload={**case.payload, field: value})
    assert caught.value.code == "execution_new_book_payload_mismatch"
    assert_safe_difference(caught.value, request)
    assert await retained_rows(case, grant["scope_id"]) == before
    assert await case.count(m.ExecutionAttemptModel) == 0


async def test_client_limit_fields_in_exact_prepared_post_are_not_authority(case):
    body = {**case.payload, "max_calls": 400, "max_tokens": 20_000_000,
            "authorization_id": "client-spoof", "provider_authorization": {"mode": "completion_bound"}}
    grant = await prepare_remaining(case, payload=body)
    assert (grant["max_calls"], grant["max_tokens"]) == (CALLS, TOKENS)
    assert grant["authorization_id"] == case.authorization_id + ":run2"
    assert await case.consume(grant, payload=body) == grant
    async with case.sessions() as session:
        row = await session.get(m.ExecutionBudgetModel, grant["scope_id"])
        assert completion_authorization(row) is None


@pytest.mark.parametrize("fault", [
    "extra_binding_key", "missing_binding_key", "extra_record_key", "downgrade_schema",
    "row_calls", "row_tokens", "resealed_calls", "resealed_tokens", "bool_limit",
    "resealed_authorization",
])
async def test_v2_native_tampering_is_denied_even_with_resealed_record(case, fault, request):
    grant = await prepare_remaining(case)
    async with case.sessions.begin() as session:
        row = await session.get(m.ExecutionBudgetModel, grant["scope_id"])
        metadata = deepcopy(row.metadata_json)
        record, binding = metadata[KEY], metadata[KEY]["binding"]
        if fault == "extra_binding_key":
            binding["extra_authority"] = True
        elif fault == "missing_binding_key":
            del binding["authorization_id"]
        elif fault == "extra_record_key":
            record["extra_authority"] = True
        elif fault == "downgrade_schema":
            metadata["policy"] = record["schema"] = binding["schema"] = "new-book-budget.v1"
        elif fault == "row_calls":
            row.max_calls = 400
        elif fault == "row_tokens":
            row.max_tokens = 20_000_000
        elif fault == "resealed_calls":
            row.max_calls = binding["max_calls"] = 400
        elif fault == "resealed_tokens":
            row.max_tokens = binding["max_tokens"] = 20_000_000
        elif fault == "bool_limit":
            binding["max_calls"] = True
        elif fault == "resealed_authorization":
            record["authorization_id"] = binding["authorization_id"] = "replacement-authorization"
        record["sha256"] = digest({key: value for key, value in record.items() if key != "sha256"})
        row.metadata_json = metadata
    tampered = await retained_rows(case, grant["scope_id"])
    with pytest.raises(ExecutionBudgetError) as caught:
        await case.consume(grant)
    assert caught.value.code == "execution_new_book_binding_invalid"
    assert_safe_difference(caught.value, request)
    assert await retained_rows(case, grant["scope_id"]) == tampered
    assert await case.count(m.ExecutionAttemptModel) == 0
