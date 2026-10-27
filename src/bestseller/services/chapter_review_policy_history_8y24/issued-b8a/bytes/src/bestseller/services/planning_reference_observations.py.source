"""Bind untrusted native observations to exact current reference leaves.

This is not a grade parser. Physical custody, historical errors, source identity
and permission to propose or assess a changed candidate belong to the caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
from uuid import UUID

from bestseller.services import planning_admission_id_protocol as ids
from bestseller.services import planning_draft_admission as admission
from bestseller.services.planning_admission_submission import parse_planning_admission
from bestseller.services.retained_planning_clauses import resolve_pointer

SCHEMA = "planning-reference-observations.v1"


def _need(condition: object, code: str) -> None:
    if not condition:
        raise ValueError("planning_reference_observations:" + code)


def _scope_prefixes(candidate: dict) -> dict[str, str]:
    result = {}
    for index, chapter in enumerate(candidate["chapters"]):
        path = f"chapters[{index}]"
        pointer = f"/candidate_payload/chapters/{index}/"
        result[path] = pointer
        for scene_index, _scene in enumerate(chapter.get("scenes", [])):
            result[f"{path}.scenes[{scene_index}]"] = f"{pointer}scenes/{scene_index}/"
    return result


def _references(refs: object, contract: dict, supplied: dict) -> list[dict]:
    rows = ids._resolve_refs(refs, contract["catalog"], supplied)
    for row in rows:
        row["catalog_row_sha256"] = ids._sha(contract["catalog"][row["id"]])
        if row["role"] == "candidate_order":
            pointer = row["pointer"]
            _need(pointer.startswith("/candidate_payload/chapters/"), "candidate_reference_path")
            relative = pointer.removeprefix("/candidate_payload")
            value = resolve_pointer(supplied["candidate_payload"], relative)
            _need(
                type(value) in (str, int, float, bool) and ids._sha(value) == row["value_sha256"],
                "candidate_reference_value",
            )
            row["candidate_reference_path"] = relative
    return rows


def bind_native_reference_observations(completion: Mapping, supplied: dict) -> dict:
    """Decode native framing and reference bindings without evaluating a verdict.

    Quoted evidence, narrative truth and grade/projection-check relationships are
    deliberately not validated or repaired. Every original payload field remains
    unchanged; an invalid reference anywhere rejects the entire binding.
    """
    _need(isinstance(completion, Mapping), "completion_object")
    _need(completion.get("finish_reason") in {"stop", "tool_calls"}, "incomplete_completion")
    calls = completion.get("tool_calls")
    _need(type(calls) is list and len(calls) == 1, "single_native_submission_required")
    _need(type(supplied) is dict, "supplied_object")
    for field in ("project_id", "workflow_run_id"):
        value = supplied.get(field)
        _need(type(value) is str, "scope_identity")
        try:
            _need(str(UUID(value)) == value, "scope_identity")
        except (ValueError, AttributeError) as exc:
            raise ValueError("planning_reference_observations:scope_identity") from exc
    # The retained catalog binds the original whole body. Recomputing IDs from
    # an altered body without its stored witness would silently retarget them.
    _need(type(supplied.get("source_reference_catalog")) is dict, "stored_catalog_required")
    contract = ids.reference_contract(supplied)
    prefixes = _scope_prefixes(supplied["candidate_payload"])
    scopes = {target["path"] for target in supplied["projection_audit_targets"]}
    resolved = []
    paths = set()

    def bind(payload: dict) -> None:
        _need(type(payload) is dict, "native_payload_object")
        violations = payload.get("hard_violations")
        _need(type(violations) is list, "hard_violations")
        for index, violation in enumerate(violations):
            _need(type(violation) is dict, "violation_object")
            code, scope = violation.get("code"), violation.get("path")
            _need(type(code) is str and code in admission._SOURCE_ROLES_BY_CODE, "unknown_code")
            _need(type(scope) is str and scope in scopes and scope in prefixes, "violation_scope")
            sources = _references(violation.get("source_refs"), contract, supplied)
            evidence = _references(violation.get("evidence_refs"), contract, supplied)
            _need(
                all(
                    code in row["allowed_codes"]
                    and row["role"] in admission._SOURCE_ROLES_BY_CODE[code]
                    for row in sources
                ),
                "source_rights",
            )
            _need(all(row["role"] == "candidate_order" for row in evidence), "evidence_role")
            _need(
                any(row["pointer"].startswith(prefixes[scope]) for row in evidence),
                "evidence_scope",
            )
            for row in (*sources, *evidence):
                if "candidate_reference_path" in row:
                    paths.add(row["candidate_reference_path"])
            resolved.append(
                {
                    "violation_index": index,
                    "code": code,
                    "path": scope,
                    "source_refs": sources,
                    "evidence_refs": evidence,
                }
            )

    # The shared decoder enforces unique JSON, one exact tool, raw-message
    # conflicts and complete arguments. Its custom callback performs no grade
    # validation, literal-quote validation or native-text normalization.
    raw, decoded = parse_planning_admission(dict(completion), payload_validator=bind)
    payload = deepcopy(decoded["parsed_payload"])
    # One copy of the complete native object; the outer physical evidence
    # retains the original completion/argument bytes. Hash their exact bytes
    # here rather than duplicating the submission in the small reference map.
    framing = {
        key: deepcopy(value)
        for key, value in decoded.items()
        if key not in {"parsed_payload", "raw_arguments", "original_content"}
    }
    framing["original_content_sha256"] = hashlib.sha256(
        decoded["original_content"].encode("utf-8")
    ).hexdigest()
    return {
        "schema": SCHEMA,
        "project_id": supplied["project_id"],
        "workflow_run_id": supplied["workflow_run_id"],
        "native_payload": payload,
        "native_payload_sha256": ids._sha(payload),
        "raw_arguments_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "native_decode_receipt": framing,
        "candidate_reference_paths": sorted(paths),
        "resolved_references": resolved,
        "reference_binding": {
            key: deepcopy(value) for key, value in contract.items() if key != "catalog"
        },
        "supplied_sha256": ids._sha(supplied),
        "grade_asserted": False,
        "author_authority_granted": False,
        "canon_asserted": False,
        "old_grade_reinterpreted": False,
        "semantic_validation_performed": False,
        "issues_dropped": 0,
        "native_text_modified": False,
    }
