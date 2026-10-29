"""Main-run RED: a granted task must never fall back to another project ledger.

Use the existing isolated SQLite fixture and real grant/ledger services. Only
the web manager's session dependency is redirected; no worker or wire starts.
"""

from contextlib import asynccontextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db import models as m
from bestseller.services.budget import ExecutionBudgetError
from bestseller.settings import load_settings
from bestseller.web import server

from .conftest import CALLS, KEY, TOKENS, diagnostic_summary

pytestmark = pytest.mark.unit

FIELD = "projects.metadata.execution_scope_id"
ERROR_CODE = "execution_new_book_binding_mismatch"


@pytest.fixture
async def handoff(case, monkeypatch):
    grant = await case.active()
    settings = load_settings(env={})
    monkeypatch.setattr(server, "load_settings", lambda: settings)

    @asynccontextmanager
    async def scope(_settings):
        async with case.sessions() as session:
            yield session

    monkeypatch.setattr(server, "session_scope", scope)
    slug = f"family-{grant['task_id']}"
    task = server.WebTaskState(
        task_id=grant["task_id"], task_type="autowrite", status="queued",
        created_at=grant["created_at"], updated_at=grant["created_at"],
        project_slug=slug, title="Offline scope handoff", current_stage="queued",
        payload={"slug": slug, "_run_conception": True, KEY: deepcopy(grant)},
    )
    manager = server.WebTaskManager()
    with manager._lock:
        manager._tasks[task.task_id] = task
    return SimpleNamespace(case=case, grant=grant, slug=slug, task=task, manager=manager)


async def add_project(handoff, metadata):
    project = m.ProjectModel(
        id=uuid4(), slug=handoff.slug, title="Offline scope handoff", genre="family",
        target_word_count=8000, target_chapters=4, project_type="fanqie_short",
        metadata_json=deepcopy(metadata),
    )
    async with handoff.case.sessions() as session:
        session.add(project)
        await session.commit()
    return project.id


async def assert_retained(handoff, before, project_id, metadata, *, calls=0):
    case, grant = handoff.case, handoff.grant
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1
    assert await case.count(m.ExecutionOperationModel) == calls
    assert await case.count(m.ExecutionAttemptModel) == calls
    async with case.sessions() as session:
        project = await session.get(m.ProjectModel, project_id)
        assert project.metadata_json == metadata
    assert handoff.task.payload[KEY] == grant


async def test_same_native_grant_survives_project_handoff_and_task_resume(handoff):
    case, grant = handoff.case, handoff.grant
    # A real settled local reservation proves handoff cannot reset consumption.
    await case.complete(case.ledger(grant), "scope-handoff-retained-result", tokens=37)
    before = await case.snapshot(grant["scope_id"])
    assert (before["max_calls"], before["max_tokens"]) == (CALLS, TOKENS)
    assert (before["calls_used"], before["tokens_charged"]) == (1, 37)
    assert before["deadline_at"] == grant["deadline_at"]
    task_id = grant["task_id"]
    assert await handoff.manager._task_execution_scope(task_id) == grant["scope_id"]

    metadata = {"execution_scope_id": grant["scope_id"], "unrelated": {"keep": True}}
    project_id = await add_project(handoff, metadata)
    assert await handoff.manager._task_execution_scope(task_id) == grant["scope_id"]

    # Rebuild the same task envelope, without dispatching or minting a new grant.
    resumed = server.WebTaskManager()
    with resumed._lock:
        resumed._tasks[task_id] = deepcopy(handoff.task)
        resumed._tasks[task_id].status = "failed"
    assert await resumed._task_execution_scope(task_id) == grant["scope_id"]
    assert await resumed._task_execution_scope(task_id) == grant["scope_id"]
    await assert_retained(handoff, before, project_id, metadata, calls=1)


