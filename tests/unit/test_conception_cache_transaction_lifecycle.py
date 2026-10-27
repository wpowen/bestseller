"""Real SQLite transactions and ORM lock intent; no PostgreSQL or provider IO."""
import asyncio
from copy import deepcopy
from uuid import UUID

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from bestseller.infra.db import models as m
from bestseller.services import conception_continuation as recovery
from bestseller.services import independent_acceptance as independent
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger, bind_execution_scope
from bestseller.settings import BudgetSettings
from test_conception_continuation import setup
from test_independent_acceptance import case as case, request_role
from test_conception_review_content_versions import BASE, STAGES, prepare_chain, request_for
from test_market_validation_resume_identity import market_source
from bestseller.services import llm

pytestmark = pytest.mark.unit


async def active(case):
    chain = await setup(case)
    await chain.prepare()
    scope = await recovery.consume(case.engine, chain.task_id, case.payload)
    request, _ = request_role()
    return scope, request.model_copy(update={"prompt_template": "stage0"})


@pytest.mark.asyncio
async def test_scan_has_no_financial_locks_and_ends_before_fresh_locked_phase(case, monkeypatch):
    scope, request = await active(case)
    original = recovery._retained_sources
    locks = []
    ended = []
    discovery = []
    receipts = []

    def executed(state):
        if state.session.get_bind() is not case.engine.sync_engine:
            return
        if getattr(state.statement, "_for_update_arg", None) is not None:
            # SQLite omits FOR UPDATE from emitted SQL. Inspect the *real ORM
            # statement* so this regression still detects PostgreSQL lock intent.
            locks.append(state.session.get_transaction())
            if discovery:
                assert discovery[0] in ended
                assert state.session.get_transaction() is not discovery[0]
        if locks and discovery:
            sql = str(state.statement)
            if "FROM execution_operations" in sql:
                assert "execution_operations.key =" in sql, "no source inventory rescan under financial locks"
            if "FROM llm_runs" in sql:
                assert "llm_runs.id =" in sql, "source audit must be reloaded by exact ID"

    def transaction_ended(session, transaction):
        if session.get_bind() is case.engine.sync_engine and transaction.parent is None:
            ended.append(transaction)

    async def scanned(read, scan_scope, grant):
        assert read.in_transaction()
        assert not locks, "retained-source scan must run before any budget/ancestor FOR UPDATE"
        discovery.append(read.sync_session.get_transaction())
        assert not read.new and not read.dirty and not read.deleted
        sources = await original(read, scan_scope, grant)
        receipts.extend(await read.scalars(select(m.ExecutionOperationModel).where(
            m.ExecutionOperationModel.scope_id == scope,
            m.ExecutionOperationModel.status.in_(["review_revision", "recovered_success_reference"]))))
        return sources

    event.listen(Session, "do_orm_execute", executed)
    event.listen(Session, "after_transaction_end", transaction_ended)
    monkeypatch.setattr(recovery, "_retained_sources", scanned)
    try:
        with bind_execution_scope(scope):
            async with case.factory() as caller:
                result = await recovery.adopt_cached(caller, request, "success0")
        assert discovery and discovery[0] in ended
        assert locks and not receipts
        assert result["content"] == "preserved source"
    finally:
        event.remove(Session, "do_orm_execute", executed)
        event.remove(Session, "after_transaction_end", transaction_ended)


def between_phases(monkeypatch, change):
    """Await a real committed mutation after discovery closes, before locking."""
    original = recovery._retained_sources
    discovery = []
    changed = []

    async def scanned(read, scope, grant):
        result = await original(read, scope, grant)
        discovery.append(read)
        return result

    class BoundarySession(AsyncSession):
        async def scalar(self, statement, *args, **kwargs):
            if (discovery and not changed
                and getattr(statement, "_for_update_arg", None) is not None):
                assert all(not read.in_transaction() for read in discovery)
                changed.append(True)
                await change()
            return await super().scalar(statement, *args, **kwargs)

    def factory(*args, **kwargs):
        return async_sessionmaker(*args, **kwargs, class_=BoundarySession)

    monkeypatch.setattr(recovery, "_retained_sources", scanned)
    monkeypatch.setattr(recovery, "async_sessionmaker", factory)
    return changed


