"""Pure serialized evidence: no database, ORM, budget mutation or provider."""

from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import pytest

from bestseller.services.commercial_protocol_transition import (
    build_transition_receipt,
    validate_transition_receipt,
)

pytestmark = pytest.mark.unit


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def case():
    project, chapter = str(uuid4()), str(uuid4())
    scope, old_hash, current = (
        "conception:original-scope",
        sha("old actual input"),
        sha("current input"),
    )
    model, op_key, attempt_id = (
        "openai/fixture-model",
        sha("original-operation"),
        "original-attempt",
    )
    run = {
        "id": str(uuid4()),
        "project_id": project,
        "workflow_run_id": None,
        "prompt_template": "chapter_commercial_quality_judge",
        "prompt_version": "v4-source-bound-review",
        "prompt_hash": old_hash,
        "model_name": model,
        "request_payload_ref": None,
        "response_payload_ref": None,
        "metadata_json": {
            "effective_input_receipt": {
                "schema": "effective-input.v1",
                "hash": old_hash,
                "model": model,
            },
            "execution_receipt": {
                "scope_id": scope,
                "operation_key": op_key,
                "attempts": [{"attempt_id": attempt_id}],
            },
        },
    }
    operation = {
        "key": op_key,
        "scope_id": scope,
        "input_hash": old_hash,
        "status": "completed",
        "result_json": {"content": "original paid raw", "model_name": model},
    }
    attempt = {
        "id": attempt_id,
        "operation_key": op_key,
        "scope_id": scope,
        "input_hash": old_hash,
        "model_name": model,
        "status": "completed",
        "reserved_tokens": 300,
        "actual_tokens": 225,
    }
    return {
        "project_id": project,
        "chapter_id": chapter,
        "execution_scope_id": scope,
        "target_protocol": {
            "prompt_version": "v11-structured-review-tool",
            "source_manifest": {"commercial_source_evidence.py": sha("actual current code")},
            "evaluation_contract": {"schema": "fixture-typed-contract.v1", "samples": 3},
        },
        "current_effective_input_hash": current,
        "snapshots": {
            "runs": [run],
            "operations": [operation],
            "attempts": [attempt],
            "quarantine_receipts": [],
        },
    }


def add_quarantined_orphan(case):
    snapshots, scope = case["snapshots"], case["execution_scope_id"]
    h, model = snapshots["runs"][0]["prompt_hash"], snapshots["runs"][0]["model_name"]
    aid, key, rid = (
        "unknown-dispatch-attempt",
        sha("opaque original operation"),
        "original-quarantine",
    )
    snapshots["operations"].append(
        {"key": key, "scope_id": scope, "input_hash": h, "status": "stopped", "result_json": None}
    )
    snapshots["attempts"].append(
        {
            "id": aid,
            "operation_key": key,
            "scope_id": scope,
            "input_hash": h,
            "model_name": model,
            "status": "reconciled_charged",
            "reserved_tokens": 400,
            "actual_tokens": None,
        }
    )
    receipt = {
        "key": sha(f"reconciliation.v1\n{scope}\n{rid}"),
        "scope_id": scope,
        "status": "reconciliation_receipt",
        "input_hash": sha("original reconciliation arguments"),
        "result_json": {
            "reconciliation_id": rid,
            "request_hash": sha("original reconciliation arguments"),
            "actor": "original operator",
            "local_dispatcher_stopped": True,
            "remote_operation_closed": False,
            "evidence_kind": "dispatcher_exit",
            "evidence_sha256": sha("original dispatcher proof"),
            "receipt": {
                "reconciliation_id": rid,
                "scope_id": scope,
                "attempt_id": aid,
                "decision": "quarantine_charged",
                "credit_released": False,
                "operation_replay_allowed": False,
                "remote_outcome": "unknown_quarantined",
                "automatic_resume": False,
                "remaining_unknown": 0,
            },
        },
    }
    snapshots["operations"].append(receipt)
    snapshots["quarantine_receipts"].append(deepcopy(receipt))
    return receipt


