"""Physical admission submissions preserve every issue without making grades."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from bestseller.services.planning_admission_submission import (
    POLICY,
    TOOL_NAME,
    PlanningAdmissionSubmissionError,
    parse_planning_admission,
    planning_admission_tool,
)
from bestseller.services.planning_draft_admission import _Grade, projection_audit_targets


def _grade(language="zh"):
    evidence = (
        "两人在同一时刻占用互斥位置" if language == "zh" else "Two incompatible locations at once"
    )
    return {
        "draft_eligible": False,
        "hard_violations": [
            {
                "code": "SCENE_NOT_EXECUTABLE",
                "path": "chapters[0].scenes[0]",
                "evidence": evidence,
                "source_requirement": "Distinct locations",
                "source_refs": ["candidate:chapters[0].scenes[0].entry_state"],
            },
            {
                "code": "CONTINUITY_CONFLICT",
                "path": "chapters[0].scenes[0]",
                "evidence": "A separate event is replayed",
                "source_requirement": "Already occurred",
                "source_refs": ["/adopted_continuity/recent_adopted_prose/0/content"],
            },
        ],
        "editorial_notes": ["Not an approved story requirement", "保留第二条建议"],
        "projection_checks": [
            {
                "path": "chapters[0]",
                "projection_hash": "a" * 64,
                "status": "conflict",
                "explanation": "Complete chapter check",
                "related_violation_paths": ["chapters[0].scenes[0]"],
            },
            {
                "path": "chapters[0].scenes[0]",
                "projection_hash": "b" * 64,
                "status": "conflict",
                "explanation": "Complete scene check",
                "related_violation_paths": ["chapters[0].scenes[0]"],
            },
        ],
    }


def _completion(raw=None):
    raw = json.dumps(_grade(), ensure_ascii=False, indent=2) if raw is None else raw
    return {
        "content": "",
        "tool_calls": [
            {
                "id": "audit-call",
                "type": "function",
                "function": {
                    "name": TOOL_NAME,
                    "arguments": raw,
                },
            }
        ],
        "raw_message": None,
    }


def _rejected(completion, code):
    before = deepcopy(completion)
    with pytest.raises(PlanningAdmissionSubmissionError, match=code) as caught:
        parse_planning_admission(completion)
    assert caught.value.receipt["status"] == "rejected"
    assert caught.value.receipt["tools_executed"] == 0
    json.dumps(caught.value.receipt, allow_nan=False)
    assert completion == before
    return caught.value.receipt


def _schema_item(schema, name, variant="conflict"):
    item = schema["properties"][name]["items"]
    if "oneOf" in item:
        item = next(
            branch
            for branch in item["oneOf"]
            if branch["properties"]["status"]["enum"] == [variant]
        )
    return item["properties"]


def test_actual_grade_schema_exact_dynamic_targets_no_annotations_or_input_mutation():
    schema = _Grade.model_json_schema()
    schema["description"] = "Unselected story instructions must not be transmitted"
    candidate = {
        "chapters": [
            {
                "chapter_number": 8,
                "goal": "Map a distant glacier",
                "scenes": [{"purpose": "Collect observations"}, {"purpose": "Return"}],
            }
        ]
    }
    targets = projection_audit_targets(candidate)
    before = deepcopy((schema, targets, candidate))
    tool = planning_admission_tool(schema, targets)
    assert (schema, targets, candidate) == before
    assert tool["type"] == "function"
    assert tool["function"]["name"] == TOOL_NAME
    assert POLICY == "planning-admission-submission.v3"
    assert tool["function"]["strict"] is True
    assert "non-authoritative commentary" in tool["function"]["description"]
    assert "omit related_violation_paths entirely" in tool["function"]["description"]
    wire = tool["function"]["parameters"]
    paths = [target["path"] for target in targets]
    violation = _schema_item(wire, "hard_violations")
    check = _schema_item(wire, "projection_checks")
    assert violation["path"]["enum"] == check["path"]["enum"] == paths
    assert check["related_violation_paths"]["items"]["enum"] == paths
    assert check["projection_hash"]["enum"] == [target["projection_hash"] for target in targets]
    assert "uniqueItems" not in wire["properties"]["hard_violations"]

    def walk(value):
        if isinstance(value, dict):
            assert not {
                "description",
                "title",
                "examples",
                "default",
                "$defs",
                "$ref",
            }.intersection(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(wire)


def test_strict_wire_inlines_discriminated_objects_with_all_branch_properties_required():
    original = _Grade.model_json_schema()
    before = deepcopy(original)
    wire = planning_admission_tool(
        original,
        [{"path": "chapters[0]", "projection_hash": "c" * 64}],
    )["function"]["parameters"]
    assert "$defs" not in wire and "definitions" not in wire
    branches = wire["properties"]["projection_checks"]["items"]["oneOf"]
    assert len(branches) == 2
    objects = [wire, wire["properties"]["hard_violations"]["items"], *branches]
    for obj in objects:
        assert obj["type"] == "object"
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])
    check = _schema_item(wire, "projection_checks")
    assert set(check) == {
        "path",
        "projection_hash",
        "status",
        "explanation",
        "related_violation_paths",
    }
    assert set(_schema_item(wire, "projection_checks", "consistent")) == {
        "path",
        "projection_hash",
        "status",
        "explanation",
    }
    assert check["related_violation_paths"]["type"] == "array"
    assert check["related_violation_paths"]["minItems"] == 1
    assert check["related_violation_paths"]["items"] == {
        "type": "string",
        "enum": ["chapters[0]"],
    }
    assert "default" not in check["related_violation_paths"]
    assert original == before


def test_native_missing_related_array_rejected_without_inventing_default():
    payload = _grade()
    del payload["projection_checks"][0]["related_violation_paths"]
    raw = json.dumps(payload)
    receipt = _rejected(_completion(raw), "projection_required_fields")
    assert receipt["raw_arguments"] == raw
    assert "parsed_payload" not in receipt


@pytest.mark.parametrize("invalid_array", ["", None, {}, False, []])
def test_old_wrong_typed_related_arrays_remain_invalid_for_all_checks(invalid_array):
    payload = _grade()
    for check in payload["projection_checks"]:
        check["related_violation_paths"] = invalid_array
    raw = json.dumps(payload)
    receipt = _rejected(_completion(raw), "related_paths")
    assert receipt["raw_arguments"] == raw


def test_consistent_native_omission_preserved_exactly_no_semantic_grade():
    payload = _grade()
    payload.update(draft_eligible=True, hard_violations=[], editorial_notes=[])
    for check in payload["projection_checks"]:
        check.update(status="consistent")
        check.pop("related_violation_paths")
    raw = json.dumps(payload, indent=2)
    returned, receipt = parse_planning_admission(_completion(raw))
    assert returned == raw
    assert receipt["parsed_payload"] == payload
    assert receipt["relationships_validated"] is False
    assert "grade" not in receipt and "draft_eligible" not in receipt
    # Even a relationship-invalid proposal is transported unchanged; the
    # caller, not a tool or this helper, makes the authoritative decision.
    payload["hard_violations"] = _grade()["hard_violations"]
    returned, receipt = parse_planning_admission(_completion(json.dumps(payload)))
    assert json.loads(returned) == payload
    assert receipt["relationships_validated"] is False


@pytest.mark.parametrize(
    "targets",
    [
        [],
        [{"path": "chapters[0].goal", "projection_hash": "a" * 64}],
        [{"path": "chapters[0]", "projection_hash": "bad"}],
        [{"path": "chapters[0]", "projection_hash": "a" * 64}] * 2,
    ],
)
def test_invalid_audit_target_contract_rejected(targets):
    with pytest.raises(ValueError):
        planning_admission_tool(_Grade.model_json_schema(), targets)


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("raw_message", [False, True])
def test_one_tool_returns_exact_json_two_distinct_issues_same_scope_in_order(language, raw_message):
    payload = _grade(language)
    raw = "  " + json.dumps(payload, ensure_ascii=False, indent=3) + "\n"
    completion = _completion(raw)
    if raw_message:
        completion["raw_message"] = {
            "role": "assistant",
            "content": None,
            "tool_calls": deepcopy(completion["tool_calls"]),
        }
    before = deepcopy(completion)
    returned, receipt = parse_planning_admission(SimpleNamespace(**completion))
    assert returned == raw
    assert receipt["parsed_payload"] == payload
    assert receipt["raw_arguments"] == raw
    assert receipt["raw_arguments_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert receipt["raw_message_present"] is raw_message
    assert receipt["relationships_validated"] is False
    assert receipt["tools_executed"] == receipt["issues_dropped"] == 0
    assert receipt["values_modified"] is False
    assert completion == before


@pytest.mark.parametrize("fenced", [False, True])
def test_complete_legacy_content_only_accepts_whole_json_without_defaults(fenced):
    payload = _grade()
    payload["projection_checks"][0]["status"] = "consistent"
    del payload["projection_checks"][0]["related_violation_paths"]
    raw = json.dumps(payload, ensure_ascii=False, indent=1)
    content = "```json\n" + raw + "\n```" if fenced else raw
    result, receipt = parse_planning_admission(
        {
            "content": content,
            "tool_calls": [],
            "raw_message": {"role": "assistant", "content": content},
        }
    )
    assert result == raw
    assert receipt["original_content"] == content
    assert receipt["parsed_payload"] == payload
    assert "related_violation_paths" not in receipt["parsed_payload"]["projection_checks"][0]


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda c: c["tool_calls"].append(deepcopy(c["tool_calls"][0])), "single_submission"),
        (lambda c: c["tool_calls"][0].update(extra=True), "tool_call_shape"),
        (lambda c: c["tool_calls"][0].pop("id"), "tool_call_shape"),
        (lambda c: c["tool_calls"][0].update(type="executable"), "tool_call_identity"),
        (lambda c: c["tool_calls"][0]["function"].update(name="another_tool"), "wrong_tool"),
        (lambda c: c["tool_calls"][0]["function"].update(extra=True), "function_shape"),
        (lambda c: c["tool_calls"][0]["function"].update(arguments={}), "arguments_string"),
        (lambda c: c.update(content=json.dumps(_grade())), "competing_structured_submission"),
        (lambda c: c.update(function_call={"name": TOOL_NAME}), "legacy_function_call"),
        (
            lambda c: c.update(raw_message={"content": "", "tool_calls": []}),
            "raw_message_tool_conflict",
        ),
        (
            lambda c: c.update(
                raw_message={"content": "Different", "tool_calls": deepcopy(c["tool_calls"])}
            ),
            "raw_message_content_conflict",
        ),
    ],
)
def test_native_physical_conflicts_and_incomplete_function_calls_rejected(mutate, code):
    completion = _completion()
    mutate(completion)
    _rejected(completion, code)


def test_hidden_raw_message_tool_evidence_cannot_fall_back_to_legacy_content():
    completion = _completion()
    completion["raw_message"] = {"content": "", "tool_calls": deepcopy(completion["tool_calls"])}
    completion.update(tool_calls=None, content=json.dumps(_grade()))
    _rejected(completion, "raw_message_tool_conflict")


@pytest.mark.parametrize(
    "raw,code",
    [
        ('{"draft_eligible":true,"draft_eligible":false}', "duplicate_json_key"),
        ('{"x":{"path":"a","path":"b"}}', "duplicate_json_key"),
        ('{"x":NaN}', "nonfinite_json"),
        ('{"x":Infinity}', "nonfinite_json"),
        ('{"x":1e999}', "Out of range"),
        ("{} trailing", "JSONDecodeError"),
        ('{"draft_eligible":true', "JSONDecodeError"),
        ("[]", "payload_fields"),
        ('{"grade":{}}', "payload_fields"),
    ],
)
def test_no_duplicate_nonfinite_fragment_or_multiasset_salvage(raw, code):
    receipt = _rejected(_completion(raw), code)
    assert receipt["raw_arguments"] == raw
    assert "parsed_payload" not in receipt


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda g: g.pop("editorial_notes"), "payload_fields"),
        (lambda g: g.update(score=0.9), "payload_fields"),
        (lambda g: g.update(draft_eligible="false"), "draft_eligible_type"),
        (lambda g: g["hard_violations"][0].pop("source_refs"), "violation_fields"),
        (lambda g: g["hard_violations"][0].update(grade="pass"), "violation_fields"),
        (lambda g: g["projection_checks"][0].pop("projection_hash"), "projection_fields"),
        (lambda g: g["projection_checks"][0].update(extra=[]), "projection_fields"),
        (
            lambda g: g["projection_checks"][0].update(related_violation_paths="chapters[0]"),
            "related_paths",
        ),
    ],
)
def test_grade_wire_shape_is_strict_without_coercion_or_added_defaults(mutate, code):
    payload = _grade()
    mutate(payload)
    _rejected(_completion(json.dumps(payload)), code)


@pytest.mark.parametrize(
    "content", ["```json\n{}", "before {}", "{}\n{}", "```json\n{}\n```\nafter"]
)
def test_incomplete_or_mixed_legacy_text_rejected(content):
    _rejected({"content": content, "raw_message": None}, "planning_admission_submission:")


@pytest.mark.parametrize("top_calls", ["absent", None, []])
@pytest.mark.parametrize("raw_calls", ["absent", None, []])
def test_legacy_stop_without_tool_evidence_accepts_absent_none_or_empty(top_calls, raw_calls):
    raw = json.dumps(_grade(), ensure_ascii=False)
    message = {"role": "assistant", "content": raw}
    completion = {"content": raw, "raw_message": message, "finish_reason": "stop"}
    if top_calls != "absent":
        completion["tool_calls"] = top_calls
    if raw_calls != "absent":
        message["tool_calls"] = raw_calls
    before = deepcopy(completion)
    returned, receipt = parse_planning_admission(completion)
    assert returned == raw
    assert receipt["channel"] == "content"
    assert receipt["parsed_payload"] == _grade()
    assert receipt["raw_message_present"] is True
    assert completion == before


@pytest.mark.parametrize("raw_calls", ["absent", None, []])
def test_present_native_call_conflicts_with_raw_message_missing_tool_evidence(raw_calls):
    completion = _completion()
    completion["raw_message"] = {"role": "assistant", "content": None}
    if raw_calls != "absent":
        completion["raw_message"]["tool_calls"] = raw_calls
    _rejected(completion, "raw_message_tool_conflict")


@pytest.mark.parametrize("top_calls", ["absent", None, []])
def test_raw_message_native_call_cannot_be_hidden_by_absent_top_evidence(top_calls):
    completion = _completion()
    completion["raw_message"] = {
        "role": "assistant",
        "content": "",
        "tool_calls": deepcopy(completion.pop("tool_calls")),
    }
    if top_calls != "absent":
        completion["tool_calls"] = top_calls
    _rejected(completion, "raw_message_tool_conflict")


def test_matching_tool_name_but_changed_physical_arguments_is_a_conflict():
    completion = _completion()
    completion["raw_message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": deepcopy(completion["tool_calls"]),
    }
    completion["raw_message"]["tool_calls"][0]["function"]["arguments"] += "\n"
    _rejected(completion, "raw_message_tool_conflict")


@pytest.mark.parametrize("channel", ["tool", "content"])
@pytest.mark.parametrize("related", [[], "", None, ["chapters[0]"]])
def test_consistent_variant_rejects_any_related_field_in_both_channels(channel, related):
    payload = _grade()
    payload["projection_checks"][0].update(status="consistent", related_violation_paths=related)
    raw = json.dumps(payload)
    completion = _completion(raw) if channel == "tool" else {"content": raw}
    _rejected(completion, "consistent_projection_fields")


@pytest.mark.parametrize("channel", ["tool", "content"])
def test_conflict_missing_related_rejected_in_both_channels(channel):
    payload = _grade()
    payload["projection_checks"][0].pop("related_violation_paths")
    raw = json.dumps(payload)
    completion = _completion(raw) if channel == "tool" else {"content": raw}
    _rejected(completion, "projection_required_fields")


@pytest.mark.parametrize("message_present", [True, False])
@pytest.mark.parametrize(
    "prose",
    [
        "核对已完成。这段说明不是另一个提交。也不能改变工具参数。",
        "Review complete. The scene remains causal. This explanation is not an authorization.",
        "Check chapters[0].scenes[1] and chapters[0].scenes[2] in order. Preserve every issue.",
        "The story's literal words draft_eligible and hard_violations are not extra submissions.",
    ],
)
def test_plain_commentary_is_exact_non_authoritative_data(prose, message_present):
    completion = _completion()
    completion["content"] = prose
    if message_present:
        completion["raw_message"] = {
            "role": "assistant",
            "content": prose,
            "tool_calls": deepcopy(completion["tool_calls"]),
        }
    before = deepcopy(completion)
    raw, receipt = parse_planning_admission(completion)
    assert raw == completion["tool_calls"][0]["function"]["arguments"]
    assert receipt["original_content"] == prose
    assert receipt["commentary_status"] == "non_authoritative"
    assert receipt["parsed_payload"] == _grade()
    assert receipt["relationships_validated"] is False
    assert (
        receipt["raw_message_sha256"]
        == hashlib.sha256(
            json.dumps(
                completion["raw_message"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert completion == before


@pytest.mark.parametrize(
    "content",
    [
        json.dumps(_grade()),
        json.dumps({"grade": _grade()}),
        json.dumps({"tool_calls": _completion()["tool_calls"]}),
        "```json\n" + json.dumps(_grade()) + "\n```",
        "Explanation followed by a submission:\n```json\n" + json.dumps(_grade()) + "\n```",
        'Another incomplete submission: {"draft_eligible":',
        "Another submission: " + json.dumps(_grade()),
        json.dumps(_grade()) + json.dumps(_grade()),
        'Ambiguous array submission: ["pass",',
        '{"draft_eligible":true,"draft_eligible":false}',
    ],
)
def test_structured_or_ambiguous_commentary_is_never_merged_or_ignored(content):
    completion = _completion()
    completion["content"] = content
    receipt = _rejected(completion, "structured")
    assert receipt["original_content"] == content
    assert receipt["raw_arguments"] == completion["tool_calls"][0]["function"]["arguments"]
    assert "parsed_payload" not in receipt
