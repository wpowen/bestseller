"""User-authorized test export projection; never changes an ORM source row."""

from copy import deepcopy
import hashlib
import json
import re

SOURCE_ID = "fa14e681-2bd9-44f5-93d1-7f2ef9900d69"
SCHEMA = "p3-test-prefix-exclusions.v1"
DECISION = "user-2026-10-28-1950"
FIELD_PATH = "workflow_runs.metadata.full_outline_revision.authorization_ref"
REASON = "Exclude historical authorization label from isolated test export only."
HASH_POLICY = (
    "Original row SHA is computed before projection and retained unchanged as provenance; "
    "the exported row is a different projection with its own SHA. Existing source, "
    "payload, artifact and complete-plan hashes are never rewritten. No production mutation."
)


def _sha(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _require(ok):
    if not ok:
        raise ValueError("prefix_exclusion_manifest_invalid")


def _path(index):
    return f"$.tables.workflow_runs[{index}].metadata_json.full_outline_revision.authorization_ref"


def exclude_workflow_authorization_ref(candidate):
    """Copy the candidate; remove only the approved workflow metadata leaf."""
    _require(candidate.get("project_id") == SOURCE_ID and "export_manifest" not in candidate)
    body = deepcopy(candidate)
    exclusions = []
    for index, row in enumerate(body["tables"]["workflow_runs"]):
        metadata = row.get("metadata_json") or {}
        revision = metadata.get("full_outline_revision")
        if not isinstance(revision, dict) or "authorization_ref" not in revision:
            continue
        _require(isinstance(revision["authorization_ref"], str))
        original_sha = _sha(row)
        del revision["authorization_ref"]
        exclusions.append(
            {
                "table": "workflow_runs",
                "database_field_path": FIELD_PATH,
                "field_path": _path(index),
                "row_id": row["id"],
                "reason": REASON,
                "original_row_sha256": original_sha,
                "exported_row_sha256": _sha(row),
            }
        )
    _require(bool(exclusions))
    body["export_manifest"] = {
        "schema": SCHEMA,
        "user_decision": DECISION,
        "isolated_test_only": True,
        "production_writes": 0,
        "original_row_sha_policy": HASH_POLICY,
        "exclusions": exclusions,
    }
    validate_exclusion_manifest(body)
    return body


def validate_exclusion_manifest(bundle):
    """Validate declarations against the projected rows; legacy bundles stay valid.

    Original row SHA is custody metadata, not a fabricated hash of the redacted
    row. It cannot be reconstructed from an omitted private value at import.
    The independent package SHA binds the capture's complete declaration.
    """
    if "export_manifest" not in bundle:
        return
    manifest = bundle["export_manifest"]
    _require(isinstance(manifest, dict))
    _require(
        set(manifest)
        == {
            "schema",
            "user_decision",
            "isolated_test_only",
            "production_writes",
            "original_row_sha_policy",
            "exclusions",
        }
    )
    _require(
        bundle.get("project_id") == SOURCE_ID
        and manifest["schema"] == SCHEMA
        and manifest["user_decision"] == DECISION
        and manifest["isolated_test_only"] is True
        and type(manifest["production_writes"]) is int
        and manifest["production_writes"] == 0
        and manifest["original_row_sha_policy"] == HASH_POLICY
    )
    entries = manifest["exclusions"]
    _require(isinstance(entries, list) and bool(entries))
    rows = bundle["tables"]["workflow_runs"]
    seen = set()
    for entry in entries:
        _require(
            isinstance(entry, dict)
            and set(entry)
            == {
                "table",
                "database_field_path",
                "field_path",
                "row_id",
                "reason",
                "original_row_sha256",
                "exported_row_sha256",
            }
        )
        indices = [i for i, row in enumerate(rows) if row.get("id") == entry["row_id"]]
        _require(len(indices) == 1 and entry["row_id"] not in seen)
        seen.add(entry["row_id"])
        index = indices[0]
        row = rows[index]
        revision = (row.get("metadata_json") or {}).get("full_outline_revision")
        _require(
            entry["table"] == "workflow_runs"
            and entry["database_field_path"] == FIELD_PATH
            and entry["field_path"] == _path(index)
            and entry["reason"] == REASON
            and isinstance(entry["original_row_sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", entry["original_row_sha256"]) is not None
            and isinstance(revision, dict)
            and "authorization_ref" not in revision
            and entry["exported_row_sha256"] == _sha(row)
        )
