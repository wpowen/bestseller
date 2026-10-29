"""Staged pure retention admission; no filesystem, database, or provider writes.

Only an audited, versioned framework request with a verified complete source
codec or exact reader v2 JSON contract may use the larger bound. Story flags
confer no authority. Ordinary
requests retain their existing 120,000-character behavior byte for byte.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from bestseller.services import llm, planning_output_budget, planning_surgical_budget
from bestseller.services.llm_capacity import assert_effective_request_capacity
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.planning_string_pool import SCHEMA as POOL_SCHEMA
from bestseller.services.planning_string_pool import decode as decode_pool
from bestseller.settings import AppSettings, LLMRoleSettings

# Strict JSON values cross this internal adapter; no domain coercion is allowed.
# ruff: noqa: ANN001, ANN202
SCHEMA = "source-complete-retention.v1"
METADATA_KEY = "source_complete_retention"
LEGACY_LIMIT = 120_000
MAX_WIRE_CHARACTERS = 1_000_000
_READER_CONTRACTS = {
    ("planner", "fanqie_reader_first_field_roles", "fanqie-rf-field-roles-v2"):
        ("fanqie-reader-first-field-roles-request.v1", "reader_first_planning", "outline"),
    ("critic", "planner_reader_first_outline_review", "rf-outline-review-v2"):
        ("reader-first-review-input.v1", "binding", "source"),
    ("planner", "planner_reader_first_full_outline_commercial_surgical_repair",
     "rf-outline-repair-v2"):
        ("fanqie-reader-first-outline-repair.v1", "reader_first_planning", "source"),
}


def is_reader_complete_request(request: llm.LLMCompletionRequest) -> bool:
    """Identify only three native wire identities; metadata grants no opt-in.

    This pure predicate is not admission. The adapter and retention_limit still
    validate the full document, native configuration, scope and physical input.
    """
    identity = (request.logical_role, request.prompt_template, request.prompt_version)
    return all(isinstance(value, str) for value in identity) and identity in _READER_CONTRACTS


def _summary(value):
    try:
        encoded = json.dumps(value, sort_keys=True, default=str).encode()
    except (TypeError, ValueError, RecursionError):
        encoded = type(value).__name__.encode()
    return {"type": type(value).__name__,
            "length": len(value) if isinstance(value, (str, list, dict, bytes)) else None,
            "sha256": hashlib.sha256(encoded).hexdigest()}


class SourceCompleteRetentionError(ValueError):
    def __init__(self, code: str, *, field=None, expected=None, actual=None) -> None:
        self.code = code
        if field is not None:
            self.differences = [{"field": field, "expected": _summary(expected),
                                 "actual": _summary(actual)}]
        super().__init__("source_complete_retention:" + code)


def _need(condition, code):
    if not condition:
        raise SourceCompleteRetentionError(code)


def _request_need(request, condition, code, field, expected, actual):
    if condition:
        return
    if request.prompt_version in {identity[2] for identity in _READER_CONTRACTS}:
        raise SourceCompleteRetentionError(code, field=field, expected=expected, actual=actual)
    _need(False, code)


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _constant(_value):
    raise SourceCompleteRetentionError("nonfinite_json")


def _reader_packet(request, start, end):
    from bestseller.services import source_bound_planning_content as protocol

    def need(condition, code, field, expected, actual):
        _request_need(request, condition, code, field, expected, actual)

    need(type(start) is int and start == 0, "packet_offsets", "packet_start", 0, start)
    need(type(end) is int and end == len(request.user_prompt), "packet_offsets",
         "packet_end", len(request.user_prompt), end)

    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "duplicate_json_key", "request.user_prompt.json_keys",
                 "unique JSON keys", key)
            result[key] = value
        return result

    def constant(value):
        need(False, "nonfinite_json", "request.user_prompt", "finite JSON", value)

    try:
        document = json.loads(request.user_prompt, object_pairs_hook=pairs, parse_constant=constant)
        _json(document).encode("utf-8")  # Reject overflow and invalid Unicode too.
    except SourceCompleteRetentionError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise SourceCompleteRetentionError(
            "reader_json_invalid", field="request.user_prompt",
            expected="strict finite JSON document", actual=request.user_prompt,
        ) from None
    need(type(document) is dict, "codec_object_required", "request.user_prompt",
         "complete JSON object", document)
    schema, marker_key, source_key = _READER_CONTRACTS[
        (request.logical_role, request.prompt_template, request.prompt_version)
    ]
    need(document.get("schema") == schema, "reader_document_contract",
         "request.user_prompt.schema", schema, document.get("schema"))
    marker = document.get(marker_key)
    prefix = "request.user_prompt." + marker_key
    need(type(marker) is dict, "reader_binding_changed", prefix, "native reader binding", marker)
    version = protocol.READER_FIRST_PROTOCOL_V2
    need(marker.get("protocol") == version, "reader_binding_changed",
         prefix + ".protocol", version, marker.get("protocol"))
    try:
        config, config_hash = protocol._reader_config(protocol=version)
    except (ValueError, TypeError, KeyError, OSError):
        raise SourceCompleteRetentionError(
            "reader_config_invalid", field="reader_first.config",
            expected="current native versioned configuration", actual=version,
        ) from None
    keys = ["config_sha256", "protocol", "user_input_sha256"]
    need(sorted(marker) == keys, "reader_binding_changed", prefix + ".keys", keys, sorted(marker))
    need(marker.get("config_sha256") == config_hash, "reader_binding_changed",
         prefix + ".config_sha256", config_hash, marker.get("config_sha256"))
    original_hash = marker.get("user_input_sha256")
    need(type(original_hash) is str and len(original_hash) == 64
         and all(char in "0123456789abcdef" for char in original_hash),
         "reader_binding_changed", prefix + ".user_input_sha256", "sha256", original_hash)
    metadata_binding = request.metadata.get("reader_first_planning")
    try:
        same_binding = _json(metadata_binding) == _json(marker)
    except (ValueError, TypeError, RecursionError):
        same_binding = False
    need(same_binding, "reader_binding_changed",
         "request.metadata.reader_first_planning", marker, metadata_binding)
    source = document.get(source_key)
    need(type(source) is dict, "reader_source_required", "request.user_prompt." + source_key,
         "complete native source object", source)
    source_hash = _sha(_json(source))
    need(document.get("source_sha256") == source_hash, "reader_source_hash_changed",
         "request.user_prompt.source_sha256", source_hash, document.get("source_sha256"))
    if marker_key == "binding":
        expected_output = config["review_output_tokens"]
        need(type(request.max_tokens_override) is int
             and request.max_tokens_override == expected_output, "output_budget_changed",
             "request.max_tokens_override", expected_output, request.max_tokens_override)
        need(request.temperature_override == .2, "output_budget_changed",
             "request.temperature_override", .2, request.temperature_override)
        need(document.get("checkpoint") == "outline", "reader_document_contract",
             "request.user_prompt.checkpoint", "outline", document.get("checkpoint"))
        need(document.get("config_sha256") == config_hash, "reader_binding_changed",
             "request.user_prompt.config_sha256", config_hash, document.get("config_sha256"))
        dimensions = list(protocol.READER_FIRST_DIMENSIONS)
        need(document.get("dimensions") == dimensions, "reader_document_contract",
             "request.user_prompt.dimensions", dimensions, document.get("dimensions"))
        estimated = protocol.estimate_prompt_tokens(request.system_prompt + request.user_prompt)
        maximum = config["outline_input_tokens"]
        need(estimated <= maximum, "reader_input_capacity_exceeded",
             "reader_first.outline_input_tokens", maximum, estimated)
    elif source_key == "outline":
        slot = document.get("annotation_attempt")
        need(type(slot) is int and slot in (0, 1), "reader_document_contract",
             "request.user_prompt.annotation_attempt", [0, 1], slot)
        need(type(document.get("chapter_leaf_values")) is dict, "reader_document_contract",
             "request.user_prompt.chapter_leaf_values", "complete native leaf map",
             document.get("chapter_leaf_values"))
        rejected = document.get("rejected_response")
        need("rejected_response" in document and (rejected is None or type(rejected) is str),
             "reader_document_contract", "request.user_prompt.rejected_response",
             "null or complete rejected response", rejected)
    else:
        for key in ("reader_review", "patch_protocol"):
            need(type(document.get(key)) is dict, "reader_document_contract",
                 "request.user_prompt." + key, "complete native object", document.get(key))
    return request.user_prompt, document, document


def _same_json_types(expected, actual):
    if type(expected) is not type(actual):
        return False
    if type(expected) is dict:
        return (expected.keys() == actual.keys()
                and all(_same_json_types(value, actual[key]) for key, value in expected.items()))
    if type(expected) is list:
        return (len(expected) == len(actual)
                and all(_same_json_types(a, b) for a, b in zip(expected, actual, strict=True)))
    return True


def _packet(request, start, end):
    if is_reader_complete_request(request):
        return _reader_packet(request, start, end)
    _need(
        type(start) is int and type(end) is int and 0 <= start < end <= len(request.user_prompt),
        "packet_offsets",
    )
    raw = request.user_prompt[start:end]
    wire = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    _json(wire)  # Also reject overflowing exponents, e.g. 1e999.
    _need(type(wire) is dict, "codec_object_required")
    container = decode_pool(wire) if wire.get("schema") == POOL_SCHEMA else wire
    _need(
        type(container) is dict
        and container.get("schema") in {f"lossless-source-encoding.v{i}" for i in (1, 2, 3, 4)},
        "codec_schema_unknown",
    )
    source = decode_lossless_source(container)
    return raw, wire, source


def _role(request, role):
    if is_reader_complete_request(request):
        _request_need(request, isinstance(role, LLMRoleSettings), "output_budget_missing",
                      "effective_role", "resolved LLMRoleSettings", type(role).__name__)
        _request_need(request, type(role.max_tokens) is int and role.max_tokens > 0,
                      "output_budget_changed", "effective_role.max_tokens",
                      "positive native output allowance", role.max_tokens)
        return role  # Preserve actual runtime output/stream/reasoning; never enlarge it.
    if planning_output_budget.is_literal_full_source_request(request):
        budget = request.metadata.get("planning_output_budget")
        _need(type(budget) is dict, "output_budget_missing")
        expected = planning_output_budget.output_budget(role)
        _need(
            type(expected) is dict
            and _json(budget) == _json(expected)
            and type(request.max_tokens_override) is int
            and request.max_tokens_override == expected["total_output_tokens"],
            "output_budget_changed",
        )
        return planning_output_budget.apply_output_budget(request, role)
    _request_need(request,
        request.prompt_template is not None and planning_surgical_budget.applies(request),
        "request_contract",
        "request.contract", list(_READER_CONTRACTS),
        [request.logical_role, request.prompt_template, request.prompt_version],
    )
    budget = request.metadata.get("planning_surgical_output_budget")
    _need(type(budget) is dict, "output_budget_missing")
    expected = planning_surgical_budget.output_budget_for_request(
        request, role, answer_tokens=budget.get("answer_tokens")
    )
    _need(
        _json(budget) == _json(expected)
        and type(request.max_tokens_override) is int
        and request.max_tokens_override == expected["total_output_tokens"],
        "output_budget_changed",
    )
    return planning_surgical_budget.apply_role(request, role)


def source_complete_retention_metadata(
    request: llm.LLMCompletionRequest,
    *,
    role: LLMRoleSettings,
    logical_source: object,
    packet_start: int,
    packet_end: int,
) -> dict:
    """Producer-side adapter after its full-source wire has been constructed.

    Supply the entire known source root, never a summary or story-asserted flag.
    The packet is an existing lossless codec, or the entire ordinary JSON user
    document of an exact reader v2 request. No prompts/parameters are modified
    and no paid/source/grade reuse is granted.
    """
    for name in ("project_id", "workflow_run_id"):
        value = getattr(request, name)
        _request_need(request, isinstance(value, UUID), "scope_required",
                      "request." + name, "native UUID", value)
    _request_need(request, request.messages_override is None, "messages_override_unsupported",
                  "request.messages_override", None, request.messages_override)
    effective_role = _role(request, role)
    raw, wire, decoded = _packet(request, packet_start, packet_end)
    if planning_output_budget.is_literal_full_source_request(request):
        _need(
            wire.get("schema") == "lossless-source-encoding.v1"
            and wire.get("encoding") == "identity"
            and wire.get("references") == [],
            "literal_source_required",
        )
    try:
        equal_source = _json(decoded) == _json(logical_source)
        if is_reader_complete_request(request):
            equal_source = equal_source and _same_json_types(decoded, logical_source)
    except (ValueError, TypeError, RecursionError):
        if not is_reader_complete_request(request):
            raise
        equal_source = False
    _request_need(request, equal_source, "source_roundtrip_changed",
                  "logical_source", decoded, logical_source)
    return {
        "schema": SCHEMA,
        "project_id": str(request.project_id),
        "workflow_run_id": str(request.workflow_run_id),
        "logical_role": request.logical_role,
        "prompt_template": request.prompt_template,
        "prompt_version": request.prompt_version,
        "effective_input_sha256": llm._effective_input_hash(request, effective_role),
        "wire_system_sha256": _sha(request.system_prompt),
        "wire_user_sha256": _sha(request.user_prompt),
        "wire_source_sha256": _sha(raw),
        "logical_source_sha256": _sha(_json(decoded)),
        "codec_schema": wire["schema"],
        "packet_start": packet_start,
        "packet_end": packet_end,
        "offset_unit": "unicode_code_points",
        "prefix_sha256": _sha(request.user_prompt[:packet_start]),
        "suffix_sha256": _sha(request.user_prompt[packet_end:]),
        "source_complete": True,
        "roundtrip_verified": True,
        "author_authority_granted": False,
        "grade_asserted": False,
    }


def retention_limit(
    settings: AppSettings,
    request: llm.LLMCompletionRequest,
    *,
    audit: tuple[UUID, str] | None,
    role: LLMRoleSettings,
) -> int:
    """Before claim and again for physical retention: return 120k or validated 1M.

    Failure on the explicit path must propagate; never catch it and silently
    fall back to bounded legacy retention. The caller passes the actual resolved
    physical role, and repeats on every retry/fallback with that actual role.
    """
    if METADATA_KEY not in request.metadata:
        _request_need(request,
            not ((request.prompt_template is not None and planning_surgical_budget.applies(request))
                 or planning_output_budget.is_literal_full_source_request(request)
                 or is_reader_complete_request(request)),
            "source_complete_optin_missing",
            "request.metadata." + METADATA_KEY, "native complete-source receipt", None,
        )
        return LEGACY_LIMIT
    for name in ("project_id", "workflow_run_id"):
        value = getattr(request, name)
        _request_need(request, isinstance(value, UUID), "scope_required",
                      "request." + name, "native UUID", value)
    current_audit = llm._project_prompt_audit.get()
    _request_need(request,
        type(audit) is tuple
        and len(audit) == 2
        and audit == current_audit
        and audit[0] == request.project_id
        and isinstance(audit[1], str)
        and bool(audit[1].strip()),
        "project_audit_required",
        "project_prompt_audit", [request.project_id, "current native audit"], audit,
    )
    _request_need(request, settings.artifact_store.retain_prompt_payloads is True,
                  "retention_disabled", "artifact_store.retain_prompt_payloads",
                  True, settings.artifact_store.retain_prompt_payloads)
    _request_need(request, settings.artifact_store.mode == "local", "local_store_required",
                  "artifact_store.mode", "local", settings.artifact_store.mode)
    _request_need(request, request.messages_override is None, "messages_override_unsupported",
                  "request.messages_override", None, request.messages_override)
    _request_need(request,
        len(request.system_prompt) + len(request.user_prompt) <= MAX_WIRE_CHARACTERS,
        "global_wire_character_limit",
        "request.wire_characters", MAX_WIRE_CHARACTERS,
        len(request.system_prompt) + len(request.user_prompt),
    )
    receipt = request.metadata[METADATA_KEY]
    _request_need(request, type(receipt) is dict, "optin_contract",
                  "request.metadata." + METADATA_KEY, "native complete-source receipt", receipt)
    _request_need(request, receipt.get("schema") == SCHEMA, "optin_contract",
                  "request.metadata." + METADATA_KEY + ".schema", SCHEMA, receipt.get("schema"))
    raw, _wire, source = _packet(request, receipt.get("packet_start"), receipt.get("packet_end"))
    _request_need(request, len(raw) <= MAX_WIRE_CHARACTERS, "source_character_limit",
                  "request.source_characters", MAX_WIRE_CHARACTERS, len(raw))
    expected = source_complete_retention_metadata(
        request,
        role=role,
        logical_source=source,
        packet_start=receipt["packet_start"],
        packet_end=receipt["packet_end"],
    )
    if is_reader_complete_request(request):
        prefix = "request.metadata." + METADATA_KEY
        _request_need(request, receipt.keys() == expected.keys(), "transport_or_scope_changed",
                      prefix + ".keys", sorted(expected), sorted(receipt))
        for key, value in expected.items():
            _request_need(request, type(receipt[key]) is type(value) and receipt[key] == value,
                          "transport_or_scope_changed", prefix + "." + key, value, receipt[key])
    else:
        _need(_json(receipt) == _json(expected), "transport_or_scope_changed")
    payload = llm._effective_input_payload(request, _role(request, role))
    # Count complete physical messages and request schemas, including escaped
    # text, framing and tool_choice. No source suffix is omitted.
    wire_chars = len(_json({key: payload[key] for key in ("messages", "tools", "tool_choice")}))
    _request_need(request, wire_chars <= MAX_WIRE_CHARACTERS, "global_wire_character_limit",
                  "request.effective_wire_characters", MAX_WIRE_CHARACTERS, wire_chars)
    assert_effective_request_capacity(payload)  # Actual model+route AND current finite-run ceiling.
    return MAX_WIRE_CHARACTERS
