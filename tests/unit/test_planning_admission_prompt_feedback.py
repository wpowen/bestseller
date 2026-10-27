"""Feedback is a lossless view of rejected input, never a replacement submission."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError
import pytest

from bestseller.services import planning_draft_admission as admission


def _journal(raw, feedback=None):
    return [
        {
            "status": "protocol_invalid",
            "completion": {
                "content": "auxiliary commentary",
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call",
                        "type": "function",
                        "function": {"name": "submit_planning_admission", "arguments": raw},
                    }
                ],
                "metadata": {"physical": "must remain unchanged"},
            },
            "error": {
                "type": "PlanningAdmissionSubmissionError",
                "detail": "planning_admission_submission:editorial_notes",
                "feedback": feedback if feedback is not None else [],
            },
        }
    ]


def _render(journal):
    original = deepcopy(journal)
    supplied = {"story": {"完整事实": [True, None, "unchanged"]}}
    digest = admission.content_hash(supplied)
    prompt = json.loads(admission._admission_prompt(supplied, digest, journal))
    assert journal == original
    assert prompt["input"] == supplied
    assert prompt["graded_input_hash"] == digest
    return prompt, prompt["protocol_repair"]["previous_attempts"][0]


def test_object_view_retains_invalid_schema_and_physical_journal():
    grade = {
        "draft_eligible": False,
        "hard_violations": [],
        "editorial_notes": "wrong STRING 类型",
        "projection_checks": [],
        "extra": {"authored": {"$ref": "/literal"}, "types": [True, 1, 1.5, None]},
    }
    raw = "\n" + json.dumps(grade, ensure_ascii=False, indent=2) + " \n"
    journal = _journal(
        raw,
        [
            {
                "loc": ["editorial_notes"],
                "code": "schema_invalid",
                "value": grade["editorial_notes"],
                "schema_code": "list_type",
            }
        ],
    )
    prompt, attempt = _render(journal)
    assert attempt["raw_grade"] == grade
    assert isinstance(attempt["raw_grade"]["editorial_notes"], str)
    with pytest.raises(ValidationError):
        admission._Grade.model_validate(attempt["raw_grade"])
    assert journal[0]["completion"]["tool_calls"][0]["function"]["arguments"] == raw
    assert journal[0]["status"] == "protocol_invalid"
    assert attempt["physical_return_sha256"] == admission.content_hash(
        admission._physical_return(journal[0]["completion"])
    )
    assert attempt["error"]["code"] == journal[0]["error"]["detail"]
    assert attempt["error"]["detail"] == [
        {
            "loc": ["editorial_notes"],
            "code": "schema_invalid",
            "schema_code": "list_type",
            "value_ref": "/raw_grade/editorial_notes",
            "value_sha256": admission.content_hash(grade["editorial_notes"]),
        }
    ]
    assert "JSON Pointer" in prompt["protocol_repair"]["instruction"]
    assert "不表示验证通过" in prompt["protocol_repair"]["instruction"]


@pytest.mark.parametrize(
    "raw",
    [
        '{"x":1,"x":2}',
        '{"nested":{"x":1,"x":2}}',
        '{"nested":[{"x":1,"x":1}]}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":-Infinity}',
        '{"x":1e999}',
        '{"x":-1e999}',
        "{broken",
        '[{"editorial_notes":"bad"}]',
        "null",
        "true",
        "42",
        '"text"',
    ],
)
def test_invalid_nonobject_or_ambiguous_arguments_remain_exact_strings(raw):
    detail = [{"loc": ["x"], "code": "invalid", "value": 1}]
    _, attempt = _render(_journal(raw, detail))
    assert attempt["raw_grade"] == raw
    assert attempt["error"]["detail"] == detail


@pytest.mark.parametrize(
    "value",
    [False, None, 7, 1.25, "原文", [1, False, None], {"nested": ["x", {"$ref": "authored"}]}],
)
def test_exact_scalar_array_and_object_values_can_be_referenced(value):
    grade = {"a/b~c": [value]}
    loc = ["a/b~c", 0]
    detail = [{"loc": loc, "code": "rejected", "value": value, "extra": {"keep": True}}]
    _, attempt = _render(_journal(json.dumps(grade, ensure_ascii=False), detail))
    result = attempt["error"]["detail"][0]
    assert result == {
        "loc": loc,
        "code": "rejected",
        "extra": {"keep": True},
        "value_ref": "/raw_grade/a~1b~0c/0",
        "value_sha256": admission.content_hash(value),
    }
    assert attempt["raw_grade"] == grade


@pytest.mark.parametrize(
    "loc,value",
    [
        (["missing"], "v"),
        (["items", -1], "v"),
        (["items", 1], "v"),
        (["items", "0"], "v"),
        (["items", True], "v"),
        (["items", 0.0], "v"),
        (["items", 0, "missing"], "v"),
        (["flag"], 1),
        (["count"], True),
        (["count"], 1.0),
        (["items"], ["different"]),
        (["object"], {"x": 1}),
        ("items/0", "v"),
        (None, "v"),
    ],
)
def test_unresolved_or_type_inexact_feedback_is_preserved(loc, value):
    grade = {"items": ["v"], "flag": True, "count": 1, "object": {"x": True}}
    detail = [{"loc": loc, "value": value, "code": "unchanged"}]
    _, attempt = _render(_journal(json.dumps(grade), detail))
    assert attempt["error"]["detail"] == detail


def test_existing_reference_fields_and_nonstandard_feedback_are_not_overwritten():
    feedback = [
        None,
        "opaque",
        {"loc": ["x"], "code": "no_value"},
        {"loc": ["x"], "value": 1, "value_ref": "authored"},
        {"loc": ["x"], "value": 1, "value_sha256": "authored"},
    ]
    _, attempt = _render(_journal('{"x":1}', feedback))
    assert attempt["error"]["detail"] == feedback
    journal = _journal('{"x":1}')
    journal[0]["error"]["feedback"] = {"opaque": "unchanged"}
    assert _render(journal)[1]["error"]["detail"] == {"opaque": "unchanged"}


def test_no_tool_or_multiple_tools_do_not_decode_commentary_as_submission():
    for calls in ([], None, [{"function": {"arguments": '{"x":1}'}}] * 2):
        journal = _journal('{"x":1}')
        journal[0]["completion"]["tool_calls"] = calls
        journal[0]["completion"]["content"] = '{"x":1}'
        assert _render(journal)[1]["raw_grade"] == '{"x":1}'


def test_empty_journal_does_not_change_initial_prompt():
    source = {"any": [None, 7, "完整source"]}
    assert json.loads(admission._admission_prompt(source, "hash", [])) == {
        "input": source,
        "graded_input_hash": "hash",
    }


def test_actual_retained_rejected_submission_replays_without_mutation():
    root = Path(__file__).resolve().parents[2]
    state_path = (
        root
        / "reports/continuous-recovery-2026-10-16"
        / "workflow-f22ceff1-bd1e-4f1b-82ea-25614239b7af.json"
    )
    request_path = (
        root / "artifacts/runtime/llm_payloads/34faaa4d-0199-40f5-adb2-5c6f0df8e226.request.json"
    )
    if not state_path.exists() or not request_path.exists():
        pytest.skip("operator's retained offline artifacts are not included in checkout")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (state_path, request_path)}
    state = json.loads(state_path.read_text())
    protocol = state["candidates"][-1]["source_admission_protocol"]
    request = json.loads(request_path.read_text())
    logical = json.loads(request["messages"]["user"]["text"])
    # This case replays the first rejected physical submission even after later recovery.
    journal = [deepcopy(protocol["attempts"][0])]
    raw = journal[0]["completion"]["tool_calls"][0]["function"]["arguments"]
    original = deepcopy(journal)
    output = json.loads(
        admission._admission_prompt(logical["input"], protocol["graded_input_hash"], journal)
    )
    assert journal == original
    assert len(journal) == 1 and journal[0]["status"] == "protocol_invalid"
    assert output["input"] == logical["input"]
    assert admission.content_hash(output["input"]) == protocol["graded_input_hash"]
    attempt = output["protocol_repair"]["previous_attempts"][0]
    assert attempt["raw_grade"] == json.loads(raw)
    assert isinstance(attempt["raw_grade"]["editorial_notes"], str)
    with pytest.raises(ValidationError):
        admission._Grade.model_validate(attempt["raw_grade"])
    for issue in attempt["error"]["detail"]:
        assert "value" not in issue
        node = attempt
        for token in issue["value_ref"].split("/")[1:]:
            token = token.replace("~1", "/").replace("~0", "~")
            node = node[int(token)] if isinstance(node, list) else node[token]
        assert admission.content_hash(node) == issue["value_sha256"]
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before


def test_selected_diagnostics_preserve_false_literal_and_every_actual_error_value_without_raw_duplication():
 grade={'draft_eligible':False,'hard_violations':[],'projection_checks':[],'editorial_notes':['unrelated raw diagnostic text '*200]}
 raw=json.dumps(grade,ensure_ascii=False);errors=[{'loc':['projection_checks',0,'related_violation_paths',0],'code':'admission_projection_dependency_invalid','value':'chapters[2]','reason':'dangling','allowed_violation_paths':{'values':['chapters[2].scenes[0]'],'total':1,'omitted':0,'sha256':'exact'}}]
 journal=_journal(raw,errors);before=deepcopy(journal);supplied={'actual_sources':{'unknown_authored':['不可改变否定原文',None,False]}}
 prompt=json.loads(admission._admission_prompt(supplied,admission.content_hash(supplied),journal,diagnostic_only=True));selected=prompt['protocol_repair']['previous_attempts'][0]
 assert selected['prior_draft_eligible_literal']=={'present':True,'value':False}
 assert selected['error']['diagnostics']==errors and selected['raw_submission_sha256']==admission.content_hash(raw)
 assert 'raw_grade' not in selected and 'unrelated raw diagnostic text' not in json.dumps(selected)
 assert journal==before and journal[0]['completion']['tool_calls'][0]['function']['arguments']==raw
 assert prompt['input']==supplied and selected['old_grade_rewritten'] is False
