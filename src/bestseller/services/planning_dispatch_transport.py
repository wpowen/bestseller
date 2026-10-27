"""Post-reuse dispatch encoding, never asset selection or paid-input equivalence.

The caller computes its logical cache identity and attempts reuse first. Only
the actual dispatched request uses this wire; its effective hash must include
every physical field. Existing generic/retry suffix bytes are never rewritten.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bestseller.services import source_bound_planning_content as content

# ruff: noqa: ANN401 -- strict JSON planning documents, no domain coercion.
POLICY = "planning-dispatch-transport.v1"


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("planning_dispatch_duplicate_json_key")
        result[key] = value
    return result


def _document(user: str) -> tuple[dict[str, Any], str]:
    def invalid_constant(value: str) -> None:
        raise ValueError("planning_dispatch_nonfinite_json")

    start = len(user) - len(user.lstrip())
    document, end = json.JSONDecoder(
        object_pairs_hook=_pairs, parse_constant=invalid_constant
    ).raw_decode(user, start)
    if type(document) is not dict:
        raise ValueError("planning_dispatch_document_object_required")
    _json(document)  # Reject scalar overflow such as 1e999, without coercion.
    suffix = user[end:]
    if suffix.lstrip().startswith(("{", "[")):
        raise ValueError("planning_dispatch_multiple_json_documents")
    return document, suffix


def prepare_planning_dispatch(
    system: str,
    user: str,
    *,
    tools: list[dict[str, Any]] | None,
    output_tokens: int,
) -> tuple[str, dict[str, Any]]:
    """Remeasure a full request; encode its complete JSON prefix only if needed.

    Fitting input bytes stay exact, including an already encoded builder root.
    Unknown/malformed prefixes fail closed. No source content, retry feedback,
    output allowance, kind selection or generation permission is manufactured.
    """
    if not isinstance(system, str) or not isinstance(user, str):
        raise ValueError("planning_dispatch_prompt_strings_required")
    capacity = None
    try:
        capacity = content.assert_source_bound_planning_request_capacity(
            system, user, tools=tools, output_tokens=output_tokens
        )
    except content.SourceBoundPlanningCapacityError:
        pass
    document, suffix = _document(user)
    logical = content.decode_source_bound_planning_document(document)
    kind = logical.get("kind")
    contract = logical.get("current_output_contract")
    if (
        logical.get("protocol") != content.SOURCE_BOUND_PLANNING_PROTOCOL
        or type(kind) is not str
        or kind not in {"book_spec", "world_spec", "cast_spec", "volume_plan"}
        or type(contract) is not dict
        or contract.get("kind") != kind
        or contract.get("expected_root") != ("array" if kind == "volume_plan" else "object")
        or type(contract.get("native_submission")) is not dict
        or contract["native_submission"].get("function_name") != "submit_" + kind
    ):
        raise ValueError("planning_dispatch_unknown_document")
    original = _json(logical)
    wire = user
    encoding = "identity"
    if capacity is None:
        prefix = content._overflow_planning_prompt(
            system,
            logical,
            capacity_tools=tools,
            capacity_output_tokens=output_tokens,
            capacity_user_suffix=suffix,
        )
        wire = prefix + suffix
        restored, restored_suffix = _document(wire)
        if (
            restored_suffix != suffix
            or _json(content.decode_source_bound_planning_document(restored)) != original
        ):
            raise ValueError("planning_dispatch_roundtrip_mismatch")
        capacity = content.assert_source_bound_planning_request_capacity(
            system, wire, tools=tools, output_tokens=output_tokens
        )
        if wire != user:
            encoding = restored["planning_document"]["encoding"]
            if encoding == "identity":
                encoding = "planning-overflow-identity"
    return wire, {
        "schema": POLICY,
        "encoding": encoding,
        "logical_document_sha256": _sha(original),
        "base_prompt_sha256": _sha(_json({"system": system, "user": user})),
        "base_user_sha256": _sha(user),
        "system_sha256": _sha(system),
        "wire_prompt_sha256": _sha(_json({"system": system, "user": wire})),
        "wire_user_sha256": _sha(wire),
        "suffix_sha256": _sha(suffix),
        "suffix_characters": len(suffix),
        "roundtrip_verified": True,
        "capacity": capacity,
    }
