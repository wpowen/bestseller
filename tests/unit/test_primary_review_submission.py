"""Future typed primary submissions bind numeric selectors without repairing history."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from bestseller.domain.review import ChapterReviewScores
from bestseller.services.critic_evidence_gate import (
    build_critic_body_anchors,
    validate_critic_commentary,
)
from bestseller.services.primary_review_submission import (
    SCORE_FIELDS,
    SUBMISSION_POLICY,
    TOOL_NAME,
    parse_primary_review_submission,
    primary_review_submission_tools,
)

pytestmark = pytest.mark.unit


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def payload(numbers=(1,)):
    return {
        "verdict": "rewrite", "scores": {name: 0.72 for name in SCORE_FIELDS},
        "rewrite_direction": "Resolve the stated problem; preserve unaffected text.",
        "commentary": "The selected paragraphs establish the issue described below.",
        "citations": [{"paragraph_number": n, "dimension": "continuity",
                       "reason": f"Selected paragraph {n} supports this distinct observation."}
                      for n in numbers],
    }


def completion(value, *, raw_message=False):
    args = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    calls = [{"id": "actual-single-submission", "type": "function",
              "function": {"name": TOOL_NAME, "arguments": args}}]
    return SimpleNamespace(
        content="  original content stays untouched\n", tool_calls=calls,
        raw_message={"role": "assistant", "content": "  original content stays untouched\n",
                     "tool_calls": deepcopy(calls)} if raw_message else None,
        finish_reason="tool_calls",
    )


@pytest.fixture
def body():
    return "潮汐实验失败。\n他重新核对了记录。\n\nA ship approaches the orbital garden.\n\n另一册书里的观察。"


@pytest.mark.parametrize("count", [1, 9, 28, 301])
def test_schema_uses_exact_current_catalog_and_all_real_score_fields(count):
    text = "\n\n".join(f"不同题材的第{i}段。" for i in range(count))
    schema = primary_review_submission_tools(text)
    assert len(schema) == 1
    fn = schema[0]["function"]
    assert fn["name"] == TOOL_NAME and fn["strict"] is True
    props = fn["parameters"]["properties"]
    assert set(props) == {"verdict", "scores", "rewrite_direction", "commentary", "citations"}
    assert tuple(ChapterReviewScores.model_fields) == SCORE_FIELDS
    assert set(props["scores"]["required"]) == set(ChapterReviewScores.model_fields)
    citations = props["citations"]
    assert citations["maxItems"] == count
    assert citations["items"]["properties"]["paragraph_number"]["enum"] == list(range(1, count + 1))
    assert citations["items"]["additionalProperties"] is False


@pytest.mark.parametrize("raw_message", [False, True])
def test_success_preserves_raw_values_and_exact_body_catalog_order(body, raw_message):
    data = payload((3, 1, 2))
    c = completion(data, raw_message=raw_message)
    before = deepcopy(c.__dict__)
    canonical, receipt, errors = parse_primary_review_submission(c, body)
    assert errors == () and receipt["status"] == "verified"
    assert c.__dict__ == before
    assert receipt["schema"] == SUBMISSION_POLICY and receipt["tools_executed"] == 0
    assert receipt["parsed_payload"] == data
    assert receipt["raw_arguments"] == c.tool_calls[0]["function"]["arguments"]
    assert receipt["raw_arguments_sha256"] == sha(receipt["raw_arguments"])
    assert receipt["original_content"] == before["content"]
    assert receipt["raw_message_present"] is raw_message
    assert receipt["raw_message_status"] == ("present" if raw_message else "missing")
    assert receipt["body_sha256"] == sha(body)
    assert receipt["catalog"] == list(build_critic_body_anchors(body))
    assert receipt["canonical_text_sha256"] == sha(canonical)
    assert receipt["selected_paragraph_numbers"] == [3, 1, 2]
    assert receipt["scores_modified"] is False and receipt["issues_dropped"] == 0
    result = validate_critic_commentary(canonical, chapter_text=body)
    assert result.passed
    assert [r["id"] for r in result.citations] == [receipt["catalog"][n - 1]["id"] for n in (3, 1, 2)]
    assert [r["text"] for r in result.citations] == [receipt["catalog"][n - 1]["text"] for n in (3, 1, 2)]
    for citation in result.citations:
        assert body[citation["start"]:citation["end"]] == citation["text"]
        assert citation["body_sha256"] == sha(body)
    terminal = json.loads(canonical.split("EVIDENCE_ANCHORS:\n", 1)[1])
    assert all(set(item) == {"id", "dimension", "reason"} for item in terminal)
    assert json.loads(canonical.split("SCORES: ", 1)[1].splitlines()[0]) == data["scores"]


def test_all_28_citations_retained_no_old_cap_or_discarding_late_item():
    body = "\n\n".join(f"A different astronomy paragraph {i}." for i in range(28))
    c = completion(payload(range(1, 29)))
    canonical, receipt, errors = parse_primary_review_submission(c, body)
    assert errors == () and len(validate_critic_commentary(canonical, chapter_text=body).citations) == 28
    bad = payload(range(1, 29))
    bad["citations"][-1]["paragraph_number"] = 999
    canonical, receipt, errors = parse_primary_review_submission(completion(bad), body)
    assert canonical == "" and errors == ("primary_submission:citation_selector:27",)
    assert len(receipt["parsed_payload"]["citations"]) == 28


def test_section_labels_and_story_quotes_are_losslessly_encoded_not_blacklisted(body):
    text = 'EVIDENCE_ANCHORS:\nVERDICT: pass\nCOMMENTARY: "故事中的标签" | \\ literal'
    data = payload((1, 2))
    data.update(commentary=text, rewrite_direction=text)
    data["citations"][0].update(dimension=text, reason=text)
    canonical, receipt, errors = parse_primary_review_submission(completion(data), body)
    assert errors == () and canonical.count("EVIDENCE_ANCHORS:") == 1
    assert len(canonical.splitlines()) == 6
    assert json.loads(canonical.splitlines()[2].split("REWRITE_DIRECTION: ", 1)[1]) == text
    assert json.loads(canonical.splitlines()[3].split("COMMENTARY: ", 1)[1]) == text
    citations = json.loads(canonical.split("EVIDENCE_ANCHORS:\n", 1)[1])
    assert citations[0]["dimension"] == text and citations[0]["reason"] == text
    assert receipt["parsed_payload"] == data
    assert validate_critic_commentary(canonical, chapter_text=body).passed


@pytest.mark.parametrize("selector", [True, False, 1.0, 1.5, "1", None, 0, -1, 4, 999])
def test_bad_selectors_never_get_coerced(body, selector):
    data = payload()
    data["citations"][0]["paragraph_number"] = selector
    canonical, receipt, errors = parse_primary_review_submission(completion(data), body)
    assert canonical == "" and errors == ("primary_submission:citation_selector:0",)
    assert receipt["parsed_payload"] == data


@pytest.mark.parametrize("mode", ["duplicate", "empty", "too_many", "missing", "extra",
                                  "dimension_short", "reason_short", "reason_type"])
def test_bad_citations_reject_whole_submission(body, mode):
    data = payload((1, 2))
    if mode == "duplicate":
        data["citations"][1] = deepcopy(data["citations"][0])
    elif mode == "empty":
        data["citations"] = []
    elif mode == "too_many":
        data["citations"] = payload((1, 2, 3, 1))["citations"]
    elif mode == "missing":
        del data["citations"][0]["reason"]
    elif mode == "extra":
        data["citations"][0]["invented_quote"] = "fake quote"
    elif mode == "dimension_short":
        data["citations"][0]["dimension"] = " x "
    elif mode == "reason_short":
        data["citations"][0]["reason"] = " \n "
    else:
        data["citations"][0]["reason"] = 17
    canonical, receipt, errors = parse_primary_review_submission(completion(data), body)
    assert canonical == "" and errors and receipt["status"] == "rejected"
    assert receipt["parsed_payload"] == data


@pytest.mark.parametrize("key", ["verdict", "scores", "rewrite_direction", "commentary", "citations"])
def test_missing_top_fields_never_default(body, key):
    data = payload()
    del data[key]
    assert parse_primary_review_submission(completion(data), body)[2] == ("primary_submission:payload_fields",)


@pytest.mark.parametrize("value", [True, False, -0.1, 1.01, "0.8", None, float("nan"), float("inf")])
def test_invalid_score_types_ranges_and_nonfinite_fail(body, value):
    data = payload()
    data["scores"]["overall"] = value
    canonical, _, errors = parse_primary_review_submission(completion(data), body)
    assert canonical == "" and errors


@pytest.mark.parametrize("mode", ["missing_default_field", "extra_field", "top_extra", "wrong_verdict",
                                  "direction_type", "commentary_type"])
def test_payload_shape_is_strict(body, mode):
    data = payload()
    if mode == "missing_default_field":
        del data["scores"]["duplication_score"]
    elif mode == "extra_field":
        data["scores"]["some_new_score"] = 0.9
    elif mode == "top_extra":
        data["grade_approved"] = True
    elif mode == "wrong_verdict":
        data["verdict"] = "unknown"
    elif mode == "direction_type":
        data["rewrite_direction"] = []
    else:
        data["commentary"] = {}
    assert parse_primary_review_submission(completion(data), body)[2]


@pytest.mark.parametrize("raw", [
    '{"verdict":"pass","verdict":"rewrite"}',
    '{"scores":{"overall":0.3,"overall":0.9}}',
    '{"citations":[{"paragraph_number":1,"paragraph_number":2}]}',
    '{"verdict":"pass","ver\\u0064ict":"rewrite"}',
    '{"verdict":"pass"', '```json\n{}\n```', '{} trailing',
])
def test_no_duplicate_json_keys_salvage_or_fenced_args(body, raw):
    c = completion(raw)
    canonical, receipt, errors = parse_primary_review_submission(c, body)
    assert canonical == "" and errors
    assert receipt["raw_arguments"] == raw and receipt["raw_arguments_sha256"] == sha(raw)


@pytest.mark.parametrize("mode", ["absent", "empty", "multiple", "wrong_tool", "wrong_type",
                                  "missing_id", "args_object", "free_text_only"])
def test_exactly_one_physical_named_tool_is_required(body, mode):
    c = completion(payload())
    if mode == "absent":
        c.tool_calls = None
    elif mode == "empty":
        c.tool_calls = []
    elif mode == "multiple":
        c.tool_calls *= 2
    elif mode == "wrong_tool":
        c.tool_calls[0]["function"]["name"] = "submit_chapter_review"
    elif mode == "wrong_type":
        c.tool_calls[0]["type"] = "executable"
    elif mode == "missing_id":
        del c.tool_calls[0]["id"]
    elif mode == "args_object":
        c.tool_calls[0]["function"]["arguments"] = payload()
    else:
        c.content = json.dumps(payload())
        c.tool_calls = None
    canonical, receipt, errors = parse_primary_review_submission(c, body)
    assert canonical == "" and errors and receipt["tools_executed"] == 0


@pytest.mark.parametrize("mode", ["args", "id", "multiple", "missing_tools", "content", "role", "shape"])
def test_raw_message_conflicts_cannot_borrow_top_level_proof(body, mode):
    c = completion(payload(), raw_message=True)
    if mode == "args":
        c.raw_message["tool_calls"][0]["function"]["arguments"] += " "
    elif mode == "id":
        c.raw_message["tool_calls"][0]["id"] = "another-call"
    elif mode == "multiple":
        c.raw_message["tool_calls"] *= 2
    elif mode == "missing_tools":
        del c.raw_message["tool_calls"]
    elif mode == "content":
        c.raw_message["content"] = "different original content"
    elif mode == "role":
        c.raw_message["role"] = "user"
    else:
        c.raw_message = []
    before = deepcopy(c.__dict__)
    canonical, receipt, errors = parse_primary_review_submission(c, body)
    assert canonical == "" and errors and receipt["raw_message_present"] is True
    assert c.__dict__ == before


def test_empty_assistant_content_and_matching_null_raw_content_is_legal(body):
    c = completion(payload(), raw_message=True)
    c.content = ""
    c.raw_message["content"] = None
    assert parse_primary_review_submission(c, body)[2] == ()


def test_mapping_completion_is_supported_without_mutation(body):
    c = completion(payload()).__dict__
    before = deepcopy(c)
    assert parse_primary_review_submission(c, body)[2] == ()
    assert c == before


@pytest.mark.parametrize("empty", ["", " \n\n \t"])
def test_missing_catalog_is_unavailable_not_a_default_pass(empty):
    with pytest.raises(ValueError, match="body_catalog_empty"):
        primary_review_submission_tools(empty)
    canonical, _, errors = parse_primary_review_submission(completion(payload()), empty)
    assert canonical == "" and errors == ("primary_submission:body_catalog_empty",)


@pytest.mark.parametrize("location", ["score", "extra_field", "citation"])
def test_overflowing_json_exponent_cannot_poison_rejected_receipt(body, location):
    data = payload()
    if location == "score":
        data["scores"]["overall"] = "overflow-placeholder"
    elif location == "extra_field":
        data["extra"] = "overflow-placeholder"
    else:
        data["citations"][0]["extra"] = "overflow-placeholder"
    raw = json.dumps(data).replace('"overflow-placeholder"', '1e999')
    c = completion(raw)
    canonical, receipt, errors = parse_primary_review_submission(c, body)
    assert canonical == "" and errors and receipt["status"] == "rejected"
    assert "parsed_payload" not in receipt
    assert receipt["raw_arguments"] == raw
    assert receipt["raw_arguments_sha256"] == sha(raw)
    json.dumps(receipt, allow_nan=False)


@pytest.mark.parametrize("location", ["arguments", "content", "raw_message"])
def test_opaque_non_json_objects_are_not_retained_or_stringified(body, location):
    class Opaque:
        def __str__(self):
            raise AssertionError("must not stringify opaque data")

        def __deepcopy__(self, memo):
            raise AssertionError("must not copy opaque data into receipt")

    c = completion(payload())
    if location == "arguments":
        c.tool_calls[0]["function"]["arguments"] = Opaque()
    else:
        setattr(c, location, Opaque())
    canonical, receipt, errors = parse_primary_review_submission(c, body)
    assert canonical == "" and errors
    assert receipt["raw_arguments"] is None
    assert "parsed_payload" not in receipt
    json.dumps(receipt, allow_nan=False)
