"""Real SQLite admission: an unsent stopped claim is not a paid quarantine.

Reuse the existing commercial fixtures; never replace an admission validator,
send to a provider, or repair the ledger from the projection being tested.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_commercial_component_cohort import SCOPE, _judge
from test_commercial_component_cohort import cohort as cohort
from test_commercial_slot_quarantine import _case, _ExitPrepared, _journal, _prepare_exact

from bestseller.infra.db import models as m
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_slot_quarantine as quarantine
from bestseller.services.budget import (
    ExecutionBudgetError,
    ExecutionLedger,
    ExecutionReconciliation,
    bind_execution_scope,
    execution_operation_id,
)
from bestseller.settings import BudgetSettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _rows(rig):
    """Read every fixture table through a separate connection, including JSON."""
    assert rig.engine.url.get_backend_name() == "sqlite"
    assert rig.engine.url.database.endswith("_test.sqlite3")
    async with async_sessionmaker(rig.engine)() as session:
        databases = (await session.execute(text("PRAGMA database_list"))).all()
        assert next(row[2] for row in databases if row[1] == "main") == rig.engine.url.database
        snapshot = {}
        for model in (
            m.ProjectModel,
            m.ChapterModel,
            m.ChapterDraftVersionModel,
            m.WorkflowRunModel,
            m.ReviewReportModel,
            m.LlmRunModel,
            m.ExecutionBudgetModel,
            m.ExecutionOperationModel,
            m.ExecutionAttemptModel,
        ):
            rows = (await session.execute(select(model.__table__))).mappings().all()
            snapshot[model.__tablename__] = sorted(
                (json.loads(json.dumps(dict(row), default=str)) for row in rows),
                key=lambda row: json.dumps(row, sort_keys=True),
            )
        return snapshot


async def _project(case, *, journal=None, **changes):
    rig = case.rig
    arguments = {
        "journal": await _journal(case) if journal is None else journal,
        "key": case.key,
        "owner_id": case.owner_id,
        "slot": case.slot,
        "source_receipt": deepcopy(case.prepared["source_receipt"]),
        "effective_critic": await judge._commercial_effective_critic_configuration(
            rig.session,
            rig.settings,
            rig.project.id,
        ),
        "current_input_hash": case.input_hash,
    }
    arguments.update(changes)
    with bind_execution_scope(SCOPE):
        return await quarantine.project_quarantined_slot(rig.session, **arguments)


async def _expire_unsent_claim(case):
    """Simulate dispatcher exit, then let the real claim guard stop the root."""
    root = await case.rig.session.get(m.ExecutionOperationModel, case.root, populate_existing=True)
    assert root.status == "pending" and root.result_json is None
    root.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    await case.rig.session.commit()
    with pytest.raises(ExecutionBudgetError, match=r"^execution_operation_stopped$"):
        await case.ledger.claim(case.operation_id, case.input_hash)


async def _stopped_case(rig, monkeypatch):
    case = await _case(rig, monkeypatch, mode="pending")
    await _expire_unsent_claim(case)
    return case


async def _prepared_identity(rig, request):
    owner = await rig.session.get(
        m.WorkflowRunModel,
        request.workflow_run_id,
        populate_existing=True,
    )
    key, journal = next(iter(owner.metadata_json["commercial_component_cohorts"].items()))
    prepared = deepcopy(journal["slots"][request.sample_slot])
    namespace = f"commercial-component:{key}:round:{prepared['used_attempts']}"
    input_hash = request.metadata["prepared_effective_input"]["hash"]
    operation_id = (
        execution_operation_id(
            namespace,
            subject=f"critic:{request.prompt_template}:{request.prompt_version}",
            revision=input_hash,
        )
        + f":sample:{request.sample_slot}"
    )
    await rig.session.rollback()
    ledger = ExecutionLedger(rig.engine, SCOPE, BudgetSettings())
    claim = await ledger.claim(operation_id, input_hash)
    for row in (rig.project, rig.chapter, rig.draft, rig.owner, rig.budget):
        await rig.session.refresh(row)
    return SimpleNamespace(
        rig=rig,
        key=key,
        owner_id=request.workflow_run_id,
        slot=request.sample_slot,
        prepared=prepared,
        root=claim.key,
        operation_id=operation_id,
        namespace=namespace,
        input_hash=input_hash,
        ledger=ledger,
        request=request,
        forbidden=[],
        claim=claim,
    )


async def _assert_same_root_reclaim(case, before):
    """The gateway's real claim implementation owns the sole allowed mutation."""
    table = m.ExecutionOperationModel.__tablename__
    old_operations = {row["key"]: row for row in before[table]}
    old_root = old_operations[case.root]
    claim = await case.ledger.claim(case.operation_id, case.input_hash)
    assert claim.key == case.root and claim.cached_result is None
    assert claim.owner != old_root["owner"]
    after = await _rows(case.rig)
    assert {k: v for k, v in after.items() if k != table} == {
        k: v for k, v in before.items() if k != table
    }
    operations = {row["key"]: row for row in after[table]}
    receipt_key = hashlib.sha256(
        f"unsent-reclaim.v1\n{case.root}\n{old_root['owner']}".encode()
    ).hexdigest()
    assert set(operations) == set(old_operations) | {receipt_key}
    for key, row in old_operations.items():
        if key != case.root:
            assert operations[key] == row
    root = operations[case.root]
    assert root == {
        **old_root,
        "status": "pending",
        "owner": claim.owner,
        "lease_until": root["lease_until"],
    }
    receipt = operations[receipt_key]
    assert receipt["status"] == "unsent_reclaim_receipt"
    assert receipt["scope_id"] == SCOPE and receipt["input_hash"] == case.input_hash
    assert receipt["owner"] == old_root["owner"]
    assert receipt["result_json"] == {
        "operation_key": case.root,
        "old_owner": old_root["owner"],
        "new_owner": claim.owner,
        "credit_released": False,
    }
    assert case.forbidden == []