async def receipt_count(case, scope):
    async with case.factory() as read:
        return await read.scalar(select(func.count()).select_from(m.ExecutionOperationModel).where(
            m.ExecutionOperationModel.scope_id == scope,
            m.ExecutionOperationModel.status.in_(["review_revision", "recovered_success_reference"])))


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,code", [
    ("grant", "execution_continuation_preflight_changed"),
    ("submission", "execution_continuation_preflight_changed"),
    ("invalid_grant", "execution_acceptance_binding_invalid"),
    ("missing_grant", "execution_continuation_binding_invalid"),
    ("cancelled", "execution_cancelled"),
    ("unknown", "execution_result_unknown"),
    ("awaiting_submission", "execution_submission_not_authorized"),
    ("exhausted", "execution_budget_exhausted"),
    ("expired", "execution_deadline_exhausted"),
    ("authorization", "execution_continuation_preflight_changed"),
    ("ancestor", "execution_original_acceptance_changed"),
    ("source_content", "execution_original_success_changed"),
    ("source_metadata", "execution_original_success_changed"),
    ("source_input", "execution_original_success_changed"),
    ("source_scope", "execution_original_success_changed"),
    ("source_status", "execution_original_success_changed"),
    ("source_missing", "execution_original_success_changed"),
    ("run_usage", "execution_success_audit_missing"),
    ("run_model", "execution_success_audit_missing"),
    ("run_missing", "execution_success_audit_missing"),
    ("run_metadata", "execution_original_success_changed"),
    ("run_template", "execution_original_success_changed"),
    ("run_role", "execution_original_success_changed"),
    ("run_response", "execution_original_success_changed"),
])
async def test_changed_proof_between_transactions_fails_closed(case, monkeypatch, mutation, code):
    from datetime import datetime, timedelta, timezone

    scope, request = await active(case)

    async def change():
        async with case.factory() as writer, writer.begin():
            row = await writer.get(m.ExecutionBudgetModel, scope)
            meta = deepcopy(row.metadata_json)
            grant = meta[independent.KEY]
            if mutation == "grant":
                grant["request"]["authorization_ref"] = "different valid grant"
                grant["request_sha256"] = independent.digest(grant["request"])
            elif mutation == "submission":
                grant["submitted_at"] = "different submission receipt"
            elif mutation == "invalid_grant":
                grant["request"]["authorization_ref"] = "invalid digest"
            elif mutation == "missing_grant":
                meta.pop(independent.KEY)
            elif mutation == "authorization":
                meta["provider_authorization"] = {
                    "mode": "completion_bound", "status": "active", "models": [independent.MODEL],
                    "endpoint_hosts": ["api.minimaxi.com"], "authorization_ref": "changed",
                    "objective": "changed objective"}
            elif mutation in {"cancelled", "unknown", "awaiting_submission", "exhausted"}:
                row.status = mutation
            elif mutation == "expired":
                row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            elif mutation == "ancestor":
                old = await writer.get(m.ExecutionBudgetModel, case.old_scope)
                old.calls_used += 1
            else:
                source = await writer.get(m.ExecutionOperationModel, "source0")
                if mutation.startswith("source_"):
                    if mutation == "source_missing":
                        await writer.delete(source)
                    elif mutation == "source_content":
                        source.result_json = {**source.result_json, "content": "changed"}
                    elif mutation == "source_metadata":
                        source.result_json = {**source.result_json, "metadata": {"changed": True}}
                    else:
                        setattr(source, {"source_input": "input_hash", "source_scope": "scope_id",
                            "source_status": "status"}[mutation],
                            case.old_scope if mutation == "source_scope" else "changed")
                else:
                    run = await writer.get(m.LlmRunModel, UUID(source.result_json["llm_run_id"]))
                    if mutation == "run_missing":
                        await writer.delete(run)
                    else:
                        attr = {"run_usage": "output_tokens", "run_model": "model_name",
                            "run_metadata": "metadata_json", "run_template": "prompt_template",
                            "run_role": "logical_role", "run_response": "response_payload_ref"}[mutation]
                        setattr(run, attr, 99 if mutation == "run_usage" else
                            {"changed": True} if mutation == "run_metadata" else "changed")
            row.metadata_json = meta

    changed = between_phases(monkeypatch, change)
    with bind_execution_scope(scope):
        async with case.factory() as caller:
            with pytest.raises(ExecutionBudgetError, match=code):
                await recovery.adopt_cached(caller, request, "success0")
    assert changed == [True]
    assert await receipt_count(case, scope) == 0
    async with case.factory() as read:
        row = await read.get(m.ExecutionBudgetModel, scope)
        assert (row.calls_used, row.tokens_charged) == (0, 0)
        assert (await read.get(m.ExecutionOperationModel, "source27")).status == "unknown"
        assert (await read.get(m.ExecutionAttemptModel, "new27")).actual_tokens is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,code", [
    ("paused", "execution_authorization_paused"),
    ("closed", "execution_authorization_closed"),
    ("reference", "execution_continuation_preflight_changed"),
    ("ancestor", "execution_original_acceptance_changed"),
    ("source", "execution_original_success_changed"),
    ("run", "execution_original_success_changed"),
])
async def test_fresh_review_revision_rechecks_its_authority_and_sources(case, monkeypatch, mutation, code):
    _, scope = await prepare_chain(case)
    request, role = request_for({**BASE, "synopsis": "new complete synopsis"}, *STAGES[0])
    payload = llm._effective_input_payload(request, role)
    prompt_hash = llm._effective_input_hash(request, role)

    async def change():
        async with case.factory() as writer, writer.begin():
            row = await writer.get(m.ExecutionBudgetModel, scope)
            if mutation in {"paused", "closed", "reference"}:
                meta = deepcopy(row.metadata_json)
                if mutation == "reference":
                    meta["provider_authorization"]["authorization_ref"] = "different objective"
                else:
                    meta["provider_authorization"]["status"] = mutation
                row.metadata_json = meta
            elif mutation == "ancestor":
                old = await writer.get(m.ExecutionBudgetModel, case.old_scope)
                old.tokens_charged += 1
            else:
                source = await writer.get(m.ExecutionOperationModel, "source0")
                if mutation == "source":
                    source.input_hash = "different old input"
                else:
                    run = await writer.get(m.LlmRunModel, UUID(source.result_json["llm_run_id"]))
                    run.metadata_json = {"changed": True}

    changed = between_phases(monkeypatch, change)
    with bind_execution_scope(scope):
        async with case.factory() as caller:
            with pytest.raises(ExecutionBudgetError, match=code):
                await recovery.adopt_cached(caller, request, prompt_hash, payload)
    assert changed == [True]
    assert await receipt_count(case, scope) == 0


