"""Pure, lossless transport for one non-executable planning admission proposal.

The caller validates target/hash pairing, coverage, relationships and source
authority against its actual request. No grade, defaults or coercions are made
here, and no historical response is upgraded to a current policy result.
"""

# ruff: noqa: ANN401 -- Strictly validated physical JSON completion values.

from __future__ import annotations

from bestseller.services.tool_call_evidence import same_tool_call_evidence

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import re
from typing import Any

POLICY = "planning-admission-submission.v3"
TOOL_NAME = "submit_planning_admission"
_ROOT = {"draft_eligible", "hard_violations", "editorial_notes", "projection_checks"}
_VIOLATION = {"code", "path", "evidence", "source_requirement", "source_refs"}
_CHECK = {"path", "projection_hash", "status", "explanation"}
_CODES = {
    "IDENTITY_CONFLICT",
    "ABILITY_RULE_CONFLICT",
    "CONTINUITY_CONFLICT",
    "EXPLICIT_CREATION_REQUIREMENT_CONFLICT",
    "SCENE_NOT_EXECUTABLE",
}


class PlanningAdmissionSubmissionError(ValueError):
    """Rejected physical evidence; caller can retain this finite JSON receipt."""

    def __init__(self, code: str, receipt: dict[str, Any]) -> None:
        self.code = "planning_admission_submission:" + code
        self.receipt = deepcopy({**receipt, "status": "rejected", "error": self.code})
        super().__init__(self.code)


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def _json(value: Any) -> str:
    text = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    text.encode("utf-8")
    return text


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _wire_schema(schema: Any, root: dict, refs: tuple = ()) -> Any:
    if not isinstance(schema, dict):
        return deepcopy(schema)
    if "$ref" in schema:
        ref = schema["$ref"]
        _need(ref not in refs, "schema_reference")
        return _wire_schema(_resolve(schema, root), root, (*refs, ref))
    result = {}
    for key, value in schema.items():
        if key in {"description", "title", "examples", "default", "$defs", "definitions"}:
            continue
        if key in {"properties", "patternProperties", "dependentSchemas"}:
            result[key] = {name: _wire_schema(child, root, refs) for name, child in value.items()}
        elif key in {
            "items",
            "additionalProperties",
            "contains",
            "propertyNames",
            "not",
            "if",
            "then",
            "else",
            "unevaluatedProperties",
            "unevaluatedItems",
        }:
            result[key] = _wire_schema(value, root, refs)
        elif key in {"anyOf", "allOf", "oneOf", "prefixItems"}:
            result[key] = [_wire_schema(child, root, refs) for child in value]
        else:
            result[key] = deepcopy(value)
    if result.get("type") == "object" or "properties" in result:
        result["required"] = list(result.get("properties", {}))
        result["additionalProperties"] = False
    return result


def _resolve(schema: dict, root: dict) -> dict:
    seen = set()
    while "$ref" in schema:
        ref = schema["$ref"]
        _need(isinstance(ref, str) and ref.startswith("#/") and ref not in seen, "schema_reference")
        seen.add(ref)
        schema = root
        for part in ref[2:].split("/"):
            schema = schema[part.replace("~1", "/").replace("~0", "~")]
        _need(isinstance(schema, dict), "schema_reference")
    return schema


