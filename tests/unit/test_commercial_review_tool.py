import json
from types import SimpleNamespace

import pytest

from bestseller.services.commercial_review_tool import (
    TOOL_NAME,
    review_submission_tools,
    submitted_review_payload,
)
from bestseller.services.commercial_source_evidence import (
    assemble_commercial_sources,
    commercial_input_receipt,
)


def sources(body="守门人把旧信放在桌上。", *, rule=False):
    return assemble_commercial_sources(
        chapter_number=2,
        content_md=body,
        previous_chapters=[{"chapter_number": 1, "body": "昨夜信件送抵港口。"}],
        source={
            "approved_creation_contracts": [{"hard": True, "source": "user", "text": "第一人称。"}]
        }
        if rule
        else {},
    )


def call(raw='{"wire_version":"commercial-review-wire.v2"}', name=TOOL_NAME):
    return {"id": "review-id", "type": "function", "function": {"name": name, "arguments": raw}}


def test_schema_enumerates_only_supplied_ids_and_requires_factual_time_relation():
    src = sources()
    schema = review_submission_tools(src)[0]["function"]["parameters"]
    assert set(schema["required"]) == set(schema["properties"])
    assert "checks" not in schema["properties"]
    variants = schema["properties"]["issues"]["items"]["anyOf"]
    assert len(variants) == 3
    for variant in variants:
        props = variant["properties"]
        assert set(variant["required"]) == set(props)
        assert all(src.catalog[key]["kind"] == "current" for key in props["body_id"]["enum"])
        kind = props["assessment_kind"]["enum"][0]
        if kind == "narrative_quality":
            assert "source_id" not in props and "temporal_relation" not in props
        else:
            allowed = {"current"} if kind == "current_fact_conflict" else {"prior", "observation"}
            assert all(src.catalog[key]["kind"] in allowed for key in props["source_id"]["enum"])
            assert "temporal_relation" in variant["required"]


def test_explicit_rules_and_changed_body_cannot_reuse_stale_tool_schema_or_budget_hash():
    a, b = sources(rule=True), sources("守门人烧毁旧信。", rule=True)
    ta, tb = review_submission_tools(a), review_submission_tools(b)
    checks = ta[0]["function"]["parameters"]["properties"]["checks"]
    expected = {key for key, row in a.catalog.items() if row["kind"] == "constraint"}
    assert set(checks["required"]) == expected == set(checks["properties"])
    ra = commercial_input_receipt(
        a, system_prompt="sys", user_prompt=a.text, output_tokens=8192, tools=ta
    )
    rb = commercial_input_receipt(
        b, system_prompt="sys", user_prompt=b.text, output_tokens=8192, tools=tb
    )
    plain = commercial_input_receipt(a, system_prompt="sys", user_prompt=a.text, output_tokens=8192)
    assert ra["tool_schema_sha256"] != rb["tool_schema_sha256"]
    assert ra["tool_schema_estimated_tokens"] > 0
    assert ra["reserved_input_tokens"] > plain["reserved_input_tokens"]


@pytest.mark.parametrize(
    "calls",
    [None, [], [call(), call()], [call(name="rewrite_chapter")], [call(raw={})], [call(raw="")]],
)
def test_missing_multiple_or_executable_tool_names_cannot_grant_report_authority(calls):
    original = json.dumps(calls)
    raw, receipt, errors = submitted_review_payload(
        SimpleNamespace(tool_calls=calls, content="original text")
    )
    assert errors and raw == "original text" and receipt["status"] == "rejected"
    assert receipt["tools_executed"] == 0 and json.dumps(calls) == original


def test_single_submission_retains_exact_arguments_and_does_not_execute_tools():
    raw = '{"wire_version":"commercial-review-wire.v2", "pass": false}'
    result, receipt, errors = submitted_review_payload(
        SimpleNamespace(tool_calls=[call(raw)], content="")
    )
    assert result == raw and errors == () and receipt["status"] == "received"
    assert receipt["tools_executed"] == 0 and receipt["calls_received"] == 1