@pytest.mark.parametrize("prior_reclaim", [False, True])
async def test_stopped_zero_attempt_projection_preserves_rows_then_real_claim_reuses_root(
    cohort,
    monkeypatch,
    prior_reclaim,
):
    case = await _stopped_case(cohort, monkeypatch)
    if prior_reclaim:
        # Generate a genuine historical receipt, then interrupt the next owner.
        claim = await case.ledger.claim(case.operation_id, case.input_hash)
        assert claim.key == case.root
        await _expire_unsent_claim(case)
    before = await _rows(cohort)
    journal = await _journal(case)
    assert not [
        row
        for row in before[m.ExecutionAttemptModel.__tablename__]
        if row["operation_key"] == case.root
    ]
    assert await _project(case) is None
    assert await _rows(cohort) == before and await _journal(case) == journal
    reusable, binding = await _prepare_exact(case)
    assert reusable is None and binding["namespace"] == case.namespace
    assert await _rows(cohort) == before and await _journal(case) == journal
    await _assert_same_root_reclaim(case, before)


async def test_three_slot_input_keeps_completed_and_paid_quarantine_while_reclaiming_unsent(
    cohort,
    monkeypatch,
):
    physical_fixture = judge.complete_text
    retained = []
    stop_slot = 1

    async def interrupt(session, settings, request):
        if request.sample_slot.endswith(f":{stop_slot}"):
            retained.append(request)
            raise _ExitPrepared()
        return await physical_fixture(session, settings, request)

    monkeypatch.setattr(judge, "complete_text", interrupt)
    with pytest.raises(_ExitPrepared):
        await _judge(cohort, bound=True)
    middle = await _prepared_identity(cohort, retained[-1])
    model = cohort.settings.llm.critic.model
    attempt = await middle.ledger.reserve(middle.claim, model, middle.input_hash, 1000)
    await middle.ledger.record_transport_failure(
        attempt,
        {
            "schema": "transport-failure.v1",
            "physical_sends": 1,
            "exception_type": "CancelledError",
            "http_status": None,
            "prompt_template": middle.request.prompt_template,
            "logical_role": "critic",
            "project_id": str(middle.request.project_id),
            "workflow_run_id": str(middle.owner_id),
            "operator_interruption": True,
            "elapsed_ms": 1000,
            "timeout_seconds": 180,
            "stream": False,
            "partial_stream_observed": False,
            "error_chain": [],
        },
    )
    await middle.ledger.settle(attempt, "unknown")
    await middle.ledger.reconcile(
        ExecutionReconciliation(
            reconciliation_id="offline-three-slot-" + attempt,
            attempt_id=attempt,
            expected_input_hash=middle.input_hash,
            decision="quarantine_charged",
            evidence_kind="dispatcher_exit",
            evidence_sha256="e" * 64,
            actor="explicit offline test operator",
            remote_operation_closed=False,
            local_dispatcher_stopped=True,
        )
    )
    unavailable, _ = await _prepare_exact(middle)
    assert unavailable.infrastructure_failure and not unavailable.passed
    stop_slot = 2
    with pytest.raises(_ExitPrepared):
        await _judge(cohort, bound=True)
    last = await _prepared_identity(cohort, retained[-1])
    await _expire_unsent_claim(last)
    journal = await _journal(last)
    slots = journal["slots"]
    assert {slot: entry["state"] for slot, entry in slots.items()} == {
        "chapter-quality.independent-v2:0": "completed",
        "chapter-quality.independent-v2:1": "quarantined",
        "chapter-quality.independent-v2:2": "prepared",
    }
    assert journal["binding"]["samples"] == 3
    assert slots[middle.slot]["used_attempts"] == slots[last.slot]["used_attempts"] == 1
    assert slots[middle.slot]["report"]["overall_score"] == 0
    before = await _rows(cohort)
    budget = before[m.ExecutionBudgetModel.__tablename__][0]
    assert budget["calls_used"] == 2 and budget["tokens_charged"] == 1007
    assert await _project(last) is None
    reusable, binding = await _prepare_exact(last)
    assert reusable is None and binding["namespace"] == last.namespace
    assert await _rows(cohort) == before and await _journal(last) == journal
    await _assert_same_root_reclaim(last, before)
    assert len(cohort.calls) == 1 and len(retained) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "result",
        "root_scope",
        "root_input",
        "body",
        "source",
        "request",
        "request_owner",
        "slot",
        "source_metadata",
        "current_input",
        "critic",
    ],
)
async def test_zero_attempt_does_not_bypass_request_source_body_or_root_guards(
    cohort,
    monkeypatch,
    fault,
):
    case = await _stopped_case(cohort, monkeypatch)
    journal = await _journal(case)
    entry = journal["slots"][case.slot]
    arguments = {}
    if fault in {"result", "root_scope", "root_input"}:
        root = await cohort.session.get(
            m.ExecutionOperationModel,
            case.root,
            populate_existing=True,
        )
        if fault == "result":
            root.result_json = {"retained_paid_return": True}
        elif fault == "root_scope":
            root.scope_id = "test:foreign-root-scope"
        else:
            root.input_hash = "0" * 64
    elif fault == "body":
        cohort.draft.content_md += "另一个正文。"
    elif fault == "source":
        entry["source_receipt"]["body_sha256"] = "0" * 64
    elif fault == "request":
        entry["request"]["user_prompt"] += " changed frozen input"
    elif fault == "request_owner":
        entry["request"]["workflow_run_id"] = str(cohort.project.id)
    elif fault == "slot":
        entry["request"]["sample_slot"] = "chapter-quality.independent-v2:0"
    elif fault == "source_metadata":
        entry["request"]["metadata"]["commercial_source_input_receipt"]["source_sha256"] = "0" * 64
    elif fault == "current_input":
        arguments["current_input_hash"] = "0" * 64
    else:
        role = await judge._commercial_effective_critic_configuration(
            cohort.session,
            cohort.settings,
            cohort.project.id,
        )
        arguments["effective_critic"] = {**role, "model": "minimax/foreign-fixture"}
    await cohort.session.commit()
    before = await _rows(cohort)
    with pytest.raises(ValueError, match="commercial_slot_quarantine_"):
        await _project(case, journal=journal, **arguments)
    assert await _rows(cohort) == before
    assert case.forbidden == []