def planning_admission_tool(grade_schema: Mapping, targets: Sequence[Mapping], *, projection_kind: str = 'chapters') -> dict:
    """Restrict supplied Grade selectors without importing its service or data."""
    _need(isinstance(grade_schema, Mapping), "grade_schema")
    _need(isinstance(targets, (list, tuple)) and bool(targets), "audit_targets")
    paths, hashes = [], []
    _need(projection_kind in {'chapters', 'volumes'}, 'audit_target_kind')
    pattern = (r"chapters\[\d+\](?:\.scenes\[\d+\])?" if projection_kind == 'chapters'
               else r"volumes\[\d+\](?:\.information_flow_scene_cards\[\d+\])?")
    for target in targets:
        _need(isinstance(target, Mapping), "audit_target")
        path, digest = target.get("path"), target.get("projection_hash")
        _need(
            isinstance(path, str)
            and re.fullmatch(pattern, path) is not None
            and path not in paths,
            "audit_target_path",
        )
        _need(
            isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            "audit_target_hash",
        )
        paths.append(path)
        if digest not in hashes:
            hashes.append(digest)
    original = dict(grade_schema)
    schema = _wire_schema(original, original)
    try:
        properties = _resolve(schema, schema)["properties"]
        _need(set(properties) == _ROOT, "grade_schema_fields")
        violation = _resolve(properties["hard_violations"]["items"], schema)["properties"]
        check = _resolve(properties["projection_checks"]["items"], schema)["properties"]
        violation["path"]["enum"] = paths.copy()
        check["path"]["enum"] = paths.copy()
        check["projection_hash"]["enum"] = hashes
        check["related_violation_paths"]["items"]["enum"] = paths.copy()
        consistent = deepcopy(properties["projection_checks"]["items"])
        conflict = deepcopy(consistent)
        consistent["properties"].pop("related_violation_paths")
        consistent["properties"]["status"] = {"type": "string", "enum": ["consistent"]}
        consistent["required"] = list(consistent["properties"])
        conflict["properties"]["status"] = {"type": "string", "enum": ["conflict"]}
        conflict["properties"]["related_violation_paths"]["minItems"] = 1
        properties["projection_checks"]["items"] = {"oneOf": [consistent, conflict]}
    except (KeyError, TypeError) as exc:
        raise ValueError("grade_schema_shape") from exc
    if projection_kind == 'volumes':
        violation['source_refs']['items']['pattern'] = r'^(?:/(?:approved_creation_contracts|approved_world_spec|approved_cast_spec|approved_book_spec|adopted_continuity)/[^ ]+|candidate:volumes\[\d+\](?:\.[A-Za-z_][A-Za-z_0-9]*|\[\d+\])*)$'
        violation['source_refs']['items']['description'] = '相对input的source_authority完整路径，不加/input；candidate字段用点号，不混用/字段。每项代码仍须符合角色权限。'
        violation['evidence']['description'] = '仅填候选投影内逐字连续片段，不加字段名、外层引号、省略号或说明。解释填projection_checks.explanation。'
        violation['source_requirement']['description'] = '仅填有权限source_ref叶子的逐字片段，不加路径标签、推论或新要求。'
    _json(schema)
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "strict": True,
            "description": "Submit one complete planning admission proposal. "
            "Call only this tool; do not submit another JSON grade or submission envelope. "
            "Any accompanying plain prose is non-authoritative commentary. "
            "Consistent checks have exactly path, projection_hash, status, explanation; "
            "omit related_violation_paths entirely. Conflict checks also require a nonempty "
            "related_violation_paths array. Other empty arrays use [], never a string or null. "
            "This records data only; "
            "it executes no action, grants no approval and changes no scores or budget.",
            "parameters": schema,
        },
    }


def _field(completion: Any, name: str) -> Any:
    return (
        completion.get(name) if isinstance(completion, Mapping) else getattr(completion, name, None)
    )


