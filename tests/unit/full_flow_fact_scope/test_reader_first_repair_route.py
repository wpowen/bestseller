"""Routing RED: verified native proposals classify debt, never authorize edits."""

from copy import deepcopy
import json
import socket

import pytest
from sqlalchemy.engine import Engine
from test_commercial_action_projection import slot
from test_commercial_action_projection import stable as stable
from test_commercial_neutral_quorum_consumer import report as neutral_report
from test_primary_review_submission import completion, payload
from test_rewrite_fact_scope import PARENT, _report, _sources

from bestseller.services import llm
from bestseller.services import rewrite_fact_scope as scopes
from bestseller.services.chapter_draft_candidate_eligibility import (
    _proof_sha,
    verified_commercial_actions,
)
from bestseller.services.primary_review_submission import parse_primary_review_submission
from bestseller.services.rewrite_patch import text_hash

pytestmark = pytest.mark.unit
__all__ = ["neutral_report", "stable"]

INDEX_FIELDS = (
    "fact_action_indexes", "editorial_action_indexes",
    "audit_action_indexes", "unresolved_action_indexes",
)
FLAGS = (
    "body_edit_authorized", "unrestricted_fallback_allowed",
    "adoption_authorized", "grades_modified", "grants_credit",
)
RESULT_FIELDS = {
    "schema", "route", "parent_sha256", "report_payload_sha256",
    "action_projection_sha256", "primary_receipt_sha256",
    "primary_editorial_proposal", "primary_citation_ids",
    "requires_explicit_split", "deferred_reasons", *INDEX_FIELDS, *FLAGS,
}


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("routing unit forbids DB/network/model execution")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Engine, "raw_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def primary(report, verdict="rewrite", *, body=PARENT, dimension="readability"):
    value = payload((1, 2))
    value["verdict"] = verdict
    for citation in value["citations"]:
        citation["dimension"] = dimension
    original = completion(value, raw_message=True).__dict__
    text, receipt, errors = parse_primary_review_submission(original, body)
    assert not errors and receipt["status"] == "verified"
    report.update(
        primary_submission_original=original,
        primary_submission_receipt=receipt,
        critic_response=text,
    )


def route(report, *, body=PARENT):
    function = getattr(scopes, "reader_first_repair_route", None)
    assert callable(function), "reader_first_repair_route API missing"
    before = deepcopy(report)
    try:
        result = function(body, report)
    finally:
        assert report == before
    assert set(result) == RESULT_FIELDS
    assert result["schema"] == "reader-first-repair-routing.v1"
    assert result["route"] in {
        "fact_only", "editorial_only", "mixed", "no_body_edit", "unknown",
    }
    assert all(result[field] is False for field in FLAGS)
    assert result["parent_sha256"] == text_hash(body)
    assert result["report_payload_sha256"] == text_hash(canonical(report))
    summary = report["evidence_summary"]
    assert result["action_projection_sha256"] == summary[
        "commercial_action_projection"
    ]["projection_sha256"]
    receipt = report["primary_submission_receipt"]
    assert result["primary_receipt_sha256"] == text_hash(canonical(receipt))
    assert result["primary_editorial_proposal"] is (
        receipt["parsed_payload"]["verdict"] == "rewrite"
    )
    catalog = receipt["catalog"]
    assert result["primary_citation_ids"] == [
        catalog[citation["paragraph_number"] - 1]["id"]
        for citation in receipt["parsed_payload"]["citations"]
    ]
    actions = verified_commercial_actions(summary, body)
    all_indexes = []
    for field in INDEX_FIELDS:
        indexes = result[field]
        assert isinstance(indexes, list)
        assert indexes == sorted(set(indexes))
        assert all(type(index) is int for index in indexes)
        all_indexes.extend(indexes)
    assert sorted(all_indexes) == list(range(len(actions)))
    assert result["requires_explicit_split"] is bool(
        result["fact_action_indexes"] and (
            result["editorial_action_indexes"] or result["primary_editorial_proposal"]
        )
    )
    assert set(result["deferred_reasons"]) <= {
        "mixed_fact_editorial", "commercial_threshold_blocker",
        "window_unavailable", "window_not_passed",
    }
    assert body not in canonical(result)
    return result


