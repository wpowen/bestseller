"""Independent real SQLite grant/cache boundaries; no provider or network.

The shared fixture creates the CP, paid partial and quarantine through native
ledger APIs. Its synthetic input hash deliberately is not a real gateway hash.
Ledger tests exercise reservation; the gateway test proves that distinction
cannot be used to send. No synthetic return is a literary grade.
"""
from copy import deepcopy
import socket

import httpx
import pytest
from sqlalchemy import func, select

from test_conception_logline_recovery import (  # noqa: F401
    authorize, case, recovered, running_owner,
)
from bestseller.infra.db import models as m
from bestseller.services import conception_logline_recovery as recovery, llm
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger, bind_execution_scope

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def forbid_provider_and_network(monkeypatch):
    attempted = []

    def forbidden(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Independent replacement tests must not contact a provider")

    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    monkeypatch.setattr(llm, "_get_litellm", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert attempted == [], "A swallowed exception must not conceal a provider call"


async def prepared(c):
    plan = await authorize(c)
    wid = await running_owner(c)
    with bind_execution_scope(c.scope), llm.bind_conception_model(
        "minimax-m3.1-flash", task_id=c.task_id, workflow_run_id=wid,
    ):
        async with c.factory() as session:
            settings, request = await recovery.prepare_logline_request(session, c.settings, c.request)
    return plan, wid, settings, request


async def native_counts(c):
    async with c.factory() as session:
        budget = await session.get(m.ExecutionBudgetModel, c.scope)
        attempts = await session.scalar(select(func.count()).select_from(m.ExecutionAttemptModel))
        return budget.calls_used, budget.tokens_charged, attempts


async def reserve(c, ledger, claim, wid, request, **changes):
    kwargs = dict(endpoint=recovery.ENDPOINT, workflow_id=str(wid), template=request.prompt_template,
                  version=request.prompt_version,
                  single_dispatch_id=request.metadata["single_dispatch_authorization_id"])
    kwargs.update(changes)
    return await ledger.reserve(claim, recovery.MODEL, c.input_hash, 400,
                                **kwargs)


async def test_one_native_attempt_then_cached_replacement_preserves_old_charged_root(recovered):
    c = recovered
    before = await native_counts(c)
    plan, wid, settings, request = await prepared(c)
    assert len(plan["prompt_version"]) <= m.LlmRunModel.__table__.c.prompt_version.type.length == 32
    assert request.prompt_version == plan["prompt_version"]
    assert plan["original_operation_key"] == c.claim.key
    claim = await c.ledger.claim(request.operation_id, c.input_hash)
    assert claim.key == plan["replacement_operation_key"]
    aid = await reserve(c, c.ledger, claim, wid, request)
    result = llm.LLMCompletionResult(content="SYNTHETIC transport result; not a literary grade",
        provider="openai", model_name=recovery.MODEL, input_tokens=10, output_tokens=5,
        finish_reason="stop", metadata={"synthetic_fixture": True}).model_dump(mode="json")
    assert await c.ledger.settle(aid, "completed", actual_tokens=15, claim=claim, result=result) is None
    resumed = ExecutionLedger(c.engine, c.scope, settings.budget)
    cached = await resumed.claim(request.operation_id, c.input_hash)
    await resumed.assert_result_adoptable(cached, cached=True)
    assert cached.cached_result == result
    assert await native_counts(c) == (before[0] + 1, before[1] + 15, before[2] + 1)
    # A completed replacement is not a perpetual ban on later genuine edits.
    # This only returns the new request to normal gates; it grants no paid call.
    changed = c.request.model_copy(update={"user_prompt": c.request.user_prompt + "\nGENUINE FOLLOWUP REVISION"})
    with bind_execution_scope(c.scope), llm.bind_conception_model(
        "minimax-m3.1-flash", task_id=c.task_id, workflow_run_id=wid,
    ):
        async with c.factory() as session:
            followup_settings, followup = await recovery.prepare_logline_request(session, c.settings, changed)
    assert followup_settings is c.settings and followup is changed
    assert followup.operation_id is None
    assert "single_dispatch_authorization_id" not in followup.metadata
    assert await native_counts(c) == (before[0] + 1, before[1] + 15, before[2] + 1)
    with pytest.raises(ExecutionBudgetError, match="single_dispatch|execution_operation_stopped"):
        await reserve(c, resumed, cached, wid, request)
    async with c.factory() as session:
        budget = await session.get(m.ExecutionBudgetModel, c.scope)
        grants = budget.metadata_json["single_dispatch_grants"]
        assert len(grants) == 1
        grant = grants[request.metadata["single_dispatch_authorization_id"]]
        assert (grant["physical_attempt_cap"], grant["status"], grant["used_attempt_id"]) == (1, "spent", aid)
        old = await session.get(m.ExecutionOperationModel, c.claim.key)
        old_attempt = await session.get(m.ExecutionAttemptModel, c.aid)
        assert (old.status, old.result_json) == ("stopped", None)
        assert (old_attempt.status, old_attempt.reserved_tokens, old_attempt.actual_tokens) == (
            "reconciled_charged", 47934, None)
    assert await native_counts(c) == (before[0] + 1, before[1] + 15, before[2] + 1)


@pytest.mark.parametrize("fault", ["missing_grant", "foreign_operation", "foreign_input"])
async def test_native_reservation_cannot_drop_or_rebind_grant(recovered, fault):
    c = recovered
    _, wid, _, request = await prepared(c)
    before = await native_counts(c)
    claim = await c.ledger.claim(request.operation_id, c.input_hash)
    changes = {}
    if fault == "missing_grant":
        changes["single_dispatch_id"] = None
    elif fault == "foreign_operation":
        claim = await c.ledger.claim("unapproved-replacement-operation", c.input_hash)
    else:
        # Preserve the authorization but present a different effective request.
        with pytest.raises(ExecutionBudgetError, match="single_dispatch"):
            await c.ledger.reserve(claim, recovery.MODEL, "f" * 64, 400,
                endpoint=recovery.ENDPOINT, workflow_id=str(wid), template=request.prompt_template,
                version=request.prompt_version,
                single_dispatch_id=request.metadata["single_dispatch_authorization_id"])
        assert await native_counts(c) == before
        return
    with pytest.raises(ExecutionBudgetError, match="single_dispatch"):
        await reserve(c, c.ledger, claim, wid, request, **changes)
    assert await native_counts(c) == before


async def test_complete_text_checks_real_effective_hash_before_any_claim_or_send(recovered):
    c = recovered
    _, wid, settings, request = await prepared(c)
    before = await native_counts(c)
    async with c.factory() as session:
        keys_before = set(await session.scalars(select(m.ExecutionOperationModel.key)))
    with pytest.raises(ExecutionBudgetError, match="execution_prepared_input_changed"):
        with bind_execution_scope(c.scope), llm.bind_conception_model(
            "minimax-m3.1-flash", task_id=c.task_id, workflow_run_id=wid,
        ):
            async with c.factory() as session:
                await llm.complete_text(session, settings, request)
    assert await native_counts(c) == before
    async with c.factory() as session:
        assert set(await session.scalars(select(m.ExecutionOperationModel.key))) == keys_before


async def test_changed_current_logline_cannot_fall_through_to_ungranted_request(recovered):
    c = recovered
    await authorize(c)
    wid = await running_owner(c)
    changed = c.request.model_copy(update={"user_prompt": c.request.user_prompt + "\nFOREIGN CURRENT INPUT"})
    before = await native_counts(c)
    with bind_execution_scope(c.scope), llm.bind_conception_model(
        "minimax-m3.1-flash", task_id=c.task_id, workflow_run_id=wid,
    ):
        async with c.factory() as session:
            with pytest.raises(ExecutionBudgetError, match="execution_logline_replacement_denied"):
                await recovery.prepare_logline_request(session, c.settings, changed)
    assert await native_counts(c) == before


async def test_fake_completed_root_and_spent_flag_without_native_attempt_deny_followup(recovered):
    c = recovered
    plan, wid, _, request = await prepared(c)
    async with c.factory() as session, session.begin():
        budget = await session.get(m.ExecutionBudgetModel, c.scope)
        grants = deepcopy(budget.metadata_json["single_dispatch_grants"])
        grant = grants[request.metadata["single_dispatch_authorization_id"]]
        grant.update(status="spent", used_attempt_id="nonexistent-native-attempt")
        budget.metadata_json = {**budget.metadata_json, "single_dispatch_grants": grants}
        session.add(m.ExecutionOperationModel(key=plan["replacement_operation_key"], scope_id=c.scope,
            input_hash=c.input_hash, status="completed", owner="SYNTHETIC corrupt root fixture",
            lease_until=budget.deadline_at, result_json={"provider": "openai", "model_name": recovery.MODEL,
                "content": "SYNTHETIC forged completion; not a provider return"}))
    before = await native_counts(c)
    changed = c.request.model_copy(update={"user_prompt": c.request.user_prompt + "\nUNPROVEN FOLLOWUP"})
    with bind_execution_scope(c.scope), llm.bind_conception_model(
        "minimax-m3.1-flash", task_id=c.task_id, workflow_run_id=wid,
    ):
        async with c.factory() as session:
            assert await session.get(m.ExecutionAttemptModel, "nonexistent-native-attempt") is None
            with pytest.raises(ExecutionBudgetError, match="execution_logline_replacement_denied"):
                await recovery.prepare_logline_request(session, c.settings, changed)
    assert await native_counts(c) == before
