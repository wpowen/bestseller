"""Resolve explicit authored material from native SDK wire bytes only.

No model verdict, score, source identifier or quotation is inferred. Configs
choose a JSON pointer or an exact substring in an identified wire string. The
result is a proposal; all actual native semantic/paid validators remain intact.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202 -- JSON pointer and authored JSON boundary.
from copy import deepcopy
import json
import re

from .contracts import canonical, digest, need


def pointer(value, path):
    need(
        type(path) is str and (not path or path.startswith("/")) and len(path) <= 2048,
        "SCRIPT_WIRE_POINTER_INVALID",
    )
    for raw in path.split("/")[1:] if path else ():
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            need(
                bool(re.fullmatch(r"0|[1-9]\d*", key)) and int(key) < len(value),
                "SCRIPT_WIRE_SOURCE_MISSING",
            )
            value = value[int(key)]
        else:
            need(isinstance(value, dict) and key in value, "SCRIPT_WIRE_SOURCE_MISSING")
            value = value[key]
    return value


def bind_authored_material(authored, wire_body, *, expected_wire_sha256):
    need(digest(wire_body) == expected_wire_sha256, "SCRIPT_AUTHORED_WIRE_MISMATCH")
    need(len(canonical(authored).encode()) <= 512_000, "SCRIPT_RESPONSE_UNBOUNDED")
    bindings, nodes = [], [0]

    def walk(value, field="", depth=0, opinion=False):
        nodes[0] += 1
        need(depth <= 64 and nodes[0] <= 100_000, "SCRIPT_AUTHORED_MATERIAL_UNBOUNDED")
        if type(value) is dict:
            if "$wire" in value:
                need(
                    not opinion
                    and field
                    not in {
                        "passed",
                        "pass",
                        "verdict",
                        "decision",
                        "status",
                        "classification",
                        "score",
                        "confidence",
                        "result",
                    }
                    and not field.endswith("_score"),
                    "SCRIPT_VERDICT_MUST_BE_AUTHORED",
                )
                need(
                    set(value) <= {"$wire", "decode_json", "pointer", "quote", "key_index"},
                    "SCRIPT_WIRE_BINDING_INVALID",
                )
                selected = pointer(wire_body, value["$wire"])
                if value.get("decode_json"):
                    need(type(selected) is str, "SCRIPT_WIRE_JSON_REQUIRED")
                    try:
                        selected = json.loads(selected)
                    except (ValueError, TypeError):
                        need(False, "SCRIPT_WIRE_JSON_REQUIRED")
                if "pointer" in value:
                    selected = pointer(selected, value["pointer"])
                if "key_index" in value:
                    index = value["key_index"]
                    need(
                        type(selected) is dict
                        and type(index) is int
                        and 0 <= index < len(selected),
                        "SCRIPT_WIRE_SOURCE_MISSING",
                    )
                    selected = list(selected)[index]
                if "quote" in value:
                    quote = value["quote"]
                    need(
                        type(selected) is str
                        and type(quote) is str
                        and bool(quote)
                        and quote in selected,
                        "SCRIPT_WIRE_QUOTE_MISMATCH",
                    )
                    selected = quote  # exact, contiguous; no normalization.
                bindings.append(
                    {
                        "field": field,
                        "wire_pointer": value["$wire"],
                        "selected_sha256": digest(selected),
                    }
                )
                return deepcopy(selected)
            if "$object" in value:
                rows = value["$object"]
                need(
                    set(value) == {"$object"} and type(rows) is list and len(rows) <= 128,
                    "SCRIPT_WIRE_BINDING_INVALID",
                )
                result = {}
                for row in rows:
                    need(
                        type(row) is dict and set(row) == {"key", "value"},
                        "SCRIPT_WIRE_BINDING_INVALID",
                    )
                    key = walk(row["key"], "source_id", depth + 1)
                    need(type(key) is str and key not in result, "SCRIPT_WIRE_BINDING_INVALID")
                    result[key] = walk(row["value"], field, depth + 1, opinion)
                return result
            return {
                key: walk(item, key, depth + 1, opinion or key in {"scores", "dimension_scores"})
                for key, item in value.items()
            }
        if type(value) is list:
            return [walk(item, field, depth + 1, opinion) for item in value]
        # Explicit verdicts/scores stay literal. Source-bound values must come
        # through a wire binding, including integer paragraph selectors.
        source_field = field.endswith(
            ("_source_id", "_source_ids", "_quote_id", "_quote_ids", "_quote", "_quotes")
        ) or field in {
            "source_id",
            "source_ids",
            "body_id",
            "body_ids",
            "paragraph_number",
            "source_quote",
            "seed_evidence",
            "candidate_evidence",
        }
        if source_field and value not in (None, "", "unknown"):
            need(False, "SCRIPT_SOURCE_VALUE_REQUIRES_NATIVE_WIRE")
        return deepcopy(value)

    payload = walk(authored)
    return payload, {
        "schema": "authored-native-wire-bindings.v1",
        "wire_sha256": expected_wire_sha256,
        "bindings": bindings,
        "verdicts_inferred": False,
    }
