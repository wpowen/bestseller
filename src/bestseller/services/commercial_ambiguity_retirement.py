"""Pure authorization for ONE new assessment despite unresolved historical spend.

This is NOT proof of different old requests, no earlier payment, or ownership.
The caller persists the fixed baseline BEFORE dispatch, revalidates every read,
and continues the EXISTING stable cohort/three slots. Never auto-grow a baseline
or use this receipt identity as a new slot/operation namespace.

snapshots: operations (roots + transport/control rows), attempts (ALL originals),
quarantine_receipts (full reconciliation rows also present in operations).
Only proposed ambiguous evidence belongs here; known commercial/noncommercial
routes continue through their separate existing validators. All rows must be
serialized original JSON snapshots, including their complete metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
from typing import Any
from uuid import UUID

from bestseller.services.commercial_protocol_transition import _digest, _index, _sha, _version

# ruff: noqa: ANN401
SCHEMA = "commercial-ambiguity-retirement.v1"
MEANING = "authorization_one_new_protocol_assessment_despite_unknown_prior_spend"
_CONTROL = {"transport_result", "transport_failure_receipt", "reconciliation_receipt"}


def _require(value: Any, code: str) -> None:
    if not value:
        raise ValueError(f"commercial_ambiguity_{code}")


def _no_protocol_evidence(value: Any) -> None:
    """Inspect structured evidence, never classify story prose by keywords."""
    if isinstance(value, Mapping):
        _require(
            not any(isinstance(k, str) and k.startswith("commercial_") for k in value),
            "commercial_source_or_protocol_marker",
        )
        if "paid_call_origin" in value:
            origin = value["paid_call_origin"]
            _require(
                isinstance(origin, Mapping)
                and origin.get("schema") == "paid-call-origin.v1"
                and origin.get("status") == "unknown"
                and all(
                    origin.get(k) is None
                    for k in (
                        "project_id",
                        "workflow_run_id",
                        "step_run_id",
                        "prompt_template",
                        "prompt_version",
                    )
                ),
                "known_or_malformed_paid_origin",
            )
        if value.get("prompt_template") or value.get("prompt_version"):
            raise ValueError("commercial_ambiguity_known_route_or_protocol")
        _require(not value.get("tool_calls"), "tool_protocol_evidence")
        _require(value.get("name") != "submit_chapter_review", "tool_protocol_evidence")
        for key, item in value.items():
            if key not in {"content", "body", "text"} or isinstance(item, (Mapping, list)):
                _no_protocol_evidence(item)
    elif isinstance(value, list):
        for item in value:
            _no_protocol_evidence(item)


def _quarantines(
    rows: dict[str, dict[str, Any]],
    operations: dict[str, dict[str, Any]],
    attempts: dict[str, dict[str, Any]],
    scope: str,
) -> dict[str, str]:
    by_attempt = {}
    for key, row in rows.items():
        _require(
            key in operations and _digest(row) == _digest(operations[key]), "quarantine_snapshot"
        )
        _require(
            row.get("scope_id") == scope and row.get("status") == "reconciliation_receipt",
            "quarantine_row",
        )
        record = row.get("result_json")
        _require(isinstance(record, Mapping), "quarantine_record")
        receipt = record.get("receipt")
        _require(isinstance(receipt, Mapping), "quarantine_receipt")
        aid, rid = receipt.get("attempt_id"), record.get("reconciliation_id")
        _require(
            isinstance(rid, str) and rid and receipt.get("reconciliation_id") == rid,
            "quarantine_identity",
        )
        _require(
            key == hashlib.sha256(f"reconciliation.v1\n{scope}\n{rid}".encode()).hexdigest()
            and _sha(row.get("input_hash"))
            and row["input_hash"] == record.get("request_hash"),
            "quarantine_record_hash",
        )
        _require(
            receipt.get("scope_id") == scope
            and receipt.get("decision") == "quarantine_charged"
            and receipt.get("credit_released") is False
            and receipt.get("operation_replay_allowed") is False
            and receipt.get("automatic_resume") is False
            and receipt.get("remote_outcome") == "unknown_quarantined"
            and type(receipt.get("remaining_unknown")) is int
            and receipt["remaining_unknown"] == 0
            and record.get("local_dispatcher_stopped") is True
            and record.get("remote_operation_closed") is False
            and record.get("evidence_kind") == "dispatcher_exit"
            and _sha(record.get("evidence_sha256")),
            "quarantine_not_charged_closed",
        )
        _require(
            aid in attempts
            and attempts[aid].get("status") == "reconciled_charged"
            and aid not in by_attempt,
            "quarantine_attempt",
        )
        by_attempt[aid] = key
    return by_attempt


def _baseline(snapshots: dict[str, Any], scope: str, current: str) -> dict[str, Any]:
    _require(
        isinstance(snapshots, dict)
        and set(snapshots) == {"operations", "attempts", "quarantine_receipts"},
        "snapshot_shape",
    )
    operations = _index(snapshots["operations"], "key")
    attempts = _index(snapshots["attempts"], "id")
    quarantines = _index(snapshots["quarantine_receipts"], "key")
    roots = {k: row for k, row in operations.items() if row.get("status") not in _CONTROL}
    _require(roots, "empty_baseline")
    for row in [*operations.values(), *attempts.values()]:
        _no_protocol_evidence(row)
    by_parent = {}
    for aid, attempt in attempts.items():
        key = attempt.get("operation_key")
        _require(key in roots and attempt.get("scope_id") == scope, "attempt_parent_or_scope")
        _require(
            _sha(attempt.get("input_hash")) and attempt["input_hash"] != current,
            "matching_current_attempt_or_bad_hash",
        )
        _require(attempt.get("input_hash") == roots[key].get("input_hash"), "attempt_input_link")
        _require(
            attempt.get("status") in {"completed", "reconciled_charged"}, "nonterminal_attempt"
        )
        _require(
            isinstance(attempt.get("model_name"), str) and attempt["model_name"], "attempt_model"
        )
        _require(
            type(attempt.get("reserved_tokens")) is int
            and attempt["reserved_tokens"] > 0
            and (
                attempt.get("actual_tokens") is None
                or (type(attempt["actual_tokens"]) is int and attempt["actual_tokens"] >= 0)
            ),
            "attempt_finance_shape",
        )
        by_parent.setdefault(key, []).append(aid)
    charged = _quarantines(quarantines, operations, attempts, scope)
    transports, observations = {}, {}
    for key, row in operations.items():
        _require(_sha(key) and row.get("scope_id") == scope, "operation_identity_or_scope")
        status = row.get("status")
        if status == "reconciliation_receipt":
            _require(key in quarantines, "unlisted_control")
            continue
        _require(
            _sha(row.get("input_hash")) and row["input_hash"] != current,
            "matching_current_operation_or_bad_hash",
        )
        if status in {"transport_result", "transport_failure_receipt"}:
            envelope = row.get("result_json")
            _require(isinstance(envelope, Mapping), "transport_envelope")
            aid = envelope.get("attempt_id")
            _require(
                aid in attempts
                and envelope.get("operation_key") == attempts[aid]["operation_key"]
                and row["input_hash"] == attempts[aid]["input_hash"],
                "transport_attempt_link",
            )
            prefix = (
                "transport-result.v1" if status == "transport_result" else "transport-failure.v1"
            )
            _require(
                key == hashlib.sha256(f"{prefix}\n{scope}\n{aid}".encode()).hexdigest()
                and envelope.get("schema") == prefix,
                "transport_identity",
            )
            if status == "transport_result":
                _require(
                    envelope.get("input_hash") == attempts[aid]["input_hash"]
                    and aid not in transports,
                    "transport_actual_hash",
                )
                _require(isinstance(envelope.get("result"), Mapping), "transport_result")
                _no_protocol_evidence(envelope["result"])
                transports[aid] = envelope["result"]
            else:
                _require(aid in charged, "failure_without_charged_quarantine")
                _no_protocol_evidence(envelope)
        else:
            _require(status in {"completed", "stopped"}, "nonterminal_operation")
            _no_protocol_evidence(row)
    for key, root in roots.items():
        ids = by_parent.get(key, [])
        _require(ids, "all_original_attempts_required")
        if root["status"] == "stopped":
            _require(
                root.get("result_json") is None and all(a in charged for a in ids),
                "stopped_without_exact_quarantine",
            )
            observations[key] = {
                "hash_domain": "unknown",
                "prior_request_equivalence": "unknown",
                "route": "unknown",
                "body": "unknown",
                "source": "unknown",
                "workflow": "unknown",
                "remote_result": "unknown_quarantined",
            }
            continue
        result = root.get("result_json")
        _require(isinstance(result, Mapping), "completed_result")
        meta = result.get("metadata")
        _require(isinstance(meta, Mapping), "original_metadata_required")
        effective, execution = meta.get("effective_input_receipt"), meta.get("execution_receipt")
        _require(
            isinstance(effective, Mapping)
            and effective.get("schema") == "effective-input.v1"
            and effective.get("hash") == root["input_hash"]
            and effective.get("model") == result.get("model_name"),
            "completed_v1_witness",
        )
        _require(
            isinstance(execution, Mapping)
            and execution.get("scope_id") == scope
            and execution.get("operation_key") == key,
            "completed_execution_link",
        )
        declared = execution.get("attempts")
        _require(
            isinstance(declared, list)
            and len(declared) == len(ids)
            and all(isinstance(v, Mapping) for v in declared)
            and {v.get("attempt_id") for v in declared} == set(ids),
            "all_attempts_not_declared",
        )
        for aid in ids:
            _require(
                attempts[aid]["status"] == "completed"
                and aid in transports
                and attempts[aid]["model_name"] == result.get("model_name"),
                "completed_attempt_proof",
            )
            paid = transports[aid]
            for field in (
                "content",
                "provider",
                "model_name",
                "input_tokens",
                "output_tokens",
                "finish_reason",
                "tool_calls",
                "raw_message",
            ):
                _require(
                    field in paid
                    and field in result
                    and _digest(paid[field]) == _digest(result[field]),
                    "immutable_paid_result_mismatch",
                )
            receipt = (paid.get("metadata") or {}).get("effective_input_receipt")
            _require(
                isinstance(receipt, Mapping)
                and receipt.get("schema") == "effective-input.v1"
                and receipt.get("hash") == root["input_hash"]
                and receipt.get("model") == attempts[aid]["model_name"],
                "transport_v1_witness",
            )
        observations[key] = {
            "hash_domain": "effective-input.v1",
            "prior_request_equivalence": "unknown",
            "route": "unknown",
            "body": "unknown",
            "source": "unknown",
            "workflow": "unknown",
            "remote_result": "retained_paid_result",
        }
    hashes = {
        "operations": {k: _digest(v) for k, v in operations.items()},
        "attempts": {k: _digest(v) for k, v in attempts.items()},
        "quarantine_receipts": {k: _digest(v) for k, v in quarantines.items()},
    }
    return {
        "row_hashes": hashes,
        "baseline_sha256": _digest(hashes),
        "observations": observations,
        "exclusions": {
            "operation_keys": sorted(operations),
            "attempt_ids": sorted(attempts),
            "quarantine_receipt_keys": sorted(quarantines),
        },
    }


def build_ambiguity_retirement_receipt(
    *,
    project_id: str,
    chapter_id: str,
    execution_scope_id: str,
    body_sha256: str,
    current_effective_input_hash: str,
    target_protocol: dict[str, Any],
    stable_cohort_key: str,
    allowed_slot_labels: list[str],
    actor: str,
    reason: str,
    original_authorization_ref: str,
    snapshots: dict[str, Any],
) -> dict[str, Any]:
    """Build explicit authorization; it neither allocates slots nor retires debt."""
    _require(
        str(UUID(project_id)) == project_id and str(UUID(chapter_id)) == chapter_id, "binding_uuid"
    )
    _require(
        isinstance(execution_scope_id, str) and 0 < len(execution_scope_id) <= 256, "binding_scope"
    )
    _require(_sha(body_sha256) and _sha(current_effective_input_hash), "target_hash")
    _require(isinstance(stable_cohort_key, str) and stable_cohort_key, "existing_cohort_key")
    _require(
        isinstance(allowed_slot_labels, list)
        and len(allowed_slot_labels) == 3
        and all(isinstance(v, str) and v for v in allowed_slot_labels)
        and len(set(allowed_slot_labels)) == 3,
        "existing_three_slots",
    )
    _require(
        all(isinstance(v, str) and v.strip() for v in (actor, reason, original_authorization_ref)),
        "explicit_authorization_audit",
    )
    _require(
        isinstance(target_protocol, dict)
        and set(target_protocol) == {"prompt_version", "source_manifest", "evaluation_contract"},
        "target_protocol_shape",
    )
    version = _version(target_protocol["prompt_version"])
    _require(version is not None and version >= 11, "typed_target_protocol")
    manifest = target_protocol["source_manifest"]
    _require(
        isinstance(manifest, dict)
        and manifest
        and all(isinstance(k, str) and k and _sha(v) for k, v in manifest.items()),
        "target_manifest",
    )
    _require(
        isinstance(target_protocol["evaluation_contract"], dict)
        and target_protocol["evaluation_contract"],
        "target_contract",
    )
    binding = deepcopy(
        {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "execution_scope_id": execution_scope_id,
            "body_sha256": body_sha256,
            "current_effective_input_hash": current_effective_input_hash,
            "target_protocol": target_protocol,
            "stable_cohort_key": stable_cohort_key,
            "allowed_slot_labels": allowed_slot_labels,
            "actor": actor,
            "reason": reason,
            "original_authorization_ref": original_authorization_ref,
        }
    )
    # Audit/nonce/baseline changes cannot mint a different receipt identity.
    # A changed actual request needs a different target receipt, but NEVER a
    # different consumption identity: that remains the existing cohort key.
    identity_fields = {
        k: binding[k]
        for k in (
            "project_id",
            "chapter_id",
            "execution_scope_id",
            "body_sha256",
            "current_effective_input_hash",
            "target_protocol",
            "stable_cohort_key",
            "allowed_slot_labels",
        )
    }
    return {
        "schema": SCHEMA,
        "meaning": MEANING,
        "identity": _digest({"schema": SCHEMA, "assessment": identity_fields}),
        "binding": binding,
        **_baseline(snapshots, execution_scope_id, current_effective_input_hash),
        "prior_spend_ambiguity_accepted": True,
        "different_old_request_asserted": False,
        "no_earlier_payment_asserted": False,
        "ownership_inferred": False,
        "credit_released": False,
        "operation_replay_allowed": False,
        "old_grades_reused": False,
        "all_historical_debt_retained": True,
        "cohort_identity_changed": False,
    }


def validate_ambiguity_retirement_receipt(
    receipt: dict[str, Any], **binding: Any
) -> dict[str, Any]:
    """Exact reread; never auto-grow or regenerate a persisted authorization.

    Runtime must provide the full relevant current inventory, preserve this
    baseline, serialize on its existing cohort, and reject absent receipts.
    This pure helper cannot prove query completeness or perform consumption.
    """
    expected = build_ambiguity_retirement_receipt(**binding)
    _require(
        isinstance(receipt, dict) and _digest(receipt) == _digest(expected),
        "receipt_or_snapshot_drift",
    )
    return deepcopy(
        {
            **expected["exclusions"],
            "stable_cohort_key": binding["stable_cohort_key"],
            "allowed_slot_labels": binding["allowed_slot_labels"],
        }
    )
