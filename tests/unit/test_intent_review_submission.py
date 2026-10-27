"""Offline protocol fixtures, not paid calls or literary acceptance."""

# ruff: noqa: RUF001

from __future__ import annotations

from copy import deepcopy
import json
from uuid import uuid4

import pytest

from bestseller.services import intent_alignment, llm
from bestseller.services import intent_review_submission as review
from bestseller.settings import LLMRoleSettings

pytestmark = pytest.mark.unit
ROLE = LLMRoleSettings(
    model="minimax/offline-test",
    temperature=0.2,
    max_tokens=8192,
    timeout_seconds=30,
    api_base="https://example.invalid",
)


def inputs():
    return {
        "candidate": {
            "premise": "A courier earns entry by rescuing a rival.",
            "synopsis": "The rescued rival publicly opens the gate.",
            "story_spine": {"goal": "enter the city", "a/b~c": ["a paid-off rivalry"]},
        },
        "user_controls": {
            "genre": "adventure",
            "language": "en",
            "genre_intent_contract": {
                "user_tags": ["satisfying payoff"],
                "default_tags": ["unselected system"],
                "tag_source": "user",
            },
        },
        "intent_tags": ["satisfying payoff"],
        "genre_label": "adventure",
    }


def payload(request, *, verdict="pass"):
    source = json.loads(request.user_prompt)
    ref = {k: source["catalog"][0][k] for k in ("source_path", "anchor_id")}
    tag = source["intent_tags"][0] if source["intent_tags"] else None
    items = (
        {
            tag: {
                "verdict": verdict,
                "reason": "The action has a visible outcome.",
                "evidence": [ref],
            }
        }
        if tag
        else {}
    )
    return {
        "items": items,
        "counter_elements": (
            [
                {
                    **ref,
                    "against": tag,
                    "reason": "A source-bound negative interpretation, not story truth.",
                }
            ]
            if verdict == "fail"
            else []
        ),
        "revise_direction": "",
        "cost_verdict": "not_requested",
    }


