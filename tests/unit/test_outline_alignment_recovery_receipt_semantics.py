"""Canonical outer keys must not hide impossible budget receipt semantics."""

from copy import deepcopy
from datetime import UTC, datetime
import hashlib

import pytest
from sqlalchemy import update

from bestseller.infra.db import models as db
from bestseller.services import rewrite_outline_dispatch_recovery as recovery
from bestseller.services.budget import ExecutionReconciliation, bind_execution_scope
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings
from test_outline_alignment_unstarted_recovery import (
    recovery_db, guard_dispatch_boundaries,  # noqa: F401 -- Offline fixture boundaries.
    failed_case, financial_rows, kwargs,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def technical_case(factory, kind):
    case = await failed_case(factory)
    async with factory() as session:
        attempt = await session.get(db.ExecutionAttemptModel, "old-attempt-0")
        attempt.status, attempt.actual_tokens = "reconciled_charged", None
        root = await session.get(db.ExecutionOperationModel, attempt.operation_key)
        root.status, root.result_json = "stopped", None
        budget = await session.get(db.ExecutionBudgetModel, case.scope)
        budget.tokens_charged += 5000
        if kind == "failure":
            record = {"schema": "transport-failure.v1", "attempt_id": attempt.id,
                "operation_key": root.key, "physical_sends": 1, "exception_type": "TimeoutError",
                "operator_interruption": False, "stream": False, "partial_stream_observed": False,
                "elapsed_ms": 12000, "timeout_seconds": 10, "http_status": None,
                "project_id": str(case.project.id), "workflow_run_id": None,
                "logical_role": "planner", "prompt_template": "old-plan", "error_chain": []}
            key = hashlib.sha256(f"transport-failure.v1\n{case.scope}\n{attempt.id}".encode()).hexdigest()
            op = db.ExecutionOperationModel(key=key, scope_id=case.scope, input_hash=attempt.input_hash,
                status="transport_failure_receipt", owner=root.owner, lease_until=root.lease_until,
                result_json=record)
        else:
            request = ExecutionReconciliation(reconciliation_id="old-quarantine", attempt_id=attempt.id,
                expected_input_hash=attempt.input_hash, actor="offline-operator",
                evidence_kind="dispatcher_exit", evidence_sha256="b" * 64,
                decision="quarantine_charged", remote_operation_closed=False,
                local_dispatcher_stopped=True)
            case.request = request.model_dump(mode="json")
            record = {"at": datetime.now(UTC).isoformat(), "actor": request.actor,
                "reconciliation_id": request.reconciliation_id,
                "evidence_kind": request.evidence_kind, "evidence_sha256": request.evidence_sha256,
                "remote_operation_closed": False, "local_dispatcher_stopped": True,
                "request_hash": content_hash(case.request), "receipt": {
                    "status": "active", "scope_id": case.scope, "attempt_id": attempt.id,
                    "reconciliation_id": request.reconciliation_id, "decision": request.decision,
                    "calls_used": budget.calls_used, "tokens_charged": budget.tokens_charged,
                    "remaining_unknown": 0, "credit_released": False, "automatic_resume": False,
                    "remote_outcome": "unknown_quarantined", "operation_replay_allowed": False}}
            key = hashlib.sha256(f"reconciliation.v1\n{case.scope}\nold-quarantine".encode()).hexdigest()
            op = db.ExecutionOperationModel(key=key, scope_id=case.scope, input_hash=record["request_hash"],
                status="reconciliation_receipt", owner="manual-reconciliation",
                lease_until=root.lease_until, result_json=record)
        session.add(op)
        await session.commit()
        case.technical_key = key
    return case


@pytest.mark.parametrize("kind", ["failure", "reconciliation"])
async def test_real_producer_shapes_pass_and_keep_all_charges(recovery_db, kind):  # noqa: F811
    case = await technical_case(recovery_db, kind)
    with bind_execution_scope(case.scope):
        async with recovery_db() as session:
            before = await financial_rows(session, case)
            p = await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case))
        async with recovery_db() as session:
            await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case), execute=True,
                expected_witness_sha256=p["witness_sha256"])
            await session.commit()
            assert before == await financial_rows(session, case)


