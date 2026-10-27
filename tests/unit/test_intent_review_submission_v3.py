"""Offline v3 contract checks. Synthetic origin is not paid or literary acceptance."""

# ruff: noqa: RUF001

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.services import intent_review_submission as review
from bestseller.services import llm
from bestseller.settings import LLMRoleSettings

pytestmark = pytest.mark.unit
ROLE = LLMRoleSettings(
    model="minimax/offline-test", max_tokens=8192, temperature=0.2, timeout_seconds=30
)
TAG = "explicit direction"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_for(**overrides):
    args = {
        "candidate": {
            "premise": "A courier gains entry by rescuing a rival.",
            "synopsis": "The rescued rival opens the gate.",
            "story_spine": {"goal": "Enter the city", "a/b~c": ["Earn recognition"]},
        },
        "user_controls": {
            "genre_intent_contract": {"tag_source": "user", "user_tags": [TAG]},
        },
        "intent_tags": [TAG],
        "genre_label": "adventure",
        "workflow_run_id": uuid4(),
    }
    args.update(overrides)
    return review.build_intent_review_request(**args)


def payload(request, verdict="pass"):
    source = json.loads(request.user_prompt)
    ref = {k: source["catalog"][0][k] for k in ("source_path", "anchor_id")}
    return {
        "items": {
            TAG: {
                "verdict": verdict,
                "reason": "Current structure supports the direction.",
                "evidence": [ref],
            },
        },
        "counter_elements": (
            [{**ref, "against": TAG, "reason": "Structural contradiction."}]
            if verdict == "fail"
            else []
        ),
        "revise_direction": "",
        "cost_verdict": "not_requested",
    }


def completion(request, data=None, *, inner=None, outer=None):
    if inner is None:
        inner = json.dumps(data if data is not None else payload(request), ensure_ascii=False)
    if outer is None:
        outer = json.dumps({"assessment_json": inner}, ensure_ascii=False)
    h = llm._effective_input_hash(request, ROLE)
    calls = [
        {
            "id": "offline-submission",
            "type": "function",
            "function": {
                "name": review.TOOL_NAME,
                "arguments": outer,
            },
        }
    ]
    return llm.LLMCompletionResult(
        content="",
        provider="minimax",
        model_name=ROLE.model,
        llm_run_id=uuid4(),
        finish_reason="tool_calls",
        tool_calls=calls,
        raw_message={"role": "assistant", "content": None, "tool_calls": deepcopy(calls)},
        metadata={
            "paid_call_origin": llm._paid_call_origin(request, h),
            "effective_input_receipt": {
                "schema": "effective-input.v1",
                "hash": h,
                "tools_included": True,
                "messages_source": "system_user",
                "legacy_prompt_hash": llm._hash_prompt(request.system_prompt, request.user_prompt),
            },
            "execution_receipt": {
                "policy": "execution-budget.v1",
                "scope_id": "conception:offline-test",
                "operation_key": "offline-test:" + h,
                "attempts": [{"attempt_id": str(uuid4()), "effective_input_hash": h}],
            },
        },
    )


