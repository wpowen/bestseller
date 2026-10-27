"""Typed JSON field observations, never authored quotations or semantic approval."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any

from bestseller.services.prompt_source_policy import content_hash

SCHEMA = "source-bound-field-presence.v1"


def _json(value: object) -> str:
    """Reject non-JSON coercions before using the shared planner-compatible hash."""

    def check(item: object) -> None:
        if item is None or type(item) in (str, bool, int, float):
            return
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            for child in item.values():
                check(child)
            return
        if isinstance(item, list):
            for child in item:
                check(child)
            return
        raise ValueError("Field evidence requires literal JSON values")

    try:
        check(value)
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("Invalid JSON evidence payload") from exc


def _field(value: object) -> tuple[str, str, str]:
    if not isinstance(value, str):
        raise ValueError("Evidence field must be a string")
    parts = value.split(".")
    if len(parts) != 2 or parts[0] not in {"world", "cast"} or not parts[1]:
        raise ValueError("Expected an exact world.<field> or cast.<field>")
    section = f"{parts[0]}_spec"
    escaped = parts[1].replace("~", "~0").replace("/", "~1")
    return section, parts[1], f"/{section}/{escaped}"


def _option(
    payload: Mapping[str, Any],
    field: str,
    side: str,
    payload_hash: str,
) -> dict[str, Any]:
    if side not in {"source", "candidate"}:
        raise ValueError("Evidence side must be source or candidate")
    section, key, pointer = _field(field)
    container = payload.get(section)
    if not isinstance(container, dict):
        raise ValueError("Evidence section is missing or is not a JSON object")
    present = key in container
    value = container.get(key)
    value_type = "absent"
    if present:
        value_type = {
            type(None): "null",
            bool: "boolean",
            int: "integer",
            float: "number",
            str: "string",
            list: "array",
            dict: "object",
        }[type(value)]
    state = {
        "schema": SCHEMA,
        "kind": "field_presence",
        "side": side,
        "field": field,
        "json_pointer": pointer,
        "payload_hash": payload_hash,
        "present": present,
        "value_type": value_type,
        "value_hash": content_hash(value) if present else None,
    }
    prefix = "sp_" if side == "source" else "cp_"
    return {"quote_id": prefix + content_hash(state), **state}


def compile_field_presence_options(
    payload: Mapping[str, Any],
    *,
    fields: Sequence[str],
    side: str,
) -> list[dict[str, Any]]:
    """Observe exact requested roots; callers choose which textual gaps need this.

    Values stay in the original payload rather than being copied into a second
    catalog. Absence is distinct from a present JSON null or empty container.
    Source observations describe the supplied baseline, not author authority.
    """
    if not isinstance(payload, Mapping) or side not in {"source", "candidate"}:
        raise ValueError("Invalid field evidence payload or side")
    _json(payload)
    if isinstance(fields, (str, bytes)):
        raise ValueError("fields must be a sequence of exact field names")
    for field in fields:
        _field(field)
    payload_hash = content_hash(payload)
    return [_option(payload, field, side, payload_hash) for field in sorted(set(fields))]


def resolve_field_presence_evidence(
    entry: Mapping[str, Any],
    options: Sequence[Mapping[str, Any]],
    *,
    payload: Mapping[str, Any],
    side: str,
) -> dict[str, Any]:
    """Resolve a current server catalog reference and reprove it against JSON.

    Invalid, stale, ambiguous or text-bearing references raise ValueError.
    Only source_relation is reviewer prose; it cannot override observed state.
    """
    if not isinstance(entry, Mapping):
        raise ValueError("Invalid evidence entry")
    quote_id = entry.get("quote_id")
    if not isinstance(quote_id, str) or not quote_id:
        raise ValueError("Missing typed evidence reference")
    matches = [option for option in options if option.get("quote_id") == quote_id]
    if len(matches) != 1:
        raise ValueError("Unknown or ambiguous typed evidence reference")
    option = matches[0]
    expected = compile_field_presence_options(
        payload,
        fields=[entry.get("field")],
        side=side,
    )[0]
    if _json(dict(option)) != _json(expected):
        raise ValueError("Typed evidence catalog does not match current payload")
    relation = entry.get("source_relation")
    if not isinstance(relation, str) or not relation.strip():
        raise ValueError("Missing evidence relation")
    for key, value in entry.items():
        if key == "source_relation":
            continue
        if key not in expected or _json(value) != _json(expected[key]):
            raise ValueError("Evidence entry overrides server observation")
    return {**expected, "source_relation": relation}


def field_presence_evidence_matches(
    evidence: Mapping[str, Any],
    *,
    payload: Mapping[str, Any],
    field: str,
    side: str,
) -> bool:
    """Revalidate resolved typed coverage without granting semantic permissions."""
    try:
        expected = compile_field_presence_options(payload, fields=[field], side=side)[0]
        resolved = resolve_field_presence_evidence(
            evidence,
            [expected],
            payload=payload,
            side=side,
        )
        return _json(dict(evidence)) == _json(resolved)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
