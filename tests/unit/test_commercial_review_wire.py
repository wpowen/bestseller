from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from bestseller.services.commercial_review_wire import WIRE_VERSION, parse_commercial_wire
from bestseller.services.commercial_source_evidence import assemble_commercial_sources


def sources(*, rule=False):
    return assemble_commercial_sources(
        chapter_number=2,
        content_md="信使把铜钥匙收进盒子。\n\n次日他拿出了银钥匙。",
        previous_chapters=[{"chapter_number": 1, "body": "昨日匠人交付铜钥匙。"}],
        source={
            "established_facts": {"facts": [{"text": "港口昨日已经关闭。"}]},
            **(
                {
                    "approved_chapter_contract": {
                        "text": "使用第三人称。",
                        "hard": True,
                        "source": "user",
                    }
                }
                if rule
                else {}
            ),
        },
    )


def wire(src=None, kind="narrative_quality", *, score=0.61, severity="critical"):
    src = src or sources()
    current = next(e for e in src.catalog.values() if e["kind"] == "current")
    issue = {
        "code": "MISSING_MOTIVATION",
        "severity": severity,
        "assessment_kind": kind,
        "body_id": current["id"],
        "reason": "选择缺少动机。读者无法理解行动因果。",
        "required_fix": "补足已有行动的动机。",
    }
    if kind != "narrative_quality":
        source_kind = {
            "prior_fact_conflict": "prior",
            "constraint_violation": "constraint",
            "current_fact_conflict": "current",
        }[kind]
        issue["source_id"] = [e for e in src.catalog.values() if e["kind"] == source_kind][-1]["id"]
    if kind in ("prior_fact_conflict", "current_fact_conflict"):
        issue["temporal_relation"] = "same_event_or_state"
    result = {
        "wire_version": WIRE_VERSION,
        "pass": False,
        "overall_score": score,
        "dimension_scores": {"readability": score},
        "issues": [issue],
        "rewrite_plan": {"scope": "chapter", "change": ["补足动机"]},
    }
    rules = [key for key, e in src.catalog.items() if e["kind"] == "constraint"]
    if rules:
        result["checks"] = {
            key: {"result": "FAIL", "body_id": current["id"], "reason": "明确违例。"}
            for key in rules
        }
    return result


@pytest.mark.parametrize(
    "kind",
    ["narrative_quality", "prior_fact_conflict", "current_fact_conflict", "constraint_violation"],
)
def test_source_id_resolution_retains_actual_scores_and_all_model_issues(kind):
    src = sources(rule=kind == "constraint_violation")
    payload = wire(src, kind)
    original = deepcopy(payload)
    raw = json.dumps(payload)
    domain, receipt, errors = parse_commercial_wire(raw, src)
    assert errors == () and receipt["status"] == "verified"
    assert payload == original and domain["overall_score"] == 0.61
    assert domain["dimension_scores"] == payload["dimension_scores"]
    assert receipt["issues_dropped"] == 0 and not receipt["scores_modified"]
    issue = domain["blocking_issues"][0]
    assert issue["evidence"] == src.catalog[payload["issues"][0]["body_id"]]["text"]
    assert issue["body_citations"][0]["kind"] == "current"
    assert len(domain["blocking_issues"]) == 1
    if kind != "narrative_quality":
        assert issue["source_assertion"] == src.catalog[payload["issues"][0]["source_id"]]["text"]
        assert issue["current_assertion"] == issue["evidence"]
    assert receipt["raw_report_sha256"]


@pytest.mark.parametrize(
    "severity,group",
    [
        ("critical", "blocking_issues"),
        ("high", "blocking_issues"),
        ("medium", "audit_issues"),
        ("low", "audit_issues"),
    ],
)
def test_flat_issues_route_by_original_severity_without_dropping_or_score_change(severity, group):
    payload = wire(severity=severity, score=0.97)
    payload["issues"] *= 7
    domain, receipt, errors = parse_commercial_wire(json.dumps(payload), sources())
    assert errors == () and len(domain[group]) == 7
    assert all(i["severity"] == severity for i in domain[group])
    assert domain["overall_score"] == 0.97 and receipt["issues_dropped"] == 0


