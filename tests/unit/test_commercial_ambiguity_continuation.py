"""Fixed legacy inventory continuation, no DB, provider, or invented ownership."""

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from test_commercial_ambiguity_retirement import case as case
from test_commercial_ambiguity_retirement import sha, stopped

from bestseller.services.commercial_ambiguity_continuation import (
    build_continuation_grant,
    validate_continuation_grant,
)
from bestseller.services.commercial_ambiguity_retirement import (
    SCHEMA as BASELINE_SCHEMA,
)
from bestseller.services.commercial_ambiguity_retirement import (
    build_ambiguity_retirement_receipt,
)
from bestseller.services.commercial_protocol_transition import _digest

pytestmark = pytest.mark.unit


@pytest.fixture
def continuation(case):
    stopped(case)
    baseline = build_ambiguity_retirement_receipt(**case)
    arguments = {
        "project_id": case["project_id"],
        "execution_scope_id": case["execution_scope_id"],
        "baseline_workflow_id": str(
            uuid5(
                NAMESPACE_URL,
                "bestseller:" + BASELINE_SCHEMA + ":" + baseline["identity"],
            )
        ),
        "baseline_receipt": baseline,
        "target_protocol_policy": {
            "prompt_version": case["target_protocol"]["prompt_version"],
            "source_manifest": deepcopy(case["target_protocol"]["source_manifest"]),
        },
        "original_authorization_ref": case["original_authorization_ref"],
        "actor": "explicit goal-authorized operator",
        "reason": "Continue normal assessments while preserving this exact unknown debt",
    }
    return arguments, case


def test_exact_fixed_baseline_replay_preserves_all_rows_and_permissions(continuation):
    arguments, case = continuation
    before = deepcopy(continuation)
    grant = build_continuation_grant(**arguments)
    result = validate_continuation_grant(grant, **arguments, snapshots=case["snapshots"])
    assert result["row_hashes"] == arguments["baseline_receipt"]["row_hashes"]
    assert result["operation_keys"] == arguments["baseline_receipt"]["exclusions"]["operation_keys"]
    assert result["attempt_ids"] == arguments["baseline_receipt"]["exclusions"]["attempt_ids"]
    assert result["baseline_receipt_sha256"] == _digest(arguments["baseline_receipt"])
    assert "stable_cohort_key" not in result and "allowed_slot_labels" not in result
    for flag in (
        "credit_released",
        "operation_replay_allowed",
        "ownership_inferred",
        "different_old_request_asserted",
        "no_earlier_payment_asserted",
        "old_grades_reused",
        "cohort_identity_changed",
    ):
        assert grant[flag] is False
    assert grant["all_historical_debt_retained"] is True
    assert continuation == before
    result["row_hashes"]["operations"].clear()
    assert arguments["baseline_receipt"]["row_hashes"]["operations"]


def test_new_body_chapter_contract_still_requires_own_exact_child(continuation):
    arguments, case = continuation
    grant = build_continuation_grant(**arguments)
    for chapter, body in ((case["chapter_id"], "next body"), (str(uuid4()), "other chapter")):
        validate_continuation_grant(grant, **arguments, snapshots=case["snapshots"])
        child_input = deepcopy(case)
        child_input.update(
            chapter_id=chapter,
            body_sha256=sha(body),
            current_effective_input_hash=sha("actual request " + body),
            stable_cohort_key=sha("existing cohort " + body),
        )
        child_input["target_protocol"]["evaluation_contract"] = {"source_ids": [body], "samples": 3}
        child = build_ambiguity_retirement_receipt(**child_input)
        assert child["identity"] != arguments["baseline_receipt"]["identity"]
        assert child["binding"]["stable_cohort_key"] == child_input["stable_cohort_key"]
        assert child["row_hashes"] == grant["row_hashes"]
        # A grant never excuses collision with the actual new request.
        child_input["current_effective_input_hash"] = case["snapshots"]["attempts"][0]["input_hash"]
        with pytest.raises(ValueError, match="matching_current"):
            build_ambiguity_retirement_receipt(**child_input)


def test_row_order_and_json_key_order_do_not_change_fixed_inventory(continuation):
    arguments, case = continuation
    grant = build_continuation_grant(**arguments)
    snapshots = {
        k: [dict(reversed(list(row.items()))) for row in reversed(rows)]
        for k, rows in case["snapshots"].items()
    }
    assert (
        validate_continuation_grant(grant, **arguments, snapshots=snapshots)["row_hashes"]
        == grant["row_hashes"]
    )


@pytest.mark.parametrize("table", ["operations", "attempts", "quarantine_receipts"])
@pytest.mark.parametrize("mutation", ["add", "remove", "change", "duplicate"])
def test_any_current_inventory_drift_rejects_without_autogrow(continuation, table, mutation):
    arguments, case = continuation
    grant = build_continuation_grant(**arguments)
    before = deepcopy(grant)
    snapshots = deepcopy(case["snapshots"])
    rows = snapshots[table]
    if mutation == "add":
        row = deepcopy(rows[0])
        row["id" if table == "attempts" else "key"] = "new-unlisted-identity"
        rows.append(row)
    elif mutation == "remove":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(deepcopy(rows[0]))
    else:
        rows[0]["new_runtime_field"] = "changed original evidence"
    with pytest.raises(ValueError):
        validate_continuation_grant(grant, **arguments, snapshots=snapshots)
    assert grant == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", str(uuid4())),
        ("execution_scope_id", "different-scope"),
        ("baseline_workflow_id", str(uuid4())),
        ("original_authorization_ref", "new-auth"),
        ("actor", "different actor"),
        ("reason", "different explicit reason"),
        ("actor", ""),
        ("reason", ""),
    ],
)
def test_exact_authorization_and_baseline_binding_required(continuation, field, value):
    arguments, case = continuation
    grant = build_continuation_grant(**arguments)
    changed = {**arguments, field: value}
    with pytest.raises(ValueError):
        validate_continuation_grant(grant, **changed, snapshots=case["snapshots"])


