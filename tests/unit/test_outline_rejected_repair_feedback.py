"""Retain the best graded source without forgetting a rejected repair's diagnosis."""
from copy import deepcopy
import json

import pytest

from bestseller.services import planner
from bestseller.services.prompt_source_policy import content_hash

pytestmark = pytest.mark.unit


def _payload(goal, *, run="source-run"):
    return {"batch_name": "first-window", "chapters": [{"chapter_number": 1,
        "goal": goal, "scenes": [{"scene_number": 1, "purpose": {"story": goal}}]}],
        "_meta": {"workflow_run_id": run, "input_hash": "source-input"}}


def _report(score, *, evidence="The receipt is not visible before the inference.", code="MISSING_RECEIPT"):
    return {"passed": False, "overall_score": score, "graded_outline_hash": "original-graded-hash",
        "outline_repair_source_snapshot_hash": "original-source-snapshot",
        "repair_directives": ["REPAIR_RETAINED_RECEIPT"],
        "blocking_issues": [{"code": code, "severity": "high", "evidence": evidence,
                             "required_fix": "UNTRUSTED_ADVICE: invent a new ability."}],
        "audit_issues": [], "llm_run_id": "judge-source-run"}


def _select(best, best_report, candidate, candidate_report, *, round_idx=0, max_rounds=3):
    return planner._select_outline_repair_source_and_directives(
        best_payload=best, best_report=best_report, candidate_payload=candidate,
        candidate_report=candidate_report, project_metadata={}, round_idx=round_idx, max_rounds=max_rounds)


def _strip_feedback(report):
    return {key: value for key, value in report.items() if key != "rejected_repair_feedback"}


def test_worse_revision_keeps_best_source_grade_and_carries_exact_current_rejection():
    best = _payload("Recover the receipt before the depot closes.")
    best_report = _report(0.79)
    rejected = _payload("Invent a shortcut that loses the receipt.", run="repair-run")
    rejected_report = _report(0.42, evidence="The repair removes the receipt and gives Alice unsupported knowledge.",
                              code="REPAIR_KNOWLEDGE_REGRESSION")
    originals = deepcopy((best, best_report, rejected, rejected_report))
    source, report, directives = _select(best, best_report, rejected, rejected_report)
    assert source == best and source != rejected
    assert _strip_feedback(report) == best_report
    assert report["overall_score"] == 0.79
    assert report["graded_outline_hash"] == best_report["graded_outline_hash"]
    assert report["outline_repair_source_snapshot_hash"] == best_report["outline_repair_source_snapshot_hash"]
    assert directives == best_report["repair_directives"]
    feedback = report["rejected_repair_feedback"]
    assert feedback["candidate_hash"] == content_hash(rejected)
    assert feedback["report"] == rejected_report
    assert feedback["report_hash"] == content_hash(rejected_report)
    assert feedback["report"]["overall_score"] == 0.42
    assert feedback["authority"] == "untrusted_rejected_revision_diagnostics_only"
    assert not feedback["repeated_without_new_diagnostics"]
    assert not feedback["report_truncated"]
    assert (best, best_report, rejected, rejected_report) == originals
    feedback["report"]["blocking_issues"][0]["evidence"] = "mutated returned copy"
    assert rejected_report == originals[3]


def test_repeated_rejection_stops_even_if_workflow_and_judge_run_ids_change():
    best, best_report = _payload("Keep the receipt."), _report(0.79)
    rejected, rejected_report = _payload("Lose the receipt."), _report(0.42)
    source, report, _ = _select(best, best_report, rejected, rejected_report)
    original_source, original_report = deepcopy(source), deepcopy(report)
    replay = deepcopy(rejected)
    replay["_meta"]["workflow_run_id"] = "another-workflow-same-body"
    replay_report = deepcopy(rejected_report)
    replay_report.update(llm_run_id="another-judge-run", cache_hit=True, overall_score=0.41)
    next_source, next_report, directives = _select(source, report, replay, replay_report, round_idx=1)
    feedback = next_report["rejected_repair_feedback"]
    assert next_source == best and next_report["overall_score"] == 0.79
    assert directives == []
    assert feedback["repeated_without_new_diagnostics"]
    assert feedback["report"] == replay_report  # Current failure is not replaced by the old 0.42 report.
    assert feedback["candidate_hash"] == content_hash(replay)
    assert feedback["feedback_hash"] == report["rejected_repair_feedback"]["feedback_hash"]
    assert source == original_source and report == original_report


def test_same_body_with_new_critical_diagnostic_can_receive_a_corrective_round():
    best, best_report = _payload("Keep the receipt."), _report(0.79)
    rejected = _payload("Lose the receipt.")
    source, report, _ = _select(best, best_report, rejected, _report(0.42))
    new_report = _report(0.42, code="WRONG_SENDER", evidence="The revised signature contradicts the approved sender.")
    new_report["blocking_issues"][0]["severity"] = "critical"
    next_source, next_report, directives = _select(source, report, rejected, new_report, round_idx=1)
    assert next_source == best and next_report["overall_score"] == 0.79 and directives
    feedback = next_report["rejected_repair_feedback"]
    assert feedback["report"] == new_report
    assert not feedback["repeated_without_new_diagnostics"]
    assert feedback["feedback_hash"] != report["rejected_repair_feedback"]["feedback_hash"]


