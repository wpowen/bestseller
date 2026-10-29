"""Pure SDK response contracts; never runs a review or writes native state.

P3 owns concrete story scripts and transport installation. This module accepts
only a named, inventoried template and a caller-generated runtime payload. It
does not infer a passing answer, manufacture evidence IDs, or copy fallbacks.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202, ANN204, ANN206 -- JSON and observer boundaries are duck typed.
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import importlib
import json
import re
from uuid import UUID

from .inventory import INVENTORY_SCHEMA, TEMPLATE_NAME

TAPE_SCHEMA = "v3-scripted-provider-tape.v1"
MODES = frozenset({"auto", "json", "sse", "timeout", "disconnect"})
FINISH_REASONS = frozenset({"stop", "length", "tool_calls", "content_filter"})
HASH_SELECTORS = frozenset(
    {
        "body_sha256",
        "messages_tools_sha256",
        "system_tools_sha256",
        "native_operation_key",
        "native_input_hash",
    }
)


class ScriptContractError(ValueError):
    """Fixed diagnostic codes, without body, prompt, schema or arguments."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def need(condition, code):
    if not condition:
        raise ScriptContractError(code)


def canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (ValueError, TypeError, RecursionError):
        raise ScriptContractError("SCRIPT_JSON_INVALID") from None


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Limits:
    max_entries: int = 4096
    max_responses_per_entry: int = 8
    max_call_index: int = 4096
    max_response_bytes: int = 512_000
    max_document_bytes: int = 32_000_000
    max_tokens: int = 100_000

    def __post_init__(self):
        need(
            all(type(value) is int and value > 0 for value in vars(self).values()),
            "SCRIPT_LIMIT_INVALID",
        )


DEFAULT_LIMITS = Limits()


class TemplateCatalog:
    def __init__(self, inventory):
        need(
            isinstance(inventory, Mapping) and inventory.get("schema") == INVENTORY_SCHEMA,
            "SCRIPT_INVENTORY_SCHEMA",
        )
        need(not inventory.get("parse_failures"), "SCRIPT_SOURCE_PARSE_FAILED")
        self.inventory = deepcopy(inventory)
        self.names = frozenset(
            row["template"] for row in inventory["templates"] if not row["historical_only"]
        )

    def require(self, template):
        need(
            type(template) is str and bool(TEMPLATE_NAME.fullmatch(template)),
            "SCRIPT_TEMPLATE_INVALID",
        )
        need(template in self.names, "SCRIPT_TEMPLATE_UNCOVERED")
        return template


@dataclass(frozen=True)
class CallIdentity:
    prompt_template: str
    call_index: int
    project_id: str | UUID | None = None
    workflow_run_id: str | UUID | None = None
    body_sha256: str | None = None
    binding_hashes: Mapping | None = None

    def match(self, catalog, limits):
        catalog.require(self.prompt_template)
        need(
            type(self.call_index) is int and 1 <= self.call_index <= limits.max_call_index,
            "SCRIPT_CALL_INDEX_UNBOUNDED",
        )
        row = {"prompt_template": self.prompt_template, "call_index": self.call_index}
        for key in ("project_id", "workflow_run_id"):
            value = getattr(self, key)
            if value is not None:
                try:
                    need(isinstance(value, (str, UUID)), "SCRIPT_UUID_INVALID")
                    row[key] = str(UUID(str(value)))
                except (ValueError, TypeError, AttributeError):
                    raise ScriptContractError("SCRIPT_UUID_INVALID") from None
        if self.body_sha256 is not None:
            need(
                type(self.body_sha256) is str
                and bool(re.fullmatch(r"[0-9a-f]{64}", self.body_sha256)),
                "SCRIPT_BODY_HASH_INVALID",
            )
            row["body_sha256"] = self.body_sha256
        if self.binding_hashes is not None:
            need(
                isinstance(self.binding_hashes, Mapping)
                and set(self.binding_hashes) <= HASH_SELECTORS,
                "SCRIPT_BINDING_HASH_INVALID",
            )
            for key, value in self.binding_hashes.items():
                need(
                    type(value) is str and bool(re.fullmatch(r"[0-9a-f]{64}", value)),
                    "SCRIPT_BINDING_HASH_INVALID",
                )
                need(key not in row or row[key] == value, "SCRIPT_BINDING_HASH_CONFLICT")
                row[key] = value
        # Some pre-project conception calls also lack a workflow UUID. Bind
        # those to P3 wire_fingerprints(body)['body_sha256'], never a fake UUID.
        need(
            any(key in row for key in ({"project_id", "workflow_run_id"} | HASH_SELECTORS)),
            "SCRIPT_IDENTITY_UNBOUND",
        )
        return row

    @classmethod
    def from_observation(cls, observation, *, body_sha256=None, binding_hashes=None):
        try:
            return cls(
                observation.prompt_template,
                observation.call_index,
                observation.project_id,
                observation.workflow_run_id,
                body_sha256,
                binding_hashes,
            )
        except AttributeError:
            raise ScriptContractError("SCRIPT_OBSERVATION_INVALID") from None


