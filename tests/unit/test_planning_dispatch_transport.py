"""Dispatch transport after reuse: strict sources/suffixes, no DB or model calls."""

from copy import deepcopy
import hashlib
import json

import pytest
from test_source_bound_planning_content import _compile, _project
from test_source_bound_planning_overflow import _build, _large

from bestseller.services import source_bound_planning_content as content
from bestseller.services.planner import _STRUCTURED_FUNCTION_SUFFIX
from bestseller.services.planning_dispatch_transport import prepare_planning_dispatch
from bestseller.services.source_bound_planning_submission import (
    VOLUME_NATIVE_POLICY,
    planning_submission_tool,
)


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _plain(kind="world_spec"):
    project = _project()
    book, world, cast, volumes = _compile(project)
    projection = {
        "book_spec": book,
        "world_spec": world,
        "cast_spec": cast,
        "volume_plan": volumes,
    }[kind]
    system, user = content.build_source_bound_planning_prompts(
        kind=kind,
        project=project,
        premise="Independent observers calibrate their telescope.",
        projection=projection,
        book_spec=book,
        world_spec=world,
        cast_spec=cast,
    )
    tools = [
        planning_submission_tool(
            kind, version=VOLUME_NATIVE_POLICY if kind == "volume_plan" else None
        )
    ]
    return system, user, tools


def _split(user):
    start = len(user) - len(user.lstrip())
    document, end = json.JSONDecoder().raw_decode(user, start)
    return document, user[end:]


@pytest.mark.parametrize("kind", ["book_spec", "world_spec", "cast_spec", "volume_plan"])
def test_actual_default_builder_fitting_dispatch_bytes_and_logical_identity_stay_exact(
    kind, monkeypatch
):
    system, base, tools = _plain(kind)
    user = base + _STRUCTURED_FUNCTION_SUFFIX

    def forbidden(*args, **kwargs):
        raise AssertionError("Fitting requests must not be transformed before/after reuse")

    monkeypatch.setattr(content, "_overflow_planning_prompt", forbidden)
    wire, receipt = prepare_planning_dispatch(system, user, tools=tools, output_tokens=12288)
    assert wire == user and receipt["encoding"] == "identity"
    assert receipt["logical_document_sha256"] == _sha(_json(json.loads(base)))
    assert receipt["base_prompt_sha256"] == receipt["wire_prompt_sha256"]
    assert receipt["base_user_sha256"] == receipt["wire_user_sha256"] == _sha(user)
    assert receipt["suffix_sha256"] == _sha(_STRUCTURED_FUNCTION_SUFFIX)
    assert receipt["capacity"]["output_reserve_tokens"] == 12288
    assert receipt["capacity"]["tool_tokens"] > 0 and receipt["capacity"]["budget_passed"]


def test_noncanonical_json_format_and_suffix_bytes_remain_exact_when_fitting():
    system, base, tools = _plain()
    suffix = _STRUCTURED_FUNCTION_SUFFIX + "\r\nFull repair feedback: {literal data}\t\n"
    user = " \r\n" + json.dumps(json.loads(base), ensure_ascii=False, indent=2) + suffix
    wire, receipt = prepare_planning_dispatch(system, user, tools=tools, output_tokens=12288)
    assert wire.encode() == user.encode() and receipt["encoding"] == "identity"
    assert receipt["suffix_characters"] == len(suffix)


def test_overflow_encodes_entire_logical_document_and_never_changes_suffix_or_source_values():
    system, base, tools = _plain()
    logical = json.loads(base)
    logical["unknown_authored_archive"] = ["古典记录🌕 exact unknown values and tail. " * 200] * 40
    logical["unknown_authored_refs"] = [{"$ref": 0}, {"$ref": "#/literal"}]
    original = deepcopy(logical)
    suffix = _STRUCTURED_FUNCTION_SUFFIX + "\r\nFULL DIAGNOSTICS:\nkeep every source and issue.\t"
    user = _json(logical) + suffix
    with pytest.raises(content.SourceBoundPlanningCapacityError):
        content.assert_source_bound_planning_request_capacity(
            system, user, tools=tools, output_tokens=12288
        )
    wire, receipt = prepare_planning_dispatch(system, user, tools=tools, output_tokens=12288)
    document, actual_suffix = _split(wire)
    assert receipt["encoding"] != "identity"
    assert actual_suffix == suffix and wire.endswith(suffix)
    assert _json(content.decode_source_bound_planning_document(document)) == _json(original)
    assert document["current_output_contract"] == original["current_output_contract"]
    assert list(document)[-1] == "current_output_contract"
    assert receipt["logical_document_sha256"] == _sha(_json(original))
    assert receipt["base_user_sha256"] == _sha(user) and receipt["wire_user_sha256"] == _sha(wire)
    assert receipt["roundtrip_verified"] and receipt["capacity"]["budget_passed"]
    assert logical == original
    assert (wire, receipt) == prepare_planning_dispatch(
        system, user, tools=tools, output_tokens=12288
    )