@pytest.mark.parametrize(
    "kind", ["current_fact_conflict", "prior_fact_conflict", "constraint_violation"],
)
def test_exact_native_fact_tuples_select_fact_only_without_edit_authority(kind):
    report = _report(kind=kind)
    result = route(report)
    assert result["route"] == "fact_only"
    assert result["fact_action_indexes"] == [0]
    assert result["editorial_action_indexes"] == []
    assert result["deferred_reasons"] == []
    assert scopes.derive_fact_repair_scope(PARENT, report) is not None


@pytest.mark.parametrize("code", ["OPAQUE_47", "FACT_TIMELINE_777", "RECEIPT_09_17"])
@pytest.mark.parametrize("verdict", ["pass", "rewrite"])
def test_commercial_audit_does_not_silence_primary_or_infer_facts_from_names(code, verdict):
    report = _report(
        kind="narrative_quality",
        original=slot(_sources(), code=code, kind="narrative_quality"),
    )
    primary(report, verdict, dimension=code)
    assert scopes.derive_fact_repair_scope(PARENT, report) is None
    result = route(report)
    assert result["audit_action_indexes"] == [0]
    assert result["fact_action_indexes"] == result["editorial_action_indexes"] == []
    assert result["route"] == ("editorial_only" if verdict == "rewrite" else "no_body_edit")
    assert result["deferred_reasons"] == []


@pytest.mark.parametrize(
    "kind", ["current_fact_conflict", "prior_fact_conflict", "constraint_violation"],
)
def test_primary_rewrite_and_commercial_fact_are_explicit_mixed_debt(kind):
    report = _report(kind=kind)
    primary(report, "rewrite", dimension="continuity")
    # Primary rewrite is an editorial proposal, not a PASS for legacy fact scope.
    assert scopes.derive_fact_repair_scope(PARENT, report) is None
    with pytest.raises(ValueError, match=r"^fact_repair_scope_unavailable$"):
        scopes.require_fact_repair_scope(PARENT, report)
    result = route(report)
    assert result["route"] == "mixed"
    assert result["fact_action_indexes"] == [0]
    assert result["editorial_action_indexes"] == []
    assert result["primary_editorial_proposal"] is True
    assert result["requires_explicit_split"] is True
    assert result["deferred_reasons"] == ["mixed_fact_editorial"]


@pytest.mark.parametrize("verdict", ["pass", "rewrite"])
def test_all_commercial_fact_and_editorial_actions_remain_mixed(verdict):
    report = _report(narrative_block=True)
    primary(report, verdict)
    result = route(report)
    assert result["route"] == "mixed"
    assert result["fact_action_indexes"] and result["editorial_action_indexes"]
    assert result["requires_explicit_split"] is True
    assert "mixed_fact_editorial" in result["deferred_reasons"]
    assert scopes.derive_fact_repair_scope(PARENT, report) is None


def test_verified_commercial_editorial_has_its_own_proposal_route():
    report = _report(kind="narrative_quality", narrative_block=True)
    result = route(report)
    assert result["route"] == "editorial_only"
    assert result["editorial_action_indexes"] and result["audit_action_indexes"]
    assert result["fact_action_indexes"] == []
    assert result["primary_editorial_proposal"] is False


@pytest.mark.parametrize("verdict", ["pass", "rewrite"])
def test_retained_threshold_is_unknown_even_with_a_valid_primary_proposal(verdict):
    report = _report(original=slot(_sources(), score=0.4, issues=False))
    primary(report, verdict)
    result = route(report)
    assert result["route"] == "unknown"
    assert result["unresolved_action_indexes"]
    assert "commercial_threshold_blocker" in result["deferred_reasons"]
    assert result["primary_editorial_proposal"] is (verdict == "rewrite")


@pytest.mark.parametrize(
    "fault", [
        "missing", "free_text", "cached_verdict", "cached_citations",
        "canonical", "raw_message", "foreign_body", "invalid_selector",
    ],
)
def test_primary_editorial_requires_actual_structured_current_body_citations(fault):
    report = _report(kind="narrative_quality")
    primary(report)
    if fault == "missing":
        report.pop("primary_submission_original")
    elif fault == "free_text":
        report["primary_submission_original"]["content"] = report["critic_response"]
        report["primary_submission_original"]["tool_calls"] = []
    elif fault == "cached_verdict":
        report["primary_submission_receipt"]["parsed_payload"]["verdict"] = "pass"
    elif fault == "cached_citations":
        report["primary_submission_receipt"]["selected_paragraph_numbers"] = [999]
    elif fault == "canonical":
        report["critic_response"] += "\nUnsubmitted editorial instructions."
    elif fault == "raw_message":
        report["primary_submission_original"]["raw_message"]["content"] = "Other return"
    elif fault == "foreign_body":
        primary(report, body=PARENT + "\n\nA newer paragraph.")
    else:
        original = completion(payload((999,)), raw_message=True).__dict__
        text, receipt, errors = parse_primary_review_submission(original, PARENT)
        assert errors and receipt["status"] == "rejected"
        report.update(
            primary_submission_original=original,
            primary_submission_receipt=receipt,
            critic_response=text,
        )
    with pytest.raises(ValueError, match=r"^reader_first_repair_route_unavailable$"):
        route(report)


