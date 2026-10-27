"""Explicit ambiguity authorization, not retrospective attribution or credit."""

from copy import deepcopy
import hashlib
from uuid import uuid4

import pytest

from bestseller.services.commercial_ambiguity_retirement import (
    MEANING,
    build_ambiguity_retirement_receipt,
    validate_ambiguity_retirement_receipt,
)

pytestmark = pytest.mark.unit


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def case():
    scope, h, model = "conception:original", sha("retained input"), "openai/fixture"
    key, aid = sha("original completed operation"), "original-completed-attempt"
    meta = {
        "effective_input_receipt": {"schema": "effective-input.v1", "hash": h, "model": model},
        "execution_receipt": {
            "scope_id": scope,
            "operation_key": key,
            "attempts": [{"attempt_id": aid}],
        },
        "llm_run_flushed": False,
    }
    result = {
        "content": "Original paid prose mentioning submit_chapter_review is story data.",
        "provider": "fixture",
        "model_name": model,
        "input_tokens": 20,
        "output_tokens": 10,
        "finish_reason": "stop",
        "tool_calls": None,
        "raw_message": None,
        "metadata": meta,
    }
    root = {
        "key": key,
        "scope_id": scope,
        "input_hash": h,
        "status": "completed",
        "result_json": result,
    }
    transport = {
        "key": sha(f"transport-result.v1\n{scope}\n{aid}"),
        "scope_id": scope,
        "input_hash": h,
        "status": "transport_result",
        "result_json": {
            "schema": "transport-result.v1",
            "attempt_id": aid,
            "operation_key": key,
            "input_hash": h,
            "result": deepcopy(result),
        },
    }
    attempt = {
        "id": aid,
        "operation_key": key,
        "scope_id": scope,
        "input_hash": h,
        "model_name": model,
        "status": "completed",
        "reserved_tokens": 200,
        "actual_tokens": 30,
    }
    return {
        "project_id": str(uuid4()),
        "chapter_id": str(uuid4()),
        "execution_scope_id": scope,
        "body_sha256": sha("current body"),
        "current_effective_input_hash": sha("current exact request"),
        "target_protocol": {
            "prompt_version": "v11-structured-review-tool",
            "source_manifest": {"typed_policy.py": sha("actual policy")},
            "evaluation_contract": {"schema": "typed-assessment.v1", "samples": 3},
        },
        "stable_cohort_key": sha("existing body cohort"),
        "allowed_slot_labels": [
            "chapter-quality.independent-v2:0",
            "chapter-quality.independent-v2:1",
            "chapter-quality.independent-v2:2",
        ],
        "actor": "authorized operator",
        "reason": "New typed assessment despite original ambiguity",
        "original_authorization_ref": "existing-explicit-completion-bound-authorization",
        "snapshots": {
            "operations": [root, transport],
            "attempts": [attempt],
            "quarantine_receipts": [],
        },
    }


def stopped(case):
    s, scope = case["snapshots"], case["execution_scope_id"]
    key, aid, h, rid = (
        sha("stopped original"),
        "charged-unknown-attempt",
        sha("unknown hash domain"),
        "charged-proof",
    )
    s["operations"].append(
        {"key": key, "scope_id": scope, "input_hash": h, "status": "stopped", "result_json": None}
    )
    s["attempts"].append(
        {
            "id": aid,
            "operation_key": key,
            "scope_id": scope,
            "input_hash": h,
            "model_name": "openai/fixture",
            "status": "reconciled_charged",
            "reserved_tokens": 700,
            "actual_tokens": None,
        }
    )
    row = {
        "key": sha(f"reconciliation.v1\n{scope}\n{rid}"),
        "scope_id": scope,
        "status": "reconciliation_receipt",
        "input_hash": sha("original reconciliation args"),
        "result_json": {
            "reconciliation_id": rid,
            "request_hash": sha("original reconciliation args"),
            "actor": "original operator",
            "evidence_kind": "dispatcher_exit",
            "evidence_sha256": sha("exact dispatcher evidence"),
            "local_dispatcher_stopped": True,
            "remote_operation_closed": False,
            "receipt": {
                "scope_id": scope,
                "reconciliation_id": rid,
                "attempt_id": aid,
                "decision": "quarantine_charged",
                "credit_released": False,
                "operation_replay_allowed": False,
                "automatic_resume": False,
                "remote_outcome": "unknown_quarantined",
                "remaining_unknown": 0,
            },
        },
    }
    s["operations"].append(row)
    s["quarantine_receipts"].append(deepcopy(row))
    return key, row