def test_closed_quarantine_can_use_equal_v1_witness_without_assigning_wf_or_body(case):
    add_quarantined_orphan(case)
    before = deepcopy(case)
    receipt = build_transition_receipt(**case)
    ids = validate_transition_receipt(receipt, **case)
    assert ids["run_ids"] == [case["snapshots"]["runs"][0]["id"]]
    assert len(ids["attempt_ids"]) == 2
    assert len(ids["quarantine_receipt_keys"]) == 1
    assert case == before
    assert receipt["ownership_inferred"] is receipt["body_exclusion_asserted"] is False
    assert receipt["old_grades_reused"] is receipt["credit_released"] is False
    assert receipt["operation_replay_allowed"] is receipt["cohort_identity_changed"] is False
    assert "first_same_body_or_no_earlier_payment" in receipt["meaning"]
    assert build_transition_receipt(**case) == receipt


@pytest.mark.parametrize("version", ["v4-source-bound-review", "v7", "v8-source", "v9-wire", "v10"])
def test_only_explicit_legacy_versions_are_retired(case, version):
    case["snapshots"]["runs"][0]["prompt_version"] = version
    assert validate_transition_receipt(build_transition_receipt(**case), **case)["run_ids"]


@pytest.mark.parametrize(
    "version", ["v5", "v6", "v11", "v11-structured-review-tool", "v12", None, "unknown"]
)
def test_current_and_unclassified_protocols_never_gain_exclusions(case, version):
    case["snapshots"]["runs"][0]["prompt_version"] = version
    with pytest.raises(ValueError, match="current_or_unknown_protocol"):
        build_transition_receipt(**case)


@pytest.mark.parametrize("location", ["run", "attempt", "operation", "transport"])
def test_any_matching_actual_hash_blocks(case, location):
    s, current = case["snapshots"], case["current_effective_input_hash"]
    if location == "run":
        s["runs"][0]["prompt_hash"] = current
    elif location == "attempt":
        s["attempts"][0]["input_hash"] = current
    elif location == "operation":
        s["operations"][0]["input_hash"] = current
    else:
        aid = s["attempts"][0]["id"]
        s["operations"].append(
            {
                "key": sha(f"transport-result.v1\n{case['execution_scope_id']}\n{aid}"),
                "scope_id": case["execution_scope_id"],
                "status": "transport_result",
                "input_hash": current,
                "result_json": {
                    "schema": "transport-result.v1",
                    "attempt_id": aid,
                    "operation_key": s["operations"][0]["key"],
                    "input_hash": current,
                    "result": {"model_name": s["attempts"][0]["model_name"]},
                },
            }
        )
    with pytest.raises(ValueError):
        build_transition_receipt(**case)


@pytest.mark.parametrize(
    "kind,status",
    [
        ("attempts", "reserved"),
        ("attempts", "unknown"),
        ("operations", "pending"),
        ("operations", "unknown"),
    ],
)
def test_active_or_unknown_work_is_not_retired(case, kind, status):
    case["snapshots"][kind][0]["status"] = status
    with pytest.raises(ValueError):
        build_transition_receipt(**case)


@pytest.mark.parametrize("mutation", ["schema", "hash", "scope", "model", "missing"])
def test_incomparable_or_wrong_scope_witness_is_rejected(case, mutation):
    run = case["snapshots"]["runs"][0]
    metadata = run["metadata_json"]
    if mutation == "schema":
        metadata["effective_input_receipt"]["schema"] = "prompt-only.v0"
    elif mutation == "hash":
        metadata["effective_input_receipt"]["hash"] = sha("different")
    elif mutation == "scope":
        metadata["execution_receipt"]["scope_id"] = "project:foreign"
    elif mutation == "model":
        metadata["effective_input_receipt"]["model"] = "different model"
    else:
        del metadata["effective_input_receipt"]
    with pytest.raises(ValueError):
        build_transition_receipt(**case)


