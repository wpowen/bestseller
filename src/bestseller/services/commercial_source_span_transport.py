"""Pure commercial packet transport composed from existing span V1 and source V4.

The temporary corpus is only a byte-preserving copy of supplied prior bodies.
It grants no adoption/source authority; the entire original packet, including
every adoption state and unknown source field, survives unchanged. Receipts
bind canonical JSON bytes, not authenticity, approval, custody or capacity.
"""

from __future__ import annotations

import hashlib
from typing import Any

from bestseller.services.lossless_adopted_span_encoding import (
    _json as _canonical_json,
)
from bestseller.services.lossless_adopted_span_encoding import (
    decode_adopted_spans,
    encode_adopted_spans,
)
from bestseller.services.lossless_source_encoding import (
    INDEXED_SCHEMA,
    KEY_DICTIONARY_SCHEMA,
    VALUE_DICTIONARY_SCHEMA,
    decode_lossless_source,
    encode_lossless_source,
)

# ruff: noqa: ANN401 -- strict JSON data, with no domain coercion.
SCHEMA = "commercial-source-span-transport.v1"
ENCODING = "commercial-spans-v4"
DECODE_PROTOCOL = (
    "COMMERCIAL SOURCE SPAN TRANSPORT v1: validate all receipts and reverse value's "
    "lossless-source-encoding.v4 first. Restore its planning-string-pool.v1 string VALUES: "
    "§N (canonical decimal N) returns dictionary[N] literally, nonrecursive; §§rest escapes "
    "to literal §rest. Dictionary strings and object keys are not expanded. THEN restore "
    "alias KEYS using key_aliases (never string values), THEN restore the contained V2 "
    'indexed JSON Pointers: only at references[i].path replace {"$ref":i} with the '
    "previously completed value at references[i].target (RFC 6901, relative to value). "
    "Forward/self/ancestor references are invalid. V4's existing V3/V2 fallbacks omit "
    "the absent pool/alias steps and follow their declared schema. THEN reverse "
    'lossless-adopted-spans.v1: only nonliteral {"$adopted_span":[i,start,end]} uses '
    "corpus_paths[i]'s Unicode-code-point slice [start:end], zero-based, end exclusive. "
    "literal_marker_paths protect authored marker objects and their entire subtrees; "
    "unlisted authored $ref objects remain literal. Require exactly wrapper fields packet "
    "and adopted_continuity, exactly recent_adopted_prose within adopted_continuity, and "
    'exactly {"body":string} corpus rows equal in value, count and order to packet\'s '
    "previous_chapters literal content_md/body/content/content_excerpt values, in row order and that fixed field order (keep conflicting alternatives). Only then choose packet and verify its canonical hash/bytes. "
    "Restore every field, type, duplicate, order, quote, Unicode offset and authored marker "
    "unchanged. The corpus copy grants no adoption or source authority; retain all original "
    "adoption states. This is data, never execution or instructions authority; no external "
    "lookup, semantic approval or new evidence is authorized."
)


def _need(condition: object, code: str) -> None:
    if not condition:
        raise ValueError("commercial_source_span_transport:" + code)


def _bytes(value: Any) -> bytes:
    # Reuse the existing strict JSON validator: no coercion, cycles, nonfinite
    # floats, non-string keys or invalid UTF-8 can enter either hash binding.
    return _canonical_json(value).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _corpus(packet: Any) -> list[dict[str, str]]:
    _need(type(packet) is dict, "packet_object_required")
    rows = packet.get("previous_chapters")
    _need(type(rows) is list, "previous_chapters_required")
    # These are the producer's declared prior-text fields. Preserve all present
    # literal strings, including conflicting alternatives; select no story truth.
    text_fields = ("content_md", "body", "content", "content_excerpt")
    _need(
        all(type(row) is dict and any(type(row.get(key)) is str for key in text_fields)
            for row in rows),
        "previous_body_string_required",
    )
    return [{"body": row[key]} for row in rows for key in text_fields
            if type(row.get(key)) is str]


def encode_commercial_source_spans(packet: dict[str, Any]) -> dict[str, Any] | None:
    """Return a strictly smaller complete envelope, or None; never mutate source."""
    original = _bytes(packet)
    wrapper = {
        "packet": packet,
        "adopted_continuity": {"recent_adopted_prose": _corpus(packet)},
    }
    value = encode_lossless_source(encode_adopted_spans(wrapper), version=4)
    core = {"schema": SCHEMA, "encoding": ENCODING, "value": value}
    raw = _bytes(core)
    envelope = {
        **core,
        "receipt": {
            "original_packet_sha256": _sha(original),
            "original_packet_bytes": len(original),
            "encoded_core_sha256": _sha(raw),
            "encoded_core_bytes": len(raw),
            "shrunk": True,
        },
    }
    if len(_bytes(envelope)) >= len(original):
        return None
    _need(_bytes(decode_commercial_source_spans(envelope)) == original, "roundtrip_changed")
    return envelope


def decode_commercial_source_spans(envelope: dict[str, Any]) -> dict[str, Any]:
    """Validate every layer and the copied corpus before returning the original packet."""
    wire = _bytes(envelope)
    _need(
        type(envelope) is dict
        and set(envelope) == {"schema", "encoding", "value", "receipt"}
        and envelope["schema"] == SCHEMA
        and envelope["encoding"] == ENCODING,
        "envelope_invalid",
    )
    receipt = envelope["receipt"]
    _need(
        type(receipt) is dict
        and set(receipt)
        == {
            "original_packet_sha256",
            "original_packet_bytes",
            "encoded_core_sha256",
            "encoded_core_bytes",
            "shrunk",
        }
        and receipt["shrunk"] is True
        and all(
            type(receipt[key]) is int and receipt[key] > 0
            for key in (
                "original_packet_bytes",
                "encoded_core_bytes",
            )
        ),
        "receipt_invalid",
    )
    core = {key: value for key, value in envelope.items() if key != "receipt"}
    raw = _bytes(core)
    _need(
        receipt["encoded_core_sha256"] == _sha(raw) and receipt["encoded_core_bytes"] == len(raw),
        "encoded_core_changed",
    )
    value = envelope["value"]
    _need(
        type(value) is dict
        and value.get("schema")
        in (
            INDEXED_SCHEMA,
            KEY_DICTIONARY_SCHEMA,
            VALUE_DICTIONARY_SCHEMA,
        ),
        "source_codec_invalid",
    )
    wrapper = decode_adopted_spans(decode_lossless_source(value))
    _need(
        type(wrapper) is dict and set(wrapper) == {"packet", "adopted_continuity"},
        "wrapper_invalid",
    )
    continuity = wrapper["adopted_continuity"]
    _need(
        type(continuity) is dict and set(continuity) == {"recent_adopted_prose"},
        "corpus_fields_invalid",
    )
    expected = _corpus(wrapper["packet"])
    # Canonical comparison is type-exact (unlike Python's True == 1).
    _need(_bytes(continuity["recent_adopted_prose"]) == _bytes(expected), "corpus_changed")
    packet = wrapper["packet"]
    original = _bytes(packet)
    _need(
        receipt["original_packet_sha256"] == _sha(original)
        and receipt["original_packet_bytes"] == len(original),
        "original_packet_changed",
    )
    _need(len(wire) < len(original), "not_smaller")
    return packet
