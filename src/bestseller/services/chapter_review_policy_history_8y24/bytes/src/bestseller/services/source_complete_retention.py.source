"""Staged pure retention admission; no filesystem, database, or provider writes.

Only an audited, versioned framework request with a verified complete source
codec may use the larger bound. Story flags confer no authority. Ordinary
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


class SourceCompleteRetentionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("source_complete_retention:" + code)


def _need(condition, code):
    if not condition:
        raise SourceCompleteRetentionError(code)


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


def _packet(request, start, end):
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
    _need(
        request.prompt_template is not None and planning_surgical_budget.applies(request),
        "request_contract",
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
    The complete encoded packet must be an actual existing lossless codec. No
    prompts/parameters are modified and no paid/source/grade reuse is granted.
    """
    _need(
        isinstance(request.project_id, UUID) and isinstance(request.workflow_run_id, UUID), "scope_required"
    )
    _need(request.messages_override is None, "messages_override_unsupported")
    effective_role = _role(request, role)
    raw, wire, decoded = _packet(request, packet_start, packet_end)
    if planning_output_budget.is_literal_full_source_request(request):
        _need(
            wire.get("schema") == "lossless-source-encoding.v1"
            and wire.get("encoding") == "identity"
            and wire.get("references") == [],
            "literal_source_required",
        )
    _need(_json(decoded) == _json(logical_source), "source_roundtrip_changed")
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
        _need(
            not ((request.prompt_template is not None and planning_surgical_budget.applies(request))
                 or planning_output_budget.is_literal_full_source_request(request)),
            "source_complete_optin_missing",
        )
        return LEGACY_LIMIT
    _need(
        isinstance(request.project_id, UUID) and isinstance(request.workflow_run_id, UUID), "scope_required"
    )
    current_audit = llm._project_prompt_audit.get()
    _need(
        type(audit) is tuple
        and len(audit) == 2
        and audit == current_audit
        and audit[0] == request.project_id
        and isinstance(audit[1], str)
        and bool(audit[1].strip()),
        "project_audit_required",
    )
    _need(settings.artifact_store.retain_prompt_payloads is True, "retention_disabled")
    _need(settings.artifact_store.mode == "local", "local_store_required")
    _need(request.messages_override is None, "messages_override_unsupported")
    _need(
        len(request.system_prompt) + len(request.user_prompt) <= MAX_WIRE_CHARACTERS,
        "global_wire_character_limit",
    )
    receipt = request.metadata[METADATA_KEY]
    _need(type(receipt) is dict and receipt.get("schema") == SCHEMA, "optin_contract")
    raw, _wire, source = _packet(request, receipt.get("packet_start"), receipt.get("packet_end"))
    _need(len(raw) <= MAX_WIRE_CHARACTERS, "source_character_limit")
    expected = source_complete_retention_metadata(
        request,
        role=role,
        logical_source=source,
        packet_start=receipt["packet_start"],
        packet_end=receipt["packet_end"],
    )
    _need(_json(receipt) == _json(expected), "transport_or_scope_changed")
    payload = llm._effective_input_payload(request, _role(request, role))
    # Count complete physical messages and request schemas, including escaped
    # text, framing and tool_choice. No source suffix is omitted.
    wire_chars = len(_json({key: payload[key] for key in ("messages", "tools", "tool_choice")}))
    _need(wire_chars <= MAX_WIRE_CHARACTERS, "global_wire_character_limit")
    assert_effective_request_capacity(payload)  # Actual model+route AND current finite-run ceiling.
    return MAX_WIRE_CHARACTERS
