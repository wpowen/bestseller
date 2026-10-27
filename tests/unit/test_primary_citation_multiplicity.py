"""Future citation tuples retain independent issues without counting source votes."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from bestseller.services.critic_evidence_gate import (
    CRITIC_ANCHOR_ADAPTER_POLICY,
    CRITIC_CITATION_TUPLE_POLICY,
    build_critic_body_anchors,
    validate_critic_commentary,
)
from bestseller.services.primary_review_submission import (
    LEGACY_SUBMISSION_POLICY,
    SCORE_FIELDS,
    SUBMISSION_POLICY,
    TOOL_NAME,
    parse_primary_review_submission,
    primary_review_submission_tools,
)

pytestmark = pytest.mark.unit
BODY = "\n\n".join(
    [
        "The sensor warmed after the technician moved its cable.",
        "The pilot promised to return the spare lens at dawn.",
        "The operator labelled the sample before closing the box.",
        "The rain stopped while the receiver was still silent.",
        "A second observer wrote down the station clock reading.",
        "The team left the locked box beside the receiver.",
    ]
)


@pytest.mark.parametrize(
    ("paragraph_count", "frozen_v1_sha"),
    [
        (1, "601f919d7bcd5947cdfeb2a3d2e0257fc824faefe050cdef5bf7c484288170bf"),
        (3, "90adc66ca5c18635c1de2aafa7214acf864ab31781b6f0b1e8194b8aa4a571a6"),
        (6, "0817c980a27eb569161265806a736189a3fb3968f48185a8cbac1db267fadbf5"),
        (28, "5613bbf0a2ddbedf5febe9bc23a52d378b545d2770c1c7330d38fb712fb20b7a"),
    ],
)
def test_explicit_historical_builder_preserves_frozen_v1_tools(paragraph_count, frozen_v1_sha):
    # Golden hashes captured from the unchanged v1 builder before this policy change.
    body = "\n\n".join(f"Independent source paragraph {n}." for n in range(paragraph_count))
    historical = primary_review_submission_tools(body, submission_policy=LEGACY_SUBMISSION_POLICY)
    canonical = json.dumps(
        historical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == frozen_v1_sha
    future = primary_review_submission_tools(body)
    citations = future[0]["function"]["parameters"]["properties"]["citations"]
    assert citations.pop("uniqueItems") is True
    assert "not votes" in citations.pop("description")
    assert future == historical


@pytest.mark.parametrize("policy", ["invented-policy", None, [], 7])
def test_unknown_tool_policy_cannot_reconstruct_a_historical_request(policy):
    with pytest.raises(ValueError, match=r"^submission_policy_unknown$"):
        primary_review_submission_tools(BODY, submission_policy=policy)


def payload(rows=None):
    return {
        "verdict": "rewrite",
        "scores": dict.fromkeys(SCORE_FIELDS, 0.62),
        "rewrite_direction": "Keep both source-backed observations as separate proposals.",
        "commentary": "The cited paragraph supports separate state and promise observations.",
        "citations": deepcopy(
            rows
            if rows is not None
            else [
                {
                    "paragraph_number": 2,
                    "dimension": "continuity",
                    "reason": "The lens remains promised.",
                },
                {
                    "paragraph_number": 2,
                    "dimension": "continuity",
                    "reason": "The dawn timing is a prediction.",
                },
            ]
        ),
    }


def completion(value):
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    calls = [
        {
            "id": "fixture-physical-call",
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "arguments": raw,
            },
        }
    ]
    return SimpleNamespace(
        content="",
        tool_calls=calls,
        raw_message={
            "role": "assistant",
            "content": "",
            "tool_calls": deepcopy(calls),
        },
    )


def validate(canonical, body=BODY, policy=CRITIC_CITATION_TUPLE_POLICY):
    return validate_critic_commentary(canonical, chapter_text=body, citation_policy=policy)


def canonical_rows(rows):
    return (
        "A full review with exact current sources and no external score claim.\nEVIDENCE_ANCHORS:\n"
        + json.dumps(rows)
    )


@pytest.mark.parametrize("different", ["reason", "dimension"])
def test_same_source_different_issue_is_retained_in_both_layers(different):
    data = payload()
    if different == "dimension":
        data["citations"][1]["reason"] = data["citations"][0]["reason"]
        data["citations"][1]["dimension"] = "goal"
    c = completion(data)
    before = deepcopy(c.__dict__)
    canonical, receipt, errors = parse_primary_review_submission(c, BODY)
    assert errors == () and receipt["schema"] == SUBMISSION_POLICY == "primary-review-submission.v2"
    assert receipt["parsed_payload"] == data and c.__dict__ == before
    assert receipt["raw_arguments"] == c.tool_calls[0]["function"]["arguments"]
    assert (
        receipt["raw_arguments_sha256"]
        == hashlib.sha256(receipt["raw_arguments"].encode()).hexdigest()
    )
    assert receipt["selected_paragraph_numbers"] == [2, 2]
    assert receipt["citation_identity_policy"] == CRITIC_CITATION_TUPLE_POLICY
    assert receipt["issues_dropped"] == 0 and receipt["scores_modified"] is False
    evidence = validate(canonical)
    assert evidence.passed and len(evidence.citations) == 2
    anchor = build_critic_body_anchors(BODY)[1]
    assert [row["reason"] for row in evidence.citations] == [
        row["reason"] for row in data["citations"]
    ]
    for row in evidence.citations:
        assert row["id"] == anchor["id"] and row["text"] == anchor["text"]
        assert row["text"] == BODY[row["start"] : row["end"]]
        assert row["normalization_receipt"]["policy_version"] == CRITIC_CITATION_TUPLE_POLICY
    coverage = receipt["source_coverage"]
    assert (
        coverage
        == evidence.source_coverage
        == {
            "schema": "critic-distinct-source-coverage.v1",
            "citation_row_count": 2,
            "distinct_anchor_ids": [anchor["id"]],
            "distinct_anchor_count": 1,
            "catalog_anchor_count": 6,
            "counts_as_votes": False,
            "scores_modified": False,
        }
    )
    scores = json.loads(canonical.split("SCORES: ", 1)[1].splitlines()[0])
    assert scores == data["scores"]
    # Default legacy policy still rejects the old repeated-selector shape.
    assert not validate_critic_commentary(canonical, chapter_text=BODY).passed
    assert not validate(canonical, policy=CRITIC_ANCHOR_ADAPTER_POLICY).passed


def test_exact_duplicate_tuple_is_invalid_without_salvage_in_either_layer():
    data = payload()
    data["citations"][1] = deepcopy(data["citations"][0])
    canonical, receipt, errors = parse_primary_review_submission(completion(data), BODY)
    assert canonical == "" and errors == ("primary_submission:citation_duplicate:1",)
    assert receipt["parsed_payload"] == data and receipt["status"] == "rejected"
    anchor = build_critic_body_anchors(BODY)[1]
    row = {"id": anchor["id"], "dimension": "continuity", "reason": "Same exact observation."}
    evidence = validate(canonical_rows([row, deepcopy(row)]))
    assert not evidence.passed and evidence.citations == () and evidence.source_coverage is None


def test_all_rows_and_first_seen_source_order_are_preserved_without_coverage_inflation():
    rows = [
        {"paragraph_number": n, "dimension": "continuity", "reason": f"Distinct issue number {i}."}
        for i, n in enumerate([3, 1, 3, 2, 1, 3])
    ]
    canonical, receipt, errors = parse_primary_review_submission(completion(payload(rows)), BODY)
    evidence = validate(canonical)
    assert errors == () and evidence.passed
    assert receipt["selected_paragraph_numbers"] == [3, 1, 3, 2, 1, 3]
    assert [row["reason"] for row in evidence.citations] == [row["reason"] for row in rows]
    assert receipt["source_coverage"] == evidence.source_coverage
    assert evidence.source_coverage["citation_row_count"] == 6
    assert evidence.source_coverage["distinct_anchor_count"] == 3


def test_tool_and_both_validators_keep_existing_capacity_with_no_row_truncation():
    schema = primary_review_submission_tools(BODY)[0]["function"]["parameters"]["properties"][
        "citations"
    ]
    assert schema["maxItems"] == 6 and schema["uniqueItems"] is True
    rows = [
        {"paragraph_number": 1, "dimension": "continuity", "reason": f"Distinct reason {i}."}
        for i in range(7)
    ]
    canonical, receipt, errors = parse_primary_review_submission(completion(payload(rows)), BODY)
    assert canonical == "" and errors == ("primary_submission:citation_count",)
    assert len(receipt["parsed_payload"]["citations"]) == 7
    anchor = build_critic_body_anchors(BODY)[0]
    assert not validate(
        canonical_rows(
            [
                {"id": anchor["id"], "dimension": row["dimension"], "reason": row["reason"]}
                for row in rows
            ]
        )
    ).passed


@pytest.mark.parametrize(
    "fault", ["selector", "bool_selector", "short_dimension", "bad_reason", "extra_quote"]
)
def test_bad_late_item_after_legitimate_source_reuse_rejects_the_whole_submission(fault):
    data = payload()
    data["citations"].append(
        {"paragraph_number": 3, "dimension": "continuity", "reason": "Third issue."}
    )
    item = data["citations"][-1]
    if fault == "selector":
        item["paragraph_number"] = 999
    if fault == "bool_selector":
        item["paragraph_number"] = True
    if fault == "short_dimension":
        item["dimension"] = " "
    if fault == "bad_reason":
        item["reason"] = {"invented": "object"}
    if fault == "extra_quote":
        item["quote"] = "invented source"
    canonical, receipt, errors = parse_primary_review_submission(completion(data), BODY)
    assert canonical == "" and errors and receipt["status"] == "rejected"
    assert receipt["parsed_payload"] == data and len(receipt["parsed_payload"]["citations"]) == 3


@pytest.mark.parametrize(
    "fault", ["foreign_id", "bad_reason", "extra_quote", "missing_dimension", "bad_range"]
)
def test_gate_bad_late_item_never_retains_only_good_prefix(fault):
    anchors = build_critic_body_anchors(BODY)
    rows = [
        {"id": anchors[1]["id"], "dimension": "continuity", "reason": reason}
        for reason in ("First state concern.", "Second promise concern.")
    ]
    bad = {"id": anchors[2]["id"], "dimension": "continuity", "reason": "Late concern."}
    if fault == "foreign_id":
        bad["id"] = build_critic_body_anchors("Different source body.")[0]["id"]
    if fault == "bad_reason":
        bad["reason"] = None
    if fault == "extra_quote":
        bad["quote"] = "invented"
    if fault == "missing_dimension":
        del bad["dimension"]
    if fault == "bad_range":
        bad["id"] = anchors[0]["id"] + "-P9999"
    result = validate(canonical_rows([*rows, bad]))
    assert not result.passed and result.citations == () and result.source_coverage is None


def test_equivalent_range_spellings_use_resolved_tuple_identity():
    anchors = build_critic_body_anchors(BODY)
    short = anchors[0]["id"] + "-P0003"
    long = anchors[0]["id"] + "-" + anchors[2]["id"]
    rows = [
        {"id": short, "dimension": "continuity", "reason": "A shared range supports this issue."},
        {"id": long, "dimension": "continuity", "reason": "Another issue uses the same range."},
    ]
    result = validate(canonical_rows(rows))
    assert result.passed and len(result.citations) == 2
    assert result.source_coverage["distinct_anchor_count"] == 3
    assert [v["raw_citation"] for v in result.citations] == rows
    assert all(
        row["text"] == BODY[anchors[0]["start"] : anchors[2]["end"]] for row in result.citations
    )
    rows[-1]["reason"] = rows[0]["reason"]
    assert not validate(canonical_rows(rows)).passed


def test_overlapping_ranges_count_each_underlying_anchor_once():
    anchors = build_critic_body_anchors(BODY)
    rows = [
        {"id": anchors[0]["id"] + "-P0003", "dimension": "continuity", "reason": "First issue."},
        {"id": anchors[1]["id"] + "-P0004", "dimension": "goal", "reason": "Second issue."},
    ]
    result = validate(canonical_rows(rows))
    assert result.passed and result.source_coverage["citation_row_count"] == 2
    assert result.source_coverage["distinct_anchor_count"] == 4
    assert result.source_coverage["distinct_anchor_ids"] == [row["id"] for row in anchors[:4]]


@pytest.mark.parametrize("policy", ["invented-policy", None, [], 7])
def test_unknown_policy_never_falls_back_to_a_more_permissive_validator(policy):
    canonical, _, errors = parse_primary_review_submission(completion(payload()), BODY)
    assert not errors
    assert not validate(canonical, policy=policy).passed


def test_tuple_policy_does_not_fall_back_to_unbound_free_text_quotes():
    text = 'This critique only quotes an excerpt: EVIDENCE: "The sensor warmed".'
    assert not validate(text).passed


@pytest.mark.parametrize(
    "fault", ["raw_tool_conflict", "raw_content_conflict", "duplicate_json_key"]
)
def test_physical_conflicts_and_ambiguous_json_remain_unknown(fault):
    data = payload()
    c = completion(data)
    if fault == "raw_tool_conflict":
        c.raw_message["tool_calls"][0]["function"]["arguments"] += " "
    elif fault == "raw_content_conflict":
        c.raw_message["content"] = "Different physical content."
    else:
        raw = c.tool_calls[0]["function"]["arguments"].replace(
            '"verdict": "rewrite",', '"verdict": "rewrite", "verdict": "pass",', 1
        )
        c = completion(raw)
    canonical, receipt, errors = parse_primary_review_submission(c, BODY)
    assert canonical == "" and errors and receipt["status"] == "rejected"
