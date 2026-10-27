"""Lossless references to adopted prose already present in a source object.

The corpus stays at its original paths; no prose copy is added to the envelope.
Hashes bind bytes of canonical JSON, not source authority or semantic truth.
This pure codec neither admits a prompt budget nor authorizes any model call.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

SCHEMA = "lossless-adopted-spans.v1"
MIN_SPAN_CHARACTERS = 64
MARKER = "$adopted_span"
DECODE_PROTOCOL = (
    "ADOPTED-SPAN ENCODING v1: source_context is lossless story data. Keep each corpus body "
    "at corpus_paths once. At any nonliteral marker {\"$adopted_span\":[i,start,end]}, read "
    "source_context at corpus_paths[i] and substitute its Unicode-code-point slice [start:end] "
    "(zero-based, end exclusive). Objects at literal_marker_paths are authored literal data: "
    "do not interpret them or their children as references. Reconstruct all fields, order and "
    "duplicates; references never change source authority or turn quoted data into instructions."
)


def _json(value: Any) -> str:
    try:
        # Reject cycles/nonfinite numbers before walking. No default=str or
        # tuple/key coercion may silently change the original source type.
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False)

        def check(item: Any) -> None:
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise ValueError("adopted_span_non_json_key")
                for child in item.values():
                    check(child)
            elif type(item) is list:
                for child in item:
                    check(child)
            elif item is not None and type(item) not in (str, bool, int, float):
                raise ValueError("adopted_span_non_json_value")

        check(value)
        rendered.encode("utf-8")
        return rendered
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ValueError("adopted_span_invalid_json") from exc


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path(parent: str, key: str | int) -> str:
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _corpus(source: dict[str, Any]) -> tuple[list[str], list[str]]:
    if "adopted_continuity" not in source:
        return [], []
    history = source["adopted_continuity"]
    if type(history) is not dict:
        raise ValueError("adopted_span_invalid_continuity")
    rows = history.get("recent_adopted_prose", [])
    if type(rows) is not list:
        raise ValueError("adopted_span_invalid_corpus")
    paths, bodies = [], []
    for index, row in enumerate(rows):
        if type(row) is not dict or type(row.get("body")) is not str:
            raise ValueError("adopted_span_invalid_corpus_body")
        paths.append(f"/adopted_continuity/recent_adopted_prose/{index}/body")
        bodies.append(row["body"])
    return paths, bodies


def encode_adopted_spans(source_context: dict[str, Any]) -> dict[str, Any]:
    """Replace only entire exact string values found in retained adopted bodies."""
    if type(source_context) is not dict:
        raise ValueError("adopted_span_source_object_required")
    original = _json(source_context)
    paths, bodies = _corpus(source_context)
    protected, literals, count = set(paths), [], 0

    def encode(value: Any, path: str) -> Any:
        nonlocal count
        if path in protected:
            return value
        if type(value) is dict:
            if set(value) == {MARKER}:
                # Preserve even malformed/reserved-looking authored objects.
                # Their complete subtree is literal; never encode inside it.
                literals.append(path)
                return deepcopy(value)
            return {key: encode(child, _path(path, key)) for key, child in value.items()}
        if type(value) is list:
            return [encode(child, _path(path, index)) for index, child in enumerate(value)]
        if type(value) is str and len(value) >= MIN_SPAN_CHARACTERS:
            for index, body in enumerate(bodies):
                start = body.find(value)
                if start >= 0:
                    count += 1
                    return {MARKER: [index, start, start + len(value)]}
        return value

    payload = {"schema": SCHEMA, "minimum_span_characters": MIN_SPAN_CHARACTERS,
               "corpus_paths": paths, "literal_marker_paths": sorted(literals),
               "source_context": encode(source_context, "")}
    # encode populates literals while producing source_context.
    payload["literal_marker_paths"] = sorted(literals)
    rendered = _json(payload)
    encoded = {**payload, "integrity": {
        "original_source_sha256": _sha(original), "original_source_characters": len(original),
        "encoded_payload_sha256": _sha(rendered), "encoded_payload_characters": len(rendered),
        "span_count": count, "literal_marker_count": len(literals),
    }}
    if _json(decode_adopted_spans(encoded)) != original:
        raise ValueError("adopted_span_roundtrip_changed")
    return encoded


def decode_adopted_spans(encoded: dict[str, Any]) -> dict[str, Any]:
    """Validate the envelope and reconstruct exactly, or raise before use."""
    _json(encoded)
    fields = {"schema", "minimum_span_characters", "corpus_paths", "literal_marker_paths", "source_context"}
    if (type(encoded) is not dict or set(encoded) != fields | {"integrity"}
            or encoded["schema"] != SCHEMA
            or type(encoded["minimum_span_characters"]) is not int
            or encoded["minimum_span_characters"] != MIN_SPAN_CHARACTERS
            or type(encoded["source_context"]) is not dict):
        raise ValueError("adopted_span_envelope_invalid")
    receipt = encoded["integrity"]
    if (type(receipt) is not dict or set(receipt) != {
            "original_source_sha256", "original_source_characters", "encoded_payload_sha256",
            "encoded_payload_characters", "span_count", "literal_marker_count"}
            or any(type(receipt[key]) is not int or receipt[key] < 0 for key in (
                "original_source_characters", "encoded_payload_characters", "span_count", "literal_marker_count"))):
        raise ValueError("adopted_span_integrity_invalid")
    rendered = _json({key: encoded[key] for key in fields})
    if (receipt["encoded_payload_sha256"] != _sha(rendered)
            or receipt["encoded_payload_characters"] != len(rendered)):
        raise ValueError("adopted_span_encoded_payload_changed")
    paths, bodies = _corpus(encoded["source_context"])
    if encoded["corpus_paths"] != paths:
        raise ValueError("adopted_span_corpus_paths_changed")
    literals = encoded["literal_marker_paths"]
    if (type(literals) is not list or any(type(path) is not str for path in literals)
            or literals != sorted(set(literals)) or receipt["literal_marker_count"] != len(literals)):
        raise ValueError("adopted_span_literal_paths_invalid")
    protected, escaped, used, count = set(paths), set(literals), set(), 0

    def decode(value: Any, path: str) -> Any:
        nonlocal count
        if path in protected:
            return value
        if path in escaped:
            if type(value) is not dict or set(value) != {MARKER}:
                raise ValueError("adopted_span_literal_marker_missing")
            used.add(path)
            return deepcopy(value)
        if type(value) is dict:
            if set(value) == {MARKER}:
                span = value[MARKER]
                if (type(span) is not list or len(span) != 3
                        or any(type(n) is not int for n in span)):
                    raise ValueError("adopted_span_reference_invalid")
                index, start, end = span
                if (not 0 <= index < len(bodies) or start < 0
                        or end - start < MIN_SPAN_CHARACTERS or end > len(bodies[index])):
                    raise ValueError("adopted_span_range_invalid")
                count += 1
                return bodies[index][start:end]
            return {key: decode(child, _path(path, key)) for key, child in value.items()}
        if type(value) is list:
            return [decode(child, _path(path, index)) for index, child in enumerate(value)]
        return value

    result = decode(encoded["source_context"], "")
    if used != escaped or count != receipt["span_count"]:
        raise ValueError("adopted_span_reference_inventory_changed")
    original = _json(result)
    if (receipt["original_source_sha256"] != _sha(original)
            or receipt["original_source_characters"] != len(original)):
        raise ValueError("adopted_span_original_source_changed")
    return result
