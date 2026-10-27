"""The actual empty unavailable-wrapper shape must retain authenticated original custody."""

from copy import deepcopy

import pytest
from sqlalchemy.orm.attributes import flag_modified
from test_commercial_component_cohort import SCOPE, _judge, _save_report
from test_commercial_component_cohort import cohort as cohort
from test_commercial_quarantine_action_proofs import _all_rows, _sources
from test_commercial_quarantine_retry_reader import _read
from test_commercial_slot_quarantine import _case

from bestseller.infra.db import models as m
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.commercial_slot_quarantine import requires_verified_quarantine_slots
from bestseller.services.commercial_source_evidence import commercial_judge_unavailable

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# Field-for-field projection receipt shape from the preserved real UNKNOWN
# report. IDs, prose and quality values come from this isolated SQLite cohort.
_ACTUAL_UNSUPPORTED_PROJECTION = {
    "reason": "unsupported_slot_schema",
    "schema": "commercial-action-projection.v1",
    "status": "unsupported",
    "grants_slots": False,
    "scores_modified": False,
    "authorizes_replay": False,
    "raw_cohort_modified": False,
}


async def _unknown_wrapper_prior(case):
    rig = case.rig
    original = await _judge(rig, bound=True)
    prior = await _save_report(rig, original)
    wrapper = commercial_judge_unavailable(
        diagnostic="commercial action evidence unavailable: unsupported_slot_schema",
        result=original,
    )
    assert wrapper.infrastructure_failure and wrapper.raw_excerpt == ""
    aliased = original.model_dump(mode="json", by_alias=True)
    digest = recovery._digest(original.model_dump(mode="json"))
    assert "pass" in aliased and "passed" not in aliased
    assert recovery._digest(aliased) != digest
    assert not requires_verified_quarantine_slots(wrapper)
    assert requires_verified_quarantine_slots(original)
    payload = deepcopy(prior.structured_output)
    payload["evidence_summary"].update(
        {
            "llm_commercial_judge": wrapper.model_dump(mode="json", by_alias=True),
            "llm_commercial_judge_original": aliased,
            "commercial_action_projection": {
                **_ACTUAL_UNSUPPORTED_PROJECTION,
                "original_aggregate_sha256": digest,
            },
        }
    )
    prior.structured_output = payload
    await rig.session.commit()
    return prior, original


async def test_actual_unknown_wrapper_reads_typed_original_before_detector_without_new_calls(
    cohort,
    monkeypatch,
):
    case = await _case(cohort, monkeypatch)
    prior, original = await _unknown_wrapper_prior(case)
    before = await _all_rows(cohort)
    context = await _read(case, prior)
    slot = context["slots"][case.slot]
    assert slot["state"] == "quarantined" and slot["raw_report"] == ""
    assert slot["spent_attempt"] == 1 and slot["report"]["llm_run_id"] is None
    assert slot["report"]["overall_score"] == 0 and slot["report"]["passed"] is False
    assert "proof" not in slot and "sources" not in slot
    with pytest.raises(ValueError, match="commercial_recovery_raw_report_missing"):
        recovery.prepare_commercial_slot_retry(
            context,
            sample_slot=case.slot,
            sources=_sources(cohort),
            evaluation_contract=slot["evaluation_contract"],
        )
    assert await _read(case, prior) == context
    replay = await _judge(cohort, bound=True, retry=context)
    assert replay.model_dump(mode="json") == original.model_dump(mode="json")
    assert await _all_rows(cohort) == before
    assert len(cohort.calls) == 2 and case.forbidden == []


@pytest.mark.parametrize(
    "fault",
    [
        "original_missing",
        "digest_missing",
        "alias_digest",
        "original_changed",
        "original_rehashed",
        "orphan_pointer",
        "scope_unknown",
        "body",
        "draft",
        "no_sources",
        "original_no_quarantine",
        "grant_flag",
        "schema",
        "live_root_drift",
    ],
)
async def test_wrapper_original_cannot_bypass_hash_body_journal_or_live_proof(
    cohort,
    monkeypatch,
    fault,
):
    case = await _case(cohort, monkeypatch)
    prior, _ = await _unknown_wrapper_prior(case)
    payload = deepcopy(prior.structured_output)
    summary = payload["evidence_summary"]
    projection = summary["commercial_action_projection"]
    original = summary["llm_commercial_judge_original"]
    if fault == "original_missing":
        del summary["llm_commercial_judge_original"]
    elif fault == "digest_missing":
        del projection["original_aggregate_sha256"]
    elif fault == "alias_digest":
        projection["original_aggregate_sha256"] = recovery._digest(original)
    elif fault in {"original_changed", "original_rehashed"}:
        original["overall_score"] = 0.999
        if fault == "original_rehashed":
            from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult

            projection["original_aggregate_sha256"] = recovery._digest(
                LLMQualityJudgeResult.model_validate(original).model_dump(mode="json"),
            )
    elif fault == "original_no_quarantine":
        from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult

        original["raw_excerpt"] = "{}"
        projection["original_aggregate_sha256"] = recovery._digest(
            LLMQualityJudgeResult.model_validate(original).model_dump(mode="json"),
        )
    elif fault == "orphan_pointer":
        metadata = deepcopy(cohort.chapter.metadata_json)
        del metadata["commercial_component_cohorts"][case.key]
        cohort.chapter.metadata_json = metadata
    elif fault == "scope_unknown":
        budget = await cohort.session.get(m.ExecutionBudgetModel, SCOPE, populate_existing=True)
        budget.status = "unknown"
    elif fault == "body":
        payload["reviewed_body_fingerprint"]["digest"] = "0" * 64
    elif fault == "draft":
        payload["draft_id"] = str(case.owner_id)
    elif fault == "grant_flag":
        projection["grants_slots"] = True
    elif fault == "schema":
        projection["schema"] = "untrusted"
    elif fault == "live_root_drift":
        root = await cohort.session.get(
            m.ExecutionOperationModel, case.root, populate_existing=True
        )
        root.status = "completed"
    prior.structured_output = payload
    flag_modified(prior, "structured_output")
    await cohort.session.commit()
    before = await _all_rows(cohort)
    with pytest.raises((ValueError, ExecutionBudgetError)):
        await _read(case, prior, sources=fault != "no_sources")
    assert await _all_rows(cohort) == before and case.forbidden == []
