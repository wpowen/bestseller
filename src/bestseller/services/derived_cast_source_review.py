# ruff: noqa: ANN401, B904, E501, RUF001
"""Bounded legacy-cast candidate preparation and independent source review.

The candidate builder preserves the legacy cast as an unapproved observation
and records field diffs; it never treats that observation as current canon.
Only typed approved-source patches and two independent receipts can make the
candidate eligible for projection.  The explicit preparation phase may call
the configured LLM once for editing and twice for independent receipts;
projection verifies those receipts before use.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
import json
from typing import Any
from uuid import uuid4

from bestseller.services.story_rebuild_workspace import source_content_hash

VERSION = "derived-cast-source-review.v5"
AXES = ("identity_binding", "source_fidelity", "mechanism_and_cost", "dependency_scope")
_IGNORED_SOURCE_KEYS = frozenset({"_meta", "metadata", "control", "audit", "provenance"})
_APPROVED_SOURCE_DOMAINS = frozenset({"premise", "book_spec", "world_spec", "historical_cast"})
_PROTECTED_FIELDS = frozenset(
    {
        "name", "canonical_name", "aliases", "role", "gender", "pronoun_set_zh",
        "pronoun_set_en", "power_tier", "strength", "fear", "secret", "decision_policy",
        "metadata", "life_history", "moral_framework", "family_imprint", "psych_profile",
    }
)


def normalize_cast_recovery_sources(
    book_content: Any, world_content: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Unwrap persisted artifact envelopes identically for prepare and projection."""

    def unwrap(value: Any, key: str) -> dict[str, Any]:
        if isinstance(value, Mapping) and isinstance(value.get(key), Mapping):
            return deepcopy(dict(value[key]))
        return deepcopy(dict(value)) if isinstance(value, Mapping) else {}

    return unwrap(book_content, "book_spec"), unwrap(world_content, "world_spec")


async def collect_approved_historical_casts(
    session: Any, *, project_id: Any, current_version: int
) -> list[Any]:
    """Read the same immutable historical Cast set for prepare and projection."""

    from sqlalchemy import select

    from bestseller.domain.enums import ArtifactType
    from bestseller.infra.db.models import PlanningArtifactVersionModel

    result = await session.scalars(
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == ArtifactType.CAST_SPEC.value,
            PlanningArtifactVersionModel.status == "approved",
            PlanningArtifactVersionModel.version_no < current_version,
        )
        .order_by(PlanningArtifactVersionModel.version_no.asc())
    )
    return list(result.all())


def _text(value: object) -> str:
    return str(value or "").strip()


def _format_path(path: list[object]) -> str:
    rendered = str(path[0]) if path else ""
    for part in path[1:]:
        rendered += f"[{part}]" if isinstance(part, int) else f".{part}"
    return rendered