def test_completed_and_unwitnessed_stopped_authorize_only_existing_assessment(case):
    key, _ = stopped(case)
    before = deepcopy(case)
    receipt = build_ambiguity_retirement_receipt(**case)
    ids = validate_ambiguity_retirement_receipt(receipt, **case)
    assert receipt["meaning"] == MEANING
    assert receipt["observations"][key]["hash_domain"] == "unknown"
    assert all(
        o["prior_request_equivalence"] == "unknown" for o in receipt["observations"].values()
    )
    assert ids["stable_cohort_key"] == case["stable_cohort_key"]
    assert ids["allowed_slot_labels"] == case["allowed_slot_labels"]
    assert len(ids["attempt_ids"]) == 2
    for flag in [
        "different_old_request_asserted",
        "no_earlier_payment_asserted",
        "ownership_inferred",
        "credit_released",
        "operation_replay_allowed",
        "old_grades_reused",
        "cohort_identity_changed",
    ]:
        assert receipt[flag] is False
    assert receipt["all_historical_debt_retained"] is True
    assert case == before
    assert build_ambiguity_retirement_receipt(**case) == receipt


@pytest.mark.parametrize("where", ["root", "attempt", "transport"])
def test_any_matching_current_digest_blocks_even_unknown_domain(case, where):
    if where == "root":
        key, _ = stopped(case)
        next(r for r in case["snapshots"]["operations"] if r["key"] == key)["input_hash"] = case[
            "current_effective_input_hash"
        ]
    elif where == "attempt":
        case["snapshots"]["attempts"][0]["input_hash"] = case["current_effective_input_hash"]
    else:
        case["snapshots"]["operations"][1]["input_hash"] = case["current_effective_input_hash"]
    with pytest.raises(ValueError):
        build_ambiguity_retirement_receipt(**case)


@pytest.mark.parametrize(
    "field,value",
    [
        ("credit_released", True),
        ("operation_replay_allowed", True),
        ("remote_outcome", "completed"),
        ("decision", "abandon_charged"),
        ("remaining_unknown", 1),
        ("attempt_id", "unrelated-attempt"),
    ],
)
def test_exact_charged_closure_required_for_unknown_domain(case, field, value):
    _, row = stopped(case)
    row["result_json"]["receipt"][field] = value
    case["snapshots"]["quarantine_receipts"] = [deepcopy(row)]
    with pytest.raises(ValueError):
        build_ambiguity_retirement_receipt(**case)


@pytest.mark.parametrize(
    "collection,status",
    [
        ("operations", "pending"),
        ("operations", "unknown"),
        ("attempts", "reserved"),
        ("attempts", "unknown"),
    ],
)
def test_nonterminal_evidence_never_receives_exclusions(case, collection, status):
    case["snapshots"][collection][0]["status"] = status
    with pytest.raises(ValueError):
        build_ambiguity_retirement_receipt(**case)


@pytest.mark.parametrize(
    "marker",
    [
        "commercial_source_input_receipt",
        "commercial_evaluation_contract",
        "commercial_wire_version",
        "commercial_sample_slot",
        "commercial_component_cohorts",
    ],
)
def test_commercial_source_or_protocol_markers_must_use_normal_path(case, marker):
    case["snapshots"]["operations"][0]["result_json"]["metadata"][marker] = {}
    with pytest.raises(ValueError, match="commercial_source_or_protocol_marker"):
        build_ambiguity_retirement_receipt(**case)


@pytest.mark.parametrize("version", ["v11-structured-review-tool", "v4-source-bound-review"])
def test_known_route_does_not_become_unknown_to_gain_ambiguity_authorization(case, version):
    case["snapshots"]["operations"][0]["result_json"]["metadata"]["paid_call_origin"] = {
        "schema": "paid-call-origin.v1",
        "status": "recorded",
        "project_id": None,
        "prompt_template": "chapter_commercial_quality_judge",
        "prompt_version": version,
    }
    with pytest.raises(ValueError, match="paid_origin"):
        build_ambiguity_retirement_receipt(**case)


def test_structured_tool_marker_is_not_mistaken_for_prose(case):
    case["snapshots"]["operations"][1]["result_json"]["result"]["tool_calls"] = [
        {"type": "function", "function": {"name": "submit_chapter_review", "arguments": "{}"}}
    ]
    with pytest.raises(ValueError, match="tool_protocol_evidence"):
        build_ambiguity_retirement_receipt(**case)


