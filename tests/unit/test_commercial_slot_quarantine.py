"""Real SQLite stable sampler; reconcile only in explicit operator setup."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified
from test_commercial_component_cohort import SCOPE, _judge, _new_workflow
from test_commercial_component_cohort import cohort as cohort
from test_commercial_judge_recovery import _review_completion, report

from bestseller.infra.db import models as m
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services import llm
from bestseller.services.budget import (
    ExecutionBudgetError,
    ExecutionLedger,
    ExecutionReconciliation,
    execution_operation_id,
)
from bestseller.settings import BudgetSettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _ExitPrepared(BaseException):
    pass


async def _financial(rig):
    state = {}
    async with async_sessionmaker(rig.engine)() as session:
        for model in (
            m.ExecutionBudgetModel,
            m.ExecutionOperationModel,
            m.ExecutionAttemptModel,
            m.LlmRunModel,
        ):
            rows = (await session.execute(select(model.__table__))).mappings().all()
            state[model.__tablename__] = sorted(
                json.dumps(dict(row), sort_keys=True, default=str) for row in rows
            )
    return state


async def _journal(case):
    row = await case.rig.session.get(m.WorkflowRunModel, case.owner_id, populate_existing=True)
    return deepcopy(row.metadata_json["commercial_component_cohorts"][case.key])


async def _case(rig, monkeypatch, *, malformed=False, reconcile=True, mode="quarantined"):
    path = rig.engine.url.database
    assert path.endswith("_test.sqlite3")
    identity = (await rig.session.execute(text("PRAGMA database_list"))).all()
    assert next(row[2] for row in identity if row[1] == "main") == path
    rig.outcomes[1] = [not malformed]
    physical = judge.complete_text
    retained = []

    async def interrupt(session, settings, request):
        if request.sample_slot.endswith(":2"):
            retained.append(request)
            raise _ExitPrepared()
        return await physical(session, settings, request)

    monkeypatch.setattr(judge, "complete_text", interrupt)
    with pytest.raises(_ExitPrepared):
        await _judge(rig, bound=True)
    assert len(rig.calls) == 2
    request = retained[0]
    owner_id = request.workflow_run_id
    owner = await rig.session.get(m.WorkflowRunModel, owner_id, populate_existing=True)
    key, journal = next(iter(owner.metadata_json["commercial_component_cohorts"].items()))
    prepared = deepcopy(journal["slots"][request.sample_slot])
    role = await judge._commercial_effective_critic_configuration(
        rig.session,
        rig.settings,
        request.project_id,
    )
    input_hash = request.metadata["prepared_effective_input"]["hash"]
    namespace = f"commercial-component:{key}:round:{prepared['used_attempts']}"
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
    attempt_id = None
    if mode != "pending":
        attempt_id = await ledger.reserve(claim, role["model"], input_hash, 1000)
    if mode == "completed":
        run_id = uuid4()
        execution = {
            "scope_id": SCOPE,
            "operation_key": claim.key,
            "attempts": [{"attempt_id": attempt_id, "status": "completed"}],
        }
        paid = _review_completion(
            content=json.dumps(report(0.81), ensure_ascii=False),
            provider="minimax",
            model_name=role["model"],
            input_tokens=3,
            output_tokens=4,
            finish_reason="tool_calls",
            metadata={
                **request.metadata,
                "paid_call_origin": llm._paid_call_origin(request, input_hash),
                "effective_input_receipt": {"hash": input_hash, "model": role["model"]},
                "execution_receipt": execution,
            },
        )
        await ledger.settle(
            attempt_id,
            "completed",
            actual_tokens=7,
            claim=claim,
            result=paid.model_dump(mode="json"),
        )
        rig.session.add(
            m.LlmRunModel(
                id=run_id,
                project_id=request.project_id,
                workflow_run_id=owner_id,
                logical_role="critic",
                provider="minimax",
                model_name=role["model"],
                prompt_template=request.prompt_template,
                prompt_version=request.prompt_version,
                prompt_hash=input_hash,
                input_tokens=3,
                output_tokens=4,
                finish_reason="tool_calls",
                metadata_json={**request.metadata, "execution_receipt": execution},
            )
        )
        await rig.session.commit()
    elif mode == "quarantined":
        await ledger.record_transport_failure(
            attempt_id,
            {
                "schema": "transport-failure.v1",
                "physical_sends": 1,
                "exception_type": "CancelledError",
                "http_status": None,
                "prompt_template": request.prompt_template,
                "logical_role": "critic",
                "project_id": str(request.project_id),
                "workflow_run_id": str(owner_id),
                "operator_interruption": True,
                "elapsed_ms": 53360,
                "timeout_seconds": 180,
                "stream": False,
                "partial_stream_observed": False,
                "error_chain": [],
            },
        )
        await ledger.settle(attempt_id, "unknown")
    reconciliation_id = "offline-dispatcher-exit-" + (attempt_id or "unsent")
    if reconcile and mode == "quarantined":
        evidence = ExecutionReconciliation(
            reconciliation_id=reconciliation_id,
            attempt_id=attempt_id,
            expected_input_hash=input_hash,
            decision="quarantine_charged",
            evidence_kind="dispatcher_exit",
            evidence_sha256="e" * 64,
            actor="explicit offline test operator",
            remote_operation_closed=False,
            local_dispatcher_stopped=True,
        )
        receipt = await ledger.reconcile(evidence)
        assert receipt["remaining_unknown"] == 0 and receipt["credit_released"] is False
    for model in (rig.project, rig.chapter, rig.draft, rig.owner, rig.budget):
        await rig.session.refresh(model)
    forbidden = []

    async def deny(*args, **kwargs):
        forbidden.append("complete_text")
        raise AssertionError("quarantined replay must never call complete_text")

    async def deny_reconcile(*args, **kwargs):
        raise AssertionError("runtime cannot reconcile financial evidence")

    monkeypatch.setattr(judge, "complete_text", deny)
    monkeypatch.setattr(llm, "complete_text", deny)
    monkeypatch.setattr(ExecutionLedger, "reconcile", deny_reconcile)
    return SimpleNamespace(
        rig=rig,
        key=key,
        owner_id=owner_id,
        slot=request.sample_slot,
        prepared=prepared,
        root=claim.key,
        operation_id=operation_id,
        namespace=namespace,
        input_hash=input_hash,
        ledger=ledger,
        attempt=attempt_id,
        request=request,
        forbidden=forbidden,
        reconciliation_key=hashlib.sha256(
            f"reconciliation.v1\n{SCOPE}\n{reconciliation_id}".encode()
        ).hexdigest(),
    )


@pytest.mark.parametrize("malformed", [False, True])
async def test_actual_stable_sampler_retains_original_n_and_never_grades_quarantine(
    cohort,
    monkeypatch,
    malformed,
):
    case = await _case(cohort, monkeypatch, malformed=malformed)
    before = await _financial(cohort)
    result = await _judge(cohort, bound=True)
    aggregate = json.loads(result.raw_excerpt)
    assert aggregate["requested_samples"] == 3 and aggregate["required_valid_samples"] == 2
    assert aggregate["valid_samples"] == (1 if malformed else 2)
    assert result.infrastructure_failure is malformed
    if malformed:
        assert not result.passed and result.overall_score == 0
        assert aggregate["status"] == "insufficient_valid_samples"
    quarantined = aggregate["samples"][2]
    assert quarantined["valid"] is False
    report = quarantined["report"]
    assert report["passed"] is False and report["overall_score"] == 0
    assert report["dimension_scores"] == {} and report["llm_run_id"] is None
    journal = await _journal(case)
    entry = journal["slots"][case.slot]
    assert entry["state"] == "quarantined" and entry["used_attempts"] == 1
    assert entry["quarantine_history"][0]["prepared_entry"] == case.prepared
    assert entry["quarantine_history"][0]["proof"]["root"]["key"] == case.root
    assert len(entry["quarantine_history"]) == 1
    assert entry["report"] == report
    assert await _financial(cohort) == before
    if not malformed:
        # The original owner also cannot turn the quarantined draw into round 2.
        same_owner = await _judge(cohort, bound=True, retry={"attempt": 2, "slots": {}})
        assert same_owner.model_dump(mode="json") == result.model_dump(mode="json")
        assert await _journal(case) == journal
        assert await _financial(cohort) == before
    for retry in (None, {"attempt": 2, "slots": {}}):
        workflow = await _new_workflow(cohort)
        replay = await _judge(cohort, bound=True, workflow=workflow, retry=retry)
        assert replay.model_dump(mode="json") == result.model_dump(mode="json")
        assert await _journal(case) == journal
        assert await _financial(cohort) == before
    assert case.forbidden == [] and len(cohort.calls) == 2


@pytest.mark.parametrize("project_first", [False, True])
@pytest.mark.parametrize(
    "fault",
    [
        "request",
        "source",
        "critic",
        "attempt_hash",
        "attempt_scope",
        "new_unsettled",
        "return",
        "transport_result",
        "cross_scope_return",
        "reconciliation_hash",
        "reconciliation_local",
        "reconciliation_remote",
        "reconciliation_credit",
        "missing_receipt",
        "duplicate_receipt",
        "reconciliation_type",
        "reconciliation_time",
        "failure_changed",
        "projection_grade",
    ],
)
async def test_invalid_or_stale_live_proof_never_reuses_or_redraws(
    cohort,
    monkeypatch,
    project_first,
    fault,
):
    case = await _case(cohort, monkeypatch)
    if project_first:
        await _judge(cohort, bound=True)
    owner = await cohort.session.get(m.WorkflowRunModel, case.owner_id, populate_existing=True)
    metadata = deepcopy(owner.metadata_json)
    entry = metadata["commercial_component_cohorts"][case.key]["slots"][case.slot]
    if fault in {"request", "source", "critic", "projection_grade"}:
        target = entry["quarantine_history"][-1]["prepared_entry"] if project_first else entry
        if fault == "request":
            target["request"]["user_prompt"] += "changed input"
        elif fault == "source":
            target["source_receipt"]["source_sha256"] = "0" * 64
        elif fault == "critic":
            target["request"]["metadata"]["commercial_effective_critic_sha256"] = "0" * 64
        elif project_first:
            entry["report"]["passed"] = True
        else:
            target["used_attempts"] = 2
        owner.metadata_json = metadata
    elif fault in {"attempt_hash", "attempt_scope"}:
        attempt = await cohort.session.get(m.ExecutionAttemptModel, case.attempt)
        if fault == "attempt_hash":
            attempt.input_hash = "0" * 64
        else:
            attempt.scope_id = "another-scope"
    elif fault == "new_unsettled":
        cohort.session.add(
            m.ExecutionAttemptModel(
                id=uuid4().hex,
                operation_key=case.root,
                scope_id=SCOPE,
                input_hash=case.request.metadata["prepared_effective_input"]["hash"],
                model_name=cohort.settings.llm.critic.model,
                status="reserved",
                reserved_tokens=1000,
                created_at=datetime.now(UTC),
            )
        )
    elif fault in {"return", "transport_result", "cross_scope_return"}:
        root = await cohort.session.get(m.ExecutionOperationModel, case.root)
        if fault == "return":
            root.result_json = {"content": "conflicting return"}
        else:
            cohort.session.add(
                m.ExecutionOperationModel(
                    key=hashlib.sha256(
                        f"transport-result.v1\n{SCOPE}\n{case.attempt}".encode()
                    ).hexdigest(),
                    scope_id="another-scope" if fault == "cross_scope_return" else SCOPE,
                    input_hash=root.input_hash,
                    status="transport_result",
                    owner=root.owner,
                    lease_until=root.lease_until,
                    result_json={
                        "attempt_id": case.attempt,
                        "operation_key": case.root,
                        "result": {"content": "conflicting return"},
                    },
                )
            )
    elif fault == "failure_changed":
        failure = await cohort.session.get(
            m.ExecutionOperationModel,
            hashlib.sha256(f"transport-failure.v1\n{SCOPE}\n{case.attempt}".encode()).hexdigest(),
        )
        failure.result_json = {**failure.result_json, "physical_sends": 0}
    else:
        reconciliation = await cohort.session.get(
            m.ExecutionOperationModel, case.reconciliation_key
        )
        record = deepcopy(reconciliation.result_json)
        if fault == "missing_receipt":
            await cohort.session.delete(reconciliation)
        elif fault == "duplicate_receipt":
            cohort.session.add(
                m.ExecutionOperationModel(
                    key="duplicate-operator-receipt",
                    scope_id=SCOPE,
                    input_hash=reconciliation.input_hash,
                    status="reconciliation_receipt",
                    owner=reconciliation.owner,
                    lease_until=reconciliation.lease_until,
                    result_json=record,
                )
            )
        else:
            if fault == "reconciliation_hash":
                record["request_hash"] = "0" * 64
            elif fault == "reconciliation_local":
                record["local_dispatcher_stopped"] = False
            elif fault == "reconciliation_remote":
                record["remote_operation_closed"] = True
            elif fault == "reconciliation_type":
                record["local_dispatcher_stopped"] = 1
            elif fault == "reconciliation_time":
                record["at"] = "2000-01-01T00:00:00+00:00"
            else:
                record["receipt"]["credit_released"] = True
            reconciliation.result_json = record
            flag_modified(reconciliation, "result_json")
    await cohort.session.commit()
    before = await _financial(cohort)
    previous = await _journal(case)
    result = await _judge(cohort, bound=True)
    assert result.infrastructure_failure and not result.passed
    assert await _journal(case) == previous
    assert await _financial(cohort) == before
    assert case.forbidden == []


async def test_unknown_scope_without_operator_receipt_remains_blocked(cohort, monkeypatch):
    case = await _case(cohort, monkeypatch, reconcile=False)
    before = await _financial(cohort)
    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
        await _prepare_exact(case)
    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
        await _judge(cohort, bound=True)
    assert (await _journal(case))["slots"][case.slot] == case.prepared
    assert await _financial(cohort) == before
    assert case.forbidden == []


async def _prepare_exact(case):
    from bestseller.services.budget import bind_execution_scope
    from bestseller.services.commercial_source_evidence import assemble_commercial_sources

    rig = case.rig
    sources = assemble_commercial_sources(
        chapter_number=2,
        content_md=rig.draft.content_md,
        source=judge._commercial_source_projection(
            {},
            chapter_number=2,
            content_md=rig.draft.content_md,
        ),
        previous_chapters=[],
    )
    role = await judge._commercial_effective_critic_configuration(
        rig.session,
        rig.settings,
        rig.project.id,
    )
    with bind_execution_scope(SCOPE):
        return await recovery.prepare_commercial_component_slot(
            rig.session,
            project_id=rig.project.id,
            chapter_id=rig.chapter.id,
            draft_id=rig.draft.id,
            workflow_run_id=case.owner_id,
            content_md=rig.draft.content_md,
            sources=sources,
            evaluation_contract=case.request.metadata["commercial_evaluation_contract"],
            sample_slot=case.slot,
            samples=3,
            retry_context=None,
            effective_critic=role,
            current_input_hash=case.input_hash,
            settings=rig.settings,
        )


async def test_prepared_exact_completed_root_delegates_real_gateway_cache_without_payment(
    cohort,
    monkeypatch,
):
    gateway = llm.complete_text
    case = await _case(cohort, monkeypatch, mode="completed")
    before = await _financial(cohort)
    reusable, binding = await _prepare_exact(case)
    assert reusable is None and binding["namespace"] == case.namespace
    assert (await _journal(case))["slots"][case.slot] == case.prepared

    async def no_payment(*args, **kwargs):
        raise AssertionError("completed physical return must not reserve or call provider")

    monkeypatch.setattr(ExecutionLedger, "reserve", no_payment)
    monkeypatch.setattr(llm, "_call_litellm", no_payment)
    monkeypatch.setattr(llm, "complete_text", gateway)
    monkeypatch.setattr(judge, "complete_text", gateway)
    cohort.settings.llm.mock = False
    result = await _judge(cohort, bound=True)
    assert not result.infrastructure_failure
    entry = (await _journal(case))["slots"][case.slot]
    assert entry["state"] == "completed" and entry["used_attempts"] == 1
    assert entry["report"]["llm_run_id"] is not None
    assert "quarantine_history" not in entry
    assert await _financial(cohort) == before
    assert len(cohort.calls) == 2 and case.forbidden == []


async def test_prepared_exact_pending_without_attempt_delegates_old_claim_and_reclaim(
    cohort,
    monkeypatch,
):
    case = await _case(cohort, monkeypatch, mode="pending")
    root = await cohort.session.get(m.ExecutionOperationModel, case.root)
    root.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    await cohort.session.commit()
    before = await _financial(cohort)
    reusable, binding = await _prepare_exact(case)
    assert reusable is None and binding["namespace"] == case.namespace
    assert await _financial(cohort) == before
    assert (await _journal(case))["slots"][case.slot] == case.prepared
    with pytest.raises(ExecutionBudgetError, match=r"^execution_operation_stopped$"):
        await case.ledger.claim(case.operation_id, case.input_hash)
    reclaimed = await case.ledger.claim(case.operation_id, case.input_hash)
    assert reclaimed.key == case.root and reclaimed.cached_result is None
    async with async_sessionmaker(cohort.engine)() as session:
        budget = await session.get(m.ExecutionBudgetModel, SCOPE)
        attempts = list(
            await session.scalars(
                select(m.ExecutionAttemptModel).where(
                    m.ExecutionAttemptModel.operation_key == case.root,
                )
            )
        )
        assert not attempts and budget.calls_used == 2 and budget.tokens_charged == 14
    assert case.forbidden == []


@pytest.mark.parametrize("mode", ["pending", "completed"])
@pytest.mark.parametrize("fault", ["request", "source", "input_hash"])
async def test_prepared_nonstopped_old_hash_candidate_cannot_delegate_changed_input(
    cohort,
    monkeypatch,
    mode,
    fault,
):
    case = await _case(cohort, monkeypatch, mode=mode)
    if fault == "input_hash":
        case.input_hash = "0" * 64
    else:
        owner = await cohort.session.get(m.WorkflowRunModel, case.owner_id, populate_existing=True)
        metadata = deepcopy(owner.metadata_json)
        entry = metadata["commercial_component_cohorts"][case.key]["slots"][case.slot]
        if fault == "request":
            entry["request"]["user_prompt"] += "changed frozen request"
        else:
            entry["source_receipt"]["source_sha256"] = "0" * 64
        owner.metadata_json = metadata
        await cohort.session.commit()
    before = await _financial(cohort)
    with pytest.raises(ValueError, match="commercial_slot_quarantine_"):
        await _prepare_exact(case)
    assert await _financial(cohort) == before
    assert case.forbidden == []


@pytest.mark.parametrize("status", ["pending", "completed"])
async def test_existing_quarantined_root_status_drift_never_delegates(
    cohort,
    monkeypatch,
    status,
):
    case = await _case(cohort, monkeypatch)
    await _judge(cohort, bound=True)
    root = await cohort.session.get(m.ExecutionOperationModel, case.root, populate_existing=True)
    root.status = status
    await cohort.session.commit()
    before = await _financial(cohort)
    with pytest.raises(ValueError, match="commercial_slot_quarantine_"):
        await _prepare_exact(case)
    assert await _financial(cohort) == before
    assert case.forbidden == []


@pytest.mark.parametrize("fault", ["body", "slot", "round", "aggregate_n", "aggregate_majority"])
async def test_cross_binding_or_changed_aggregate_fails_closed(cohort, monkeypatch, fault):
    case = await _case(cohort, monkeypatch)
    result = await _judge(cohort, bound=True)
    if fault.startswith("aggregate_"):
        receipt = json.loads(result.raw_excerpt)
        receipt["requested_samples" if fault == "aggregate_n" else "required_valid_samples"] = 2
        if fault == "aggregate_majority":
            receipt["required_valid_samples"] = 1
        changed = result.model_copy(update={"raw_excerpt": json.dumps(receipt)})
        from bestseller.services.budget import bind_execution_scope

        before = await _financial(cohort)
        with bind_execution_scope(SCOPE), pytest.raises(ValueError, match="quarantine_aggregate"):
            await recovery.bind_commercial_component_aggregate(
                cohort.session,
                project_id=cohort.project.id,
                chapter_id=cohort.chapter.id,
                content_md=cohort.draft.content_md,
                result=changed,
            )
    else:
        owner = await cohort.session.get(m.WorkflowRunModel, case.owner_id, populate_existing=True)
        metadata = deepcopy(owner.metadata_json)
        entry = metadata["commercial_component_cohorts"][case.key]["slots"][case.slot]
        if fault == "body":
            draft = await cohort.session.get(m.ChapterDraftVersionModel, cohort.draft.id)
            draft.content_md += "新正文"
            await cohort.session.commit()
            cohort.draft.content_md = draft.content_md
        else:
            old = entry["quarantine_history"][-1]["prepared_entry"]
            if fault == "slot":
                old["request"]["sample_slot"] = "chapter-quality.independent-v2:0"
            else:
                old["used_attempts"] = 2
            owner.metadata_json = metadata
            await cohort.session.commit()
        before = await _financial(cohort)
        result = await _judge(cohort, bound=True)
        assert result.infrastructure_failure
    assert await _financial(cohort) == before
    assert case.forbidden == []
