"""Exact normal-POST authorization and once-only durable consumption contract."""

from copy import deepcopy
from datetime import datetime, timedelta
import json
from uuid import UUID, uuid4

import pytest

from bestseller.infra.db import models as m
from bestseller.services.budget import ExecutionBudgetError, completion_authorization

from .conftest import (
    CALLS,
    ENDPOINT,
    KEY,
    MODEL,
    SELECTOR,
    TOKENS,
    diagnostic_summary,
    payload_digest,
)

pytestmark = pytest.mark.unit


def assert_difference(error, field, request):
    differences = getattr(error, "differences", ())
    request.node.user_properties.append(("budget_error_code", error.code))
    request.node.user_properties.append((
        "budget_differences", json.dumps(differences, sort_keys=True),
    ))
    request.node.user_properties.append((
        "removed_record_property_compatibility_differences",
        json.dumps([{
            "field": "pytest.junit_family",
            "expected": diagnostic_summary(["legacy", "xunit1"]),
            "actual": diagnostic_summary(request.config.getini("junit_family")),
        }], sort_keys=True),
    ))
    matches = [item for item in differences if item.get("field") == field]
    assert matches, (field, differences)
    for side in ("expected", "actual"):
        summary = matches[0][side]
        assert set(summary) == {"type", "length", "sha256"}
        assert isinstance(summary["type"], str) and summary["type"]
        assert summary["length"] is None or type(summary["length"]) is int
        assert len(summary["sha256"]) == 64
        int(summary["sha256"], 16)
    return matches[0]


async def test_prepare_is_native_finite_and_does_not_send_or_authorize_before_post(case, clock):
    original = deepcopy(case.payload)
    grant = await case.prepare()
    assert set(grant) == {
        "grant_id", "task_id", "scope_id", "payload_sha256", "model", "endpoint",
        "max_calls", "max_tokens", "created_at", "deadline_at",
    }
    assert str(UUID(grant["grant_id"])) == grant["grant_id"]
    assert str(UUID(grant["task_id"])) == grant["task_id"]
    assert grant["scope_id"] == f"conception:{grant['task_id']}"
    assert grant["payload_sha256"] == payload_digest(original)
    assert (grant["max_calls"], grant["max_tokens"]) == (CALLS, TOKENS)
    assert (grant["model"], grant["endpoint"]) == (MODEL, ENDPOINT)
    assert datetime.fromisoformat(grant["created_at"]) == clock.instant
    assert datetime.fromisoformat(grant["deadline_at"]) == clock.instant + timedelta(hours=24)
    row = await case.snapshot(grant["scope_id"])
    assert row["status"] == "awaiting_submission"
    assert (row["calls_used"], row["tokens_charged"]) == (0, 0)
    assert KEY in row["metadata"]
    assert "provider_authorization" not in row["metadata"]
    assert "independent_acceptance" not in row["metadata"]
    assert await case.count(m.ExecutionBudgetModel) == 1
    assert await case.count(m.ExecutionOperationModel) == 0
    assert await case.count(m.ExecutionAttemptModel) == 0
    with pytest.raises(ExecutionBudgetError, match="execution_submission_not_authorized"):
        await case.ledger(grant).claim("before-post", "input")
    assert await case.snapshot(grant["scope_id"]) == row
    assert case.payload == original


async def test_prepare_replay_and_delayed_consume_never_slide_creation_deadline(case, clock):
    grant = await case.prepare()
    before = await case.snapshot(grant["scope_id"])
    clock.advance(hours=3)
    assert await case.prepare() == grant
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.consume(grant) == grant
    consumed = await case.snapshot(grant["scope_id"])
    assert consumed["status"] == "active"
    assert consumed["deadline_at"] == grant["deadline_at"]
    clock.advance(hours=2)
    assert await case.prepare() == grant
    assert await case.consume(grant) == grant
    assert await case.snapshot(grant["scope_id"]) == consumed


async def test_consume_crash_before_task_persistence_reuses_exact_id_after_new_engine(case):
    from sqlalchemy.ext.asyncio import create_async_engine

    grant = await case.active()
    before = await case.snapshot(grant["scope_id"])
    # No WebTask exists yet: simulate a process loss after the native commit.
    restarted = create_async_engine(case.engine.url)
    try:
        rebound = await case.api.consume_new_book_budget(
            restarted, payload=case.post(grant), model=MODEL, endpoint=ENDPOINT,
        )
    finally:
        await restarted.dispose()
    assert rebound == grant
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1
    assert await case.count(m.ExecutionOperationModel) == 0