def completion(request, data=None):
    """Gateway-shaped synthetic origin; no ledger authorization or provider action."""
    h = llm._effective_input_hash(request, ROLE)
    namespace = llm._semantic_operation_namespace.get()
    identity = llm.execution_operation_id(
        namespace[0] if namespace else "offline-fixture",
        subject=f"{request.logical_role}:{request.prompt_template}:{request.prompt_version}",
        revision=h,
    )
    calls = [
        {
            "id": "submission-1",
            "type": "function",
            "function": {
                "name": review.TOOL_NAME,
                "arguments": json.dumps({"assessment_json": json.dumps(data if data is not None else payload(request))}),
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
            "paid_call_origin": llm._paid_call_origin(request, h)
            | {"workflow_run_id": str(request.workflow_run_id or uuid4())},
            "execution_receipt": {
                "policy": "execution-budget.v1",
                "scope_id": "conception:offline-test",
                "operation_key": identity + f":sample:{request.sample_slot}",
                "attempts": [
                    {
                        "attempt_id": str(uuid4()),
                        "effective_input_hash": h,
                        "model": ROLE.model,
                        "reserved_tokens": 8192,
                    }
                ],
            },
            "effective_input_receipt": {
                "schema": "effective-input.v1",
                "hash": h,
                "messages_source": "system_user",
                "tools_included": True,
                "model": ROLE.model,
                "legacy_prompt_hash": llm._hash_prompt(request.system_prompt, request.user_prompt),
            },
        },
    )


def parse(request, data=None):
    return review.parse_intent_review_submission(completion(request, data), request)


def test_catalog_paths_are_exact_and_controls_are_not_evidence():
    args = inputs()
    original = deepcopy(args)
    request = review.build_intent_review_request(**args)
    source = json.loads(request.user_prompt)
    assert source["candidate"] == args["candidate"]
    assert source["user_controls"] == args["user_controls"]
    assert source["catalog"] == [
        {"anchor_id": 1, "source_path": "/candidate/premise", "text": args["candidate"]["premise"]},
        {
            "anchor_id": 2,
            "source_path": "/candidate/story_spine/a~1b~0c/0",
            "text": "a paid-off rivalry",
        },
        {"anchor_id": 3, "source_path": "/candidate/story_spine/goal", "text": "enter the city"},
        {
            "anchor_id": 4,
            "source_path": "/candidate/synopsis",
            "text": args["candidate"]["synopsis"],
        },
    ]
    assert all("unselected system" not in row["text"] for row in source["catalog"])
    schema = request.tools[0]["function"]["parameters"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"assessment_json"}
    assert schema["properties"]["assessment_json"]["type"] == "string"
    assert schema["required"] == ["assessment_json"]
    assert request.tool_choice == "required" and request.sample_slot == 0
    assert request.prompt_version == review.INTENT_REVIEW_PROMPT_VERSION
    assert args == original


@pytest.mark.parametrize("verdict", ["pass", "fail", "unknown"])
def test_pass_fail_unknown_preserve_physical_tool_and_exact_quotes(verdict):
    request = review.build_intent_review_request(**inputs())
    result = completion(request, payload(request, verdict=verdict))
    report = review.parse_intent_review_submission(result, request)
    receipt = report["receipt"]
    assert receipt["original"]["tool_calls"] == result.tool_calls
    assert receipt["original"]["raw_message"] == result.raw_message
    assert receipt["raw_arguments"] == result.tool_calls[0]["function"]["arguments"]
    assert receipt["operation_key"] == result.metadata["execution_receipt"]["operation_key"]
    if verdict == "unknown":
        assert report["status"] == "unknown" and report["items"] == {} and not report["failed_tags"]
    else:
        assert report["status"] == "verified"
        item = report["items"]["satisfying payoff"]
        assert item["quote"] == inputs()["candidate"]["premise"]
        assert item["pass"] is (verdict == "pass")
        assert report["failed_tags"] == (["satisfying payoff"] if verdict == "fail" else [])
        assert report["counter_elements"] == (
            []
            if verdict == "pass"
            else [
                {
                    "anchor_id": 1,
                    "field": "/candidate/premise",
                    "quote": inputs()["candidate"]["premise"],
                    "against": "satisfying payoff",
                    "reason": "A source-bound negative interpretation, not story truth.",
                }
            ]
        )


@pytest.mark.parametrize(
    "failure",
    [
        "wrong_anchor",
        "wrong_path",
        "crossed_pair",
        "bool_anchor",
        "late_bad_counter",
        "extra_quote",
        "missing_item",
        "missing_evidence",
        "fail_no_counter",
        "duplicate_json_key",
        "nan",
        "infinity",
        "mock",
        "fallback",
        "fallback_source",
        "length",
        "missing_id",
        "missing_origin",
        "unknown_origin",
        "missing_workflow",
        "wrong_protocol",
        "wrong_effective",
        "wrong_source",
        "wrong_attempt",
        "missing_operation",
        "multiple_tools",
        "wrong_function",
        "raw_conflict",
        "free_text_only",
        "cost_standard_fail",
    ],
)
def test_bad_late_item_or_physical_proof_never_yields_partial_grade(failure):
    request = review.build_intent_review_request(**inputs())
    data = payload(request)
    item = data["items"]["satisfying payoff"]
    if failure == "wrong_anchor":
        item["evidence"][0]["anchor_id"] = 999
    elif failure == "wrong_path":
        item["evidence"][0]["source_path"] = "/user_controls/genre"
    elif failure == "crossed_pair":
        item["evidence"][0]["source_path"] = "/candidate/synopsis"
    elif failure == "bool_anchor":
        item["evidence"][0]["anchor_id"] = True
    elif failure == "late_bad_counter":
        data = payload(request, verdict="fail")
        data["counter_elements"].append({**data["counter_elements"][0], "anchor_id": 999})
    elif failure == "extra_quote":
        item["evidence"][0]["quote"] = "made-up continuous quote"
    elif failure == "missing_item":
        data["items"] = {}
    elif failure == "missing_evidence":
        item["evidence"] = []
    elif failure == "fail_no_counter":
        item["verdict"] = "fail"
    elif failure == "cost_standard_fail":
        data["cost_verdict"] = "fail"
    result = completion(request, data)
    if failure in {"duplicate_json_key", "nan", "infinity"}:
        result.tool_calls[0]["function"]["arguments"] = {
            "duplicate_json_key": '{"items":{},"items":{}}',
            "nan": '{"x":NaN}',
            "infinity": '{"x":1e999}',
        }[failure]
        result.raw_message = None
    elif failure == "mock":
        result.provider = "mock"
    elif failure == "fallback":
        result.fallback_used = True
    elif failure == "fallback_source":
        result.fallback_source = "static"
    elif failure == "length":
        result.finish_reason = "length"
    elif failure == "missing_id":
        result.llm_run_id = None
    elif failure == "missing_origin":
        result.metadata.pop("paid_call_origin")
    elif failure == "unknown_origin":
        result.metadata["paid_call_origin"]["status"] = "unknown"
    elif failure == "missing_workflow":
        result.metadata["paid_call_origin"]["workflow_run_id"] = None
    elif failure == "wrong_protocol":
        result.metadata["paid_call_origin"]["prompt_version"] = "old-free-text"
    elif failure == "wrong_effective":
        result.metadata["effective_input_receipt"]["hash"] = "a" * 64
    elif failure == "wrong_source":
        result.metadata["effective_input_receipt"]["legacy_prompt_hash"] = "a" * 64
    elif failure == "wrong_attempt":
        result.metadata["execution_receipt"]["attempts"][-1]["effective_input_hash"] = "a" * 64
    elif failure == "missing_operation":
        result.metadata["execution_receipt"]["operation_key"] = ""
    elif failure == "multiple_tools":
        result.tool_calls.append(deepcopy(result.tool_calls[0]))
    elif failure == "wrong_function":
        result.tool_calls[0]["function"]["name"] = "run_rewrite"
    elif failure == "raw_conflict":
        result.raw_message["tool_calls"][0]["function"]["arguments"] = "{}"
    elif failure == "free_text_only":
        result.content, result.tool_calls = "all pass", None
    report = review.parse_intent_review_submission(result, request)
    assert report["status"] == "unknown"
    assert report["failed_tags"] == [] and report["items"] == {}
    assert report["counter_elements"] == [] and report["revise_direction"] == ""
    assert report["receipt"]["errors"]
    assert report["receipt"]["original"]["tool_calls"] == result.tool_calls


def test_missing_raw_message_is_explicit_and_allowed_but_not_missing_top_level_tools():
    request = review.build_intent_review_request(**inputs())
    result = completion(request)
    result.raw_message = None
    report = review.parse_intent_review_submission(result, request)
    assert report["status"] == "verified" and report["receipt"]["raw_message_missing"] is True


@pytest.mark.parametrize("style", ["standard", "minimal", "external"])
def test_cost_scope_is_explicit_not_invented_from_standard(style):
    args = inputs() | {"cost_style": style}
    args["user_controls"]["genre_intent_contract"].update(
        source="user", explicit_enhancers={"cost_style": style}
    )
    request = review.build_intent_review_request(**args)
    data = payload(request)
    data["cost_verdict"] = "not_requested" if style == "standard" else "pass"
    assert parse(request, data)["status"] == "verified"
    if style != "standard":
        data["cost_verdict"] = "unknown"
        assert parse(request, data)["status"] == "unknown"


def test_cost_only_requires_actual_cost_verdict_and_grounded_failure():
    args = inputs() | {"intent_tags": [], "cost_style": "minimal"}
    args["user_controls"]["genre_intent_contract"].update(
        source="user", explicit_enhancers={"cost_style": "minimal"}
    )
    request = review.build_intent_review_request(**args)
    data = payload(request)
    data["cost_verdict"] = "fail"
    assert parse(request, data)["status"] == "unknown"
    ref = json.loads(request.user_prompt)["catalog"][0]
    data["counter_elements"] = [
        {
            "anchor_id": ref["anchor_id"],
            "source_path": ref["source_path"],
            "against": "代价档（能力不带自损）",
            "reason": "A stated self-loss.",
        }
    ]
    report = parse(request, data)
    assert report["status"] == "verified" and len(report["cost_violations"]) == 1


@pytest.mark.parametrize(
    "failure",
    [
        "whole_state",
        "runtime_control",
        "nested_history",
        "tuple",
        "nonfinite",
        "oversize",
        "bool_slot",
        "recheck_slot",
    ],
)
async def test_invalid_or_oversize_input_never_dispatches(monkeypatch, failure):
    args = inputs()
    if failure == "whole_state":
        args["candidate"]["last_failure"] = {}
    elif failure == "runtime_control":
        args["user_controls"]["conception_log"] = []
    elif failure == "nested_history":
        args["user_controls"]["genre_intent_contract"]["llm_run_ids"] = []
    elif failure == "tuple":
        args["candidate"]["story_spine"]["bad"] = (1, 2)
    elif failure == "nonfinite":
        args["candidate"]["story_spine"]["bad"] = float("nan")
    elif failure == "oversize":
        args["candidate"]["synopsis"] = "full canonical text" * 100_000
    elif failure == "bool_slot":
        args["sample_slot"] = True
    else:
        args.update(phase="recheck", sample_slot=1)

    async def forbidden(*_):
        pytest.fail("bad inputs must fail before paid gateway")

    monkeypatch.setattr(review, "complete_text", forbidden)
    with pytest.raises(ValueError):
        await review.evaluate_intent_alignment_sample(None, None, **args)


async def test_public_entry_explicit_slots_are_stable_across_workflow_and_recheck_distinct(
    monkeypatch,
):
    calls = []

    async def gateway(session, settings, request):
        result = completion(request)
        calls.append((deepcopy(request), result.metadata["execution_receipt"]["operation_key"]))
        return result

    monkeypatch.setattr(review, "complete_text", gateway)
    for slot, phase in [(0, "initial"), (1, "initial"), (0, "initial"), (0, "recheck")]:
        report = await intent_alignment.evaluate_intent_alignment_sample(
            None, None, **inputs(), sample_slot=slot, phase=phase, workflow_run_id=uuid4()
        )
        assert report["status"] == "verified"
        assert report["receipt"]["sample_slot"] == slot
        assert report["receipt"]["operation_key"] == calls[-1][1]
    assert calls[0][1] != calls[1][1]
    assert calls[0][1] == calls[2][1]  # New workflow cannot create a new logical slot.
    assert calls[0][1] != calls[3][1]
    assert calls[0][0].user_prompt == calls[1][0].user_prompt
    assert all(r.tools and r.tool_choice == "required" for r, _ in calls)


def test_cross_workflow_recovery_preserves_original_origin_requires_exact_alias():
    original, current = uuid4(), uuid4()
    request = review.build_intent_review_request(**inputs(), workflow_run_id=current)
    result = completion(request)
    result.metadata["paid_call_origin"]["workflow_run_id"] = str(original)
    assert review.parse_intent_review_submission(result, request)["status"] == "unknown"
    result.metadata["execution_recovery"] = llm._paid_call_origin(
        request, result.metadata["effective_input_receipt"]["hash"]
    ) | {"schema": "execution-recovery.v1"}
    parsed = review.parse_intent_review_submission(result, request)
    assert parsed["status"] == "verified"
    assert parsed["receipt"]["paid_call_origin"]["workflow_run_id"] == str(original)


def test_repeated_anchors_are_not_extra_source_coverage_and_rows_are_lossless():
    request = review.build_intent_review_request(**inputs())
    data = payload(request, verdict="fail")
    data["counter_elements"].append(
        {**data["counter_elements"][0], "reason": "Another interpretation."}
    )
    report = parse(request, data)
    assert report["status"] == "verified" and len(report["counter_elements"]) == 2
    assert report["receipt"]["source_coverage"]["distinct_anchor_count"] == 1
    assert report["receipt"]["source_coverage"]["citation_count"] == 3


@pytest.mark.parametrize("case", ["default_tag", "legacy_tag", "default_effect", "invented_cost"])
def test_defaults_and_inference_never_become_explicit_human_requests(case):
    args = inputs()
    genre = args["user_controls"]["genre_intent_contract"]
    if case == "default_tag":
        args["intent_tags"] = ["unselected system"]
    elif case == "legacy_tag":
        genre["tag_source"] = "legacy_inferred"
    elif case == "default_effect":
        genre.update(
            source="user",
            user_tags=[],
            explicit_enhancers={"effect_skills": ["hype_satisfaction_engine"]},
        )
        args["user_controls"]["creation_intent_contract"] = {
            "field_sources": {"story_enhancers": "default"}
        }
        args["intent_tags"] = ["爽点满足"]
    else:
        args["cost_style"] = "minimal"
    with pytest.raises(ValueError, match="source_unverified"):
        review.build_intent_review_request(**args)


def test_existing_explicit_effect_contract_route_without_default_tone_upgrade():
    args = inputs()
    genre = args["user_controls"]["genre_intent_contract"]
    genre.update(
        source="user",
        user_tags=[],
        tone_preference="light",
        explicit_enhancers={
            "effect_skills": ["hype_satisfaction_engine"],
            "cost_style": "standard",
        },
    )
    args["user_controls"]["creation_intent_contract"] = {
        "field_sources": {"story_enhancers": "explicit", "tone_preference": "default"},
        "story_enhancers": {
            "effect_skills": ["hype_satisfaction_engine"],
            "cost_style": "standard",
        },
    }
    args["intent_tags"] = ["爽点满足"]
    assert review.build_intent_review_request(**args).tools
    args["intent_tags"].append("轻松基调")
    with pytest.raises(ValueError, match="intent_source_unverified"):
        review.build_intent_review_request(**args)


async def test_gateway_failure_does_not_retry_or_change_slot_context(monkeypatch):
    calls = []

    async def gateway(*args):
        calls.append(args[-1])
        raise RuntimeError("offline transport unavailable")

    monkeypatch.setattr(review, "complete_text", gateway)
    before = llm._semantic_operation_namespace.get()
    with pytest.raises(RuntimeError, match="transport unavailable"):
        await review.evaluate_intent_alignment_sample(None, None, **inputs(), sample_slot=1)
    assert len(calls) == 1 and calls[0].sample_slot == 1
    assert llm._semantic_operation_namespace.get() == before


def test_wire_proof_is_explicitly_not_semantic_or_rewrite_approval():
    request = review.build_intent_review_request(**inputs())
    report = parse(request, payload(request, verdict="fail"))
    assert report["status"] == "verified"
    assert report["receipt"]["source_grounding_verified"] is True
    assert report["receipt"]["semantic_judgment_verified"] is False
    assert report["receipt"]["rewrite_authorized"] is False
    assert report["receipt"]["adoption_authorized"] is False
