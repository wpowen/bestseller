"""Unit tests for the hard-fact extraction parser.

These lock in the defensive decoding behaviour that prevents the autowrite
pipeline from losing a chapter-state snapshot (or cascading into a broken
transaction) when the LLM returns JSON containing raw control characters.
"""

from __future__ import annotations

import pytest

from bestseller.services.continuity import _parse_extraction_payload


def test_accepts_well_formed_payload() -> None:
    raw = '{"facts":[{"name":"timer","value":"10","kind":"countdown"}]}'

    facts, _time_anchor, _chapter_time_span, err = _parse_extraction_payload(raw)

    assert err is None
    assert len(facts) == 1
    assert facts[0].name == "timer"
    assert facts[0].kind == "countdown"


def test_accepts_payload_with_embedded_newlines_inside_strings() -> None:
    # The LLM occasionally emits real newlines inside string values. Strict
    # ``json.loads`` would reject this as "Invalid control character at…";
    # ``strict=False`` must accept it.
    raw = '{"facts":[{"name":"note","value":"line1\nline2","kind":"other"}]}'

    facts, _time_anchor, _chapter_time_span, err = _parse_extraction_payload(raw)

    assert err is None, f"expected success, got error={err!r}"
    assert len(facts) == 1
    assert "line1" in facts[0].value


def test_accepts_fenced_json_with_control_characters() -> None:
    raw = (
        "```json\n"
        '{"facts":[{"name":"end\tstate","value":"ok","kind":"other"}]}\n'
        "```"
    )

    facts, _time_anchor, _chapter_time_span, err = _parse_extraction_payload(raw)

    assert err is None
    assert len(facts) == 1


def test_sanitizes_stray_control_bytes_before_giving_up() -> None:
    # 0x08 (BACKSPACE) is a real control byte that even ``strict=False``
    # rejects when it leaks outside a string. The second-chance branch
    # should strip it and recover the payload.
    raw = '{"facts":[\x08{"name":"x","value":"1","kind":"other"}]}'

    facts, _time_anchor, _chapter_time_span, err = _parse_extraction_payload(raw)

    assert err is None, f"expected success, got error={err!r}"
    assert len(facts) == 1
    assert facts[0].name == "x"


def test_reports_error_when_nothing_is_recoverable() -> None:
    raw = "this is definitely not JSON"

    facts, _time_anchor, _chapter_time_span, err = _parse_extraction_payload(raw)

    assert facts == []
    assert err is not None
    assert "no_json_object_found" in err


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "\n\n",
    ],
)
def test_reports_empty_response(raw: str) -> None:
    facts, _time_anchor, _chapter_time_span, err = _parse_extraction_payload(raw)

    assert facts == []
    assert err == "empty_response"


def test_extracts_time_anchor_and_chapter_time_span() -> None:
    raw = (
        '{"time_anchor":"末世第 4 天 清晨",'
        '"chapter_time_span":"约 3 小时",'
        '"facts":[{"name":"timer","value":"10","kind":"countdown"}]}'
    )

    facts, time_anchor, chapter_time_span, err = _parse_extraction_payload(raw)

    assert err is None
    assert len(facts) == 1
    assert time_anchor == "末世第 4 天 清晨"
    assert chapter_time_span == "约 3 小时"


def test_time_anchor_defaults_to_none_when_missing() -> None:
    raw = '{"facts":[{"name":"x","value":"1","kind":"other"}]}'

    facts, time_anchor, chapter_time_span, err = _parse_extraction_payload(raw)

    assert err is None
    assert len(facts) == 1
    assert time_anchor is None
    assert chapter_time_span is None


def test_time_anchor_null_is_accepted() -> None:
    raw = (
        '{"time_anchor":null,"chapter_time_span":null,'
        '"facts":[{"name":"x","value":"1","kind":"other"}]}'
    )

    facts, time_anchor, chapter_time_span, err = _parse_extraction_payload(raw)

    assert err is None
    assert time_anchor is None
    assert chapter_time_span is None
