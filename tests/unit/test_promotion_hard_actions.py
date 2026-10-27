"""Pure action evidence; server snapshots are fixtures, not live gate proof."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest
from test_primary_review_submission import completion, payload
from test_commercial_action_projection import sources, slot, stable  # noqa: F401

from bestseller.services.promotion_hard_actions import (
    BoundGateReport, CommercialPromotionEvidence, classify_promotion_hard_actions,
)
from bestseller.services.primary_review_submission import parse_primary_review_submission
from bestseller.services.write_gate import GateConfig


BODY = "The observatory rotated toward the blue moon.\n\nThe astronomer locked its dome."
GATES = {name: f"fixture-server-{name}.v1" for name in (
    "primary", "deterministic", "explicit_plan", "identity", "canon", "continuity", "bible", "length",
)}


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def gate(name, *, findings=None, hard=None, verifier=None, source=None, **changes):
    verifier = verifier or GATES.get(name, f"fixture-server-{name}.v1")
    source = source or {"current_server_source": name, "revision": 12}
    report = {
        "verifier": verifier, "status": "verified", "coverage": "complete",
        "body_sha256": hashlib.sha256(BODY.encode()).hexdigest(), "chapter_number": 2,
        "source_sha256": sha(source), "findings": findings or [],
        "passed": not bool(hard), "blocking_issues": hard or [], "audit_issues": [], "unknown_issues": [],
        **changes,
    }
    return BoundGateReport(verifier, hashlib.sha256(BODY.encode()).hexdigest(), 2, source, report)


def inputs():
    submitted = payload()
    submitted["verdict"] = "pass"
    submitted["scores"] = dict.fromkeys(submitted["scores"], .62)
    submitted["rewrite_direction"] = "Optional polish only; preserve established facts."
    primary = completion(submitted, raw_message=True)
    primary.provider = "fixture-physical-response"
    primary.fallback_used = False
    primary.llm_run_id = "00000000-0000-4000-8000-000000000007"
    reports = {name: gate(name) for name in GATES}
    _, receipt, errors = parse_primary_review_submission(primary, BODY)
    assert not errors
    reports["primary"] = gate(
        "primary", llm_run_id=primary.llm_run_id, submission_sha256=sha(receipt))
    return {
        "review_result": {
            "verdict": "rewrite", "severity_max": "medium", "scores": {"overall": .62},
            "findings": [{"category": "coverage", "severity": "medium", "message": "Soft progression debt"}],
            "evidence_summary": {},
        },
        "content_md": BODY, "chapter_number": 2, "gate_config": GateConfig({}),
        "primary_completion": primary, "gate_reports": reports,
        "required_gate_verifiers": dict(GATES),
        "current_source_hashes": {name: sha(value.source) for name, value in reports.items()},
    }


def with_commercial(data, original, source):
    snapshot = gate("commercial", aggregate_sha256=sha(original.model_dump(mode="json")))
    data["required_gate_verifiers"]["commercial"] = snapshot.verifier
    data["current_source_hashes"]["commercial"] = sha(snapshot.source)
    data["commercial"] = CommercialPromotionEvidence(original, source, snapshot)


def with_window(data, issues):
    snapshot = gate("window", blocking_issues=issues)
    data["required_gate_verifiers"]["window"] = snapshot.verifier
    data["current_source_hashes"]["window"] = sha(snapshot.source)
    data["window"] = snapshot


def test_soft_rewrite_can_clear_hard_evidence_without_regrading_or_granting_adoption():
    data = inputs()
    before = deepcopy(data)
    result = classify_promotion_hard_actions(**data)
    assert result["hard_gates_passed"] is True
    assert result["soft_debt_evidence_eligible"] is True
    assert result["replacement_supported"] is True
    assert result["blocking_codes"] == result["unknown_reasons"] == []
    assert result["original_review_sha256"] == sha(data["review_result"])
    assert data == before
    assert data["review_result"]["verdict"] == "rewrite"
    assert data["review_result"]["scores"]["overall"] == .62
    assert all(result[k] is False for k in (
        "scores_modified", "verdict_modified", "promotion_approved", "debt_authorized", "grants_credit",
    ))


@pytest.mark.parametrize("name", list(GATES))
def test_missing_required_gate_is_unknown_not_verified_empty(name):
    data = inputs()
    data["gate_reports"].pop(name)
    result = classify_promotion_hard_actions(**data)
    assert not result["hard_gates_passed"]
    assert result["replacement_supported"] is False
    assert f"{name}:bound_server_report_missing" in result["unknown_reasons"]


@pytest.mark.parametrize("failure", ["body", "chapter", "source", "version", "coverage", "status", "raw_mapping"])
def test_stale_incomplete_or_metadata_reports_cannot_grant_source_clearance(failure):
    data = inputs()
    value = data["gate_reports"]["explicit_plan"]
    if failure == "body":
        value = replace(value, body_sha256="f" * 64)
    elif failure == "chapter":
        value = replace(value, chapter_number=3)
    elif failure == "source":
        value = replace(value, source={"revision": "other-current-plan"})
    elif failure == "version":
        value = replace(value, verifier="unsupported.v9")
    elif failure == "raw_mapping":
        value = {"source": "user", "verified": True, **value.report}
    else:
        value = replace(value, report={**value.report, failure: "unknown"})
    data["gate_reports"]["explicit_plan"] = value
    result = classify_promotion_hard_actions(**data)
    assert not result["hard_gates_passed"] and result["unknown_reasons"]


@pytest.mark.parametrize("failure", ["provider", "fallback", "finish", "body", "rewrite"])
def test_primary_requires_real_complete_body_bound_submission_and_typed_clearance(failure):
    data = inputs()
    primary = data["primary_completion"]
    if failure == "provider":
        primary.provider = "mock"
    elif failure == "fallback":
        primary.fallback_used = True
    elif failure == "finish":
        primary.finish_reason = "length"
    else:
        document = json.loads(primary.tool_calls[0]["function"]["arguments"])
        if failure == "body":
            document["citations"][0]["paragraph_number"] = 999
        else:
            document["verdict"] = "rewrite"
        primary.tool_calls[0]["function"]["arguments"] = json.dumps(document)
        primary.raw_message["tool_calls"] = deepcopy(primary.tool_calls)
    result = classify_promotion_hard_actions(**data)
    assert not result["hard_gates_passed"]
    assert result["blocking_codes"] == []
    assert any(reason.startswith("primary:") for reason in result["unknown_reasons"])


@pytest.mark.parametrize("code", ["FORBIDDEN_TERM_HIT", "PARAGRAPH_DUPLICATE_PARAPHRASE", "LENGTH_UNDER"])
def test_source_body_hard_deterministic_codes_survive_audit_default_and_low_severity(code):
    data = inputs()
    data["gate_reports"]["deterministic"] = gate(
        "deterministic", findings=[{"code": code, "severity": "low"}], passed=False)
    result = classify_promotion_hard_actions(**data)
    assert code in result["blocking_codes"] and not result["hard_gates_passed"]


@pytest.mark.parametrize("code,configured,blocked", [
    ("OPENING_PRESSURE_THIN", "audit_only", False),
    ("OPENING_PRESSURE_THIN", "block", True),
    ("NEW_UNREGISTERED_DETECTOR", None, True),
    ("NEW_UNREGISTERED_DETECTOR", "audit_only", False),
])
def test_deterministic_core_reuses_existing_effective_actions(code, configured, blocked):
    data = inputs()
    data["gate_config"] = GateConfig({code: configured} if configured else {})
    data["gate_reports"]["deterministic"] = gate(
        "deterministic", findings=[{"code": code, "severity": "critical"}], passed=False)
    result = classify_promotion_hard_actions(**data)
    assert bool(result["blocking_codes"]) is blocked
    assert result["hard_gates_passed"] is not blocked


def test_deterministic_missing_findings_does_not_use_empty_default():
    data = inputs()
    value = data["gate_reports"]["deterministic"]
    report = dict(value.report)
    report.pop("findings")
    data["gate_reports"]["deterministic"] = replace(value, report=report)
    result = classify_promotion_hard_actions(**data)
    assert "deterministic:findings_missing" in result["unknown_reasons"]


def test_known_hard_blocker_is_preserved_alongside_missing_primary_unknown():
    data = inputs()
    data["primary_completion"] = None
    data["gate_reports"]["explicit_plan"] = gate(
        "explicit_plan", hard=[{"code": "VERIFIED_CURRENT_PLAN_REQUIREMENT", "severity": "medium"}])
    result = classify_promotion_hard_actions(**data)
    assert result["status"] == "unknown"
    assert result["blocking_codes"] == ["VERIFIED_CURRENT_PLAN_REQUIREMENT"]


@pytest.mark.parametrize("failure", ["submission", "run_id", "source"])
def test_body_citations_alone_cannot_reuse_primary_from_another_source_or_paid_return(failure):
    data = inputs()
    snapshot = data["gate_reports"]["primary"]
    if failure == "source":
        snapshot = replace(snapshot, source={"different_current_source": True})
    else:
        key = "submission_sha256" if failure == "submission" else "llm_run_id"
        snapshot = replace(snapshot, report={**snapshot.report, key: "foreign"})
    data["gate_reports"]["primary"] = snapshot
    result = classify_promotion_hard_actions(**data)
    assert not result["hard_gates_passed"]
    assert any(reason.startswith("primary:") for reason in result["unknown_reasons"])


def test_unresolved_plan_hard_check_does_not_become_empty_clearance():
    data = inputs()
    data["gate_reports"]["explicit_plan"] = gate(
        "explicit_plan", unknown_issues=[{"code": "APPLICABILITY_UNVERIFIED"}])
    result = classify_promotion_hard_actions(**data)
    assert "explicit_plan:source_checks_unresolved" in result["unknown_reasons"]
    assert not result["soft_debt_evidence_eligible"]


def test_raw_critical_craft_finding_is_not_its_own_source_hard_authority():
    data = inputs()
    data["review_result"]["findings"][0]["severity"] = "critical"
    result = classify_promotion_hard_actions(**data)
    assert result["hard_gates_passed"] is True
    assert data["review_result"]["findings"][0]["severity"] == "critical"


def test_unregistered_review_finding_is_unknown_even_at_low_severity():
    data = inputs()
    data["review_result"]["findings"][0].update(category="new_source_detector", severity="low")
    result = classify_promotion_hard_actions(**data)
    assert result["blocking_codes"] == []
    assert "review:unclassified_finding:new_source_detector" in result["unknown_reasons"]


@pytest.mark.parametrize("producer", ["commercial", "window"])
def test_existing_evaluator_summary_cannot_be_silently_omitted_from_classification(producer):
    data = inputs()
    data["review_result"]["evidence_summary"][f"llm_{producer}_quality_judge" if producer == "window"
                                             else "llm_commercial_judge"] = {"pass": False}
    result = classify_promotion_hard_actions(**data)
    assert f"{producer}:existing_evidence_not_classified" in result["unknown_reasons"]


def test_incomplete_window_report_cannot_use_missing_array_as_no_issues():
    data = inputs()
    with_window(data, [])
    report = dict(data["window"].report)
    report.pop("blocking_issues")
    data["window"] = replace(data["window"], report=report)
    assert not classify_promotion_hard_actions(**data)["hard_gates_passed"]


@pytest.mark.parametrize("code", ["RHYTHM_FATIGUE_DUAL_HIGH_PRESSURE", "TEMPLATE_FIRST_LINE_REPEAT", "CANON_FORBIDDEN_TERM"])
def test_raw_window_critical_cannot_gain_canon_or_formula_authority(code):
    data = inputs()
    data["gate_config"] = GateConfig({}, "block")
    with_window(data, [{"code": code, "severity": "critical", "evidence": BODY}])
    result = classify_promotion_hard_actions(**data)
    assert result["hard_gates_passed"] is True
    assert result["issue_actions"][-1]["effective_action"] == "audit_only"


@pytest.mark.parametrize("action,proof,expected", [("audit_only", False, "clear"), ("block", False, "unknown"), ("block", True, "blocked")])
def test_registered_window_craft_action_needs_config_and_actual_source_evidence(action, proof, expected):
    data = inputs()
    data["gate_config"] = GateConfig({"ENDING_SENTENCE_WEAK": action})
    issue = {"code": "ENDING_SENTENCE_WEAK", "severity": "critical", "evidence": BODY}
    if proof:
        issue["source_evidence"] = {}
    with_window(data, [issue])
    assert classify_promotion_hard_actions(**data)["status"] == expected


@pytest.mark.asyncio
async def test_commercial_critical_narrative_is_audit_without_flipping_scalar_pass(stable):
    src = sources()
    original = await stable([slot(src, i) for i in range(3)])
    before = deepcopy(original.model_dump(mode="json"))
    data = inputs()
    with_commercial(data, original, src)
    result = classify_promotion_hard_actions(**data)
    assert result["hard_gates_passed"] is True
    assert original.passed is False
    assert original.model_dump(mode="json") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["prior_fact_conflict", "constraint_violation"])
async def test_nonrepresentative_high_source_blocker_is_not_lost_from_lineage(stable, kind):
    src = sources(rule=kind == "constraint_violation")
    original = await stable([
        slot(src, 0), slot(src, 1),
        slot(src, 2, code="SOURCE_RULE_FAILURE", kind=kind, severity="high", score=.83),
    ])
    data = inputs()
    with_commercial(data, original, src)
    result = classify_promotion_hard_actions(**data)
    assert "SOURCE_RULE_FAILURE" in result["blocking_codes"]
    assert not result["hard_gates_passed"]


@pytest.mark.asyncio
async def test_commercial_unknown_slot_abstains_without_changing_existing_quorum_policy(stable):
    src = sources()
    original = await stable([slot(src, 0), slot(src, 1), slot(src, 2, unknown=True)])
    data = inputs()
    with_commercial(data, original, src)
    result = classify_promotion_hard_actions(**data)
    assert result["commercial_unknown_sample_slots"] == ["chapter-quality.independent-v2:2"]
    assert "commercial:unknown_sample_slots" not in result["unknown_reasons"]
    assert result["hard_gates_passed"] is True


@pytest.mark.asyncio
async def test_retained_commercial_numeric_policy_is_debt_not_source_authority(stable):
    src = sources()
    original = await stable([slot(src, i, score=.7) for i in range(3)])
    data = inputs()
    with_commercial(data, original, src)
    result = classify_promotion_hard_actions(**data)
    assert result["hard_gates_passed"] is True
    assert original.overall_score == pytest.approx(.7) and original.passed is False


@pytest.mark.asyncio
async def test_mismatched_physical_cohort_snapshot_cannot_borrow_projection_proof(stable):
    src = sources()
    original = await stable([slot(src, i) for i in range(3)])
    data = inputs()
    with_commercial(data, original, src)
    snapshot = data["commercial"].cohort
    bad = replace(snapshot, report={**snapshot.report, "aggregate_sha256": "f" * 64})
    data["commercial"] = replace(data["commercial"], cohort=bad)
    assert "commercial:cohort_or_body_changed" in classify_promotion_hard_actions(**data)["unknown_reasons"]
