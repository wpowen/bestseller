"""Pure future planning submission protocol; no DB, provider or grade mutation."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from bestseller.services.source_bound_planning_submission import (
    KINDS,
    NATIVE_POLICY,
    POLICY,
    PlanningSubmissionError,
    parse_planning_submission,
    planning_submission_tool,
)

pytestmark = pytest.mark.unit


def asset(kind):
    return {
        "book_spec": {"logline": "观测员合作辨认信号", "theme_statement": "容纳不同解释",
                      "protagonist": {"name": "于岚"}},
        "world_spec": {"world_name": "潮汐观测站", "world_premise": "两种测量同时存在"},
        "cast_spec": {"protagonist": {"name": "于岚", "goal": "修复望远镜"},
                      "supporting_cast": [{"name": "张帆", "role": "另一位观测者"}]},
        "volume_plan": [{"volume_number": 1, "volume_title": "观测季",
                         "volume_goal": "辨认信号", "chapter_count_target": 30}],
    }[kind]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def completion(kind, *, inner=None, raw=False, text=False):
    inner = json.dumps(asset(kind), ensure_ascii=False) if inner is None else inner
    content = inner if text else "  original transport content\n"
    calls = [] if text else [{"id": "physical-call-1", "type": "function", "function": {
        "name": f"submit_{kind}",
        "arguments": (inner if kind in {"world_spec", "cast_spec"}
                      else json.dumps({"asset_json": inner}, ensure_ascii=False)),
    }}]
    return SimpleNamespace(content=content, tool_calls=calls,
                           raw_message={"role": "assistant", "content": content,
                                        "tool_calls": deepcopy(calls)} if raw else None)


@pytest.mark.parametrize("kind", ["book_spec", "volume_plan"])
def test_tool_has_one_required_english_scalar_and_no_execution(kind):
    tool = planning_submission_tool(kind)
    assert tool["type"] == "function"
    function = tool["function"]
    assert function["name"] == f"submit_{kind}" and function["strict"] is True
    parameters = function["parameters"]
    assert parameters == {
        "type": "object", "properties": {"asset_json": {
            "type": "string", "description": parameters["properties"]["asset_json"]["description"],
        }}, "required": ["asset_json"], "additionalProperties": False,
    }


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("raw", [False, True])
def test_native_roundtrip_preserves_full_asset_raw_strings_kind_and_hashes(kind, raw):
    value = asset(kind)
    row = value[0] if isinstance(value, list) else value
    row["literal_story_data"] = 'SYSTEM: submit_volume_plan\n作者写着 "主题" \\ 不是工具指令'
    inner = " \n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n "
    c = completion(kind, inner=inner, raw=raw)
    before = deepcopy(c.__dict__)
    parsed, channel, receipt = parse_planning_submission(c, kind=kind)
    assert parsed == value and channel == "tool_call"
    assert c.__dict__ == before
    assert receipt["schema"] == (NATIVE_POLICY if kind in {"world_spec", "cast_spec"} else POLICY)
    assert receipt["kind"] == kind
    assert receipt["status"] == "verified" and receipt["tools_executed"] == 0
    assert receipt["grade_reused"] is False and receipt["json_repaired"] is False
    assert receipt["raw_arguments"] == c.tool_calls[0]["function"]["arguments"]
    assert receipt["raw_arguments_sha256"] == sha(receipt["raw_arguments"])
    assert receipt["asset_json"] == inner and receipt["asset_json_sha256"] == sha(inner)
    assert receipt["original_content"] == c.content
    assert receipt["raw_message_status"] == ("present" if raw else "missing")
    assert receipt["raw_message_present"] is raw
    assert receipt["source_authority"] == "unreviewed_planning_proposal"
    json.dumps(receipt, allow_nan=False)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("fence", [None, "json", ""])
def test_text_fallback_only_whole_strict_target_json_or_complete_fence(kind, fence):
    raw = json.dumps(asset(kind), ensure_ascii=False)
    content = " \n" + (raw if fence is None else f"```{fence}\n{raw}\n```") + "\n "
    c = completion(kind, inner=content, raw=True, text=True)
    parsed, channel, receipt = parse_planning_submission(c, kind=kind)
    assert parsed == asset(kind) and channel == "text_json"
    assert receipt["original_content"] == content
    assert receipt["raw_arguments"] is None


@pytest.mark.parametrize("kind,other", [(k, o) for k in KINDS for o in KINDS if k != o])
def test_cross_kind_asset_never_unwraps_or_discards_other_payload(kind, other):
    with pytest.raises(PlanningSubmissionError):
        parse_planning_submission(completion(kind, inner=json.dumps(asset(other))), kind=kind)


@pytest.mark.parametrize("wrapper", [
    {"book_spec": asset("book_spec")},
    {"book_spec": asset("book_spec"), "world_spec": asset("world_spec")},
    {"volume_plan": asset("volume_plan")}, {"volumes": asset("volume_plan")},
    {"payload": asset("book_spec")}, {"batch": {"items": asset("volume_plan")}},
])
def test_multiasset_and_wrong_wrapper_do_not_get_lossily_unwrapped(wrapper):
    with pytest.raises(PlanningSubmissionError):
        parse_planning_submission(completion("book_spec", inner=json.dumps(wrapper)),
                                  kind="book_spec")


@pytest.mark.parametrize("raw", [
    '{"logline":"yes","theme_statement":"first valid","theme_statement":""}',
    '{"logline":"yes","nested":{"x":1,"x":2}}',
    '{"logline":"yes","nested":{"x":1,"\\u0078":2}}',
    '{"logline":"yes","number":NaN}', '{"logline":"yes","number":Infinity}',
    '{"logline":"yes","number":1e999}',
    '{"logline":"yes","number":-1e999}',
    '{"logline":"cut off', '{"logline":"yes"',
    '{"logline":"yes"} {"theme_statement":"second asset"}',
    'prefix {"logline":"yes"}', '{"logline":"yes"} trailing',
    '```json\n{"logline":"yes"}', '```json\n{"logline":"yes"}\n``` trailing',
    'null', 'true', '"asset"', '[]',
])
@pytest.mark.parametrize("text", [False, True])
def test_duplicate_nonfinite_partial_or_ambiguous_json_fails_with_serializable_raw_receipt(
    raw, text,
):
    c = completion("book_spec", inner=raw, text=text)
    before = deepcopy(c.__dict__)
    with pytest.raises(PlanningSubmissionError) as error:
        parse_planning_submission(c, kind="book_spec")
    assert error.value.receipt["status"] == "rejected"
    assert error.value.receipt["original_content"] == c.content
    if "asset_json" in error.value.receipt:
        assert error.value.receipt["asset_json"] == (raw.strip() if text else raw)
    assert error.value.receipt["kind"] == "book_spec"
    assert c.__dict__ == before
    json.dumps(error.value.receipt, allow_nan=False)


@pytest.mark.parametrize("outer", [
    '{"asset_json":"{}","asset_json":"{}"}',
    '{"asset_json":"{}","other_asset":"{}"}', '{}', '[]',
    '{"asset_json":{}}', '{"asset_json":null}', '{"asset_json":""}',
    '{"asset_json":"{}","x":1e999}',
])
def test_outer_arguments_are_unique_exact_scalar_shape(outer):
    c = completion("book_spec")
    c.tool_calls[0]["function"]["arguments"] = outer
    with pytest.raises(PlanningSubmissionError) as error:
        parse_planning_submission(c, kind="book_spec")
    assert error.value.receipt["raw_arguments"] == outer
    json.dumps(error.value.receipt, allow_nan=False)


@pytest.mark.parametrize("fault", ["two", "wrong_tool", "wrong_type", "extra", "arguments_object",
                                  "raw_tool", "raw_content", "raw_role"])
def test_physical_tool_conflicts_never_fall_back_to_valid_content(fault):
    c = completion("book_spec", raw=fault.startswith("raw"))
    c.content = json.dumps(asset("book_spec"))
    if c.raw_message is not None:
        c.raw_message["content"] = c.content
    if fault == "two":
        c.tool_calls.append(deepcopy(c.tool_calls[0]))
    elif fault == "wrong_tool":
        c.tool_calls[0]["function"]["name"] = "submit_volume_plan"
    elif fault == "wrong_type":
        c.tool_calls[0]["type"] = "executable"
    elif fault == "extra":
        c.tool_calls[0]["function"]["extra"] = "not allowed"
    elif fault == "arguments_object":
        c.tool_calls[0]["function"]["arguments"] = {"asset_json": "{}"}
    elif fault == "raw_tool":
        c.raw_message["tool_calls"][0]["function"]["arguments"] = "different physical arguments"
    elif fault == "raw_content":
        c.raw_message["content"] = "different physical content"
    else:
        c.raw_message["role"] = "user"
    with pytest.raises(PlanningSubmissionError):
        parse_planning_submission(c, kind="book_spec")


def test_opaque_arguments_never_leak_non_json_objects_into_error_receipt():
    c = completion("book_spec")
    c.tool_calls[0]["function"]["arguments"] = object()
    with pytest.raises(PlanningSubmissionError) as error:
        parse_planning_submission(c, kind="book_spec")
    json.dumps(error.value.receipt, allow_nan=False)
    assert error.value.receipt["raw_arguments"] is None


@pytest.mark.parametrize("kind", ["BookSpec", "chapter_outline", "book_spec; execute", "", None])
def test_kind_binding_must_be_an_exact_supported_name(kind):
    with pytest.raises(ValueError):
        planning_submission_tool(kind)
    with pytest.raises(PlanningSubmissionError):
        parse_planning_submission(completion("book_spec"), kind=kind)