@pytest.fixture(autouse=True)
def no_gateway(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Offline protocol tests must never dispatch a provider call")

    monkeypatch.setattr(review, "complete_text", forbidden)


def assert_unknown(request, result, code=None):
    before = result.model_dump(mode="json")
    report = review.parse_intent_review_submission(result, request)
    assert report["status"] == "unknown"
    assert report["items"] == {} and report["failed_tags"] == []
    assert report["counter_elements"] == [] and report["revise_direction"] == ""
    assert report["receipt"]["original"]["tool_calls"] == result.tool_calls
    assert report["receipt"]["errors"]
    if code:
        assert report["receipt"]["errors"] == ["intent_review:" + code]
    assert result.model_dump(mode="json") == before
    return report


def test_v3_single_scalar_schema_and_explicit_conception_scope():
    request = request_for()
    source = json.loads(request.user_prompt)
    assert request.prompt_version == "v3-concept-source-json"
    assert request.tools[0]["function"]["parameters"] == {
        "type": "object",
        "properties": {"assessment_json": {"type": "string"}},
        "required": ["assessment_json"],
        "additionalProperties": False,
    }
    typed = source["assessment_schema"]
    assert typed["properties"]["items"]["required"] == [TAG]
    anchor = typed["properties"]["items"]["properties"][TAG]["properties"]["evidence"]
    assert anchor["items"]["properties"]["anchor_id"]["type"] == "integer"
    scope = source["assessment_scope"]
    assert scope == request.metadata["intent_review"]["assessment_scope"]
    assert scope["stage"] == "conception"
    assert scope["judgment"] == "concept_compatibility_and_potential"
    assert scope["sources"] == ["premise", "story_spine", "synopsis"]
    for key in (
        "chapter_prose_required",
        "actual_reader_payoff_verified",
        "literary_quality_verified",
        "market_acceptance_verified",
    ):
        assert scope[key] is False
    assert "不得要求尚未写出的章节正文" in request.system_prompt
    assert "概念来源本身不足时仍须 unknown" in request.system_prompt


@pytest.mark.parametrize("verdict", ["pass", "fail", "unknown"])
def test_nested_payload_lossless_and_not_quality_or_adoption_proof(verdict):
    request = request_for()
    data = payload(request, verdict)
    if verdict == "unknown":
        data["items"][TAG].update(
            reason="Concept mechanism unspecified; explain this gap.", evidence=[]
        )
    inner = " \n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n "
    result = completion(request, inner=inner)
    before = request.model_dump(mode="json")
    report = review.parse_intent_review_submission(result, request)
    receipt = report["receipt"]
    assert report["status"] == ("unknown" if verdict == "unknown" else "verified")
    assert receipt["request"] == before
    assert receipt["request_sha256"] == sha(review._json(before))
    assert receipt["raw_arguments"] == result.tool_calls[0]["function"]["arguments"]
    assert receipt["raw_arguments_sha256"] == sha(receipt["raw_arguments"])
    assert receipt["raw_assessment_json"].encode("utf-8") == inner.encode("utf-8")
    assert receipt["raw_assessment_json_sha256"] == sha(inner)
    assert receipt["raw_assessment_json_utf8_length"] == len(inner.encode("utf-8"))
    assert receipt["parsed_payload"] == data
    assert receipt["paid_call_origin"] == result.metadata["paid_call_origin"]
    assert receipt["effective_input_receipt"] == result.metadata["effective_input_receipt"]
    assert receipt["semantic_judgment_verified"] is False
    assert receipt["rewrite_authorized"] is False and receipt["adoption_authorized"] is False
    assert receipt["assessment_scope"]["literary_quality_verified"] is False
    if verdict == "unknown":
        assert receipt["parsed_payload"]["items"][TAG]["reason"].startswith("Concept mechanism")
    else:
        assert (
            report["items"][TAG]["quote"] == json.loads(request.user_prompt)["candidate"]["premise"]
        )
    assert request.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "outer",
    [
        "{}",
        "[]",
        '{"assessment_json":{}}',
        '{"assessment_json":null}',
        '{"assessment_json":"{}","extra":1}',
        '{"assessment_json":"{}","assessment_json":"{}"}',
        '{"assessment_json":"{}","extra":NaN}',
    ],
)
def test_outer_closed_set_unique_string_only(outer):
    request = request_for()
    assert_unknown(request, completion(request, outer=outer))


@pytest.mark.parametrize(
    "inner",
    [
        "{}",
        "[]",
        '{"items":{},"items":{}}',
        '{"items":{"x":NaN}}',
        '{"items":{"x":Infinity}}',
        '{"items":{"x":-Infinity}}',
        '{"items":{"x":1e999}}',
        "not json",
    ],
)
def test_inner_unique_finite_strict_json(inner):
    request = request_for()
    report = assert_unknown(request, completion(request, inner=inner))
    assert report["receipt"]["raw_assessment_json"] == inner