async def test_existing_quarantine_with_deleted_attempt_never_becomes_unsent(cohort, monkeypatch):
    case = await _case(cohort, monkeypatch)
    unavailable, _ = await _prepare_exact(case)
    assert unavailable.infrastructure_failure and not unavailable.passed
    assert (await _journal(case))["slots"][case.slot]["state"] == "quarantined"
    await cohort.session.execute(
        delete(m.ExecutionAttemptModel).where(
            m.ExecutionAttemptModel.operation_key == case.root,
        )
    )
    await cohort.session.commit()
    before = await _rows(cohort)
    with pytest.raises(ValueError, match="commercial_slot_quarantine_"):
        await _project(case)
    assert await _rows(cohort) == before
    assert case.forbidden == []


@pytest.mark.parametrize("state", ["reserved", "unknown", "reconciled_charged"])
async def test_real_paid_or_uncertain_attempt_never_gets_unsent_reclaim(cohort, monkeypatch, state):
    mode = "reserved" if state == "reserved" else "quarantined"
    case = await _case(cohort, monkeypatch, mode=mode, reconcile=state != "unknown")
    if state == "reserved":
        # A stopped dispatcher still owns a potentially paid reservation.
        root = await cohort.session.get(
            m.ExecutionOperationModel,
            case.root,
            populate_existing=True,
        )
        root.status = "stopped"
        await cohort.session.commit()
    before = await _rows(cohort)
    attempts = [
        row
        for row in before[m.ExecutionAttemptModel.__tablename__]
        if row["operation_key"] == case.root
    ]
    assert len(attempts) == 1 and attempts[0]["status"] == state
    if state == "reconciled_charged":
        unavailable, projected = await _project(case)
        assert unavailable.infrastructure_failure and not unavailable.passed
        assert unavailable.overall_score == 0 and unavailable.llm_run_id is None
        assert projected["state"] == "quarantined"
    else:
        with pytest.raises((ValueError, ExecutionBudgetError)):
            await _project(case)
    with pytest.raises(ExecutionBudgetError):
        await case.ledger.claim(case.operation_id, case.input_hash)
    assert await _rows(cohort) == before
    assert case.forbidden == []