@pytest.mark.parametrize(
    "field,value",
    [
        ("credit_released", True),
        ("operation_replay_allowed", True),
        ("decision", "abandon_charged"),
        ("remote_outcome", "completed"),
        ("remaining_unknown", 1),
        ("attempt_id", "foreign-attempt"),
    ],
)
def test_stopped_requires_exact_charged_nonreplayable_quarantine(case, field, value):
    row = add_quarantined_orphan(case)
    row["result_json"]["receipt"][field] = value
    case["snapshots"]["quarantine_receipts"] = [deepcopy(row)]
    with pytest.raises(ValueError):
        build_transition_receipt(**case)


def test_stopped_without_quarantine_proof_is_not_free_credit(case):
    case["snapshots"]["operations"][0].update(status="stopped", result_json=None)
    case["snapshots"]["attempts"][0]["status"] = "reconciled_charged"
    with pytest.raises(ValueError):
        build_transition_receipt(**case)


@pytest.mark.parametrize(
    "change", ["metadata", "new_current_run", "new_attempt", "delete_attempt", "receipt_tamper"]
)
def test_validate_rereads_closed_set_and_rejects_drift_or_new_unlisted_work(case, change):
    receipt = build_transition_receipt(**case)
    if change == "metadata":
        case["snapshots"]["runs"][0]["metadata_json"]["audit"] = "changed"
    elif change == "new_current_run":
        row = deepcopy(case["snapshots"]["runs"][0])
        row.update(id=str(uuid4()), prompt_version="v11-structured-review-tool")
        case["snapshots"]["runs"].append(row)
    elif change == "new_attempt":
        row = deepcopy(case["snapshots"]["attempts"][0])
        row.update(id="another actual send", input_hash=case["current_effective_input_hash"])
        case["snapshots"]["attempts"].append(row)
    elif change == "delete_attempt":
        case["snapshots"]["attempts"] = []
    else:
        receipt["credit_released"] = True
    with pytest.raises(ValueError):
        validate_transition_receipt(receipt, **case)


def test_identity_has_no_actor_nonce_or_row_order_and_pins_target_contract(case):
    row = add_quarantined_orphan(case)
    first = build_transition_receipt(**case)
    case["snapshots"]["operations"].reverse()
    case["snapshots"]["attempts"].reverse()
    assert build_transition_receipt(**case) == first
    row["result_json"]["actor"] = "another operator"
    case["snapshots"]["quarantine_receipts"] = [deepcopy(row)]
    assert build_transition_receipt(**case)["identity"] == first["identity"]
    with pytest.raises(ValueError, match="drift"):
        validate_transition_receipt(first, **case)
    case["target_protocol"]["evaluation_contract"]["samples"] = 4
    assert build_transition_receipt(**case)["identity"] != first["identity"]


def test_json_roundtrip_and_returned_ids_do_not_mutate_receipt(case):
    receipt = json.loads(json.dumps(build_transition_receipt(**case)))
    ids = validate_transition_receipt(receipt, **case)
    ids["run_ids"].clear()
    assert receipt["exclusions"]["run_ids"]


def test_attempt_cannot_use_its_transport_receipt_as_parent(case):
    s, scope = case["snapshots"], case["execution_scope_id"]
    aid = "self-parenting-transport-attempt"
    key = sha(f"transport-result.v1\n{scope}\n{aid}")
    attempt = deepcopy(s["attempts"][0])
    attempt.update(id=aid, operation_key=key)
    s["attempts"].append(attempt)
    s["operations"].append(
        {
            "key": key,
            "scope_id": scope,
            "status": "transport_result",
            "input_hash": attempt["input_hash"],
            "result_json": {
                "schema": "transport-result.v1",
                "attempt_id": aid,
                "operation_key": key,
                "input_hash": attempt["input_hash"],
                "result": {"content": "paid", "model_name": attempt["model_name"]},
            },
        }
    )
    with pytest.raises(ValueError, match="attempt_parent_not_request"):
        build_transition_receipt(**case)


