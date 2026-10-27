"""Lossless repeated string values for an already encoded planning document.

Keys remain literal. A leading section sign is escaped in every unpooled value;
dictionary values are literal and are never recursively interpreted as markers.
"""

from collections import Counter
import hashlib
import json
from typing import Any

SCHEMA = "planning-string-pool.v1"
PREFIX = "§"
INSTRUCTION = (
    "source_review_data uses planning-string-pool.v1. Restore string VALUES in value first: "
    "§§rest means the literal string §rest; §N (canonical decimal N) means dictionary[N], "
    "returned literally without recursive expansion. Object keys are untouched. Then restore "
    "the contained lossless-source-encoding.v3 keys and its indexed JSON Pointer container. "
    "Only codec-declared pointers expand; authored $ref objects remain literal source data. "
    "Restore every field, quote, order, explicit control and logical hash before review. "
    "Encoding grants no author authority or semantic approval.\n"
)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _values(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _values(child)
    elif isinstance(value, str):
        yield value


def _map(value: Any, transform):
    if isinstance(value, dict):
        return {key: _map(child, transform) for key, child in value.items()}
    if isinstance(value, list):
        return [_map(child, transform) for child in value]
    return transform(value) if isinstance(value, str) else value


def encode(value: dict[str, Any]) -> dict[str, Any]:
    counts = Counter(_values(value))
    # Use a conservative index width; neither source strings nor keys are clipped.
    marker_cost = len(canonical(PREFIX + str(len(counts))))
    profitable = [text for text, count in counts.items() if count > 1
        and (len(canonical(text)) - marker_cost) * count > len(canonical(text)) + 1]
    dictionary = sorted(profitable, key=lambda text: (
        -((len(canonical(text)) - marker_cost) * counts[text] - len(canonical(text)) - 1), text,
    ))
    indices = {text: index for index, text in enumerate(dictionary)}

    def replace(text):
        if text in indices:
            return PREFIX + str(indices[text])
        return PREFIX + text if text.startswith(PREFIX) else text

    wire = {"schema": SCHEMA, "dictionary": dictionary, "value": _map(value, replace),
            "original_sha256": digest(value)}
    if decode(wire) != value:
        raise ValueError("planning_string_pool_roundtrip_mismatch")
    return wire


def decode(wire: dict[str, Any]) -> dict[str, Any]:
    if (not isinstance(wire, dict)
        or set(wire) != {"schema", "dictionary", "value", "original_sha256"}
        or wire.get("schema") != SCHEMA or not isinstance(wire.get("value"), dict)):
        raise ValueError("planning_string_pool_envelope_invalid")
    dictionary = wire["dictionary"]
    if (not isinstance(dictionary, list) or not all(isinstance(v, str) for v in dictionary)
        or len(set(dictionary)) != len(dictionary)):
        raise ValueError("planning_string_pool_dictionary_invalid")

    def restore(text):
        if not text.startswith(PREFIX):
            return text
        if text.startswith(PREFIX * 2):
            return text[1:]
        index = text[1:]
        if (not index.isascii() or not index.isdecimal() or str(int(index)) != index
            or int(index) >= len(dictionary)):
            raise ValueError("planning_string_pool_reference_invalid")
        return dictionary[int(index)]

    restored = _map(wire["value"], restore)
    if digest(restored) != wire["original_sha256"]:
        raise ValueError("planning_string_pool_original_hash_mismatch")
    return restored