def test_identical_rejected_baseline_also_stops_when_repair_makes_no_progress():
    best, best_report = _payload("Receipt remains unexplained."), _report(0.79)
    source, report, first_directives = _select(None, None, best, best_report)
    assert first_directives and report["rejected_repair_feedback"]["same_body_as_retained_source"]
    _, replay_report, directives = _select(source, report, best, best_report, round_idx=1)
    assert not directives and replay_report["rejected_repair_feedback"]["repeated_without_new_diagnostics"]
    assert _strip_feedback(replay_report) == best_report


def test_feedback_history_is_bounded_and_still_detects_a_recent_cycle():
    source, report = _payload("Best receipt path."), _report(0.79)
    cases = []
    for index in range(6):
        candidate = _payload(f"Rejected alternate {index}.")
        failure = _report(0.42, evidence=f"Failure evidence {index}.")
        cases.append((candidate, failure))
        source, report, directives = _select(source, report, candidate, failure)
        assert directives
        assert len(report["rejected_repair_feedback"]["previous_attempt_hashes"]) <= 3
    _, _, directives = _select(source, report, *cases[-2], round_idx=1)
    assert not directives


def test_oversized_feedback_preserves_actual_critical_findings_without_nested_reports():
    best, best_report = _payload("Best receipt path."), _report(0.79)
    rejected = _payload("Worse receipt path.")
    failure = _report(0.42)
    failure["blocking_issues"] = [{"code": f"LOW_{n}", "severity": "low", "evidence": "low evidence " * 1000}
                                  for n in range(20)]
    failure["blocking_issues"].append({"code": "ACTUAL_CRITICAL", "severity": "critical",
        "evidence": "CURRENT_CRITICAL_EVIDENCE " * 1000, "required_fix": "UNTRUSTED_FIX " * 1000})
    failure["audit_issues"] = deepcopy(failure["blocking_issues"])
    failure["dimension_scores"] = {f"dimension_{i}": 0.2 for i in range(40)}
    failure["rejected_repair_feedback"] = {"report": {"nested": "STALE_RECURSIVE_FEEDBACK"}}
    before = deepcopy(failure)
    source, report, _ = _select(best, best_report, rejected, failure)
    feedback = report["rejected_repair_feedback"]
    assert source == best and _strip_feedback(report) == best_report
    assert feedback["report_truncated"]
    assert feedback["report"]["overall_score"] == 0.42
    assert len(feedback["report"]["blocking_issues"]) <= 4
    assert len(feedback["report"]["audit_issues"]) <= 2
    assert len(feedback["report"]["dimension_scores"]) <= 16
    assert feedback["report"]["blocking_issues"][0]["code"] == "ACTUAL_CRITICAL"
    assert "CURRENT_CRITICAL_EVIDENCE" in feedback["report"]["blocking_issues"][0]["evidence"]
    assert "STALE_RECURSIVE_FEEDBACK" not in json.dumps(feedback)
    assert len(json.dumps(feedback, ensure_ascii=False)) <= 12000
    assert failure == before


@pytest.mark.parametrize("round_idx,max_rounds", [(3, 10), (1, 1), (0, 0)])
def test_effective_repair_budget_never_exceeds_three_or_existing_lower_limit(round_idx, max_rounds):
    best, best_report = _payload("Best receipt path."), _report(0.79)
    candidate, failure = _payload("Worse receipt path."), _report(0.42)
    source, report, directives = _select(best, best_report, candidate, failure,
                                         round_idx=round_idx, max_rounds=max_rounds)
    assert source == best and report["overall_score"] == 0.79
    assert directives == []
    assert report["rejected_repair_feedback"]["report"] == failure


@pytest.mark.parametrize("which_passes", ["candidate", "best"])
def test_a_passed_source_is_not_replaced_or_reopened_by_rejection_feedback(which_passes):
    best, best_report = _payload("Best receipt path."), _report(0.79)
    candidate, candidate_report = _payload("Lower score but passed."), _report(0.42)
    (candidate_report if which_passes == "candidate" else best_report)["passed"] = True
    source, report, directives = _select(best, best_report, candidate, candidate_report)
    assert source == (candidate if which_passes == "candidate" else best)
    assert report == (candidate_report if which_passes == "candidate" else best_report)
    assert directives == []


def test_a_better_rejected_candidate_remains_paired_with_its_own_grade():
    best, best_report = _payload("Old source."), _report(0.6)
    candidate, candidate_report = _payload("Improved source."), _report(0.79)
    source, report, directives = _select(best, best_report, candidate, candidate_report)
    assert source == candidate and _strip_feedback(report) == candidate_report
    assert report["overall_score"] == 0.79 and directives
    assert "rejected_repair_feedback" not in report
