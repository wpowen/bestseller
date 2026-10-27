"""Pure lossless-source codec checks. No database or provider access."""

from copy import deepcopy
import hashlib
import json

import pytest

from bestseller.services.lossless_adopted_span_encoding import (
    MARKER, MIN_SPAN_CHARACTERS, decode_adopted_spans, encode_adopted_spans,
)

pytestmark = pytest.mark.unit


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def source():
    excerpt = "她把两份记录并排展开，逐字核对时间和笔迹，再将原件交给仍在门外等待的见证人。" * 3
    body = "𠮷🧪e\u0301开头\n\n" + excerpt + "\n结尾。"
    return {
        "adopted_continuity": {"recent_adopted_prose": [
            {"chapter_id": "chapter-one", "body": body},
            {"chapter_id": "chapter-two", "body": "Another chapter."},
        ], "source_bound_canon": [{"evidence": excerpt, "value": excerpt}],
            "source_bound_timeline": [{"evidence": excerpt}]},
        "current_source_channels": {"quote": excerpt, "not_exact": excerpt + "不同", "short": "开头"},
        "ordered": [False, 0, None, "", excerpt, excerpt],
    }


def rehash(encoded):
    raw = canonical({key: value for key, value in encoded.items() if key != "integrity"})
    encoded["integrity"]["encoded_payload_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    encoded["integrity"]["encoded_payload_characters"] = len(raw)


def marker(encoded):
    return encoded["source_context"]["current_source_channels"]["quote"]


def test_roundtrip_unicode_ranges_hashes_order_duplicates_and_no_input_mutation():
    original = source()
    before = deepcopy(original)
    encoded = encode_adopted_spans(original)
    retained = deepcopy(encoded)
    decoded = decode_adopted_spans(json.loads(json.dumps(encoded)))
    assert decoded == original == before
    assert canonical(decoded) == canonical(original)
    assert encoded == retained
    assert encoded["integrity"]["span_count"] == 6
    assert encoded["literal_marker_paths"] == []
    assert encoded["corpus_paths"] == [
        "/adopted_continuity/recent_adopted_prose/0/body",
        "/adopted_continuity/recent_adopted_prose/1/body",
    ]
    index, start, end = marker(encoded)[MARKER]
    assert index == 0 and start == len("𠮷🧪e\u0301开头\n\n")
    assert end - start == len(original["current_source_channels"]["quote"])
    assert encoded["source_context"]["adopted_continuity"]["recent_adopted_prose"] == original["adopted_continuity"]["recent_adopted_prose"]
    # The excerpt has six references plus its one original location in the body;
    # no separate corpus text table duplicates that body.
    assert set(encoded) == {"schema", "minimum_span_characters", "corpus_paths",
                            "literal_marker_paths", "source_context", "integrity"}
    assert encoded["source_context"]["current_source_channels"]["not_exact"] == original["current_source_channels"]["not_exact"]
    assert encoded["source_context"]["current_source_channels"]["short"] == "开头"
    raw = canonical(original)
    assert encoded["integrity"]["original_source_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert encoded["integrity"]["original_source_characters"] == len(raw)


@pytest.mark.parametrize("authored", [
    {MARKER: [0, 0, 100]}, {MARKER: [999, -2, 0]}, {MARKER: "ordinary authored data"},
    {MARKER: {"nested": {MARKER: [0, 0, 100]}}},
])
def test_authored_marker_objects_are_explicit_literals_even_when_reference_shaped(authored):
    original = source()
    original["a~/b"] = deepcopy(authored)
    original["ordinary"] = {MARKER: "a key among other keys", "text": original["current_source_channels"]["quote"]}
    encoded = encode_adopted_spans(original)
    assert encoded["literal_marker_paths"] == ["/a~0~1b"]
    assert encoded["source_context"]["a~/b"] == authored
    assert decode_adopted_spans(encoded) == original
    assert encoded["integrity"]["literal_marker_count"] == 1


def test_root_authored_marker_and_empty_history_roundtrip():
    for value in ({}, {MARKER: [0, 0, 100]}, {"adopted_continuity": {"recent_adopted_prose": []}}):
        encoded = encode_adopted_spans(value)
        assert encoded["corpus_paths"] == []
        assert encoded["integrity"]["span_count"] == 0
        assert decode_adopted_spans(encoded) == value


def test_exact_matching_does_not_normalize_or_join_noncontiguous_quotes():
    original = source()
    body = original["adopted_continuity"]["recent_adopted_prose"][0]["body"]
    original["lookalikes"] = [body.replace("e\u0301", "é"), body[:50] + "…" + body[-60:],
        body.replace("\n", " ")]
    encoded = encode_adopted_spans(original)
    assert encoded["source_context"]["lookalikes"] == original["lookalikes"]
    assert decode_adopted_spans(encoded) == original


@pytest.mark.parametrize("span", [[True, 0, 100], [0, False, 100], [0, 0, 100.0],
    [99, 0, 100], [0, -1, 100], [0, 0, 999999], [0, 100, 50], [0, 0, 63],
    [0, 0], "not a range"])
def test_bad_ranges_fail_even_after_encoded_checksum_is_recomputed(span):
    encoded = encode_adopted_spans(source())
    marker(encoded)[MARKER] = span
    rehash(encoded)
    with pytest.raises(ValueError, match="adopted_span_"):
        decode_adopted_spans(encoded)


@pytest.mark.parametrize("change", ["body", "literal", "schema", "corpus_path", "span_count",
    "source_sha", "source_length", "encoded_sha", "encoded_length", "extra_field", "threshold",
    "nonexistent_literal", "fake_literal", "duplicate_literal"])
def test_tampered_content_metadata_or_escape_inventory_is_rejected(change):
    encoded = encode_adopted_spans(source())
    if change == "body":
        encoded["source_context"]["adopted_continuity"]["recent_adopted_prose"][0]["body"] += "x"
    elif change == "literal":
        encoded["source_context"]["ordered"][0] = True
    elif change == "schema":
        encoded["schema"] = "unknown"
    elif change == "corpus_path":
        encoded["corpus_paths"] = ["/current_source_channels/quote"]
    elif change == "span_count":
        encoded["integrity"]["span_count"] += 1
    elif change == "source_sha":
        encoded["integrity"]["original_source_sha256"] = "0" * 64
    elif change == "source_length":
        encoded["integrity"]["original_source_characters"] += 1
    elif change == "encoded_sha":
        encoded["integrity"]["encoded_payload_sha256"] = "0" * 64
    elif change == "encoded_length":
        encoded["integrity"]["encoded_payload_characters"] += 1
    elif change == "extra_field":
        encoded["hidden_override"] = True
    elif change == "threshold":
        encoded["minimum_span_characters"] = 1
    else:
        paths = {"nonexistent_literal": ["/absent"], "fake_literal": ["/ordered"],
            "duplicate_literal": ["/absent", "/absent"]}[change]
        encoded["literal_marker_paths"] = paths
        encoded["integrity"]["literal_marker_count"] = len(paths)
    if change not in {"encoded_sha", "encoded_length"}:
        rehash(encoded)
    with pytest.raises(ValueError, match="adopted_span_"):
        decode_adopted_spans(encoded)


@pytest.mark.parametrize("bad", [None, [], {"x": (1, 2)}, {1: "nonstring key"}, {"x": float("nan")},
    {"x": float("inf")}, {"x": "\ud800"}, {"adopted_continuity": None},
    {"adopted_continuity": {"recent_adopted_prose": [{}]}},
    {"adopted_continuity": {"recent_adopted_prose": [{"body": 10}]}}])
def test_non_json_or_malformed_declared_corpus_is_not_silently_coerced(bad):
    with pytest.raises(ValueError, match="adopted_span_"):
        encode_adopted_spans(bad)


def test_cycles_reject_without_recursing_forever():
    original = {"x": []}
    original["x"].append(original)
    with pytest.raises(ValueError, match="adopted_span_invalid_json"):
        encode_adopted_spans(original)


def test_threshold_boundaries_and_distinct_corpus_indexes_are_deterministic():
    a, b = "甲" * MIN_SPAN_CHARACTERS, "乙" * MIN_SPAN_CHARACTERS
    original = {"adopted_continuity": {"recent_adopted_prose": [{"body": a}, {"body": b}]},
        "values": [a, b, a[:-1]]}
    one, two = encode_adopted_spans(original), encode_adopted_spans(deepcopy(original))
    assert one == two
    assert one["source_context"]["values"] == [{MARKER: [0, 0, len(a)]}, {MARKER: [1, 0, len(b)]}, a[:-1]]
    assert decode_adopted_spans(one) == original
