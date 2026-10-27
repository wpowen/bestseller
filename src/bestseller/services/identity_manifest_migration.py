"""Pure, append-only repair of identities omitted from an authoritative Cast."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.services.narrative_contracts import build_identity_manifest


def identity_manifest_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                   allow_nan=False).encode("utf-8")
    ).hexdigest()


def _tokens(entry: Mapping[str, Any]) -> set[str]:
    aliases = entry.get("aliases") or []
    if not isinstance(aliases, list):
        raise ValueError("Identity aliases must be a list.")
    return {"".join(value.lower().split())
            for value in (entry.get("name"), *aliases) if isinstance(value, str) and value.strip()}


def propose_identity_manifest_extension(
    existing_manifest: Sequence[Mapping[str, Any]], *, project_id: str,
    cast_source: Mapping[str, Any],
) -> dict[str, Any]:
    """Propose missing source names without editing any existing identity lock.

    ``cast_source`` is an export of an existing approved Cast artifact, with
    id/project_id/artifact_type/version_no/status/content. Approval is checked,
    never created by this helper. Production callers must load that DB record.
    """
    if (str(cast_source.get("project_id")) != str(project_id)
        or cast_source.get("artifact_type") != "cast_spec"
        or cast_source.get("status") != "approved"
        or not cast_source.get("id")
        or type(cast_source.get("version_no")) is not int
        or cast_source.get("version_no", 0) < 1
        or not isinstance(cast_source.get("content"), dict)):
        raise ValueError("An approved Cast artifact from this project is required.")
    before = copy.deepcopy(list(existing_manifest))
    if any(not isinstance(entry, Mapping) or not _tokens(entry) for entry in before):
        raise ValueError("Existing identity locks must be named records.")
    occupied = set().union(*(_tokens(entry) for entry in before)) if before else set()
    additions = []
    for entry in build_identity_manifest(cast_source["content"]):
        tokens = _tokens(entry)
        if tokens & occupied:
            # Existing names, aliases, genders and all other locks are immutable.
            continue
        additions.append(copy.deepcopy(entry))
        occupied.update(tokens)
    candidate = before + additions
    return {
        "api_version": "identity_manifest_extension_v1", "project_id": str(project_id),
        "status": "candidate" if additions else "unchanged",
        "source": {"artifact_id": str(cast_source["id"]),
                   "version_no": cast_source["version_no"], "status": "approved",
                   "content_hash": identity_manifest_hash(cast_source["content"])},
        "before_hash": identity_manifest_hash(before),
        "after_hash": identity_manifest_hash(candidate),
        "original_manifest": before, "candidate_manifest": candidate,
        "added_entries": additions,
    }


def apply_identity_manifest_extension(
    metadata: Mapping[str, Any], proposal: Mapping[str, Any], *,
    cast_source: Mapping[str, Any], expected_source_hash: str,
    actor: str, evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Return new metadata with the complete old snapshot and a source receipt.

    No DB writes, semantic approval or identity attributes are fabricated.
    Repeated application returns the exact existing metadata unchanged.
    """
    if not isinstance(actor, str) or not actor.strip() or not isinstance(evidence, Mapping) or not evidence:
        raise ValueError("An operator actor and explicit evidence are required.")
    if identity_manifest_hash(cast_source.get("content")) != expected_source_hash:
        raise ValueError("Cast source hash changed; re-audit before applying.")
    verified = propose_identity_manifest_extension(
        proposal.get("original_manifest", []), project_id=str(proposal.get("project_id")),
        cast_source=cast_source,
    )
    if verified != dict(proposal) or verified["source"]["content_hash"] != expected_source_hash:
        raise ValueError("Proposal is not the exact source-bound append-only candidate.")
    result = copy.deepcopy(dict(metadata))
    current_hash = identity_manifest_hash(result.get("identity_manifest", []))
    if current_hash == verified["after_hash"]:
        return result
    if current_hash != verified["before_hash"]:
        raise ValueError("Identity manifest changed; re-audit before applying.")
    receipts = result.get("identity_manifest_migration_receipts", [])
    if not isinstance(receipts, list):
        raise ValueError("Identity migration receipts must be a list.")
    result["identity_manifest_migration_receipts"] = receipts + [{
        "kind": "identity_manifest_extension_v1", "actor": actor.strip(),
        "evidence": copy.deepcopy(dict(evidence)), "source": verified["source"],
        "before_hash": verified["before_hash"], "after_hash": verified["after_hash"],
        "original_manifest": verified["original_manifest"],
        "added_entries": verified["added_entries"],
    }]
    result["identity_manifest"] = verified["candidate_manifest"]
    # Approval is inherited solely from the loaded Cast record, not the operator.
    return result