def test_matching_witness_elsewhere_cannot_hide_parent_attempt_hash_disagreement(case):
    s = case["snapshots"]
    other = deepcopy(s["runs"][0])
    other.update(id=str(uuid4()), prompt_hash=sha("other historical request"))
    other["metadata_json"]["effective_input_receipt"]["hash"] = other["prompt_hash"]
    s["runs"].append(other)
    s["attempts"][0]["input_hash"] = other["prompt_hash"]
    with pytest.raises(ValueError, match="attempt_parent_input_mismatch"):
        build_transition_receipt(**case)


def test_full_receipt_types_are_pinned_not_python_boolean_integer_equality(case):
    receipt = build_transition_receipt(**case)
    receipt["credit_released"] = 0
    with pytest.raises(ValueError, match="drift"):
        validate_transition_receipt(receipt, **case)


def test_result_model_cannot_disagree_with_original_witness(case):
    case["snapshots"]["operations"][0]["result_json"]["model_name"] = "unproven actual route"
    with pytest.raises(ValueError, match="paid_result_model"):
        build_transition_receipt(**case)


def test_original_snapshot_metadata_alias_does_not_change_ownership(case):
    row = case["snapshots"]["runs"][0]
    row["metadata"] = row.pop("metadata_json")
    receipt = build_transition_receipt(**case)
    assert validate_transition_receipt(receipt, **case)["run_ids"] == [row["id"]]
    assert row["workflow_run_id"] is None


def test_immutable_transport_is_pinned_and_its_actual_hash_must_match(case):
    s, scope = case["snapshots"], case["execution_scope_id"]
    attempt = s["attempts"][0]
    result = deepcopy(s["operations"][0]["result_json"])
    row = {
        "key": sha(f"transport-result.v1\n{scope}\n{attempt['id']}"),
        "scope_id": scope,
        "status": "transport_result",
        "input_hash": attempt["input_hash"],
        "result_json": {
            "schema": "transport-result.v1",
            "attempt_id": attempt["id"],
            "operation_key": attempt["operation_key"],
            "input_hash": attempt["input_hash"],
            "result": result,
        },
    }
    s["operations"].append(row)
    receipt = build_transition_receipt(**case)
    assert row["key"] in validate_transition_receipt(receipt, **case)["operation_keys"]
    result["metadata"] = {
        "effective_input_receipt": {
            "schema": "effective-input.v1",
            "hash": case["current_effective_input_hash"],
        }
    }
    with pytest.raises(ValueError, match="paid_effective_hash"):
        build_transition_receipt(**case)

@pytest.mark.parametrize('version', ['v2', 'v3-source-bound-opening'])
def test_original_null_project_legacy_protocol_retains_unknown_ownership(case, version):
    before = deepcopy(case)
    run = case['snapshots']['runs'][0]
    run['project_id'] = None
    run['prompt_version'] = version
    receipt = build_transition_receipt(**case)
    assert receipt['ownership_inferred'] is False
    assert receipt['body_exclusion_asserted'] is False
    assert receipt['exclusions']['run_ids'] == [run['id']]
    assert run['project_id'] is None and run['workflow_run_id'] is None
    assert case['current_effective_input_hash'] == before['current_effective_input_hash']
    assert validate_transition_receipt(receipt, **case) == receipt['exclusions']


def test_null_project_does_not_waive_foreign_execution_scope(case):
    run = case['snapshots']['runs'][0]
    run['project_id'] = None
    run['metadata_json']['execution_receipt']['scope_id'] = 'another-original-scope'
    with pytest.raises(ValueError, match='comparable_v1_witness_missing'):
        build_transition_receipt(**case)
