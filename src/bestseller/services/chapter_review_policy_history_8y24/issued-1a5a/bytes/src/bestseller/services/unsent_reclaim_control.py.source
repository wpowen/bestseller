"""Pure structural evidence for budget.py's retained unsent reclaim controls.

The caller must serialize the COMPLETE same-scope operation graph and ALL actual
attempts from one consistent snapshot. A pure function cannot discover omitted
rows or authenticate that inventory. No imports of application code, I/O, clocks,
provider calls, mutation, purpose proof, ownership attribution or credit decision.

The producer has no payload schema field. The witness schema below belongs only
to this validator. A valid stopped parent with no attempts still has UNKNOWN
request purpose; the guard must independently validate every root operation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


_CONTROL = "unsent_reclaim_receipt"
_FIELDS = {"operation_key", "old_owner", "new_owner", "credit_released"}


def _require(condition: Any, reason: str) -> None:
    if not condition:
        raise ValueError("unsent_reclaim_control_" + reason)


def _json(value: Any) -> None:
    """Accept only finite, already serialized JSON; do not coerce custom objects."""
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return
    if kind is float:
        _require(math.isfinite(value), "nonfinite_json")
        return
    if kind is list:
        for item in value:
            _json(item)
        return
    if kind is dict:
        for key, item in value.items():
            _require(type(key) is str, "nonstring_json_key")
            _json(item)
        return
    raise ValueError("unsent_reclaim_control_unserialized_json")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":")).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _text(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def validate_unsent_reclaim_control(
    operation: dict, *, operations: list[dict], actual_attempts: list[dict],
) -> dict:
    """Return a control-only witness, or ValueError for missing/unsafe evidence.

    Every supplied actual must bind to its scope/root/input. Only reclaimed
    parents require completed nonnegative usage or rejected/not_sent zero usage;
    unrelated roots keep their independent financial closure checks. Reconciliation and other terminal failures need another validator.
    Pending/unknown roots fail closed. Historical controls remain valid after a
    later owner completes an actual call. No timestamp is inferred from leases.
    All JSON fields, including opaque extras, are retained by the witness hashes.
    """
    try:
        return _validate(operation, operations, actual_attempts)
    except (TypeError, KeyError, AttributeError, RecursionError, OverflowError,
            UnicodeError) as error:
        raise ValueError("unsent_reclaim_control_malformed_evidence") from error


def _validate(operation, operations, actual_attempts):
    _json([operation, operations, actual_attempts])
    _require(type(operation) is dict and type(operations) is list
             and type(actual_attempts) is list, "unserialized_rows")
    _require(operation.get("status") == _CONTROL, "wrong_status")
    scope = operation.get("scope_id")
    _require(_text(scope), "scope_missing")
    by_key = {}
    for row in operations:
        _require(type(row) is dict, "invalid_operation_row")
        key = row.get("key")
        _require(_sha(key) and key not in by_key, "duplicate_or_invalid_key")
        _require(row.get("scope_id") == scope, "scope_mismatch")
        _require(_sha(row.get("input_hash")) and _text(row.get("owner"))
                 and _text(row.get("status")), "invalid_operation_identity")
        # These ledger models have no project FK. Never fabricate one from a
        # scope, owner, result, or input hash; reject explicit contrary evidence.
        _require(row.get("project_id") is None, "not_projectless")
        by_key[key] = row
    target = by_key.get(operation.get("key"))
    _require(target is not None and _canonical(target) == _canonical(operation),
             "target_not_in_inventory")

    groups = {}
    for row in operations:
        if row["status"] != _CONTROL:
            continue
        receipt = row.get("result_json")
        _require(type(receipt) is dict and set(receipt) == _FIELDS, "payload_fields")
        parent_key = receipt["operation_key"]
        _require(_sha(parent_key), "invalid_parent_key")
        parent = by_key.get(parent_key)
        _require(parent is not None and parent is not row, "missing_or_self_parent")
        _require(parent["status"] in {"completed", "stopped"}, "unsafe_parent_status")
        old, new = receipt["old_owner"], receipt["new_owner"]
        _require(_text(old) and _text(new) and old != new, "invalid_owner_edge")
        _require(receipt["credit_released"] is False, "payload_credit_field")
        expected = hashlib.sha256(
            ("unsent-reclaim.v1\n" + parent_key + "\n" + old).encode("utf-8")
        ).hexdigest()
        _require(row["key"] == expected, "producer_key_mismatch")
        _require(row["owner"] == old, "row_owner_mismatch")
        _require(row["input_hash"] == parent["input_hash"], "parent_input_mismatch")
        groups.setdefault(parent_key, []).append(row)

    attempts = {}
    attempts_by_root = {}
    for row in actual_attempts:
        _require(type(row) is dict, "invalid_actual_row")
        aid = row.get("id")
        _require(_text(aid) and aid not in attempts, "duplicate_or_invalid_actual_id")
        parent_key = row.get("operation_key")
        _require(_sha(parent_key), "invalid_actual_parent")
        parent = by_key.get(parent_key)
        _require(parent is not None, "unsafe_actual_parent")
        if parent_key in groups:
            _require(parent["status"] in {"completed", "stopped"}, "unsafe_actual_parent")
        _require(row.get("scope_id") == scope
                 and row.get("input_hash") == parent["input_hash"], "actual_binding")
        _require(row.get("project_id") is None, "not_projectless")
        status, tokens = row.get("status"), row.get("actual_tokens")
        if parent_key in groups:
            _require(type(tokens) is int and (
                (status == "completed" and tokens >= 0)
                or (status in {"rejected", "not_sent"} and tokens == 0)
            ), "unsafe_actual_status_or_usage")
        attempts[aid] = row
        attempts_by_root.setdefault(parent_key, []).append(row)

    chains = {}
    for parent_key, siblings in groups.items():
        parent = by_key[parent_key]
        if parent["status"] == "completed":
            _require(any(a["status"] == "completed"
                         for a in attempts_by_root.get(parent_key, [])),
                     "completed_parent_without_completed_actual")
        outgoing, incoming = {}, set()
        for row in siblings:
            receipt = row["result_json"]
            old, new = receipt["old_owner"], receipt["new_owner"]
            _require(old not in outgoing, "duplicate_old_owner")
            _require(new not in incoming, "owner_merge")
            outgoing[old] = row
            incoming.add(new)
        starts = set(outgoing) - incoming
        _require(len(starts) == 1 and parent["owner"] not in outgoing,
                 "fork_cycle_or_current_owner")
        owner = next(iter(starts))
        owners, keys, seen = [owner], [], set()
        while owner in outgoing:
            _require(owner not in seen, "owner_cycle")
            seen.add(owner)
            row = outgoing[owner]
            keys.append(row["key"])
            owner = row["result_json"]["new_owner"]
            owners.append(owner)
        _require(len(keys) == len(siblings) and owner == parent["owner"],
                 "disconnected_or_wrong_current_owner")
        chains[parent_key] = {"owners": owners, "control_keys": keys}

    parent_key = target["result_json"]["operation_key"]
    operation_inventory = sorted(operations, key=lambda row: row["key"])
    actual_inventory = sorted(actual_attempts, key=lambda row: row["id"])
    return {
        "schema": "unsent-reclaim-control-witness.v1",
        "control_key": target["key"], "parent_key": parent_key, "scope_id": scope,
        "parent_status": by_key[parent_key]["status"],
        "owner_chain": chains[parent_key],
        "root_requires_independent_validation": True,
        "control_row_sha256": _hash(target),
        "parent_row_sha256": _hash(by_key[parent_key]),
        "operation_row_sha256": {r["key"]: _hash(r) for r in operation_inventory},
        "actual_row_sha256": {r["id"]: _hash(r) for r in actual_inventory},
        "operations_sha256": _hash(operation_inventory),
        "actual_attempts_sha256": _hash(actual_inventory),
        "inventory_sha256": _hash({"operations": operation_inventory,
                                    "actual_attempts": actual_inventory}),
    }