@pytest.mark.asyncio
async def test_caller_transaction_is_never_committed_and_receipt_has_exact_source(case):
    scope, request = await active(case)
    async with case.factory() as read:
        before = independent.snapshot(await read.get(m.ExecutionBudgetModel, scope))
        source = await read.get(m.ExecutionOperationModel, "source0")
        result = deepcopy(source.result_json)
        source_hash = independent.digest(result)
    with bind_execution_scope(scope):
        async with case.factory() as caller:
            row = await caller.get(m.ExecutionBudgetModel, scope)
            caller.add(m.ExecutionOperationModel(key="caller-pending", scope_id=scope, input_hash="pending",
                status="caller-only", owner="fixture", lease_until=row.deadline_at))
            adopted = await recovery.adopt_cached(caller, request, "success0")
            assert caller.in_transaction() and caller.new
            await caller.rollback()
    receipt = adopted["metadata"]["execution_receipt"]
    assert adopted["metadata"]["paid_call_origin"] == result["metadata"]["paid_call_origin"]
    assert adopted["llm_run_id"] == result["llm_run_id"]
    assert receipt["source_result_sha256"] == source_hash
    assert receipt["source_operation_key"] == "source0" and receipt["attempts"] == []
    async with case.factory() as read:
        assert await read.get(m.ExecutionOperationModel, "caller-pending") is None
        assert (await read.get(m.ExecutionOperationModel, "source0")).result_json == result
        assert independent.snapshot(await read.get(m.ExecutionBudgetModel, scope)) == before
        reference = await read.get(m.ExecutionOperationModel, receipt["operation_key"])
        assert reference.status == "recovered_success_reference"
        assert reference.result_json == {"operation_key": "source0", "input_hash": "success0",
            "result_sha256": source_hash, "template": "stage0", "role": "critic",
            "source_llm_run_id": result["llm_run_id"], "source_scope": source.scope_id}
        assert await read.scalar(select(func.count()).select_from(m.ExecutionAttemptModel).where(
            m.ExecutionAttemptModel.scope_id == scope)) == 0


