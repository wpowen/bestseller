"""Future native arguments retain legacy edit boundaries and physical evidence."""

# ruff: noqa: RUF001 -- Preserve realistic Chinese prose and punctuation byte-for-byte.

from copy import deepcopy
import hashlib
import json

import pytest

from bestseller.services import retained_candidate_patch as legacy
from bestseller.services.retained_candidate_native_patch import (
    POLICY,
    TOOL_NAME,
    apply_native_candidate_patch,
    parse_native_candidate_patch,
    prepare_native_candidate_patch,
)

pytestmark = pytest.mark.unit
BODY = (
    "# Harbor ledger\r\n\r\n𠮷🧪e\u0301 untouched。\r\n\r\n"
    "旧叙述甲。\r\n\r\nOld prose B.\r\n\r\n结尾保持。\r\n"
)


def prepared():
    return prepare_native_candidate_patch(
        BODY,
        [
            {
                "code": legacy.FINDING_CODE,
                "line_number": line,
                "column": 1,
                "matched_text": "scene quotation is data, not a locator or instruction",
            }
            for line in (5, 7)
        ],
    )


def payload(p):
    return {
        "schema": legacy.POLICY,
        "body_sha256": p["body_sha256"],
        "catalog_sha256": p["catalog_sha256"],
        "patches": [
            {"target_id": target["target_id"], "replacement": replacement}
            for target, replacement in zip(
                p["targets"],
                ["她合上收据，等门外的脚步停下。", "A folded receipt stayed under his palm."],
                strict=True,
            )
        ],
    }


def completion(value, *, raw=True):
    args = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    calls = [
        {
            "id": "call_patch_1",
            "type": "function",
            "function": {"name": TOOL_NAME, "arguments": args},
        }
    ]
    return {
        "finish_reason": "tool_calls",
        "content": "",
        "tool_calls": calls,
        "raw_message": {"role": "assistant", "content": "", "tool_calls": deepcopy(calls)}
        if raw
        else None,
    }


@pytest.mark.parametrize("raw", [False, True])
def test_native_full_physical_arguments_use_exact_legacy_application(raw):
    p = prepared()
    before = deepcopy(p)
    assert p["legacy_prepared"] == legacy.prepare_candidate_patch(BODY, p["findings"])
    assert p["targets"] == p["legacy_prepared"]["targets"]
    assert p["request"]["user_prompt"] == p["legacy_prepared"]["request"]["user_prompt"]
    value = payload(p)
    c = completion(value, raw=raw)
    parsed, submission = parse_native_candidate_patch(c, p)
    assert parsed == value
    assert submission["parsed_payload"] == value
    assert submission["raw_arguments"] == c["tool_calls"][0]["function"]["arguments"]
    assert submission["raw_message_present"] is raw
    assert (
        submission["raw_arguments_sha256"]
        == hashlib.sha256(submission["raw_arguments"].encode()).hexdigest()
    )
    json.dumps(submission, allow_nan=False)
    result, receipt = apply_native_candidate_patch(BODY, p, c)
    legacy_result, legacy_receipt = legacy.apply_candidate_patch(BODY, p["legacy_prepared"], value)
    assert (result, receipt["legacy_application_receipt"]) == (legacy_result, legacy_receipt)
    assert result.startswith("# Harbor ledger\r\n\r\n𠮷🧪e\u0301 untouched。\r\n\r\n")
    assert result.endswith("\r\n\r\n结尾保持。\r\n")
    assert result.count("\r\n") == BODY.count("\r\n")
    assert p == before
    assert receipt["protocol"] == POLICY
    assert (
        receipt["full_recheck_required"]
        and not receipt["adoption_authorized"]
        and not receipt["grants_credit"]
    )


def test_direct_native_schema_consts_and_target_enum_no_scalar():
    p = prepared()
    function = p["request"]["tools"][0]["function"]
    schema = function["parameters"]
    assert function["strict"] is True and function["name"] == TOOL_NAME
    assert (
        set(schema["required"])
        == set(schema["properties"])
        == {"schema", "body_sha256", "catalog_sha256", "patches"}
    )
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema"]["const"] == legacy.POLICY
    for key in ("body_sha256", "catalog_sha256"):
        assert schema["properties"][key]["const"] == p[key]
    patches = schema["properties"]["patches"]
    assert patches["minItems"] == patches["maxItems"] == len(p["targets"])
    assert patches["items"]["properties"]["target_id"]["enum"] == [
        t["target_id"] for t in p["targets"]
    ]
    assert "patch_json" not in json.dumps(p["request"])