async def test_concurrent_same_post_consumes_once_and_returns_same_binding(case):
    import asyncio

    grant = await case.prepare()
    left, right = await asyncio.gather(case.consume(grant), case.consume(grant))
    assert left == right == grant
    first = await case.snapshot(grant["scope_id"])
    assert await case.consume(grant) == grant
    assert await case.snapshot(grant["scope_id"]) == first
    assert await case.count(m.ExecutionBudgetModel) == 1


async def test_missing_locator_preserves_legacy_without_creating_or_consuming_rows(case):
    grant = await case.prepare()
    before = await case.snapshot(grant["scope_id"])
    assert await case.api.consume_new_book_budget(
        case.engine, payload=case.payload, model=MODEL, endpoint=ENDPOINT,
    ) is None
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1


async def test_client_approval_and_unlimited_fields_cannot_override_operator_fixed_caps(case):
    payload = {
        **case.payload, "approval": True, "max_calls": None, "max_tokens": 0,
        "deadline_seconds": None, "provider_authorization": {"mode": "completion_bound"},
    }
    grant = await case.prepare(payload=payload)
    assert (grant["max_calls"], grant["max_tokens"]) == (CALLS, TOKENS)
    assert await case.consume(grant, payload=payload) == grant
    async with case.sessions() as session:
        row = await session.get(m.ExecutionBudgetModel, grant["scope_id"])
        assert completion_authorization(row) is None


@pytest.mark.parametrize("locator", [None, True, 400, "", "not-a-uuid", str(uuid4())])
async def test_locator_is_never_authority_or_permission_to_fallback(case, locator, request):
    grant = await case.prepare()
    before = await case.snapshot(grant["scope_id"])
    with pytest.raises(ExecutionBudgetError) as raised:
        await case.api.consume_new_book_budget(
            case.engine, payload={**case.payload, SELECTOR: locator},
            model=MODEL, endpoint=ENDPOINT,
        )
    assert raised.value.code == "execution_new_book_grant_invalid"
    difference = assert_difference(raised.value, f"request.{SELECTOR}", request)
    assert difference["actual"] == diagnostic_summary(locator)
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1


@pytest.mark.parametrize("field,value", [
    ("concept_seed", "另一份完整创作请求"),
    ("selection", {"channel": "male", "genre": "family", "tags": ["reunion"]}),
    ("story_enhancers", {"effect_skills": ["different_skill"]}),
    ("draft_mode", True),
    ("autonomous_book", False),
    ("stop_after_conception", True),
    ("chapter_count", 4.0),
    ("llm_model_id", "another-model"),
    ("unknown_future_control", {"value": "must-also-bind"}),
    ("client_intent_manifest", {"schema_version": "changed"}),
])
async def test_every_post_field_and_json_type_binds_before_consume(
    case, field, value, request,
):
    grant = await case.prepare()
    before = await case.snapshot(grant["scope_id"])
    changed = {**deepcopy(case.payload), field: value}
    with pytest.raises(ExecutionBudgetError) as raised:
        await case.consume(grant, payload=changed)
    assert raised.value.code == "execution_new_book_payload_mismatch"
    difference = assert_difference(raised.value, "request.payload", request)
    assert difference["expected"] == diagnostic_summary(case.payload)
    assert difference["actual"] == diagnostic_summary(changed)
    diagnostic = json.dumps(raised.value.differences, ensure_ascii=False)
    assert case.payload["concept_seed"] not in diagnostic
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionOperationModel) == 0


async def test_json_key_order_is_not_a_new_payload_but_nested_locator_is_bound(case):
    grant = await case.prepare()
    reordered = dict(reversed(list(case.payload.items())))
    assert await case.consume(grant, payload=reordered) == grant
    nested = deepcopy(case.payload)
    nested["client_intent_manifest"][SELECTOR] = grant["grant_id"]
    with pytest.raises(ExecutionBudgetError, match="execution_new_book_payload_mismatch"):
        await case.consume(grant, payload=nested)