@pytest.mark.parametrize(
    "mutation",
    [
        "no_v1",
        "wrong_model",
        "missing_transport",
        "changed_paid_content",
        "missing_attempt",
        "extra_attempt",
        "control_self_parent",
    ],
)
def test_completed_requires_original_v1_model_all_attempts_and_immutable_transport(case, mutation):
    s = case["snapshots"]
    if mutation == "no_v1":
        del s["operations"][0]["result_json"]["metadata"]["effective_input_receipt"]
    elif mutation == "wrong_model":
        s["attempts"][0]["model_name"] = "unproven actual model"
    elif mutation == "missing_transport":
        s["operations"].pop()
    elif mutation == "changed_paid_content":
        s["operations"][1]["result_json"]["result"]["content"] = "different raw result"
    elif mutation == "missing_attempt":
        s["attempts"] = []
    elif mutation == "extra_attempt":
        other = deepcopy(s["attempts"][0])
        other["id"] = "new unlisted actual attempt"
        s["attempts"].append(other)
    else:
        s["attempts"][0]["operation_key"] = s["operations"][1]["key"]
    with pytest.raises(ValueError):
        build_ambiguity_retirement_receipt(**case)


@pytest.mark.parametrize(
    "change",
    ["row_metadata", "new_root", "removed_transport", "new_current_marker", "tampered_receipt"],
)
def test_fixed_receipt_cannot_grow_or_ignore_new_evidence(case, change):
    receipt = build_ambiguity_retirement_receipt(**case)
    s = case["snapshots"]
    if change == "row_metadata":
        s["operations"][0]["result_json"]["metadata"]["audit"] = "new value"
    elif change == "new_root":
        stopped(case)
    elif change == "removed_transport":
        s["operations"].pop()
    elif change == "new_current_marker":
        s["operations"][0]["result_json"]["metadata"]["commercial_wire_version"] = "current"
    else:
        receipt["credit_released"] = 0
    with pytest.raises(ValueError):
        validate_ambiguity_retirement_receipt(receipt, **case)


def test_actor_reason_and_baseline_do_not_create_new_consumption_identity(case):
    receipt = build_ambiguity_retirement_receipt(**case)
    case["actor"], case["reason"] = "another operator", "same explicit authorization"
    assert build_ambiguity_retirement_receipt(**case)["identity"] == receipt["identity"]
    with pytest.raises(ValueError, match="drift"):
        validate_ambiguity_retirement_receipt(receipt, **case)
    stopped(case)
    assert build_ambiguity_retirement_receipt(**case)["identity"] == receipt["identity"]
    case["current_effective_input_hash"] = sha("changed actual current request")
    changed = build_ambiguity_retirement_receipt(**case)
    assert changed["identity"] != receipt["identity"]
    assert changed["binding"]["stable_cohort_key"] == receipt["binding"]["stable_cohort_key"]
    assert changed["binding"]["allowed_slot_labels"] == receipt["binding"]["allowed_slot_labels"]


@pytest.mark.parametrize("slots", [[], ["a", "b"], ["a", "b", "c", "d"], ["a", "a", "c"]])
def test_no_new_or_extra_slots(case, slots):
    case["allowed_slot_labels"] = slots
    with pytest.raises(ValueError, match="existing_three_slots"):
        build_ambiguity_retirement_receipt(**case)


@pytest.mark.parametrize(
    "surface", ["attempt", "transport_envelope", "quarantine", "nested_content"]
)
def test_protocol_markers_cannot_hide_on_other_structured_surfaces(case, surface):
    s = case["snapshots"]
    if surface == "attempt":
        s["attempts"][0]["metadata"] = {"commercial_source_manifest": {}}
    elif surface == "transport_envelope":
        s["operations"][1]["result_json"]["commercial_wire_version"] = "current"
    elif surface == "quarantine":
        _, row = stopped(case)
        row["result_json"]["metadata"] = {"commercial_source_manifest": {}}
        s["quarantine_receipts"] = [deepcopy(row)]
    else:
        s["operations"][0]["result_json"]["raw_message"] = {
            "content": [{"tool_calls": [{"function": {"name": "submit_chapter_review"}}]}]
        }
    with pytest.raises(ValueError):
        build_ambiguity_retirement_receipt(**case)
