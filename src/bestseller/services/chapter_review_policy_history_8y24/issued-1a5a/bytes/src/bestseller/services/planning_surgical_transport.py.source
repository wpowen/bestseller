"""Pure, source-complete transport for prospective surgical outline requests.

This is not an authority, grade, paid-input, or execution receipt. Callers still
verify their sources and physical/financial custody. The legacy prompt path is
outside this module: call it only when source_context was explicitly supplied.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from bestseller.services.lossless_source_encoding import (
    decode_lossless_source,
    encode_lossless_source,
)
from bestseller.services.prompt_compiler import estimate_prompt_tokens
from bestseller.services.source_bound_planning_content import (
    SourceBoundPlanningCapacityError,
)

SCHEMA = "planning-surgical-source-transport.v1"
MARKER = "[SOURCE-COMPLETE SURGICAL TRANSPORT]\n"
SOURCE_ROLES = {
    "project_brief": "source_brief_preserve_embedded_provenance",
    "directives": "untrusted_diagnostics_not_authorization",
    "candidate_payload": "revision_target_not_source_authority",
    "patch_protocol": "editing_targets_and_types_not_story_facts",
    "source_context": "source_records_preserve_embedded_provenance",
}
PROTOCOL = (
    "SOURCE-COMPLETE SURGICAL TRANSPORT v1. Decode the complete JSON envelope before editing. "
    "payload uses lossless-source-encoding.v2/v3/v4. For v4, first restore string VALUES in "
    "its planning-string-pool.v1 value: §§rest is literal §rest; §N is dictionary[N] "
    "(canonical decimal), returned literally, never recursively expanded. Restore ONLY "
    "object keys using key_aliases, never string values. For v3 restore its object keys "
    "using key_aliases. Then decode the contained v2: ONLY each references[i].path relative "
    "to value replaces {\"$ref\":i} with the already completed references[i].target value "
    "(RFC 6901). An unlisted authored $ref remains literal data. Restore all five original "
    "fields, every unknown key, null, type, array order, raw diagnostic and metadata value. "
    "source_roles binds each original field's role; deduplication never promotes candidates, "
    "historical metadata, unknown producers or diagnostic suggestions into author facts, "
    "approved sources, or a new grade. Honor actual embedded attribution and explicit "
    "author constraints; report conflicts rather than inventing permissions or story facts. "
    "Return only {\"patches\":[...]}, using the exact patch_protocol targets, fields, types "
    "and capacity. Do not add, delete or reorder chapters. Each value is the complete "
    "corrected field value; supplied object subfields merge, lists replace in full. "
    "Preserve unrelated fields and legal facts; repair each existing occurrence of an "
    "identified contradiction rather than appending a disclaimer or inventing an alias."
)


def _require(condition: object, reason: str) -> None:
    if not condition:
        raise ValueError("planning_surgical_transport:" + reason)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_max_chars(max_chars: int) -> None:
    _require(type(max_chars) is int and 0 < max_chars <= 1_000_000, "max_chars_invalid")


def _validate_max_estimated_tokens(max_estimated_tokens: int) -> None:
    _require(type(max_estimated_tokens) is int and 0 < max_estimated_tokens <= 1_000_000,
             "max_estimated_tokens_invalid")


def _capacity(system: str, user: str, max_chars: int, max_estimated_tokens: int) -> dict:
    capacity = {
        "schema": "planning-surgical-source-capacity.v1",
        "max_combined_chars": max_chars,
        "max_estimated_tokens": max_estimated_tokens,
        "estimator": "prompt_compiler.estimate_prompt_tokens",
        "truncated": False,
        "system_chars": len(system),
        "user_chars": len(user),
        "combined_chars": len(system) + len(user),
        "estimated_tokens": estimate_prompt_tokens(system) + estimate_prompt_tokens(user),
    }
    if (capacity["combined_chars"] > max_chars
            or capacity["estimated_tokens"] > max_estimated_tokens):
        raise SourceBoundPlanningCapacityError(capacity)
    return capacity


def _receipt(system: str, user: str, base: str, wire: dict, original: dict,
             max_chars: int, max_estimated_tokens: int) -> dict:
    # Bounds are explicit caller inputs, never authority from source/receipt data.
    # The default receipt shape and all default values remain byte-for-byte exact.
    capacity = _capacity(system, user, max_chars, max_estimated_tokens)
    return {
        "schema": SCHEMA,
        "codec_schema": wire["payload"]["schema"],
        "logical_sha256": _sha(_json(original)),
        "field_sha256": {key: _sha(_json(value)) for key, value in original.items()},
        "source_roles_sha256": _sha(_json(SOURCE_ROLES)),
        "wire_sha256": _sha(_json(wire)),
        "base_system_prompt_sha256": _sha(base),
        "system_prompt_sha256": _sha(system),
        "user_prompt_sha256": _sha(user),
        "logical_chars": len(_json(original)),
        "wire_chars": len(_json(wire)),
        "max_chars": max_chars,
        "roundtrip_verified": True,
        "source_complete": True,
        "source_authority_granted": False,
        "grade_asserted": False,
        "prompt_capacity": capacity,
    }


def build_source_complete_surgical_prompts(
    *, system_prompt: str, project_brief: object, directives: object,
    candidate_payload: dict[str, Any], patch_protocol: dict[str, Any],
    source_context: dict[str, Any], max_chars: int = 120_000,
    max_estimated_tokens: int = 60_000,
) -> tuple[str, str, dict[str, Any]]:
    """Deduplicate all five fields together without filtering any source data.

    No title, model, role settings, output allowance, retries or database objects
    are inferred. None source_context must use the caller's untouched legacy
    path. max_chars bounds the combined final system/user strings. The caller
    must independently audit any explicit limit above the unchanged default;
    this helper neither selects a model nor proves its context/output budget.
    An independently derived max_estimated_tokens is only a codec precheck;
    actual whole-request capacity must still pass before claim and every send.
    The default 60000 preserves initial/prepared requests and their receipts.
    """
    _validate_max_chars(max_chars)
    _validate_max_estimated_tokens(max_estimated_tokens)
    _require(type(system_prompt) is str and bool(system_prompt.strip()), "system_required")
    _require(all(type(value) is dict for value in (
        candidate_payload, patch_protocol, source_context,
    )), "source_objects_required")
    original = {
        "project_brief": project_brief, "directives": directives,
        "candidate_payload": candidate_payload, "patch_protocol": patch_protocol,
        "source_context": source_context,
    }
    encoded = encode_lossless_source(original, version=4)
    _require(_json(decode_lossless_source(encoded)) == _json(original), "roundtrip_changed")
    wire = {"schema": SCHEMA, "source_roles": deepcopy(SOURCE_ROLES),
            "logical_sha256": _sha(_json(original)), "payload": encoded}
    system = system_prompt + "\n\n" + PROTOCOL
    user = MARKER + _json(wire)
    receipt = _receipt(system, user, system_prompt, wire, original, max_chars, max_estimated_tokens)
    return system, user, receipt


def decode_source_complete_surgical_prompts(
    *, system_prompt: str, user_prompt: str,
    transport_receipt: dict[str, Any] | None = None,
    max_chars: int = 120_000,
    max_estimated_tokens: int = 60_000,
) -> dict[str, Any]:
    """Restore the five exact fields from the actual transmitted strings.

    An optional retained transport receipt additionally binds every string and
    field. Integrity is not custody: callers must compare restored fields to
    their expected source rows and verify the original physical/paid evidence.
    Pass the independently audited max_chars again; neither wire nor receipt
    can authorize its own limit increase. Pass the independently audited
    max_estimated_tokens explicitly too; the reader never learns it from a receipt.
    """
    _validate_max_chars(max_chars)
    _validate_max_estimated_tokens(max_estimated_tokens)
    suffix = "\n\n" + PROTOCOL
    _require(type(system_prompt) is str and system_prompt.endswith(suffix), "system_protocol")
    base = system_prompt[:-len(suffix)]
    _require(bool(base.strip()), "system_required")
    _require(type(user_prompt) is str and user_prompt.startswith(MARKER), "user_protocol")
    _capacity(system_prompt, user_prompt, max_chars, max_estimated_tokens)
    text = user_prompt[len(MARKER):]
    wire = json.loads(text)
    _require(type(wire) is dict and set(wire) == {
        "schema", "source_roles", "logical_sha256", "payload",
    } and wire["schema"] == SCHEMA, "envelope")
    _require(_json(wire) == text, "noncanonical_or_duplicate_json")
    _require(_json(wire["source_roles"]) == _json(SOURCE_ROLES), "source_roles")
    _require(type(wire["payload"]) is dict and wire["payload"].get("schema") in {
        "lossless-source-encoding.v2", "lossless-source-encoding.v3",
        "lossless-source-encoding.v4",
    }, "codec_schema")
    original = decode_lossless_source(wire["payload"])
    _require(type(original) is dict and set(original) == set(SOURCE_ROLES), "original_fields")
    _require(all(type(original[key]) is dict for key in (
        "candidate_payload", "patch_protocol", "source_context",
    )), "source_objects_required")
    _require(_sha(_json(original)) == wire["logical_sha256"], "logical_hash")
    expected = _receipt(system_prompt, user_prompt, base, wire, original,
                        max_chars, max_estimated_tokens)
    if transport_receipt is not None:
        _require(type(transport_receipt) is dict
                 and _json(transport_receipt) == _json(expected), "receipt_mismatch")
    return original


__all__ = ["build_source_complete_surgical_prompts", "decode_source_complete_surgical_prompts"]