@pytest.mark.parametrize("after_valid_lookup", [False, True], ids=["initial", "reloaded"])
@pytest.mark.parametrize("variant", [
    "missing", "null", "empty", "malformed", "bool", "project_fallback", "other_grant",
])
async def test_native_grant_rejects_missing_or_changed_project_scope(
    handoff, variant, after_valid_lookup, request,
):
    case, grant = handoff.case, handoff.grant
    metadata = {"execution_scope_id": grant["scope_id"], "unrelated": {"keep": True}}
    project_id = await add_project(handoff, metadata)
    if after_valid_lookup:
        assert await handoff.manager._task_execution_scope(grant["task_id"]) == grant["scope_id"]

    values = {
        "null": None, "empty": "", "malformed": "invalid-native-scope", "bool": False,
        "project_fallback": f"project:{project_id}", "other_grant": f"conception:{uuid4()}",
    }
    if variant == "missing":
        del metadata["execution_scope_id"]
    else:
        metadata["execution_scope_id"] = values[variant]
    # Commit through another real session, so a prior in-memory lookup is stale.
    async with case.sessions() as session:
        project = await session.get(m.ProjectModel, project_id)
        project.metadata_json = deepcopy(metadata)
        await session.commit()
    before = await case.snapshot(grant["scope_id"])
    difference = {
        "field": FIELD,
        "expected": diagnostic_summary(grant["scope_id"]),
        "actual": diagnostic_summary(metadata.get("execution_scope_id")),
    }
    # Record safe operands even when the genuine RED is DID NOT RAISE.
    request.node.user_properties.append(("expected_budget_error_code", ERROR_CODE))
    request.node.user_properties.append((
        "expected_budget_differences", json.dumps([difference], sort_keys=True),
    ))
    with pytest.raises(ExecutionBudgetError) as caught:
        await handoff.manager._task_execution_scope(grant["task_id"])
    error = caught.value
    differences = getattr(error, "differences", ())
    request.node.user_properties.append(("budget_error_code", error.code))
    request.node.user_properties.append((
        "budget_differences", json.dumps(differences, sort_keys=True),
    ))
    assert error.code == ERROR_CODE
    assert error.scope_id == grant["scope_id"]
    assert difference in differences
    await assert_retained(handoff, before, project_id, metadata)


async def test_legacy_task_without_grant_keeps_project_fallback(handoff):
    # An unrelated native grant must not change a legacy task's lookup contract.
    handoff.task.payload.pop(KEY)
    project_id = await add_project(handoff, {"unrelated": {"keep": True}})
    before = await handoff.case.snapshot(handoff.grant["scope_id"])
    assert await handoff.manager._task_execution_scope(handoff.task.task_id) == (
        f"project:{project_id}"
    )
    assert await handoff.case.snapshot(handoff.grant["scope_id"]) == before
    assert await handoff.case.count(m.ExecutionBudgetModel) == 1
    assert await handoff.case.count(m.ExecutionOperationModel) == 0
    assert await handoff.case.count(m.ExecutionAttemptModel) == 0


async def assert_task_binding_rejected(
    handoff, request, field, expected, actual, *, audit_only=False,
):
    case, grant = handoff.case, handoff.grant
    before = await case.snapshot(grant["scope_id"])
    task_before = deepcopy(handoff.task.to_dict())
    difference = {
        "field": field, "expected": diagnostic_summary(expected),
        "actual": diagnostic_summary(actual),
    }
    request.node.user_properties.append(("expected_budget_error_code", ERROR_CODE))
    request.node.user_properties.append((
        "expected_budget_differences", json.dumps([difference], sort_keys=True),
    ))
    with pytest.raises(ExecutionBudgetError) as caught:
        await handoff.manager._task_execution_scope(grant["task_id"], audit_only=audit_only)
    error = caught.value
    differences = getattr(error, "differences", ())
    request.node.user_properties.append(("budget_error_code", error.code))
    request.node.user_properties.append((
        "budget_differences", json.dumps(differences, sort_keys=True),
    ))
    assert error.code == ERROR_CODE
    assert error.scope_id == grant["scope_id"]
    assert difference in differences
    assert await case.snapshot(grant["scope_id"]) == before
    assert await case.count(m.ExecutionBudgetModel) == 1
    assert await case.count(m.ExecutionOperationModel) == 0
    assert await case.count(m.ExecutionAttemptModel) == 0
    assert handoff.task.to_dict() == task_before