def test_already_encoded_default_char_overflow_root_and_retry_suffix_are_supported():
    project, assets, projection = _large()
    system, base = _build(project, assets, projection)
    original = content.decode_source_bound_planning_document(json.loads(base))
    assert json.loads(base)["schema"] == content.OVERFLOW_SCHEMA
    tools = [planning_submission_tool("volume_plan", version=VOLUME_NATIVE_POLICY)]
    suffix = (
        _STRUCTURED_FUNCTION_SUFFIX + "\nFULL RETRY: preserve exact canonical and all findings."
    )
    wire, receipt = prepare_planning_dispatch(
        system, base + suffix, tools=tools, output_tokens=12288
    )
    document, actual_suffix = _split(wire)
    assert actual_suffix == suffix
    assert _json(content.decode_source_bound_planning_document(document)) == _json(original)
    assert receipt["logical_document_sha256"] == _sha(_json(original))
    # A second dispatch remeasures its encoded input; it does not nest wrappers.
    retry = "\nSECOND COMPLETE FEEDBACK: do not omit sources."
    next_wire, next_receipt = prepare_planning_dispatch(
        system, wire + retry, tools=tools, output_tokens=12288
    )
    next_document, next_suffix = _split(next_wire)
    assert next_suffix == suffix + retry
    assert _json(content.decode_source_bound_planning_document(next_document)) == _json(original)
    assert next_receipt["logical_document_sha256"] == receipt["logical_document_sha256"]


@pytest.mark.parametrize(
    "raw",
    [
        '{"protocol":1,"protocol":2}',
        '{"x":NaN}',
        '{"x":1e999}',
        '{"x":Infinity}',
        '{"unterminated":',
        '[{"not":"document"}]',
        '{"unknown":"document"}',
        '{}\n{"second":"document"}',
        "```json\n{}\n```",
        'prefix before {"x":1}',
    ],
)
def test_malformed_duplicate_nonfinite_or_unknown_json_never_salvages_or_unwraps(raw):
    with pytest.raises(ValueError):
        prepare_planning_dispatch("System", raw, tools=None, output_tokens=8192)


def test_changed_overflow_contract_is_rejected_even_if_request_fits():
    project, assets, projection = _large()
    system, user = _build(project, assets, projection)
    wrapper = json.loads(user)
    wrapper["current_output_contract"]["kind"] = "other-asset"
    with pytest.raises(ValueError, match="overflow_contract_invalid"):
        prepare_planning_dispatch(
            system, _json(wrapper) + _STRUCTURED_FUNCTION_SUFFIX, tools=None, output_tokens=8192
        )


@pytest.mark.parametrize(
    "fault",
    [
        "empty_contract",
        "unknown_kind",
        "kind_type",
        "contract_kind",
        "function",
        "native_type",
        "root",
    ],
)
def test_plain_known_protocol_requires_consistent_actual_kind_contract(fault):
    system, base, tools = _plain()
    logical = json.loads(base)
    contract = logical["current_output_contract"]
    if fault == "empty_contract":
        logical["current_output_contract"] = {}
    elif fault == "unknown_kind":
        logical["kind"] = "another-asset"
    elif fault == "kind_type":
        logical["kind"] = []
    elif fault == "contract_kind":
        contract["kind"] = "cast_spec"
    elif fault == "function":
        contract["native_submission"]["function_name"] = "submit_cast_spec"
    elif fault == "native_type":
        contract["native_submission"] = []
    else:
        contract["expected_root"] = "array"
    with pytest.raises(ValueError, match="unknown_document"):
        prepare_planning_dispatch(
            system, _json(logical) + _STRUCTURED_FUNCTION_SUFFIX, tools=tools, output_tokens=12288
        )


@pytest.mark.parametrize("side", ["tools", "suffix", "output"])
def test_full_request_budget_failure_remains_failure_without_elision_or_budget_increase(side):
    system, base, tools = _plain()
    suffix = _STRUCTURED_FUNCTION_SUFFIX
    output = 12288
    if side == "tools":
        tools = [{"type": "function", "function": {"parameters": {"description": "源" * 90000}}}]
    elif side == "suffix":
        suffix += "\nFULL RETRY FEEDBACK:" + "源" * 120001
    else:
        output = 65536
    user = base + suffix
    with pytest.raises(content.SourceBoundPlanningCapacityError):
        prepare_planning_dispatch(system, user, tools=tools, output_tokens=output)
    assert user == base + suffix


def test_overflow_roundtrip_guard_rejects_dropped_unknown_field(monkeypatch):
    system, base, tools = _plain()
    logical = json.loads(base)
    logical["unknown_authored_archive"] = ["原样保留的旧记录。" * 200] * 80
    user = _json(logical) + _STRUCTURED_FUNCTION_SUFFIX

    def corrupt(system, complete, **kwargs):
        damaged = deepcopy(complete)
        damaged.pop("unknown_authored_archive")
        return _json(damaged)

    monkeypatch.setattr(content, "_overflow_planning_prompt", corrupt)
    with pytest.raises(ValueError, match="roundtrip_mismatch"):
        prepare_planning_dispatch(system, user, tools=tools, output_tokens=12288)