@pytest.mark.asyncio
async def test_slow_discovery_allows_real_concurrent_claim_without_reservation(case, monkeypatch):
    scope, request = await active(case)
    original = recovery._retained_sources
    scanning, release = asyncio.Event(), asyncio.Event()

    async def scanned(read, scan_scope, grant):
        sources = await original(read, scan_scope, grant)
        scanning.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        return sources

    monkeypatch.setattr(recovery, "_retained_sources", scanned)

    async def adopt():
        with bind_execution_scope(scope):
            async with case.factory() as caller:
                return await recovery.adopt_cached(caller, request, "success0")

    task = asyncio.create_task(adopt())
    try:
        await asyncio.wait_for(scanning.wait(), timeout=3)
        ledger = ExecutionLedger(case.engine, scope, BudgetSettings())
        claim = await asyncio.wait_for(ledger.claim("concurrent-local-claim", "new-input"), timeout=2)
        assert claim.cached_result is None
        assert not task.done()
    finally:
        release.set()
        if not task.done():
            await asyncio.wait_for(task, timeout=3)
    assert task.result()["content"] == "preserved source"
    async with case.factory() as read:
        row = await read.get(m.ExecutionBudgetModel, scope)
        assert (row.calls_used, row.tokens_charged) == (0, 0)
        assert await read.scalar(select(func.count()).select_from(m.ExecutionAttemptModel).where(
            m.ExecutionAttemptModel.scope_id == scope)) == 0