@pytest.mark.parametrize("variant", [
    "null_binding", "missing_field", "extra_field", "grant_id", "task_id", "scope_id",
    "payload_sha256", "model", "endpoint", "max_calls", "max_tokens", "created_at",
    "deadline_at", "max_calls_float", "max_tokens_float",
])
async def test_forged_task_binding_cannot_replace_consumed_native_grant(handoff, variant, request):
    grant = handoff.grant
    forged = deepcopy(grant)
    field, expected = "task.new_book_budget_grant", grant
    if variant == "null_binding":
        forged = None
        expected = "native grant binding"
    elif variant == "missing_field":
        del forged["grant_id"]
        field, expected = "task.new_book_budget_grant.keys", sorted(grant)
    elif variant == "extra_field":
        forged["untrusted_extra"] = True
        field, expected = "task.new_book_budget_grant.keys", sorted(grant)
    else:
        replacements = {
            "grant_id": str(uuid4()), "task_id": str(uuid4()),
            "scope_id": f"conception:{uuid4()}", "payload_sha256": "0" * 64,
            "model": "fixture/unbound-model", "endpoint": "https://foreign.invalid/v1",
            "max_calls": CALLS + 1, "max_tokens": TOKENS + 1,
            "created_at": "2026-10-29T02:00:00+00:00",
            "deadline_at": "2026-10-31T01:00:00+00:00",
            "max_calls_float": float(CALLS), "max_tokens_float": float(TOKENS),
        }
        changed_field = variant.removesuffix("_float")
        forged[changed_field] = replacements[variant]
        field = f"task.new_book_budget_grant.{changed_field}"
        expected = grant[changed_field]
        request.node.user_properties.append((
            "forged_binding_operand", json.dumps({
                "field": f"task.new_book_budget_grant.{changed_field}",
                "expected": diagnostic_summary(grant[changed_field]),
                "actual": diagnostic_summary(forged[changed_field]),
            }, sort_keys=True),
        ))
        if variant in {"task_id", "scope_id"}:
            field = "task.grant_identity"
            expected = [grant["task_id"], grant["scope_id"]]
    handoff.task.payload[KEY] = forged
    if field == "task.grant_identity":
        actual = [forged["task_id"], forged["scope_id"]]
    elif field == "task.new_book_budget_grant.keys":
        actual = sorted(forged)
    elif field == "task.new_book_budget_grant":
        actual = forged
    else:
        actual = forged[changed_field]
    await assert_task_binding_rejected(handoff, request, field, expected, actual)


@pytest.mark.parametrize("variant", ["task_only", "payload_only", "both_foreign", "empty"])
async def test_foreign_project_slug_cannot_borrow_native_task_grant(handoff, variant, request):
    # This is an ordinary foreign Project row, without synthetic approval markers.
    foreign_slug = f"family-{uuid4()}"
    foreign_metadata = {"execution_scope_id": f"project:{uuid4()}"}
    foreign_id = await add_project(
        SimpleNamespace(case=handoff.case, slug=foreign_slug), foreign_metadata,
    )
    if variant in {"task_only", "both_foreign"}:
        handoff.task.project_slug = foreign_slug
    if variant in {"payload_only", "both_foreign"}:
        handoff.task.payload["slug"] = foreign_slug
    if variant == "empty":
        handoff.task.project_slug = ""
        handoff.task.payload["slug"] = ""
    await assert_task_binding_rejected(
        handoff, request, "task.project_slug", handoff.task.payload["slug"],
        handoff.task.project_slug,
    )
    async with handoff.case.sessions() as session:
        foreign = await session.get(m.ProjectModel, foreign_id)
        assert foreign.slug == foreign_slug
        assert foreign.metadata_json == foreign_metadata


@pytest.mark.parametrize("audit_only", [False, True], ids=["normal", "audit"])
@pytest.mark.parametrize("variant", ["legacy_continuation", "same_grant", "project", "malformed"])
async def test_active_continuation_cannot_bypass_native_grant_resolution(
    handoff, variant, audit_only, monkeypatch, request,
):
    from bestseller.services import conception_continuation

    values = {
        "legacy_continuation": f"conception-continuation:{uuid4()}",
        "same_grant": handoff.grant["scope_id"], "project": f"project:{uuid4()}",
        "malformed": "invalid-continuation-scope",
    }
    continuation = values[variant]
    handoff.task.payload["_active_continuation_scope"] = continuation
    delegated = []

    async def forbidden_legacy_resolution(*args, **kwargs):
        delegated.append(True)
        pytest.fail("finite new-book grant reached legacy continuation resolution")

    monkeypatch.setattr(conception_continuation, "active_scope", forbidden_legacy_resolution)
    await assert_task_binding_rejected(
        handoff, request, "task._active_continuation_scope", None, continuation,
        audit_only=audit_only,
    )
    assert delegated == []
