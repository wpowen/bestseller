"""No-result placeholders are not paid cohorts; durable orphans never grant credit."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from uuid import uuid4

import pytest
from test_commercial_component_cohort import (
    SCOPE,
    _history,
    _judge,
    _save_report,
)
from test_commercial_component_cohort import (
    cohort as cohort,
)
from test_commercial_judge_recovery import BODY, sources

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
)
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services.commercial_source_evidence import commercial_judge_unavailable

pytestmark = pytest.mark.unit


def placeholder():
    return commercial_judge_unavailable(
        diagnostic=(
            "adopted commercial history unavailable: commercial_recovery_sample_receipt_missing"
        ),
    ).model_dump(mode="json", by_alias=True)


def aggregate():
    receipt = {
        "schema_version": "chapter-quality-aggregation.v1",
        "chapter_number": 2,
        "requested_samples": 3,
        "required_valid_samples": 2,
        "valid_samples": 0,
        "reference_corpus_key": "generic",
        "status": "insufficient_valid_samples",
        "samples": [
            {
                "sample_slot": f"chapter-quality.independent-v2:{slot}",
                "valid": False,
                "report": placeholder(),
            }
            for slot in range(3)
        ],
    }
    return LLMQualityJudgeResult.model_validate(
        {
            "scope": "chapter",
            "passed": False,
            "blocking_issues": [
                {
                    "code": "CHAPTER_JUDGE_UNAVAILABLE",
                    "severity": "critical",
                    "evidence": "Only 0/3 valid samples; 2 required.",
                    "required_fix": "Judge-only re-evaluation; no prose verdict is available.",
                }
            ],
            "rewrite_plan": {
                "scope": "judge",
                "instructions": "Insufficient valid judge samples; do not rewrite the chapter.",
            },
            "raw_excerpt": json.dumps(receipt),
        }
    ).model_dump(mode="json")


@pytest.mark.parametrize("shape", ["leaf", "aggregate", "wrapped_aggregate"])
def test_exact_framework_no_result_shapes_only(shape):
    value = placeholder() if shape == "leaf" else aggregate()
    if shape == "wrapped_aggregate":
        value = commercial_judge_unavailable(
            diagnostic="commercial cohort aggregation unavailable: aggregate_unbound",
            raw=value["raw_excerpt"],
        ).model_dump(mode="json")
    before = deepcopy(value)
    assert recovery._is_unpaid_commercial_placeholder(value, expected_samples=3)
    assert value == before


@pytest.mark.parametrize(
    "change",
    [
        "paid_id",
        "score",
        "dimensions",
        "raw",
        "extra_proof",
        "issue",
        "plan",
        "false_as_zero",
        "one_paid_child",
        "one_raw_child",
        "one_grade_child",
        "duplicate_slot",
        "missing_slot",
        "claimed_valid",
        "wrong_samples",
        "extra_receipt",
        "non_integer_samples",
    ],
)
def test_purported_grades_proofs_malformed_and_partial_aggregates_are_not_placeholders(change):
    value = (
        aggregate()
        if change
        in {
            "one_paid_child",
            "one_raw_child",
            "one_grade_child",
            "duplicate_slot",
            "missing_slot",
            "claimed_valid",
            "wrong_samples",
            "extra_receipt",
            "non_integer_samples",
        }
        else placeholder()
    )
    if change == "paid_id":
        value["llm_run_id"] = str(uuid4())
    elif change == "score":
        value["overall_score"] = 0.73
    elif change == "dimensions":
        value["dimension_scores"] = {"readability": 0.0}
    elif change == "raw":
        value["raw_excerpt"] = "a physical but malformed report"
    elif change == "extra_proof":
        value["paid_operation_key"] = "claimed"
    elif change == "issue":
        value["blocking_issues"][0]["code"] = "ACTUAL_NARRATIVE_PROBLEM"
    elif change == "plan":
        value["rewrite_plan"]["change"] = ["invent a new event"]
    elif change == "false_as_zero":
        value["pass"] = 0
    else:
        receipt = json.loads(value["raw_excerpt"])
        if change == "one_paid_child":
            receipt["samples"][1]["report"]["llm_run_id"] = str(uuid4())
        elif change == "one_raw_child":
            receipt["samples"][1]["report"]["raw_excerpt"] = "unverified physical raw"
        elif change == "one_grade_child":
            receipt["samples"][1]["report"]["overall_score"] = 0.73
        elif change == "duplicate_slot":
            receipt["samples"][1]["sample_slot"] = receipt["samples"][0]["sample_slot"]
        elif change == "missing_slot":
            receipt["samples"].pop()
        elif change == "claimed_valid":
            receipt["valid_samples"] = 1
        elif change == "wrong_samples":
            receipt["requested_samples"] = 4
        elif change == "non_integer_samples":
            receipt["requested_samples"] = 3.0
        else:
            receipt["paid_proof"] = "claimed"
        value["raw_excerpt"] = json.dumps(receipt)
    assert not recovery._is_unpaid_commercial_placeholder(value, expected_samples=3)


async def historical(rig):
    return await recovery._historical_commercial_cohort(
        rig.session,
        project_id=rig.project.id,
        chapter_id=rig.chapter.id,
        draft_id=rig.draft.id,
        body_hash=hashlib.sha256(BODY.encode()).hexdigest(),
        content_md=BODY,
        sources=sources(),
        contract={},
        scope=SCOPE,
        n=3,
        expected_model=rig.settings.llm.critic.model,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["leaf", "aggregate", "wrapped_aggregate"])
async def test_empty_raw_placeholder_does_not_poison_first_unspent_cohort(cohort, shape):
    rig = cohort
    value = placeholder() if shape == "leaf" else aggregate()
    if shape == "wrapped_aggregate":
        value = commercial_judge_unavailable(
            diagnostic="commercial cohort aggregation unavailable: aggregate_unbound",
            raw=value["raw_excerpt"],
        ).model_dump(mode="json")
    old = await _save_report(rig, LLMQualityJudgeResult.model_validate(value))
    before = deepcopy(old.structured_output)
    assert await historical(rig) is None
    retry = await recovery.load_commercial_retry_context(
        rig.session,
        project_id=rig.project.id,
        chapter_id=rig.chapter.id,
        draft_id=rig.draft.id,
        content_md=BODY,
        retry_context={"attempt": 2, "prior_report_id": str(old.id)},
    )
    assert retry is None
    # This is the FIRST cohort, not a replenished second round. Synthetic
    # physical fixtures record calls locally; no provider is contacted.
    await _judge(rig, bound=True, retry=retry)
    assert len(rig.calls) == 3
    await _judge(rig, bound=True, retry=retry)
    assert len(rig.calls) == 3
    await rig.session.refresh(old)
    assert old.structured_output == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "run",
        "run_missing_workflow",
        "run_unknown_body",
        "journal",
        "pointer",
        "opaque_operation",
        "completed_operation",
        "transport_result",
        "unknown_operation",
        "orphan_attempt",
    ],
)
async def test_placeholder_never_authorizes_fresh_credit_over_paid_or_opaque_history(cohort, kind):
    rig = cohort
    old = await _save_report(rig, LLMQualityJudgeResult.model_validate(placeholder()))
    original = deepcopy(old.structured_output)
    source_hash = hashlib.sha256(BODY.encode()).hexdigest()
    if kind.startswith("run"):
        rig.session.add(
            LlmRunModel(
                project_id=rig.project.id,
                workflow_run_id=rig.owner.id if kind != "run_missing_workflow" else None,
                logical_role="critic",
                provider="minimax",
                model_name="fixture",
                prompt_template="chapter_commercial_quality_judge",
                prompt_hash="a" * 64,
                metadata_json={
                    "commercial_source_input_receipt": (
                        {} if kind == "run_unknown_body" else {"body_sha256": source_hash}
                    )
                },
            )
        )
    elif kind in {"journal", "pointer"}:
        key = recovery._digest(
            {
                "project_id": str(rig.project.id),
                "chapter_id": str(rig.chapter.id),
                "body_sha256": source_hash,
            }
        )
        if kind == "journal":
            rig.owner.metadata_json = {
                "commercial_component_cohorts": {
                    key: {
                        "binding": {"source_binding": {"body_sha256": source_hash}},
                        "slots": {"slot": {"state": "prepared", "used_attempts": 1}},
                    }
                }
            }
        else:
            rig.chapter.metadata_json = {
                "commercial_component_cohorts": {
                    key: {
                        "workflow_run_id": str(rig.owner.id),
                    }
                }
            }
    else:
        operation_key = "b" * 64
        if kind != "orphan_attempt":
            result = {"opaque": True}
            if kind in {"completed_operation", "transport_result"}:
                result = {
                    "content": "retained tool result",
                    "metadata": {
                        "paid_call_origin": {
                            "schema": "paid-call-origin.v1",
                            "status": "recorded",
                            "project_id": str(rig.project.id),
                            "workflow_run_id": str(rig.owner.id),
                            "prompt_template": "chapter_commercial_quality_judge",
                        }
                    },
                }
            if kind == "transport_result":
                result = {"result": result, "operation_key": "c" * 64}
            if kind == "unknown_operation":
                result = None
            rig.session.add(
                ExecutionOperationModel(
                    key=operation_key,
                    scope_id=SCOPE,
                    input_hash="a" * 64,
                    status="transport_result"
                    if kind == "transport_result"
                    else ("unknown" if kind == "unknown_operation" else "completed"),
                    owner="fixture",
                    lease_until=datetime.now(UTC) + timedelta(hours=1),
                    result_json=result,
                )
            )
        if kind in {"orphan_attempt", "unknown_operation"}:
            rig.session.add(
                ExecutionAttemptModel(
                    id="d" * 32,
                    operation_key=operation_key,
                    scope_id=SCOPE,
                    model_name="fixture",
                    input_hash="a" * 64,
                    status="unknown",
                    reserved_tokens=123,
                    actual_tokens=None,
                    created_at=datetime.now(UTC),
                )
            )
    await rig.session.commit()
    counters = rig.budget.calls_used, rig.budget.tokens_charged
    with pytest.raises(ValueError, match="commercial_cohort_"):
        await historical(rig)
    assert len(rig.calls) == 0
    await rig.session.refresh(rig.budget)
    await rig.session.refresh(old)
    assert (rig.budget.calls_used, rig.budget.tokens_charged) == counters
    assert old.structured_output == original


@pytest.mark.asyncio
async def test_placeholder_beside_verified_paid_cohort_replays_without_fresh_draws(cohort):
    rig = cohort
    _, prior_grade, prior, latest = await _history(rig)
    await _save_report(rig, LLMQualityJudgeResult.model_validate(placeholder()))
    originals = deepcopy(prior.structured_output), deepcopy(latest.structured_output)
    spent = len(rig.calls)
    result = await _judge(rig, bound=True)
    assert result.overall_score == prior_grade.overall_score == 0.73
    assert len(rig.calls) == spent
    assert (prior.structured_output, latest.structured_output) == originals


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["score", "raw", "paid_id", "empty_object"])
async def test_claimed_grade_or_missing_paid_proof_does_not_become_placeholder(cohort, change):
    rig = cohort
    value = placeholder()
    if change == "score":
        value["overall_score"] = 0.73
    elif change == "raw":
        value["raw_excerpt"] = '{"purported_paid_report":true}'
    elif change == "paid_id":
        value["llm_run_id"] = str(uuid4())
    row = await _save_report(rig, LLMQualityJudgeResult.model_validate(value))
    if change == "empty_object":
        row.structured_output = {
            **row.structured_output,
            "evidence_summary": {"llm_commercial_judge": {}},
        }
        await rig.session.commit()
    with pytest.raises((ValueError, TypeError)):
        await historical(rig)
    assert len(rig.calls) == 0


def test_duplicate_json_keys_in_aggregate_are_not_a_no_paid_proof():
    value = aggregate()
    value["raw_excerpt"] = value["raw_excerpt"][:-1] + ',"valid_samples":0}'
    assert not recovery._is_unpaid_commercial_placeholder(value)


@pytest.mark.asyncio
async def test_placeholder_with_changed_original_draft_body_binding_still_fails(cohort):
    rig = cohort
    row = await _save_report(rig, LLMQualityJudgeResult.model_validate(placeholder()))
    payload = deepcopy(row.structured_output)
    payload["reviewed_body_fingerprint"]["digest"] = "0" * 64
    row.structured_output = payload
    await rig.session.commit()
    with pytest.raises(ValueError, match="commercial_cohort_exact_draft_body_changed"):
        await historical(rig)
    assert rig.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["primary", "other_body", "other_chapter", "malformed_primary_origin"]
)
async def test_only_verifiably_unrelated_paid_routes_do_not_spend_current_body_cohort(cohort, kind):
    from bestseller.infra.db.models import WorkflowRunModel

    rig = cohort
    await _save_report(rig, LLMQualityJudgeResult.model_validate(placeholder()))
    workflow = rig.owner
    operation_key = "e" * 64
    prompt_hash = "f" * 64
    if kind == "other_chapter":
        workflow = WorkflowRunModel(
            project_id=rig.project.id,
            workflow_type="chapter_pipeline",
            scope_type="chapter",
            scope_id=uuid4(),
            status="completed",
            metadata_json={},
        )
        rig.session.add(workflow)
        await rig.session.flush()
    origin = {
        "schema": "paid-call-origin.v1",
        "status": "recorded",
        "project_id": str(rig.project.id),
        "workflow_run_id": str(workflow.id),
        "step_run_id": None,
        "logical_role": "critic",
        "prompt_hash": prompt_hash,
        "prompt_version": "fixture",
        "prompt_template": (
            "chapter_commercial_quality_judge"
            if kind in {"other_body", "other_chapter"}
            else "chapter_review"
        ),
    }
    if kind == "malformed_primary_origin":
        origin.pop("prompt_hash")
    if kind == "other_body":
        rig.session.add(
            LlmRunModel(
                project_id=rig.project.id,
                workflow_run_id=workflow.id,
                logical_role="critic",
                provider="minimax",
                model_name="fixture",
                prompt_template="chapter_commercial_quality_judge",
                prompt_hash=prompt_hash,
                metadata_json={
                    "commercial_source_input_receipt": dict(
                        sources(body=BODY + "后来。澳").receipt
                    ),
                    "execution_receipt": {"operation_key": operation_key, "scope_id": SCOPE},
                },
            )
        )
    rig.session.add(
        ExecutionOperationModel(
            key=operation_key,
            scope_id=SCOPE,
            input_hash=prompt_hash,
            status="completed",
            owner="fixture",
            lease_until=datetime.now(UTC) + timedelta(hours=1),
            result_json={"metadata": {"paid_call_origin": origin}, "tool_calls": None},
        )
    )
    await rig.session.commit()
    if kind == "malformed_primary_origin":
        with pytest.raises(
            ValueError, match="commercial_cohort_opaque_operation_requires_recovery"
        ):
            await historical(rig)
    else:
        assert await historical(rig) is None
    assert rig.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["score", "raw", "paid_id"])
async def test_retry_loader_does_not_reset_a_purported_grade_or_raw_paid_result(cohort, change):
    rig = cohort
    value = placeholder()
    value[{"score": "overall_score", "raw": "raw_excerpt", "paid_id": "llm_run_id"}[change]] = {
        "score": 0.73,
        "raw": "physical raw",
        "paid_id": str(uuid4()),
    }[change]
    row = await _save_report(rig, LLMQualityJudgeResult.model_validate(value))
    with pytest.raises(ValueError, match="commercial_recovery_sample_receipt_missing"):
        await recovery.load_commercial_retry_context(
            rig.session,
            project_id=rig.project.id,
            chapter_id=rig.chapter.id,
            draft_id=rig.draft.id,
            content_md=BODY,
            retry_context={"attempt": 2, "prior_report_id": str(row.id)},
        )
    assert rig.calls == []


async def control_receipt(rig, kind, *, opaque_parent=False):
    operation_key, prompt_hash, attempt_id = "1" * 64, "2" * 64, "3" * 32
    quarantined = kind == "quarantine"
    origin = {
        "schema": "paid-call-origin.v1",
        "status": "recorded",
        "project_id": str(rig.project.id),
        "workflow_run_id": str(rig.owner.id),
        "step_run_id": None,
        "logical_role": "critic",
        "prompt_hash": prompt_hash,
        "prompt_version": "fixture",
        "prompt_template": "chapter_review",
    }
    parent = ExecutionOperationModel(
        key=operation_key,
        scope_id=SCOPE,
        input_hash=prompt_hash,
        status="stopped" if quarantined or opaque_parent else "completed",
        owner="fixture",
        lease_until=datetime.now(UTC) + timedelta(hours=1),
        result_json=None
        if quarantined or opaque_parent
        else {"metadata": {"paid_call_origin": origin}},
    )
    attempt = ExecutionAttemptModel(
        id=attempt_id,
        operation_key=operation_key,
        scope_id=SCOPE,
        input_hash=prompt_hash,
        model_name="fixture",
        status="reconciled_charged",
        reserved_tokens=123,
        actual_tokens=None,
        created_at=datetime.now(UTC),
    )
    if quarantined and not opaque_parent:
        # The charged unknown request is attributable to another body. Its
        # operation stays stopped; excluding it never releases that charge.
        rig.session.add(
            LlmRunModel(
                project_id=rig.project.id,
                workflow_run_id=rig.owner.id,
                logical_role="critic",
                provider="minimax",
                model_name="fixture",
                prompt_template="chapter_commercial_quality_judge",
                prompt_hash=prompt_hash,
                metadata_json={
                    "commercial_source_input_receipt": dict(sources(body=BODY + "此前。").receipt),
                    "execution_receipt": {"operation_key": operation_key, "scope_id": SCOPE},
                },
            )
        )
    if kind == "transport_failure":
        key = hashlib.sha256(f"transport-failure.v1\n{SCOPE}\n{attempt_id}".encode()).hexdigest()
        payload = {
            "schema": "transport-failure.v1",
            "attempt_id": attempt_id,
            "operation_key": operation_key,
            "physical_sends": 1,
            "exception_type": "TimeoutError",
            "prompt_template": "chapter_review",
            "logical_role": "critic",
            "project_id": str(rig.project.id),
            "workflow_run_id": str(rig.owner.id),
        }
        row = ExecutionOperationModel(
            key=key,
            scope_id=SCOPE,
            input_hash=prompt_hash,
            status="transport_failure_receipt",
            owner="fixture",
            lease_until=parent.lease_until,
            result_json=payload,
        )
    else:
        identifier = "retained-fixture-reconciliation"
        key = hashlib.sha256(f"reconciliation.v1\n{SCOPE}\n{identifier}".encode()).hexdigest()
        receipt = {
            "scope_id": SCOPE,
            "reconciliation_id": identifier,
            "attempt_id": attempt_id,
            "decision": "quarantine_charged" if quarantined else "recover_completed",
            "status": "active",
            "remaining_unknown": 0,
            "calls_used": 1,
            "tokens_charged": 123,
            "automatic_resume": False,
            "credit_released": False,
        }
        if quarantined:
            receipt.update(remote_outcome="unknown_quarantined", operation_replay_allowed=False)
        row = ExecutionOperationModel(
            key=key,
            scope_id=SCOPE,
            input_hash="4" * 64,
            status="reconciliation_receipt",
            owner="manual-reconciliation",
            lease_until=parent.lease_until,
            result_json={
                "reconciliation_id": identifier,
                "request_hash": "4" * 64,
                "actor": "fixture",
                "evidence_kind": "dispatcher_exit" if quarantined else "provider_receipt",
                "evidence_sha256": "5" * 64,
                "remote_operation_closed": not quarantined,
                "local_dispatcher_stopped": quarantined,
                "receipt": receipt,
            },
        )
    rig.session.add_all([parent, attempt, row])
    rig.budget.calls_used = 1
    rig.budget.tokens_charged = 123
    await rig.session.commit()
    return parent, attempt, row


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["reconciliation", "transport_failure", "quarantine"])
async def test_control_evidence_is_resolved_to_parent_not_counted_as_opaque_request(cohort, kind):
    rig = cohort
    await _save_report(rig, LLMQualityJudgeResult.model_validate(placeholder()))
    parent, attempt, row = await control_receipt(rig, kind)
    before = parent.status, attempt.status, deepcopy(row.result_json)
    assert await historical(rig) is None
    assert (parent.status, attempt.status, row.result_json) == before
    assert (rig.budget.calls_used, rig.budget.tokens_charged) == (1, 123)
    assert rig.calls == []
    if kind == "quarantine":
        assert parent.status == "stopped"
        receipt = row.result_json["receipt"]
        assert receipt["credit_released"] is False
        assert receipt["operation_replay_allowed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["reconciliation", "transport_failure"])
async def test_control_receipt_does_not_exempt_opaque_underlying_request(cohort, kind):
    rig = cohort
    await _save_report(rig, LLMQualityJudgeResult.model_validate(placeholder()))
    parent, attempt, row = await control_receipt(rig, kind, opaque_parent=True)
    before = parent.status, attempt.status, deepcopy(row.result_json)
    with pytest.raises(ValueError, match="commercial_cohort_opaque_operation_requires_recovery"):
        await historical(rig)
    assert (parent.status, attempt.status, row.result_json) == before
    assert (rig.budget.calls_used, rig.budget.tokens_charged) == (1, 123)
    assert rig.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["attempt", "input", "key", "credit", "replay"])
async def test_malformed_or_cross_bound_control_receipt_cannot_hide_history(cohort, change):
    rig = cohort
    await _save_report(rig, LLMQualityJudgeResult.model_validate(placeholder()))
    parent, attempt, row = await control_receipt(rig, "quarantine")
    payload = deepcopy(row.result_json)
    if change == "attempt":
        payload["receipt"]["attempt_id"] = "missing-attempt"
    elif change == "input":
        row.input_hash = "0" * 64
    elif change == "key":
        row.key = "0" * 64
    elif change == "credit":
        payload["receipt"]["credit_released"] = True
    else:
        payload["receipt"]["operation_replay_allowed"] = True
    row.result_json = payload
    await rig.session.commit()
    with pytest.raises(ValueError, match="commercial_cohort_control_receipt_"):
        await historical(rig)
    assert parent.status == "stopped" and attempt.status == "reconciled_charged"
    assert (rig.budget.calls_used, rig.budget.tokens_charged) == (1, 123)
    assert rig.calls == []


@pytest.mark.asyncio
async def test_current_body_under_foreign_chapter_workflow_is_conflict_not_exclusion(cohort):
    from bestseller.infra.db.models import WorkflowRunModel

    rig = cohort
    await _save_report(rig, LLMQualityJudgeResult.model_validate(placeholder()))
    foreign = WorkflowRunModel(
        project_id=rig.project.id,
        workflow_type="chapter_pipeline",
        scope_type="chapter",
        scope_id=uuid4(),
        status="completed",
        metadata_json={},
    )
    rig.session.add(foreign)
    await rig.session.flush()
    rig.session.add(
        LlmRunModel(
            project_id=rig.project.id,
            workflow_run_id=foreign.id,
            logical_role="critic",
            provider="minimax",
            model_name="fixture",
            prompt_template="chapter_commercial_quality_judge",
            prompt_hash="a" * 64,
            metadata_json={"commercial_source_input_receipt": dict(sources().receipt)},
        )
    )
    await rig.session.commit()
    with pytest.raises(ValueError, match="commercial_cohort_paid_body_workflow_scope_conflict"):
        await historical(rig)
    assert rig.calls == []