def _schema_guard(schema):
    # jsonschema must never resolve a remote reference during offline validation.
    pending, count = [schema], 0
    while pending:
        value = pending.pop()
        count += 1
        need(count <= 100_000, "SCRIPT_SCHEMA_UNBOUNDED")
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"}:
                    need(isinstance(item, str) and item.startswith("#"), "SCRIPT_SCHEMA_REMOTE_REF")
                if key == "$id":
                    need(isinstance(item, str) and item.startswith("#"), "SCRIPT_SCHEMA_REMOTE_REF")
                pending.append(item)
        elif isinstance(value, list):
            pending.extend(value)


def tool_response(
    catalog, template, wire_body, *, tool_name, arguments, usage, mode="auto", limits=DEFAULT_LIMITS
):
    """Encode one proposal against the exact tool schema on the SDK wire.

    Caller supplies actual source-bound IDs/quotes in arguments. JSON-schema
    validity is a structural check, never native business or quality acceptance.
    Nothing here generates pass/approval/grade fields or executes a function.
    """
    catalog.require(template)
    need(isinstance(wire_body, Mapping), "SCRIPT_WIRE_BODY_INVALID")
    tools = wire_body.get("tools")
    need(isinstance(tools, list), "SCRIPT_TOOL_SCHEMA_MISSING")
    matches = [
        item.get("function")
        for item in tools
        if isinstance(item, Mapping)
        and item.get("type") == "function"
        and isinstance(item.get("function"), Mapping)
        and item["function"].get("name") == tool_name
    ]
    need(len(matches) == 1, "SCRIPT_TOOL_UNCOVERED")
    choice = wire_body.get("tool_choice")
    need(choice != "none", "SCRIPT_TOOL_CHOICE_MISMATCH")
    if isinstance(choice, Mapping):
        need(
            choice.get("type") == "function"
            and (choice.get("function") or {}).get("name") == tool_name,
            "SCRIPT_TOOL_CHOICE_MISMATCH",
        )
    schema = matches[0].get("parameters")
    need(isinstance(schema, Mapping), "SCRIPT_TOOL_SCHEMA_MISSING")
    need(len(canonical(schema).encode()) <= limits.max_response_bytes, "SCRIPT_SCHEMA_UNBOUNDED")
    _schema_guard(schema)
    canonical(arguments)
    try:
        jsonschema = importlib.import_module("jsonschema")
    except ImportError:
        raise ScriptContractError("SCRIPT_SCHEMA_VALIDATOR_UNAVAILABLE") from None
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(arguments)
    except (jsonschema.ValidationError, jsonschema.SchemaError, RecursionError):
        raise ScriptContractError("SCRIPT_TOOL_ARGUMENT_SCHEMA_MISMATCH") from None
    spec = {
        "mode": mode,
        "content": "",
        "tool_calls": [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": canonical(arguments),
                },
            }
        ],
        "finish_reason": "tool_calls",
        "usage": deepcopy(usage),
    }
    validate_response(spec, limits)
    return spec


def text_response(
    catalog, template, content, *, usage, finish_reason, mode="auto", limits=DEFAULT_LIMITS
):
    """Encode runtime-generated text; no default verdict or fallback text."""
    catalog.require(template)
    spec = {
        "mode": mode,
        "content": content,
        "finish_reason": finish_reason,
        "usage": deepcopy(usage),
    }
    validate_response(spec, limits)
    return spec


def validate_response(spec, limits=DEFAULT_LIMITS):
    need(type(spec) is dict, "SCRIPT_RESPONSE_INVALID")
    allowed = {
        "mode",
        "content",
        "tool_calls",
        "usage",
        "finish_reason",
        "chunk_characters",
        "disconnect_after_chunks",
    }
    need(not set(spec) - allowed and spec.get("mode", "auto") in MODES, "SCRIPT_RESPONSE_INVALID")
    need(len(canonical(spec).encode()) <= limits.max_response_bytes, "SCRIPT_RESPONSE_UNBOUNDED")
    for key, minimum in (("chunk_characters", 1), ("disconnect_after_chunks", 0)):
        value = spec.get(key, minimum)
        need(
            type(value) is int and minimum <= value <= limits.max_response_bytes,
            "SCRIPT_STREAM_BOUNDS_INVALID",
        )
    if spec.get("mode") == "timeout":
        return  # P3 timeout specs intentionally contain no completed return.
    need(isinstance(spec.get("content", ""), str), "SCRIPT_CONTENT_INVALID")
    usage = spec.get("usage")
    need(
        type(usage) is dict
        and set(usage) <= {"prompt_tokens", "completion_tokens", "total_tokens"},
        "SCRIPT_USAGE_INVALID",
    )
    need(
        all(
            type(usage.get(key)) is int and 0 <= usage[key] <= limits.max_tokens
            for key in ("prompt_tokens", "completion_tokens")
        ),
        "SCRIPT_USAGE_INVALID",
    )
    need(
        "total_tokens" not in usage
        or (
            type(usage["total_tokens"]) is int
            and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
        ),
        "SCRIPT_USAGE_INVALID",
    )
    need(spec.get("finish_reason") in FINISH_REASONS, "SCRIPT_FINISH_REASON_INVALID")
    calls = spec.get("tool_calls", [])
    need(type(calls) is list and len(calls) <= 1, "SCRIPT_TOOL_COUNT_INVALID")
    for call in calls:
        need(
            type(call) is dict
            and set(call) <= {"type", "function"}
            and call.get("type", "function") == "function",
            "SCRIPT_TOOL_SHAPE_INVALID",
        )
        fn = call.get("function")
        need(
            type(fn) is dict
            and set(fn) == {"name", "arguments"}
            and type(fn["name"]) is str
            and bool(fn["name"])
            and type(fn["arguments"]) is str,
            "SCRIPT_TOOL_SHAPE_INVALID",
        )
    need(spec["finish_reason"] != "tool_calls" or bool(calls), "SCRIPT_TOOL_COUNT_INVALID")


