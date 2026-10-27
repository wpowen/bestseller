"""Future-only scalar native submissions for one complete planning asset.

Pure transport validation: no JSON repair, lossy envelope unwrapping, schema
defaults, execution, grading, adoption, storage or provider dispatch.
"""

# ruff: noqa: ANN401 -- Physical completion shapes and JSON values are validated below.

from __future__ import annotations

from bestseller.services.tool_call_evidence import same_tool_call_evidence

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
from typing import Any

POLICY = "source-bound-planning-submission.v1"
NATIVE_POLICY = "planning-native-model-schema.v2"
VOLUME_NATIVE_POLICY = "planning-native-volume-wrapper.v1"
KINDS = ("book_spec", "world_spec", "cast_spec", "volume_plan")
_NATIVE_KINDS = {"world_spec", "cast_spec"}
_BOOK = {"logline", "theme_statement", "dramatic_question", "series_engine"}
_WORLD = {"world_name", "world_premise"}
_WRAPPERS = {*KINDS, "volumes", "artifacts", "assets", "batch"}


class PlanningSubmissionError(ValueError):
    """Rejected physical evidence, safe to retain in the caller's repair journal."""

    def __init__(self, code: str, receipt: dict[str, Any]) -> None:
        self.code = "planning_submission:" + code
        self.receipt = deepcopy({**receipt, "status": "rejected", "error": self.code})
        super().__init__(self.code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def _kind(kind: str) -> None:
    _require(isinstance(kind, str) and kind in KINDS, "unsupported_kind")


def _version(kind: str, version: str | None) -> None:
    _require(
        version is None or (kind == "volume_plan" and version == VOLUME_NATIVE_POLICY),
        "unsupported_submission_version",
    )


def _json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)
    text.encode("utf-8")
    return text


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _wire_schema(schema: Any) -> Any:
    """Remove only annotations, never same-named fields inside properties."""
    if not isinstance(schema, dict):
        return deepcopy(schema)
    result = {}
    for key, value in schema.items():
        if key in {"description", "title", "examples", "default"}:
            continue
        if key in {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}:
            result[key] = {name: _wire_schema(child) for name, child in value.items()}
        elif key in {"items", "additionalProperties", "contains", "propertyNames", "if", "then",
                     "else", "not", "unevaluatedProperties", "unevaluatedItems"}:
            result[key] = _wire_schema(value)
        elif key in {"allOf", "anyOf", "oneOf", "prefixItems"}:
            result[key] = [_wire_schema(child) for child in value]
        else:
            result[key] = deepcopy(value)
    return result


def _model(kind: str) -> Any:
    from bestseller.domain.story_bible import CastSpecInput, WorldSpecInput

    _require(kind in _NATIVE_KINDS, "native_contract_kind_required")
    return WorldSpecInput if kind == "world_spec" else CastSpecInput


def planning_submission_contract(kind: str, *, version: str | None = None) -> dict[str, Any]:
    """Full validation schema with descriptive/default story instructions removed.

    This is a wire shape, not an author contract. Optional fields stay optional;
    existing completeness/source/semantic gates still run in the caller.
    """
    _kind(kind)
    _version(kind, version)
    if version == VOLUME_NATIVE_POLICY:
        from bestseller.domain.story_bible import VolumePlanEntryInput

        original = VolumePlanEntryInput.model_json_schema(mode="validation", by_alias=False)
        entry = _wire_schema(original)
        definitions = entry.pop("$defs", {})
        definitions["VolumePlanEntryInput"] = entry
        wire = {
            "type": "object",
            "properties": {"volumes": {
                "type": "array", "minItems": 1,
                "items": {"$ref": "#/$defs/VolumePlanEntryInput"},
            }},
            "required": ["volumes"], "additionalProperties": False,
            "$defs": definitions,
        }
        return {
            "wire_format": "native_volume_wrapper", "function_name": "submit_volume_plan",
            "contract_version": VOLUME_NATIVE_POLICY, "json_schema": wire,
            "schema_sha256": _sha(_json(wire)),
            "original_model_schema_sha256": _sha(_json(original)),
        }
    original = _model(kind).model_json_schema(mode="validation")
    wire = _wire_schema(original)
    return {
        "wire_format": "native_asset_object", "function_name": f"submit_{kind}",
        "contract_version": NATIVE_POLICY, "json_schema": wire,
        "schema_sha256": _sha(_json(wire)),
        "original_model_schema_sha256": _sha(_json(original)),
    }


