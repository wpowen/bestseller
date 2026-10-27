"""Pure proof for explicitly retiring legacy commercial assessment evidence.

No storage, dispatch, budget mutation, result adoption or inferred WF/body owner.
The operator supplies complete serialized original rows, including ALL attempts
for each proposed operation. The existing runtime owns cohort/slot identity.

API: build_transition_receipt(**binding, snapshots=...) and
validate_transition_receipt(receipt, **binding, snapshots=...). ``binding`` is
project_id, chapter_id, execution_scope_id, target_protocol and
current_effective_input_hash. target_protocol contains prompt_version,
source_manifest (path -> SHA256), and evaluation_contract. snapshots contains
runs, operations, attempts and quarantine_receipts lists; quarantine_receipts
are full reconciliation operation rows also present in operations.

These inputs describe proposed legacy exclusions plus newly observed relevant
orphan/commercial evidence. They are not a body attribution inventory: callers
must not filter unknown-body evidence away. Existing bound current cohorts are
handled by the ordinary cohort validator, never replenished through this API.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
from typing import Any
from uuid import UUID

# ruff: noqa: ANN401
SCHEMA = "commercial-protocol-transition.v1"
TEMPLATE = "chapter_commercial_quality_judge"
LEGACY_VERSIONS = frozenset({2, 3, 4, 7, 8, 9, 10})


def _require(value: Any, code: str) -> None:
    if not value:
        raise ValueError(f"commercial_transition_{code}")


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _version(value: Any) -> int | None:
    match = re.match(r"^v(\d+)(?:[-.]|$)", value) if isinstance(value, str) else None
    return int(match[1]) if match else None


def _metadata(row: Mapping[str, Any]) -> Mapping[str, Any]:
    if "metadata" in row and "metadata_json" in row:
        _require(row["metadata"] == row["metadata_json"], "metadata_alias_conflict")
    value = row.get("metadata_json", row.get("metadata", {}))
    _require(isinstance(value, Mapping), "metadata_invalid")
    return value


def _index(rows: Any, key: str) -> dict[str, dict[str, Any]]:
    _require(isinstance(rows, list), "snapshot_list_required")
    out = {}
    for row in rows:
        _require(isinstance(row, dict), "snapshot_row_required")
        identity = row.get(key)
        _require(
            isinstance(identity, str) and identity and identity not in out, "snapshot_identity"
        )
        _digest(row)  # Reject non-JSON values and non-finite numbers.
        out[identity] = row
    return out


def _binding(
    *,
    project_id: str,
    chapter_id: str,
    execution_scope_id: str,
    target_protocol: dict[str, Any],
    current_effective_input_hash: str,
) -> dict[str, Any]:
    _require(
        str(UUID(project_id)) == project_id and str(UUID(chapter_id)) == chapter_id, "binding_uuid"
    )
    _require(
        isinstance(execution_scope_id, str) and 0 < len(execution_scope_id) <= 256, "binding_scope"
    )
    _require(_sha(current_effective_input_hash), "current_effective_hash")
    _require(
        isinstance(target_protocol, dict)
        and set(target_protocol) == {"prompt_version", "source_manifest", "evaluation_contract"},
        "target_protocol_shape",
    )
    version = _version(target_protocol["prompt_version"])
    _require(version is not None and version > 10, "target_protocol_version")
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
        "target_evaluation_contract",
    )
    return deepcopy(
        {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "execution_scope_id": execution_scope_id,
            "target_protocol": target_protocol,
            "current_effective_input_hash": current_effective_input_hash,
            "effective_hash_schema": "effective-input.v1",
        }
    )


def _proof(binding: dict[str, Any], snapshots: dict[str, Any]) -> dict[str, Any]:
    _require(
        isinstance(snapshots, dict)
        and set(snapshots) == {"runs", "operations", "attempts", "quarantine_receipts"},
        "snapshot_shape",
    )
    runs = _index(snapshots["runs"], "id")
    operations = _index(snapshots["operations"], "key")
    attempts = _index(snapshots["attempts"], "id")
    quarantines = _index(snapshots["quarantine_receipts"], "key")
    scope, current = binding["execution_scope_id"], binding["current_effective_input_hash"]
    retired, witnesses = {}, {}
    for uid, run in runs.items():
        # NULL remains unattributed; the original execution scope below is the
        # financial boundary, never a backfill of project or workflow ownership.
        _require(run.get("project_id") in {None, binding["project_id"]}, "foreign_project")
        meta = _metadata(run)
        effective, execution = (
            meta.get("effective_input_receipt", {}),
            meta.get("execution_receipt", {}),
        )
        _require(isinstance(effective, Mapping) and isinstance(execution, Mapping), "run_receipts")
        if run.get("prompt_template") != TEMPLATE:
            continue
        _require(
            _version(run.get("prompt_version")) in LEGACY_VERSIONS,
            "current_or_unknown_protocol_paid_run",
        )
        h = run.get("prompt_hash")
        _require(_sha(h) and h != current, "matching_current_request")
        _require(
            effective.get("schema") == "effective-input.v1"
            and effective.get("hash") == h
            and execution.get("scope_id") == scope
            and _sha(execution.get("operation_key")),
            "comparable_v1_witness_missing",
        )
        _require(isinstance(run.get("model_name"), str) and run["model_name"], "run_model_missing")
        _require(effective.get("model", run["model_name"]) == run["model_name"], "witness_model")
        witnesses.setdefault((h, run["model_name"]), []).append(uid)
        retired[uid] = _digest(run)
    _require(retired, "legacy_closed_set_empty")

    # Every supplied actual attempt is fenced before any retirement decision.
    by_operation = {}
    for uid, attempt in attempts.items():
        _require(attempt.get("scope_id") == scope, "attempt_scope")
        _require(_sha(attempt.get("input_hash")), "attempt_input_hash")
        _require(attempt["input_hash"] != current, "matching_current_attempt")
        _require(
            attempt.get("status") in {"completed", "reconciled_charged"}, "attempt_not_terminal"
        )
        _require(attempt.get("operation_key") in operations, "attempt_parent_missing")
        parent = operations[attempt["operation_key"]]
        _require(parent.get("status") in {"completed", "stopped"}, "attempt_parent_not_request")
        _require(attempt["input_hash"] == parent.get("input_hash"), "attempt_parent_input_mismatch")
        _require(
            (attempt["input_hash"], attempt.get("model_name")) in witnesses,
            "actual_attempt_v1_witness_missing",
        )
        by_operation.setdefault(attempt["operation_key"], []).append(uid)

    quarantine_by_attempt = {}
    for key, row in quarantines.items():
        _require(
            key in operations and _digest(operations[key]) == _digest(row),
            "quarantine_snapshot_mismatch",
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
        expected_key = hashlib.sha256(f"reconciliation.v1\n{scope}\n{rid}".encode()).hexdigest()
        _require(
            key == expected_key
            and row.get("input_hash") == record.get("request_hash")
            and _sha(record.get("request_hash")),
            "quarantine_record_hash",
        )
        _require(
            receipt.get("scope_id") == scope
            and receipt.get("decision") == "quarantine_charged"
            and receipt.get("credit_released") is False
            and receipt.get("operation_replay_allowed") is False
            and receipt.get("remote_outcome") == "unknown_quarantined"
            and receipt.get("automatic_resume") is False
            and type(receipt.get("remaining_unknown")) is int
            and receipt["remaining_unknown"] == 0
            and record.get("local_dispatcher_stopped") is True
            and record.get("remote_operation_closed") is False
            and record.get("evidence_kind") == "dispatcher_exit"
            and _sha(record.get("evidence_sha256")),
            "quarantine_not_charged_and_closed",
        )
        _require(
            aid in attempts
            and attempts[aid].get("status") == "reconciled_charged"
            and aid not in quarantine_by_attempt,
            "quarantine_attempt",
        )
        quarantine_by_attempt[aid] = key

    excluded_ops = {}
    for key, op in operations.items():
        _require(_sha(key) and op.get("scope_id") == scope, "operation_scope_or_key")
        status, envelope = op.get("status"), op.get("result_json")
        if status == "reconciliation_receipt":
            _require(key in quarantines, "unlisted_reconciliation_receipt")
            continue
        if status in {"transport_result", "transport_failure_receipt"}:
            _require(isinstance(envelope, Mapping), "transport_envelope")
            aid = envelope.get("attempt_id")
            _require(aid in attempts, "transport_attempt_missing")
            attempt = attempts[aid]
            _require(
                envelope.get("operation_key") == attempt["operation_key"]
                and op.get("input_hash") == attempt["input_hash"],
                "transport_input_link",
            )
            prefix = (
                "transport-result.v1" if status == "transport_result" else "transport-failure.v1"
            )
            _require(
                key == hashlib.sha256(f"{prefix}\n{scope}\n{aid}".encode()).hexdigest(),
                "transport_identity",
            )
            if status == "transport_result":
                _require(
                    envelope.get("schema") == "transport-result.v1"
                    and envelope.get("input_hash") == attempt["input_hash"],
                    "transport_hash",
                )
                result = envelope.get("result")
                _require(
                    isinstance(result, Mapping)
                    and result.get("model_name") == attempt.get("model_name"),
                    "transport_model",
                )
            else:
                result = None
                _require(aid in quarantine_by_attempt, "failure_transport_not_quarantined")
        else:
            _require(status in {"completed", "stopped"}, "operation_not_terminal")
            _require(
                _sha(op.get("input_hash")) and op["input_hash"] != current,
                "matching_current_operation",
            )
            actual = by_operation.get(key, [])
            _require(actual, "all_actual_attempts_required")
            if status == "stopped":
                _require(
                    envelope is None and all(a in quarantine_by_attempt for a in actual),
                    "stopped_operation_not_quarantined",
                )
            else:
                _require(
                    isinstance(envelope, Mapping)
                    and all(attempts[a]["status"] == "completed" for a in actual),
                    "completed_operation_invalid",
                )
            _require(
                any(h == op["input_hash"] for h, _ in witnesses), "operation_v1_witness_missing"
            )
            result = envelope
        if isinstance(result, Mapping):
            _require(
                isinstance(result.get("content"), str)
                and (op["input_hash"], result.get("model_name")) in witnesses,
                "paid_result_model_or_content",
            )
            meta = result.get("metadata", {})
            _require(isinstance(meta, Mapping), "paid_metadata")
            effective = meta.get("effective_input_receipt")
            if effective is not None:
                _require(
                    isinstance(effective, Mapping)
                    and effective.get("schema") == "effective-input.v1"
                    and effective.get("hash") == op["input_hash"],
                    "paid_effective_hash",
                )
            origin = meta.get("paid_call_origin")
            if origin is not None:
                _require(
                    isinstance(origin, Mapping)
                    and origin.get("schema") == "paid-call-origin.v1"
                    and origin.get("status") == "recorded"
                    and origin.get("project_id") in {None, binding["project_id"]}
                    and origin.get("prompt_template") == TEMPLATE
                    and origin.get("prompt_hash") == op["input_hash"]
                    and _version(origin.get("prompt_version")) in LEGACY_VERSIONS,
                    "current_foreign_or_unknown_paid_origin",
                )
        excluded_ops[key] = _digest(op)
    for run in runs.values():
        if run["id"] not in retired:
            continue
        execution = _metadata(run)["execution_receipt"]
        key = execution["operation_key"]
        _require(key in excluded_ops and key in by_operation, "legacy_run_operation_missing")
        actual = by_operation[key]
        _require(
            operations[key]["input_hash"] == run["prompt_hash"]
            and all(
                attempts[a]["input_hash"] == run["prompt_hash"]
                and attempts[a]["model_name"] == run["model_name"]
                for a in actual
            ),
            "legacy_run_attempt_input_mismatch",
        )
        declared = execution.get("attempts")
        _require(
            isinstance(declared, list)
            and len(declared) == len(actual)
            and all(isinstance(v, Mapping) for v in declared)
            and {v.get("attempt_id") for v in declared} == set(actual),
            "legacy_run_attempt_set",
        )
    _require(
        all(
            a in quarantine_by_attempt
            for a, v in attempts.items()
            if v["status"] == "reconciled_charged"
        ),
        "charged_attempt_receipt_missing",
    )
    return {
        "row_hashes": {
            "runs": retired,
            "operations": excluded_ops,
            "attempts": {k: _digest(v) for k, v in attempts.items()},
            "quarantine_receipts": {k: _digest(v) for k, v in quarantines.items()},
        },
        "exclusions": {
            "run_ids": sorted(retired),
            "operation_keys": sorted(excluded_ops),
            "attempt_ids": sorted(attempts),
            "quarantine_receipt_keys": sorted(quarantines),
        },
        "v1_hash_witnesses": [
            {"input_hash": h, "model_name": model, "original_run_ids": sorted(ids)}
            for (h, model), ids in sorted(witnesses.items())
        ],
    }


def build_transition_receipt(
    *,
    project_id: str,
    chapter_id: str,
    execution_scope_id: str,
    target_protocol: dict[str, Any],
    current_effective_input_hash: str,
    snapshots: dict[str, Any],
) -> dict[str, Any]:
    """Build a deterministic, unpersisted explicit protocol-retirement proof."""
    binding = _binding(
        project_id=project_id,
        chapter_id=chapter_id,
        execution_scope_id=execution_scope_id,
        target_protocol=target_protocol,
        current_effective_input_hash=current_effective_input_hash,
    )
    proof = _proof(binding, snapshots)
    return {
        "schema": SCHEMA,
        "identity": _digest({"schema": SCHEMA, "binding": binding}),
        "binding": binding,
        **proof,
        "meaning": "explicit_new_protocol_assessment_not_first_same_body_or_no_earlier_payment",
        "body_exclusion_asserted": False,
        "ownership_inferred": False,
        "credit_released": False,
        "operation_replay_allowed": False,
        "old_grades_reused": False,
        "cohort_identity_changed": False,
    }


def validate_transition_receipt(
    receipt: dict[str, Any],
    *,
    project_id: str,
    chapter_id: str,
    execution_scope_id: str,
    target_protocol: dict[str, Any],
    current_effective_input_hash: str,
    snapshots: dict[str, Any],
) -> dict[str, list[str]]:
    """Revalidate current originals, binding and closed set; return exact IDs only.

    The caller must reread the complete relevant inventory, not only receipt IDs.
    New/unlisted current protocol rows and any actual matching/unknown attempt
    therefore fail before exclusions are returned. Persisting this proof does
    not authorize a dispatch or override the runtime's existing cohort fences.
    """
    expected = build_transition_receipt(
        project_id=project_id,
        chapter_id=chapter_id,
        execution_scope_id=execution_scope_id,
        target_protocol=target_protocol,
        current_effective_input_hash=current_effective_input_hash,
        snapshots=snapshots,
    )
    _require(
        isinstance(receipt, dict) and _digest(receipt) == _digest(expected),
        "receipt_or_original_snapshot_drift",
    )
    return deepcopy(expected["exclusions"])
