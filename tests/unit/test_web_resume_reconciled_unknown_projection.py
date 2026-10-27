"""Explicit RUN completes only the durable, fully charged reconciliation gap."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.schema import CreateTable
from test_web_project_resume_status import (
    adopted_db,  # noqa: F401
    arrange,
    read,
)
from test_web_project_resume_status import (
    protected as web_protected,
)
from test_web_project_resume_status import (
    store as resume_store,  # noqa: F401
)

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import llm
from bestseller.services import web_project_resume as web_resume
from bestseller.services.budget import (
    ExecutionBudgetError,
    ExecutionLedger,
    ExecutionReconciliation,
)
from bestseller.settings import BudgetSettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def store(resume_store):  # noqa: F811
    def begin(connection):
        # Make SQLite enforce the outer transaction around a SAVEPOINT too.
        connection.exec_driver_sql("BEGIN")

    event.listen(resume_store.session.bind.sync_engine, "begin", begin)
    async with resume_store.session.bind.begin() as connection:
        await connection.exec_driver_sql(
            str(
                CreateTable(LlmRunModel.__table__).compile(dialect=connection.dialect),
            ).replace("::jsonb", "")
        )
    resume_store.model_calls = []
    try:
        yield resume_store
    finally:
        event.remove(resume_store.session.bind.sync_engine, "begin", begin)
        assert resume_store.model_calls == []


async def protected(store):
    async with store.factory() as session:
        runs = list((await session.execute(select(*LlmRunModel.__table__.columns))).mappings())
    return [*await web_protected(store), runs]


def key(kind, scope, identifier):
    return hashlib.sha256(f"{kind}\n{scope}\n{identifier}".encode()).hexdigest()


async def crash_gap(store, monkeypatch, *, status="paused", extra_paid=False):
    """Real ledger reconciliation committed; deliberately omit the project half."""

    async def no_model(*args, **kwargs):
        store.model_calls.append(True)
        raise AssertionError("explicit RUN cannot call a model")

    monkeypatch.setattr(llm, "complete_text", no_model)
    await arrange(store, intent="pause")
    scope = store.project.metadata_json["execution_scope_id"]
    workflow_id, attempt_id, operation_key = uuid4(), uuid4().hex, uuid4().hex
    input_hash = hashlib.sha256(uuid4().bytes).hexdigest()
    deadline = datetime.now(UTC) + timedelta(days=1)
    terminal = {
        "terminal": True,
        "code": "execution_result_unknown",
        "workflow_id": str(workflow_id),
    }
    prior_history = [{"terminal": {"code": "old-stop"}, "receipt": {"old": True}}]
    store.project.status = status
    store.project.metadata_json = {
        **store.project.metadata_json,
        "execution_terminal": terminal,
        "execution_reconciliation_history": prior_history,
        "production_paused": True,
        "production_pause_reason": "execution_result_unknown",
        "retained_pending_review": {"draft": str(store.draft.id)},
    }
    store.session.add_all(
        [
            WorkflowRunModel(
                id=workflow_id,
                project_id=store.project.id,
                workflow_type="generate_volume_plan",
                status="failed",
                error_message="execution_result_unknown",
                metadata_json={"execution_scope_id": scope},
            created_at=datetime.now(UTC) - timedelta(minutes=1),
            ),
            ExecutionBudgetModel(
                scope_id=scope,
                status="unknown",
                max_calls=20,
                max_tokens=100000,
                calls_used=3,
                tokens_charged=6000,
                deadline_at=deadline,
                metadata_json={"retained": [None, "fee"]},
            ),
            ExecutionOperationModel(
                key=operation_key,
                scope_id=scope,
                input_hash=input_hash,
                status="unknown",
                owner="stopped-dispatcher",
                lease_until=deadline,
                result_json=None,
            ),
            ExecutionAttemptModel(
                id=attempt_id,
                operation_key=operation_key,
                scope_id=scope,
                input_hash=input_hash,
                model_name="offline-proof-model",
                reserved_tokens=2000,
                actual_tokens=None,
                status="unknown",
                created_at=datetime.now(UTC),
            ),
        ]
    )
    await store.session.commit()
    ledger = ExecutionLedger(store.session.bind, scope, BudgetSettings())
    paid = [(attempt_id, operation_key, input_hash)]
    if extra_paid:
        second_id, second_root = uuid4().hex, uuid4().hex
        second_hash = hashlib.sha256(uuid4().bytes).hexdigest()
        store.session.add_all(
            [
                ExecutionOperationModel(
                    key=second_root,
                    scope_id=scope,
                    input_hash=second_hash,
                    status="unknown",
                    owner="second-stopped-dispatcher",
                    lease_until=deadline,
                ),
                ExecutionAttemptModel(
                    id=second_id,
                    operation_key=second_root,
                    scope_id=scope,
                    input_hash=second_hash,
                    model_name="offline-proof-model",
                    reserved_tokens=2000,
                    actual_tokens=None,
                    status="unknown",
                    created_at=datetime.now(UTC),
                ),
            ]
        )
        await store.session.commit()
        paid.append((second_id, second_root, second_hash))
    for aid, _root, _hash in paid:
        await ledger.record_transport_failure(
            aid,
            {
                "schema": "transport-failure.v1",
                "physical_sends": 1,
                "project_id": str(store.project.id),
                "workflow_run_id": str(workflow_id),
                "exception_type": "ReadTimeout",
                "operator_interruption": False,
            },
        )
    for aid, _root, expected_hash in paid:
        receipt = await ledger.reconcile(
            ExecutionReconciliation(
                reconciliation_id=uuid4().hex,
                attempt_id=aid,
                expected_input_hash=expected_hash,
                decision="quarantine_charged",
                evidence_kind="dispatcher_exit",
                evidence_sha256="a" * 64,
                actor="offline explicit operator",
                remote_operation_closed=False,
                local_dispatcher_stopped=True,
            )
        )
    attempt_id, operation_key, input_hash = paid[-1]
    # A newer successful unrelated workflow must not become the terminal owner.
    store.session.add(
        WorkflowRunModel(
            project_id=store.project.id,
            workflow_type="chapter_review",
            status="completed",
            metadata_json={"retained": True},
            created_at=datetime.now(UTC),
        )
    )
    await store.session.commit()
    assert receipt["remaining_unknown"] == 0 and receipt["status"] == "active"
    return SimpleNamespace(
        scope=scope,
        workflow_id=workflow_id,
        attempt_id=attempt_id,
        operation_key=operation_key,
        input_hash=input_hash,
        terminal=deepcopy(terminal),
        prior_history=deepcopy(prior_history),
        receipt=receipt,
        receipt_key=key("reconciliation.v1", scope, receipt["reconciliation_id"]),
        failure_key=key("transport-failure.v1", scope, attempt_id),
    )


async def explicit(store, entry=None):
    async with store.factory() as session:
        async with session.begin():
            return await (entry or web_resume.request_explicit_web_resume)(
                session,
                slug=store.project.slug,
                actor="web_dashboard",
                reason="explicit RUN",
            )


async def all_rows(store):
    async with store.factory() as session:
        return [
            list((await session.execute(select(*model.__table__.columns))).mappings())
            for model in (ProjectModel, BookProductionControlModel)
        ] + await protected(store)


@pytest.mark.parametrize("status", ["paused", "writing"])
async def test_committed_ledger_crash_gap_normal_run_exactly_once(store, monkeypatch, status):
    proof = await crash_gap(store, monkeypatch, status=status)
    if status == "writing":
        # The old early no-op must not conceal a stale terminal either.
        control = await store.session.get(BookProductionControlModel, store.project.id)
        control.desired_state = "run"
        await store.session.commit()
    before = await protected(store)
    assert await explicit(store)
    project, control = await read(store)
    assert project.status == "writing"
    assert "execution_terminal" not in project.metadata_json
    assert "production_paused" not in project.metadata_json
    assert "production_pause_reason" not in project.metadata_json
    assert project.metadata_json["retained_pending_review"] == {"draft": str(store.draft.id)}
    assert project.metadata_json["execution_reconciliation_history"] == [
        *proof.prior_history,
        {"terminal": proof.terminal, "receipt": proof.receipt},
    ]
    assert project.metadata_json["reconciled_pause_resume_receipts"] == [
        {
            "command_serial": 8,
            "actor": "web_dashboard",
            "reconciliation_id": proof.receipt["reconciliation_id"],
        }
    ]
    assert control.desired_state == "run" and control.command_serial == 8
    assert control.detail_json["web_resume_status_history"][0] == {"old": True}
    assert len(control.detail_json["web_resume_status_history"]) == 2
    assert await protected(store) == before
    after = await all_rows(store)
    assert not await explicit(store)
    assert await all_rows(store) == after


async def break_proof(store, proof, fault):
    session = store.session
    budget = await session.get(ExecutionBudgetModel, proof.scope, populate_existing=True)
    attempt = await session.get(ExecutionAttemptModel, proof.attempt_id, populate_existing=True)
    operation = await session.get(
        ExecutionOperationModel, proof.operation_key, populate_existing=True
    )
    saved = await session.get(ExecutionOperationModel, proof.receipt_key, populate_existing=True)
    failed = await session.get(ExecutionOperationModel, proof.failure_key, populate_existing=True)
    project = await session.get(ProjectModel, store.project.id, populate_existing=True)
    record = deepcopy(saved.result_json)
    receipt = record["receipt"]
    if fault.startswith("receipt:"):
        field, value = {
            "scope": ("scope_id", "project:wrong-scope"),
            "decision": ("decision", "abandon_charged"),
            "attempt": ("attempt_id", "wrong-attempt"),
            "status": ("status", "unknown"),
            "remaining": ("remaining_unknown", 1),
            "bool_remaining": ("remaining_unknown", False),
            "replay": ("operation_replay_allowed", True),
            "credit": ("credit_released", True),
            "automatic": ("automatic_resume", True),
            "outcome": ("remote_outcome", "closed"),
            "identifier": ("reconciliation_id", "wrong-id"),
            "calls": ("calls_used", 999),
            "tokens": ("tokens_charged", 0),
        }[fault.split(":")[1]]
        receipt[field] = value
    elif fault.startswith("record:"):
        field, value = {
            "remote": ("remote_operation_closed", True),
            "dispatcher": ("local_dispatcher_stopped", False),
            "evidence": ("evidence_kind", "provider_receipt"),
            "hash": ("evidence_sha256", "malformed"),
            "fingerprint": ("request_hash", "c" * 64),
            "actor": ("actor", "changed actor"),
            "timestamp": ("at", None),
        }[fault.split(":")[1]]
        record[field] = value
    elif fault.startswith("failure:"):
        field, value = {
            "workflow": ("workflow_run_id", str(uuid4())),
            "attempt": ("attempt_id", "wrong"),
            "project": ("project_id", str(uuid4())),
            "operation": ("operation_key", "wrong"),
            "schema": ("schema", "wrong"),
            "zero_send": ("physical_sends", 0),
            "bool_send": ("physical_sends", True),
            "multi_send": ("physical_sends", 2),
        }[fault.split(":")[1]]
        failed.result_json = {**failed.result_json, field: value}
    elif fault.startswith("budget:"):
        state = fault.split(":")[1]
        if state == "expired":
            budget.deadline_at = datetime.now(UTC) - timedelta(days=1)
        elif state == "cancel_flag":
            budget.metadata_json = {**budget.metadata_json, "cancel_requested": True}
        elif state in {"authorization_paused", "authorization_closed"}:
            budget.metadata_json = {
                **budget.metadata_json,
                "provider_authorization": {
                    "mode": "completion_bound",
                    "authorization_ref": "original-only",
                    "models": ["offline"],
                    "endpoint_hosts": ["offline.invalid"],
                    "objective": "original",
                    "status": state.removeprefix("authorization_"),
                },
            }
        else:
            budget.status = state
    elif fault.startswith("new_attempt:"):
        state = fault.split(":")[1]
        root_closed = state.endswith("_closed_root")
        state = state.removesuffix("_closed_root")
        root_key = uuid4().hex
        session.add(
            ExecutionOperationModel(
                key=root_key,
                scope_id=proof.scope,
                input_hash="new-input",
                owner="new-owner",
                status="stopped" if root_closed else "unknown" if state == "unknown" else "pending",
                lease_until=budget.deadline_at,
            )
        )
        session.add(
            ExecutionAttemptModel(
                id=uuid4().hex,
                scope_id=proof.scope,
                operation_key=root_key,
                input_hash="new-input",
                status=state,
                model_name="offline-proof-model",
                reserved_tokens=2000,
                actual_tokens=None,
                created_at=datetime.now(UTC),
            )
        )
    elif fault.startswith("paid_return:"):
        kind = fault.split(":")[1]
        session.add(
            ExecutionOperationModel(
                key=key(kind, proof.scope, proof.attempt_id),
                scope_id=proof.scope,
                input_hash=proof.input_hash,
                status="transport_result",
                owner=operation.owner,
                lease_until=budget.deadline_at,
                result_json={"retained": "late paid return"},
            )
        )
    elif fault.startswith("alias_return:"):
        _, binding, position, destination = fault.split(":")
        linked = {binding: proof.operation_key if binding == "operation_key" else proof.attempt_id}
        session.add(
            ExecutionOperationModel(
                key=uuid4().hex,
                scope_id=proof.scope if destination == "same" else "project:other",
                input_hash="not-used-for-attribution",
                status="transport_result",
                owner="return-owner",
                lease_until=budget.deadline_at,
                result_json=linked if position == "direct" else {"receipt": linked},
            )
        )
    elif fault.startswith("llm_run:"):
        session.add(
            LlmRunModel(
                project_id=store.project.id if fault.endswith("same") else None,
                logical_role="critic",
                provider="offline-proof",
                model_name="offline-proof-model",
                metadata_json={"execution_receipt": {"operation_key": proof.operation_key}},
            )
        )
    elif fault.startswith("project:"):
        field, value = {
            "structural": ("structural_repair_required", True),
            "audit": ("generation_resume_blocked_until_repair_audit", True),
            "other_pause": ("production_pause_reason", "unknown_safety_stop"),
            "malformed_pause": ("production_pause_reason", ["execution_result_unknown"]),
            "other_terminal": (
                "execution_terminal",
                {"terminal": True, "code": "execution_cancelled"},
            ),
            "another_stop": (
                "execution_budget",
                {"terminal": True, "code": "execution_authorization_paused"},
            ),
            "history": ("execution_reconciliation_history", {}),
            "resume_history": ("reconciled_pause_resume_receipts", "malformed"),
            "workflow_missing": (
                "execution_terminal",
                {"terminal": True, "code": "execution_result_unknown"},
            ),
            "workflow_malformed": (
                "execution_terminal",
                {"terminal": True, "code": "execution_result_unknown", "workflow_id": []},
            ),
        }[fault.split(":")[1]]
        project.metadata_json = {**project.metadata_json, field: value}
    elif fault == "missing_durable":
        await session.delete(saved)
    elif fault == "wrong_durable_key":
        saved.key = "e" * 64
    elif fault == "wrong_durable_scope":
        saved.scope_id = "project:wrong-scope"
    elif fault == "wrong_durable_status":
        saved.status = "completed"
    elif fault == "wrong_durable_hash":
        saved.input_hash = "d" * 64
    elif fault == "durable_index_mismatch":
        saved.result_json = {**saved.result_json, "extra": True}
    elif fault == "missing_index":
        budget.metadata_json = {**budget.metadata_json, "reconciliations": []}
    elif fault == "malformed_index":
        budget.metadata_json = {**budget.metadata_json, "reconciliations": "malformed"}
    elif fault == "duplicate_index":
        budget.metadata_json = {**budget.metadata_json, "reconciliations": [record, record]}
    elif fault == "missing_ledger":
        await session.delete(budget)
    elif fault == "missing_attempt":
        await session.delete(attempt)
    elif fault == "attempt_unknown":
        attempt.status = "unknown"
    elif fault == "attempt_actual_tokens":
        attempt.actual_tokens = 123
    elif fault == "attempt_wrong_hash":
        attempt.input_hash = "different-input"
    elif fault == "attempt_wrong_scope":
        attempt.scope_id = "project:wrong-scope"
    elif fault == "attempt_zero_reservation":
        attempt.reserved_tokens = 0
    elif fault == "operation_unknown":
        operation.status = "unknown"
    elif fault == "operation_return":
        operation.result_json = {"paid": True}
    elif fault == "operation_wrong_scope":
        operation.scope_id = "project:wrong-scope"
    elif fault == "operation_wrong_hash":
        operation.input_hash = "different-input"
    elif fault == "missing_failure":
        await session.delete(failed)
    elif fault == "failure_wrong_key":
        failed.key = "f" * 64
    elif fault == "failure_wrong_hash":
        failed.input_hash = "different-input"
    elif fault == "failure_wrong_owner":
        failed.owner = "unrelated-dispatcher"
    elif fault == "ambiguous_failure":
        session.add(
            ExecutionOperationModel(
                key=uuid4().hex,
                scope_id=proof.scope,
                input_hash=proof.input_hash,
                status=failed.status,
                owner=failed.owner,
                lease_until=budget.deadline_at,
                result_json=deepcopy(failed.result_json),
            )
        )
    elif fault == "workflow_not_failed":
        (await session.get(WorkflowRunModel, proof.workflow_id)).status = "running"
    elif fault == "workflow_wrong_project":
        (await session.get(WorkflowRunModel, proof.workflow_id)).project_id = uuid4()
    elif fault == "project_other_workflow":
        project.metadata_json = {
            **project.metadata_json,
            "execution_terminal": {
                **proof.terminal,
                "workflow_id": str(uuid4()),
            },
        }
    elif fault in {"invalid_control", "invalid_control_history"}:
        control = await session.get(BookProductionControlModel, store.project.id)
        if fault == "invalid_control":
            control.desired_state = "unknown"
        else:
            control.detail_json = {"web_resume_status_history": "malformed"}
    elif fault.startswith("status:"):
        project.status = fault.split(":")[1]
    else:
        raise AssertionError(f"unknown fault: {fault}")
    if fault.startswith(("receipt:", "record:")):
        # Mutate BOTH persisted copies, so equality alone cannot prove a receipt.
        saved.result_json = record
        budget.metadata_json = {**budget.metadata_json, "reconciliations": [record]}
    # JSON equality treats False == 0 / True == 1. Force the deliberately
    # malformed fixtures to reach SQLite instead of passing against old JSON.
    for row, attribute in (
        (budget, "metadata_json"),
        (saved, "result_json"),
        (failed, "result_json"),
        (project, "metadata_json"),
    ):
        if row not in session.deleted:
            flag_modified(row, attribute)
    await session.commit()


@pytest.mark.parametrize(
    "fault",
    [
        "missing_durable",
        "wrong_durable_key",
        "wrong_durable_scope",
        "wrong_durable_status",
        "wrong_durable_hash",
        "durable_index_mismatch",
        "missing_index",
        "malformed_index",
        "duplicate_index",
        "missing_ledger",
        "missing_attempt",
        "attempt_unknown",
        "attempt_actual_tokens",
        "attempt_wrong_hash",
        "attempt_wrong_scope",
        "attempt_zero_reservation",
        "operation_unknown",
        "operation_return",
        "operation_wrong_scope",
        "operation_wrong_hash",
        "missing_failure",
        "failure_wrong_key",
        "failure_wrong_hash",
        "failure_wrong_owner",
        "ambiguous_failure",
        "workflow_not_failed",
        "workflow_wrong_project",
        "project_other_workflow",
        "invalid_control",
        "invalid_control_history",
        *[
            "receipt:" + name
            for name in (
                "scope",
                "decision",
                "attempt",
                "status",
                "remaining",
                "bool_remaining",
                "replay",
                "credit",
                "automatic",
                "outcome",
                "identifier",
                "calls",
                "tokens",
            )
        ],
        *[
            "record:" + name
            for name in (
                "remote",
                "dispatcher",
                "evidence",
                "hash",
                "fingerprint",
                "actor",
                "timestamp",
            )
        ],
        *[
            "failure:" + name
            for name in (
                "workflow",
                "attempt",
                "project",
                "operation",
                "schema",
                "zero_send",
                "bool_send",
                "multi_send",
            )
        ],
        *[
            "budget:" + name
            for name in (
                "unknown",
                "cancelled",
                "exhausted",
                "expired",
                "awaiting_submission",
                "cancel_flag",
                "authorization_paused",
                "authorization_closed",
            )
        ],
        *[
            "new_attempt:" + name
            for name in (
                "unknown",
                "reserved",
                "completed",
                "unknown_closed_root",
                "reserved_closed_root",
                "unrecognized_closed_root",
            )
        ],
        "paid_return:transport-result.v1",
        "paid_return:provider-return.v1",
        *[
            f"alias_return:{binding}:{position}:{destination}"
            for binding in ("operation_key", "attempt_id")
            for position in ("direct", "receipt")
            for destination in ("same", "cross")
        ],
        "llm_run:same",
        "llm_run:cross",
        *[
            "project:" + name
            for name in (
                "structural",
                "audit",
                "other_pause",
                "malformed_pause",
                "other_terminal",
                "another_stop",
                "history",
                "resume_history",
                "workflow_missing",
                "workflow_malformed",
            )
        ],
        "status:archived",
        "status:cancelled",
        "status:completed",
        "status:needs_replan",
    ],
)
async def test_unproven_or_current_stop_is_blocked_without_any_writes(store, monkeypatch, fault):
    proof = await crash_gap(store, monkeypatch)
    await break_proof(store, proof, fault)
    before = await all_rows(store)
    writes = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().split(None, 1)[0].upper() in {"UPDATE", "INSERT", "DELETE"}:
            writes.append(statement)

    event.listen(store.session.bind.sync_engine, "before_cursor_execute", capture)
    try:
        async with store.factory() as session:
            # Catch inside the transaction and commit: refusal itself must be read-only.
            with pytest.raises((ExecutionBudgetError, web_resume.WebProjectResumeBlockedError)):
                await web_resume.request_explicit_web_resume(
                    session,
                    slug=store.project.slug,
                    actor="web_dashboard",
                    reason="explicit RUN",
                )
            assert not session.dirty and not session.new and not session.deleted
            await session.commit()
    finally:
        event.remove(store.session.bind.sync_engine, "before_cursor_execute", capture)
    assert writes == []
    assert await all_rows(store) == before
    if fault in {"receipt:bool_remaining", "failure:bool_send"}:
        async with store.factory() as session:
            if fault.startswith("receipt"):
                stored = await session.get(ExecutionOperationModel, proof.receipt_key)
                value = stored.result_json["receipt"]["remaining_unknown"]
            else:
                stored = await session.get(ExecutionOperationModel, proof.failure_key)
                value = stored.result_json["physical_sends"]
            assert type(value) is bool


async def add_free_failure(store, proof, *, rejected=False, completed_reclaim=False):
    ledger = ExecutionLedger(store.session.bind, proof.scope, BudgetSettings())
    operation_id = "free-history:" + uuid4().hex
    input_hash = hashlib.sha256(uuid4().bytes).hexdigest()
    claim = await ledger.claim(operation_id, input_hash)
    aid = await ledger.reserve(claim, "offline-proof-model", input_hash, 40)
    await ledger.record_transport_failure(
        aid,
        {
            "schema": "transport-failure.v1",
            "physical_sends": 1 if rejected else 0,
            "http_status": 429 if rejected else None,
            "project_id": str(store.project.id),
            "workflow_run_id": str(proof.workflow_id),
            "exception_type": "FreeFailure",
        },
    )
    await ledger.settle(aid, "rejected" if rejected else "not_sent", actual_tokens=0)
    await ledger.finish(claim, None)
    if completed_reclaim:
        reclaimed = await ledger.claim(operation_id, input_hash)
        paid_id = await ledger.reserve(reclaimed, "offline-proof-model", input_hash, 60)
        result = {"model_name": "offline-proof-model", "text": "preserved paid sibling"}
        await ledger.settle(paid_id, "completed", actual_tokens=37, claim=reclaimed, result=result)
        await ledger.finish(reclaimed, result)
        async with store.factory() as session:
            receipt = await session.get(
                ExecutionOperationModel, key("unsent-reclaim.v1", claim.key, claim.owner)
            )
            assert receipt.status == "unsent_reclaim_receipt"
            assert receipt.result_json["credit_released"] is False
            assert (await session.get(ExecutionOperationModel, claim.key)).owner != claim.owner
    return aid


@pytest.mark.parametrize("rejected", [False, True])
@pytest.mark.parametrize("completed_reclaim", [False, True])
async def test_known_free_failure_in_same_workflow_does_not_mask_paid_quarantine(
    store,
    monkeypatch,
    rejected,
    completed_reclaim,
):
    proof = await crash_gap(store, monkeypatch)
    free_id = await add_free_failure(
        store, proof, rejected=rejected, completed_reclaim=completed_reclaim
    )
    before = await protected(store)
    assert await explicit(store)
    project, control = await read(store)
    assert project.status == "writing" and control.command_serial == 8
    assert project.metadata_json["execution_reconciliation_history"][-1] == {
        "terminal": proof.terminal,
        "receipt": proof.receipt,
    }
    assert await protected(store) == before
    async with store.factory() as session:
        assert (await session.get(ExecutionAttemptModel, free_id)).actual_tokens == 0


async def test_multiple_paid_quarantines_require_every_proof_and_use_latest_end_zero(
    store, monkeypatch
):
    proof = await crash_gap(store, monkeypatch, extra_paid=True)
    async with store.factory() as session:
        budget = await session.get(ExecutionBudgetModel, proof.scope)
        receipts = [record["receipt"] for record in budget.metadata_json["reconciliations"]]
        assert [r["remaining_unknown"] for r in receipts] == [1, 0]
        assert [r["status"] for r in receipts] == ["unknown", "active"]
    before = await protected(store)
    assert await explicit(store)
    project, _ = await read(store)
    assert project.metadata_json["execution_reconciliation_history"][-1]["receipt"] == receipts[-1]
    assert await protected(store) == before


async def test_second_quarantine_cannot_hide_unproved_first_paid_failure(store, monkeypatch):
    proof = await crash_gap(store, monkeypatch, extra_paid=True)
    async with store.factory() as session:
        budget = await session.get(ExecutionBudgetModel, proof.scope)
        first = budget.metadata_json["reconciliations"][0]
        await session.delete(
            await session.get(
                ExecutionOperationModel,
                key("reconciliation.v1", proof.scope, first["reconciliation_id"]),
            )
        )
        await session.commit()
    before = await all_rows(store)
    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
        await explicit(store)
    assert await all_rows(store) == before


async def test_unrelated_paid_result_same_hash_and_closed_attempts_are_preserved(
    store, monkeypatch
):
    proof = await crash_gap(store, monkeypatch)
    async with store.factory() as session:
        root = uuid4().hex
        deadline = (await session.get(ExecutionBudgetModel, proof.scope)).deadline_at
        session.add_all(
            [
                ExecutionOperationModel(
                    key=root,
                    scope_id=proof.scope,
                    input_hash=proof.input_hash,
                    status="completed",
                    owner="other-owner",
                    lease_until=deadline,
                    result_json={"text": "other paid identity"},
                ),
                ExecutionAttemptModel(
                    id=uuid4().hex,
                    operation_key=root,
                    scope_id=proof.scope,
                    input_hash=proof.input_hash,
                    status="completed",
                    reserved_tokens=100,
                    actual_tokens=80,
                    model_name="offline-proof-model",
                    created_at=datetime.now(UTC),
                ),
                ExecutionOperationModel(
                    key=uuid4().hex,
                    scope_id="project:unrelated",
                    input_hash=proof.input_hash,
                    status="transport_result",
                    owner="other-owner",
                    lease_until=deadline,
                    result_json={"operation_key": root, "attempt_id": uuid4().hex},
                ),
                LlmRunModel(
                    project_id=None,
                    logical_role="critic",
                    provider="offline-proof",
                    model_name="offline-proof-model",
                    metadata_json={
                        "execution_receipt": {"operation_key": root},
                        "input_hash": proof.input_hash,
                    },
                ),
            ]
        )
        await session.commit()
    before = await protected(store)
    assert await explicit(store)
    assert await protected(store) == before


async def test_no_materialized_chapter_still_resumes_planning(store, monkeypatch):
    await crash_gap(store, monkeypatch)
    await store.session.execute(delete(ChapterDraftVersionModel))
    await store.session.execute(delete(ChapterModel))
    await store.session.commit()
    assert await explicit(store)
    assert (await read(store))[0].status == "planning"


@pytest.mark.parametrize("late_failure", [False, True])
async def test_normal_entry_migration_and_run_are_one_rollback_unit(
    store, monkeypatch, late_failure
):
    await crash_gap(store, monkeypatch)
    before = await all_rows(store)
    original = web_resume.request_run

    async def refuse_after_run(*args, **kwargs):
        await original(*args, **kwargs)
        raise web_resume.WebProjectResumeBlockedError("late refusal")

    if late_failure:
        # Command failure injection; financial/structural proof validators stay real.
        monkeypatch.setattr(web_resume, "request_run", refuse_after_run)
    async with store.factory() as session:
        if late_failure:
            with pytest.raises(web_resume.WebProjectResumeBlockedError, match="late refusal"):
                await web_resume.request_explicit_web_resume(
                    session,
                    slug=store.project.slug,
                    actor="web_dashboard",
                    reason="explicit RUN",
                )
            await session.commit()
        else:
            assert await web_resume.request_explicit_web_resume(
                session,
                slug=store.project.slug,
                actor="web_dashboard",
                reason="explicit RUN",
            )
            await session.rollback()
    assert await all_rows(store) == before
