"""Explicit continuation of ONE fixed ambiguity baseline; pure, no dispatch.

The caller must load the named original completed baseline workflow, persist an
explicit operator grant, and supply the COMPLETE current residual inventory.
Hashes are integrity checks, not signatures or proof of DB/operator authority.
Validation reconstructs the original receipt using that inventory. It does not
attribute old spend, allocate credit, or authorize replay. Each new assessment
still needs its own exact body/source/evaluation/request-bound child receipt and
the existing cohort/three-slot guards, checkpointed before provider dispatch.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from bestseller.services.commercial_ambiguity_retirement import (
    MEANING as BASELINE_MEANING,
)
from bestseller.services.commercial_ambiguity_retirement import (
    SCHEMA as BASELINE_SCHEMA,
)
from bestseller.services.commercial_ambiguity_retirement import (
    validate_ambiguity_retirement_receipt,
)
from bestseller.services.commercial_protocol_transition import _digest, _sha, _version

# ruff: noqa: ANN401
SCHEMA = "commercial-ambiguity-continuation.v1"
MEANING = "explicit_goal_authorization_to_preserve_fixed_legacy_ambiguity"
_FLAGS = {
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
_IDENTITY_FIELDS = (
    "project_id",
    "chapter_id",
    "execution_scope_id",
    "body_sha256",
    "current_effective_input_hash",
    "target_protocol",
    "stable_cohort_key",
    "allowed_slot_labels",
)


def _require(value: Any, code: str) -> None:
    if not value:
        raise ValueError(f"commercial_ambiguity_continuation_{code}")


def _check_baseline(receipt: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    """Check retained structure; row preimages are checked by validate below."""
    _require(isinstance(receipt, dict), "baseline_required")
    binding = receipt.get("binding")
    _require(isinstance(binding, dict), "baseline_binding")
    _require(
        set(binding) == set(_IDENTITY_FIELDS) | {"actor", "reason", "original_authorization_ref"},
        "baseline_binding_shape",
    )
    _require(
        set(receipt)
        == {
            "schema",
            "meaning",
            "identity",
            "binding",
            "row_hashes",
            "baseline_sha256",
            "observations",
            "exclusions",
            *_FLAGS,
        }
        and receipt.get("schema") == BASELINE_SCHEMA
        and receipt.get("meaning") == BASELINE_MEANING
        and all(receipt.get(k) is v for k, v in _FLAGS.items()),
        "baseline_schema_or_permissions",
    )
    identity = _digest(
        {
            "schema": BASELINE_SCHEMA,
            "assessment": {key: binding[key] for key in _IDENTITY_FIELDS},
        }
    )
    _require(receipt.get("identity") == identity, "baseline_identity")
    # This is the existing runtime's workflow identity domain, not a new ledger
    # operation, request, cohort, or consumption namespace.
    _require(
        workflow_id == str(uuid5(NAMESPACE_URL, "bestseller:" + BASELINE_SCHEMA + ":" + identity)),
        "baseline_workflow_identity",
    )
    hashes = receipt.get("row_hashes")
    _require(
        isinstance(hashes, dict)
        and set(hashes) == {"operations", "attempts", "quarantine_receipts"}
        and all(isinstance(rows, dict) for rows in hashes.values())
        and hashes["operations"]
        and hashes["attempts"]
        and all(
            isinstance(k, str) and k and _sha(v)
            for rows in hashes.values()
            for k, v in rows.items()
        )
        and receipt.get("baseline_sha256") == _digest(hashes),
        "baseline_row_hashes",
    )
    _require(
        receipt.get("exclusions")
        == {
            "operation_keys": sorted(hashes["operations"]),
            "attempt_ids": sorted(hashes["attempts"]),
            "quarantine_receipt_keys": sorted(hashes["quarantine_receipts"]),
        },
        "baseline_exclusions",
    )
    _digest(receipt)  # Reject non-JSON/non-finite retained evidence.
    return binding


def build_continuation_grant(
    *,
    project_id: str,
    execution_scope_id: str,
    baseline_workflow_id: str,
    baseline_receipt: dict[str, Any],
    target_protocol_policy: dict[str, Any],
    original_authorization_ref: str,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    """Build an operator proposal, never infer authorization from metadata.

    Explicitly selected target policy may differ from the historical baseline's
    policy. Subsequent reads must match this grant's exact selected policy; the
    per-body evaluation contract deliberately belongs to the child receipt.
    """
    _require(str(UUID(project_id)) == project_id, "project_uuid")
    _require(
        isinstance(execution_scope_id, str) and 0 < len(execution_scope_id) <= 256,
        "scope",
    )
    _require(
        all(isinstance(v, str) and v.strip() for v in (original_authorization_ref, actor, reason)),
        "explicit_authorization_audit",
    )
    old = _check_baseline(baseline_receipt, baseline_workflow_id)
    _require(
        old["project_id"] == project_id
        and old["execution_scope_id"] == execution_scope_id
        and old["original_authorization_ref"] == original_authorization_ref,
        "baseline_authorization_binding",
    )
    _require(
        isinstance(target_protocol_policy, dict)
        and set(target_protocol_policy) == {"prompt_version", "source_manifest"},
        "target_policy_shape",
    )
    version = _version(target_protocol_policy["prompt_version"])
    manifest = target_protocol_policy["source_manifest"]
    _require(version is not None and version >= 11, "target_protocol")
    _require(
        isinstance(manifest, dict)
        and manifest
        and all(isinstance(k, str) and k and _sha(v) for k, v in manifest.items()),
        "target_manifest",
    )
    binding = deepcopy(
        {
            "project_id": project_id,
            "execution_scope_id": execution_scope_id,
            "baseline_workflow_id": baseline_workflow_id,
            "baseline_receipt_sha256": _digest(baseline_receipt),
            "target_protocol_policy": target_protocol_policy,
            "original_authorization_ref": original_authorization_ref,
            "actor": actor,
            "reason": reason,
        }
    )
    return {
        "schema": SCHEMA,
        "meaning": MEANING,
        # Audit text cannot mint a fresh consumption identity. This grant ID
        # must never be used as a cohort, slot, or paid operation namespace.
        "identity": _digest(
            {
                "schema": SCHEMA,
                "authorization": {k: v for k, v in binding.items() if k not in {"actor", "reason"}},
            }
        ),
        "binding": binding,
        "baseline_sha256": baseline_receipt["baseline_sha256"],
        "row_hashes": deepcopy(baseline_receipt["row_hashes"]),
        "exclusions": deepcopy(baseline_receipt["exclusions"]),
        **_FLAGS,
    }


def validate_continuation_grant(
    grant: dict[str, Any],
    *,
    project_id: str,
    execution_scope_id: str,
    baseline_workflow_id: str,
    baseline_receipt: dict[str, Any],
    target_protocol_policy: dict[str, Any],
    original_authorization_ref: str,
    actor: str,
    reason: str,
    snapshots: dict[str, Any],
) -> dict[str, Any]:
    """Exact grant and original baseline replay against current FULL inventory.

    No old chapter/cohort identity is returned for reuse. Caller must still
    validate the current body-specific child with the existing ambiguity helper
    (including collision with the actual current effective request hash).
    """
    expected = build_continuation_grant(
        project_id=project_id,
        execution_scope_id=execution_scope_id,
        baseline_workflow_id=baseline_workflow_id,
        baseline_receipt=baseline_receipt,
        target_protocol_policy=target_protocol_policy,
        original_authorization_ref=original_authorization_ref,
        actor=actor,
        reason=reason,
    )
    _require(isinstance(grant, dict) and _digest(grant) == _digest(expected), "grant_drift")
    # Full reconstruction verifies every retained field and every original row
    # hash, including ALL attempts and controls. No baseline union or extension.
    validate_ambiguity_retirement_receipt(
        baseline_receipt,
        **baseline_receipt["binding"],
        snapshots=snapshots,
    )
    return deepcopy(
        {
            **baseline_receipt["exclusions"],
            "row_hashes": baseline_receipt["row_hashes"],
            "baseline_sha256": baseline_receipt["baseline_sha256"],
            "baseline_receipt_sha256": expected["binding"]["baseline_receipt_sha256"],
            "baseline_workflow_id": baseline_workflow_id,
        }
    )
