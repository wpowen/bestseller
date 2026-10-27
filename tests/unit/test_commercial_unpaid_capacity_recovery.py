"""Typed pre-dispatch capacity refusals preserve prepared cursors, never credit."""

from copy import deepcopy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_commercial_component_cohort import SCOPE, _judge, _save_report
from test_commercial_component_cohort import cohort as cohort  # noqa: F401
from test_commercial_judge_recovery import BODY, sources
from test_commercial_unpaid_placeholder import aggregate, historical

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, LlmRunModel
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services.commercial_source_evidence import commercial_input_receipt, commercial_judge_unavailable

pytestmark = pytest.mark.unit


def _budget_report():
    receipt = commercial_input_receipt(
        sources(), system_prompt="Review the complete input.",
        user_prompt=sources().text + "甲" * 100000, output_tokens=8192,
        tools=[{"type": "function", "function": {"name": "submit_chapter_review", "parameters": {}}}],
    )
    assert receipt["budget_passed"] is False
    return commercial_judge_unavailable(
        diagnostic="complete commercial source input exceeds admission budget; no paid judge call",
        raw=json.dumps(receipt, ensure_ascii=False),
    ).model_dump(mode="json")


def _aggregate(*, wrapped=False):
    value = aggregate()
    receipt = json.loads(value["raw_excerpt"])
    for sample in receipt["samples"]:
        sample["report"] = _budget_report()
    value["raw_excerpt"] = json.dumps(receipt)
    if wrapped:
        value = commercial_judge_unavailable(
            diagnostic="commercial cohort aggregation unavailable: commercial_cohort_aggregate_incomplete",
            raw=value["raw_excerpt"],
        ).model_dump(mode="json")
    return value


@pytest.mark.parametrize("shape", ["leaf", "aggregate", "wrapped"])
def test_exact_budget_refusal_is_no_result_not_a_paid_cohort(shape):
    value = _budget_report() if shape == "leaf" else _aggregate(wrapped=shape == "wrapped")
    before = deepcopy(value)
    assert recovery._is_unpaid_commercial_placeholder(value, expected_samples=3)
    assert value == before