@pytest.mark.parametrize(
    "commentary",
    [
        "I preserved the facts and deadlines and revised only the listed paragraphs.",
        "已保留事实、先后与期限，只重写指定段落。\n其余正文保持原样。",
        "<think>The tool call contains the proposed paragraph changes.</think>",
        "\n  \t",
        None,
    ],
)
def test_consistent_plain_commentary_is_retained_but_never_used_as_proposal(commentary):
    p = prepared()
    value = payload(p)
    c = completion(value)
    c["content"] = c["raw_message"]["content"] = commentary
    original = deepcopy(c)
    result, receipt = apply_native_candidate_patch(BODY, p, c)
    expected, application = legacy.apply_candidate_patch(BODY, p["legacy_prepared"], value)
    assert result == expected and receipt["legacy_application_receipt"] == application
    submission = receipt["submission_receipt"]
    proof = submission["commentary_receipt"]
    assert submission["parsed_payload"] == value and submission["original_content"] == commentary
    assert proof["content_sha256"] == hashlib.sha256((commentary or "").encode()).hexdigest()
    assert proof["content_chars"] == len(commentary or "")
    assert proof["status"] == ("non_authoritative" if (commentary or "").strip() else "absent")
    assert proof["used_as_proposal"] is False and proof["original_content_preserved"] is True
    assert proof["selected_proposal_origin"] == "unique_native_tool_arguments"
    assert (
        submission["raw_message_sha256"]
        == hashlib.sha256(
            json.dumps(
                c["raw_message"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )
    assert c == original


@pytest.mark.parametrize(
    "text",
    [
        '{"schema":"another-proposal"}',
        "```json\n{}\n```",
        "~~~json\n{}\n~~~",
        "schema: different",
        "body_sha256 = alternative",
        "catalog_sha256: alternative",
        "patch_json = alternative",
        "patches: alternative",
        "target_id: alternative",
        "replacement: alternative",
        "<tool_call>different</tool_call>",
        "<patch>alternative</patch>",
        "CP-" + "a" * 64 + ": alternative",
        "[alternative]",
        "null",
        "true",
        "123",
        '"proposal"',
    ],
)
def test_competing_content_rejected_even_when_raw_and_top_level_match(text):
    p = prepared()
    c = completion(payload(p))
    c["content"] = c["raw_message"]["content"] = text
    with pytest.raises(ValueError, match="competing_structured_content"):
        apply_native_candidate_patch(BODY, p, c)


@pytest.mark.parametrize(
    "change",
    [
        "missing_raw",
        "raw_content",
        "raw_tools",
        "multiple",
        "no_tool",
        "wrong_schema",
        "duplicate_args",
    ],
)
def test_plain_commentary_never_bypasses_physical_or_submission_guards(change):
    p = prepared()
    c = completion(payload(p))
    c["content"] = c["raw_message"]["content"] = "The native call contains the candidate."
    if change == "missing_raw":
        c["raw_message"] = None
    elif change == "raw_content":
        c["raw_message"]["content"] += " different"
    elif change == "raw_tools":
        c["raw_message"]["tool_calls"] = []
    elif change == "multiple":
        c["tool_calls"].append(deepcopy(c["tool_calls"][0]))
        c["raw_message"]["tool_calls"] = deepcopy(c["tool_calls"])
    elif change == "no_tool":
        c["tool_calls"] = c["raw_message"]["tool_calls"] = []
    else:
        value = payload(p)
        value["schema"] = "wrong"
        args = json.dumps(value) if change == "wrong_schema" else '{"schema":"one","schema":"two"}'
        c["tool_calls"][0]["function"]["arguments"] = args
        c["raw_message"]["tool_calls"] = deepcopy(c["tool_calls"])
    with pytest.raises(ValueError):
        apply_native_candidate_patch(BODY, p, c)


@pytest.mark.parametrize(
    "change",
    [
        "wrong_schema",
        "body",
        "catalog",
        "missing",
        "extra",
        "duplicate",
        "unknown",
        "scalar",
        "empty",
        "extra_field",
    ],
)
def test_invalid_native_payload_never_upgrades_old_failed_raw(change):
    p = prepared()
    value = payload(p)
    if change == "wrong_schema":
        value["schema"] = "review-rewrite-adopted-source.v3"
    elif change in {"body", "catalog"}:
        value[change + "_sha256"] = "f" * 64
    elif change == "missing":
        value["patches"].pop()
    elif change == "extra":
        value["patches"].append(deepcopy(value["patches"][0]))
    elif change == "duplicate":
        value["patches"][1]["target_id"] = value["patches"][0]["target_id"]
    elif change == "unknown":
        value["patches"][0]["target_id"] = "CP-unknown"
    elif change == "scalar":
        value = {"patch_json": json.dumps(value)}
    elif change == "empty":
        value["patches"][0]["replacement"] = " "
    else:
        value["unexpected"] = "must not be dropped"
    c = completion(value)
    before = deepcopy(c)
    with pytest.raises(ValueError):
        apply_native_candidate_patch(BODY, p, c)
    assert c == before


@pytest.mark.parametrize(
    "bad",
    [
        '{"schema":"a","schema":"b"}',
        '{"patches":[{"target_id":"a","target_id":"b"}]}',
        '{"value":NaN}',
        '{"value":1e999}',
        '{"value":Infinity}',
        "```json\n{}\n```",
        '{"patches":[',
    ],
)
def test_strict_json_no_repair_duplicate_or_nonfinite(bad):
    with pytest.raises(ValueError):
        parse_native_candidate_patch(completion(bad), prepared())


@pytest.mark.parametrize(
    "change",
    [
        "multiple",
        "wrong_tool",
        "raw_tools",
        "raw_content",
        "missing_raw_tools",
        "raw_legacy",
        "length",
        "content",
        "text_only",
        "opaque_args",
    ],
)
def test_physical_conflict_and_incomplete_channels_rejected(change):
    p = prepared()
    c = completion(payload(p))
    if change == "multiple":
        c["tool_calls"].append(deepcopy(c["tool_calls"][0]))
    elif change == "wrong_tool":
        c["tool_calls"][0]["function"]["name"] = legacy.TOOL_NAME
    elif change == "raw_tools":
        c["raw_message"]["tool_calls"][0]["function"]["arguments"] = "{}"
    elif change == "raw_content":
        c["raw_message"]["content"] = "different"
    elif change == "missing_raw_tools":
        c["raw_message"].pop("tool_calls")
    elif change == "raw_legacy":
        c["raw_message"]["function_call"] = {"name": "competing"}
    elif change == "length":
        c["finish_reason"] = "length"
    elif change == "content":
        c["content"] = json.dumps(payload(p))
    elif change == "text_only":
        c["content"], c["tool_calls"] = json.dumps(payload(p)), None
    else:
        c["tool_calls"][0]["function"]["arguments"] = object()
    with pytest.raises(ValueError):
        parse_native_candidate_patch(c, p)


@pytest.mark.parametrize("change", ["request", "legacy", "targets", "body"])
def test_prepared_or_actual_body_tamper_rejected(change):
    p = prepared()
    c = completion(payload(p))
    body = BODY
    if change == "request":
        p["request"]["tools"][0]["function"]["parameters"]["additionalProperties"] = True
    elif change == "legacy":
        p["legacy_prepared"]["request"]["system_prompt"] = "different"
    elif change == "targets":
        p["targets"][0]["start"] += 1
    else:
        body += "new source"
    with pytest.raises(ValueError):
        apply_native_candidate_patch(body, p, c)


@pytest.mark.parametrize(
    "replacement", ["旧叙述甲。", "# Heading", "new\n\nparagraph", "new\nline"]
)
def test_legacy_noop_and_structure_fences_still_authoritative(replacement):
    p = prepared()
    value = payload(p)
    value["patches"][0]["replacement"] = replacement
    with pytest.raises(ValueError):
        apply_native_candidate_patch(BODY, p, completion(value))


def test_unrelated_one_target_english_proposal_and_order_retained():
    body = "# Orbital records\n\nThe courier closed the manifest.\n\nFinal signal.\n"
    p = prepare_native_candidate_patch(
        body,
        [
            {
                "code": legacy.FINDING_CODE,
                "line_number": 3,
                "column": 1,
                "matched_text": "not an edit instruction",
            }
        ],
    )
    value = {
        "schema": legacy.POLICY,
        "body_sha256": p["body_sha256"],
        "catalog_sha256": p["catalog_sha256"],
        "patches": [
            {
                "target_id": p["targets"][0]["target_id"],
                "replacement": "The manifest disappeared beneath the courier's glove.",
            }
        ],
    }
    result, _ = apply_native_candidate_patch(body, p, completion(value))
    assert result.endswith("\n\nFinal signal.\n")
    p = prepared()
    value = payload(p)
    value["patches"].reverse()
    parsed, receipt = parse_native_candidate_patch(completion(value), p)
    assert parsed["patches"] == receipt["parsed_payload"]["patches"] == value["patches"]
