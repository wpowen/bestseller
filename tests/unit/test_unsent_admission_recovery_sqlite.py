"""Generic history repair with original zero-send proofs, never free replay."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, WorkflowRunModel
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.retry_ledger import execution_terminal_code
from bestseller.web import server
from test_web_unsent_state_sqlite import prepare
from gateway_ledger_support import gateway_ledger as gateway_ledger

pytestmark = pytest.mark.unit


def setup_recovery(monkeypatch, tmp_path, engine):
    manager, task, snapshot = prepare(monkeypatch, tmp_path, engine)
    workflow_id = uuid4()
    task.payload["_conception_workflow_run_id"] = str(workflow_id)
    task.payload["authored_contract"] = "museum opening must remain public"
    task.project_slug = "allocated-public-museum-story"
    manager._mark_execution_terminal(task.task_id, ExecutionBudgetError("execution_ledger_unavailable"))

    async def enrich():
        metadata = MetaData()
        table = WorkflowRunModel.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type, column.server_default = JSON(), None
        table.constraints = {c for c in table.constraints if c.__class__.__name__ != "ForeignKeyConstraint"}
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(WorkflowRunModel(id=workflow_id, workflow_type="conception_initial",
                status="failed", error_message="execution_provider_not_sent", metadata_json={}))
            attempt = await session.scalar(select(ExecutionAttemptModel))
            failure_key = hashlib.sha256(f"transport-failure.v1\n{attempt.scope_id}\n{attempt.id}".encode()).hexdigest()
            failure = await session.get(ExecutionOperationModel, failure_key)
            failure.result_json = {**failure.result_json, "workflow_run_id": str(workflow_id),
                "operator_interruption": False}
            await session.commit()
            return attempt.id, failure_key

    attempt_id, failure_key = asyncio.run(enrich())
    return manager, task, snapshot, {"expected_attempt_id": attempt_id, "dry_run": False}, failure_key


def test_recovery_is_durable_idempotent_and_does_not_dispatch_or_change_budget(monkeypatch, tmp_path, gateway_ledger):
    manager, task, snapshot, payload, _ = setup_recovery(monkeypatch, tmp_path, gateway_ledger)
    before, original = snapshot(), deepcopy(task.to_dict())
    preview = asyncio.run(manager.recover_unsent_admission(task.task_id, {**payload, "dry_run": True}))
    assert preview["ready_for_manual_resume"]
    assert task.to_dict() == original
    outcome = asyncio.run(manager.recover_unsent_admission(task.task_id, payload))
    assert outcome["ready_for_manual_resume"]
    assert execution_terminal_code(task.result) is None
    assert outcome["receipt"]["previous_projection"]["terminal"] == original["result"]["execution_terminal"]
    assert task.payload == original["payload"]
    assert task.progress_events[:-1] == original["progress_events"]
    assert snapshot() == before
    after = deepcopy(task.to_dict())
    replay = asyncio.run(manager.recover_unsent_admission(task.task_id, payload))
    assert replay["receipt"] == outcome["receipt"]
    assert task.to_dict() == after
    restored = server.WebTaskManager(persist_path=manager._persist_path)
    assert restored._tasks[task.task_id].result == task.result
    assert not restored._pending_auto_resume_ids


def test_expired_window_is_projected_as_expired_without_amending_it(monkeypatch, tmp_path, gateway_ledger):
    manager, task, snapshot, payload, _ = setup_recovery(monkeypatch, tmp_path, gateway_ledger)
    async def expire():
        async with AsyncSession(gateway_ledger) as session:
            row = await session.get(ExecutionBudgetModel, f"conception:{task.task_id}")
            row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
    asyncio.run(expire())
    before = snapshot()
    outcome = asyncio.run(manager.recover_unsent_admission(task.task_id, payload))
    assert not outcome["ready_for_manual_resume"]
    assert execution_terminal_code(task.result) == "execution_deadline_exhausted"
    assert outcome["receipt"]["previous_projection"]["terminal"]["code"] == "execution_ledger_unavailable"
    assert snapshot() == before


@pytest.mark.parametrize("renewal_kind", ["audited_time_only", "audited_from_now", "raw_deadline", "extra_calls"])
def test_expired_projection_requires_exact_time_only_operator_authorization(monkeypatch, tmp_path, gateway_ledger, renewal_kind):
    from bestseller.services.execution_authorization import authorize_execution_phase
    manager, task, snapshot, payload, _ = setup_recovery(monkeypatch, tmp_path, gateway_ledger)
    scope = f"conception:{task.task_id}"
    async def expire():
        async with AsyncSession(gateway_ledger) as session:
            row = await session.get(ExecutionBudgetModel, scope)
            row.deadline_at = datetime.now(timezone.utc) - timedelta(hours=3) if renewal_kind == "audited_from_now" else datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
    asyncio.run(expire())
    original = asyncio.run(manager.recover_unsent_admission(task.task_id, payload))
    before = snapshot()
    if renewal_kind == "raw_deadline":
        async def raw_edit():
            async with AsyncSession(gateway_ledger) as session:
                row = await session.get(ExecutionBudgetModel, scope)
                row.deadline_at = datetime.now(timezone.utc) + timedelta(hours=1)
                await session.commit()
        asyncio.run(raw_edit())
    else:
        asyncio.run(authorize_execution_phase(gateway_ledger, scope_id=scope,
            idempotency_key="explicit-time-renewal", actor="human-fixture", reason="same original acceptance",
            authorization_ref="isolated-human-answer", expected_max_calls=before[3], expected_max_tokens=before[4],
            additional_calls=int(renewal_kind == "extra_calls"), additional_tokens=0, additional_seconds=3600,
            renew_from_now=renewal_kind == "audited_from_now"))
    renewed_budget = snapshot()
    if renewal_kind not in {"audited_time_only", "audited_from_now"}:
        with pytest.raises(ValueError, match="time-only authorization"):
            asyncio.run(manager.recover_unsent_admission(task.task_id, payload))
        assert execution_terminal_code(task.result) == "execution_deadline_exhausted"
        assert snapshot() == renewed_budget
        return
    outcome = asyncio.run(manager.recover_unsent_admission(task.task_id, payload))
    assert outcome["ready_for_manual_resume"]
    assert outcome["receipt"] == original["receipt"]
    assert outcome["time_renewal_receipt"]["automatic_resume"] is False
    assert task.result["last_execution_time_renewal"] == outcome["time_renewal_receipt"]
    assert execution_terminal_code(task.result) is None
    assert snapshot() == renewed_budget
    assert before[1:5] == renewed_budget[1:5]
    stable = deepcopy(task.to_dict())
    assert asyncio.run(manager.recover_unsent_admission(task.task_id, payload))["ready_for_manual_resume"]
    assert task.to_dict() == stable


@pytest.mark.parametrize("corrupt", ["sent", "bool_send", "workflow", "wrong_attempt", "unknown", "cancelled", "interrupted", "missing_receipt", "paid"])
def test_migration_rejects_missing_or_conflicting_original_evidence(monkeypatch, tmp_path, gateway_ledger, corrupt):
    manager, task, snapshot, payload, failure_key = setup_recovery(monkeypatch, tmp_path, gateway_ledger)
    async def change():
        async with AsyncSession(gateway_ledger) as session:
            failure = await session.get(ExecutionOperationModel, failure_key)
            data = dict(failure.result_json)
            if corrupt in {"sent", "bool_send"}:
                data["physical_sends"] = 1 if corrupt == "sent" else False
            if corrupt == "workflow":
                data["workflow_run_id"] = str(uuid4())
            if corrupt == "interrupted":
                data["operator_interruption"] = True
            if corrupt in {"unknown", "cancelled"}:
                row = await session.get(ExecutionBudgetModel, f"conception:{task.task_id}")
                row.status = corrupt
            if corrupt == "paid":
                attempt = await session.get(ExecutionAttemptModel, payload["expected_attempt_id"])
                attempt.status, attempt.actual_tokens = "completed", 12
            failure.result_json = data
            flag_modified(failure, "result_json")
            if corrupt == "missing_receipt":
                await session.delete(failure)
            await session.commit()
    asyncio.run(change())
    if corrupt == "wrong_attempt":
        payload["expected_attempt_id"] = "missing"
    before, original = snapshot(), deepcopy(task.to_dict())
    with pytest.raises(ValueError):
        asyncio.run(manager.recover_unsent_admission(task.task_id, payload))
    assert task.to_dict() == original
    assert snapshot() == before
