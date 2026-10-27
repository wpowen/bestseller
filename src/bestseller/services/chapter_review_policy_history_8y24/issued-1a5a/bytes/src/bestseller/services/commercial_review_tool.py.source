"""A report submission schema, without executable tools or another model round."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from bestseller.services.commercial_review_wire import WIRE_VERSION
from bestseller.services.commercial_source_evidence import CommercialSources

# ruff: noqa: ANN401
TOOL_NAME = "submit_chapter_review"
TOOL_POLICY = "commercial-report-submission.v1"
DIMENSIONS = (
    "opening_pull",
    "readability",
    "commercial_pull",
    "character_agency",
    "decision_intelligence",
    "character_voice_distinction",
    "scene_execution",
    "continuity",
    "methodology_compliance",
    "hook_strength",
    "knowledge_boundary",
    "real_world_plausibility",
    "object_signal_logic",
    "call_plausibility",
    "capability_demonstrated",
    "material_advancement_score",
)


def review_submission_tools(
    sources: CommercialSources, *, compact: bool = False
) -> list[dict[str, Any]]:
    """Make IDs an explicit enum for this immutable input, then validate again locally."""
    ids = {
        kind: [key for key, row in sources.catalog.items() if row["kind"] == kind]
        for kind in ("current", "prior", "observation", "constraint")
    }
    variants = []
    kinds = {
        "narrative_quality": None,
        "current_fact_conflict": ids["current"],
        "prior_fact_conflict": ids["prior"] + ids["observation"],
        "constraint_violation": ids["constraint"],
    }
    for kind, origins in kinds.items():
        if origins == []:
            continue
        props = {
            "code": {"type": "string"},
            "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
            "assessment_kind": {"type": "string", "enum": [kind]},
            "body_id": {"type": "string", "enum": ids["current"]},
            "reason": {"type": "string"},
            "required_fix": {"type": "string"},
        }
        if origins is not None:
            props["source_id"] = {"type": "string", "enum": origins}
        if kind in {"prior_fact_conflict", "current_fact_conflict"}:
            props["temporal_relation"] = {
                "type": "string",
                "enum": ["same_event_or_state", "unexplained_transition"],
            }
        variants.append(
            {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            }
        )
    props = {
        "wire_version": {"type": "string", "enum": [WIRE_VERSION]},
        "pass": {"type": "boolean"},
        "overall_score": {"type": "number", "minimum": 0, "maximum": 1},
        "dimension_scores": {
            "type": "object",
            "properties": {
                key: {"type": "number", "minimum": 0, "maximum": 1} for key in DIMENSIONS
            },
            "required": list(DIMENSIONS),
            "additionalProperties": False,
        },
        "issues": {"type": "array", "items": {"anyOf": variants}},
        "rewrite_plan": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "preserve": {"type": "array", "items": {"type": "string"}},
                "change": {"type": "array", "items": {"type": "string"}},
                "instructions": {"type": "string"},
            },
            "required": ["scope", "preserve", "change", "instructions"],
            "additionalProperties": False,
        },
    }
    if ids["constraint"]:
        checks = {
            key: {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "enum": ["PASS", "FAIL"]},
                    "body_id": {"type": "string", "enum": ids["current"]},
                    "reason": {"type": "string"},
                },
                "required": ["result", "body_id", "reason"],
                "additionalProperties": False,
            }
            for key in ids["constraint"]
        }
        props["checks"] = {
            "type": "object",
            "properties": checks,
            "required": list(checks),
            "additionalProperties": False,
        }
    tools = [
        {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": "Submit one chapter review against supplied immutable sources. "
                "This only records the report; it cannot rewrite text, adopt drafts or run tools.",
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": list(props),
                    "additionalProperties": False,
                },
            },
        }
    ]
    if compact:
        for tool in tools:
            parameters = tool["function"]["parameters"]
            tool["function"]["parameters"] = _compact_exact_string_enums(parameters)
    return tools


def _exact_string_enum_pattern(values: list[str]) -> str:
    """Emit only literal trie paths, with an absolute ECMAScript end assertion."""
    root: dict = {}
    for value in values:
        node = root
        for char in value:
            node = node.setdefault(char, {})
        node[None] = {}

    def literal(char: str) -> str:
        # Unlike re.escape, these escapes are also valid ECMAScript /u syntax.
        if char in "^$\\.*+?()[]{}|/":
            return "\\" + char
        if ord(char) < 32 or char in "\u2028\u2029":
            return f"\\u{ord(char):04x}"
        return char

    # Collapse single-child paths and use a stack: long literal IDs must not
    # depend on Python's recursion limit. Dict insertion order follows the enum.
    pending: list = [root]
    parts = []
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            parts.append(item)
            continue
        alternatives = []
        for char, child in item.items():
            if char is None:
                alternatives.append([""])
                continue
            prefix = [literal(char)]
            while len(child) == 1 and None not in child:
                char, child = next(iter(child.items()))
                prefix.append(literal(char))
            alternatives.append(["".join(prefix), child])
        tokens: list = []
        if len(alternatives) > 1:
            tokens.append("(?:")
        for index, alternative in enumerate(alternatives):
            if index:
                tokens.append("|")
            tokens.extend(alternative)
        if len(alternatives) > 1:
            tokens.append(")")
        pending.extend(reversed(tokens))
    return "^(?:" + "".join(parts) + r")(?![\s\S])"


def _compact_exact_string_enums(schema: Any) -> Any:
    """Copy a schema, replacing only strictly smaller exact string languages."""
    if not isinstance(schema, dict):
        return deepcopy(schema)
    # JSON data under const/default/examples/enum is never a subschema.
    result = deepcopy(schema)
    for key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
        if isinstance(schema.get(key), dict):
            result[key] = {name: _compact_exact_string_enums(child)
                           for name, child in schema[key].items()}
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        if isinstance(schema.get(key), list):
            result[key] = [_compact_exact_string_enums(child) for child in schema[key]]
    for key in ("items", "additionalItems", "additionalProperties", "unevaluatedProperties",
                "unevaluatedItems", "contains", "not", "if", "then", "else", "propertyNames",
                "contentSchema"):
        if isinstance(schema.get(key), dict):
            result[key] = _compact_exact_string_enums(schema[key])
    values = schema.get("enum")
    if (
        schema.get("type") != "string"
        or not isinstance(values, list)
        or not values
        or any(type(value) is not str for value in values)
        # Native IDs are short. Preserve unusually complex finite languages as
        # enums rather than exceed ECMAScript/Python regular-expression limits.
        or any(len(value) > 256 for value in values)
        or "pattern" in schema
        # UTF-16 surrogate sequences alias scalar characters in ECMAScript.
        # Retain such enums rather than silently widening Python's language.
        or any(0xD800 <= ord(char) <= 0xDFFF for value in values for char in value)
    ):
        return result
    candidate = {key: value for key, value in result.items() if key != "enum"}
    pattern = _exact_string_enum_pattern(values)
    if len(pattern) > 16384:
        return result
    candidate["pattern"] = pattern

    def size(value: Any) -> int:
        return len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8", errors="backslashreplace"
            )
        )

    return candidate if size(candidate) < size(result) else result


def submitted_review_payload(completion: Any) -> tuple[str, dict[str, Any], tuple[str, ...]]:
    """No tool is executed. Missing, foreign or multiple submissions remain unknown."""
    calls = completion.tool_calls
    retained = json.dumps(calls, ensure_ascii=False, sort_keys=True)
    receipt = {
        "policy": TOOL_POLICY,
        "tool_calls_sha256": hashlib.sha256(retained.encode()).hexdigest(),
        "calls_received": len(calls) if isinstance(calls, list) else 0,
        "tools_executed": 0,
        "status": "rejected",
    }
    if not isinstance(calls, list) or len(calls) != 1:
        return completion.content, receipt, ("submission: exactly one review tool call required",)
    call = calls[0]
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict) or function.get("name") != TOOL_NAME:
        return completion.content, receipt, ("submission: unrecognized tool name",)
    raw = function.get("arguments")
    if not isinstance(raw, str) or not raw.strip():
        return completion.content, receipt, ("submission: nonempty JSON arguments required",)
    receipt["status"] = "received"
    return raw, receipt, ()


def review_evaluation_contract(
    *,
    settings: Any,
    sources: CommercialSources,
    system_prompt: str,
    instruction_prefix: str,
    tools: list[dict[str, Any]],
    min_overall: float,
    min_dimensions: dict[str, float],
    model_catalog_key: str | None,
    output_tokens: int,
) -> dict[str, Any]:
    """Bind pre-gateway result reuse to the same effective evaluation policy."""
    from pathlib import Path

    from bestseller.services.commercial_review_wire import ADAPTER_VERSION

    def digest(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
            ).encode()
        ).hexdigest()

    catalog = Path("config/model_catalog.yaml")
    return {
        "schema": "commercial-evaluation-contract.v1",
        "wire_version": WIRE_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "submission_policy": TOOL_POLICY,
        "source_protocol_sha256": sources.receipt["source_protocol_sha256"],
        "system_prompt_sha256": digest(system_prompt),
        "instruction_prefix_sha256": digest(instruction_prefix),
        "tools_sha256": digest(tools),
        "tool_choice": "required",
        "critic_configuration_sha256": digest(settings.llm.critic.model_dump(mode="json")),
        "model_catalog_key": model_catalog_key,
        "model_catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest()
        if catalog.exists()
        else None,
        "min_overall": min_overall,
        "min_dimensions": dict(min_dimensions),
        "output_tokens": output_tokens,
    }
