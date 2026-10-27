"""Lossless primary-review source transport, without payment or grade authority.

Literal requests retain the existing assembler's bytes and receipt. Encoded
requests bind the exact logical source and original task to their physical wire.
Custody, effective role/input hashes and paid returns remain caller obligations.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from bestseller.services import lossless_adopted_span_encoding as spans
from bestseller.services import lossless_source_encoding as pointers
from bestseller.services import review_source_context as source
from bestseller.services.prompt_compiler import (
    PromptBudgetError,
    PromptCompilerReport,
    estimate_prompt_tokens,
)

# ruff: noqa: ANN401 -- exact JSON source and receipt domains.
POLICY = "primary-review-source-transport.v1"
MAX_PROMPT_CHARACTERS = 120_000
_INSTRUCTIONS = {
    "json-pointer": (
        "PRIMARY REVIEW SOURCE TRANSPORT: decode the lossless JSON Pointer envelope. "
        'ONLY at references entries\' declared paths relative to value, replace {"$ref":target} '
        "with the previously completed value at target (RFC 6901). "
    ),
    "indexed-json-pointer": (
        "PRIMARY REVIEW SOURCE TRANSPORT: decode the lossless indexed JSON Pointer envelope. "
        'ONLY at references[i].path relative to value, replace {"$ref":i} with the previously '
        "completed value at references[i].target (RFC 6901, relative to value). "
    ),
    "key-dictionary": (
        "PRIMARY REVIEW SOURCE TRANSPORT: first restore object KEYS only inside value using "
        "key_aliases (alias key -> original key); never replace string values or array items. "
        "Then decode the restored indexed JSON Pointer envelope: ONLY at references[i].path "
        'relative to value, replace {"$ref":i} with the previously completed value at '
        "references[i].target (RFC 6901, relative to value). Use normal native report field "
        "names, never transport aliases. "
    ),
    "adopted-spans": "PRIMARY REVIEW SOURCE TRANSPORT: " + spans.DECODE_PROTOCOL + " ",
    "key-and-value-dictionary": (
        "PRIMARY REVIEW SOURCE V4: first restore planning-string-pool.v1 VALUES "
        "(§N=dictionary[N] literally, nonrecursive; §§rest=literal §rest), then restore "
        "KEYS using key_aliases, then restore V2 indexed pointers ONLY at declared "
        "references[i].path, replacing {\"$ref\":i} with the completed value at "
        "references[i].target (RFC6901 relative to value). V3/V2 fallbacks omit absent layers. "
    ),
}
_INSTRUCTIONS["adopted-spans-v4"] = (
    _INSTRUCTIONS["key-and-value-dictionary"] + spans.DECODE_PROTOCOL + " "
)
_NOTICE = (
    "Unlisted authored reference objects remain literal data. Reconstruct every original "
    "source field, quote, order and Unicode offset before review. Encoding never changes "
    "source precedence or grants author, system or tool authority. The following original "
    "task, CURRENT_DRAFT, anchor catalog and retry feedback remain unchanged."
)


# Append-only wire dialect: old retained requests keep their exact decoder and
# notice bytes. Only source-complete requests that failed every older dialect
# may use this shorter, equivalent instruction set.
_COMPACT_ENCODING = "adopted-spans-v4-compact"
_INSTRUCTIONS[_COMPACT_ENCODING] = (
    "Decode V4 VALUES in its value pool only: §N=dictionary[N] (nonrecursive), §§x=literal §x; restore KEYS via "
    "key_aliases, then replace {\"$ref\":i} only at references[i].path with the completed value at references[i].target "
    "relative to value (RFC6901). Absent layers are identity. Then substitute "
    "nonliteral {\"$adopted_span\":[i,s,e]} with source_context at corpus_paths[i] "
    "sliced [s:e] in Unicode codepoints (zero-based,end exclusive). "
    "literal_marker_paths and descendants stay literal. "
)
_COMPACT_NOTICE = (
    "Reconstruct all fields, order, duplicates and Unicode offsets. Unlisted/authored "
    "refs stay data; source authority is unchanged. Original task, CURRENT_DRAFT, "
    "anchor catalog and retry feedback follow unchanged."
)

# A final append-only dialect also refers to the unchanged task as a text
# corpus. This preserves full correction parents without repeating their
# shared current-draft passages. Earlier retained wire dialects stay exact.
_FRAGMENT_ENCODING = "task-fragments-v4-compact"
_INSTRUCTIONS[_FRAGMENT_ENCODING] = (
    "Decode V4 VALUES in its value pool only: §N=dictionary[N] literally (nonrecursive), "
    "§§x=literal §x. Restore KEYS via key_aliases, then indexed pointers only at declared "
    "paths using completed target values (RFC6901 relative to value). Decode "
    "review-source-fragments.v1: nonliteral {\"$review_span\":[i,s,e]} takes Unicode "
    "codepoint slice [s:e] of source_context at corpus_paths[i]; index len(corpus_paths) "
    "instead means the full unchanged original task after this packet. "
    "{\"$review_fragments\":[...]} concatenates literal strings and spans. "
    "literal_marker_paths and descendants stay literal. "
)


def _notice(encoding: str) -> str:
    return _COMPACT_NOTICE if encoding in (_COMPACT_ENCODING, _FRAGMENT_ENCODING) else _NOTICE


class PrimaryReviewTransportError(ValueError):
    """Malformed or source-mismatched retained transport, never a prose verdict."""


def _need(condition: object, code: str) -> None:
    if not condition:
        raise PrimaryReviewTransportError("primary_review_transport:" + code)


def _json(value: Any) -> str:
    # Reuse the codec's strict JSON validation; no tuple/key/default=str coercion.
    pointers._copy_json(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in items:
        _need(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _load(text: str) -> Any:
    def invalid(value: str) -> None:
        raise PrimaryReviewTransportError("primary_review_transport:nonfinite_json")
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=invalid)


def _capacity(system: str, user: str, tools: Any, output_tokens: int) -> dict[str, Any]:
    _need(type(output_tokens) is int and 0 < output_tokens < source.TOTAL_BUDGET, "output_reserve")
    _need(tools is None or type(tools) is list, "tools_type")
    tool_text = _json(tools) if tools is not None else ""
    tool_tokens = estimate_prompt_tokens(tool_text)
    estimated = estimate_prompt_tokens(system) + estimate_prompt_tokens(user) + tool_tokens
    # Preserve the original assembler's conservative allowance as well as the
    # full-request safety formula; adding tools must never enlarge its budget.
    allowance = int((source.TOTAL_BUDGET - output_tokens) / source.SAFETY_FACTOR) - 512
    reserved = int(estimated * source.SAFETY_FACTOR) + 512
    characters = len(system) + len(user) + len(tool_text)
    return {
        "estimated_input_tokens": estimated, "tool_schema_estimated_tokens": tool_tokens,
        "tool_schema_sha256": _sha(tool_text) if tools is not None else None,
        "input_allowance_tokens": allowance, "reserved_input_tokens": reserved,
        "output_reserve_tokens": output_tokens, "total_token_budget": source.TOTAL_BUDGET,
        "token_safety_factor": source.SAFETY_FACTOR,
        "combined_characters": characters, "max_combined_characters": MAX_PROMPT_CHARACTERS,
        "truncated": False,
        "budget_passed": (allowance > 0 and estimated <= allowance
                          and reserved + output_tokens <= source.TOTAL_BUDGET
                          and characters <= MAX_PROMPT_CHARACTERS),
    }


def _reject(capacity: dict[str, Any] | None = None) -> PromptBudgetError:
    error = PromptBudgetError(
        "primary review complete source/task/tools cannot fit; no paid request",
        required_keys=("review_adopted_sources", "review_original_task", "primary_review_tools"),
    )
    error.capacity = capacity
    return error


def _encoded_receipt(system: str, user: str, *, original_system: str, task: str,
                     original: str, packet: str, encoding: str, tools: Any,
                     output_tokens: int) -> dict[str, Any]:
    capacity = _capacity(system, user, tools, output_tokens)
    if not capacity["budget_passed"]:
        raise _reject(capacity)
    system_tokens, user_tokens = estimate_prompt_tokens(system), estimate_prompt_tokens(user)
    budget = capacity["input_allowance_tokens"] - capacity["tool_schema_estimated_tokens"]
    keys = ("review_source_protocol", "review_original_system", "primary_source_decoder",
            "review_adopted_sources", "review_original_task")
    # The encoded route assembles exact blocks directly (no strip/trim/dedupe),
    # and reports the same compiler report shape using the measured final wire.
    report = PromptCompilerReport(
        kept=keys, system_tokens=system_tokens, user_tokens=user_tokens,
        total_tokens=system_tokens + user_tokens, budget_tokens=budget,
        safety_margin=0, safety_margin_tokens=0, usable_budget_tokens=budget,
        budget_remaining_tokens=budget - system_tokens - user_tokens,
        required_block_count=2, required_blocks_kept=keys[:2], required_complete=True,
        final_hash=_sha(_json({"system": system, "user": user})),
        provenance_sources=("framework_protocol", "derived"),
        block_source_hashes={"review_adopted_sources": _sha(original),
                             "review_original_task": _sha(task)},
        source_integrity_blocks=("review_adopted_sources", "review_original_task"),
        source_integrity_complete=True,
    )
    return {
        "schema": source.SOURCE_POLICY, "source_sha256": _sha(original), "source_complete": True,
        "output_reserve_tokens": output_tokens, "total_budget": source.TOTAL_BUDGET,
        "safety_factor": source.SAFETY_FACTOR, "compiler": report.model_dump(mode="json"),
        "primary_source_transport": {
            "schema": POLICY, "encoding": encoding, "logical_source_sha256": _sha(original),
            "original_source_characters": len(original), "wire_source_characters": len(packet),
            "wire_source_sha256": _sha(packet), "wire_system_sha256": _sha(system),
            "wire_user_sha256": _sha(user), "original_system_sha256": _sha(original_system),
            "original_task_sha256": _sha(task), "packet_start": 0, "packet_end": len(packet),
            "offset_unit": "unicode_code_points", "roundtrip_verified": True,
            "capacity": capacity,
        },
    }


def prepare_source_complete_prompt(*, system_prompt: str, user_prompt: str,
    source_context: dict[str, Any], output_tokens: int,
    tools: list[dict[str, Any]] | None = None) -> tuple[str, str, dict[str, Any]]:
    """Preserve a fitting legacy request; otherwise encode only the full source."""
    _need(type(source_context) is dict and isinstance(system_prompt, str)
          and isinstance(user_prompt, str), "input_type")
    original = _json(source_context)
    _capacity("", "", tools, output_tokens)  # Validate before attempting assembly.
    try:
        literal = source.prepare_source_complete_prompt(
            system_prompt=system_prompt, user_prompt=user_prompt,
            source_context=source_context, output_tokens=output_tokens,
        )
    except PromptBudgetError:
        literal = None
    if literal is not None and _capacity(literal[0], literal[1], tools, output_tokens)["budget_passed"]:
        return literal
    last_capacity = _capacity(source.SOURCE_PROTOCOL + "\n\n" + system_prompt,
                              original + "\n\n" + user_prompt, tools, output_tokens)
    for version in (
        1, 2, 3, "adopted-spans", 4, "adopted-spans-v4",
        _COMPACT_ENCODING, _FRAGMENT_ENCODING,
    ):
        if version == _FRAGMENT_ENCODING:
            minimum_system = (
                source.SOURCE_PROTOCOL + "\n\n" + system_prompt + "\n\n"
                + _INSTRUCTIONS[version] + _notice(version)
            )
            if not _capacity(minimum_system, user_prompt, tools, output_tokens)["budget_passed"]:
                continue  # An unchanged oversized task cannot benefit from source references.
            encoded = pointers.encode_lossless_source(
                _fragment_encode(source_context, user_prompt), version=4,
            )
            restored = _fragment_decode(pointers.decode_lossless_source(encoded), user_prompt)
        elif version in ("adopted-spans-v4", _COMPACT_ENCODING):
            encoded = pointers.encode_lossless_source(spans.encode_adopted_spans(source_context), version=4)
            restored = spans.decode_adopted_spans(pointers.decode_lossless_source(encoded))
        else:
            encoded = (spans.encode_adopted_spans(source_context) if version == "adopted-spans"
                       else pointers.encode_lossless_source(source_context, version=version))
            restored = (spans.decode_adopted_spans(encoded) if version == "adopted-spans"
                        else pointers.decode_lossless_source(encoded))
        _need(_json(restored) == original, "source_roundtrip")
        explicit = ("adopted-spans", "adopted-spans-v4", _COMPACT_ENCODING, _FRAGMENT_ENCODING)
        encoding = version if version in explicit else encoded["encoding"]
        packet = _json(encoded)
        if encoding == "identity" or len(packet) >= len(original):
            continue
        system = source.SOURCE_PROTOCOL + "\n\n" + system_prompt + "\n\n" + _INSTRUCTIONS[encoding] + _notice(encoding)
        user = packet + "\n\n" + user_prompt
        last_capacity = _capacity(system, user, tools, output_tokens)
        if not last_capacity["budget_passed"]:
            continue
        receipt = _encoded_receipt(
            system, user, original_system=system_prompt, task=user_prompt,
            original=original, packet=packet, encoding=encoding, tools=tools,
            output_tokens=output_tokens,
        )
        verify_primary_review_transport(system, user, source_context=source_context,
            receipt=receipt, tools=tools, output_tokens=output_tokens)
        return system, user, receipt
    raise _reject(last_capacity)


def verify_primary_review_transport(system: str, user: str, *, source_context: dict[str, Any],
    receipt: dict[str, Any], tools: list[dict[str, Any]] | None,
    output_tokens: int) -> None:
    """Verify actual wire against full current sources, without granting paid reuse."""
    _need(isinstance(system, str) and isinstance(user, str) and type(source_context) is dict
          and type(receipt) is dict, "input_type")
    original = _json(source_context)
    _need(receipt.get("schema") == source.SOURCE_POLICY
          and receipt.get("source_complete") is True
          and receipt.get("source_sha256") == _sha(original), "logical_source_binding")
    prefix = source.SOURCE_PROTOCOL + "\n\n"
    _need(system.startswith(prefix), "system_protocol")
    transport = receipt.get("primary_source_transport")
    if transport is None:
        _need(user.startswith(original + "\n\n"), "literal_source")
        expected = source.prepare_source_complete_prompt(
            system_prompt=system[len(prefix):], user_prompt=user[len(original) + 2:],
            source_context=source_context, output_tokens=output_tokens,
        )
        _need(expected[:2] == (system, user) and _json(expected[2]) == _json(receipt), "literal_receipt")
        _need(_capacity(system, user, tools, output_tokens)["budget_passed"], "input_budget")
        return
    _need(type(transport) is dict and transport.get("schema") == POLICY, "transport_schema")
    encoding = transport.get("encoding")
    _need(isinstance(encoding, str) and encoding in _INSTRUCTIONS, "encoding")
    end = transport.get("packet_end")
    _need(type(transport.get("packet_start")) is int and transport["packet_start"] == 0
          and type(end) is int and 0 < end <= len(user)
          and user[end:end + 2] == "\n\n", "packet_offsets")
    packet, task = user[:end], user[end + 2:]
    encoded = _load(packet)
    _need(type(encoded) is dict, "packet_object")
    _need(_json(encoded) == packet, "packet_canonical")
    _need((encoding == "adopted-spans" and encoded.get("schema") == spans.SCHEMA)
          or (encoding in ("adopted-spans-v4", _COMPACT_ENCODING, _FRAGMENT_ENCODING) and encoded.get("schema") in (
              pointers.INDEXED_SCHEMA, pointers.KEY_DICTIONARY_SCHEMA, pointers.VALUE_DICTIONARY_SCHEMA))
          or (encoding not in ("adopted-spans", "adopted-spans-v4", _COMPACT_ENCODING, _FRAGMENT_ENCODING)
              and encoded.get("encoding") == encoding), "codec_identity")
    if encoding == _FRAGMENT_ENCODING:
        restored = _fragment_decode(pointers.decode_lossless_source(encoded), task)
    elif encoding in ("adopted-spans-v4", _COMPACT_ENCODING):
        restored = spans.decode_adopted_spans(pointers.decode_lossless_source(encoded))
    else:
        restored = (spans.decode_adopted_spans(encoded) if encoding == "adopted-spans"
                    else pointers.decode_lossless_source(encoded))
    _need(_json(restored) == original, "decoded_source_binding")
    suffix = "\n\n" + _INSTRUCTIONS[encoding] + _notice(encoding)
    _need(system.endswith(suffix), "decode_protocol")
    original_system = system[len(prefix):-len(suffix)]
    expected_receipt = _encoded_receipt(
        system, user, original_system=original_system, task=task, original=original,
        packet=packet, encoding=encoding, tools=tools, output_tokens=output_tokens,
    )
    _need(_json(receipt) == _json(expected_receipt), "wire_receipt_mismatch")


_FRAGMENT_SCHEMA = "review-source-fragments.v1"
_FRAGMENT_SPAN = "$review_span"
_FRAGMENT_TEXT = "$review_fragments"
_FRAGMENT_MIN = 32


def _fragment_path(parent: str, key: str | int) -> str:
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _fragment_corpus(source: dict[str, Any], original_task: str) -> tuple[list[str], list[str]]:
    _need(type(original_task) is str, "fragment_task_type")
    paths, bodies = spans._corpus(source)
    return paths, [*bodies, original_task]


def _fragment_encode(source_context: dict[str, Any], original_task: str) -> dict[str, Any]:
    original = _json(source_context)
    paths, bodies = _fragment_corpus(source_context, original_task)
    protected = set(paths)
    literals = []
    span_count = 0
    index = {}
    for i, body in enumerate(bodies):
        for start in range(max(0, len(body) - _FRAGMENT_MIN + 1)):
            index.setdefault(body[start : start + _FRAGMENT_MIN], []).append((i, start))

    def text(value: str) -> Any:
        nonlocal span_count
        pieces = []
        cursor = 0
        literal_start = 0
        while cursor + _FRAGMENT_MIN <= len(value):
            best = None
            for i, start in index.get(value[cursor : cursor + _FRAGMENT_MIN], []):
                size = _FRAGMENT_MIN
                body = bodies[i]
                while (
                    start + size < len(body)
                    and cursor + size < len(value)
                    and body[start + size] == value[cursor + size]
                ):
                    size += 1
                if best is None or size > best[0]:
                    best = (size, i, start)
                if size == len(value) - cursor:
                    break  # A complete remaining suffix cannot have a longer match.
            if best is None:
                cursor += 1
                continue
            size, i, start = best
            if cursor > literal_start:
                pieces.append(value[literal_start:cursor])
            pieces.append({_FRAGMENT_SPAN: [i, start, start + size]})
            span_count += 1
            cursor += size
            literal_start = cursor
        if not pieces:
            return value
        if literal_start < len(value):
            pieces.append(value[literal_start:])
        return pieces[0] if len(pieces) == 1 else {_FRAGMENT_TEXT: pieces}

    def encode(value: Any, path: str) -> Any:
        if path in protected:
            return value
        if type(value) is dict:
            if set(value) in ({_FRAGMENT_SPAN}, {_FRAGMENT_TEXT}):
                literals.append(path)
                return deepcopy(value)
            return {k: encode(v, _fragment_path(path, k)) for k, v in value.items()}
        if type(value) is list:
            return [encode(v, _fragment_path(path, i)) for i, v in enumerate(value)]
        return text(value) if type(value) is str and len(value) >= _FRAGMENT_MIN else value

    packet = {
        "schema": _FRAGMENT_SCHEMA,
        "minimum_span_characters": _FRAGMENT_MIN,
        "corpus_paths": paths,
        "original_task_sha256": _sha(original_task),
        "original_task_characters": len(original_task),
        "literal_marker_paths": [],
        "source_context": encode(source_context, ""),
    }
    packet["literal_marker_paths"] = sorted(literals)
    rendered = _json(packet)
    packet["integrity"] = {
        "original_source_sha256": _sha(original),
        "original_source_characters": len(original),
        "encoded_payload_sha256": _sha(rendered),
        "encoded_payload_characters": len(rendered),
        "span_count": span_count,
        "literal_marker_count": len(literals),
    }
    _need(_json(_fragment_decode(packet, original_task)) == original, "fragment_roundtrip")
    return packet


def _fragment_decode(packet: dict[str, Any], original_task: str) -> dict[str, Any]:
    _json(packet)
    fields = {
        "schema",
        "minimum_span_characters",
        "corpus_paths",
        "original_task_sha256",
        "original_task_characters",
        "literal_marker_paths",
        "source_context",
    }
    _need(
        type(packet) is dict
        and set(packet) == fields | {"integrity"}
        and packet["schema"] == _FRAGMENT_SCHEMA
        and type(packet["source_context"]) is dict
        and type(packet["minimum_span_characters"]) is int
        and packet["minimum_span_characters"] == _FRAGMENT_MIN,
        "fragment_envelope",
    )
    receipt = packet["integrity"]
    keys = {
        "original_source_sha256",
        "original_source_characters",
        "encoded_payload_sha256",
        "encoded_payload_characters",
        "span_count",
        "literal_marker_count",
    }
    _need(
        type(receipt) is dict
        and set(receipt) == keys
        and all(
            type(receipt[k]) is int and receipt[k] >= 0
            for k in (
                "original_source_characters",
                "encoded_payload_characters",
                "span_count",
                "literal_marker_count",
            )
        ),
        "fragment_integrity",
    )
    rendered = _json({k: packet[k] for k in fields})
    _need(
        receipt["encoded_payload_sha256"] == _sha(rendered)
        and receipt["encoded_payload_characters"] == len(rendered),
        "fragment_packet_changed",
    )
    _need(
        type(original_task) is str
        and packet["original_task_sha256"] == _sha(original_task)
        and type(packet["original_task_characters"]) is int
        and packet["original_task_characters"] == len(original_task),
        "fragment_task_changed",
    )
    paths, bodies = _fragment_corpus(packet["source_context"], original_task)
    _need(packet["corpus_paths"] == paths, "fragment_corpus_changed")
    literals = packet["literal_marker_paths"]
    _need(
        type(literals) is list
        and all(type(p) is str for p in literals)
        and literals == sorted(set(literals))
        and len(literals) == receipt["literal_marker_count"],
        "fragment_literal_inventory",
    )
    protected = set(paths)
    escaped = set(literals)
    seen = set()
    count = 0

    def span(value: Any) -> str:
        nonlocal count
        _need(
            type(value) is list and len(value) == 3 and all(type(n) is int for n in value),
            "fragment_span_type",
        )
        i, start, end = value
        _need(
            0 <= i < len(bodies)
            and start >= 0
            and end - start >= _FRAGMENT_MIN
            and end <= len(bodies[i]),
            "fragment_span_range",
        )
        count += 1
        return bodies[i][start:end]

    def decode(value: Any, path: str) -> Any:
        if path in protected:
            return value
        if path in escaped:
            _need(
                type(value) is dict and set(value) in ({_FRAGMENT_SPAN}, {_FRAGMENT_TEXT}),
                "fragment_literal_missing",
            )
            seen.add(path)
            return deepcopy(value)
        if type(value) is dict:
            if set(value) == {_FRAGMENT_SPAN}:
                return span(value[_FRAGMENT_SPAN])
            if set(value) == {_FRAGMENT_TEXT}:
                pieces = value[_FRAGMENT_TEXT]
                _need(type(pieces) is list and bool(pieces), "fragment_chunks_type")
                restored = []
                references = 0
                for part in pieces:
                    if type(part) is str:
                        restored.append(part)
                    else:
                        _need(
                            type(part) is dict and set(part) == {_FRAGMENT_SPAN},
                            "fragment_chunk_type",
                        )
                        restored.append(span(part[_FRAGMENT_SPAN]))
                        references += 1
                _need(references > 0, "fragment_no_references")
                return "".join(restored)
            return {k: decode(v, _fragment_path(path, k)) for k, v in value.items()}
        if type(value) is list:
            return [decode(v, _fragment_path(path, i)) for i, v in enumerate(value)]
        return value

    result = decode(packet["source_context"], "")
    _need(seen == escaped and count == receipt["span_count"], "fragment_reference_inventory")
    original = _json(result)
    _need(
        receipt["original_source_sha256"] == _sha(original)
        and receipt["original_source_characters"] == len(original),
        "fragment_original_changed",
    )
    return result
