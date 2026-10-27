"""Pure retained-return replay and unrelated English/Chinese protocol cases."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from bestseller.services import planning_draft_admission as admission
from bestseller.services.planning_admission_diagnostics import collect_protocol_feedback

pytestmark = pytest.mark.unit


@pytest.fixture(params=["en", "zh"])
def specimen(request):
    name, detail = (
        ("Pilot", "The signal is red.") if request.param == "en" else ("领航员", "信号灯是红色。")
    )
    candidate = {
        "chapters": [
            {
                "chapter_number": 4,
                "opening_situation": detail,
                "scenes": [
                    {"participants": [name], "action_sequence": ["A", "B"]},
                    {"participants": [name], "action_sequence": ["C"]},
                ],
            }
        ]
    }
    supplied = {
        "approved_creation_contracts": {"identity": name},
        "adopted_continuity": {"recent_adopted_prose": [{"body": detail}]},
        "approved_world_spec": {"rule": detail},
        "approved_book_spec": {"pacing": "slow"},
    }
    supplied["source_authority"] = admission._source_authority(supplied)
    result = {
        "draft_eligible": False,
        "hard_violations": [
            {
                "code": "CONTINUITY_CONFLICT",
                "path": "chapters[0].scenes[0]",
                "evidence": detail,
                "source_requirement": detail,
                "source_refs": ["/adopted_continuity/recent_adopted_prose/0/body"],
            }
        ],
        "editorial_notes": [],
        "projection_checks": [
            {
                **target,
                "status": "conflict",
                "explanation": detail,
                "related_violation_paths": ["chapters[0].scenes[0]"],
            }
            for target in admission.projection_audit_targets(candidate)
        ],
    }
    return candidate, supplied, result


def feedback(specimen, result=None, validator=None):
    candidate, supplied, original = specimen
    return collect_protocol_feedback(
        json.dumps(result or original, ensure_ascii=False), candidate, supplied, validator
    )


def test_valid_protocol_is_not_semantic_approval_or_grade(specimen):
    before = deepcopy(specimen)
    assert feedback(specimen, validator=admission._grade) == []
    assert specimen == before
    # An existing pointer of the right role cannot establish entailment. A
    # false quotation is a semantic task, not something this helper can detect.
    result = deepcopy(specimen[2])
    result["hard_violations"][0]["evidence"] = "The adopted signal was blue / 已采用信号为蓝色。"
    assert feedback(specimen, result) == []


def test_collects_all_independent_path_dependency_authority_errors(specimen):
    candidate, supplied, result = deepcopy(specimen)
    result["hard_violations"][0]["path"] = "candidate:chapters[0].scenes[0].action_sequence[0]"
    result["hard_violations"][0]["source_refs"] = [
        "adopted_continuity/recent_adopted_prose/0/body",
        "candidate:chapters[0].scenes[0].action_sequence[8]",
        "/approved_book_spec/pacing",
        "/adopted_continuity/not_a_leaf",
        "/adopted_continuity/bad~2pointer",
    ]
    result["hard_violations"].append(
        {
            **deepcopy(result["hard_violations"][0]),
            "path": "chapters[0].scenes[0].entry_state 与 chapters[0].scenes[1].entry_state",
        }
    )
    result["projection_checks"][0]["related_violation_paths"] = [
        result["hard_violations"][0]["path"],
        result["hard_violations"][0]["path"],
        "chapters[99]",
    ]
    errors = feedback((candidate, supplied, result), validator=admission._grade)
    indexed = {(tuple(e["loc"]), e["code"]): e for e in errors}
    first = indexed[(("hard_violations", 0, "path"), "admission_violation_path_invalid")]
    assert first["reason"] == "candidate_prefix_not_allowed"
    assert first["value"] == result["hard_violations"][0]["path"]
    assert (
        indexed[(("hard_violations", 1, "path"), "admission_violation_path_invalid")]["reason"]
        == "single_candidate_path_required"
    )
    assert (
        indexed[
            (
                ("projection_checks", 0, "related_violation_paths", 1),
                "admission_projection_dependency_invalid",
            )
        ]["reason"]
        == "repeated"
    )
    assert (
        indexed[
            (
                ("projection_checks", 0, "related_violation_paths", 2),
                "admission_projection_dependency_invalid",
            )
        ]["reason"]
        == "dangling"
    )
    assert (("hard_violations", 0, "source_refs", 0), "admission_source_pointer_invalid") in indexed
    assert (("hard_violations", 0, "source_refs", 1), "admission_candidate_ref_invalid") in indexed
    assert (
        indexed[(("hard_violations", 0, "source_refs", 2), "admission_source_authority_invalid")][
            "actual_role"
        ]
        == "advisory"
    )
    assert (
        ("hard_violations", 0, "source_refs", 3),
        "admission_source_authority_invalid",
    ) in indexed
    assert (("hard_violations", 0, "source_refs", 4), "admission_source_pointer_invalid") in indexed
    assert first["allowed_target_scopes"]["values"] == sorted(
        item["path"] for item in admission.projection_audit_targets(candidate)
    )


def test_projection_coverage_hash_duplicates_and_consistency_all_reported(specimen):
    candidate, supplied, result = deepcopy(specimen)
    result["draft_eligible"] = True
    result["projection_checks"][0]["projection_hash"] = "0" * 64
    result["projection_checks"][1]["status"] = "consistent"
    result["projection_checks"].append(deepcopy(result["projection_checks"][0]))
    result["projection_checks"][2]["path"] = "chapters[99]"
    codes = {e["code"] for e in feedback((candidate, supplied, result))}
    assert {
        "admission_inconsistent_grade",
        "admission_projection_hash_mismatch",
        "admission_projection_conflict_eligible",
        "admission_projection_dependency_invalid",
        "admission_projection_coverage_mismatch",
        "admission_duplicate_projection_check",
    } <= codes


def test_schema_errors_do_not_hide_path_errors(specimen):
    result = deepcopy(specimen[2])
    result["draft_eligible"] = "false"
    result["hard_violations"][0]["path"] = "chapters[77]"
    errors = feedback(specimen, result)
    assert any(
        e["loc"] == ["draft_eligible"] and e["code"] == "admission_schema_invalid" for e in errors
    )
    assert any(e["code"] == "admission_violation_path_invalid" for e in errors)


def test_missing_authority_context_is_diagnostic_not_exception(specimen):
    errors = collect_protocol_feedback(json.dumps(specimen[2]), specimen[0], {})
    assert any(
        e["code"] == "admission_diagnostic_context_invalid"
        and e["loc"] == ["supplied", "source_authority"]
        for e in errors
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("{bad", "admission_json_invalid"),
        ('{"draft_eligible":true,"draft_eligible":false}', "admission_duplicate_json_key"),
        ('{"nested":{"value":NaN,"value":Infinity}}', "admission_nonfinite_json"),
    ],
)
def test_strict_json_is_never_repaired(specimen, raw, expected):
    before = deepcopy(specimen)
    errors = collect_protocol_feedback(raw, specimen[0], specimen[1])
    assert any(e["code"] == expected for e in errors)
    assert specimen == before


def test_allowed_catalogs_bounded_without_truncating_candidate(specimen):
    candidate, supplied, result = deepcopy(specimen)
    candidate["chapters"][0]["scenes"] *= 50
    before = deepcopy(candidate)
    result["hard_violations"][0]["path"] = "bad"
    errors = collect_protocol_feedback(json.dumps(result), candidate, supplied)
    catalog = next(e["allowed_target_scopes"] for e in errors if "allowed_target_scopes" in e)
    assert catalog["total"] == 101 and len(catalog["values"]) == 32 and catalog["omitted"] == 69
    assert candidate == before


def test_actual_two_retained_returns_all_errors_without_regrading():
    root = Path(__file__).resolve().parents[2]
    report = (
        root
        / "reports/continuous-recovery-2026-10-16"
        / "20261017T184916651909Z-current-planning-failure-diagnostics.json"
    )
    if not report.exists():
        pytest.skip("local retained production fixture unavailable")
    state = json.loads(report.read_text())["workflows"][0]["metadata"]["candidates"][-1]
    candidate = state["candidate"]
    attempts = state["source_admission_protocol"]["attempts"]
    assert len(attempts) == 2
    for i, attempt in enumerate(attempts):
        completion = attempt["completion"]
        payload_root = root / "artifacts/runtime/llm_payloads"
        request = json.loads(
            (payload_root / (completion["llm_run_id"] + ".request.json")).read_text()
        )
        response = json.loads(
            (payload_root / (completion["llm_run_id"] + ".response.json")).read_text()
        )
        assert response["content"]["text"] == completion["content"]
        assert response["content"]["truncated"] is False
        assert request["replayable"] is False and request["messages"]["user"]["truncated"] is True
        # Decode only individually complete source fields in the retained
        # prefix. Rebuilt role catalog is fixture context, never full-request
        # replay evidence or a newly authorized grade.
        raw_request = request["messages"]["user"]["text"]
        supplied = {}
        for key in (
            "approved_creation_contracts",
            "approved_book_spec",
            "approved_world_spec",
            "approved_cast_spec",
            "adopted_continuity",
        ):
            marker = '"' + key + '":'
            start = raw_request.index(marker) + len(marker)
            supplied[key] = json.JSONDecoder().raw_decode(raw_request[start:])[0]
        supplied["source_authority"] = admission._source_authority(supplied)
        before = deepcopy((candidate, supplied, attempt))
        errors = collect_protocol_feedback(
            completion["content"], candidate, supplied, admission._grade
        )
        assert (candidate, supplied, attempt) == before
        assert any(e["code"] == "admission_projection_dependency_invalid" for e in errors)
        assert any(e["code"] == "admission_violation_path_invalid" for e in errors)
        assert any(e["code"] == "admission_candidate_ref_invalid" for e in errors)
        assert any(e["code"] == "admission_source_pointer_invalid" for e in errors)
        assert any(e["code"] == "admission_source_authority_invalid" for e in errors)
        if i == 0:
            assert any(e.get("reason") == "repeated" for e in errors)
        else:
            assert sum(e.get("reason") == "single_candidate_path_required" for e in errors) == 2
