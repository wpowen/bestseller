"""Real SQLite continuation binding: preserve ledger stops and audit guards."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json

import pytest
from sqlalchemy import select, text
from test_creation_configuration_slice import store as store
from test_execution_phase_handoff import ready

from bestseller.infra.db import models as m
from bestseller.services import execution_phase_handoff as handoff
from bestseller.services import independent_acceptance as acceptance
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.conception_continuation import active_scope

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _bound(store, tmp_path):
    assert store.path.name.endswith("_test.sqlite3")
    async with store.engine.connect() as connection:
        databases = (await connection.execute(text("PRAGMA database_list"))).all()
        assert next(row[2] for row in databases if row[1] == "main") == str(store.path)
    case = await ready(store, tmp_path)
    async with store.sessions() as session:
        receipt = await handoff.create(session, task_id=case.task, payload=case.payload)
        await session.commit()
    case.payload["_execution_phase_handoff"] = receipt["receipt_key"]
    assert await active_scope(store.engine, case.task, case.payload) == case.scope
    return case


async def _state(store):
    """Inspect persisted grants, holds, paid receipts and source identities."""
    state = {}
    async with store.sessions() as session:
        for model in (
            m.ExecutionBudgetModel,
            m.ExecutionOperationModel,
            m.ExecutionAttemptModel,
            m.ProjectModel,
            m.PlanningArtifactVersionModel,
            m.LlmRunModel,
            m.WorkflowRunModel,
        ):
            rows = (await session.execute(select(model.__table__))).mappings().all()
            state[model.__tablename__] = sorted(
                json.dumps(dict(row), sort_keys=True, ensure_ascii=False, default=str)
                for row in rows
            )
    return state


async def _stop(store, case, status, *, authorization="active", expired=False):
    async with store.sessions() as session:
        budget = await session.get(m.ExecutionBudgetModel, case.scope)
        budget.status = status
        if authorization is None:
            budget.metadata_json = {
                key: value
                for key, value in budget.metadata_json.items()
                if key != "provider_authorization"
            }
        else:
            budget.metadata_json = {
                **budget.metadata_json,
                "provider_authorization": {
                    **budget.metadata_json["provider_authorization"],
                    "status": authorization,
                },
            }
        if expired:
            budget.deadline_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()


@pytest.mark.parametrize(
    "status,authorization,expired,code",
    [
        ("unknown", "active", False, "execution_result_unknown"),
        ("cancelled", "active", False, "execution_cancelled"),
        ("awaiting_submission", "active", False, "execution_submission_not_authorized"),
        ("active", "paused", False, "execution_authorization_paused"),
        ("active", "closed", False, "execution_authorization_closed"),
        ("exhausted", None, False, "execution_budget_exhausted"),
        ("active", None, True, "execution_deadline_exhausted"),
    ],
)
async def test_real_active_scope_preserves_exact_budget_stop_without_writes(
    store,
    tmp_path,
    status,
    authorization,
    expired,
    code,
):
    case = await _bound(store, tmp_path)
    await _stop(store, case, status, authorization=authorization, expired=expired)
    before = await _state(store)
    with pytest.raises(ExecutionBudgetError) as raised:
        await active_scope(store.engine, case.task, case.payload)
    assert raised.value.code == code
    assert raised.value.scope_id == case.scope
    assert raised.value.__cause__ is None  # Not remapped from a handoff ValueError.
    assert await _state(store) == before
    assert store.calls == []


async def test_completion_bound_exhausted_legacy_counters_do_not_create_a_cap(store, tmp_path):
    case = await _bound(store, tmp_path)
    await _stop(store, case, "exhausted", expired=True)
    async with store.sessions() as session:
        budget = await session.get(m.ExecutionBudgetModel, case.scope)
        budget.calls_used = budget.max_calls + 1
        budget.tokens_charged = budget.max_tokens + 1
        await session.commit()
    before = await _state(store)
    assert await active_scope(store.engine, case.task, case.payload) == case.scope
    assert await _state(store) == before


async def test_unknown_audit_validates_retained_binding_without_reopening_or_writes(
    store, tmp_path
):
    case = await _bound(store, tmp_path)
    await _stop(store, case, "unknown")
    async with store.sessions() as session:
        project = await session.get(m.ProjectModel, store.pid)
        project.status = "paused"
        project.metadata_json = {
            **project.metadata_json,
            "execution_terminal": {"terminal": True, "code": "execution_result_unknown"},
            "production_paused": True,
            "production_pause_reason": "execution_result_unknown",
        }
        await session.commit()
    before = await _state(store)
    assert await active_scope(store.engine, case.task, case.payload, audit_only=True) == case.scope
    with pytest.raises(ExecutionBudgetError, match=r"^execution_result_unknown$"):
        await active_scope(store.engine, case.task, case.payload)
    assert await _state(store) == before


@pytest.mark.parametrize("audit_only", [False, True])
@pytest.mark.parametrize(
    "fault",
    ["author", "canonical", "intent", "quality", "receipt", "projection", "task", "grant"],
)
async def test_actual_binding_guards_still_fail_closed_and_audit_is_read_only(
    store,
    tmp_path,
    audit_only,
    fault,
):
    case = await _bound(store, tmp_path)
    if audit_only:
        await _stop(store, case, "unknown")
    if fault == "author":
        case.payload["concept_seed"] = "different author intent"
    async with store.sessions() as session:
        project = await session.get(m.ProjectModel, store.pid)
        if fault == "canonical":
            snapshot = await session.get(m.PlanningArtifactVersionModel, case.sid)
            snapshot.content = {**snapshot.content, "premise": "changed reviewed source"}
        elif fault == "intent":
            project.metadata_json = {
                **project.metadata_json,
                "creation_intent_contract_hash": "changed",
            }
        elif fault == "quality":
            attempt = await session.get(m.ExecutionAttemptModel, "fixture-attempt")
            attempt.actual_tokens = None
        elif fault == "receipt":
            operation = await session.get(
                m.ExecutionOperationModel,
                case.payload["_execution_phase_handoff"],
            )
            operation.result_json = {**operation.result_json, "binding": {}}
        elif fault == "projection":
            project.metadata_json = {
                **project.metadata_json,
                "execution_phase_handoff_receipts": [],
            }
        elif fault in {"task", "grant"}:
            budget = await session.get(m.ExecutionBudgetModel, case.scope)
            grant = deepcopy(budget.metadata_json[acceptance.KEY])
            if fault == "task":
                grant["request"]["continuation_task_id"] = "another-task"
                grant["request_sha256"] = acceptance.digest(grant["request"])
            else:
                grant["request_sha256"] = "forged"
            budget.metadata_json = {**budget.metadata_json, acceptance.KEY: grant}
        await session.commit()
    before = await _state(store)
    with pytest.raises(ExecutionBudgetError) as raised:
        await active_scope(store.engine, case.task, case.payload, audit_only=audit_only)
    assert raised.value.code == (
        "execution_acceptance_binding_invalid"
        if fault == "grant"
        else "execution_continuation_binding_invalid"
    )
    assert await _state(store) == before
    assert store.calls == []


@pytest.mark.parametrize("authorization", [None, "paused", "closed"])
async def test_audit_does_not_bypass_authorization_guard(store, tmp_path, authorization):
    case = await _bound(store, tmp_path)
    await _stop(store, case, "unknown", authorization=authorization)
    before = await _state(store)
    with pytest.raises(ExecutionBudgetError, match=r"^execution_continuation_binding_invalid$"):
        await active_scope(store.engine, case.task, case.payload, audit_only=True)
    assert await _state(store) == before