@pytest.mark.asyncio
async def test_normal_concurrent_financial_accounting_is_preserved(case, monkeypatch):
    scope, request = await active(case)

    async def change():
        async with case.factory() as writer, writer.begin():
            row = await writer.get(m.ExecutionBudgetModel, scope)
            row.calls_used = 1
            row.tokens_charged = 30

    changed = between_phases(monkeypatch, change)
    with bind_execution_scope(scope):
        async with case.factory() as caller:
            assert (await recovery.adopt_cached(caller, request, "success0"))["content"] == "preserved source"
    assert changed == [True]
    async with case.factory() as read:
        row = await read.get(m.ExecutionBudgetModel, scope)
        assert (row.calls_used, row.tokens_charged) == (1, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("sample_slot", [None, 0, 2])
async def test_ambiguous_input_requires_exact_semantic_operation_and_sample(case, sample_slot):
    import hashlib

    chain = await setup(case)
    operation_id = "generic-review-operation"
    suffix = f":sample:{sample_slot}" if sample_slot is not None else ""
    expected_key = hashlib.sha256((chain.parent_scope + "\n" + operation_id + suffix).encode()).hexdigest()
    async with case.factory() as writer, writer.begin():
        source = await writer.get(m.ExecutionOperationModel, "source0")
        source.key = expected_key
        attempt = await writer.get(m.ExecutionAttemptModel, "new0")
        attempt.operation_key = expected_key
        other = await writer.get(m.ExecutionOperationModel, "source1")
        other.input_hash = "success0"
        other.result_json = {**other.result_json, "content": "different sample result",
            "metadata": {"paid_call_origin": {"prompt_template": "stage0", "logical_role": "critic"}}}
        run = await writer.get(m.LlmRunModel, UUID(other.result_json["llm_run_id"]))
        run.prompt_template = "stage0"
    await chain.prepare()
    scope = await recovery.consume(case.engine, chain.task_id, case.payload)
    request, _ = request_role()
    request = request.model_copy(update={"prompt_template": "stage0", "operation_id": operation_id,
        "sample_slot": sample_slot})
    with bind_execution_scope(scope):
        async with case.factory() as caller:
            adopted = await recovery.adopt_cached(caller, request, "success0")
            assert adopted["content"] == "preserved source"
            assert adopted["metadata"]["execution_receipt"]["source_operation_key"] == expected_key
            for wrong in (request.model_copy(update={"operation_id": None}),
                request.model_copy(update={"operation_id": "different operation"}),
                request.model_copy(update={"sample_slot": 9})):
                with pytest.raises(ExecutionBudgetError, match="execution_continuation_preflight_changed"):
                    await recovery.adopt_cached(caller, wrong, "success0")
    assert await receipt_count(case, scope) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,code", [
    ("attempt_status", "execution_success_audit_missing"),
    ("attempt_usage", "execution_success_audit_missing"),
    ("attempt_missing", "execution_success_audit_missing"),
    ("run_provenance", "execution_original_success_changed"),
])
async def test_current_scope_market_source_is_revalidated_without_inventory_rescan(case, monkeypatch, mutation, code):
    scope, request, prompt_hash, run_id, _ = await market_source(case)

    async def change():
        async with case.factory() as writer, writer.begin():
            if mutation == "run_provenance":
                run = await writer.get(m.LlmRunModel, run_id)
                run.metadata_json = {"execution_receipt": {"operation_key": "different source"}}
            else:
                attempt = await writer.get(m.ExecutionAttemptModel, "market-attempt")
                if mutation == "attempt_missing":
                    await writer.delete(attempt)
                elif mutation == "attempt_usage":
                    attempt.actual_tokens += 1
                else:
                    attempt.status = "unknown"

    changed = between_phases(monkeypatch, change)
    with bind_execution_scope(scope):
        async with case.factory() as caller:
            with pytest.raises(ExecutionBudgetError, match=code):
                await recovery.adopt_cached(caller, request, prompt_hash)
    assert changed == [True]
    assert await receipt_count(case, scope) == 0


@pytest.mark.asyncio
async def test_receipt_serialization_is_independent_of_existing_provider_send_lock(case):
    scope, request = await active(case)
    with bind_execution_scope(scope):
        # The existing provider lock can span a whole network round trip.
        # Cache receipts need only serialize their own short audit writes.
        async with independent.serialize(scope):
            async with case.factory() as caller:
                adopted = await asyncio.wait_for(recovery.adopt_cached(caller, request, "success0"), timeout=2)
    assert adopted["content"] == "preserved source"
    assert await receipt_count(case, scope) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [False, True])
async def test_concurrent_discovery_keeps_one_receipt_without_rescans_under_locks(case, monkeypatch, revision):
    if revision:
        _, scope = await prepare_chain(case)
        request, role = request_for({**BASE, "synopsis": "new complete synopsis"}, *STAGES[0])
        payload = llm._effective_input_payload(request, role)
        prompt_hash = llm._effective_input_hash(request, role)
    else:
        scope, request = await active(case)
        payload, prompt_hash = None, "success0"
    original = recovery._retained_sources
    ready = asyncio.Event()
    scans = []

    async def scanned(read, scan_scope, grant):
        sources = await original(read, scan_scope, grant)
        scans.append(read)
        if len(scans) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=3)
        return sources

    monkeypatch.setattr(recovery, "_retained_sources", scanned)

    async def adopt():
        with bind_execution_scope(scope):
            async with case.factory() as caller:
                return await recovery.adopt_cached(caller, request, prompt_hash, payload)

    first, second = await asyncio.wait_for(asyncio.gather(adopt(), adopt()), timeout=5)
    assert len(scans) == 2
    assert all(not read.in_transaction() for read in scans)
    assert first == second
    assert await receipt_count(case, scope) == 1
    async with case.factory() as read:
        row = await read.get(m.ExecutionBudgetModel, scope)
        assert (row.calls_used, row.tokens_charged) == (0, 0)
        assert await read.scalar(select(func.count()).select_from(m.ExecutionAttemptModel).where(
            m.ExecutionAttemptModel.scope_id == scope)) == 0