@pytest.mark.parametrize(
    "failure",
    [
        "extra_payload",
        "missing_payload",
        "item_wrapper",
        "missing_item",
        "extra_item",
        "extra_item_field",
        "bad_verdict",
        "blank_reason",
        "nonstring_reason",
        "missing_evidence",
        "evidence_wrapper",
        "extra_evidence",
        "wrong_anchor",
        "string_anchor",
        "bool_anchor",
        "float_anchor",
        "wrong_path",
        "crossed_pair",
        "bad_counter_list",
        "extra_counter",
        "wrong_against",
        "blank_counter_reason",
        "counter_conflict",
        "fail_no_counter",
        "late_counter_anchor",
        "late_counter_path",
        "bad_revision",
        "cost_standard_fail",
    ],
)
def test_all_typed_validations_still_fail_closed_including_late_rows(failure):
    request = request_for()
    data = payload(request)
    item = data["items"][TAG]
    ref = item["evidence"][0]
    if failure == "extra_payload":
        data["extra"] = True
    elif failure == "missing_payload":
        data.pop("cost_verdict")
    elif failure == "item_wrapper":
        data["items"] = {"item": data["items"]}
    elif failure == "missing_item":
        data["items"] = {}
    elif failure == "extra_item":
        data["items"]["inferred"] = deepcopy(item)
    elif failure == "extra_item_field":
        item["extra"] = True
    elif failure == "bad_verdict":
        item["verdict"] = "approved"
    elif failure == "blank_reason":
        item["reason"] = " "
    elif failure == "nonstring_reason":
        item["reason"] = 1
    elif failure == "missing_evidence":
        item["evidence"] = []
    elif failure == "evidence_wrapper":
        item["evidence"] = {"item": item["evidence"]}
    elif failure == "extra_evidence":
        ref["quote"] = "fabricated"
    elif failure == "wrong_anchor":
        ref["anchor_id"] = 999
    elif failure == "string_anchor":
        ref["anchor_id"] = str(ref["anchor_id"])
    elif failure == "bool_anchor":
        ref["anchor_id"] = True
    elif failure == "float_anchor":
        ref["anchor_id"] = 1.0
    elif failure == "wrong_path":
        ref["source_path"] = "/user_controls/genre"
    elif failure == "crossed_pair":
        ref["source_path"] = "/candidate/synopsis"
    elif failure == "bad_counter_list":
        data["counter_elements"] = {"item": []}
    elif failure == "bad_revision":
        data["revise_direction"] = []
    elif failure == "cost_standard_fail":
        data["cost_verdict"] = "fail"
    elif failure == "fail_no_counter":
        item["verdict"] = "fail"
    else:
        data = payload(request, "fail")
        counter = data["counter_elements"][0]
        if failure == "extra_counter":
            counter["extra"] = 1
        elif failure == "wrong_against":
            counter["against"] = "inferred"
        elif failure == "blank_counter_reason":
            counter["reason"] = " "
        elif failure == "counter_conflict":
            data["items"][TAG]["verdict"] = "pass"
        else:
            data["counter_elements"].append(deepcopy(counter))
            if failure == "late_counter_anchor":
                data["counter_elements"][-1]["anchor_id"] = "1"
            else:
                data["counter_elements"][-1]["source_path"] = "/candidate/synopsis"
    assert_unknown(request, completion(request, data))