@pytest.mark.parametrize("report", [None, {}, ["editorial"]])
def test_missing_native_report_never_implies_editorial_or_no_body_edit(report):
    with pytest.raises(ValueError, match=r"^reader_first_repair_route_unavailable$"):
        route(report)


@pytest.mark.parametrize("fault", ["missing_projection", "raw_return", "authority", "lineage"])
def test_route_cannot_bypass_public_commercial_proof_validation(fault):
    report = _report()
    summary = report["evidence_summary"]
    if fault == "missing_projection":
        summary.pop("commercial_action_projection")
    elif fault == "raw_return":
        original = summary["llm_commercial_judge_original"]
        envelope = json.loads(original["raw_excerpt"])
        envelope["raw_report"] += " "
        original["raw_excerpt"] = json.dumps(envelope)
    elif fault == "authority":
        summary["commercial_action_projection"]["issue_actions"][0]["authority"] = "craft_only"
    else:
        summary["commercial_action_projection"]["issue_actions"][0]["lineage"][0][
            "issue"
        ]["body_citations"][0]["quote"] = "Foreign body"
    with pytest.raises(ValueError, match=r"^commercial_action_proof_unverified$"):
        route(report)


@pytest.mark.parametrize("fault", ["missing", "false", "blocking", "infrastructure", "missing_run"])
def test_window_debt_retains_primary_proposal_without_weakening_fact_preconditions(fault):
    report = _report(kind="narrative_quality")
    primary(report)
    summary = report["evidence_summary"]
    window = summary["llm_window_quality_judge"]
    if fault == "missing":
        summary.pop("llm_window_quality_judge")
    elif fault == "false":
        window["passed"] = False
    elif fault == "missing_run":
        window["llm_run_id"] = None
    else:
        window["blocking_issues"] = [{
            "code": "JUDGE_TIMEOUT" if fault == "infrastructure" else "WINDOW_FACT_CONFLICT",
            "severity": "critical", "evidence": "Unresolved window observation",
            "required_fix": "Recover current review evidence.",
        }]
    assert scopes.derive_fact_repair_scope(PARENT, report) is None
    result = route(report)
    assert result["route"] == "unknown"
    assert result["primary_editorial_proposal"] is True
    expected = "window_unavailable" if fault in {"missing", "missing_run"} else "window_not_passed"
    assert expected in result["deferred_reasons"]


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["pass", "rewrite"])
async def test_native_quorum_unknown_abstention_keeps_zero_authority(neutral_report, verdict):
    primary(neutral_report, verdict)
    before = deepcopy(neutral_report)
    result = route(neutral_report)
    assert result["route"] == ("fact_only" if verdict == "pass" else "mixed")
    proof = neutral_report["evidence_summary"]["commercial_action_projection"]
    assert proof["unknown_sample_slots"] == ["chapter-quality.independent-v2:2"]
    assert all(proof["slots"][-1][field] == 0 for field in (
        "actions_contributed", "scores_contributed", "domain_issues_contributed",
    ))
    assert neutral_report == before


@pytest.mark.asyncio
async def test_rehashed_unknown_slot_cannot_gain_route_evidence(neutral_report):
    summary = neutral_report["evidence_summary"]
    proof = summary["commercial_action_projection"]
    proof["slots"][-1]["scores_contributed"] = 1
    proof["projection_sha256"] = _proof_sha(summary["llm_commercial_judge"])
    with pytest.raises(ValueError, match=r"^commercial_action_proof_unverified$"):
        route(neutral_report)


def test_route_does_not_use_weaker_private_actions_for_unsupported_craft_policy():
    report = _report(
        kind="narrative_quality",
        original=slot(_sources(), code="ENDING_SENTENCE_WEAK", kind="narrative_quality"),
    )
    with pytest.raises(ValueError, match=r"^commercial_action_policy_unverified$"):
        route(report)
