"""Actual round-2 producer/freeze with isolated SQLite physical ledger proofs."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm.attributes import flag_modified
from test_commercial_component_cohort import SCOPE, _judge, _new_workflow, _save_report
from test_commercial_component_cohort import cohort as cohort
from test_commercial_judge_recovery import _review_completion, report
from test_commercial_quarantine_action_proofs import _load
from test_commercial_slot_quarantine import _ExitPrepared, _financial, _journal, _prepare_exact

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
from bestseller.services.commercial_slot_quarantine import _digest
from bestseller.settings import BudgetSettings, LLMRoleSettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _round2_case(rig, monkeypatch, mode="quarantined"):
    rig.outcomes[2] = [True]
    first = await _judge(rig, bound=True)
    prior = await _save_report(rig, first)
    retry = await recovery.load_commercial_retry_context(
        rig.session,
        project_id=rig.project.id,
        chapter_id=rig.chapter.id,
        draft_id=rig.draft.id,
        content_md=rig.draft.content_md,
        retry_context={"attempt": 2, "prior_report_id": str(prior.id)},
    )
    original_prepare = recovery.prepare_commercial_component_slot
    original_complete = judge.complete_text
    captured = {}

    async def observe_prepare(*args, **kwargs):
        value = await original_prepare(*args, **kwargs)
        if kwargs["sample_slot"].endswith(":1"):
            captured.update(
                base_hash=kwargs["current_input_hash"],
                sources=kwargs["sources"],
                critic=kwargs["effective_critic"],
            )
        return value

    async def interrupt(session, settings, request):
        if request.sample_slot.endswith(":1"):
            captured["request"] = request
            raise _ExitPrepared()
        return await original_complete(session, settings, request)

    monkeypatch.setattr(recovery, "prepare_commercial_component_slot", observe_prepare)
    monkeypatch.setattr(judge, "complete_text", interrupt)
    with pytest.raises(_ExitPrepared):
        await _judge(rig, bound=True, retry=retry)
    monkeypatch.setattr(recovery, "prepare_commercial_component_slot", original_prepare)
    request = captured["request"]
    assert "prepared_effective_input" not in request.metadata
    final_hash = llm._effective_input_hash(
        request, LLMRoleSettings.model_validate(captured["critic"])
    )
    assert final_hash != captured["base_hash"]
    assert request.metadata["commercial_evidence_recovery"]["attempt"] == 2
    owner = await rig.session.get(
        m.WorkflowRunModel, request.workflow_run_id, populate_existing=True
    )
    key, journal = next(iter(owner.metadata_json["commercial_component_cohorts"].items()))
    prepared = deepcopy(journal["slots"][request.sample_slot])
    assert prepared["state"] == "prepared" and prepared["used_attempts"] == 2
    assert prepared["retry_context"] == retry
    namespace = f"commercial-component:{key}:round:2"
    operation_id = (
        execution_operation_id(
            namespace,
            subject=f"critic:{request.prompt_template}:{request.prompt_version}",
            revision=final_hash,
        )
        + f":sample:{request.sample_slot}"
    )
    await rig.session.rollback()
    ledger = ExecutionLedger(rig.engine, SCOPE, BudgetSettings())
    claim = await ledger.claim(operation_id, final_hash)
    attempt_id = None
    reconciliation_key = None
    if mode != "pending":
        attempt_id = await ledger.reserve(claim, captured["critic"]["model"], final_hash, 1000)
    if mode == "completed":
        execution = {
            "scope_id": SCOPE,
            "operation_key": claim.key,
            "attempts": [{"attempt_id": attempt_id, "status": "completed"}],
        }
        paid = _review_completion(
            content=json.dumps(report(0.86), ensure_ascii=False),
            provider="minimax",
            model_name=captured["critic"]["model"],
            input_tokens=3,
            output_tokens=4,
            finish_reason="tool_calls",
            metadata={
                **request.metadata,
                "paid_call_origin": llm._paid_call_origin(request, final_hash),
                "effective_input_receipt": {
                    "hash": final_hash,
                    "model": captured["critic"]["model"],
                },
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
                id=uuid4(),
                project_id=request.project_id,
                workflow_run_id=request.workflow_run_id,
                logical_role="critic",
                provider="minimax",
                model_name=captured["critic"]["model"],
                prompt_template=request.prompt_template,
                prompt_version=request.prompt_version,
                prompt_hash=final_hash,
                input_tokens=3,
                output_tokens=4,
                finish_reason="tool_calls",
                metadata_json={**request.metadata, "execution_receipt": execution},
            )
        )
        await rig.session.commit()
    elif mode in {"quarantined", "unknown", "stopped"}:
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
                "workflow_run_id": str(request.workflow_run_id),
                "operator_interruption": True,
                "elapsed_ms": 53360,
                "timeout_seconds": 180,
                "stream": False,
                "partial_stream_observed": False,
                "error_chain": [],
            },
        )
        await ledger.settle(attempt_id, "unknown")
        if mode == "quarantined":
            recon_id = "round2-test-dispatcher-exit-" + attempt_id
            await ledger.reconcile(
                ExecutionReconciliation(
                    reconciliation_id=recon_id,
                    attempt_id=attempt_id,
                    expected_input_hash=final_hash,
                    decision="quarantine_charged",
                    evidence_kind="dispatcher_exit",
                    evidence_sha256="e" * 64,
                    actor="explicit offline operator",
                    remote_operation_closed=False,
                    local_dispatcher_stopped=True,
                )
            )
            reconciliation_key = hashlib.sha256(
                f"reconciliation.v1\n{SCOPE}\n{recon_id}".encode()
            ).hexdigest()
        elif mode == "stopped":
            # A stopped row without operator reconciliation is never sufficient.
            root = await rig.session.get(
                m.ExecutionOperationModel, claim.key, populate_existing=True
            )
            root.status = "stopped"
            budget = await rig.session.get(m.ExecutionBudgetModel, SCOPE, populate_existing=True)
            budget.status = "active"
            await rig.session.commit()
    for model in (rig.project, rig.chapter, rig.draft, rig.owner, rig.budget):
        await rig.session.refresh(model)
    forbidden = []

    async def deny(*args, **kwargs):
        forbidden.append("payment")
        raise AssertionError("round2 recovery must not pay or reconcile")

    monkeypatch.setattr(judge, "complete_text", deny)
    monkeypatch.setattr(llm, "complete_text", deny)
    monkeypatch.setattr(llm, "_call_litellm", deny)
    monkeypatch.setattr(ExecutionLedger, "reserve", deny)
    monkeypatch.setattr(ExecutionLedger, "reconcile", deny)
    return SimpleNamespace(
        rig=rig,
        request=request,
        owner_id=owner.id,
        key=key,
        slot=request.sample_slot,
        prepared=prepared,
        input_hash=captured["base_hash"],
        final_hash=final_hash,
        namespace=namespace,
        root=claim.key,
        attempt=attempt_id,
        retry=retry,
        reconciliation_key=reconciliation_key,
        forbidden=forbidden,
    )


async def test_real_round2_cold_completed_return_uses_gateway_without_new_payment(
    cohort, monkeypatch
):
    gateway = llm.complete_text
    case = await _round2_case(cohort, monkeypatch, "completed")
    before = await _financial(cohort)
    reusable, call = await _prepare_exact(case)
    assert reusable is None and call["namespace"] == case.namespace
    assert (await _journal(case))["slots"][case.slot] == case.prepared
    monkeypatch.setattr(llm, "complete_text", gateway)
    monkeypatch.setattr(judge, "complete_text", gateway)
    cohort.settings.llm.mock = False
    result = await _judge(cohort, bound=True)
    assert not result.infrastructure_failure
    entry = (await _journal(case))["slots"][case.slot]
    assert entry["state"] == "completed" and entry["used_attempts"] == 2
    assert entry["report"]["overall_score"] == 0.86
    assert "quarantine_history" not in entry
    assert await _financial(cohort) == before
    assert case.forbidden == [] and len(cohort.calls) == 3


async def test_real_round2_quarantine_replay_and_reader_proof_are_idempotent(cohort, monkeypatch):
    case = await _round2_case(cohort, monkeypatch)
    before = await _financial(cohort)
    result = await _judge(cohort, bound=True)
    assert not result.infrastructure_failure  # Two existing valid slots; quarantine adds no grade.
    journal = await _journal(case)
    entry = journal["slots"][case.slot]
    assert entry["state"] == "quarantined" and entry["used_attempts"] == 2
    assert entry["quarantine_history"][-1]["prepared_entry"] == case.prepared
    proof = entry["quarantine_history"][-1]["proof"]
    assert proof["input_hash"] == case.final_hash
    assert proof["round2_input_binding"]["base_input_hash"] == case.input_hash
    assert proof["round2_input_binding"]["retry_context_sha256"] == _digest(case.retry)
    for _ in range(2):
        assert (await _load(case, result))[case.slot]["proof"] == proof
        workflow = await _new_workflow(cohort)
        replay = await _judge(cohort, bound=True, workflow=workflow, retry=case.retry)
        assert replay.model_dump(mode="json") == result.model_dump(mode="json")
        assert await _journal(case) == journal
        assert await _financial(cohort) == before
    assert case.forbidden == [] and len(cohort.calls) == 3


@pytest.mark.parametrize("mode", ["completed", "quarantined"])
async def test_round2_jsonb_context_key_order_keeps_frozen_feedback_bytes(
    cohort, monkeypatch, mode
):
    case = await _round2_case(cohort, monkeypatch, mode)
    owner = await cohort.session.get(m.WorkflowRunModel, case.owner_id, populate_existing=True)
    metadata = deepcopy(owner.metadata_json)
    entry = metadata["commercial_component_cohorts"][case.key]["slots"][case.slot]
    entry["retry_context"] = json.loads(json.dumps(entry["retry_context"], sort_keys=True))
    owner.metadata_json = metadata
    await cohort.session.commit()
    before = await _financial(cohort)
    reusable, binding = await _prepare_exact(case)
    if mode == "completed":
        assert reusable is None and binding["attempt"] == 2
    else:
        assert reusable.infrastructure_failure and not binding
    entry = (await _journal(case))["slots"][case.slot]
    assert entry["request"] == case.request.model_dump(mode="json")
    assert await _financial(cohort) == before and case.forbidden == []


@pytest.mark.parametrize("mode", ["pending", "stopped", "unknown"])
async def test_real_round2_nonquarantine_paths_keep_existing_guards(cohort, monkeypatch, mode):
    case = await _round2_case(cohort, monkeypatch, mode)
    before = await _financial(cohort)
    if mode == "pending":
        reused, call = await _prepare_exact(case)
        assert reused is None and call["attempt"] == 2 and call["namespace"] == case.namespace
    else:
        with pytest.raises((ValueError, ExecutionBudgetError)) as error:
            await _prepare_exact(case)
        if mode == "unknown":
            assert error.value.code == "execution_result_unknown"
    assert (await _journal(case))["slots"][case.slot] == case.prepared
    assert await _financial(cohort) == before and case.forbidden == []


@pytest.mark.parametrize("project_first", [False, True])
@pytest.mark.parametrize(
    "fault",
    [
        "base_hash",
        "request",
        "feedback",
        "context",
        "previous",
        "slot",
        "round",
        "final_receipt",
        "attempt",
        "reconciliation",
        "context_round_bool",
        "previous_round_bool",
        "receipt_null",
    ],
)
async def test_real_round2_tamper_never_replays_or_projects(
    cohort, monkeypatch, project_first, fault
):
    case = await _round2_case(cohort, monkeypatch)
    if project_first:
        await _judge(cohort, bound=True)
    owner = await cohort.session.get(m.WorkflowRunModel, case.owner_id, populate_existing=True)
    metadata = deepcopy(owner.metadata_json)
    entry = metadata["commercial_component_cohorts"][case.key]["slots"][case.slot]
    prepared = entry["quarantine_history"][-1]["prepared_entry"] if project_first else entry
    if fault == "base_hash":
        case.input_hash = "0" * 64
    elif fault == "request":
        prepared["request"]["system_prompt"] += "changed"
    elif fault == "feedback":
        prepared["request"]["metadata"]["commercial_evidence_recovery"]["prior_report_id"] = str(
            uuid4()
        )
    elif fault == "context":
        prepared["retry_context"]["prior_report_id"] = str(uuid4())
    elif fault == "previous":
        prepared["previous_report"]["llm_run_id"] = str(uuid4())
    elif fault == "slot":
        prepared["retry_context"]["slots"][case.slot]["source_binding"]["body_sha256"] = "0" * 64
    elif fault == "round":
        prepared["used_attempts"] = 1
    elif fault == "final_receipt":
        prepared["request"]["metadata"]["prepared_effective_input"] = {
            "schema": "effective-input.v1",
            "hash": case.input_hash,
        }
    elif fault == "receipt_null":
        prepared["request"]["metadata"]["prepared_effective_input"] = None
    elif fault == "context_round_bool":
        prepared["retry_context"]["slots"][case.slot]["spent_attempt"] = True
    elif fault == "previous_round_bool":
        previous = prepared["previous_report"]
        raw = json.loads(previous["raw_excerpt"])
        raw["attempt"] = True
        previous["raw_excerpt"] = json.dumps(raw)
        prepared["retry_context"]["slots"][case.slot]["report"] = deepcopy(previous)
    elif fault == "attempt":
        attempt = await cohort.session.get(
            m.ExecutionAttemptModel, case.attempt, populate_existing=True
        )
        attempt.input_hash = "0" * 64
    elif fault == "reconciliation":
        row = await cohort.session.get(
            m.ExecutionOperationModel, case.reconciliation_key, populate_existing=True
        )
        row.result_json = {**row.result_json, "local_dispatcher_stopped": False}
    if fault not in {"attempt", "reconciliation", "base_hash"}:
        owner.metadata_json = metadata
        flag_modified(owner, "metadata_json")
    await cohort.session.commit()
    before = await _financial(cohort)
    previous = await _journal(case)
    with pytest.raises(ValueError, match="commercial_slot_quarantine_"):
        await _prepare_exact(case)
    assert await _journal(case) == previous
    assert await _financial(cohort) == before and case.forbidden == []