@pytest.mark.parametrize(
    "failure",
    [
        "legacy_v1",
        "missing_version",
        "invented_quote",
        "foreign_body",
        "future_source",
        "wrong_kind",
        "temporal_cap",
        "invented_checks",
        "old_audit",
        "missing_reason",
        "invalid_score",
        "narrative_disguised_fact",
    ],
)
def test_invalid_new_report_never_becomes_a_verified_quality_verdict(failure):
    src = sources()
    payload = wire(src, "prior_fact_conflict", score=0.99)
    issue = payload["issues"][0]
    if failure == "legacy_v1":
        payload["wire_version"] = "commercial-review-wire.v1"
    if failure == "missing_version":
        payload.pop("wire_version")
    if failure == "invented_quote":
        issue["quote"] = "并不存在的文字。"
    if failure == "foreign_body":
        issue["body_id"] = "Dforeign:P0001"
    if failure == "future_source":
        issue["source_id"] = "PREV10:P0001"
    if failure == "wrong_kind":
        issue["source_id"] = issue["body_id"]
    if failure == "temporal_cap":
        issue["temporal_relation"] = "later_progression"
    if failure == "invented_checks":
        payload["checks"] = {"hard_gate": {"result": "FAIL"}}
    if failure == "old_audit":
        payload["audit_issues"] = []
    if failure == "missing_reason":
        issue["reason"] = ""
    if failure == "invalid_score":
        payload["overall_score"] = True
    if failure == "narrative_disguised_fact":
        issue["assessment_kind"] = "narrative_quality"
    before = deepcopy(payload)
    returned, receipt, errors = parse_commercial_wire(json.dumps(payload), src)
    assert errors and receipt["status"] == "rejected"
    assert "domain_mapping_sha256" not in receipt and returned == before == payload
    assert receipt["issues_dropped"] == 0 and not receipt["scores_modified"]


def test_reference_id_cannot_impersonate_an_observation():
    src = sources()
    payload = wire(src, "prior_fact_conflict")
    catalog = deepcopy(src.catalog)
    catalog[payload["issues"][0]["source_id"]]["kind"] = "reference"
    _, _, errors = parse_commercial_wire(json.dumps(payload), replace(src, catalog=catalog))
    assert any("incorrectly typed source" in e for e in errors)


@pytest.mark.parametrize("failure", ["unknown_rule", "missing_rule", "foreign_body"])
def test_rule_checks_require_only_actual_authorized_ids(failure):
    src = sources(rule=True)
    payload = wire(src, "constraint_violation")
    rule_id = next(iter(payload["checks"]))
    if failure == "unknown_rule":
        payload["checks"]["invented"] = payload["checks"][rule_id]
    if failure == "missing_rule":
        payload["checks"] = {}
    if failure == "foreign_body":
        payload["checks"][rule_id]["body_id"] = "Dforeign:P0001"
    returned, receipt, errors = parse_commercial_wire(json.dumps(payload), src)
    assert errors and receipt["status"] == "rejected" and returned == payload


def test_four_explicit_rule_failures_cannot_hide_behind_focus_limit_or_high_score():
    src = assemble_commercial_sources(
        chapter_number=2,
        content_md="信使收好信件。",
        previous_chapters=[],
        source={
            "approved_creation_contracts": [
                {"hard": True, "source": "user", "text": t}
                for t in ("使用第一人称。", "保持现在时。", "保留信件。", "保持收信人匿名。")
            ]
        },
    )
    body = next(e for e in src.catalog.values() if e["kind"] == "current")
    rules = [key for key, e in src.catalog.items() if e["kind"] == "constraint"]
    assert len(rules) == 4
    payload = {
        "wire_version": WIRE_VERSION,
        "pass": True,
        "overall_score": 0.99,
        "dimension_scores": {"readability": 0.99},
        "issues": [],
        "rewrite_plan": {},
        "checks": {
            key: {"result": "FAIL", "body_id": body["id"], "reason": "该处违反所引创作者条款。"}
            for key in rules
        },
    }
    before = deepcopy(payload)
    domain, receipt, errors = parse_commercial_wire(json.dumps(payload), src)
    assert errors == () and len(domain["blocking_issues"]) == 4 and payload == before
    assert receipt["derived_issues_added"] == 4 and domain["overall_score"] == 0.99
    from bestseller.domain.llm_quality_judge import quality_judge_result_from_mapping

    judged = quality_judge_result_from_mapping(domain, scope="chapter", min_overall=0.8)
    assert not judged.passed and judged.has_critical


def test_duplicate_json_fields_cannot_override_a_failed_report():
    raw = json.dumps(wire())[:-1] + ',"pass":true}'
    _, receipt, errors = parse_commercial_wire(raw, sources())
    assert errors and receipt["status"] == "rejected"