@pytest.mark.parametrize("change", [
    "paid_id", "score", "diagnostic", "budget_true", "truncated", "missing_binding",
    "unknown_receipt", "extra_proof", "bad_hash", "bool_count", "wrong_reserved", "not_over_budget",
    "duplicate_json", "huge_count", "one_different_body", "one_paid_child", "one_empty_child",
])
def test_budget_words_or_malformed_receipts_cannot_hide_paid_unknown_or_mixed_inputs(change):
    value = _aggregate()
    cohort_receipt = json.loads(value["raw_excerpt"])
    child = cohort_receipt["samples"][1]["report"]
    receipt = json.loads(child["raw_excerpt"])
    if change == "paid_id":
        child["llm_run_id"] = str(uuid4())
    elif change == "score":
        child["overall_score"] = 0.8
    elif change == "diagnostic":
        child["blocking_issues"][0]["evidence"] = "An unknown provider error."
    elif change in {"one_paid_child", "one_empty_child"}:
        if change == "one_paid_child":
            child["llm_run_id"] = str(uuid4())
        else:
            child["raw_excerpt"] = ""
    else:
        if change == "budget_true": receipt["budget_passed"] = True
        elif change == "truncated": receipt["truncated"] = True
        elif change == "missing_binding": receipt.pop("body_sha256")
        elif change == "unknown_receipt": receipt["schema_version"] = "unverified-budget.v1"
        elif change == "extra_proof": receipt["paid_operation_key"] = "claimed"
        elif change == "bad_hash": receipt["source_sha256"] = "not-a-digest"
        elif change == "bool_count": receipt["estimated_input_tokens"] = True
        elif change == "wrong_reserved": receipt["reserved_input_tokens"] -= 1
        elif change == "not_over_budget":
            receipt["estimated_input_tokens"] = 10000
            receipt["reserved_input_tokens"] = 15512
        elif change == "one_different_body": receipt["body_sha256"] = "0" * 64
        elif change == "huge_count": receipt["estimated_input_tokens"] = 10 ** 1000
        child["raw_excerpt"] = json.dumps(receipt)
        if change == "duplicate_json":
            child["raw_excerpt"] = child["raw_excerpt"][:-1] + ',"budget_passed":false}'
    value["raw_excerpt"] = json.dumps(cohort_receipt)
    assert not recovery._is_unpaid_commercial_placeholder(value, expected_samples=3)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_id,error", [
    (None, "prior_report_id_missing"), ("", "prior_report_id_missing"),
    ("None", "prior_report_id_invalid"), ("null", "prior_report_id_invalid"),
    (False, "prior_report_id_invalid"), ([], "prior_report_id_invalid"),
    ("00000000-0000-0000-0000-000000000000", "prior_report_id_invalid"),
])
async def test_missing_or_sentinel_prior_report_id_is_finite_before_any_database_read(prior_id, error):
    class NoRead:
        async def get(self, *_args, **_kwargs):
            raise AssertionError("Invalid prior ID reached database lookup")

    with pytest.raises(ValueError, match="commercial_recovery_" + error):
        await recovery.load_commercial_retry_context(
            NoRead(), project_id=uuid4(), chapter_id=uuid4(), draft_id=uuid4(), content_md=BODY,
            retry_context={"attempt": 2, "prior_report_id": prior_id},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("historical_prepared", [False, True])
async def test_actual_sqlite_budget_aggregate_reuses_all_prepared_round_one_slots_without_dispatch(
    cohort, monkeypatch, historical_prepared,
):
    from bestseller.services import chapter_llm_quality_judge as judge
    from bestseller.services import commercial_review_tool as tool
    from bestseller.services import commercial_review_transport as transport
    from bestseller.services.budget import bind_execution_scope

    rig = cohort
    rig.generation_input = {"explicit_creator_requirements": {"full_source": "甲" * 100000}}
    captured = {}
    actual_contract = tool.review_evaluation_contract
    actual_transport = transport.prepare_commercial_review_transport

    def contract(**kwargs):
        captured["contract"] = actual_contract(**kwargs)
        return captured["contract"]

    def render(*args, **kwargs):
        captured["sources"] = kwargs["sources"]
        return actual_transport(*args, **kwargs)

    monkeypatch.setattr(tool, "review_evaluation_contract", contract)
    monkeypatch.setattr(transport, "prepare_commercial_review_transport", render)
    first = await _judge(rig, bound=True)
    assert first.infrastructure_failure and rig.calls == []
    assert recovery._is_unpaid_commercial_placeholder(first.model_dump(mode="json"), expected_samples=3)
    await rig.session.refresh(rig.owner)
    assert not rig.owner.metadata_json.get("commercial_component_cohorts")
    old = await _save_report(rig, first)
    old_report = deepcopy(old.structured_output)
    effective = await judge._commercial_effective_critic_configuration(rig.session, rig.settings, rig.project.id)
    preparations = []

    async def prepare(slot):
        with bind_execution_scope(SCOPE):
            reusable, call = await recovery.prepare_commercial_component_slot(
                rig.session, project_id=rig.project.id, chapter_id=rig.chapter.id,
                draft_id=rig.draft.id, workflow_run_id=rig.owner.id, content_md=BODY,
                sources=captured["sources"], evaluation_contract=captured["contract"],
                sample_slot=slot, samples=3, retry_context=None, effective_critic=effective,
                settings=rig.settings,
            )
        assert reusable is None and call["attempt"] == 1
        return deepcopy(call)

    if historical_prepared:
        for index in range(3):
            preparations.append(await prepare(f"chapter-quality.independent-v2:{index}"))
    await rig.session.refresh(rig.owner)
    journals = deepcopy(rig.owner.metadata_json.get("commercial_component_cohorts"))
    if historical_prepared:
        journal = next(iter(journals.values()))
        assert len(journal["slots"]) == 3
        assert all(slot == {"state": "prepared", "used_attempts": 1} for slot in journal["slots"].values())
    retry = await recovery.load_commercial_retry_context(
        rig.session, project_id=rig.project.id, chapter_id=rig.chapter.id, draft_id=rig.draft.id,
        content_md=BODY, retry_context={"attempt": 2, "prior_report_id": str(old.id)},
    )
    assert retry is None
    second = await _judge(rig, bound=True, retry=retry)
    assert second.infrastructure_failure and rig.calls == []
    if historical_prepared:
        assert [await prepare(f"chapter-quality.independent-v2:{index}") for index in range(3)] == preparations
    await rig.session.refresh(rig.owner)
    await rig.session.refresh(old)
    assert rig.owner.metadata_json.get("commercial_component_cohorts") == journals
    assert old.structured_output == old_report
    budget = await rig.session.get(ExecutionBudgetModel, SCOPE)
    assert budget.calls_used == budget.tokens_charged == 0
    for model in (LlmRunModel, ExecutionOperationModel, ExecutionAttemptModel):
        assert not list(await rig.session.scalars(select(model)))


@pytest.mark.asyncio
async def test_valid_shape_budget_receipt_cannot_claim_a_different_body(cohort):
    rig = cohort
    value = _aggregate()
    receipt = json.loads(value["raw_excerpt"])
    for sample in receipt["samples"]:
        budget = json.loads(sample["report"]["raw_excerpt"])
        budget["body_sha256"] = "0" * 64
        sample["report"]["raw_excerpt"] = json.dumps(budget)
    value["raw_excerpt"] = json.dumps(receipt)
    assert recovery._is_unpaid_commercial_placeholder(value)
    old = await _save_report(rig, LLMQualityJudgeResult.model_validate(value))
    with pytest.raises(ValueError, match="commercial_recovery_prior_body_mismatch"):
        await recovery.load_commercial_retry_context(
            rig.session, project_id=rig.project.id, chapter_id=rig.chapter.id, draft_id=rig.draft.id,
            content_md=BODY, retry_context={"attempt": 2, "prior_report_id": str(old.id)},
        )
    assert rig.calls == []


@pytest.mark.asyncio
async def test_budget_aggregate_does_not_exempt_actual_unbound_paid_run(cohort):
    rig = cohort
    old = await _save_report(rig, LLMQualityJudgeResult.model_validate(_aggregate()))
    original = deepcopy(old.structured_output)
    run = LlmRunModel(
        project_id=rig.project.id, workflow_run_id=rig.owner.id, logical_role="critic",
        provider="minimax", model_name="fixture", prompt_template="chapter_commercial_quality_judge",
        prompt_hash="a" * 64, metadata_json={"commercial_source_input_receipt": dict(sources().receipt)},
    )
    rig.session.add(run)
    await rig.session.commit()
    with pytest.raises(ValueError, match="commercial_cohort_legacy_paid_result_requires_report"):
        await historical(rig)
    await rig.session.refresh(old)
    assert old.structured_output == original and rig.calls == []
    assert (await rig.session.get(ExecutionBudgetModel, SCOPE)).calls_used == 0


def _transport_refusal():
    from bestseller.services.commercial_review_transport import (
        CommercialReviewTransportError, prepare_commercial_review_transport,
    )
    with pytest.raises(CommercialReviewTransportError) as caught:
        prepare_commercial_review_transport(
            "完整审查。", sources().text + "甲" * 100000, sources=sources(),
            tools=[{"type": "function", "function": {"name": "submit_chapter_review", "parameters": {}}}],
            output_tokens=8192,
        )
    return commercial_judge_unavailable(
        diagnostic="complete commercial source input exceeds admission budget; no paid judge call",
        raw=json.dumps(caught.value.receipt),
    ).model_dump(mode="json")


@pytest.mark.parametrize("fault", [None, "missing_body", "extra_binding", "bad_body", "paid", "capacity_true",
    "bool_count", "wrong_reserved", "not_exceeded", "unknown_field", "missing_catalog", "diagnostic"])
def test_transport_capacity_refusal_requires_exact_typed_source_and_capacity(fault):
    value = _transport_refusal()
    receipt = json.loads(value["raw_excerpt"])
    if fault == "missing_body": receipt["source_binding"].pop("body_sha256")
    elif fault == "extra_binding": receipt["source_binding"]["paid_id"] = "a" * 64
    elif fault == "bad_body": receipt["source_binding"]["body_sha256"] = "bad"
    elif fault == "paid": value["llm_run_id"] = str(uuid4())
    elif fault == "capacity_true": receipt["capacity"]["budget_passed"] = True
    elif fault == "bool_count": receipt["capacity"]["estimated_input_tokens"] = True
    elif fault == "wrong_reserved": receipt["capacity"]["reserved_input_tokens"] -= 1
    elif fault == "not_exceeded":
        receipt["capacity"].update(estimated_input_tokens=1000, reserved_input_tokens=2012, combined_characters=1000)
    elif fault == "unknown_field": receipt["paid_operation_key"] = "opaque"
    elif fault == "missing_catalog": receipt.pop("catalog_sha256")
    elif fault == "diagnostic": value["blocking_issues"][0]["evidence"] = "opaque failure"
    value["raw_excerpt"] = json.dumps(receipt)
    assert recovery._is_unpaid_commercial_placeholder(value) is (fault is None)


@pytest.mark.asyncio
async def test_transport_unpaid_receipt_different_explicit_body_rejected(cohort):
    rig = cohort
    value = _transport_refusal()
    receipt = json.loads(value["raw_excerpt"])
    receipt["source_binding"]["body_sha256"] = "0" * 64
    value["raw_excerpt"] = json.dumps(receipt)
    assert recovery._is_unpaid_commercial_placeholder(value)
    old = await _save_report(rig, LLMQualityJudgeResult.model_validate(value))
    with pytest.raises(ValueError, match="commercial_recovery_prior_body_mismatch"):
        await recovery.load_commercial_retry_context(
            rig.session, project_id=rig.project.id, chapter_id=rig.chapter.id, draft_id=rig.draft.id,
            content_md=BODY, retry_context={"attempt": 2, "prior_report_id": str(old.id)},
        )
    assert rig.calls == []
