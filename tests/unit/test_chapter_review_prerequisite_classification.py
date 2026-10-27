"""Pure lifecycle classification never claims financial proof or dispatch rights."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from bestseller.services.chapter_review_prerequisite import prerequisite_wait_receipt


@pytest.fixture
def case():
    fp = {
        "schema": "chapter_review_body_v1",
        "algorithm": "sha256",
        "digest": "b" * 64,
        "normalization": "newlines_lf_only",
    }
    binding = {
        "project_id": "p",
        "chapter_id": "c",
        "source_draft_id": "d",
        "source_body_hash": fp["digest"],
        "body_fingerprint": fp,
        "cycle_id": "cycle",
        "attempt": 1,
        "operation_namespace": "chapter-critic:cycle:1",
        "review_workflow_run_id": "owner",
        "policy_source_hash": "policy",
        "semantic_dependencies_sha256": "source",
    }
    report = SimpleNamespace(
        id="report",
        project_id="p",
        target_type="chapter",
        target_id="c",
        verdict="unknown",
        llm_run_id=None,
        structured_output={
            "draft_id": "d",
            "reviewed_body_fingerprint": fp,
            "primary_submission_receipt": None,
            "primary_submission_original": None,
            "rewrite_instructions": None,
            "untrusted_critic_response": "",
            "critic_evidence": {"passed": False, "status": "unavailable"},
            "evidence_summary": {
                "primary_review_not_dispatched": {
                    "reason": "required_judge_evidence_unavailable",
                    "provider_calls": 0,
                    "body_fingerprint": fp,
                },
                "commercial": {"paid_run": "retained-unchanged"},
            },
        },
    )
    return report, binding


@pytest.mark.parametrize(
    "reason", ["required_judge_evidence_unavailable", "primary_source_capacity_unavailable"]
)
def test_lifecycle_receipt_has_no_financial_or_dispatch_authority(case, reason):
    report, binding = case
    report.structured_output["evidence_summary"]["primary_review_not_dispatched"]["reason"] = reason
    before = deepcopy(report.structured_output)
    receipt = prerequisite_wait_receipt(report, binding=binding)
    assert receipt["state"] == "waiting_infrastructure"
    assert (
        receipt["financial_proof"]
        is receipt["dispatch_authorized"]
        is receipt["credit_released"]
        is False
    )
    assert receipt["operation_namespace"] == binding["operation_namespace"]
    assert report.structured_output == before
    assert "operation_key" not in receipt  # No fabricated gateway key or absence proof.


@pytest.mark.parametrize("zero", [False, 0.0, "0", None, 1, -1])
def test_zero_call_claim_is_strictly_typed(case, zero):
    report, binding = case
    report.structured_output["evidence_summary"]["primary_review_not_dispatched"][
        "provider_calls"
    ] = zero
    assert prerequisite_wait_receipt(report, binding=binding) is None


@pytest.mark.parametrize("reason", ["timeout", "paid_invalid_anchor", [], None, ""])
def test_arbitrary_unknown_cannot_use_waiting_reason(case, reason):
    report, binding = case
    report.structured_output["evidence_summary"]["primary_review_not_dispatched"]["reason"] = reason
    assert prerequisite_wait_receipt(report, binding=binding) is None


@pytest.mark.parametrize(
    "field,value", [("llm_run_id", "paid"), ("llm_run_id", "None"), ("verdict", "pass")]
)
def test_paid_or_available_primary_never_waits_as_unpaid(case, field, value):
    report, binding = case
    setattr(report, field, value)
    assert prerequisite_wait_receipt(report, binding=binding) is None
