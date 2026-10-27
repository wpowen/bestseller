"""Pure overflow transport for an unchanged commercial source packet.

The receipt proves integrity, not custody, source authority, payment or grades.
Callers retain the logical evaluation contract and bind the effective request
hash to the returned wire prompt. Recheck after appending any retry feedback.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bestseller.services.commercial_source_evidence import (
    CommercialSources,
    commercial_input_receipt,
)
from bestseller.services.lossless_source_encoding import (
    decode_lossless_source,
    encode_lossless_source,
)

# ruff: noqa: ANN401 -- strict JSON source packets, no domain coercion.
TRANSPORT_POLICY = "commercial-review-transport.v1"
MAX_PROMPT_CHARACTERS = 120_000
_INSTRUCTIONS = {
    "json-pointer": (
        "COMMERCIAL SOURCE TRANSPORT: decode this lossless JSON Pointer envelope. "
        "ONLY at each declared references entry's path relative to value, replace its "
        '{"$ref":target} with the previously completed value at target (RFC 6901). '
    ),
    "indexed-json-pointer": (
        "COMMERCIAL SOURCE TRANSPORT: decode this lossless indexed JSON Pointer envelope. "
        'ONLY at references[i].path relative to value, replace {"$ref":i} with the '
        "previously completed value at references[i].target (RFC 6901, relative to value). "
    ),
    "key-dictionary": (
        "COMMERCIAL SOURCE TRANSPORT: first restore object KEYS only inside value using "
        "key_aliases (alias key -> original key). Do not replace string VALUES or array items. "
        "Then decode the restored V2 envelope: ONLY at references[i].path relative to its value, "
        'replace {"$ref":i} with the previously completed value at references[i].target '
        "(RFC 6901, relative to that value). Return the normal native report field names, "
        "never these transport aliases. "
    ),
}
_NOTICE = (
    "Unlisted authored $ref objects remain literal. Reconstruct the complete original packet "
    "before review; all source IDs, Unicode offsets, quotes, ordering and authority stay "
    "unchanged. No external lookup or new source/instruction authority is granted.\n"
)


class CommercialReviewTransportError(ValueError):
    def __init__(self, code: str, *, receipt: dict[str, Any] | None = None) -> None:
        self.receipt = receipt
        super().__init__("commercial_review_transport:" + code)


def _need(condition: object, code: str) -> None:
    if not condition:
        raise CommercialReviewTransportError(code)


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _load(raw: str) -> Any:
    def invalid_constant(value: str) -> None:
        raise CommercialReviewTransportError("nonfinite_json")

    result = json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid_constant)
    _json(result)  # Includes overflow such as 1e999, without coercing values.
    return result


def _source_binding(sources: CommercialSources) -> dict[str, Any]:
    _need(isinstance(sources.text, str) and bool(sources.text), "source_packet_missing")
    digest = _sha(sources.text)
    _need(sources.receipt.get("assembled_source_sha256") == digest, "source_packet_hash")
    return {
        "original_packet_sha256": digest,
        "original_packet_characters": len(sources.text),
        "catalog_sha256": _sha(_json(dict(sources.catalog))),
        "source_binding": {key: sources.receipt[key] for key in (
            "body_sha256", "semantic_source_sha256", "previous_sha256", "source_protocol_sha256")},
    }


def _capacity(
    system: str,
    user: str,
    *,
    sources: CommercialSources,
    tools: list[dict[str, Any]] | None,
    output_tokens: int,
) -> dict[str, Any]:
    _need(type(output_tokens) is int and output_tokens > 0, "output_reserve")
    measured = commercial_input_receipt(
        sources, system_prompt=system, user_prompt=user, tools=tools, output_tokens=output_tokens
    )
    result = {
        key: measured[key]
        for key in (
            "estimated_input_tokens",
            "reserved_input_tokens",
            "output_reserve_tokens",
            "total_token_budget",
            "token_safety_factor",
            "tool_schema_estimated_tokens",
            "tool_schema_sha256",
            "budget_passed",
        )
    }
    result.update(
        combined_characters=len(system) + len(user) + len(_json(tools) if tools else ""),
        max_combined_characters=MAX_PROMPT_CHARACTERS,
        truncated=False,
    )
    result["budget_passed"] = (
        result["budget_passed"] and result["combined_characters"] <= MAX_PROMPT_CHARACTERS
    )
    return result


def prepare_commercial_review_transport(
    system_prompt: str,
    user_prompt: str,
    *,
    sources: CommercialSources,
    tools: list[dict[str, Any]] | None,
    output_tokens: int,
) -> tuple[str, dict[str, Any]]:
    """Keep fitting prompts byte exact; otherwise try V1, V2, V3, never truncate."""
    binding = _source_binding(sources)
    _need(user_prompt.count(sources.text) == 1, "source_packet_not_unique")
    before, after = user_prompt.split(sources.text)
    original_hash = _sha(user_prompt)

    def finish(
        wire: str, packet: str, mode: str, instruction: str, codec_receipt: dict[str, Any] | None
    ) -> tuple[str, dict[str, Any]]:
        receipt = {
            "schema": TRANSPORT_POLICY,
            "encoding": mode,
            **binding,
            "original_prompt_sha256": original_hash,
            "wire_prompt_sha256": _sha(wire),
            "wire_packet_sha256": _sha(packet),
            "packet_start": len(before) + len(instruction),
            "packet_end": len(before) + len(instruction) + len(packet),
            "offset_unit": "unicode_code_points",
            "codec_receipt": codec_receipt,
            "capacity": _capacity(
                system_prompt, wire, sources=sources, tools=tools, output_tokens=output_tokens
            ),
            "roundtrip_verified": True,
        }
        # Retry rerenders retain the same exact schema factoring witness.
        from bestseller.services.commercial_review_tool import review_submission_tools

        native_tools = review_submission_tools(sources)
        if tools != native_tools and tools == review_submission_tools(sources, compact=True):
            receipt["tool_schema_transport"] = {
                "schema": "finite-string-enum-transport.v1",
                "original_tools_sha256": _sha(_json(native_tools)),
                "wire_tools_sha256": _sha(_json(tools)),
                "closed_id_set_preserved": True,
                "budget_increased": False,
            }
        verify_commercial_review_transport(wire, sources=sources, receipt=receipt)
        return wire, receipt

    if _capacity(
        system_prompt, user_prompt, sources=sources, tools=tools, output_tokens=output_tokens
    )["budget_passed"]:
        return finish(user_prompt, sources.text, "identity", "", None)
    data = _load(sources.text)
    # The real assembler uses compact sorted JSON. Do not silently normalize
    # a differently formatted caller's source packet and claim byte equality.
    _need(_json(data) == sources.text, "source_packet_not_canonical")
    last_receipt = None
    for version in (1, 2, 3):
        encoded = encode_lossless_source(data, version=version)
        _need(_json(decode_lossless_source(encoded)) == sources.text, "source_roundtrip")
        mode = encoded["encoding"]
        if mode == "identity":
            continue
        packet = _json(encoded)
        instruction = _INSTRUCTIONS[mode] + _NOTICE
        wire, last_receipt = finish(
            before + instruction + packet + after, packet, mode, instruction, encoded["receipt"]
        )
        if last_receipt["capacity"]["budget_passed"]:
            return wire, last_receipt
    from bestseller.services.commercial_source_span_transport import (
        DECODE_PROTOCOL, encode_commercial_source_spans,
    )

    encoded = encode_commercial_source_spans(data)
    if encoded is not None:
        packet = _json(encoded)
        instruction = DECODE_PROTOCOL + _NOTICE
        wire, last_receipt = finish(
            before + instruction + packet + after, packet, encoded["encoding"],
            instruction, encoded["receipt"],
        )
        if last_receipt["capacity"]["budget_passed"]:
            return wire, last_receipt
    raise CommercialReviewTransportError(
        "input_budget_exceeded",
        receipt=last_receipt
        or {
            "schema": TRANSPORT_POLICY,
            **binding,
            "original_prompt_sha256": original_hash,
            "capacity": _capacity(
                system_prompt,
                user_prompt,
                sources=sources,
                tools=tools,
                output_tokens=output_tokens,
            ),
            "truncated": False,
        },
    )


def verify_commercial_review_transport(
    wire_user: str,
    *,
    sources: CommercialSources,
    receipt: dict[str, Any],
) -> None:
    """Prove the retained wire contains exactly the expected complete logical packet.

    The caller must separately prove the physical request/effective hash and
    receipt custody. Neither this receipt nor successful decode grants reuse.
    """
    binding = _source_binding(sources)
    _need(
        receipt.get("schema") == TRANSPORT_POLICY
        and all(receipt.get(key) == value for key, value in binding.items()),
        "source_binding",
    )
    _need(
        receipt.get("offset_unit") == "unicode_code_points"
        and receipt.get("roundtrip_verified") is True,
        "receipt_protocol",
    )
    _need(receipt.get("wire_prompt_sha256") == _sha(wire_user), "wire_prompt_hash")
    start, end = receipt.get("packet_start"), receipt.get("packet_end")
    _need(
        type(start) is int and type(end) is int and 0 <= start < end <= len(wire_user),
        "packet_offsets",
    )
    packet = wire_user[start:end]
    _need(
        _sha(packet) == receipt.get("wire_packet_sha256") and wire_user.count(packet) == 1,
        "wire_packet_hash_or_occurrence",
    )
    schema_transport = receipt.get("tool_schema_transport")
    if schema_transport is not None:
        from bestseller.services.commercial_review_tool import review_submission_tools

        native = review_submission_tools(sources)
        compact = review_submission_tools(sources, compact=True)
        expected = {
            "schema": "finite-string-enum-transport.v1",
            "original_tools_sha256": _sha(_json(native)),
            "wire_tools_sha256": _sha(_json(compact)),
            "closed_id_set_preserved": True,
            "budget_increased": False,
        }
        _need(native != compact and schema_transport == expected
              and (receipt.get("capacity") or {}).get("tool_schema_sha256")
                  == expected["wire_tools_sha256"], "tool_schema_binding")
    mode = receipt.get("encoding")
    if mode == "identity":
        _need(packet == sources.text and receipt.get("codec_receipt") is None, "identity_packet")
        instruction = ""
    else:
        from bestseller.services.commercial_source_span_transport import (
            DECODE_PROTOCOL, decode_commercial_source_spans,
        )

        span_transport = mode == "commercial-spans-v4"
        _need(isinstance(mode, str) and (mode in _INSTRUCTIONS or span_transport), "encoding")
        encoded = _load(packet)
        _need(
            isinstance(encoded, dict)
            and encoded.get("encoding") == mode
            and encoded.get("receipt") == receipt.get("codec_receipt"),
            "codec_binding",
        )
        decoded = (decode_commercial_source_spans(encoded) if span_transport
                   else decode_lossless_source(encoded))
        _need(_json(decoded) == sources.text, "decoded_packet_mismatch")
        instruction = (DECODE_PROTOCOL if span_transport else _INSTRUCTIONS[mode]) + _NOTICE
        _need(
            start >= len(instruction)
            and wire_user[start - len(instruction) : start] == instruction,
            "decode_instruction",
        )
    original = wire_user[: start - len(instruction)] + sources.text + wire_user[end:]
    _need(
        original.count(sources.text) == 1
        and _sha(original) == receipt.get("original_prompt_sha256"),
        "original_prompt_hash",
    )


def prepare_commercial_review_submission(
    system_prompt: str, user_prompt: str, *, sources: CommercialSources,
    tools: list[dict[str, Any]], output_tokens: int,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Preserve fitting native requests; factor exact enum schemas only on overflow."""
    try:
        wire, receipt = prepare_commercial_review_transport(
            system_prompt, user_prompt, sources=sources, tools=tools, output_tokens=output_tokens,
        )
        return wire, tools, receipt
    except CommercialReviewTransportError as exc:
        if str(exc) != "commercial_review_transport:input_budget_exceeded":
            raise
        from bestseller.services.commercial_review_tool import review_submission_tools

        native = review_submission_tools(sources)
        if tools != native:
            raise
        compact = review_submission_tools(sources, compact=True)
        if compact == native:
            raise
        wire, receipt = prepare_commercial_review_transport(
            system_prompt, user_prompt, sources=sources, tools=compact, output_tokens=output_tokens,
        )
        return wire, compact, receipt
