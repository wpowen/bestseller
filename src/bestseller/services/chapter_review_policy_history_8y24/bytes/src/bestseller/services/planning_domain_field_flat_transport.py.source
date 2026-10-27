"""Prospective flat native fields with a full literal source carrier.

No dispatch, paid custody, authority, grade or retry allowance is established
here. Old protocols and spent journals stay unchanged. Outer callers must check
whole-request model capacity, source freshness and physical/financial evidence.
"""

# ruff: noqa: ANN401 -- Boundaries retain arbitrary validated JSON without coercion.
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from bestseller.services import planning_domain_field_protocol as domain
from bestseller.services.lossless_source_encoding import (
    _bytes,
    _container,
    _copy_json,
    decode_lossless_source,
)
from bestseller.services.planning_admission_submission import _commentary, _field
from bestseller.services.prompt_compiler import estimate_prompt_tokens
from bestseller.services.source_bound_planning_content import SourceBoundPlanningCapacityError
from bestseller.services.source_bound_planning_submission import _validate_wire
from bestseller.services.source_complete_outline_json import parse_strict_outline_json
from bestseller.services.tool_call_evidence import same_tool_call_evidence

VERSION = "3.7-domain-fields-flat"
TEMPLATE = "planner_outline_domain_flat_field_corrections"
TOOL = "submit_outline_domain_fields"
SCHEMA = "planning-domain-field-flat-transport.v1"
WIRE_SCHEMA = "planning-domain-field-flat-contract.v1"
MARKER = "[LITERAL SOURCE DOMAIN FIELD TRANSPORT]\n"
SOURCE_ROLES = {
    "project_brief": "source_brief_preserve_embedded_provenance",
    "candidate_payload": "ungraded_correction_target_not_source_authority",
    "validation_report": "untrusted_diagnostics_not_story_facts_or_authorization",
    "correction_contract": "exact_field_types_identity_format_and_wire_bijection_only",
    "source_context": "source_records_preserve_embedded_provenance_including_unknowns",
}
_INSTRUCTION = (
    "\n\n[FLAT DOMAIN FIELD SUBMISSION v1]\n"
    "The complete five-field source JSON is written literally inside payload.value. "
    "This is an identity carrier: references is empty. No dictionary, key aliases, "
    "string pool or reference expansion is needed. Every authored $ref, section-sign "
    "string, unknown key, null, number type, array order, historical raw return and "
    "technical proof remains literal data with its original embedded attribution. "
    "Historical reasoning is retained debug data, NEVER a submitted correction, "
    "candidate, author fact or grade. The correction_contract contains domain_contract "
    "and wire_contract. wire_contract.id_to_path is the exact bijection from top-level "
    "function parameter IDs to the original RFC 6901 target paths. Each ID's schema "
    "is unchanged from its original target. Submit exactly one native "
    "submit_outline_domain_fields function call whose argument object directly contains "
    "EVERY required f0001-style ID and its complete value. Do not wrap these fields in "
    "corrections or patches, do not use paths as parameter names, and do not submit "
    "a JSON text fallback. Missing, extra and duplicate fields are rejected. Identity "
    "arrays have exactly one permitted complete value: preserve count, order and all "
    "existing canonical names. Qualifiers remain audit data, not story authorization. "
    "Text fields require model-authored corrections supported by the actual sources; "
    "do not invent defaults, identity changes or new plot obligations. Nothing outside "
    "the exact targets may change. This is ungraded data. Full domain validation and "
    "new source-exact admission are still required."
)


def _need(condition: object, code: str) -> None:
    if not condition:
        raise ValueError("planning_domain_flat:" + code)