def _entries(cast: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in ("protagonist", "antagonist"):
        value = cast.get(path)
        if isinstance(value, Mapping) and _text(value.get("name")):
            rows.append({"path": path, "name": _text(value.get("name")), "role": _text(value.get("role")) or path, "payload": value})
    for index, value in enumerate(cast.get("supporting_cast") or []):
        if isinstance(value, Mapping) and _text(value.get("name")):
            rows.append({"path": f"supporting_cast[{index}]", "name": _text(value.get("name")), "role": _text(value.get("role")) or "supporting", "payload": value})
    return rows


def _walk_source(value: Any, path: str, source: str, out: list[dict[str, str]]) -> None:
    if isinstance(value, str):
        if value.strip():
            out.append({"source": source, "path": path, "quote": value.strip()})
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _IGNORED_SOURCE_KEYS:
                continue
            _walk_source(child, f"{path}.{key}", source, out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_source(child, f"{path}[{index}]", source, out)


def _source_index(approved_sources: Mapping[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source in ("premise", "book_spec", "world_spec"):
        _walk_source(approved_sources.get(source), source, source, rows)
    return [{"source_id": f"S{index:04d}", **row} for index, row in enumerate(rows, 1)]


def _evidence(value: object, index: list[dict[str, str]]) -> list[str]:
    text = _text(value)
    if not text:
        return []
    return [row["source_id"] for row in index if text in row["quote"]]


def _span_path_allowed(path: list[object]) -> bool:
    """Allow only existing identity/mechanism prose fields for span edits."""

    if not path or any(part in {"_meta", "metadata", "role", "gender", "strength"} for part in path):
        return False
    if _is_scalar_identity_path(path):
        return False
    if path in [
        ["antagonist", "name_reasoning"],
        ["protagonist", "goal"],
        ["protagonist", "arc_trajectory"],
        ["protagonist", "obvious_flaw"],
        ["protagonist", "golden_finger"],
        ["protagonist", "differentiated_advantage"],
        ["protagonist", "sustainable_upgrade_point"],
        ["protagonist", "decision_policy", "pressure_response"],
        ["protagonist", "decision_policy", "forbidden_behaviors"],
        ["protagonist", "decision_policy", "high_risk_allowances"],
    ]:
        return True
    if len(path) == 5 and path[:3] == ["protagonist", "social_network", "enemies"]:
        return isinstance(path[3], int) and not isinstance(path[3], bool) and path[3] >= 0 and path[4] == "name"
    if len(path) == 3 and path[0] in {"conflict_map", "antagonist_forces"}:
        return isinstance(path[1], int) and not isinstance(path[1], bool) and path[1] >= 0 and path[2] in (
            {"character_a", "character_b", "trigger_condition"}
            if path[0] == "conflict_map"
            else {"escalation_path", "threat_description"}
        )
    return False


def _editable_span_fields(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expose exact base fields so an editor can echo hashes instead of inventing them."""

    paths: list[list[object]] = [
        ["antagonist", "name_reasoning"],
        ["protagonist", "goal"],
        ["protagonist", "arc_trajectory"],
        ["protagonist", "obvious_flaw"],
        ["protagonist", "golden_finger"],
        ["protagonist", "differentiated_advantage"],
        ["protagonist", "sustainable_upgrade_point"],
        ["protagonist", "decision_policy", "pressure_response"],
        ["protagonist", "decision_policy", "forbidden_behaviors"],
        ["protagonist", "decision_policy", "high_risk_allowances"],
    ]
    for index, enemy in enumerate((candidate.get("protagonist") or {}).get("social_network", {}).get("enemies", []) or []):
        if isinstance(enemy, Mapping):
            paths.append(["protagonist", "social_network", "enemies", index, "name"])
    for key, fields in (("conflict_map", ("character_a", "character_b", "trigger_condition")),
                        ("antagonist_forces", ("escalation_path", "threat_description"))):
        for index, row in enumerate(candidate.get(key, []) or []):
            if isinstance(row, Mapping):
                paths.extend([[key, index, field] for field in fields])

    result: list[dict[str, Any]] = []
    for path in paths:
        if not _span_path_allowed(path):
            continue
        node: Any = candidate
        try:
            for part in path:
                node = node[part]
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(node, str):
            result.append({"path": path, "value": node, "before_hash": source_content_hash(node)})
    return result


def _is_scalar_identity_path(path: list[object]) -> bool:
    """Return whether a path must use a complete scalar replacement."""

    if not path:
        return False
    if path[-1] in {"name", "canonical_name"} and path[0] in {
        "protagonist", "antagonist", "supporting_cast", "characters",
    }:
        return True
    return (
        len(path) == 3
        and path[0] == "conflict_map"
        and path[-1] in {"character_a", "character_b"}
    )


def _editable_scalar_fields(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expose existing identity references as complete-value edit targets."""

    paths: list[list[object]] = []
    for root in ("protagonist", "antagonist"):
        value = candidate.get(root)
        if isinstance(value, Mapping):
            paths.extend([[root, field] for field in ("name", "canonical_name") if isinstance(value.get(field), str)])
    for index, value in enumerate(candidate.get("supporting_cast") or []):
        if isinstance(value, Mapping):
            paths.extend([["supporting_cast", index, field] for field in ("name", "canonical_name") if isinstance(value.get(field), str)])
    protagonist = candidate.get("protagonist")
    if isinstance(protagonist, Mapping):
        network = protagonist.get("social_network")
        if isinstance(network, Mapping):
            for index, value in enumerate(network.get("enemies") or []):
                if isinstance(value, Mapping) and isinstance(value.get("name"), str):
                    paths.append(["protagonist", "social_network", "enemies", index, "name"])
    for index, value in enumerate(candidate.get("conflict_map") or []):
        if isinstance(value, Mapping):
            paths.extend([["conflict_map", index, field] for field in ("character_a", "character_b") if isinstance(value.get(field), str)])

    result: list[dict[str, Any]] = []
    for path in paths:
        node: Any = candidate
        try:
            for part in path:
                node = node[part]
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(node, str) and _is_scalar_identity_path(path):
            result.append({"path": path, "value": node, "before_hash": source_content_hash(node)})
    return result


def _patch_targets(
    candidate: Mapping[str, Any],
    historical_cast_evidence: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build one authoritative path/operation contract for the editor."""

    targets: list[dict[str, Any]] = []
    seen: set[tuple[object, ...]] = set()
    for item in _editable_scalar_fields(candidate):
        path = list(item["path"])
        seen.add(tuple(path))
        target = {**item, "allowed_operation": "scalar"}
        target.pop("before_hash", None)
        targets.append(target)
    for item in _editable_span_fields(candidate):
        path = list(item["path"])
        if _is_scalar_identity_path(path) or tuple(path) in seen:
            continue
        targets.append({**item, "allowed_operation": "replace_anchor"})
    slots = candidate.get("supporting_cast")
    for row in historical_cast_evidence or []:
        if not isinstance(row, Mapping) or row.get("source") != "historical_cast":
            continue
        if (
            row.get("artifact_status") != "approved"
            or row.get("value_kind") not in {"approved_null", "approved_missing"}
            or not _text(row.get("source_id"))
            or not _text(row.get("artifact_id"))
            or not _text(row.get("artifact_hash"))
        ):
            continue
        slot_path = row.get("slot_path")
        if not isinstance(slot_path, str) or not slot_path.startswith("supporting_cast[") or not slot_path.endswith("]"):
            continue
        try:
            slot_index = int(slot_path[len("supporting_cast["):-1])
        except ValueError:
            continue
        if not isinstance(slots, list) or slot_index < 0 or slot_index >= len(slots) or not isinstance(slots[slot_index], Mapping):
            continue
        if _text(slots[slot_index].get("name")) != _text(row.get("slot_name")):
            continue
        path = ["supporting_cast", slot_index, "power_tier"]
        if row.get("path") != f"{slot_path}.power_tier":
            continue
        target = {
            "path": path,
            "value": slots[slot_index].get("power_tier"),
            "value_kind": row["value_kind"],
            "source_ids": [row["source_id"]],
            "artifact_id": row["artifact_id"],
            "artifact_hash": row["artifact_hash"],
            "slot_name": row.get("slot_name"),
            "allowed_operation": "scalar",
        }
        if tuple(path) not in seen and not any(existing["path"] == path and existing.get("value_kind") == row["value_kind"] for existing in targets):
            targets.append(target)
    return targets


def _prompt_source_index(source_index: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project the frozen index to fields needed for model citation selection."""

    keys = ("source_id", "source", "path", "quote", "value_kind", "slot_path", "slot_name", "artifact_status")
    return [{key: row[key] for key in keys if key in row} for row in source_index]


def _resolve_replace_anchor(
    field: str,
    *,
    before: object,
    context_before: object = None,
    context_after: object = None,
) -> tuple[int, int]:
    """Resolve one unique text anchor without accepting model-supplied offsets."""

    if not isinstance(before, str) or not before:
        raise ValueError("CAST_SOURCE_EDITOR_ANCHOR_INVALID")
    if context_before is not None and (not isinstance(context_before, str) or not context_before):
        raise ValueError("CAST_SOURCE_EDITOR_ANCHOR_INVALID")
    if context_after is not None and (not isinstance(context_after, str) or not context_after):
        raise ValueError("CAST_SOURCE_EDITOR_ANCHOR_INVALID")
    matches: list[tuple[int, int]] = []
    cursor = 0
    while True:
        start = field.find(before, cursor)
        if start < 0:
            break
        end = start + len(before)
        if (
            (context_before is None or field[max(0, start - len(context_before)):start] == context_before)
            and (context_after is None or field[end:end + len(context_after)] == context_after)
        ):
            matches.append((start, end))
        cursor = start + 1
    if not matches:
        raise ValueError("CAST_SOURCE_EDITOR_ANCHOR_NOT_FOUND")
    if len(matches) != 1:
        raise ValueError("CAST_SOURCE_EDITOR_ANCHOR_AMBIGUOUS")
    return matches[0]


def parse_review_json(raw: object) -> Mapping[str, Any]:
    """Parse one JSON object, accepting only one outer markdown JSON fence.

    The raw response remains persisted unchanged in the receipt.  This parser is
    intentionally syntax-only: it does not relax hashes, provider checks, or
    source evidence validation.
    """

    if not isinstance(raw, str):
        raise ValueError("CAST_SOURCE_REVIEW_RAW_RESPONSE_INVALID")
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) < 3 or lines[0].strip().lower() not in {"```", "```json"} or lines[-1].strip() != "```":
            raise ValueError("CAST_SOURCE_REVIEW_RAW_RESPONSE_INVALID")
        text = "\n".join(lines[1:-1]).strip()
        if "```" in text:
            raise ValueError("CAST_SOURCE_REVIEW_RAW_RESPONSE_INVALID")
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("CAST_SOURCE_REVIEW_RAW_RESPONSE_INVALID") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("CAST_SOURCE_REVIEW_RAW_RESPONSE_INVALID")
    return payload


def build_historical_cast_evidence(
    current_cast: Any, historical_casts: Any  # noqa: ANN401
) -> list[dict[str, Any]]:
    """Rebuild typed evidence from DB-approved Cast versions before current.

    ``historical_casts`` must be DB rows (or equivalent test records) carrying
    id, version_no, status, and content.  The content hash is recomputed; a
    report cannot self-declare approval or source integrity.
    """

    current_id = _text(getattr(current_cast, "id", None) if not isinstance(current_cast, Mapping) else current_cast.get("id"))
    current_version = getattr(current_cast, "version_no", None) if not isinstance(current_cast, Mapping) else current_cast.get("version_no")
    if current_version is None:
        raise ValueError("CAST_SOURCE_HISTORICAL_CURRENT_VERSION_REQUIRED")
    rows: list[dict[str, Any]] = []
    for artifact in historical_casts or []:
        artifact_id = _text(getattr(artifact, "id", None) if not isinstance(artifact, Mapping) else artifact.get("id"))
        version_no = getattr(artifact, "version_no", None) if not isinstance(artifact, Mapping) else artifact.get("version_no")
        status = _text(getattr(artifact, "status", None) if not isinstance(artifact, Mapping) else artifact.get("status"))
        content = getattr(artifact, "content", None) if not isinstance(artifact, Mapping) else artifact.get("content")
        if not artifact_id or artifact_id == current_id or status != "approved" or not isinstance(version_no, int) or version_no >= int(current_version) or not isinstance(content, Mapping):
            continue
        content_hash = source_content_hash(dict(content))
        for entry_index, entry in enumerate(_entries(content)):
            payload = entry["payload"]
            base = {
                "source": "historical_cast",
                "artifact_id": artifact_id,
                "artifact_status": status,
                "artifact_version": version_no,
                "artifact_hash": content_hash,
                "json_path": entry["path"],
                "evidence_role": "approved_historical_cast",
                "slot_path": entry["path"],
                "slot_name": entry["name"],
            }
            row_prefix = f"H{version_no:04d}_{artifact_id}_{entry_index:02d}"
            rows.append({**base, "source_id": row_prefix + "_name", "path": entry["path"] + ".name", "quote": entry["name"], "value_kind": "string"})
            if isinstance(payload, Mapping) and (
                "power_tier" not in payload or payload.get("power_tier") is None
            ):
                rows.append({
                    **base,
                    "source_id": row_prefix + "_power_tier",
                    "path": entry["path"] + ".power_tier",
                    "quote": "",
                    "value": None,
                    "value_kind": "approved_null" if "power_tier" in payload else "approved_missing",
                })
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        unique[(str(row["source_id"]), str(row["path"]))] = row
    return list(unique.values())


def build_cast_recovery_candidate(
    compiled_cast: Mapping[str, Any],
    legacy_cast: Mapping[str, Any],
    *,
    approved_sources: Mapping[str, Any],
    parent_ids: Mapping[str, str],
    revision_report_id: str,
    legacy_artifact_id: str,
    legacy_artifact_status: str | None = None,
    legacy_artifact_version: int | None = None,
    historical_cast_evidence: list[Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    # The legacy approved cast is the observation baseline.  The compiler is
    # only a source of bounded identity hints; it is never a replacement cast.
    candidate = deepcopy(dict(legacy_cast))
    index = _source_index(approved_sources)
    approved_source_hashes = {
        key: source_content_hash(value) for key, value in approved_sources.items()
    }
    source_artifact_ids = {
        "premise": parent_ids.get("premise"),
        "book_spec": parent_ids.get("book_spec") or parent_ids.get("book"),
        "world_spec": parent_ids.get("world_spec") or parent_ids.get("world"),
    }
    for row in index:
        domain = str(row.get("source"))
        if domain in approved_source_hashes:
            row.update(
                {
                    "artifact_id": str(source_artifact_ids.get(domain) or ""),
                    "artifact_status": "approved",
                    "artifact_hash": approved_source_hashes[domain],
                    "evidence_role": "approved_fact",
                }
            )
    historical_rows = [dict(row) for row in (historical_cast_evidence or [])]
    index.extend(historical_rows)
    compiler_hint_rows: list[dict[str, str]] = []
    _walk_source(compiled_cast, "compiler_hint", "compiler_hint", compiler_hint_rows)
    compiler_hint_index = [
        {
            "source_id": f"C{index:04d}",
            **row,
            "evidence_role": "non_authoritative_compiler_hint",
        }
        for index, row in enumerate(compiler_hint_rows, 1)
    ]
    approved_evidence_index = [
        row for row in index if row.get("source") in _APPROVED_SOURCE_DOMAINS
    ]
    compiled_by_path = {row["path"]: row for row in _entries(compiled_cast)}
    approved_role_names: dict[str, str] = {}
    for source in approved_sources.values():
        for row in _entries(source if isinstance(source, Mapping) else {}):
            approved_role_names.setdefault(row["role"], row["name"])
    audit: dict[str, Any] = {
        "version": VERSION,
        "promotion_status": "needs_independent_source_review",
        "revision_report_id": revision_report_id,
        "legacy_cast_artifact_id": legacy_artifact_id,
        "legacy_cast_hash": source_content_hash(dict(legacy_cast)),
        "legacy_attestation": {
            "artifact_id": legacy_artifact_id,
            "artifact_type": "cast_spec",
            "artifact_hash": source_content_hash(dict(legacy_cast)),
            "artifact_status": legacy_artifact_status or "unknown",
            "current_fidelity": "unverified",
            "artifact_version": legacy_artifact_version,
            "evidence_role": "legacy_preservation",
        },
        "legacy_cast": deepcopy(dict(legacy_cast)),
        "approved_source_hashes": approved_source_hashes,
        "parent_artifact_ids": dict(parent_ids),
        "source_index": index,
        "compiler_hint_index": compiler_hint_index,
        "historical_cast_evidence": historical_rows,
        "entries": [],
    }
    for legacy in _entries(legacy_cast):
        name = legacy["name"]
        row: dict[str, Any] = {
            "legacy_path": legacy["path"],
            "name": name,
            "decision": "legacy_preserved_unverified",
            "candidate_fields": [],
            "evidence": [],
            "removed": False,
        }
        target_payload = candidate
        for part in legacy["path"].replace("]", "").split("["):
            target_payload = target_payload[int(part)] if part.isdigit() else target_payload[part]
        hint = compiled_by_path.get(legacy["path"])
        desired_name = approved_role_names.get(legacy["role"]) or (
            hint.get("name") if hint else ""
        )
        if desired_name and desired_name != name:
            ids = _evidence(desired_name, approved_evidence_index)
            if ids:
                target_payload["name"] = desired_name
                row["candidate_fields"].append("name")
                row["evidence"].extend(ids)
                row["decision"] = "identity_patch_requires_source_review"
            else:
                row["decision"] = "needs_review_identity_hint_unproven"
        audit["entries"].append(row)
    final_candidate_rows: list[dict[str, str]] = []
    _walk_source(candidate, "candidate", "candidate", final_candidate_rows)
    final_start = len(index) + 1
    index.extend(
        {"source_id": f"S{final_start + i:04d}", **row}
        for i, row in enumerate(final_candidate_rows)
    )
    candidate_paths = [row["path"] for row in final_candidate_rows]
    if len(candidate_paths) != len(set(candidate_paths)):
        raise ValueError("CAST_SOURCE_CANDIDATE_INDEX_DUPLICATE_PATH")
    candidate["_meta"] = {
        **(candidate.get("_meta") if isinstance(candidate.get("_meta"), Mapping) else {}),
        "derived_cast_source_review": VERSION,
        "revision_report_id": revision_report_id,
        "parent_artifact_ids": dict(parent_ids),
    }
    audit["candidate_cast_hash"] = source_content_hash(candidate)
    audit["review_input_hash"] = source_content_hash(
        {
            "review_version": VERSION,
            "candidate_hash": audit["candidate_cast_hash"],
            "legacy_cast_hash": audit["legacy_cast_hash"],
            "approved_source_hashes": audit["approved_source_hashes"],
            "compiler_hint_index": audit["compiler_hint_index"],
            "source_index": audit["source_index"],
            "historical_cast_evidence": audit["historical_cast_evidence"],
            "parent_artifact_ids": audit["parent_artifact_ids"],
            "revision_report_id": revision_report_id,
            "entries": audit["entries"],
        }
    )
    return candidate, audit


def verify_cast_source_review(
    receipt: Mapping[str, Any], *, candidate: Mapping[str, Any], audit: Mapping[str, Any]
) -> None:
    if receipt.get("version") != VERSION:
        raise ValueError("CAST_SOURCE_REVIEW_VERSION_INVALID")
    if receipt.get("candidate_hash") != source_content_hash(dict(candidate)):
        raise ValueError("CAST_SOURCE_REVIEW_CANDIDATE_STALE")
    if receipt.get("review_input_hash") != audit.get("review_input_hash"):
        raise ValueError("CAST_SOURCE_REVIEW_INPUT_TAMPERED")
    checks = receipt.get("checks")
    if not isinstance(checks, list) or len(checks) != len(AXES):
        raise ValueError("CAST_SOURCE_REVIEW_COVERAGE")
    if [check.get("axis") for check in checks if isinstance(check, Mapping)] != list(AXES):
        raise ValueError("CAST_SOURCE_REVIEW_COVERAGE")
    seen: set[str] = set()
    index = {row["source_id"]: row for row in audit.get("source_index", []) if isinstance(row, Mapping)}
    valid_ids = set(index)
    required_domains = {
        "identity_binding": {"premise", "book_spec", "candidate"},
        "source_fidelity": {"premise", "book_spec", "world_spec", "candidate"},
        "mechanism_and_cost": {"book_spec", "world_spec", "candidate"},
        "dependency_scope": {"premise", "world_spec", "candidate"},
    }
    for check in checks:
        if not isinstance(check, Mapping) or check.get("axis") not in AXES or check["axis"] in seen:
            raise ValueError("CAST_SOURCE_REVIEW_COVERAGE")
        ids = check.get("source_ids")
        if check.get("verdict") != "pass" or not isinstance(ids, list) or len(ids) < 2 or any(i not in valid_ids for i in ids):
            raise ValueError("CAST_SOURCE_REVIEW_NOT_PASSED")
        domains = {str(index[i].get("source")) for i in ids}
        if not required_domains[str(check["axis"])] <= domains:
            raise ValueError("CAST_SOURCE_REVIEW_SOURCE_COVERAGE")
        if not _text(check.get("reason")):
            raise ValueError("CAST_SOURCE_REVIEW_REASON_REQUIRED")
        if len(str(check["reason"])) > 160:
            raise ValueError("CAST_SOURCE_REVIEW_REASON_TOO_LONG")
        parsed = parse_review_json(receipt.get("raw_response"))
        if (
            not isinstance(parsed, Mapping)
            or parsed.get("checks") != checks
            or parsed.get("candidate_hash") != receipt.get("candidate_hash")
            or parsed.get("review_input_hash") != receipt.get("review_input_hash")
        ):
            raise ValueError("CAST_SOURCE_REVIEW_CHECKS_TAMPERED")
        seen.add(str(check["axis"]))
    if seen != set(AXES):
        raise ValueError("CAST_SOURCE_REVIEW_COVERAGE")


def verify_cast_source_review_bundle(
    receipts: object, *, candidate: Mapping[str, Any], audit: Mapping[str, Any]
) -> None:
    """Require two distinct, real-provider reviews before projection."""

    if not isinstance(receipts, list) or len(receipts) != 2:
        raise ValueError("CAST_SOURCE_REVIEW_TWO_RECEIPTS_REQUIRED")
    seen: set[str] = set()
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            raise ValueError("CAST_SOURCE_REVIEW_RECEIPT_INVALID")
        reviewer_id = _text(receipt.get("review_id"))
        if not reviewer_id or reviewer_id in seen:
            raise ValueError("CAST_SOURCE_REVIEW_RECEIPT_NOT_INDEPENDENT")
        from uuid import UUID
        try:
            UUID(_text(receipt.get("llm_run_id")))
        except (ValueError, TypeError):
            raise ValueError("CAST_SOURCE_REVIEW_PROVIDER_RECEIPT_REQUIRED")
        if not _text(receipt.get("provider")) or not _text(receipt.get("model")):
            raise ValueError("CAST_SOURCE_REVIEW_PROVIDER_RECEIPT_REQUIRED")
        if str(receipt.get("provider")).lower() in {"mock", "fallback", "placeholder"}:
            raise ValueError("CAST_SOURCE_REVIEW_PROVIDER_UNAVAILABLE")
        if receipt.get("fallback_used") is not False or receipt.get("finish_reason") not in {"stop", "end_turn", "completed"}:
            raise ValueError("CAST_SOURCE_REVIEW_PROVIDER_UNAVAILABLE")
        verify_cast_source_review(receipt, candidate=candidate, audit=audit)
        run_id = _text(receipt.get("llm_run_id"))
        if run_id in seen:
            raise ValueError("CAST_SOURCE_REVIEW_RECEIPT_NOT_INDEPENDENT")
        seen.add(run_id)


def _apply_editor_patches(
    candidate: Mapping[str, Any], patches: object, source_index: list[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(patches, list) or len(patches) > 24:
        raise ValueError("CAST_SOURCE_EDITOR_PATCHES_INVALID")
    valid_ids = {str(row.get("source_id")) for row in source_index if row.get("source") != "candidate"}
    rows_by_id = {str(row.get("source_id")): row for row in source_index}
    result = deepcopy(dict(candidate))
    changes_by_index: dict[int, dict[str, Any]] = {}
    scalar_paths: set[tuple[object, ...]] = set()
    span_groups: dict[tuple[object, ...], list[tuple[int, Mapping[str, Any], str]]] = {}

    def resolve(value: Mapping[str, Any], path: list[object]) -> tuple[Any, Any, str]:
        node: Any = value
        try:
            for part in path[:-1]:
                node = node[part]
            return node, node[path[-1]], _format_path(path)
        except (KeyError, IndexError, TypeError):
            raise ValueError("CAST_SOURCE_EDITOR_PATH_MISSING")

    def evidence_for(patch: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        ids = patch.get("source_ids")
        if not isinstance(ids, list) or len(ids) < 1 or any(str(item) not in valid_ids for item in ids):
            raise ValueError("CAST_SOURCE_EDITOR_SOURCE_EVIDENCE_REQUIRED")
        evidence_rows = [rows_by_id[str(item)] for item in ids]
        if any(
            str(row.get("source")) == "candidate"
            or row.get("artifact_status") not in {None, "approved"}
            for row in evidence_rows
        ):
            raise ValueError("CAST_SOURCE_EDITOR_SOURCE_EVIDENCE_REQUIRED")
        return evidence_rows

    def validate_common(patch: Mapping[str, Any]) -> tuple[list[object], list[Mapping[str, Any]]]:
        path = patch.get("path")
        if not isinstance(path, list) or not path:
            raise ValueError("CAST_SOURCE_EDITOR_PATH_INVALID")
        evidence_rows = evidence_for(patch)
        if not isinstance(patch.get("reason"), str) or not patch["reason"].strip():
            raise ValueError("CAST_SOURCE_EDITOR_REASON_REQUIRED")
        return path, evidence_rows

    for index, patch in enumerate(patches):
        if not isinstance(patch, Mapping):
            raise ValueError("CAST_SOURCE_EDITOR_PATCH_INVALID")
        if patch.get("op") == "replace_anchor":
            required = {"op", "path", "before_hash", "before", "after", "source_ids", "reason"}
            optional = {"context_before", "context_after", "semantic_role"}
            if not required <= set(patch) or set(patch) - required - optional:
                raise ValueError("CAST_SOURCE_EDITOR_ANCHOR_INVALID")
            path, evidence_rows = validate_common(patch)
            if any(row.get("artifact_status") != "approved" or row.get("source") not in _APPROVED_SOURCE_DOMAINS for row in evidence_rows):
                raise ValueError("CAST_SOURCE_EDITOR_SOURCE_EVIDENCE_REQUIRED")
            if not _span_path_allowed(path):
                raise ValueError("CAST_SOURCE_EDITOR_ANCHOR_PATH_INVALID")
            _, field, _ = resolve(candidate, path)
            if not isinstance(field, str):
                raise ValueError("CAST_SOURCE_EDITOR_ANCHOR_FIELD_INVALID")
            if not isinstance(patch["before_hash"], str) or source_content_hash(field) != patch["before_hash"]:
                raise ValueError("CAST_SOURCE_EDITOR_ANCHOR_HASH_MISMATCH")
            start, end = _resolve_replace_anchor(
                field,
                before=patch["before"],
                context_before=patch.get("context_before"),
                context_after=patch.get("context_after"),
            )
            if not isinstance(patch["after"], str) or not patch["after"]:
                raise ValueError("CAST_SOURCE_EDITOR_ANCHOR_INVALID")
            if not any(patch["after"] in str(row.get("quote", "")) for row in evidence_rows):
                raise ValueError("CAST_SOURCE_EDITOR_AFTER_UNEVIDENCED")
            key = tuple(path)
            if key in scalar_paths:
                raise ValueError("CAST_SOURCE_EDITOR_PATH_COLLISION")
            span_groups.setdefault(key, []).append((index, {**patch, "start": start, "end": end}, field))
            continue
        if patch.get("op") == "replace_span":
            required = {"op", "path", "before_hash", "start", "end", "before", "after", "source_ids", "reason"}
            if not required <= set(patch) or set(patch) - required - {"semantic_role"}:
                raise ValueError("CAST_SOURCE_EDITOR_SPAN_INVALID")
            path, evidence_rows = validate_common(patch)
            if any(row.get("artifact_status") != "approved" or row.get("source") not in _APPROVED_SOURCE_DOMAINS for row in evidence_rows):
                raise ValueError("CAST_SOURCE_EDITOR_SOURCE_EVIDENCE_REQUIRED")
            if not _span_path_allowed(path):
                raise ValueError("CAST_SOURCE_EDITOR_SPAN_PATH_INVALID")
            _, field, _ = resolve(candidate, path)
            if not isinstance(field, str):
                raise ValueError("CAST_SOURCE_EDITOR_SPAN_FIELD_INVALID")
            if not isinstance(patch["before_hash"], str) or source_content_hash(field) != patch["before_hash"]:
                raise ValueError("CAST_SOURCE_EDITOR_SPAN_HASH_MISMATCH")
            start, end = patch["start"], patch["end"]
            if (
                isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, int) or not isinstance(end, int)
                or start < 0 or end <= start or end > len(field)
                or not isinstance(patch["before"], str)
                or field[start:end] != patch["before"]
                or not isinstance(patch["after"], str) or not patch["after"]
            ):
                raise ValueError("CAST_SOURCE_EDITOR_SPAN_RANGE_INVALID")
            if not any(patch["after"] in str(row.get("quote", "")) for row in evidence_rows):
                raise ValueError("CAST_SOURCE_EDITOR_AFTER_UNEVIDENCED")
            key = tuple(path)
            if key in scalar_paths:
                raise ValueError("CAST_SOURCE_EDITOR_PATH_COLLISION")
            span_groups.setdefault(key, []).append((index, patch, field))
            continue
        required = {"path", "before", "after", "source_ids", "reason"}
        if not required <= set(patch) or set(patch) - required - {"value_kind"}:
            raise ValueError("CAST_SOURCE_EDITOR_PATCH_INVALID")
        path = patch["path"]
        if not isinstance(path, list) or not path or tuple(path) in scalar_paths or tuple(path) in span_groups:
            raise ValueError("CAST_SOURCE_EDITOR_PATH_INVALID")
        role_reference = path and path[0] == "conflict_map" and path[-1] in {"character_a", "character_b"}
        high_risk = path == ["protagonist", "decision_policy", "high_risk_allowances"]
        approved_null = patch.get("value_kind") in {"approved_null", "approved_missing"}
        if path[0] not in {"protagonist", "antagonist", "supporting_cast", "characters"} and not role_reference:
            raise ValueError("CAST_SOURCE_EDITOR_PATH_INVALID")
        if any(part in {"_meta", "metadata", "role", "gender", "strength"} for part in path):
            raise ValueError("CAST_SOURCE_EDITOR_PROTECTED_FIELD")
        if path[-1] == "power_tier" and not approved_null:
            raise ValueError("CAST_SOURCE_EDITOR_PROTECTED_FIELD")
        allowed = {"name", "canonical_name", "name_reasoning", "background", "goal", "obvious_flaw", "relationships", "voice_profile", "social_network", "fear", "secret", "decision_policy", "summary", "risk_tolerance"}
        if path[-1] not in allowed and not role_reference and not high_risk and not approved_null:
            raise ValueError("CAST_SOURCE_EDITOR_FIELD_OUT_OF_SCOPE")
        node, before, _ = resolve(result, path)
        if before != patch["before"] or not isinstance(before, (str, type(None))):
            raise ValueError("CAST_SOURCE_EDITOR_BEFORE_MISMATCH")
        if approved_null:
            if path[-1] != "power_tier" or patch["after"] is not None:
                raise ValueError("CAST_SOURCE_EDITOR_APPROVED_NULL_INVALID")
            if (
                len(path) != 3
                or path[0] != "supporting_cast"
                or not isinstance(path[1], int)
                or isinstance(path[1], bool)
                or path[1] < 0
            ):
                raise ValueError("CAST_SOURCE_EDITOR_APPROVED_NULL_INVALID")
        elif not isinstance(patch["after"], str):
            raise ValueError("CAST_SOURCE_EDITOR_BEFORE_MISMATCH")
        evidence_rows = evidence_for(patch)
        if approved_null:
            current_slot = node if isinstance(node, Mapping) else None
            if not any(
                row.get("value_kind") == patch.get("value_kind")
                and row.get("path") == _format_path(path)
                and row.get("slot_path") == _format_path(path[:-1])
                and isinstance(current_slot, Mapping)
                and _text(current_slot.get("name")) == _text(row.get("slot_name"))
                and str(row.get("source")) == "historical_cast"
                and row.get("artifact_status") == "approved"
                for row in evidence_rows
            ):
                raise ValueError("CAST_SOURCE_EDITOR_APPROVED_NULL_UNEVIDENCED")
        elif not patch["after"].strip() or not any(patch["after"] in str(row.get("quote", "")) for row in evidence_rows):
            raise ValueError("CAST_SOURCE_EDITOR_AFTER_UNEVIDENCED")
        if not isinstance(patch["reason"], str) or not patch["reason"].strip():
            raise ValueError("CAST_SOURCE_EDITOR_REASON_REQUIRED")
        node[path[-1]] = patch["after"]
        scalar_paths.add(tuple(path))
        changes_by_index[index] = {"path": path, "before": before, "after": patch["after"], "source_ids": patch["source_ids"], "reason": patch["reason"]}

    for key, group in span_groups.items():
        ordered = sorted(group, key=lambda item: (item[1]["start"], item[1]["end"]))
        previous_end = -1
        for _, patch, _ in ordered:
            if patch["start"] < previous_end:
                raise ValueError("CAST_SOURCE_EDITOR_SPAN_OVERLAP")
            previous_end = patch["end"]
        node, field, _ = resolve(result, list(key))
        for index, patch, _ in sorted(group, key=lambda item: item[1]["start"], reverse=True):
            field = field[:patch["start"]] + patch["after"] + field[patch["end"]:]
            change = {
                "op": patch.get("op", "replace_span"),
                "path": list(key),
                "before_hash": patch["before_hash"],
                "start": patch["start"],
                "end": patch["end"],
                "before": patch["before"],
                "after": patch["after"],
                "source_ids": patch["source_ids"],
                "reason": patch["reason"],
            }
            for context_key in ("context_before", "context_after"):
                if context_key in patch:
                    change[context_key] = patch[context_key]
            changes_by_index[index] = change
        node[key[-1]] = field
    return result, [changes_by_index[index] for index in range(len(patches))]


def _append_candidate_sources(audit: dict[str, Any], candidate: Mapping[str, Any]) -> None:
    approved = [row for row in audit.get("source_index", []) if row.get("source") != "candidate"]
    rows: list[dict[str, str]] = []
    _walk_source(candidate, "candidate", "candidate", rows)
    start = len(approved) + 1
    audit["source_index"] = approved + [
        {"source_id": f"S{start + i:04d}", **row} for i, row in enumerate(rows)
    ]


def replay_editor_candidate(
    base_candidate: Mapping[str, Any], editor_receipt: Mapping[str, Any], audit: Mapping[str, Any]
) -> dict[str, Any]:
    """Replay the persisted editor patch against the deterministic base."""
    from uuid import UUID
    try:
        UUID(_text(editor_receipt.get("llm_run_id")))
    except (ValueError, TypeError) as exc:
        raise ValueError("CAST_SOURCE_EDITOR_PROVIDER_RECEIPT_REQUIRED") from exc
    if not _text(editor_receipt.get("provider")) or str(editor_receipt.get("provider")).lower() in {"mock", "fallback", "placeholder"}:
        raise ValueError("CAST_SOURCE_EDITOR_PROVIDER_UNAVAILABLE")
    if not _text(editor_receipt.get("model")) or editor_receipt.get("fallback_used") is not False or editor_receipt.get("finish_reason") not in {"stop", "end_turn", "completed"}:
        raise ValueError("CAST_SOURCE_EDITOR_PROVIDER_UNAVAILABLE")
    payload = parse_review_json(editor_receipt.get("raw_response"))
    if payload.get("base_candidate_hash") != source_content_hash(dict(base_candidate)) or payload.get("review_input_hash") != audit.get("review_input_hash"):
        raise ValueError("CAST_SOURCE_EDITOR_BASE_STALE")
    patched, changes = _apply_editor_patches(base_candidate, payload.get("patches"), list(audit.get("source_index", [])))
    if editor_receipt.get("changes") != changes:
        raise ValueError("CAST_SOURCE_EDITOR_CHANGES_TAMPERED")
    return patched


def _post_editor_audit(
    base_audit: Mapping[str, Any], candidate: Mapping[str, Any], changes: list[dict[str, Any]]
) -> dict[str, Any]:
    audit = deepcopy(dict(base_audit))
    audit["entries"] = [*audit.get("entries", []), *deepcopy(changes)]
    _append_candidate_sources(audit, candidate)
    audit["candidate_cast_hash"] = source_content_hash(dict(candidate))
    audit["review_input_hash"] = source_content_hash({
        "review_version": VERSION,
        "candidate_hash": audit["candidate_cast_hash"],
        "legacy_cast_hash": audit["legacy_cast_hash"],
        "approved_source_hashes": audit["approved_source_hashes"],
        "compiler_hint_index": audit.get("compiler_hint_index", []),
        "source_index": audit.get("source_index", []),
        "historical_cast_evidence": audit.get("historical_cast_evidence", []),
        "parent_artifact_ids": audit["parent_artifact_ids"],
        "revision_report_id": audit["revision_report_id"],
        "entries": audit["entries"],
    })
    return audit


def verify_prepared_cast_source_review(
    payload: Mapping[str, Any],
    *,
    base_candidate: Mapping[str, Any],
    base_audit: Mapping[str, Any],
    historical_cast_evidence: list[Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild every reviewed input from fresh approved sources before use."""
    editor = payload.get("editor_receipt")
    if not isinstance(editor, Mapping):
        raise ValueError("CAST_SOURCE_EDITOR_RECEIPT_REQUIRED")
    if historical_cast_evidence is not None and base_audit.get("historical_cast_evidence") != [dict(row) for row in historical_cast_evidence]:
        raise ValueError("CAST_SOURCE_HISTORICAL_EVIDENCE_TAMPERED")
    candidate = replay_editor_candidate(base_candidate, editor, base_audit)
    audit = _post_editor_audit(base_audit, candidate, editor["changes"])
    if payload.get("candidate_cast") != candidate:
        raise ValueError("CAST_SOURCE_REVIEW_CANDIDATE_TAMPERED")
    if payload.get("audit") != audit:
        raise ValueError("CAST_SOURCE_REVIEW_AUDIT_TAMPERED")
    verify_cast_source_review_bundle(payload.get("reviews"), candidate=candidate, audit=audit)
    return candidate, audit


async def prepare_cast_source_review(
    session: Any,
    settings: Any,
    *,
    project_id: Any,
    candidate: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Run one bounded source-bound editor followed by two independent critics."""

    from bestseller.services.llm import LLMCompletionRequest, complete_text

    patch_targets = _patch_targets(candidate, audit.get("historical_cast_evidence", []))
    required_domains = {
        "identity_binding": ["premise", "book_spec", "candidate"],
        "source_fidelity": ["premise", "book_spec", "world_spec", "candidate"],
        "mechanism_and_cost": ["book_spec", "world_spec", "candidate"],
        "dependency_scope": ["premise", "world_spec", "candidate"],
    }
    critic_response_schema = {
        "review_input_hash": audit["review_input_hash"],
        "candidate_hash": audit["candidate_cast_hash"],
        "checks": [
            {"axis": axis, "verdict": "pass|revise|uncertain", "reason": "<聚合证据或阻断缺口，最多160字>", "source_ids": ["<real ids>"]}
            for axis in AXES
        ],
    }
    audit_hashes = {
        "legacy_cast_hash": audit["legacy_cast_hash"],
        "approved_source_hashes": audit["approved_source_hashes"],
        "compiler_hint_index_hash": source_content_hash(audit.get("compiler_hint_index", [])),
    }
    critic_rules = (
        "审查对象是current candidate；legacy_cast只是保留审计，不能把legacy中的旧值本身当作current错误。"
        "candidate只能证明待审字段的存在，不能证明任何approved事实；每轴仍必须引用所需approved domains和candidate观察对象。"
        "一句话seed没有某个名字，不自动禁止合理派生配角；只要角色功能与approved事实相容，应标为derived_unverified或legacy_preserved_unverified，不能仅凭缺seed判硬错。"
        "逐项区分hard_conflict、legacy_preserved_unverified、derived_unverified或deferred_hidden_thread；未在seed具名本身不是冲突证据，但未核验也不等于通过。identity collision或approved机制冲突必须要求修复。"
        "响应必须恰好包含四个checks，axis严格各出现一次且顺序为identity_binding、source_fidelity、mechanism_and_cost、dependency_scope；每轴把全部缺陷合并为一个reason，最多160字，禁止每个issue追加axis或重复axis。"
        "证据不足、冲突或越界时必须判revise/uncertain；新派生细节在两critic确认与approved事实或角色功能相容前不得成为canon；不得为完成任务强制pass。"
    )
    prompt = json.dumps(
        {
            "review_input_hash": audit["review_input_hash"],
            "candidate_hash": audit["candidate_cast_hash"],
            "candidate": candidate,
            "diff": audit.get("entries"),
            "source_index": _prompt_source_index(audit["source_index"]),
            "audit_hashes": audit_hashes,
            "axes": list(AXES),
            "required_domains": required_domains,
            "response_schema": critic_response_schema,
            "rules": critic_rules,
        }, ensure_ascii=False,
    )
    editor_prompt = json.dumps({
        "base_candidate_hash": audit["candidate_cast_hash"],
        "review_input_hash": audit["review_input_hash"],
        "candidate": candidate,
        "diff": audit.get("entries"),
        "source_index": _prompt_source_index([r for r in audit["source_index"] if r.get("source") != "candidate"]),
        "audit_hashes": audit_hashes,
        "patch_targets": patch_targets,
        "response_schema": {"base_candidate_hash": audit["candidate_cast_hash"], "review_input_hash": audit["review_input_hash"], "patches": [{"path": ["<target path>"], "before": "<exact current scalar>", "after": "<complete approved scalar>", "source_ids": ["<approved source id>"], "reason": "<evidence-bound reason>"}]},
        "scalar_patch_schema": {"path": ["<target path where allowed_operation=scalar>"], "before": "<exact complete current value>", "after": "<complete approved value>", "source_ids": ["<approved source id>"], "reason": "<evidence-bound reason>"},
        "anchor_patch_schema": {"op": "replace_anchor", "path": ["<target path where allowed_operation=replace_anchor>"], "before_hash": "<echo target before_hash>", "before": "<exact unique shortest token from target value>", "context_before": "<optional exact context>", "context_after": "<optional exact context>", "after": "<continuous approved quote substring>", "source_ids": ["<approved source id>"], "reason": "<evidence-bound reason>"},
        "typed_patch_schema": {"approved_null": {"path": ["supporting_cast", 0, "power_tier"], "before": "", "after": None, "value_kind": "approved_null", "source_ids": ["H0001_artifact_00"], "reason": "历史approved Cast明确为null"}, "approved_missing": {"path": ["supporting_cast", 0, "power_tier"], "before": "", "after": None, "value_kind": "approved_missing", "source_ids": ["H0001_artifact_00"], "reason": "历史approved Cast缺省该字段"}, "role_reference": {"path": ["conflict_map", 0, "character_b"], "before": "", "after": "", "source_ids": ["S0001"], "reason": "approved角色引用"}},
        "rules": "输出且仅输出一个JSON object，必须包含base_candidate_hash、review_input_hash、patches；即使patches=[]也必须原样回显两个hash；禁止额外自然语言。只修复已有身份碰撞及approved世界直接冲突，不重写角色，最多24个有限操作。新编辑必须优先使用replace_anchor；patch_targets中的path和allowed_operation只是选择指引，响应patch禁止携带allowed_operation字段，path必须逐字来自对应target。scalar目标只输出path、完整当前before、完整approved after、source_ids、reason，不输出op或before_hash；历史null/missing还必须输出target对应的value_kind且after=null；姓名、canonical_name、角色引用以及历史approved null/missing目标都走scalar。replace_anchor目标必须输出op=replace_anchor、path、target提供的完整字段before_hash、从target.value取出的最短唯一before token、可选context_before/context_after、approved连续after、source_ids、reason；before不是整字段，代码计算坐标，禁止start/end、全局替换、禁止自行计算或臆造hash。after必须来自approved非candidate source quote；无匹配或歧义必须拒绝。同字段多操作必须基于同一base、区间不重叠并从右向左执行。replace_span仅用于既有旧回执的严格重放，不能在新响应中输出。只能用于现有白名单路径，禁止metadata、创建槽位和整角色覆盖。历史null/missing target已经绑定当前同名slot、power_tier路径、approved历史source_id和value_kind，禁止自行选择其他slot或删除字段。candidate只是待审观察；一句话seed未具名本身不是冲突证据，但未核验也不等于通过。硬approved冲突必须修复；新派生细节须由两critic判断与approved事实或角色功能相容后才能成为canon。无可靠证据返回空patches但仍带双hash。",
    }, ensure_ascii=False)
    reviews: list[dict[str, Any]] = []
    editor_receipt: dict[str, Any] | None = None
    errors: list[str] = []
    calls = 0
    perspectives = ("editor", "正向核对身份与来源", "反证核对旧代价与越界依赖")
    working_candidate = dict(candidate)
    working_audit = dict(audit)
    for perspective in perspectives:
        calls += 1
        result = None
        try:
            current_prompt = editor_prompt if perspective == "editor" else prompt
            if perspective != "editor":
                current_prompt = json.dumps(
                    {**json.loads(prompt), "candidate": working_candidate,
                     "candidate_hash": working_audit["candidate_cast_hash"],
                     "review_input_hash": working_audit["review_input_hash"],
                     "diff": working_audit.get("entries"),
                     "source_index": _prompt_source_index(working_audit["source_index"]),
                     "response_schema": {"review_input_hash": working_audit["review_input_hash"], "candidate_hash": working_audit["candidate_cast_hash"], "checks": [{"axis": axis, "verdict": "pass|revise|uncertain", "reason": "<聚合证据或阻断缺口，最多160字>", "source_ids": ["<real ids>"]} for axis in AXES]},},
                    ensure_ascii=False,
                )
            async with asyncio.timeout(300):
                result = await complete_text(
                    session,
                    settings,
                    LLMCompletionRequest(
                        logical_role="editor" if perspective == "editor" else "critic",
                        system_prompt=("你是有界派生角色编辑。只输出一个JSON object，必须包含base_candidate_hash、review_input_hash、patches；patches为空也必须双hash；禁止额外文字。" if perspective == "editor" else "你是独立派生角色来源审查员，严格按给定JSON schema输出一个JSON object并原样回显两个hash；禁止额外文字。"),
                        user_prompt=perspective + "\n" + current_prompt,
                        fallback_response='{"checks":[]}',
                        prompt_template=f"{VERSION}_prepare",
                        prompt_version=VERSION,
                        project_id=project_id,
                        max_tokens_override=5000,
                        temperature_override=0.1,
                    ),
                )
            raw = result.content
            payload = parse_review_json(raw)
            if perspective == "editor":
                from uuid import UUID
                try:
                    UUID(_text(result.llm_run_id))
                except (ValueError, TypeError) as exc:
                    raise ValueError("CAST_SOURCE_EDITOR_PROVIDER_RECEIPT_REQUIRED") from exc
                if (not _text(result.provider) or not _text(result.model_name)
                        or str(result.provider).lower() in {"mock", "fallback", "placeholder"}
                        or result.fallback_used is not False
                        or result.finish_reason not in {"stop", "end_turn", "completed"}):
                    raise ValueError("CAST_SOURCE_EDITOR_PROVIDER_UNAVAILABLE")
                if payload.get("base_candidate_hash") != audit["candidate_cast_hash"] or payload.get("review_input_hash") != audit["review_input_hash"]:
                    raise ValueError("CAST_SOURCE_EDITOR_INPUT_HASH_MISMATCH")
                working_candidate, changes = _apply_editor_patches(
                    working_candidate, payload.get("patches"), working_audit["source_index"]
                )
                working_audit = _post_editor_audit(audit, working_candidate, changes)
                editor_receipt = {
                    "version": VERSION,
                    "review_id": str(uuid4()),
                    "llm_run_id": str(result.llm_run_id),
                    "provider": result.provider,
                    "model": result.model_name,
                    "fallback_used": result.fallback_used,
                    "finish_reason": result.finish_reason,
                    "raw_response": raw,
                    "base_candidate_hash": payload["base_candidate_hash"],
                    "review_input_hash": payload["review_input_hash"],
                    "changes": changes,
                }
                continue
            receipt = {
                "version": VERSION,
                "review_id": str(uuid4()),
                "llm_run_id": str(result.llm_run_id),
                "provider": result.provider,
                "model": result.model_name,
                "fallback_used": result.fallback_used,
                "finish_reason": result.finish_reason,
                "raw_response": raw,
                "candidate_hash": payload.get("candidate_hash"),
                "checks": payload.get("checks"),
                "review_input_hash": payload.get("review_input_hash"),
            }
            reviews.append(receipt)
        except Exception as exc:
            errors.append(type(exc).__name__ + ":" + str(exc))
            if result is not None:
                reviews.append({
                    "version": VERSION,
                    "review_id": str(uuid4()),
                    "llm_run_id": str(getattr(result, "llm_run_id", "")),
                    "provider": getattr(result, "provider", ""),
                    "model": getattr(result, "model_name", ""),
                    "fallback_used": getattr(result, "fallback_used", True),
                    "finish_reason": getattr(result, "finish_reason", "error"),
                    "raw_response": getattr(result, "content", ""),
                    "candidate_hash": None,
                    "checks": [],
                    "review_input_hash": None,
                    "error": errors[-1],
                })
    report = {
        "version": VERSION,
        "status": "needs_review",
        "phase": "prepare-cast-source-review",
        "budget": {"max_calls": 3, "calls": calls},
        "candidate_cast": working_candidate,
        "audit": working_audit,
        "editor_receipt": editor_receipt,
        "reviews": reviews,
    }
    if errors:
        report["errors"] = errors
        return report
    try:
        verify_prepared_cast_source_review(report, base_candidate=candidate, base_audit=audit)
    except ValueError as exc:
        report["errors"] = [str(exc)]
        return report
    report["status"] = "review_passed"
    return report
