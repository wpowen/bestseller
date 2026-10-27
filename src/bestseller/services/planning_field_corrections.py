"""Pure, exact-path corrections to rejected planning JSON; never coerce or approve.

The caller proves the retained payload and patch origin. This sidecar only
checks an explicit patch and derives a new value, leaving both inputs intact.
"""

# JSON leaves are heterogeneous; validation uses exact runtime types.
# ruff: noqa: ANN401

from copy import deepcopy
import hashlib
import json
from typing import Any

from bestseller.services.source_bound_planning_submission import _validate_wire, wire_diagnostics
from bestseller.services.source_cast_field_repair import _at_path

SCHEMA = "planning-field-corrections.v1"


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _tokens(path: str) -> tuple[str, ...]:
    # These are the existing wire validator's diagnostic paths, not decoded
    # JSON Pointers. In particular, never reinterpret authored ~0/~1 strings.
    if path == "$":
        return ()
    if not path.startswith("$/"):
        raise ValueError("planning_field_path_invalid")
    return tuple(path[2:].split("/"))


def _existing(value: Any, tokens: tuple[str, ...]) -> Any:
    for token in tokens:
        if type(value) is dict and token in value:
            value = value[token]
        elif (type(value) is list and token.isascii() and token.isdecimal()
              and str(int(token)) == token and int(token) < len(value)):
            value = value[int(token)]
        else:
            raise ValueError("planning_field_target_missing")
    return value


def _failed_path(error: str) -> str:
    if error.startswith("schema_type:"):
        path, separator, expected = error[len("schema_type:"):].rpartition(":")
        if not separator or expected not in {
            "object", "array", "string", "integer", "number", "boolean", "null",
        }:
            raise ValueError("planning_field_error_not_patchable:" + error)
    elif error.startswith("schema_enum:"):
        path = error[len("schema_enum:"):]
    else:
        raise ValueError("planning_field_error_not_patchable:" + error)
    _tokens(path)
    return path


def correction_contract(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    """Return an exact patch schema for every existing type/enum failure.

    Root $ and array roots are supported. Missing required data, other failure
    kinds, ambiguous schema paths and overlapping targets require another
    recovery path; they never authorize broad field edits here.
    """
    _json(value)
    _json(schema)
    errors = wire_diagnostics(value, schema, schema)
    if not errors:
        # Diagnostics deliberately expose only selected union errors; the
        # strict validator may still reject an ambiguous oneOf, for example.
        try:
            _validate_wire(value, schema, schema)
        except ValueError as exc:
            raise ValueError("planning_field_error_not_patchable:" + str(exc)) from exc
        raise ValueError("planning_field_no_failed_fields")
    paths = sorted({_failed_path(error) for error in errors})
    tokens = {path: _tokens(path) for path in paths}
    for path in paths:
        for other in paths:
            if path != other and tokens[other][:len(tokens[path])] == tokens[path]:
                raise ValueError("planning_field_overlapping_targets")
    properties = {}
    for path in paths:
        current = _existing(value, tokens[path])
        try:
            leaf = _at_path(schema, schema, value, list(tokens[path]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("planning_field_schema_path_unresolved:" + path) from exc
        try:
            _validate_wire(current, leaf, schema)
        except ValueError:
            pass
        else:
            raise ValueError("planning_field_target_not_failed:" + path)
        properties[path] = leaf
    return {
        "type": "object", "properties": properties, "required": paths,
        "additionalProperties": False, "$defs": deepcopy(schema.get("$defs", {})),
    }


def apply_corrections(
    value: Any, schema: dict[str, Any], contract: dict[str, Any], patch: Any,
) -> tuple[Any, dict[str, Any]]:
    """Validate exact model-supplied values, apply them, then validate the full schema.

    No missing/default values are filled and no strings, booleans or containers
    are converted. The receipt establishes derivation, never provider custody,
    patch authorship, source authority or a quality grade.
    """
    expected = correction_contract(value, schema)
    if _json(contract) != _json(expected):
        raise ValueError("planning_field_contract_changed")
    if type(patch) is not dict or set(patch) != set(expected["required"]):
        raise ValueError("planning_field_target_set_changed")
    _json(patch)
    _validate_wire(patch, expected, expected)
    result = deepcopy(value)
    for path in expected["required"]:
        tokens = _tokens(path)
        if not tokens:
            result = deepcopy(patch[path])
            continue
        parent = _existing(result, tokens[:-1])
        key = int(tokens[-1]) if type(parent) is list else tokens[-1]
        parent[key] = deepcopy(patch[path])
    _validate_wire(result, schema, schema)
    return result, {
        "schema": SCHEMA, "derivation": "strict_schema_field_patch",
        "raw_payload_sha256": _sha(value), "patch_payload_sha256": _sha(patch),
        "result_payload_sha256": _sha(result), "original_schema_sha256": _sha(schema),
        "correction_contract_sha256": _sha(expected), "corrected_paths": expected["required"],
        "single_provider_payload_claimed": False, "patch_origin_verified": False,
        "quality_approval_granted": False, "source_authority_granted": False,
    }
