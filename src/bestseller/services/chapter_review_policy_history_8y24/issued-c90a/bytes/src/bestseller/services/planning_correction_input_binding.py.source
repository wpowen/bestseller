"""Pure, read-only binding of retained correction inputs across estimator changes.

Custody, physical request/return verification and settlement belong to the caller.
This proof neither admits a dispatch nor changes paid metadata, fees or grades.
JSON hashes use sorted compact UTF-8 JSON without ASCII escaping or nonfinite
values; wire hashes cover the exact original UTF-8 string, including instructions.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from bestseller.services.lossless_source_encoding import decode_lossless_source

SCHEMA = "planning-correction-capacity-audit-equivalence.v1"
_DOCUMENT_KEYS = frozenset({
    "schema", "complete_current_source", "current_correction_contract", "origin",
    "original_native_schema", "original_rejection", "retained_candidate",
})
_CAPACITY_NUMBERS = frozenset({
    "max_combined_chars", "max_estimated_tokens", "system_chars", "user_chars",
    "combined_chars", "estimated_tokens",
})
_CAPACITY_KEYS = _CAPACITY_NUMBERS | {"schema", "estimator", "truncated"}
_ESTIMATE_PATH = "/planning_document/complete_current_source/prompt_capacity/estimated_tokens"
_ORIGIN_PATH = "/planning_document/origin/current_full_logical_sha256"


def _need(condition: object, reason: str) -> None:
    if not condition:
        raise ValueError("planning_correction_input_binding:" + reason)


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def _wire_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha(value: object) -> str:
    return _wire_sha(_json(value))


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError("planning_correction_input_binding:nonfinite_json")


def _capacity(value: object) -> None:
    _need(type(value) is dict and set(value) == _CAPACITY_KEYS, "capacity_keys")
    _need(
        value["schema"] == "source-bound-planning-capacity.v1"
        and value["estimator"] == "prompt_compiler.estimate_prompt_tokens"
        and value["truncated"] is False,
        "capacity_schema",
    )
    _need(
        all(type(value[key]) is int and value[key] >= 0 for key in _CAPACITY_NUMBERS),
        "capacity_counts",
    )
    _need(value["combined_chars"] == value["system_chars"] + value["user_chars"],
          "capacity_sum")
    # Historical measurements may exceed their declared preference. This is a
    # structural audit, never current dispatch admission or a new spending grant.


def decode_correction_input(user: str, *, wire_instruction: str) -> dict:
    """Return the strict two-key envelope with its planning_document fully decoded.

Both the lossless transport receipt and the source-derived origin hash are
verified, even for identical inputs. Unsupported/malformed inputs raise
ValueError. Authored references remain literal data as specified by the decoder.
"""
    _need(type(user) is str and type(wire_instruction) is str and bool(wire_instruction)
          and user.startswith(wire_instruction), "wire_instruction")
    envelope = json.loads(
        user[len(wire_instruction):], object_pairs_hook=_pairs, parse_constant=_constant,
    )
    _need(type(envelope) is dict
          and set(envelope) == {"planning_document", "current_correction_contract"},
          "envelope_keys")
    _json(envelope)  # Also reject overflowing JSON floats in the outer contract.
    contract = envelope["current_correction_contract"]
    _need(type(contract) is dict, "outer_contract")
    document = decode_lossless_source(envelope["planning_document"])
    _need(type(document) is dict and set(document) in (
        _DOCUMENT_KEYS, _DOCUMENT_KEYS | {"rejected_patch_for_correction_only"},
    ), "document_keys")
    _need(document["schema"] == "planning-native-field-recovery.v1", "document_schema")
    _need(_json(document["current_correction_contract"]) == _json(contract),
          "inner_outer_contract")
    source, origin = document["complete_current_source"], document["origin"]
    _need(type(source) is dict and source.get("kind") == "volume_plan", "source_kind")
    _capacity(source.get("prompt_capacity"))
    _need(type(origin) is dict
          and origin.get("current_full_logical_sha256") == _sha(source), "derived_origin")
    if "rejected_patch_for_correction_only" in document:
        retry = document["rejected_patch_for_correction_only"]
        _need(type(retry) is dict and set(retry) == {"raw_json", "error", "source_llm_run_id"}
              and all(type(value) is str and bool(value) for value in retry.values()),
              "retry_payload")
    return {"planning_document": document, "current_correction_contract": contract}


def _normalized(envelope: dict) -> dict:
    normalized = deepcopy(envelope)
    document = normalized["planning_document"]
    del document["complete_current_source"]["prompt_capacity"]["estimated_tokens"]
    del document["origin"]["current_full_logical_sha256"]
    return normalized


def prove_capacity_audit_equivalence(
    paid_user: str, current_user: str, *, wire_instruction: str,
) -> dict | None:
    """Prove only an estimated-token leaf change and its verified derived hash.

    Return None for exact validated wire equality. Different wires with exactly
    equal complete decoded envelopes receive a proof without dropping any leaf.
    Other mismatches outside the two named leaves raise ValueError. Retry
    membership and its entire payload must match; callers classify retries by
    membership, never by wire/hash inequality.
The receipt is derived validation evidence, not authentication or paid evidence.
"""
    original = decode_correction_input(paid_user, wire_instruction=wire_instruction)
    current = decode_correction_input(current_user, wire_instruction=wire_instruction)
    if paid_user == current_user:
        return None
    # Compare complete JSON, not hashes or Python equality (True == 1).
    representation_only = _json(original) == _json(current)
    original_normalized, current_normalized = (
        (original, current) if representation_only
        else (_normalized(original), _normalized(current))
    )
    _need(_json(original_normalized) == _json(current_normalized), "whole_input_changed")
    old_document, new_document = original["planning_document"], current["planning_document"]
    old_source, new_source = (document["complete_current_source"]
                              for document in (old_document, new_document))
    old_capacity, new_capacity = old_source["prompt_capacity"], new_source["prompt_capacity"]
    _need(representation_only
          or old_capacity["estimated_tokens"] != new_capacity["estimated_tokens"],
          "estimated_tokens_unchanged")
    return {
        "schema": SCHEMA,
        "receipt_kind": "derived_validation",
        "comparison": ("entire_decoded_envelope_equal" if representation_only else
                       "entire_decoded_envelope_equal_except_estimated_tokens_and_derived_origin"),
        "original_wire_sha256": _wire_sha(paid_user),
        "current_wire_sha256": _wire_sha(current_user),
        "original_document_sha256": _sha(old_document),
        "current_document_sha256": _sha(new_document),
        "original_normalized_sha256": _sha(original_normalized),
        "current_normalized_sha256": _sha(current_normalized),
        "original_full_logical_sha256": _sha(old_source),
        "current_full_logical_sha256": _sha(new_source),
        "original_prompt_capacity": deepcopy(old_capacity),
        "current_prompt_capacity": deepcopy(new_capacity),
        "changed_paths": [] if representation_only else [_ESTIMATE_PATH, _ORIGIN_PATH],
        "raw_original_preserved": True,
        "credit_granted": False,
        "grade_asserted": False,
        "author_authority": False,
        "new_dispatch_capacity_granted": False,
    }


__all__ = ["decode_correction_input", "prove_capacity_audit_equivalence"]
