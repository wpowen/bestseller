"""Actual model-derived native schema; legacy Book/Volume bytes remain exact."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from bestseller.domain.story_bible import CastSpecInput, WorldSpecInput
from bestseller.services.source_bound_planning_submission import (
    NATIVE_POLICY,
    POLICY,
    PlanningSubmissionError,
    parse_planning_submission,
    planning_submission_contract,
    planning_submission_tool,
)

pytestmark = pytest.mark.unit
ANNOTATIONS = {"description", "title", "examples", "default"}


def dumps(value, *, sorted_keys=True):
    return json.dumps(value, ensure_ascii=False, sort_keys=sorted_keys, separators=(",", ":"))


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def payload(kind):
    if kind == "world_spec":
        return {
            "world_name": "山地观测站", "world_premise": "光学与声学同时研究",
            "rules": [{"name": "观测须留记录", "description": "每次观察保留原始数据"}],
            "history_key_events": [{"event": "共同建站", "relevance": "两地协作"}],
        }
    return {"protagonist": {"name": "于岚", "role": "supporting"},
            "supporting_cast": [{"name": "张帆", "goal": "校准望远镜"}]}


def completion(kind, value=None, *, raw_message=False, text=False, raw_arguments=None):
    value = payload(kind) if value is None else value
    raw = (json.dumps(value, ensure_ascii=False, indent=2)
           if raw_arguments is None else raw_arguments)
    content = raw if text else ""
    calls = None if text else [{"id": "native-original", "type": "function", "function": {
        "name": "submit_" + kind, "arguments": raw,
    }}]
    return SimpleNamespace(content=content, tool_calls=calls, raw_message={
        "role": "assistant", "content": content, "tool_calls": deepcopy(calls),
    } if raw_message else None)


def schema_nodes(schema, path=()):
    """Inventory schema positions, distinct from same-named asset properties."""
    if not isinstance(schema, dict):
        return
    yield path, schema
    for key, value in schema.items():
        if key in {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"}:
            for name, child in value.items():
                yield from schema_nodes(child, (*path, key, name))
        elif key in {"anyOf", "oneOf", "allOf", "prefixItems"}:
            for index, child in enumerate(value):
                yield from schema_nodes(child, (*path, key, index))
        elif key in {"items", "additionalProperties", "contains", "propertyNames", "not", "if",
                     "then", "else", "unevaluatedProperties", "unevaluatedItems"}:
            yield from schema_nodes(value, (*path, key))


@pytest.mark.parametrize("kind,model", [
    ("world_spec", WorldSpecInput), ("cast_spec", CastSpecInput),
])
def test_wire_schema_is_validation_equivalent_full_model_without_story_annotations(kind, model):
    original = model.model_json_schema(mode="validation")
    before = deepcopy(original)
    contract = planning_submission_contract(kind)
    wire = contract["json_schema"]
    assert contract["wire_format"] == "native_asset_object"
    assert contract["function_name"] == f"submit_{kind}"
    assert contract["contract_version"] == NATIVE_POLICY
    assert contract["schema_sha256"] == sha(dumps(wire))
    assert contract["original_model_schema_sha256"] == sha(dumps(original))
    old_nodes, wire_nodes = dict(schema_nodes(original)), dict(schema_nodes(wire))
    assert old_nodes.keys() == wire_nodes.keys()
    schema_children = {"properties", "$defs", "definitions", "patternProperties",
                       "dependentSchemas",
                       "anyOf", "oneOf", "allOf", "prefixItems", "items", "additionalProperties",
                       "contains", "propertyNames", "not", "if", "then", "else",
                       "unevaluatedProperties", "unevaluatedItems"}
    for path, node in wire_nodes.items():
        assert not ANNOTATIONS & node.keys()
        assert node.keys() == old_nodes[path].keys() - ANNOTATIONS
        for key, value in node.items():
            if key not in schema_children:
                assert value == old_nodes[path][key]
    assert set(wire["$defs"]) == set(original["$defs"])
    assert set(wire["properties"]) == set(original["properties"])
    assert original == before
    tool = planning_submission_tool(kind)["function"]
    assert tool["parameters"] == wire and tool["strict"] is False
    assert wire.get("required") == original.get("required")
    assert "3-quirks-and-a-wound" not in dumps(tool)
    assert "Commercial-novel IP anchors" not in dumps(tool)
    # These are REAL field names, not removable schema annotations.
    if kind == "world_spec":
        rule = wire["$defs"]["WorldRuleInput"]
        assert "description" in rule["properties"]
        assert rule["required"] == ["name", "description"]
        assert wire["$defs"]["HistoryEventInput"]["required"] == ["event"]
    else:
        anchor = wire["$defs"]["CharacterIPAnchorInput"]
        assert {"quirks", "core_wound"} <= anchor["properties"].keys()


@pytest.mark.parametrize("kind,value,tool_hash,receipt_hash", [
    ("book_spec", {"logline": "Retain the old request", "theme_statement": "Cooperation"},
     "d591513b98818dcb8a03925c9726ee6ec30c366dccc4e43da31f12707d61e890",
     "75574234eec7bc795b38648cb76c6a07b70392c8acefbc74df0e74af15ce41ce"),
    ("volume_plan", [{"volume_number": 1, "volume_title": "Observation season"}],
     "49c3ffce13ab2107343a71a156c9b4f0e96c4e88b1e2a02d8a4c7d0a8a5305f7",
     "143c5ca892a4dd00a76b4f59449ea3e8372c512e39e45bccbb65e6ed7a110105"),
])
def test_frozen_book_and_volume_tool_bytes_and_parser_receipts_unchanged(
    kind, value, tool_hash, receipt_hash,
):
    assert sha(dumps(planning_submission_tool(kind), sorted_keys=False)) == tool_hash
    inner = json.dumps(value, ensure_ascii=False)
    c = {"content": "", "tool_calls": [{"id": "unchanged-call", "type": "function", "function": {
        "name": "submit_" + kind,
        "arguments": json.dumps({"asset_json": inner}, ensure_ascii=False),
    }}], "raw_message": None}
    parsed, channel, receipt = parse_planning_submission(c, kind=kind)
    assert parsed == value and channel == "tool_call"
    assert receipt["schema"] == POLICY and sha(dumps(receipt)) == receipt_hash


@pytest.mark.parametrize("kind", ["world_spec", "cast_spec"])
@pytest.mark.parametrize("raw_message", [False, True])
def test_direct_native_preserves_raw_nested_payload_unknown_fields_and_no_defaults(
    kind, raw_message,
):
    value = payload(kind)
    value["unknown_authored_contract"] = {"title": "literal title", "description": "literal detail",
                                          "default": "literal word, not a schema default"}
    c = completion(kind, value, raw_message=raw_message)
    before = deepcopy(c.__dict__)
    result, channel, receipt = parse_planning_submission(c, kind=kind)
    assert result == value and c.__dict__ == before and channel == "tool_call"
    assert receipt["schema"] == NATIVE_POLICY and receipt["kind"] == kind
    assert receipt["raw_arguments"] == c.tool_calls[0]["function"]["arguments"]
    assert receipt["raw_arguments_sha256"] == sha(receipt["raw_arguments"])
    assert receipt["asset_json"] == receipt["raw_arguments"]
    contract = planning_submission_contract(kind)
    for key in ("schema_sha256", "original_model_schema_sha256", "wire_format", "contract_version"):
        assert receipt[key] == contract[key]
    assert "json_schema" not in receipt
    assert receipt["payload_sha256"] == sha(dumps(value))
    assert receipt["tools_executed"] == 0 and not receipt["json_repaired"]
    assert set(result) == set(value)  # No optional/default fields have appeared.
    if kind == "cast_spec":
        assert result["protagonist"] == {"name": "于岚", "role": "supporting"}
    result["unknown_authored_contract"]["title"] = "changed returned value"
    assert c.__dict__ == before


@pytest.mark.parametrize("kind", ["world_spec", "cast_spec"])
@pytest.mark.parametrize("fenced", [False, True])
def test_text_fallback_obeys_the_same_nested_contract_without_repair(kind, fenced):
    raw = json.dumps(payload(kind), ensure_ascii=False)
    if fenced:
        raw = f"```json\n{raw}\n```"
    c = completion(kind, text=True, raw_arguments=raw)
    value, channel, receipt = parse_planning_submission(c, kind=kind)
    assert value == payload(kind) and channel == "text_json"
    assert receipt["original_content"] == raw and receipt["schema"] == NATIVE_POLICY


@pytest.mark.parametrize("kind,invalid", [
    ("world_spec", {"world_name": "Station", "rules": ["a rule is not an object"]}),
    ("world_spec", {"world_name": "Station", "rules": [{"title": "not name", "description": "x"}]}),
    ("world_spec", {"world_name": "Station", "rules": [{"name": "missing description"}]}),
    ("world_spec", {"world_name": "Station", "history_key_events": ["not an event object"]}),
    ("world_spec", {"world_name": "Station", "history_key_events": [{"name": "not event"}]}),
    ("world_spec", {"world_name": "Station", "power_system": "must not coerce into a name"}),
    ("world_spec", {"world_name": "Station", "locations": ["must not coerce to a location"]}),
    ("cast_spec", {"protagonist": "must not coerce to a character"}),
    ("cast_spec", {"protagonist": [{"name": "must not pop a primary character"}]}),
    ("cast_spec", {"protagonist": {"name": "Observer", "age": "20"}}),
    ("cast_spec", {"protagonist": {"name": "Observer"}, "supporting_cast": ["string"]}),
    ("cast_spec", {"protagonist": {"name": "Observer"}, "supporting_cast": [{"goal": "no name"}]}),
    ("cast_spec", {"protagonist": {"name": "Observer"}, "conflict_map": "not an array"}),
])
@pytest.mark.parametrize("text", [False, True])
def test_nested_shape_violations_reject_before_existing_coercers_and_never_change_input(
    kind, invalid, text,
):
    c = completion(kind, invalid, text=text)
    before = deepcopy(c.__dict__)
    with pytest.raises(PlanningSubmissionError) as error:
        parse_planning_submission(c, kind=kind)
    assert error.value.code.startswith("planning_submission:schema_")
    assert c.__dict__ == before
    assert error.value.receipt["schema"] == NATIVE_POLICY
    json.dumps(error.value.receipt, allow_nan=False)


@pytest.mark.parametrize("kind", ["world_spec", "cast_spec"])
@pytest.mark.parametrize("fault", ["scalar", "duplicate", "nonfinite", "raw_conflict", "two_calls",
                                  "wrong_tool", "wrong_kind"])
def test_new_nested_transport_preserves_original_strict_gates(kind, fault):
    c = completion(kind, raw_message=fault == "raw_conflict")
    if fault == "scalar":
        c.tool_calls[0]["function"]["arguments"] = json.dumps({
            "asset_json": json.dumps(payload(kind)),
        })
    elif fault == "duplicate":
        key = "world_name" if kind == "world_spec" else "protagonist"
        value = '"Station"' if kind == "world_spec" else '{"name":"Observer"}'
        c.tool_calls[0]["function"]["arguments"] = f'{{"{key}":{value},"{key}":{value}}}'
    elif fault == "nonfinite":
        raw = json.dumps(payload(kind))
        c.tool_calls[0]["function"]["arguments"] = raw[:-1] + ',"overflow":1e999}'
    elif fault == "raw_conflict":
        c.raw_message["tool_calls"][0]["function"]["arguments"] = "{}"
    elif fault == "two_calls":
        c.tool_calls.append(deepcopy(c.tool_calls[0]))
    elif fault == "wrong_tool":
        c.tool_calls[0]["function"]["name"] = "submit_book_spec"
    else:
        c.tool_calls[0]["function"]["arguments"] = json.dumps({"logline": "wrong asset"})
    with pytest.raises(PlanningSubmissionError) as error:
        parse_planning_submission(c, kind=kind)
    json.dumps(error.value.receipt, allow_nan=False)


def test_contract_and_tool_return_independent_schema_copies():
    first = planning_submission_contract("world_spec")
    first["json_schema"]["$defs"]["WorldRuleInput"]["properties"].clear()
    next_contract = planning_submission_contract("world_spec")
    assert next_contract["json_schema"]["$defs"]["WorldRuleInput"]["properties"]
