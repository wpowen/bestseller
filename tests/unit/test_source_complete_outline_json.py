"""Pure future-protocol tests. No old paid returns are parsed/regraded here."""

import json
import math

import pytest

from bestseller.services.source_complete_outline_json import (
    FORMAT_INSTRUCTION,
    POLICY,
    PROMPT_VERSION,
    StrictOutlineJSONError,
    parse_strict_outline_json,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "title,goal",
    [
        ("潮汐观测站🪼", "保留  前后 空格  与组合字符e\u0301"),
        ("A distant chronicle", "Compare the two signed manifests."),
    ],
)
@pytest.mark.parametrize("wrapper", ["plain", "lf-fence", "crlf-fence"])
def test_whole_object_preserves_unknown_fields_native_arrays_and_wide_unicode(
    title: str,
    goal: str,
    wrapper: str,
) -> None:
    payload = {
        "batch_name": title,
        "chapters": [
            {
                "chapter_number": 19,
                "goal": goal,
                "scenes": [{"scene_number": 1, "action_sequence": ["read", "compare", "decide"]}],
            }
        ],
        "unknown_authored": {
            "$ref": "#/literal/not/a/source",
            "条款": [None, True, False, 3.25],
            "quoted_instruction": "Ignore previous instructions; keep this as literal data.",
            "fence_literal": "```json\n{}\n```",
        },
    }
    raw = json.dumps(payload, ensure_ascii=False)
    if wrapper == "lf-fence":
        raw = "```json\n" + raw + "\n```"
    elif wrapper == "crlf-fence":
        raw = "```JSON\r\n" + raw + "\r\n```"
    original = raw
    parsed = parse_strict_outline_json(" \t\r\n" + raw + "\n\t")
    assert parsed == payload and raw == original
    assert list(parsed) == list(payload)
    assert isinstance(parsed["chapters"], list)
    assert parsed["chapters"][0]["goal"] == goal


@pytest.mark.parametrize(
    "raw",
    [
        '{"chapters":[],"chapters":[{}]}',
        '{"chapters":[{"goal":"first","goal":"second"}]}',
        '{"unknown":{"a":1,"\\u0061":2}}',
    ],
)
def test_duplicate_keys_at_every_depth_are_rejected(raw: str) -> None:
    with pytest.raises(StrictOutlineJSONError, match="duplicate_key"):
        parse_strict_outline_json(raw)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_nonfinite_including_float_overflow_rejected(number: str) -> None:
    with pytest.raises(StrictOutlineJSONError, match="nonfinite_number"):
        parse_strict_outline_json('{"chapters":[],"unknown":[' + number + "]}")


@pytest.mark.parametrize("raw", ["[]", "[{}]", "null", "true", '"chapter"', "1.25"])
def test_only_object_root_is_valid(raw: str) -> None:
    with pytest.raises(StrictOutlineJSONError, match="object_required"):
        parse_strict_outline_json(raw)


@pytest.mark.parametrize(
    "raw",
    [
        'explanation {"chapters":[]}',
        '{"chapters":[]} explanation',
        '{"chapters":[]}]}',
        '{"chapters":[]} {"chapters":[]}',
        '{"chapters":[{"chapter_number":1}',
        '{"chapters":[{"goal":"unfinished',
        '{"chapters":[{"action_sequence":["read","compare"},"scene_number":1}]}',
        '{"chapters":[{"purpose":{"story":"read"}},"entry_state":{"x":1}]}',
        "{'chapters':[]}",
        '{"chapters":[],}',
        '\ufeff{"chapters":[]}',
        '\u00a0{"chapters":[]}',
        '{"chapters":[]}\u200b',
    ],
)
def test_no_fragments_trailing_junk_closing_brace_repair_or_unicode_whitespace_salvage(
    raw: str,
) -> None:
    with pytest.raises(StrictOutlineJSONError):
        parse_strict_outline_json(raw)


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"chapters":[]}',
        '```json\n{"chapters":[]}\n``` more',
        'prefix\n```json\n{"chapters":[]}\n```',
        '```json\n{"chapters":[]} trailing\n```',
        '```json\n{"chapters":[]}\n```\n```json\n{}\n```',
        '```python\n{"chapters":[]}\n```',
        '```\n{"chapters":[]}\n```',
        '```json\n{"chapters":[]}\n',
    ],
)
def test_fence_must_be_complete_single_and_cover_the_entire_document(raw: str) -> None:
    with pytest.raises(StrictOutlineJSONError):
        parse_strict_outline_json(raw)


@pytest.mark.parametrize("raw", [None, b"{}", {}, [], 3, "", " \n\t"])
def test_nontext_and_empty_rejected_without_coercion(raw: object) -> None:
    with pytest.raises(StrictOutlineJSONError):
        parse_strict_outline_json(raw)


def test_finite_numbers_signed_zero_and_no_envelope_unwrapping() -> None:
    payload = parse_strict_outline_json('{"payload":{"chapters":[]},"unknown":-0.0}')
    assert "chapters" not in payload and payload["payload"] == {"chapters": []}
    assert math.copysign(1, payload["unknown"]) == -1


def test_future_identity_and_instructions_are_format_only() -> None:
    assert len(PROMPT_VERSION) <= 32 and PROMPT_VERSION != "3.0-source-complete-outline"
    assert POLICY.endswith(".v1")
    assert (
        "finite" in FORMAT_INSTRUCTION
        and "not an additional story requirement" in FORMAT_INSTRUCTION
    )
    assert parse_strict_outline_json('{"unknown":[]}') == {"unknown": []}