@pytest.mark.parametrize("field", ["prompt_version", "source_manifest", "evaluation_contract"])
def test_policy_drift_or_contract_in_grant_rejected(continuation, field):
    arguments, case = continuation
    grant = build_continuation_grant(**arguments)
    changed = deepcopy(arguments)
    policy = changed["target_protocol_policy"]
    if field == "prompt_version":
        policy[field] = "v12-another-policy"
    elif field == "source_manifest":
        policy[field]["typed_policy.py"] = sha("changed policy")
    else:
        policy[field] = {"new body tool catalog": True}
    with pytest.raises(ValueError):
        validate_continuation_grant(grant, **changed, snapshots=case["snapshots"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "unknown"),
        ("identity", "0" * 64),
        ("baseline_sha256", "0" * 64),
        ("credit_released", True),
        ("operation_replay_allowed", True),
        ("cohort_identity_changed", True),
    ],
)
def test_tampered_grant_fails_closed(continuation, field, value):
    arguments, case = continuation
    grant = build_continuation_grant(**arguments)
    grant[field] = value
    with pytest.raises(ValueError):
        validate_continuation_grant(grant, **arguments, snapshots=case["snapshots"])


@pytest.mark.parametrize(
    "mutation", ["identity", "row_hash", "permission", "original_audit", "observations"]
)
def test_original_receipt_integrity_not_just_baseline_ids(continuation, mutation):
    arguments, case = continuation
    changed = deepcopy(arguments)
    receipt = changed["baseline_receipt"]
    if mutation == "identity":
        receipt["identity"] = sha("forged identity")
    elif mutation == "row_hash":
        first = next(iter(receipt["row_hashes"]["operations"]))
        receipt["row_hashes"]["operations"][first] = sha("forged row")
        receipt["baseline_sha256"] = _digest(receipt["row_hashes"])
    elif mutation == "permission":
        receipt["operation_replay_allowed"] = True
    elif mutation == "original_audit":
        receipt["binding"]["reason"] += " changed"
    else:
        next(iter(receipt["observations"].values()))["hash_domain"] = "invented"
    grant = build_continuation_grant(**arguments)
    with pytest.raises(ValueError):
        validate_continuation_grant(grant, **changed, snapshots=case["snapshots"])
    if mutation in {"row_hash", "observations"}:
        # Rehashing a forged grant is insufficient: reconstruct the original
        # physical baseline from CURRENT original rows, including its flags.
        forged = build_continuation_grant(**changed)
        with pytest.raises(ValueError, match="receipt_or_snapshot_drift"):
            validate_continuation_grant(forged, **changed, snapshots=case["snapshots"])


def test_explicit_target_policy_change_requires_new_explicit_grant(continuation):
    arguments, case = continuation
    original = deepcopy(arguments["baseline_receipt"])
    changed = deepcopy(arguments)
    changed["target_protocol_policy"]["source_manifest"]["new_frozen_policy.py"] = sha("new policy")
    grant = build_continuation_grant(**changed)
    validate_continuation_grant(grant, **changed, snapshots=case["snapshots"])
    assert arguments["baseline_receipt"] == original
    assert grant["identity"] != build_continuation_grant(**arguments)["identity"]
    assert grant["cohort_identity_changed"] is False


def test_valid_new_unlisted_root_cannot_extend_fixed_baseline(continuation):
    arguments, case = continuation
    grant = build_continuation_grant(**arguments)
    extended = deepcopy(case)
    snapshots = extended["snapshots"]
    root, transport = deepcopy(snapshots["operations"][:2])
    attempt = deepcopy(snapshots["attempts"][0])
    key, aid = sha("new otherwise valid root"), "new-otherwise-valid-attempt"
    root["key"] = key
    execution = root["result_json"]["metadata"]["execution_receipt"]
    execution.update(operation_key=key, attempts=[{"attempt_id": aid}])
    attempt.update(id=aid, operation_key=key)
    transport["key"] = sha(f"transport-result.v1\n{case['execution_scope_id']}\n{aid}")
    transport["result_json"].update(
        attempt_id=aid, operation_key=key, result=deepcopy(root["result_json"]),
    )
    snapshots["operations"].extend([root, transport])
    snapshots["attempts"].append(attempt)
    # The enlarged inventory is valid on its own. It still is not the original
    # operator baseline and cannot acquire continuation permission implicitly.
    larger = build_ambiguity_retirement_receipt(**extended)
    assert larger["baseline_sha256"] != grant["baseline_sha256"]
    with pytest.raises(ValueError, match="receipt_or_snapshot_drift"):
        validate_continuation_grant(grant, **arguments, snapshots=snapshots)