def build_document(catalog, responses, *, allowed_endpoints, limits=DEFAULT_LIMITS):
    """Compile a finite iterable of (CallIdentity, [response specs]) for P3 Tape."""
    entries = []
    for identity, specs in responses:
        need(len(entries) < limits.max_entries, "SCRIPT_ENTRY_COUNT_UNBOUNDED")
        need(isinstance(identity, CallIdentity), "SCRIPT_OBSERVATION_INVALID")
        match = identity.match(catalog, limits)
        need(
            type(specs) in (list, tuple) and 1 <= len(specs) <= limits.max_responses_per_entry,
            "SCRIPT_RESPONSE_COUNT_UNBOUNDED",
        )
        for spec in specs:
            validate_response(spec, limits)
        for entry in entries:
            common = set(match) & set(entry["match"])
            need(
                any(match[key] != entry["match"][key] for key in common),
                "SCRIPT_SELECTOR_AMBIGUOUS",
            )
        entries.append(
            {"id": "full-flow:" + digest(match), "match": match, "responses": deepcopy(list(specs))}
        )
    need(bool(entries), "SCRIPT_ENTRY_COUNT_UNBOUNDED")
    need(type(allowed_endpoints) is list and bool(allowed_endpoints), "SCRIPT_ENDPOINT_INVALID")
    for endpoint in allowed_endpoints:
        need(
            type(endpoint) is dict and set(endpoint) == {"scheme", "host", "port", "path"},
            "SCRIPT_ENDPOINT_INVALID",
        )
        need(
            endpoint["scheme"] in {"https", "http"}
            and type(endpoint["host"]) is str
            and bool(re.fullmatch(r"[a-z0-9.-]+", endpoint["host"]))
            and type(endpoint["port"]) is int
            and 1 <= endpoint["port"] <= 65535
            and type(endpoint["path"]) is str
            and endpoint["path"].startswith("/")
            and not any(char in endpoint["path"] for char in "*?#"),
            "SCRIPT_ENDPOINT_INVALID",
        )
    document = {
        "schema": TAPE_SCHEMA,
        "simulation_only": True,
        "allowed_endpoints": deepcopy(allowed_endpoints),
        "entries": entries,
    }
    need(
        len(canonical(document).encode()) <= limits.max_document_bytes, "SCRIPT_DOCUMENT_UNBOUNDED"
    )
    return document


def prepare_p3_tape(document, *, catalog, audit_path=None):
    """Explicit, lazy construction. Caller owns installation and process lifetime."""
    need(isinstance(catalog, TemplateCatalog), "SCRIPT_INVENTORY_SCHEMA")
    need(
        isinstance(document, Mapping) and isinstance(document.get("entries"), list),
        "SCRIPT_DOCUMENT_SCHEMA",
    )
    for entry in document["entries"]:
        need(
            isinstance(entry, Mapping) and isinstance(entry.get("match"), Mapping),
            "SCRIPT_ENTRY_INVALID",
        )
        catalog.require(entry["match"].get("prompt_template"))
    try:
        module = importlib.import_module("e2e_sim.transport")
    except ImportError:
        raise ScriptContractError("SCRIPT_P3_TRANSPORT_UNAVAILABLE") from None
    need(
        getattr(module, "TAPE_SCHEMA", None) == TAPE_SCHEMA
        and all(
            callable(getattr(module, name, None))
            for name in ("Tape", "NativeTemplateObserver", "install_scripted_http")
        ),
        "SCRIPT_P3_TRANSPORT_INTERFACE_DRIFT",
    )
    return module.Tape(
        document, audit_path=audit_path, template_observer=module.NativeTemplateObserver()
    )