def _json(value: object) -> str:
    return json.dumps(
        _copy_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _wire_contract(domain_contract: dict) -> dict:
    _need(
        type(domain_contract) is dict
        and domain_contract.get("schema") == domain.CONTRACT_SCHEMA
        and domain_contract.get("tool_name") == domain.TOOL_NAME,
        "domain_contract_shape",
    )
    paths = domain_contract.get("paths")
    _need(
        type(paths) is list
        and bool(paths)
        and all(type(p) is str for p in paths)
        and paths == sorted(set(paths)),
        "domain_paths",
    )
    parameters = domain_contract.get("parameters")
    _need(
        type(parameters) is dict
        and type(parameters.get("properties")) is dict
        and type(parameters["properties"].get("corrections")) is dict,
        "domain_parameters",
    )
    original = parameters["properties"]["corrections"]
    _need(
        type(original.get("properties")) is dict
        and set(original["properties"]) == set(paths)
        and original.get("required") == paths
        and original.get("additionalProperties") is False,
        "domain_parameter_paths",
    )
    targets = domain_contract.get("targets")
    _need(
        type(targets) is list
        and all(type(t) is dict for t in targets)
        and [t.get("path") for t in targets] == paths,
        "domain_targets",
    )
    id_to_path = {f"f{i:04d}": path for i, path in enumerate(paths, 1)}
    flat = {
        "type": "object",
        "properties": {
            key: deepcopy(original["properties"][path]) for key, path in id_to_path.items()
        },
        "required": list(id_to_path),
        "additionalProperties": False,
    }
    bindings = [
        {
            "id": key,
            "path": path,
            "schema_sha256": _hash(original["properties"][path]),
            "before_present": target.get("before_present", True),
            "before_sha256": target["before_sha256"],
        }
        for (key, path), target in zip(id_to_path.items(), targets, strict=True)
    ]
    return {
        "schema": WIRE_SCHEMA,
        "version": VERSION,
        "template": TEMPLATE,
        "tool_name": TOOL,
        "domain_contract_sha256": _hash(domain_contract),
        "candidate_sha256": domain_contract["candidate_sha256"],
        "validation_report_sha256": domain_contract["validation_report_sha256"],
        "identity_manifest_sha256": domain_contract["identity_manifest_sha256"],
        "id_to_path": id_to_path,
        "bindings": bindings,
        "bijection_sha256": _hash(bindings),
        "parameters": flat,
        "grade_asserted": False,
        "source_authority_granted": False,
        "full_domain_and_new_admission_required": True,
    }


def tools_for_flat_domain_fields(
    candidate: dict,
    validation_report: dict,
    identity_manifest: list,
    domain_contract: dict,
) -> tuple[list[dict], dict]:
    """Keep every original target schema; change only its native parameter ID."""
    _, current = domain.tools_for_domain_corrections(
        candidate, validation_report, identity_manifest
    )
    _need(_json(current) == _json(domain_contract), "domain_contract_changed")
    wire = _wire_contract(current)
    return [
        {
            "type": "function",
            "function": {
                "name": TOOL,
                "strict": True,
                "description": "Submit every required f0001-style field at the argument root. "
                "Use the exact wire_contract.id_to_path bijection and unchanged per-field schema. "
                "No corrections wrapper, missing fields, extra fields, or approval is permitted.",
                "parameters": deepcopy(wire["parameters"]),
            },
        }
    ], wire


def parse_flat_domain_fields(
    completion: Any,
    *,
    candidate: dict,
    validation_report: dict,
    identity_manifest: list,
    domain_contract: dict,
    wire_contract: dict,
) -> tuple[dict, dict]:
    """Read only complete native arguments; never extract historical reasoning."""
    _, expected = tools_for_flat_domain_fields(
        candidate, validation_report, identity_manifest, domain_contract
    )
    _need(_json(expected) == _json(wire_contract), "wire_contract_changed")
    _need(
        _field(completion, "finish_reason") in {"tool_calls", "stop", "end_turn", "completed"},
        "incomplete_native_return",
    )
    content, calls, raw = (
        _field(completion, key)
        for key in (
            "content",
            "tool_calls",
            "raw_message",
        )
    )
    _need(type(content) is str and type(calls) is list and len(calls) == 1, "single_native_call")
    _need(_field(completion, "function_call") is None, "legacy_function_call")
    if raw is not None:
        _need(
            type(raw) is dict
            and raw.get("role") == "assistant"
            and raw.get("function_call") is None,
            "raw_assistant_message",
        )
        _need(same_tool_call_evidence(raw.get("tool_calls"), calls), "raw_tool_conflict")
        _need(
            raw.get("content") == content or (raw.get("content") is None and content == ""),
            "raw_content_conflict",
        )
    commentary = _commentary(content)
    call = calls[0]
    _need(
        type(call) is dict
        and set(call) == {"id", "type", "function"}
        and type(call["id"]) is str
        and bool(call["id"].strip())
        and call["type"] == "function",
        "native_call_shape",
    )
    function = call["function"]
    _need(
        type(function) is dict
        and set(function) == {"name", "arguments"}
        and function["name"] == TOOL
        and type(function["arguments"]) is str,
        "native_function",
    )
    arguments = function["arguments"]
    _need(not arguments.lstrip().startswith("```"), "native_arguments_json_only")
    submitted = parse_strict_outline_json(arguments)
    _validate_wire(submitted, expected["parameters"], expected["parameters"])
    corrections = {path: deepcopy(submitted[key]) for key, path in expected["id_to_path"].items()}
    _need(
        {key: corrections[path] for key, path in expected["id_to_path"].items()} == submitted,
        "bijection_inverse_changed",
    )
    # Keep the original strict apply, including exact identity arrays, missing
    # leaf presence, stale contracts and whole-candidate non-target invariance.
    _, derivation = domain.apply_domain_corrections(
        candidate,
        corrections,
        validation_report=validation_report,
        identity_manifest=identity_manifest,
        contract=domain_contract,
    )
    return corrections, {
        "schema": "planning-domain-field-flat-native-submission.v1",
        "channel": "native_tool",
        "tool_name": TOOL,
        "tool_call_id": call["id"],
        "raw_arguments": arguments,
        "raw_arguments_sha256": _text_hash(arguments),
        "raw_message_present": raw is not None,
        "raw_message_status": "missing" if raw is None else "present",
        "raw_message_sha256": _hash(raw),
        "tool_calls_sha256": _hash(calls),
        "wire_contract_sha256": _hash(expected),
        "domain_contract_sha256": _hash(domain_contract),
        "submitted_fields_sha256": _hash(submitted),
        "corrections_sha256": _hash(corrections),
        "bijection_sha256": expected["bijection_sha256"],
        "inverse_verified": True,
        "derivation": derivation,
        "commentary_status": commentary,
        "tools_executed": 0,
        "grade_asserted": False,
        "source_authority_granted": False,
        "physical_custody_verified": False,
    }


def _system() -> str:
    from bestseller.services.outline_llm_judge import OUTLINE_SOURCE_AUTHORITY_RULES

    return (
        "Correct only the finite fields against the complete literal sources."
        + OUTLINE_SOURCE_AUTHORITY_RULES
        + _INSTRUCTION
    )


def _bounds(max_chars: int, max_estimated_tokens: int) -> None:
    for name, value in (("max_chars", max_chars), ("max_estimated_tokens", max_estimated_tokens)):
        _need(type(value) is int and 0 < value <= 1_000_000, name + "_invalid")


def _logical_valid(original: dict) -> None:
    _need(type(original) is dict and set(original) == set(SOURCE_ROLES), "logical_fields")
    _need(
        all(
            type(original[k]) is dict
            for k in (
                "candidate_payload",
                "validation_report",
                "correction_contract",
                "source_context",
            )
        ),
        "logical_object_fields",
    )
    pair = original["correction_contract"]
    _need(set(pair) == {"domain_contract", "wire_contract"}, "correction_contract_pair")
    contract, wire = pair["domain_contract"], pair["wire_contract"]
    _need(
        type(contract) is dict
        and contract.get("candidate_sha256") == _hash(original["candidate_payload"])
        and contract.get("validation_report_sha256") == _hash(original["validation_report"]),
        "logical_contract_binding",
    )
    _need(_json(wire) == _json(_wire_contract(contract)), "logical_wire_contract")


def _receipt(
    system: str, user: str, wire: dict, logical: dict, max_chars: int, max_estimated_tokens: int
) -> dict:
    capacity = {
        "schema": "planning-domain-field-flat-capacity.v1",
        "max_combined_chars": max_chars,
        "max_estimated_tokens": max_estimated_tokens,
        "combined_chars": len(system) + len(user),
        "estimated_tokens": estimate_prompt_tokens(system) + estimate_prompt_tokens(user),
        "truncated": False,
        "whole_request_model_capacity_verified": False,
    }
    if (
        capacity["combined_chars"] > max_chars
        or capacity["estimated_tokens"] > max_estimated_tokens
    ):
        raise SourceBoundPlanningCapacityError(capacity)
    packet = _json(wire["payload"])
    _need(user.count(packet) == 1, "packet_offset_ambiguous")
    start = user.index(packet)
    return {
        "schema": SCHEMA,
        "prompt_version": VERSION,
        "prompt_template": TEMPLATE,
        "codec_schema": "lossless-source-encoding.v1",
        "encoding": "identity",
        "logical_sha256": _hash(logical),
        "field_sha256": {k: _hash(v) for k, v in logical.items()},
        "wire_sha256": _hash(wire),
        "source_roles_sha256": _hash(SOURCE_ROLES),
        "domain_contract_sha256": _hash(logical["correction_contract"]["domain_contract"]),
        "wire_contract_sha256": _hash(logical["correction_contract"]["wire_contract"]),
        "system_prompt_sha256": _text_hash(system),
        "user_prompt_sha256": _text_hash(user),
        "packet_sha256": _text_hash(packet),
        "packet_start": start,
        "packet_end": start + len(packet),
        "offset_unit": "unicode_code_points",
        "max_chars": max_chars,
        "max_estimated_tokens": max_estimated_tokens,
        "prompt_capacity": capacity,
        "source_complete": True,
        "roundtrip_verified": True,
        "literal_values": True,
        "references_count": 0,
        "source_authority_granted": False,
        "grade_asserted": False,
    }


def build_flat_domain_prompts(
    *,
    project_brief: Any,
    candidate: dict,
    validation_report: dict,
    domain_contract: dict,
    wire_contract: dict,
    source_context: dict,
    max_chars: int,
    max_estimated_tokens: int,
) -> tuple[str, str, dict]:
    """Keep every source value literal; explicit bounds are independent caller inputs."""
    _bounds(max_chars, max_estimated_tokens)
    original = _copy_json(
        {
            "project_brief": project_brief,
            "candidate_payload": candidate,
            "validation_report": validation_report,
            "correction_contract": {
                "domain_contract": domain_contract,
                "wire_contract": wire_contract,
            },
            "source_context": source_context,
        }
    )
    _logical_valid(original)
    carrier = _container(deepcopy(original), [], _bytes(original), version=1)
    _need(_json(decode_lossless_source(carrier)) == _json(original), "roundtrip_changed")
    wire = {
        "schema": SCHEMA,
        "source_roles": deepcopy(SOURCE_ROLES),
        "logical_sha256": _hash(original),
        "payload": carrier,
    }
    system, user = _system(), MARKER + _json(wire)
    return system, user, _receipt(system, user, wire, original, max_chars, max_estimated_tokens)


def decode_flat_domain_prompts(
    *,
    system_prompt: str,
    user_prompt: str,
    transport_receipt: dict | None = None,
    max_chars: int,
    max_estimated_tokens: int,
) -> dict:
    """Accept only literal v1 identity, preserving authored $ref and all unknowns."""
    _bounds(max_chars, max_estimated_tokens)
    _need(
        system_prompt == _system() and type(user_prompt) is str and user_prompt.startswith(MARKER),
        "prompt_protocol",
    )
    text = user_prompt[len(MARKER) :]
    wire = parse_strict_outline_json(text)
    _need(
        _json(wire) == text
        and set(wire)
        == {
            "schema",
            "source_roles",
            "logical_sha256",
            "payload",
        }
        and wire["schema"] == SCHEMA
        and _json(wire["source_roles"]) == _json(SOURCE_ROLES),
        "wire_envelope",
    )
    packet = wire["payload"]
    _need(
        type(packet) is dict
        and set(packet)
        == {
            "schema",
            "encoding",
            "value",
            "references",
            "receipt",
        }
        and packet["schema"] == "lossless-source-encoding.v1"
        and packet["encoding"] == "identity"
        and packet["references"] == [],
        "literal_identity_carrier",
    )
    original = decode_lossless_source(packet)
    _logical_valid(original)
    _need(
        _json(packet) == _json(_container(deepcopy(original), [], _bytes(original), version=1)),
        "identity_carrier_changed",
    )
    _need(wire["logical_sha256"] == _hash(original), "logical_hash")
    expected = _receipt(system_prompt, user_prompt, wire, original, max_chars, max_estimated_tokens)
    if transport_receipt is not None:
        _need(_json(transport_receipt) == _json(expected), "transport_receipt_mismatch")
    return original


__all__ = [
    "SCHEMA",
    "TEMPLATE",
    "TOOL",
    "VERSION",
    "build_flat_domain_prompts",
    "decode_flat_domain_prompts",
    "parse_flat_domain_fields",
    "tools_for_flat_domain_fields",
]