def _validate_wire(value: Any, schema: Any, root: dict[str, Any], path: str = "$") -> None:
    """Validate the generated models' JSON shape before any Pydantic coercers.

    No defaults, conversions, filtering or return-value normalization. The
    model validator additionally enforces its existing non-schema constraints.
    """
    if type(schema) is bool:
        _require(schema, f"schema_forbidden:{path}")
        return
    if "$ref" in schema:
        reference = schema["$ref"]
        _require(isinstance(reference, str) and reference.startswith("#/"),
                 "unsupported_schema_reference")
        target = root
        for part in reference[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        _validate_wire(value, target, root, path)
    for operator in ("anyOf", "oneOf", "allOf"):
        if operator not in schema:
            continue
        matches = 0
        failures = []
        for alternative in schema[operator]:
            try:
                _validate_wire(value, alternative, root, path)
                matches += 1
            except ValueError as exc:
                failures.append(str(exc))
        valid = (matches == len(schema[operator]) if operator == "allOf"
                 else matches == 1 if operator == "oneOf" else matches > 0)
        if not valid:
            # Preserve the rejection and expose its deepest typed field. The
            # nullable branch must not hide e.g. life_history/education:string.
            nearest = max(failures, key=lambda error: error.count("/"), default="")
            detail = ("; nearest=" + nearest) if nearest else ""
            raise ValueError(f"schema_{operator}:{path}" + detail)
    expected = schema.get("type")
    types = {
        "object": dict, "array": list, "string": str, "integer": int,
        "boolean": bool, "null": type(None),
    }
    if expected == "number":
        _require(type(value) in (int, float), f"schema_type:{path}:number")
    elif expected is not None:
        _require(expected in types and type(value) is types[expected],
                 f"schema_type:{path}:{expected}")
    if "enum" in schema:
        _require(any(value == choice and (type(value) is bool) == (type(choice) is bool)
                     for choice in schema["enum"]), f"schema_enum:{path}")
    if type(value) is dict:
        missing = sorted(set(schema.get("required", [])) - value.keys())
        _require(not missing, f"schema_required:{path}:missing=" + ",".join(missing))
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                _validate_wire(child, properties[name], root, path + "/" + name)
            elif "additionalProperties" in schema:
                _validate_wire(child, schema["additionalProperties"], root, path + "/" + name)
    if type(value) is list:
        if "minItems" in schema:
            _require(len(value) >= schema["minItems"], f"schema_minItems:{path}")
        if "maxItems" in schema:
            _require(len(value) <= schema["maxItems"], f"schema_maxItems:{path}")
        if "items" in schema:
            for index, child in enumerate(value):
                _validate_wire(child, schema["items"], root, f"{path}/{index}")
    if type(value) is str:
        if "minLength" in schema:
            _require(len(value) >= schema["minLength"], f"schema_minLength:{path}")
        if "maxLength" in schema:
            _require(len(value) <= schema["maxLength"], f"schema_maxLength:{path}")
    if type(value) in (int, float):
        if "minimum" in schema:
            _require(value >= schema["minimum"], f"schema_minimum:{path}")
        if "maximum" in schema:
            _require(value <= schema["maximum"], f"schema_maximum:{path}")



def wire_diagnostics(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> list[str]:
    """Collect exact wire failures for repair, without changing acceptance or values.

    Nullable alternatives report the nearest typed branch; all returned paths
    are validation data, never proposed story facts or canonical authority.
    """
    if type(schema) is bool:
        try:
            _validate_wire(value, schema, root, path)
            return []
        except ValueError as exc:
            return [str(exc)]
    if "$ref" in schema:
        target = root
        for part in schema["$ref"][2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return wire_diagnostics(value, target, root, path)
    errors = []
    for operator in ("anyOf", "oneOf", "allOf"):
        if operator not in schema:
            continue
        alternatives = [wire_diagnostics(value, child, root, path) for child in schema[operator]]
        if operator == "allOf":
            errors.extend(error for branch in alternatives for error in branch)
        elif not any(not branch for branch in alternatives):
            # Prefer a structurally matching object/array branch over null.
            matching = [branch for branch in alternatives if not any(
                error == f"schema_type:{path}:null" for error in branch)]
            errors.extend(min(matching or alternatives, key=len))
    shallow = {key: child for key, child in schema.items()
               if key not in {"properties", "items", "additionalProperties", "anyOf", "oneOf", "allOf"}}
    try:
        _validate_wire(value, shallow, root, path)
    except ValueError as exc:
        errors.append(str(exc))
    if type(value) is dict:
        for key, child in value.items():
            child_schema = schema.get("properties", {}).get(key, schema.get("additionalProperties"))
            if isinstance(child_schema, (dict, bool)):
                errors.extend(wire_diagnostics(child, child_schema, root, path + "/" + key))
    elif type(value) is list and isinstance(schema.get("items"), dict):
        for index, child in enumerate(value):
            errors.extend(wire_diagnostics(child, schema["items"], root, f"{path}/{index}"))
    return list(dict.fromkeys(errors))


def planning_submission_tool(kind: str, *, version: str | None = None) -> dict[str, Any]:
    """The caller requires this one non-executable tool for the requested kind."""
    _kind(kind)
    _version(kind, version)
    if version == VOLUME_NATIVE_POLICY:
        contract = planning_submission_contract(kind, version=version)
        return {"type": "function", "function": {
            "name": contract["function_name"], "strict": False,
            "description": "Submit one complete volume_plan as direct JSON arguments with "
            "exactly one volumes array. Preserve every volume, field and item. "
            "Do not serialize the asset into asset_json or a second JSON string. "
            "This executes nothing and grants no approval or author authority.",
            "parameters": contract["json_schema"],
        }}
    if kind in _NATIVE_KINDS:
        contract = planning_submission_contract(kind)
        return {"type": "function", "function": {
            "name": contract["function_name"], "strict": False,
            "description": f"Submit one complete {kind} planning proposal "
            "as direct JSON arguments. "
            "Preserve all fields and items. Do not use asset_json or another asset wrapper. "
            "This executes nothing and grants no approval or author authority.",
            "parameters": contract["json_schema"],
        }}
    shape = "array of volume objects" if kind == "volume_plan" else "object"
    return {"type": "function", "function": {
        "name": f"submit_{kind}", "strict": True,
        "description": f"Submit one complete {kind} JSON {shape} as asset_json. "
        "Include every field and item. Do not submit other asset kinds, wrappers, "
        "duplicate keys, fragments or commentary. This records a planning proposal; "
        "it executes no action and grants no approval or author authority.",
        "parameters": {"type": "object", "properties": {
            "asset_json": {"type": "string", "description":
                f"The entire {kind} asset serialized as strict finite JSON, without a fence."},
        }, "required": ["asset_json"], "additionalProperties": False},
    }}


def _field(completion: Any, name: str) -> Any:
    return (completion.get(name) if isinstance(completion, Mapping)
            else getattr(completion, name, None))


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError("nonfinite_json")


def _strict(text: str) -> Any:
    value = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
    _json(value)  # Includes overflow exponents (1e999), finite trees and UTF-8.
    return value


def _asset(payload: Any, kind: str) -> None:
    rows = payload if kind == "volume_plan" else [payload]
    if kind == "volume_plan":
        _require(isinstance(payload, list) and bool(payload), "volume_array_required")
    else:
        _require(isinstance(payload, dict) and bool(payload), "asset_object_required")
    for row in rows:
        _require(isinstance(row, dict) and bool(row), "asset_object_required")
        _require(not (_WRAPPERS & row.keys()), "multiasset_or_wrapper")
        if kind == "book_spec":
            _require(bool(_BOOK & row.keys()) and not (_WORLD & row.keys())
                     and "volume_number" not in row, "wrong_asset_kind")
        elif kind == "world_spec":
            _require(bool(_WORLD & row.keys()) and not (_BOOK & row.keys())
                     and "volume_number" not in row, "wrong_asset_kind")
        elif kind == "cast_spec":
            _require(bool({"protagonist", "supporting_cast", "antagonist", "antagonist_forces"}
                          & row.keys()) and not ((_BOOK | _WORLD) & row.keys())
                     and "volume_number" not in row, "wrong_asset_kind")
        else:
            _require(type(row.get("volume_number")) is int and row["volume_number"] > 0
                     and not ((_BOOK | _WORLD) & row.keys()), "wrong_asset_kind")


def parse_planning_submission(
    completion: Any, *, kind: str, version: str | None = None,
) -> tuple[Any, str, dict[str, Any]]:
    """Return a complete bound-kind proposal or raise with retained raw evidence.

    Text is accepted only in the absence of tool calls. A whole JSON fence is
    allowed there; tool asset_json is always strict JSON without a fence.
    Invalid tool output never falls back to another content channel.
    """
    receipt: dict[str, Any] = {
        "schema": POLICY, "kind": kind if isinstance(kind, str) else None,
        "status": "rejected", "tools_executed": 0, "grade_reused": False,
        "raw_arguments": None, "raw_arguments_sha256": None,
    }
    try:
        _kind(kind)
        _version(kind, version)
        volume_native = version == VOLUME_NATIVE_POLICY
        contract = (planning_submission_contract(kind, version=version)
                    if kind in _NATIVE_KINDS or volume_native else None)
        if contract is not None:
            receipt.update(schema=VOLUME_NATIVE_POLICY if volume_native else NATIVE_POLICY,
                           **{k: v for k, v in contract.items() if k != "json_schema"})
        content = _field(completion, "content")
        calls = _field(completion, "tool_calls")
        raw = _field(completion, "raw_message")
        _require(isinstance(content, str), "content_string_required")
        content_hash = _sha(content)
        receipt.update(original_content=content, original_content_sha256=content_hash,
                       raw_message_present=raw is not None,
                       raw_message_status="missing" if raw is None else "present",
                       raw_message_sha256=_sha(_json(raw)),
                       tool_calls_sha256=_sha(_json(calls)))
        _require(calls is None or isinstance(calls, list), "tool_calls_shape")
        if raw is not None:
            _require(isinstance(raw, dict) and raw.get("role", "assistant") == "assistant",
                     "raw_message_shape")
            _require(same_tool_call_evidence(raw.get("tool_calls"), calls),
                     "raw_message_tool_conflict")
            _require(raw.get("content") == content
                     or (raw.get("content") is None and content == ""),
                     "raw_message_content_conflict")
        if calls:
            _require(len(calls) == 1, "single_submission_required")
            call = calls[0]
            _require(isinstance(call, dict) and set(call) == {"id", "type", "function"},
                     "tool_call_shape")
            _require(isinstance(call["id"], str) and bool(call["id"].strip()), "tool_call_id")
            _require(call["type"] == "function", "tool_call_type")
            function = call["function"]
            _require(isinstance(function, dict) and set(function) == {"name", "arguments"},
                     "function_shape")
            _require(function["name"] == f"submit_{kind}", "wrong_tool")
            arguments = function["arguments"]
            _require(isinstance(arguments, str) and bool(arguments.strip()),
                     "arguments_string_required")
            arguments_hash = _sha(arguments)
            receipt.update(raw_arguments=arguments, raw_arguments_sha256=arguments_hash,
                           tool_name=function["name"], tool_call_id=call["id"])
            outer = _strict(arguments)
            if contract is not None:
                _require(isinstance(outer, dict), "asset_object_required")
                inner = arguments
            else:
                _require(isinstance(outer, dict) and set(outer) == {"asset_json"}, "outer_fields")
                inner = outer["asset_json"]
                _require(isinstance(inner, str) and bool(inner.strip()),
                         "asset_json_string_required")
            channel = "tool_call"
        else:
            inner = content.strip()
            if inner.startswith("```"):
                fence = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", inner)
                _require(fence is not None, "whole_json_fence_required")
                inner = fence.group(1)
            channel = "text_json"
        inner_hash = _sha(inner)
        if volume_native:
            receipt.update(channel=channel, native_document_sha256=inner_hash)
        else:
            receipt.update(channel=channel, asset_json=inner, asset_json_sha256=inner_hash)
        payload = _strict(inner)
        if volume_native:
            from bestseller.domain.story_bible import VolumePlanEntryInput

            if isinstance(payload, dict):
                _require(set(payload) == {"volumes"}, "volume_wrapper_fields")
                wire_value = payload
                receipt["submission_shape"] = "native_volume_wrapper"
            else:
                # Existing text-only fallback speaks the domain's array form.
                # Tool arguments must always use the explicit native wrapper.
                _require(not calls and isinstance(payload, list), "volume_wrapper_required")
                wire_value = {"volumes": payload}
                receipt["submission_shape"] = "text_volume_array"
            _validate_wire(wire_value, contract["json_schema"], contract["json_schema"])
            payload = wire_value["volumes"]
            _asset(payload, kind)
            for row in payload:
                # The model ignores extras by default. Validate only a copy;
                # return the original JSON, never a dump with fields dropped.
                VolumePlanEntryInput.model_validate(deepcopy(row), strict=True)
            receipt.update(status="verified", payload_sha256=_sha(_json(payload)),
                           source_authority="unreviewed_planning_proposal", json_repaired=False)
            return payload, channel, receipt
        _asset(payload, kind)
        if contract is not None:
            # Compute feedback only. The original validator still decides acceptance.
            receipt["wire_diagnostics"] = wire_diagnostics(payload, contract["json_schema"], contract["json_schema"])
            _validate_wire(payload, contract["json_schema"], contract["json_schema"])
            _model(kind).model_validate(deepcopy(payload), strict=True)
        receipt.update(status="verified", payload_sha256=_sha(_json(payload)),
                       source_authority="unreviewed_planning_proposal", json_repaired=False)
        return payload, channel, receipt
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        code = (str(exc) if isinstance(exc, ValueError)
                and not isinstance(exc, json.JSONDecodeError)
                else type(exc).__name__)
        raise PlanningSubmissionError(code, receipt) from exc


__all__ = ["VOLUME_NATIVE_POLICY", "PlanningSubmissionError", "parse_planning_submission",
           "planning_submission_contract", "planning_submission_tool"]