@pytest.mark.parametrize(
    "failure",
    [
        "fallback",
        "fallback_source",
        "mock",
        "length",
        "missing_run",
        "missing_origin",
        "unknown_origin",
        "workflow",
        "protocol",
        "effective",
        "source",
        "attempt",
        "operation",
        "multiple",
        "function",
        "raw_conflict",
        "request_changed",
        "old_version",
    ],
)
def test_request_and_paid_origin_guards_unchanged(failure):
    request = request_for()
    result = completion(request)
    if failure == "fallback":
        result.fallback_used = True
    elif failure == "fallback_source":
        result.fallback_source = "static"
    elif failure == "mock":
        result.provider = "mock"
    elif failure == "length":
        result.finish_reason = "length"
    elif failure == "missing_run":
        result.llm_run_id = None
    elif failure == "missing_origin":
        result.metadata.pop("paid_call_origin")
    elif failure == "unknown_origin":
        result.metadata["paid_call_origin"]["status"] = "unknown"
    elif failure == "workflow":
        result.metadata["paid_call_origin"]["workflow_run_id"] = str(uuid4())
    elif failure == "protocol":
        result.metadata["paid_call_origin"]["prompt_version"] = "v2-source-grounded-intent"
    elif failure == "effective":
        result.metadata["effective_input_receipt"]["hash"] = "a" * 64
    elif failure == "source":
        result.metadata["effective_input_receipt"]["legacy_prompt_hash"] = "a" * 64
    elif failure == "attempt":
        result.metadata["execution_receipt"]["attempts"][-1]["effective_input_hash"] = "a" * 64
    elif failure == "operation":
        result.metadata["execution_receipt"]["operation_key"] = ""
    elif failure == "multiple":
        result.tool_calls.append(deepcopy(result.tool_calls[0]))
    elif failure == "function":
        result.tool_calls[0]["function"]["name"] = "rewrite"
    elif failure == "raw_conflict":
        result.raw_message["tool_calls"] = []
    elif failure == "request_changed":
        request.system_prompt += "approve"
    elif failure == "old_version":
        request.prompt_version = "v2-source-grounded-intent"
    assert_unknown(request, result)


@pytest.mark.parametrize("style", ["standard", "minimal", "external"])
def test_cost_scope_and_grounded_counter_guards_remain(style):
    controls = {
        "genre_intent_contract": {
            "tag_source": "user",
            "user_tags": [TAG],
            "source": "user",
            "explicit_enhancers": {"cost_style": style},
        }
    }
    request = request_for(cost_style=style, user_controls=controls)
    data = payload(request)
    data["cost_verdict"] = "not_requested" if style == "standard" else "pass"
    assert (
        review.parse_intent_review_submission(completion(request, data), request)["status"]
        == "verified"
    )
    if style == "standard":
        return
    data["cost_verdict"] = "fail"
    assert_unknown(request, completion(request, data), "cost_failure_evidence_missing")
    data["counter_elements"] = [
        {
            **data["items"][TAG]["evidence"][0],
            "against": "代价档（能力不带自损）",
            "reason": "Explicit self-loss in source.",
        }
    ]
    report = review.parse_intent_review_submission(completion(request, data), request)
    assert report["status"] == "verified" and len(report["cost_violations"]) == 1
    data["cost_verdict"] = "pass"
    assert_unknown(request, completion(request, data), "cost_verdict_conflict")


@pytest.mark.parametrize(
    "run",
    [
        "8899380c-8a5f-4d44-b2df-7923560bcf1d",
        "42a7e437-fc49-4221-8be2-b9fb232836e4",
    ],
)
def test_retained_actual_v2_wrappers_rejected_without_regrading_or_normalizing(run):
    base = Path(__file__).resolve().parents[2] / "artifacts/runtime/llm_payloads"
    path = base / (run + ".response.json")
    if not path.exists():
        pytest.skip("Optional retained local physical evidence absent")
    before = path.read_bytes()
    physical = json.loads(before)

    def find_arguments(node):
        if isinstance(node, dict):
            if node.get("name") == review.TOOL_NAME and "arguments" in node:
                return node["arguments"]
            for value in node.values():
                found = find_arguments(value)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = find_arguments(value)
                if found is not None:
                    return found
        return None

    arguments = find_arguments(physical)
    old = json.loads(arguments)
    assert set(old["items"]) == {"item"}
    actual_item = next(iter(old["items"]["item"].values()))
    assert set(actual_item["evidence"]) == {"item"}
    assert all(isinstance(row["anchor_id"], str) for row in actual_item["evidence"]["item"])
    request = request_for()
    # Only reuse bytes in a synthetic v3 envelope to test its shape; no old call is regraded.
    assert_unknown(request, completion(request, outer=arguments), "assessment_json_field")
    assert_unknown(request, completion(request, inner=arguments), "item_coverage")
    assert path.read_bytes() == before