@pytest.mark.parametrize("field,value", [
    ("model", "openai/other-model"),
    ("endpoint", "https://other-provider.invalid/v1"),
])
async def test_consume_binds_server_resolved_route(case, field, value, request):
    grant = await case.prepare()
    before = await case.snapshot(grant["scope_id"])
    with pytest.raises(ExecutionBudgetError) as raised:
        await case.consume(grant, **{field: value})
    assert raised.value.code == "execution_new_book_route_mismatch"
    difference = assert_difference(raised.value, f"request.{field}", request)
    expected = {"model": MODEL, "endpoint": ENDPOINT}[field]
    assert difference["expected"] == diagnostic_summary(expected)
    assert difference["actual"] == diagnostic_summary(value)
    assert await case.snapshot(grant["scope_id"]) == before


@pytest.mark.parametrize("field,value", [
    ("actor", "another-operator"),
    ("authorization_ref", "different-user-authorization"),
    ("model", "openai/other-model"),
    ("endpoint", "https://other-provider.invalid/v1"),
])
async def test_prepare_idempotence_cannot_rebind_existing_grant(case, field, value):
    grant = await case.prepare()
    before = await case.snapshot(grant["scope_id"])
    with pytest.raises(ExecutionBudgetError, match="execution_new_book_binding_mismatch"):
        await case.prepare(**{field: value})
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1


async def test_changed_payload_cannot_reuse_authorization_id(case):
    grant = await case.prepare()
    before = await case.snapshot(grant["scope_id"])
    with pytest.raises(ExecutionBudgetError, match="execution_new_book_binding_mismatch"):
        await case.prepare(payload={**case.payload, "concept_seed": "changed"})
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1


async def test_second_authorization_id_cannot_regrant_identical_creation(case):
    grant = await case.active()
    await case.complete(case.ledger(grant), "already-used")
    before = await case.snapshot(grant["scope_id"])
    with pytest.raises(ExecutionBudgetError, match="execution_new_book_binding_mismatch"):
        await case.prepare(authorization_id=str(uuid4()))
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1


@pytest.mark.parametrize("field,value", [
    ("project_slug", "existing-book"),
    ("project_id", str(uuid4())),
    ("_active_continuation_scope", "conception:existing"),
    ("execution_scope_id", "conception:existing"),
    (SELECTOR, str(uuid4())),
])
async def test_prepare_refuses_existing_book_scope_or_client_grant_injection(case, field, value):
    with pytest.raises(ExecutionBudgetError, match="execution_new_book_payload_invalid"):
        await case.prepare(payload={**case.payload, field: value})
    assert await case.count(m.ExecutionBudgetModel) == 0


@pytest.mark.parametrize("field", ["authorization_id", "actor", "authorization_ref"])
async def test_prepare_requires_real_operator_binding_not_approval_flag(case, field):
    with pytest.raises(ExecutionBudgetError, match="execution_new_book_grant_invalid"):
        await case.prepare(**{field: ""})
    assert await case.count(m.ExecutionBudgetModel) == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
async def test_non_json_payload_is_rejected_without_stringifying_into_authority(case, value):
    with pytest.raises(ExecutionBudgetError, match="execution_new_book_payload_invalid"):
        await case.prepare(payload={**case.payload, "extra": value})
    assert await case.count(m.ExecutionBudgetModel) == 0


@pytest.mark.parametrize("consumed", [False, True])
async def test_exact_24h_deadline_blocks_without_renewal_or_new_scope(case, clock, consumed):
    grant = await case.active() if consumed else await case.prepare()
    before = await case.snapshot(grant["scope_id"])
    clock.advance(hours=24)
    with pytest.raises(ExecutionBudgetError, match="execution_deadline_exhausted"):
        await case.consume(grant)
    assert await case.snapshot(grant["scope_id"]) == before
    # Re-reading the original prepared binding cannot turn it into a new window.
    assert await case.prepare() == grant
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1


async def test_grant_metadata_contains_no_raw_post_or_completion_authorization(case):
    grant = await case.active()
    row = await case.snapshot(grant["scope_id"])
    serialized = json.dumps(row["metadata"], ensure_ascii=False)
    assert case.payload["concept_seed"] not in serialized
    assert grant["payload_sha256"] in serialized
    async with case.sessions() as session:
        native = await session.get(m.ExecutionBudgetModel, grant["scope_id"])
        assert completion_authorization(native) is None
