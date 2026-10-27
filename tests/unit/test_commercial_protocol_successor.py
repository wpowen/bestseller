"""Synthetic retained financial rows; no DB, provider, or real-grade claims."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import pytest

from bestseller.services.commercial_protocol_successor import (
    build_commercial_protocol_successor_receipt as build,
)
from bestseller.services.commercial_protocol_successor import (
    validate_commercial_protocol_successor_receipt as validate,
)

pytestmark = pytest.mark.unit


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def inputs(rounds=1):
    project, chapter, draft, owner = (str(uuid4()) for _ in range(4))
    source = {
        k: sha(k)
        for k in (
            "body_sha256",
            "semantic_source_sha256",
            "previous_sha256",
            "source_protocol_sha256",
        )
    }
    contract = {
        "schema": "commercial-evaluation-contract.v1",
        "wire_version": "commercial-review-wire.v2",
        "adapter_version": "adapter.v2",
        "submission_policy": "commercial-report-submission.v1",
        "source_protocol_sha256": source["source_protocol_sha256"],
        "system_prompt_sha256": sha("old system"),
        "instruction_prefix_sha256": sha("user"),
        "tools_sha256": sha("tools"),
        "min_overall": 0.8,
        "min_dimensions": {"continuity": 0.7},
        "output_tokens": 8192,
        "tool_choice": "required",
    }
    binding = {
        "project_id": project,
        "chapter_id": chapter,
        "draft_id": draft,
        "source_binding": source,
        "evaluation_contract": contract,
        "samples": 3,
        "scope_id": "original-book-scope",
        "effective_critic_sha256": sha("critic"),
    }
    slots, inventory = {}, {"runs": [], "operations": [], "attempts": [], "slot_reports": []}
    for slot_no in range(3):
        slot = f"chapter-quality.independent-v2:{slot_no}"
        reports = []
        for number in range(1, rounds + 1):
            uid, aid = str(uuid4()), uuid4().hex
            root_key, input_hash = sha(f"root:{slot}:{number}"), sha(f"request:{slot}:{number}")
            raw = json.dumps(
                {
                    "wire_version": "commercial-review-wire.v2",
                    "pass": False,
                    "overall_score": 0.74,
                    "dimension_scores": {"continuity": 0.74},
                    "issues": [],
                    "rewrite_plan": {},
                },
                ensure_ascii=False,
            )
            calls = [
                {
                    "id": "submit-1",
                    "type": "function",
                    "function": {"name": "submit_chapter_review", "arguments": raw},
                }
            ]
            origin = {
                "project_id": project,
                "workflow_run_id": owner,
                "prompt_template": "chapter_commercial_quality_judge",
                "prompt_version": "v11-structured-review-tool",
                "prompt_hash": input_hash,
            }
            effective = {"schema": "effective-input.v1", "hash": input_hash, "model": "model-one"}
            execution = {
                "scope_id": binding["scope_id"],
                "operation_key": root_key,
                "attempts": [{"attempt_id": aid, "status": "completed"}],
            }
            paid = {
                "content": "auxiliary retained text",
                "tool_calls": calls,
                "raw_message": None,
                "provider": "fixture-provider",
                "model_name": "model-one",
                "finish_reason": "tool_calls",
                "input_tokens": 50,
                "output_tokens": 20,
                "fallback_used": False,
                "metadata": {"paid_call_origin": origin, "effective_input_receipt": effective},
            }
            source_receipt = {
                **source,
                "prompt_version": origin["prompt_version"],
                "source_sha256": sha("source"),
                "assembled_source_sha256": sha("assembled"),
            }
            envelope = {
                "schema_version": "commercial-slot-evidence.v1",
                "sample_slot": slot,
                "attempt": number,
                "source_binding": deepcopy(source),
                "exact_source_audit": {
                    k: source_receipt[k]
                    for k in (
                        "body_sha256",
                        "source_sha256",
                        "previous_sha256",
                        "assembled_source_sha256",
                    )
                },
                "evaluation_contract": deepcopy(contract),
                "validation_errors": [],
                "raw_report": raw,
                "raw_report_sha256": sha(raw),
                "original_overall_score": 0.74,
                "original_dimension_scores": {"continuity": 0.74},
            }
            report = {
                "llm_run_id": uid,
                "overall_score": 0.74,
                "dimension_scores": {"continuity": 0.74},
                "passed": False,
                "blocking_issues": [],
                "audit_issues": [],
                "raw_excerpt": json.dumps(envelope),
            }
            reports.append(report)
            inventory["slot_reports"].append(
                {"sample_slot": slot, "attempt": number, "report": report}
            )
            inventory["runs"].append(
                {
                    "id": uid,
                    **origin,
                    "logical_role": "critic",
                    "provider": paid["provider"],
                    "model_name": paid["model_name"],
                    "prompt_hash": input_hash,
                    "finish_reason": "tool_calls",
                    "metadata_json": {
                        "commercial_evaluation_contract": deepcopy(contract),
                        "commercial_sample_slot": slot,
                        "commercial_source_input_receipt": source_receipt,
                        "execution_receipt": execution,
                        "effective_input_receipt": effective,
                    },
                }
            )
            inventory["attempts"].append(
                {
                    "id": aid,
                    "operation_key": root_key,
                    "scope_id": binding["scope_id"],
                    "model_name": "model-one",
                    "input_hash": input_hash,
                    "status": "completed",
                    "actual_tokens": 70,
                }
            )
            inventory["operations"].extend(
                [
                    {
                        "key": root_key,
                        "scope_id": binding["scope_id"],
                        "input_hash": input_hash,
                        "status": "completed",
                        "result_json": deepcopy(paid),
                    },
                    {
                        "key": sha(f"transport-result.v1\n{binding['scope_id']}\n{aid}"),
                        "scope_id": binding["scope_id"],
                        "input_hash": input_hash,
                        "status": "transport_result",
                        "result_json": {
                            "operation_key": root_key,
                            "attempt_id": aid,
                            "input_hash": input_hash,
                            "result": deepcopy(paid),
                        },
                    },
                ]
            )
        slots[slot] = {
            "state": "completed",
            "used_attempts": rounds,
            "report": reports[-1],
            "request": {"sample_slot": slot, "prompt_version": "v11-structured-review-tool"},
        }
        if rounds == 2:
            slots[slot]["previous_report"] = reports[0]
    new = deepcopy(binding)
    new["evaluation_contract"]["system_prompt_sha256"] = sha(
        "genuinely changed source authority system"
    )
    new["source_binding"]["source_protocol_sha256"] = sha("new source policy")
    new["evaluation_contract"]["source_protocol_sha256"] = sha("new source policy")
    return {
        "old_binding": binding,
        "old_pointer": {"workflow_run_id": owner, "binding_sha256": digest(binding)},
        "old_journal": {
            "schema": "commercial-component-cohort.v1",
            "binding": binding,
            "historical": False,
            "slots": slots,
        },
        "financial_snapshots": inventory,
        "new_binding": new,
        "new_protocol_manifest": {
            "prompt_version": "v12-source-authority",
            "source_manifest": {"judge.py": sha("new code")},
            "effective_input_hash": sha("new request"),
        },
        "actor": "authorized-operator",
        "reason": "Explicit source-authority protocol successor",
    }


@pytest.mark.parametrize("rounds", [1, 2])
def test_complete_closed_proof_preserves_every_row_and_counter(rounds):
    data = inputs(rounds)
    original = deepcopy(data)
    receipt = build(**data)
    assert validate(receipt, **data) == receipt
    assert data == original
    assert len(receipt["row_hashes"]["attempts"]) == 3 * rounds
    assert set(receipt["old_slot_attempts"].values()) == {rounds}
    assert receipt["credit_released"] is False
    assert receipt["quota_reset"] is False
    assert receipt["old_grades_reused"] is False
    assert "workflow_run_id" not in receipt["new_binding"]


def test_identity_has_no_actor_reason_nonce_and_inventory_order_is_irrelevant():
    data = inputs()
    before = build(**data)
    data["actor"], data["reason"] = "another operator", "same authorized protocol"
    for rows in data["financial_snapshots"].values():
        rows.reverse()
    after = build(**data)
    assert before["identity"] == after["identity"]
    assert before["policy_target_key"] == after["policy_target_key"]
    assert before["actor"] != after["actor"]


@pytest.mark.parametrize(
    "field",
    ["project_id", "chapter_id", "draft_id", "scope_id", "samples", "effective_critic_sha256"],
)
def test_target_scope_or_configuration_cannot_change(field):
    data = inputs()
    data["new_binding"][field] = "changed"
    with pytest.raises(ValueError, match="target_or_scope_changed"):
        build(**data)


@pytest.mark.parametrize("field", ["body_sha256", "semantic_source_sha256", "previous_sha256"])
def test_new_protocol_cannot_hide_changed_actual_source(field):
    data = inputs()
    data["new_binding"]["source_binding"][field] = sha("different actual source")
    with pytest.raises(ValueError, match="actual_source_changed"):
        build(**data)


def test_version_or_manifest_only_bump_is_not_a_genuine_evaluation_change():
    data = inputs()
    data["new_binding"]["evaluation_contract"]["system_prompt_sha256"] = data["old_binding"][
        "evaluation_contract"
    ]["system_prompt_sha256"]
    with pytest.raises(ValueError, match="genuine_protocol_change_required"):
        build(**data)


@pytest.mark.parametrize("status", ["unknown", "pending", "rejected", "reconciled_charged"])
def test_no_unsettled_or_unsupported_financial_attempt_waiver(status):
    data = inputs()
    data["financial_snapshots"]["attempts"][0]["status"] = status
    with pytest.raises(ValueError, match="actual_unsettled_or_mismatch"):
        build(**data)


@pytest.mark.parametrize("collection", ["slot_reports", "runs", "operations", "attempts"])
def test_missing_inventory_rows_fail_closed(collection):
    data = inputs(2)
    data["financial_snapshots"][collection].pop(0)
    with pytest.raises(ValueError):
        build(**data)


def test_extra_actual_not_declared_by_run_is_not_silently_omitted():
    data = inputs()
    extra = deepcopy(data["financial_snapshots"]["attempts"][0])
    extra["id"] = uuid4().hex
    data["financial_snapshots"]["attempts"].append(extra)
    with pytest.raises(ValueError, match="all_actual_attempts_required"):
        build(**data)


def test_transport_paid_return_mismatch_and_missing_raw_message_are_distinct():
    data = inputs()
    build(**data)  # raw_message=None is retained, not manufactured.
    data["financial_snapshots"]["operations"][1]["result_json"]["result"]["content"] = (
        "different return"
    )
    with pytest.raises(ValueError, match="physical_return_changed"):
        build(**data)


def test_old_report_unknown_stays_unknown_without_reparse_or_grade_credit():
    data = inputs()
    report = data["financial_snapshots"]["slot_reports"][0]["report"]
    envelope = json.loads(report["raw_excerpt"])
    envelope["validation_errors"] = ["old output was unverified"]
    malformed = '{"old malformed arguments":'
    envelope["raw_report"], envelope["raw_report_sha256"] = malformed, sha(malformed)
    report["raw_excerpt"] = json.dumps(envelope)
    report["blocking_issues"] = [{"code": "CHAPTER_JUDGE_UNAVAILABLE"}]
    for op in data["financial_snapshots"]["operations"][:2]:
        paid = op["result_json"] if op["status"] == "completed" else op["result_json"]["result"]
        paid["tool_calls"][0]["function"]["arguments"] = malformed
    before = deepcopy(data)
    receipt = build(**data)
    assert receipt["old_report_unknowns_preserved"] is True
    assert receipt["unknown_financial_attempts_waived"] is False
    assert data == before


def test_receipt_rejects_old_snapshot_change_or_permission_escalation():
    data = inputs()
    receipt = build(**data)
    receipt["credit_released"] = True
    with pytest.raises(ValueError, match="receipt_or_snapshot_changed"):
        validate(receipt, **data)
    receipt = build(**data)
    data["old_journal"]["slots"]["chapter-quality.independent-v2:0"]["request"]["new"] = "mutation"
    with pytest.raises(ValueError, match="receipt_or_snapshot_changed"):
        validate(receipt, **data)


def test_threshold_lowering_cannot_disguise_itself_as_protocol_repair():
    data = inputs()
    data["new_binding"]["evaluation_contract"]["min_overall"] = 0.1
    with pytest.raises(ValueError, match="nonprotocol_evaluation_changed"):
        build(**data)


def production_descriptors(data):
    inventory = data["financial_snapshots"]
    operations = {row["key"]: row for row in inventory["operations"]}
    for run in inventory["runs"]:
        execution = run["metadata_json"]["execution_receipt"]
        descriptor = execution["attempts"][0]
        actual = next(a for a in inventory["attempts"] if a["id"] == descriptor["attempt_id"])
        actual["reserved_tokens"] = 9000
        descriptor.update(
            model=actual["model_name"],
            effective_input_hash=actual["input_hash"],
            reserved_tokens=actual["reserved_tokens"],
            wire_body_hash=sha("actual wire body"),
        )
        root = operations[execution["operation_key"]]
        transport = operations[sha(f"transport-result.v1\n{actual['scope_id']}\n{actual['id']}")]
        for paid in (root["result_json"], transport["result_json"]["result"]):
            paid["metadata"]["execution_receipt"] = deepcopy(execution)
    return data


def test_full_producer_descriptors_preserved_and_bound_to_both_physical_returns():
    data = production_descriptors(inputs(2))
    before = deepcopy(data)
    receipt = build(**data)
    assert data == before
    assert len(receipt["row_hashes"]["attempts"]) == 6
    for run in data["financial_snapshots"]["runs"]:
        assert receipt["row_hashes"]["runs"][run["id"]] == digest(run)
    assert receipt["credit_released"] is receipt["quota_reset"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "conflicting-model"),
        ("effective_input_hash", sha("wrong input")),
        ("reserved_tokens", 1),
        ("reserved_tokens", True),
        ("reserved_tokens", None),
        ("wire_body_hash", "not-sha256"),
        ("wire_body_hash", None),
    ],
)
def test_conflicting_or_malformed_producer_descriptor_fails(field, value):
    data = production_descriptors(inputs())
    data["financial_snapshots"]["runs"][0]["metadata_json"]["execution_receipt"]["attempts"][0][
        field
    ] = value
    with pytest.raises(ValueError, match="actual_descriptor_mismatch"):
        build(**data)


@pytest.mark.parametrize("witness", ["transport", "completed", "missing_actual_reservation"])
def test_descriptor_needs_original_witnesses_without_inventing_them(witness):
    data = production_descriptors(inputs())
    if witness == "missing_actual_reservation":
        data["financial_snapshots"]["attempts"][0].pop("reserved_tokens")
    else:
        operation = data["financial_snapshots"]["operations"][1 if witness == "transport" else 0]
        result = (
            operation["result_json"]["result"]
            if witness == "transport"
            else operation["result_json"]
        )
        result["metadata"]["execution_receipt"]["attempts"][0]["wire_body_hash"] = sha(
            "different wire body"
        )
    with pytest.raises(ValueError, match="actual_descriptor"):
        build(**data)


@pytest.mark.parametrize("actual_tokens", [None, True, -1])
def test_full_descriptor_never_waives_missing_or_invalid_actual_tokens(actual_tokens):
    data = production_descriptors(inputs())
    data["financial_snapshots"]["attempts"][0]["actual_tokens"] = actual_tokens
    with pytest.raises(ValueError, match="actual_unsettled_or_mismatch"):
        build(**data)


def test_additional_retained_descriptor_fields_are_not_dropped_or_authority():
    data = production_descriptors(inputs())
    for run in data["financial_snapshots"]["runs"]:
        execution = run["metadata_json"]["execution_receipt"]
        execution["attempts"][0]["opaque_audit"] = {"unrelated": "retained only"}
        root_key = execution["operation_key"]
        for operation in data["financial_snapshots"]["operations"]:
            if operation["key"] == root_key:
                result = operation["result_json"]
            elif operation["result_json"].get("operation_key") == root_key:
                result = operation["result_json"]["result"]
            else:
                continue
            result["metadata"]["execution_receipt"] = deepcopy(execution)
    before = deepcopy(data)
    receipt = build(**data)
    assert data == before
    data["financial_snapshots"]["runs"][0]["metadata_json"]["execution_receipt"]["attempts"][0][
        "opaque_audit"
    ]["unrelated"] = "changed"
    with pytest.raises(ValueError, match="actual_descriptor_physical_mismatch"):
        build(**data)
    assert receipt["unknown_financial_attempts_waived"] is False