@pytest.mark.parametrize(("field", "value"), [
    ("physical_sends", 2), ("physical_sends", True), ("physical_sends", -1),
    ("operator_interruption", 1), ("partial_stream_observed", True),
    ("elapsed_ms", -1), ("timeout_seconds", 0), ("timeout_seconds", True),
    ("http_status", 700), ("http_status", True), ("workflow_run_id", "None"),
    ("error_chain", "not structured"), ("invented_grade", True),
])
async def test_well_bound_failure_with_impossible_semantics_is_rejected(recovery_db, field, value):  # noqa: F811
    case = await technical_case(recovery_db, "failure")
    async with recovery_db() as session:
        op = await session.get(db.ExecutionOperationModel, case.technical_key)
        # JSON dirty checking considers 1 == True. Force the actual stored
        # representation so the typed semantic guard, not the ORM, is tested.
        await session.execute(update(db.ExecutionOperationModel).where(
            db.ExecutionOperationModel.key == op.key).values(
                result_json={**op.result_json, field: value}))
        await session.commit()
    with bind_execution_scope(case.scope), pytest.raises(ValueError) as caught:
        async with recovery_db() as session:
            await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case))
    assert str(caught.value) == "alignment_unsent_recovery:technical_receipt_invalid"


@pytest.mark.parametrize("mode", ["request_hash", "remote_closed", "local_stopped", "evidence_kind",
    "evidence_sha256", "actor", "id_type", "receipt_id", "receipt_count", "receipt_tokens",
    "automatic_resume", "remaining_unknown", "undercharged_receipt", "at", "invented_schema"])
async def test_well_bound_reconciliation_replays_original_request_semantics(recovery_db, mode):  # noqa: F811
    case = await technical_case(recovery_db, "reconciliation")
    async with recovery_db() as session:
        op = await session.get(db.ExecutionOperationModel, case.technical_key)
        record = deepcopy(op.result_json)
        request = deepcopy(case.request)
        if mode in {"remote_closed", "local_stopped", "evidence_kind", "evidence_sha256", "actor", "id_type"}:
            field, value = {
                "remote_closed": ("remote_operation_closed", True),
                "local_stopped": ("local_dispatcher_stopped", False),
                "evidence_kind": ("evidence_kind", "billing_record"),
                "evidence_sha256": ("evidence_sha256", "not-a-sha"),
                "actor": ("actor", ""), "id_type": ("reconciliation_id", 7),
            }[mode]
            record[field] = request[field] = value
            record["request_hash"] = content_hash(request)
        elif mode == "request_hash":
            record["request_hash"] = "0" * 64
        elif mode in {"receipt_id", "receipt_count", "receipt_tokens", "automatic_resume", "remaining_unknown"}:
            field, value = {"receipt_id": ("reconciliation_id", "different"),
                "receipt_count": ("calls_used", 0), "receipt_tokens": ("tokens_charged", True),
                "automatic_resume": ("automatic_resume", 1),
                "remaining_unknown": ("remaining_unknown", -1)}[mode]
            record["receipt"][field] = value
        elif mode == "at":
            record["at"] = "not-a-timestamp"
        elif mode == "undercharged_receipt":
            record["receipt"]["tokens_charged"] = 0
        else:
            record["schema"] = "invented"
        # Keep every old outer binding canonical: a later witness mismatch or
        # trivial key mismatch must not be what makes this negative test pass.
        key = hashlib.sha256(
            f"reconciliation.v1\n{case.scope}\n{record['reconciliation_id']}".encode()).hexdigest()
        await session.execute(update(db.ExecutionOperationModel).where(
            db.ExecutionOperationModel.key == op.key).values(key=key,
                input_hash=record["request_hash"], result_json=record))
        await session.commit()
    with bind_execution_scope(case.scope), pytest.raises(ValueError) as caught:
        async with recovery_db() as session:
            await recovery.reconcile_unstarted_outline_alignment(session,
                load_settings(env={}), **kwargs(case))
    assert str(caught.value) == "alignment_unsent_recovery:technical_receipt_invalid"
