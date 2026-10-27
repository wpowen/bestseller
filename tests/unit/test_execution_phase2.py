"""Offline physical-send, recovery identity and charged reconciliation checks."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from bestseller.infra.db.models import ExecutionBudgetModel, ExecutionOperationModel
from bestseller.services import budget, llm
from test_execution_budget_core import (
    ledger_db, ledger, GatewaySession, gateway_settings, gateway_request,  # noqa: F401 - shared pytest fixture
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_bound_semantic_namespace_survives_skipped_prior_call(ledger_db, monkeypatch):
    sent = []
    async def send(request, role):
        sent.append(request.user_prompt)
        return request.user_prompt, 1, 2, "stop", None, None
    monkeypatch.setattr(llm, "_call_litellm", send)
    async def run(text):
        return await llm.complete_text(GatewaySession(ledger_db), gateway_settings(),
                                       gateway_request().model_copy(update={"user_prompt": text}))
    with budget.bind_execution_scope("test:identity"), llm.bind_execution_operation("review:revision-1"):
        await run("prior")
        second = await run("current")
    with budget.bind_execution_scope("test:identity"), llm.bind_execution_operation("review:revision-1"):
        restored = await run("current")
    assert second.content == restored.content and sent == ["prior", "current"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "deepseek"])
@pytest.mark.parametrize("duplicate", [False, True])
async def test_native_sdk_client_physical_boundary(ledger_db, monkeypatch, provider, duplicate):
    sent = []
    async def handle(request):
        sent.append(request.content)
        return httpx.Response(200, json={"ok": True})
    async def sdk(**kwargs):
        assert kwargs["model"] == f"{provider}/synthetic"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await client.post("https://synthetic.invalid/native", json={"model": kwargs["model"]})
            if duplicate:
                await client.post("https://synthetic.invalid/native", json={"model": kwargs["model"]})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="native result", tool_calls=None), finish_reason="stop")], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2))
    monkeypatch.setattr(llm, "_get_litellm", lambda: SimpleNamespace(acompletion=sdk))
    monkeypatch.setattr(llm, "_ensure_shared_litellm_http_client", lambda: None)
    settings = gateway_settings()
    settings.llm.planner.model = f"{provider}/synthetic"
    with budget.bind_execution_scope(f"test:native:{provider}"):
        if duplicate:
            with pytest.raises(budget.ExecutionBudgetError):
                await llm.complete_text(GatewaySession(ledger_db), settings, gateway_request())
        else:
            result = await llm.complete_text(GatewaySession(ledger_db), settings, gateway_request())
            assert result.content == "native result"
    assert len(sent) == 1


def evidence(attempt, **changes):
    return budget.ExecutionReconciliation.model_validate({
        "reconciliation_id": "manual-1", "attempt_id": attempt,
        "expected_input_hash": "h", "decision": "abandon_charged",
        "evidence_kind": "provider_receipt", "evidence_sha256": "a" * 64,
        "actor": "test-operator", "remote_operation_closed": True,
        **changes,
    })


@pytest.mark.asyncio
async def test_manual_reconciliation_retains_spend_audit_and_allows_new_operation(ledger_db):
    first = ledger(ledger_db)
    op = await first.claim("old", "h")
    attempt = await first.reserve(op, "m", "h", 100)
    await first.settle(attempt, "unknown")
    receipt = await first.reconcile(evidence(attempt))
    assert receipt["remaining_unknown"] == 0
    assert receipt == await first.reconcile(evidence(attempt))
    async with async_sessionmaker(ledger_db)() as session:
        row = await session.get(ExecutionBudgetModel, first.scope_id)
        assert row.calls_used == 1 and row.tokens_charged == 100
        assert row.metadata_json["reconciliations"][0]["evidence_sha256"] == "a" * 64
    with pytest.raises(budget.ExecutionBudgetError, match="operation_stopped"):
        await first.claim("old", "h")
    await first.claim("new-authorized-operation", "h2")
    with pytest.raises(budget.ExecutionBudgetError, match="reconciliation_conflict"):
        await first.reconcile(evidence(attempt, actor="another"))


@pytest.mark.asyncio
async def test_reconciliation_checks_scope_hash_and_does_not_extend_deadline(ledger_db):
    first = ledger(ledger_db)
    op = await first.claim("old", "h")
    attempt = await first.reserve(op, "m", "h", 100)
    await first.settle(attempt, "unknown")
    with pytest.raises(budget.ExecutionBudgetError, match="reconciliation_conflict"):
        await first.reconcile(evidence(attempt, expected_input_hash="x"))
    async with async_sessionmaker(ledger_db)() as session, session.begin():
        row = await session.get(ExecutionBudgetModel, first.scope_id)
        row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    receipt = await first.reconcile(evidence(attempt))
    assert receipt["status"] == "exhausted"
    with pytest.raises(budget.ExecutionBudgetError, match="exhausted"):
        await first.claim("new", "h")


@pytest.mark.asyncio
async def test_recovered_result_reuses_paid_identity_without_free_credit(ledger_db):
    first = ledger(ledger_db)
    op = await first.claim("old", "h")
    attempt = await first.reserve(op, "m", "h", 100)
    await first.settle(attempt, "unknown")
    result = llm.LLMCompletionResult(content="verified remote result", provider="openai", model_name="m").model_dump(mode="json")
    await first.reconcile(evidence(attempt, decision="recover_completed", result=result))
    assert (await first.claim("old", "h")).cached_result["content"] == "verified remote result"
    async with async_sessionmaker(ledger_db)() as session:
        row = await session.get(ExecutionBudgetModel, first.scope_id)
        assert row.calls_used == 1 and row.tokens_charged == 100


def test_reconciliation_requires_bounded_evidence_and_remote_closure():
    from pydantic import ValidationError
    for changes in ({"remote_operation_closed": False}, {"evidence_sha256": ""},
                    {"actor": "x" * 161}, {"actual_tokens": 0}):
        with pytest.raises(ValidationError):
            evidence("attempt", **changes)


def test_operation_id_is_stable_and_revision_sensitive():
    old = budget.execution_operation_id("writer", subject="chapter:1", revision="contract-v1")
    assert old == budget.execution_operation_id("writer", subject="chapter:1", revision="contract-v1")
    assert old != budget.execution_operation_id("writer", subject="chapter:1", revision="contract-v2")
    assert len(old) <= 256


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["deepseek/deepseek-chat", "anthropic/claude-sonnet-4-5", "gemini/gemini-2.5-flash"])
async def test_real_native_litellm_adapter_hits_protected_wire(ledger_db, monkeypatch, model):
    """Actual installed adapters, synthetic host + HTTPX MockTransport."""
    import json
    sent = []
    async def handle(request):
        assert request.url.host == "synthetic.invalid"
        sent.append({"body": json.loads(request.content), "path": request.url.path})
        if model.startswith("anthropic/"):
            return httpx.Response(200, json={"id": "offline", "type": "message", "role": "assistant",
                "model": "claude-sonnet-4-5", "content": [{"type": "text", "text": "native offline"}],
                "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 4}})
        if model.startswith("gemini/"):
            return httpx.Response(200, json={"candidates": [{"content": {"role": "model",
                "parts": [{"text": "native offline"}]}, "finishReason": "STOP", "index": 0}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4, "totalTokenCount": 7}})
        return httpx.Response(200, json={"id": "offline", "object": "chat.completion",
            "created": 1, "model": "deepseek-chat", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "native offline"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx.AsyncClient, "_transport_for_url", lambda self, url: transport)
    monkeypatch.setenv("WP5_OFFLINE_NATIVE_KEY", "synthetic-offline-key")
    settings = gateway_settings()
    settings.llm.planner.model = model
    settings.llm.planner.api_base = "https://synthetic.invalid/v1"
    settings.llm.planner.api_key_env = "WP5_OFFLINE_NATIVE_KEY"
    with budget.bind_execution_scope("test:real-native-adapter"):
        result = await llm.complete_text(GatewaySession(ledger_db), settings,
                                         gateway_request(operation_id="native-adapter"))
    assert result.content == "native offline"
    assert len(sent) == 1
    if model.startswith("deepseek/"):
        assert sent[0]["body"]["model"] == "deepseek-chat"
    elif model.startswith("anthropic/"):
        assert sent[0]["body"]["model"] == "claude-sonnet-4-5"
    else:
        assert "gemini-2.5-flash" in sent[0]["path"]
    assert result.metadata["execution_receipt"]["attempts"][0]["wire_body_hash"]


@pytest.mark.asyncio
async def test_actual_checkpoint_resume_reuses_review_paid_result(ledger_db, monkeypatch):
    import copy
    from bestseller.services import conception
    from test_conception_candidate_resume import review_state
    saved, sends = [], []
    async def send(*args):
        sends.append(1)
        return "review persisted", 1, 2, "stop", None, None
    monkeypatch.setattr(llm, "_call_litellm", send)
    async def save(checkpoint):
        saved.append(copy.deepcopy(checkpoint))
    async def review(session, settings, **kwargs):
        result = await llm.complete_text(session, settings, gateway_request())
        if len(saved) == 1:
            raise budget.ExecutionBudgetError("execution_cancelled")
        return result.content
    monkeypatch.setattr(conception, "_review_conception_candidate", review)
    state = review_state()
    with budget.bind_execution_scope("test:checkpoint"):
        with pytest.raises(budget.ExecutionBudgetError):
            await conception._complete_conception_candidate(GatewaySession(ledger_db), gateway_settings(),
                state=state, checkpoint_input_hash="stable-input", checkpoint_callback=save)
    assert saved[-1]["state"]["execution_operation_identity"]
    with budget.bind_execution_scope("test:checkpoint"):
        result = await conception.run_conception_pipeline(GatewaySession(ledger_db), gateway_settings(),
            genre_key="urban", chapter_count=5, checkpoint_input_hash="stable-input",
            resume_checkpoint=saved[-1], checkpoint_callback=save)
    assert result == "review persisted" and len(sends) == 1


@pytest.mark.asyncio
async def test_task_api_reconciliation_reloads_receipt_without_resuming(ledger_db, monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    from bestseller.web import server
    from sqlalchemy import Table, Column, Uuid, String, Integer, MetaData, JSON
    history = Table("llm_runs", MetaData(),
        Column("id", Uuid, primary_key=True), Column("project_id", Uuid),
        Column("workflow_run_id", Uuid), Column("metadata", JSON),
        Column("provider", String), Column("input_tokens", Integer), Column("output_tokens", Integer))
    async with ledger_db.begin() as connection:
        await connection.run_sync(history.create)
    first = budget.ExecutionLedger(ledger_db, "conception:test-task", gateway_settings().budget)
    op = await first.claim("old", "h")
    attempt = await first.reserve(op, "m", "h", 100)
    await first.settle(attempt, "unknown")
    @asynccontextmanager
    async def session_scope(settings):
        async with async_sessionmaker(ledger_db)() as session:
            yield session
            await session.commit()
    monkeypatch.setattr(server, "session_scope", session_scope)
    manager = server.WebTaskManager(persist_path=tmp_path / "test-tasks.json")
    task = server.WebTaskState(task_id="test-task", task_type="autowrite", status="failed",
        created_at="2026-10-15T00:00:00+00:00", updated_at="2026-10-15T00:00:00+00:00",
        payload={"_run_conception": True})
    manager._tasks[task.task_id] = task
    manager._mark_execution_terminal(task.task_id, budget.ExecutionBudgetError("execution_result_unknown"))
    inventory = await manager.execution_reconciliation(task.task_id, settings=gateway_settings())
    assert inventory["attempts"][0]["attempt_id"] == attempt
    receipt = await manager.execution_reconciliation(task.task_id, evidence(attempt).model_dump(), settings=gateway_settings())
    assert receipt["ready_for_manual_resume"] and not receipt["automatic_resume"]
    restored = server.WebTaskManager(persist_path=manager._persist_path)
    assert restored._tasks[task.task_id].status == "failed"
    assert not server.execution_terminal_code(restored._tasks[task.task_id].result)
    assert not restored._pending_auto_resume_ids
    task.status = "running"
    with pytest.raises(ValueError, match="stop the task"):
        await manager.execution_reconciliation(task.task_id, evidence(attempt).model_dump(), settings=gateway_settings())


@pytest.mark.asyncio
async def test_abandoned_claim_without_reservation_cannot_freeze_scope(ledger_db):
    first = ledger(ledger_db)
    op = await first.claim("unpaid", "h")
    async with async_sessionmaker(ledger_db)() as session, session.begin():
        row = await session.get(ExecutionOperationModel, op.key)
        row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(budget.ExecutionBudgetError, match="operation_stopped"):
        await first.claim("unpaid", "h")
    await first.claim("new", "h2")


@pytest.mark.asyncio
async def test_all_abandoned_attempts_must_be_closed_before_unlock(ledger_db):
    with budget.bind_execution_scope("test:parallel-abandon"):
        original = ledger(ledger_db, "test:parallel-abandon", execution_max_calls=4)
        a, b = await original.claim("a", "h"), await original.claim("b", "h")
        first = await original.reserve(a, "m", "h", 100)
        second = await original.reserve(b, "m", "h", 100)
    restored = ledger(ledger_db, "test:parallel-abandon", execution_max_calls=4)
    receipt = await restored.reconcile(evidence(first))
    assert receipt["remaining_unknown"] == 1 and receipt["status"] == "unknown"
    with pytest.raises(budget.ExecutionBudgetError, match="result_unknown"):
        await restored.claim("new", "h")
    receipt = await restored.reconcile(evidence(second, reconciliation_id="manual-2"))
    assert receipt["remaining_unknown"] == 0 and receipt["status"] == "active"
    assert receipt["calls_used"] == 2 and receipt["tokens_charged"] == 200


@pytest.mark.asyncio
async def test_reconciliation_does_not_resurrect_cancelled_scope(ledger_db):
    first = ledger(ledger_db)
    op = await first.claim("a", "h")
    attempt = await first.reserve(op, "m", "h", 100)
    await first.settle(attempt, "unknown")
    await first.cancel()
    receipt = await first.reconcile(evidence(attempt))
    assert receipt["status"] == "cancelled" and receipt["tokens_charged"] == 100
    with pytest.raises(budget.ExecutionBudgetError, match="cancelled"):
        await first.claim("new", "h")


@pytest.mark.asyncio
async def test_reconciliation_bound_and_cross_scope_prevent_evidence_reuse(ledger_db):
    first = ledger(ledger_db)
    op = await first.claim("a", "h")
    attempt = await first.reserve(op, "m", "h", 100)
    await first.settle(attempt, "unknown")
    other = ledger(ledger_db, "test:other")
    await other.claim("a", "h")
    with pytest.raises(budget.ExecutionBudgetError, match="reconciliation_conflict"):
        await other.reconcile(evidence(attempt))
    async with async_sessionmaker(ledger_db)() as session, session.begin():
        row = await session.get(ExecutionBudgetModel, first.scope_id)
        row.metadata_json = {**row.metadata_json, "reconciliations": [
            {"reconciliation_id": f"old-{n}", "request_hash": "old"} for n in range(128)]}
    receipt = await first.reconcile(evidence(attempt))
    assert receipt["remaining_unknown"] == 0
    async with async_sessionmaker(ledger_db)() as session:
        row = await session.get(ExecutionBudgetModel, first.scope_id)
        assert len(row.metadata_json["reconciliations"]) == 128
        key = __import__("hashlib").sha256(f"reconciliation.v1\n{first.scope_id}\nold-0".encode()).hexdigest()
        assert (await session.get(ExecutionOperationModel, key)).status == "reconciliation_receipt"
    assert receipt == await first.reconcile(evidence(attempt))


@pytest.mark.asyncio
async def test_worker_manual_reconciliation_archives_only_matching_unknown(ledger_db):
    from bestseller.worker.tasks import clear_reconciled_execution_terminal
    first = ledger(ledger_db)
    op = await first.claim("a", "h")
    attempt = await first.reserve(op, "m", "h", 100)
    await first.settle(attempt, "unknown")
    receipt = await first.reconcile(evidence(attempt))
    project = SimpleNamespace(id="synthetic", metadata_json={"execution_terminal": {
        "terminal": True, "code": "execution_result_unknown"}, "author_setting": "preserve"})
    async with async_sessionmaker(ledger_db)() as session:
        assert await clear_reconciled_execution_terminal(session, project, scope_id=first.scope_id, receipt=receipt)
        assert project.metadata_json["production_paused"]
        assert project.metadata_json["execution_reconciliation_history"][0]["terminal"]["code"] == "execution_result_unknown"
        assert project.metadata_json["author_setting"] == "preserve"
        project.metadata_json["execution_terminal"] = {"terminal": True, "code": "execution_cancelled"}
        assert not await clear_reconciled_execution_terminal(session, project, scope_id=first.scope_id, receipt=receipt)
