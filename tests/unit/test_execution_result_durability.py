"""Paid response durability across process loss and competing dispatchers."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db.models import (
    ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel,
)
from bestseller.services import budget, llm
from bestseller.settings import BudgetSettings
from test_execution_budget_core import GatewaySession, gateway_request, gateway_settings

pytestmark = pytest.mark.unit


@pytest.fixture
async def result_db(tmp_path):
    path = tmp_path / "execution_result_durability_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
            await connection.run_sync(model.__table__.create)
    yield engine
    await engine.dispose()


def ledger(engine, scope="audit:paid-result"):
    return budget.ExecutionLedger(engine, scope, BudgetSettings(
        execution_max_calls=4, execution_max_tokens=100_000, execution_deadline_seconds=60,
    ))


def response(text="paid body"):
    tools = [{"id": "tool-1", "type": "function", "function": {
        "name": "inspect", "arguments": "{}"}}]
    return llm.LLMCompletionResult(
        content=text, provider="openai", model_name="openai/synthetic",
        input_tokens=3, output_tokens=4, finish_reason="stop",
        tool_calls=tools,
        raw_message=llm._build_raw_assistant_message(text, tools),
    ).model_dump(mode="json")


def evidence(attempt, **changes):
    return budget.ExecutionReconciliation.model_validate({
        "reconciliation_id": "manual-close", "attempt_id": attempt,
        "expected_input_hash": "h", "decision": "abandon_charged",
        "evidence_kind": "provider_receipt", "evidence_sha256": "a" * 64,
        "actor": "test-operator", "remote_operation_closed": True, **changes,
    })


async def rows(engine, scope, key, attempt):
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        return (await session.get(ExecutionBudgetModel, scope),
                await session.get(ExecutionOperationModel, key),
                await session.get(ExecutionAttemptModel, attempt))


async def expire_operation_lease(engine, key):
    # A new epoch is not death evidence. These abandoned/late-result fixtures
    # explicitly remove the old dispatcher's valid lease before recovery.
    async with async_sessionmaker(engine)() as session, session.begin():
        operation = await session.get(ExecutionOperationModel, key)
        assert operation is not None
        operation.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)


@pytest.mark.asyncio
async def test_dead_dispatcher_quarantine_retains_full_charge_and_refuses_replay(result_db):
    first = ledger(result_db)
    claim = await first.claim("uncertain", "h")
    attempt = await first.reserve(claim, "openai/synthetic", "h", 1200)
    await first.settle(attempt, "unknown")
    proof = evidence(attempt, decision="quarantine_charged", evidence_kind="dispatcher_exit",
        remote_operation_closed=False, local_dispatcher_stopped=True)
    receipt = await first.reconcile(proof)
    assert receipt["status"] == "active" and receipt["remote_outcome"] == "unknown_quarantined"
    assert receipt["credit_released"] is False and receipt["automatic_resume"] is False
    assert receipt["operation_replay_allowed"] is False
    assert receipt == await first.reconcile(proof)
    row, op, paid = await rows(result_db, first.scope_id, claim.key, attempt)
    assert row.calls_used == 1 and row.tokens_charged == 1200
    assert op.status == "stopped" and paid.status == "reconciled_charged"
    with pytest.raises(budget.ExecutionBudgetError, match="execution_operation_stopped"):
        await first.claim("uncertain", "h")
    # A late real return stays archived and cannot auto-adopt or refund it.
    assert await first.settle(attempt, "completed", actual_tokens=7,
        claim=claim, result=response()) == "execution_operation_stopped"
    row, op, paid = await rows(result_db, first.scope_id, claim.key, attempt)
    assert row.tokens_charged == 1200 and paid.status == "reconciled_charged"
    assert op.status == "stopped"


@pytest.mark.parametrize("change", [
    {"local_dispatcher_stopped": False}, {"remote_operation_closed": True},
    {"evidence_kind": "provider_receipt"}, {"result": response()},
])
def test_quarantine_cannot_claim_remote_closure_or_adopt_a_response(change):
    with pytest.raises(ValueError):
        evidence("attempt", **{
            "decision": "quarantine_charged", "evidence_kind": "dispatcher_exit",
            "remote_operation_closed": False, "local_dispatcher_stopped": True, **change,
        })


@pytest.mark.asyncio
async def test_exact_busy_operation_does_not_poison_the_shared_dispatcher(result_db):
    with budget.bind_execution_scope("audit:busy"):
        first = ledger(result_db, "audit:busy")
        await first.claim("same", "h")
        with pytest.raises(budget.ExecutionOperationBusyError) as caught:
            await first.claim("same", "h")
        assert not caught.value.terminal
        budget.record_execution_stop(caught.value)
        budget.raise_if_execution_stopped()
        row = await first.claim("independent", "different")
        assert row.cached_result is None


@pytest.mark.asyncio
async def test_historical_completed_orphan_blocks_other_operation_and_allows_reconciliation(result_db):
    old = ledger(result_db)
    claim = await old.claim("old", "h")
    attempt = await old.reserve(claim, "openai/synthetic", "h", 100)
    await old.settle(attempt, "completed", actual_tokens=7)
    # Historical format: charged response, no durable content, owner has exited.
    restored = ledger(result_db)
    with pytest.raises(budget.ExecutionBudgetError, match="execution_result_unknown"):
        await restored.claim("different-operation", "different-input")
    inventory = await restored.reconciliation_status()
    assert inventory["attempts"][0]["attempt_id"] == attempt
    await restored.reconcile(evidence(attempt, decision="recover_completed", result=response()))
    cached = await restored.claim("old", "h")
    assert cached.cached_result["content"] == "paid body"
    row, _, _ = await rows(result_db, old.scope_id, claim.key, attempt)
    assert (row.calls_used, row.tokens_charged) == (1, 7)


@pytest.mark.asyncio
async def test_success_is_replayable_before_upper_formatting_or_finish(result_db):
    owner = ledger(result_db)
    claim = await owner.claim("one", "h")
    attempt = await owner.reserve(claim, "openai/synthetic", "h", 100)
    await owner.settle(attempt, "completed", actual_tokens=7, claim=claim, result=response())
    restored = ledger(result_db)
    assert (await restored.claim("one", "h")).cached_result == response()
    raw = await restored.transport_result(attempt)
    assert raw["result"] == response() and raw["operation_key"] == claim.key
    row, op, paid = await rows(result_db, owner.scope_id, claim.key, attempt)
    assert (row.calls_used, row.tokens_charged, op.status, paid.status) == (1, 7, "completed", "completed")


@pytest.mark.asyncio
async def test_competing_live_owner_waits_without_discarding_paid_response(result_db):
    owner = ledger(result_db)
    claim = await owner.claim("one", "h")
    attempt = await owner.reserve(claim, "openai/synthetic", "h", 100)
    other = ledger(result_db)
    with pytest.raises(budget.ExecutionOperationBusyError, match="execution_dispatcher_inflight") as caught:
        await other.claim("two", "h2")
    assert caught.value.terminal is False
    row, op, paid = await rows(result_db, owner.scope_id, claim.key, attempt)
    assert (row.status, op.status, paid.status) == ("active", "pending", "reserved")
    assert (row.calls_used, row.tokens_charged) == (1, 100)
    assert await owner.settle(attempt, "completed", actual_tokens=7,
                              claim=claim, result=response()) is None
    assert (await owner.transport_result(attempt))["result"] == response()
    await owner.finish(claim, None)
    row, op, paid = await rows(result_db, owner.scope_id, claim.key, attempt)
    assert (row.status, op.status, paid.status) == ("active", "completed", "completed")
    assert (row.calls_used, row.tokens_charged) == (1, 7)
    assert await owner.settle(attempt, "completed", actual_tokens=7,
                              claim=claim, result=response()) is None
    assert (await other.claim("one", "h")).cached_result == response()
    row, _, _ = await rows(result_db, owner.scope_id, claim.key, attempt)
    assert (row.calls_used, row.tokens_charged) == (1, 7)


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_late_original_response_is_archived_without_overwriting_manual_decision(result_db, recover):
    owner = ledger(result_db)
    claim = await owner.claim("one", "h")
    attempt = await owner.reserve(claim, "openai/synthetic", "h", 100)
    await expire_operation_lease(result_db, claim.key)
    other = ledger(result_db)
    with pytest.raises(budget.ExecutionBudgetError, match="execution_result_unknown"):
        await other.claim("two", "h2")
    manual = response("manually recovered body")
    await other.reconcile(evidence(attempt, **({"decision": "recover_completed", "result": manual} if recover else {})))
    before, _, _ = await rows(result_db, owner.scope_id, claim.key, attempt)
    blocked = await owner.settle(attempt, "completed", actual_tokens=7, claim=claim, result=response("late body"))
    assert blocked == "execution_operation_stopped"
    after, op, paid = await rows(result_db, owner.scope_id, claim.key, attempt)
    assert (after.calls_used, after.tokens_charged, after.deadline_at, after.status) == (
        before.calls_used, before.tokens_charged, before.deadline_at, before.status)
    assert paid.status == "reconciled_charged"
    assert op.status == ("completed" if recover else "stopped")
    assert op.result_json == (manual if recover else None)
    assert (await owner.transport_result(attempt))["result"]["content"] == "late body"
    with pytest.raises(budget.ExecutionBudgetError, match="execution_operation_stopped"):
        await owner.finish(claim, response("late body"))
    _, op, _ = await rows(result_db, owner.scope_id, claim.key, attempt)
    assert op.result_json == (manual if recover else None)


@pytest.mark.asyncio
async def test_late_unknown_response_keeps_reservation_until_manual_reconciliation(result_db):
    owner = ledger(result_db)
    claim = await owner.claim("one", "h")
    attempt = await owner.reserve(claim, "openai/synthetic", "h", 100)
    await owner.settle(attempt, "unknown")
    assert await owner.settle(attempt, "completed", actual_tokens=7,
                              claim=claim, result=response()) == "execution_result_unknown"
    row, op, paid = await rows(result_db, owner.scope_id, claim.key, attempt)
    assert (row.tokens_charged, row.status, op.status, paid.status) == (100, "unknown", "unknown", "unknown")
    assert (await owner.transport_result(attempt))["result"] == response()
    await owner.reconcile(evidence(attempt, decision="recover_completed", result=response()))
    row, _, _ = await rows(result_db, owner.scope_id, claim.key, attempt)
    assert row.tokens_charged == 100


@pytest.mark.asyncio
async def test_over_usage_cannot_replace_unknown_freeze_with_exhausted(result_db):
    owner = ledger(result_db)
    claim = await owner.claim("one", "h")
    attempt = await owner.reserve(claim, "openai/synthetic", "h", 100)
    await expire_operation_lease(result_db, claim.key)
    with pytest.raises(budget.ExecutionBudgetError, match="execution_result_unknown"):
        await ledger(result_db).claim("other", "other-input")
    await owner.settle(attempt, "completed", actual_tokens=100_001, claim=claim, result=response())
    row, _, _ = await rows(result_db, owner.scope_id, claim.key, attempt)
    assert row.status == "unknown"
    receipt = await owner.reconcile(evidence(attempt))
    assert receipt["status"] == "exhausted" and receipt["credit_released"] is False


@pytest.mark.asyncio
async def test_wrong_owner_cannot_archive_or_settle_another_owners_result(result_db):
    owner = ledger(result_db)
    claim = await owner.claim("one", "h")
    attempt = await owner.reserve(claim, "openai/synthetic", "h", 100)
    with pytest.raises(budget.ExecutionBudgetError):
        await owner.settle(attempt, "completed", actual_tokens=7,
                           claim=replace(claim, owner="not-the-owner"), result=response())
    assert await owner.transport_result(attempt) is None
    row, _, paid = await rows(result_db, owner.scope_id, claim.key, attempt)
    assert (row.tokens_charged, paid.status) == (100, "reserved")


@pytest.mark.asyncio
async def test_result_storage_failure_rolls_back_success_settlement(result_db):
    owner = ledger(result_db)
    claim = await owner.claim("one", "h")
    attempt = await owner.reserve(claim, "openai/synthetic", "h", 100)
    def fail_raw_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO EXECUTION_OPERATIONS"):
            raise RuntimeError("synthetic result storage failure")
    event.listen(result_db.sync_engine, "before_cursor_execute", fail_raw_insert)
    try:
        with pytest.raises(budget.ExecutionBudgetError):
            await owner.settle(attempt, "completed", actual_tokens=7, claim=claim, result=response())
    finally:
        event.remove(result_db.sync_engine, "before_cursor_execute", fail_raw_insert)
    row, op, paid = await rows(result_db, owner.scope_id, claim.key, attempt)
    assert (row.tokens_charged, op.status, paid.status) == (100, "pending", "reserved")
    assert await owner.transport_result(attempt) is None
    with pytest.raises(budget.ExecutionOperationBusyError, match="execution_dispatcher_inflight"):
        await ledger(result_db).claim("other", "other-input")
    await expire_operation_lease(result_db, claim.key)
    with pytest.raises(budget.ExecutionBudgetError, match="execution_result_unknown"):
        await ledger(result_db).claim("other", "other-input")


@pytest.mark.asyncio
async def test_cleanup_and_enrichment_cannot_erase_or_replace_paid_content(result_db):
    owner = ledger(result_db)
    claim = await owner.claim("one", "h")
    attempt = await owner.reserve(claim, "openai/synthetic", "h", 100)
    await owner.settle(attempt, "completed", actual_tokens=7, claim=claim, result=response())
    await owner.finish(claim, None)
    enriched = response()
    enriched["metadata"] = {"effective_input_receipt": {"hash": "h"}}
    await owner.finish(claim, enriched)
    with pytest.raises(budget.ExecutionBudgetError):
        await owner.finish(claim, response("wrong sample"))
    assert (await ledger(result_db).claim("one", "h")).cached_result == enriched
    assert (await owner.transport_result(attempt))["result"] == response()
    with pytest.raises(budget.ExecutionBudgetError):
        await ledger(result_db, "audit:unrelated-scope").transport_result(attempt)


@pytest.mark.asyncio
@pytest.mark.parametrize("failpoint", ["after_settlement", "upper_formatting"])
async def test_gateway_hard_exit_preserves_paid_body_tools_and_usage(result_db, monkeypatch, failpoint):
    sent = []
    class HardExit(BaseException):
        pass
    async def send(*args):
        sent.append(1)
        data = response()
        return (data["content"], 3, 4, "stop", data["tool_calls"], data["raw_message"])
    monkeypatch.setattr(llm, "_call_litellm", send)
    if failpoint == "after_settlement":
        settle = budget.ExecutionLedger.settle
        async def stop_after_commit(self, *args, **kwargs):
            await settle(self, *args, **kwargs)
            raise HardExit()
        monkeypatch.setattr(budget.ExecutionLedger, "settle", stop_after_commit)
    else:
        def stop_formatting(*args, **kwargs):
            raise HardExit()
        monkeypatch.setattr(llm, "merge_attribution_into", stop_formatting)
    request = gateway_request(operation_id="stable-writer", sample_slot="sample-2")
    settings = gateway_settings()
    with budget.bind_execution_scope("audit:gateway-result"):
        with pytest.raises(HardExit):
            await llm.complete_text(GatewaySession(result_db), settings, request)
    if failpoint == "after_settlement":
        monkeypatch.setattr(budget.ExecutionLedger, "settle", settle)
    else:
        monkeypatch.setattr(llm, "merge_attribution_into", lambda metadata: metadata)
    with budget.bind_execution_scope("audit:gateway-result"):
        result = await llm.complete_text(GatewaySession(result_db), settings, request)
    assert result.content == "paid body" and result.tool_calls == response()["tool_calls"]
    assert result.raw_message == response()["raw_message"]
    assert (result.input_tokens, result.output_tokens, len(sent)) == (3, 4, 1)


@pytest.mark.asyncio
async def test_gateway_archives_only_rebuildable_fields_not_sdk_or_caller_metadata(result_db, monkeypatch):
    import json
    class HardExit(BaseException):
        pass
    settle = budget.ExecutionLedger.settle
    async def send(*args):
        tool = response()["tool_calls"][0]
        return ("safe body", 3, 4, "stop", [{**tool, "headers": {"auth": "SDK_SECRET"}}],
                {"headers": {"auth": "SDK_SECRET"}, "request": object(), "metadata": "SDK_METADATA"})
    async def stop_after_commit(self, *args, **kwargs):
        await settle(self, *args, **kwargs)
        raise HardExit()
    monkeypatch.setattr(llm, "_call_litellm", send)
    monkeypatch.setattr(budget.ExecutionLedger, "settle", stop_after_commit)
    request = gateway_request(operation_id="safe-return").model_copy(update={
        "metadata": {"auth": "CALLER_SECRET", "sdk_request": object()}})
    with budget.bind_execution_scope("audit:safe-result"):
        with pytest.raises(HardExit):
            await llm.complete_text(GatewaySession(result_db), gateway_settings(), request)
    restored = ledger(result_db, "audit:safe-result")
    cached = (await restored.claim("safe-return", llm._effective_input_hash(request, gateway_settings().llm.planner))).cached_result
    raw_key = cached["metadata"]["execution_receipt"]["attempts"][0]["attempt_id"]
    raw = await restored.transport_result(raw_key)
    assert raw["result"]["raw_message"] == llm._build_raw_assistant_message("safe body", response()["tool_calls"])
    saved_json = json.dumps(raw)
    assert all(marker not in saved_json for marker in ("SDK_SECRET", "SDK_METADATA", "CALLER_SECRET", "sdk_request", "headers"))


@pytest.mark.asyncio
async def test_atomic_paid_cache_preserves_distinct_samples_and_rejects_changed_input(result_db, monkeypatch):
    sent = []
    async def send(*args):
        sent.append(1)
        return f"sample-{len(sent)}", 3, 4, "stop", None, None
    monkeypatch.setattr(llm, "_call_litellm", send)
    settings = gateway_settings(calls=3)
    first = gateway_request(operation_id="writer", sample_slot="a")
    second = gateway_request(operation_id="writer", sample_slot="b")
    with budget.bind_execution_scope("audit:sample-result"):
        a = await llm.complete_text(GatewaySession(result_db), settings, first)
        b = await llm.complete_text(GatewaySession(result_db), settings, second)
    with budget.bind_execution_scope("audit:sample-result"):
        restored = await llm.complete_text(GatewaySession(result_db), settings, second)
        assert a.content == "sample-1" and b.content == restored.content == "sample-2"
        assert len(sent) == 2
        with pytest.raises(budget.ExecutionBudgetError, match="execution_operation_input_changed"):
            await llm.complete_text(GatewaySession(result_db), settings,
                                    first.model_copy(update={"user_prompt": "changed-input"}))


@pytest.mark.asyncio
@pytest.mark.parametrize("save_result", [False, True])
async def test_real_process_exit_after_committed_settlement(result_db, save_result):
    """The child exits without finally/finish; the parent reopens its ledger."""
    import asyncio
    import json
    import sys
    script = '''
import asyncio,json,os,sys
from sqlalchemy.ext.asyncio import create_async_engine
from bestseller.services.budget import ExecutionLedger
from bestseller.settings import BudgetSettings
async def main():
    engine=create_async_engine(sys.argv[1])
    ledger=ExecutionLedger(engine,"audit:subprocess",BudgetSettings(execution_max_calls=4,execution_max_tokens=100000,execution_deadline_seconds=60))
    claim=await ledger.claim("paid-writer","h")
    attempt=await ledger.reserve(claim,"openai/synthetic","h",100)
    extra={"claim":claim,"result":json.loads(sys.argv[3])} if sys.argv[2]=="True" else {}
    await ledger.settle(attempt,"completed",actual_tokens=7,**extra)
    os._exit(19)
asyncio.run(main())
'''
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, str(result_db.url), str(save_result), json.dumps(response()),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=15)
    assert child.returncode == 19, (stdout.decode(), stderr.decode())
    restored = ledger(result_db, "audit:subprocess")
    if save_result:
        assert (await restored.claim("paid-writer", "h")).cached_result == response()
    else:
        with pytest.raises(budget.ExecutionBudgetError, match="execution_result_unknown"):
            await restored.claim("different-operation", "different-input")
    async with async_sessionmaker(result_db)() as session:
        row = await session.get(ExecutionBudgetModel, restored.scope_id)
        assert (row.calls_used, row.tokens_charged) == (1, 7)


@pytest.mark.asyncio
@pytest.mark.parametrize(("when", "manual"), [
    ("in_flight", None), ("in_flight", "abandon_charged"),
    ("in_flight", "recover_completed"), ("after_finish", None), ("before_finish", None),
])
async def test_original_gateway_cannot_return_success_after_shared_freeze(result_db, monkeypatch, when, manual):
    import asyncio
    import contextvars
    scope = f"audit:late-return:{when}:{manual}"
    frozen = []
    async def competitor():
        other = ledger(result_db, scope)
        if when == "in_flight":
            # This scenario is an expired/uncertain dispatcher, not merely a
            # competing live entrance. Its eventual response must stay fenced.
            async with async_sessionmaker(result_db)() as session:
                original = (await session.scalars(select(ExecutionOperationModel).where(
                    ExecutionOperationModel.scope_id == scope,
                    ExecutionOperationModel.status == "pending",
                ))).one()
                original_key = original.key
            await expire_operation_lease(result_db, original_key)
            with pytest.raises(budget.ExecutionBudgetError, match="execution_result_unknown"):
                await other.claim("competing-operation", "competing-input")
        else:
            claim = await other.claim("competing-operation", "competing-input")
            aid = await other.reserve(claim, "openai/synthetic", "competing-input", 100)
            await other.settle(aid, "unknown")
        inventory = await other.reconciliation_status()
        if manual is not None:
            attempt = inventory["attempts"][0]
            payload = evidence(attempt["attempt_id"], expected_input_hash=attempt["input_hash"],
                               decision=manual, **({"result": response("manual body")} if manual == "recover_completed" else {}))
            await other.reconcile(payload)
        frozen.append(True)
    async def freeze_in_other_context():
        await asyncio.create_task(competitor(), context=contextvars.Context())
    async def send(*args):
        if when == "in_flight":
            await freeze_in_other_context()
        data = response("late body")
        return data["content"], 3, 4, "stop", data["tool_calls"], data["raw_message"]
    original_finish = budget.ExecutionLedger.finish
    async def finish(self, *args, **kwargs):
        if when == "before_finish" and args[1] is not None and not frozen:
            await freeze_in_other_context()
        return await original_finish(self, *args, **kwargs)
    async def persist(*args, **kwargs):
        if when == "after_finish":
            await freeze_in_other_context()
        return None
    monkeypatch.setattr(llm, "_call_litellm", send)
    monkeypatch.setattr(llm, "_persist_llm_run_safely", persist)
    monkeypatch.setattr(budget.ExecutionLedger, "finish", finish)
    with budget.bind_execution_scope(scope):
        with pytest.raises(budget.ExecutionBudgetError) as caught:
            await llm.complete_text(GatewaySession(result_db), gateway_settings(),
                                    gateway_request(operation_id="original"))
        assert caught.value.terminal is True
        assert caught.value.code == ("execution_operation_stopped" if manual else "execution_result_unknown")
        with pytest.raises(budget.ExecutionBudgetError):
            budget.raise_if_execution_stopped()
    async with async_sessionmaker(result_db)() as session:
        saved = (await session.scalars(select(ExecutionOperationModel).where(
            ExecutionOperationModel.scope_id == scope,
            ExecutionOperationModel.status == "transport_result"))).all()
        assert len(saved) == 1 and saved[0].result_json["result"]["content"] == "late body"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unknown", "cancelled", "expired"])
async def test_cached_gateway_obeys_shared_freeze_but_keeps_expired_paid_result_readable(result_db, monkeypatch, state):
    from datetime import datetime, timedelta, timezone
    sent = []
    async def send(*args):
        sent.append(1)
        return "paid cached body", 3, 4, "stop", None, None
    monkeypatch.setattr(llm, "_call_litellm", send)
    scope = f"audit:cached-freeze:{state}"
    settings = gateway_settings(calls=4)
    request = gateway_request(operation_id="cached-original")
    with budget.bind_execution_scope(scope):
        await llm.complete_text(GatewaySession(result_db), settings, request)
    other = ledger(result_db, scope)
    if state == "unknown":
        claim = await other.claim("other", "other-input")
        attempt = await other.reserve(claim, "openai/synthetic", "other-input", 100)
        await other.settle(attempt, "unknown")
    elif state == "cancelled":
        await other.cancel()
    else:
        async with async_sessionmaker(result_db)() as session, session.begin():
            row = await session.get(ExecutionBudgetModel, scope)
            row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with budget.bind_execution_scope(scope):
        if state == "expired":
            result = await llm.complete_text(GatewaySession(result_db), settings, request)
            assert result.content == "paid cached body"
        else:
            with pytest.raises(budget.ExecutionBudgetError, match=(
                "execution_result_unknown" if state == "unknown" else "execution_cancelled")):
                await llm.complete_text(GatewaySession(result_db), settings, request)
    assert len(sent) == 1