def _unique(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(_value: str) -> None:
    raise ValueError("nonfinite_json")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _shape(payload: Any) -> None:
    _need(isinstance(payload, dict) and set(payload) == _ROOT, "payload_fields")
    _need(type(payload["draft_eligible"]) is bool, "draft_eligible_type")
    _need(
        isinstance(payload["editorial_notes"], list)
        and all(isinstance(note, str) for note in payload["editorial_notes"]),
        "editorial_notes",
    )
    _need(isinstance(payload["hard_violations"], list), "hard_violations")
    for item in payload["hard_violations"]:
        _need(isinstance(item, dict) and set(item) == _VIOLATION, "violation_fields")
        _need(isinstance(item["code"], str) and item["code"] in _CODES, "violation_code")
        _need(
            all(_text(item[key]) for key in ("path", "evidence", "source_requirement")),
            "violation_text",
        )
        _need(
            isinstance(item["source_refs"], list)
            and bool(item["source_refs"])
            and all(_text(ref) for ref in item["source_refs"]),
            "source_refs",
        )
    _need(isinstance(payload["projection_checks"], list), "projection_checks")
    for item in payload["projection_checks"]:
        _need(
            isinstance(item, dict)
            and _CHECK <= item.keys()
            and item.keys() <= _CHECK | {"related_violation_paths"},
            "projection_fields",
        )
        _need(all(_text(item[key]) for key in ("path", "explanation")), "projection_text")
        _need(
            isinstance(item["projection_hash"], str)
            and re.fullmatch(r"[0-9a-f]{64}", item["projection_hash"]) is not None,
            "projection_hash",
        )
        _need(
            isinstance(item["status"], str) and item["status"] in {"consistent", "conflict"},
            "projection_status",
        )
        if item["status"] == "consistent":
            _need(set(item) == _CHECK, "consistent_projection_fields")
        else:
            _need("related_violation_paths" in item, "projection_required_fields")
            _need(
                isinstance(item["related_violation_paths"], list)
                and bool(item["related_violation_paths"])
                and all(_text(path) for path in item["related_violation_paths"]),
                "related_paths",
            )


def _commentary(content: str) -> str:
    """Plain prose is not a second submission; structured ambiguity fails closed.

    Structural delimiters/fences are inspected, never story words. Numeric
    indexes attached to identifiers (e.g. chapters[0]) remain ordinary prose
    references. No JSON is extracted, repaired, merged or used for a grade.
    """
    if not content.strip():
        return "absent"
    try:
        json.loads(content, object_pairs_hook=_unique, parse_constant=_nonfinite)
    except json.JSONDecodeError:
        pass
    except (ValueError, TypeError, OverflowError):
        raise ValueError("ambiguous_structured_commentary") from None
    else:
        raise ValueError("competing_structured_submission")
    prose = re.sub(r"(?<=[A-Za-z0-9_\]])\[\d+(?:-\d+)?\]", "", content)
    _need(
        not any(marker in prose for marker in ("```", "~~~", "{", "}", "[", "]")),
        "ambiguous_structured_commentary",
    )
    return "non_authoritative"


def parse_planning_admission(completion: Any, *, payload_validator=None) -> tuple[str, dict]:
    """Return the exact complete JSON text; caller retains the physical completion."""
    receipt = {
        "schema": POLICY,
        "status": "rejected",
        "tools_executed": 0,
        "raw_arguments": None,
        "raw_arguments_sha256": None,
    }
    try:
        content = _field(completion, "content")
        calls = _field(completion, "tool_calls")
        message = _field(completion, "raw_message")
        _need(isinstance(content, str), "content_type")
        receipt.update(
            original_content=content,
            raw_message_present=message is not None,
            raw_message_status="missing" if message is None else "present",
        )
        receipt.update(
            raw_message_sha256=_sha(_json(message)), tool_calls_sha256=_sha(_json(calls))
        )
        _need(_field(completion, "function_call") is None, "legacy_function_call")
        _need(calls is None or isinstance(calls, list), "tool_calls_type")
        if message is not None:
            _need(isinstance(message, dict), "raw_message_shape")
            _need(message.get("role", "assistant") == "assistant", "raw_message_role")
            _need(message.get("function_call") is None, "raw_message_function_call")
            raw_calls = message.get("tool_calls")
            _need(raw_calls is None or isinstance(raw_calls, list), "raw_message_tool_calls_type")
            _need(same_tool_call_evidence(raw_calls, calls), "raw_message_tool_conflict")
            _need(
                message.get("content") == content
                or (message.get("content") is None and content == ""),
                "raw_message_content_conflict",
            )
        if calls:
            _need(len(calls) == 1, "single_submission_required")
            call = calls[0]
            _need(
                isinstance(call, dict) and set(call) == {"id", "type", "function"},
                "tool_call_shape",
            )
            _need(_text(call["id"]) and call["type"] == "function", "tool_call_identity")
            function = call["function"]
            _need(
                isinstance(function, dict) and set(function) == {"name", "arguments"},
                "function_shape",
            )
            _need(function["name"] == TOOL_NAME, "wrong_tool")
            raw = function["arguments"]
            _need(_text(raw), "arguments_string_required")
            receipt.update(raw_arguments=raw, raw_arguments_sha256=_sha(raw), channel="tool")
            receipt["commentary_status"] = _commentary(content)
        else:
            raw = content
            if content.strip().startswith("```"):
                fence = re.fullmatch(r"\s*```(?:json)?[ \t]*\r?\n(.*?)\r?\n```\s*", content, re.S)
                _need(fence is not None, "incomplete_json_fence")
                raw = fence.group(1)
            receipt["channel"] = "content"
        payload = json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)
        _json(payload)  # Also rejects exponent overflow before retaining parsed values.
        (payload_validator or _shape)(payload)
        receipt.update(
            status="verified",
            raw_json_sha256=_sha(raw),
            parsed_payload=payload,
            issues_dropped=0,
            values_modified=False,
            relationships_validated=False,
        )
        return raw, receipt
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        code = str(exc) if type(exc) is ValueError else type(exc).__name__
        raise PlanningAdmissionSubmissionError(code, receipt) from exc
