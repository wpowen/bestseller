"""File SQLite, actual frozen handoff and durable quarantine; no paid execution."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_stopped_handoff_reconciliation import manager_for, stopped
from test_stopped_handoff_reconciliation import store as store

from bestseller.infra.db import models as m
from bestseller.services import historical_binding_unknown_recovery as recovery
from bestseller.services.budget import (
    ExecutionBudgetError,
    ExecutionLedger,
    ExecutionReconciliation,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def durable_state(store):
    """All paid evidence, frozen sources and workflow rows must remain byte-equivalent."""
    async with store.sessions() as session:
        state = {}
        for model in (
            m.ExecutionBudgetModel,
            m.ExecutionOperationModel,
            m.ExecutionAttemptModel,
            m.WorkflowRunModel,
            m.LlmRunModel,
            m.PlanningArtifactVersionModel,
            m.ChapterModel,
            m.ChapterDraftVersionModel,
            m.BookProductionControlModel,
        ):
            rows = list(await session.scalars(select(model)))
            state[model.__name__] = sorted(
                [
                    {
                        attr.key: deepcopy(getattr(row, attr.key))
                        for attr in row.__mapper__.column_attrs
                    }
                    for row in rows
                ],
                key=lambda item: str(item.get("id", item.get("key", item.get("scope_id")))),
            )
        return state


async def legacy_case(store, tmp_path, monkeypatch):
    case = await stopped(store, tmp_path)
    ledger = ExecutionLedger(store.engine, case.scope, store.settings.budget)
    async with store.sessions() as session:
        owner = m.WorkflowRunModel(
            id=uuid4(),
            project_id=store.pid,
            workflow_type="chapter_pipeline",
            status="failed",
            current_step="review_chapter",
            error_message="workflow_failed",
            metadata_json={
                "execution_scope_id": case.scope,
                "protected_cycle": {"grade": "retained"},
            },
            created_at=datetime.now(UTC),
        )
        session.add(owner)
        session.add(
            m.BookProductionControlModel(
                project_id=store.pid,
                desired_state="run",
                command_serial=3,
                resume_pending=False,
                detail_json={"protected": "operator intent"},
            )
        )
        project = await session.get(m.ProjectModel, store.pid)
        case.terminal = {
            "code": recovery.LEGACY_CODE,
            "terminal": True,
            "diagnostic": {"original": ["do not replace"]},
        }
        project.metadata_json = {
            **project.metadata_json,
            "execution_terminal": deepcopy(case.terminal),
            "production_paused": True,
            "production_pause_reason": recovery.LEGACY_CODE,
            "requires_machine_repair": True,
            "requires_human_review": False,
        }
        await session.commit()
        case.owner = owner.id
        case.previous_project = deepcopy(project.metadata_json)
    await ledger.record_transport_failure(
        "new-timeout-attempt",
        {
            "schema": "transport-failure.v1",
            "project_id": str(store.pid),
            "workflow_run_id": str(case.owner),
            "physical_sends": 1,
            "exception_type": "CancelledError",
            "stream": False,
            "operator_interruption": True,
            "partial_stream_observed": False,
            "error_chain": [
                {"type": "CancelledError", "frames": [{"filename": "llm.py", "function": "send"}]}
            ],
        },
    )
    case.evidence = ExecutionReconciliation(
        reconciliation_id="offline-dispatcher-exit",
        attempt_id="new-timeout-attempt",
        expected_input_hash="actual-unknown-input",
        decision="quarantine_charged",
        evidence_kind="dispatcher_exit",
        evidence_sha256="a" * 64,
        actor="explicit offline operator",
        local_dispatcher_stopped=True,
        remote_operation_closed=False,
    )
    case.receipt = await ledger.reconcile(case.evidence)
    case.manager = await manager_for(store, case, tmp_path, monkeypatch)
    task = case.manager._tasks[case.task]
    task.current_stage = task.error = recovery.LEGACY_CODE
    task.result = {
        "execution_terminal": deepcopy(case.terminal),
        "protected_result": {"prior": "retained"},
    }
    case.previous_task = recovery.task_snapshot(task)
    return case


async def test_manual_migration_preserves_original_terminal_controls_and_all_financial_evidence(
    store, tmp_path, monkeypatch
):
    case = await legacy_case(store, tmp_path, monkeypatch)
    before = await durable_state(store)
    # Inspection is read-only and cannot silently migrate the legacy projection.
    assert (await case.manager.execution_reconciliation(case.task, settings=store.settings))[
        "status"
    ] == "active"
    assert recovery.task_snapshot(case.manager._tasks[case.task]) == case.previous_task
    async with store.sessions() as session:
        assert (await session.get(m.ProjectModel, store.pid)).metadata_json == case.previous_project
    outcome = await case.manager.execution_reconciliation(
        case.task, case.evidence.model_dump(mode="json"), settings=store.settings
    )
    assert outcome == {**case.receipt, "ready_for_manual_resume": True, "automatic_resume": False}
    async with store.sessions() as session:
        project = await session.get(m.ProjectModel, store.pid)
        metadata = project.metadata_json
        assert project.status == "paused" and metadata["production_paused"] is True
        assert metadata["production_pause_reason"] == "execution_reconciled_manual_resume_required"
        assert "execution_terminal" not in metadata
        (proof,) = metadata[recovery.HISTORY]
        assert proof["schema"] == recovery.SCHEMA and proof["previous_task"] == case.previous_task
        assert proof["previous_project"]["execution_terminal"] == case.terminal
        assert proof["previous_project"]["production_pause_reason"] == recovery.LEGACY_CODE
        assert proof["previous_project"]["requires_human_review"] is False
        assert proof["previous_project"]["requires_machine_repair"] is True
        assert proof["receipt"] == case.receipt
        assert (
            metadata["execution_reconciliation_history"][0]["terminal"]["code"]
            == recovery.UNKNOWN_CODE
        )
    task = case.manager._tasks[case.task]
    assert task.status == "failed" and task.error is None
    assert task.current_stage == "execution_reconciled_manual_resume_required"
    assert task.result["protected_result"] == case.previous_task["result"]["protected_result"]
    assert task.result[recovery.HISTORY] == [proof]
    assert task.payload == case.previous_task["payload"]
    event = task.progress_events[-1]
    assert (
        event["stage"] == "execution_reconciled"
        and event["payload"]["previous_terminal"] == case.terminal
    )
    assert event["payload"]["historical_reclassification"] == proof
    assert event in task.milestone_events
    assert await durable_state(store) == before
    # Replaying the same request creates neither a second migration nor funding.
    assert (
        await case.manager.execution_reconciliation(
            case.task, case.evidence.model_dump(mode="json"), settings=store.settings
        )
        == outcome
    )
    assert await durable_state(store) == before
    async with store.sessions() as session:
        project = await session.get(m.ProjectModel, store.pid)
        assert (
            len(project.metadata_json[recovery.HISTORY])
            == len(project.metadata_json["execution_reconciliation_history"])
            == 1
        )
    assert len(task.result[recovery.HISTORY]) == 1 and store.calls == []


@pytest.mark.parametrize(
    "fault",
    [
        "task_terminal",
        "task_terminal_flag",
        "task_top_code",
        "task_stage",
        "task_cancelled",
        "task_slug",
        "task_control",
        "project_terminal",
        "project_terminal_flag",
        "project_pause",
        "project_other_stop",
        "project_scope",
        "canonical",
        "intent",
        "handoff",
        "handoff_missing",
        "quality_unsettled",
        "receipt_scope",
        "receipt_hash",
        "receipt_evidence",
        "receipt_local",
        "receipt_remote",
        "receipt_missing",
        "receipt_replay",
        "receipt_credit",
        "receipt_outcome",
        "receipt_counters",
        "receipt_remaining",
        "failure_scope",
        "failure_hash",
        "failure_attempt",
        "failure_operation",
        "failure_project",
        "failure_schema",
        "failure_sends",
        "failure_cancelled",
        "failure_chain",
        "failure_operator",
        "failure_missing",
        "attempt_status",
        "attempt_actual",
        "attempt_hash",
        "root_status",
        "root_result",
        "workflow_scope",
        "workflow_project",
        "workflow_active",
        "workflow_completed",
        "newer_workflow",
        "other_active_owner",
        "completed_transport",
        "partial_return",
        "new_unknown",
        "new_pending",
    ],
)
async def test_unproven_or_changed_identity_cannot_clear_or_rewrite_evidence(
    store, tmp_path, monkeypatch, fault
):
    case = await legacy_case(store, tmp_path, monkeypatch)
    task = case.manager._tasks[case.task]
    if fault == "task_terminal":
        task.result = {"execution_terminal": {"code": "execution_cancelled", "terminal": True}}
    if fault == "task_terminal_flag":
        task.result = {
            **task.result,
            "execution_terminal": {"code": recovery.LEGACY_CODE, "terminal": False},
        }
    if fault == "task_top_code":
        task.result = {**task.result, "code": "execution_cancelled", "terminal": True}
    if fault == "task_stage":
        task.current_stage = "another_stop"
    if fault == "task_cancelled":
        task.cancel_requested = True
    if fault == "task_slug":
        task.project_slug = "another-project"
    if fault == "task_control":
        task.payload = {**task.payload, "concept_seed": "changed author intent"}
    async with store.sessions() as session:
        project = await session.get(m.ProjectModel, store.pid)
        metadata = deepcopy(project.metadata_json)
        if fault == "project_terminal":
            metadata["execution_terminal"] = {"code": "execution_cancelled", "terminal": True}
        if fault == "project_terminal_flag":
            metadata["execution_terminal"] = {"code": recovery.LEGACY_CODE, "terminal": False}
        if fault == "project_pause":
            metadata["production_pause_reason"] = "operator_manual_pause"
        if fault == "project_other_stop":
            metadata["execution_budget"] = {"code": "execution_cancelled", "terminal": True}
        if fault == "project_scope":
            metadata["execution_scope_id"] = "project:other"
        if fault == "canonical":
            metadata["creation_canonical_snapshot_hash"] = "changed"
        if fault == "intent":
            metadata["creation_intent_contract_hash"] = "changed"
        if fault == "handoff_missing":
            metadata["execution_phase_handoff_receipts"] = []
        project.metadata_json = metadata
        if fault == "handoff":
            handoff = await session.get(
                m.ExecutionOperationModel, case.payload["_execution_phase_handoff"]
            )
            handoff.result_json = {**handoff.result_json, "binding": {}}
        if fault == "quality_unsettled":
            (await session.get(m.ExecutionAttemptModel, "fixture-attempt")).actual_tokens = None
        saved = await session.get(
            m.ExecutionOperationModel,
            recovery._key("reconciliation.v1", case.scope, case.receipt["reconciliation_id"]),
        )
        if fault == "receipt_scope":
            saved.scope_id = "project:other"
        if fault == "receipt_hash":
            saved.input_hash = "changed"
        record = deepcopy(saved.result_json)
        if fault == "receipt_evidence":
            record["evidence_sha256"] = "b" * 64
        if fault == "receipt_local":
            record["local_dispatcher_stopped"] = False
        if fault == "receipt_remote":
            record["remote_operation_closed"] = True
        if fault == "receipt_replay":
            record["receipt"]["operation_replay_allowed"] = True
        if fault == "receipt_credit":
            record["receipt"]["credit_released"] = True
        if fault == "receipt_outcome":
            record["receipt"]["remote_outcome"] = "completed"
        if fault == "receipt_counters":
            record["receipt"]["calls_used"] += 1
        if fault == "receipt_remaining":
            record["receipt"]["remaining_unknown"] = 1
        saved.result_json = record
        if fault == "receipt_missing":
            await session.delete(saved)
        failure = await session.get(
            m.ExecutionOperationModel,
            recovery._key("transport-failure.v1", case.scope, "new-timeout-attempt"),
        )
        if fault == "failure_scope":
            failure.scope_id = "project:other"
        if fault == "failure_hash":
            failure.input_hash = "changed"
        diagnostic = deepcopy(failure.result_json)
        field_changes = {
            "failure_attempt": ("attempt_id", "other"),
            "failure_operation": ("operation_key", "other"),
            "failure_project": ("project_id", str(uuid4())),
            "failure_schema": ("schema", "other"),
            "failure_sends": ("physical_sends", 0),
            "failure_cancelled": ("exception_type", "ReadTimeout"),
            "failure_chain": ("error_chain", []),
            "failure_operator": ("operator_interruption", False),
        }
        if fault in field_changes:
            key, value = field_changes[fault]
            diagnostic[key] = value
        failure.result_json = diagnostic
        if fault == "failure_missing":
            await session.delete(failure)
        attempt = await session.get(m.ExecutionAttemptModel, "new-timeout-attempt")
        if fault == "attempt_status":
            attempt.status = "unknown"
        if fault == "attempt_actual":
            attempt.actual_tokens = 100
        if fault == "attempt_hash":
            attempt.input_hash = "changed"
        root = await session.get(m.ExecutionOperationModel, "new-timeout")
        if fault == "root_status":
            root.status = "unknown"
        if fault == "root_result":
            root.result_json = {"content": "not an unknown result"}
        owner = await session.get(m.WorkflowRunModel, case.owner)
        if fault == "workflow_scope":
            owner.metadata_json = {"execution_scope_id": "project:other"}
        if fault == "workflow_project":
            owner.project_id = uuid4()
        if fault == "workflow_active":
            owner.status = "running"
        if fault == "workflow_completed":
            owner.status = "completed"
        if fault in ("newer_workflow", "other_active_owner"):
            session.add(
                m.WorkflowRunModel(
                    project_id=store.pid,
                    workflow_type="chapter_pipeline",
                    status="running" if fault == "other_active_owner" else "failed",
                    metadata_json={"execution_scope_id": case.scope},
                    created_at=owner.created_at + timedelta(seconds=1),
                )
            )
        if fault in ("completed_transport", "partial_return"):
            kind = "transport-result.v1" if fault == "completed_transport" else "provider-return.v1"
            session.add(
                m.ExecutionOperationModel(
                    key=recovery._key(kind, case.scope, attempt.id),
                    scope_id=case.scope,
                    input_hash=attempt.input_hash,
                    status="transport_result"
                    if fault == "completed_transport"
                    else "unusable_provider_return",
                    owner=root.owner,
                    lease_until=root.lease_until,
                    result_json={"preserve": True},
                )
            )
        if fault in ("new_unknown", "new_pending"):
            session.add(
                m.ExecutionOperationModel(
                    key="new-remote-operation",
                    scope_id=case.scope,
                    input_hash="new-input",
                    status="unknown" if fault == "new_unknown" else "pending",
                    owner="new",
                    lease_until=root.lease_until,
                )
            )
            session.add(
                m.ExecutionAttemptModel(
                    id="new-remote-attempt",
                    scope_id=case.scope,
                    operation_key="new-remote-operation",
                    input_hash="new-input",
                    status="unknown" if fault == "new_unknown" else "reserved",
                    model_name=attempt.model_name,
                    reserved_tokens=1000,
                    actual_tokens=None,
                    created_at=datetime.now(UTC),
                )
            )
            if fault == "new_unknown":
                (await session.get(m.ExecutionBudgetModel, case.scope)).status = "unknown"
        await session.commit()
        previous_metadata = deepcopy(project.metadata_json)
    previous_task = recovery.task_snapshot(task)
    before = await durable_state(store)
    try:
        outcome = await case.manager.execution_reconciliation(
            case.task, case.evidence.model_dump(mode="json"), settings=store.settings
        )
    except (ValueError, ExecutionBudgetError):
        pass
    else:
        assert outcome["ready_for_manual_resume"] is False
    assert recovery.task_snapshot(task) == previous_task
    async with store.sessions() as session:
        assert (await session.get(m.ProjectModel, store.pid)).metadata_json == previous_metadata
    assert await durable_state(store) == before and store.calls == []