async def test_exact_unknown_root_delegates_to_gateway_unknown_guard_without_grade(
    cohort,
    monkeypatch,
):
    case = await _stopped_case(cohort, monkeypatch)
    root = await cohort.session.get(m.ExecutionOperationModel, case.root, populate_existing=True)
    root.status = "unknown"
    await cohort.session.commit()
    before = await _rows(cohort)
    assert await _project(case) is None
    reusable, binding = await _prepare_exact(case)
    assert reusable is None and binding["namespace"] == case.namespace
    with pytest.raises(ExecutionBudgetError, match=r"^execution_result_unknown$"):
        await case.ledger.claim(case.operation_id, case.input_hash)
    assert await _rows(cohort) == before
    assert (await _journal(case))["slots"][case.slot] == case.prepared
    assert case.forbidden == []


@pytest.mark.parametrize(
    ("kind", "nested", "foreign_scope"),
    [
        ("transport_result", False, False),
        ("transport_result", False, True),
        ("transport_result", True, True),
        ("transport_failure_receipt", False, True),
        ("reconciliation_receipt", True, True),
        ("llm_run", False, False),
        ("llm_run", False, True),
    ],
)
async def test_deleted_attempt_with_retained_paid_evidence_never_delegates_as_unsent(
    cohort,
    monkeypatch,
    kind,
    nested,
    foreign_scope,
):
    paid = kind in {"transport_result", "llm_run"}
    case = await _case(cohort, monkeypatch, mode="completed" if paid else "quarantined")
    session = cohort.session
    linked = list(await session.scalars(select(m.ExecutionOperationModel)))
    retained = None
    for operation in linked:
        value = operation.result_json or {}
        if operation.key == case.root or not isinstance(value, dict):
            continue
        belongs = (
            value.get("operation_key") == case.root
            or (value.get("receipt") or {}).get("attempt_id") == case.attempt
        )
        if belongs:
            if operation.status == kind:
                retained = operation
            else:
                await session.delete(operation)
    runs = list(await session.scalars(select(m.LlmRunModel)))
    for run in runs:
        execution = (run.metadata_json or {}).get("execution_receipt") or {}
        if execution.get("operation_key") == case.root:
            if kind == "llm_run":
                if foreign_scope:
                    run.metadata_json = {
                        **run.metadata_json,
                        "execution_receipt": {**execution, "scope_id": "test:foreign-retained"},
                    }
            else:
                await session.delete(run)
    if kind != "llm_run":
        assert retained is not None
        if foreign_scope:
            retained.scope_id = "test:foreign-retained"
        if nested:
            retained.result_json = {"receipt": {**retained.result_json, "operation_key": case.root}}
    await session.execute(
        delete(m.ExecutionAttemptModel).where(
            m.ExecutionAttemptModel.operation_key == case.root,
        )
    )
    root = await session.get(m.ExecutionOperationModel, case.root, populate_existing=True)
    root.status, root.result_json = "stopped", None
    await session.commit()
    before = await _rows(cohort)
    assert not [
        row
        for row in before[m.ExecutionAttemptModel.__tablename__]
        if row["operation_key"] == case.root
    ]
    with pytest.raises(ValueError, match="commercial_slot_quarantine_"):
        await _project(case)
    assert await _rows(cohort) == before
    assert (await _journal(case))["slots"][case.slot] == case.prepared
    assert case.forbidden == []
