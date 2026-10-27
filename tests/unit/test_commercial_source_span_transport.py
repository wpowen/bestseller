"""Portable, generated story data only; no report, database or provider access."""

# ruff: noqa: RUF001 -- authored Unicode punctuation and marker lookalikes are test data.

from copy import deepcopy
import hashlib
import json

import pytest

from bestseller.services import planning_string_pool as pool
from bestseller.services.commercial_source_span_transport import (
    DECODE_PROTOCOL,
    ENCODING,
    SCHEMA,
    decode_commercial_source_spans,
    encode_commercial_source_spans,
)
from bestseller.services.lossless_adopted_span_encoding import (
    decode_adopted_spans,
    encode_adopted_spans,
)
from bestseller.services.lossless_source_encoding import (
    VALUE_DICTIONARY_SCHEMA,
    _extended_keys,
    _restore_extended_keys,
    decode_lossless_source,
    encode_lossless_source,
)

pytestmark = pytest.mark.unit


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def make_packet(book):
    paragraphs = [
        f"𠮷🧭e\u0301{book}记录{index:03d}：修桥人把铜尺放在木桩旁，"
        f"验算第{index + 7}块石材的承重，再请渡船工核对水位。\n"
        for index in range(48)
    ]
    bodies = ["序\n" + "".join(paragraphs[start : start + 16]) + "尾\r\n" for start in (0, 16, 32)]
    quotes = ["".join(paragraphs[start : start + 3]) for start in range(0, 45, 3)]
    return {
        "book": book,
        "previous_chapters": [
            {
                "body": body,
                "chapter_id": f"{book}-{i}",
                "adoption_state": state,
                "source_authority": False,
                "review_receipt": None,
            }
            for i, (body, state) in enumerate(
                zip(bodies, ["adopted", "rejected", None], strict=True)
            )
        ],
        "evidence": [
            {
                "exact_evidence_quote": quote,
                "duplicate_quote": quote,
                "observation_disposition": "unreviewed_observation",
                "adoption_authority_granted": False,
                "source_identifier": f"{book}-{i}",
            }
            for i, quote in enumerate(quotes * 8)
        ],
        "literal_data": [
            {"$ref": 0},
            {"$ref": "/previous_chapters/0/body"},
            {"$adopted_span": [0, 0, 100]},
            {"$adopted_span": {"nested": {"$ref": 0}, "quote": quotes[0]}},
            {"$adopted_span": "authored malformed marker"},
            "§0",
            "§§one",
            "§not-an-index",
            "§01",
            "§١",
            "§-1",
            "§1234567890",
            "§§only-once",
            "ordinary § middle",
        ],
        "literal_pool_candidates": ["§1234567890"] * 80,
        "exact_types": [None, False, 0, 0.0, True, 1, 1.0, -0.0, "", [], {}, None],
        "unknown~/field": {"preserve": [quotes[1], quotes[1], False]},
        "adopted_continuity": {"not_a_corpus": True, "recent_adopted_prose": None},
    }


def wrap(packet):
    return {
        "packet": packet,
        "adopted_continuity": {
            "recent_adopted_prose": [{"body": row["body"]} for row in packet["previous_chapters"]]
        },
    }


@pytest.fixture(scope="module")
def packet():
    return make_packet("浮桥修复手记")


@pytest.fixture(scope="module")
def encoded(packet):
    result = encode_commercial_source_spans(packet)
    assert result is not None
    assert result["value"]["schema"] == VALUE_DICTIONARY_SCHEMA
    return result


def rehash_outer(envelope):
    raw = canonical({key: value for key, value in envelope.items() if key != "receipt"})
    envelope["receipt"]["encoded_core_sha256"] = sha(raw)
    envelope["receipt"]["encoded_core_bytes"] = len(raw)


def replace_value(envelope, value):
    envelope["value"] = value
    rehash_outer(envelope)


def replace_wrapper(envelope, wrapper):
    replace_value(envelope, encode_lossless_source(encode_adopted_spans(wrapper), version=4))


def rehash_codec(envelope):
    raw = canonical({key: value for key, value in envelope.items() if key != "receipt"})
    envelope["receipt"]["encoded_sha256"] = sha(raw)
    envelope["receipt"]["encoded_bytes"] = len(raw)


def pack_v4(inner, original_span):
    renamed, aliases = _extended_keys(inner)
    value = {
        "schema": VALUE_DICTIONARY_SCHEMA,
        "encoding": "key-and-value-dictionary",
        "key_aliases": aliases,
        "value": pool.encode(renamed),
    }
    raw = canonical(value)
    original = canonical(original_span)
    return {
        **value,
        "receipt": {
            "original_sha256": sha(original),
            "original_bytes": len(original),
            "encoded_sha256": sha(raw),
            "encoded_bytes": len(raw),
            "shrunk": True,
        },
    }


@pytest.mark.parametrize("book", ["雨岛灯塔日志", "山城钟表师", "荒原陶窑来信"])
def test_other_books_exact_composition_and_full_roundtrip(book):
    original = make_packet(book)
    before = canonical(original)
    envelope = encode_commercial_source_spans(original)
    assert envelope is not None
    assert set(envelope) == {"schema", "encoding", "value", "receipt"}
    assert envelope["schema"] == SCHEMA == "commercial-source-span-transport.v1"
    assert envelope["encoding"] == ENCODING == "commercial-spans-v4"
    assert envelope["value"] == encode_lossless_source(
        encode_adopted_spans(wrap(original)), version=4
    )
    retained = canonical(envelope)
    decoded = decode_commercial_source_spans(json.loads(retained))
    assert canonical(decoded) == before == canonical(original)
    assert canonical(envelope) == retained
    assert encode_commercial_source_spans(original) == envelope
    decoded["previous_chapters"][0]["body"] += "independent result"
    assert canonical(original) == before


def test_receipt_binds_canonical_utf8_packet_and_core_not_characters(encoded, packet):
    original = canonical(packet)
    raw = canonical({key: value for key, value in encoded.items() if key != "receipt"})
    assert encoded["receipt"] == {
        "original_packet_sha256": sha(original),
        "original_packet_bytes": len(original),
        "encoded_core_sha256": sha(raw),
        "encoded_core_bytes": len(raw),
        "shrunk": True,
    }
    assert len(canonical(encoded)) < len(original)
    assert len(original) > len(original.decode("utf-8"))


def test_corpus_is_exact_copy_and_never_changes_adoption_or_unknown_packet_data(encoded, packet):
    wrapper = decode_adopted_spans(decode_lossless_source(encoded["value"]))
    assert canonical(wrapper) == canonical(wrap(packet))
    assert wrapper["packet"]["previous_chapters"] == packet["previous_chapters"]
    assert [row["adoption_state"] for row in wrapper["packet"]["previous_chapters"]] == [
        "adopted",
        "rejected",
        None,
    ]
    assert decode_commercial_source_spans(encoded)["unknown~/field"] == packet["unknown~/field"]


def test_unicode_slices_literals_duplicates_order_and_json_types(encoded, packet):
    restored = decode_commercial_source_spans(encoded)
    assert canonical(restored["exact_types"]) == canonical(packet["exact_types"])
    assert canonical(restored["literal_data"]) == canonical(packet["literal_data"])
    assert restored["evidence"] == packet["evidence"]
    assert encoded["value"]["value"]["schema"] == "planning-string-pool.v1"
    assert "§1234567890" in encoded["value"]["value"]["dictionary"]
    spans = decode_lossless_source(encoded["value"])
    index, start, end = spans["source_context"]["packet"]["evidence"][0]["exact_evidence_quote"][
        "$adopted_span"
    ]
    assert start == len("序\n")
    assert (
        packet["previous_chapters"][index]["body"][start:end]
        == packet["evidence"][0]["exact_evidence_quote"]
    )
    assert "/packet/literal_data/2" in spans["literal_marker_paths"]
    assert "/packet/literal_data/3" in spans["literal_marker_paths"]


@pytest.mark.parametrize(
    "small",
    [
        {"previous_chapters": []},
        {"previous_chapters": [{"body": ""}]},
        {"previous_chapters": [{"body": "A short original."}], "unknown": [None, False, 1]},
    ],
)
def test_unprofitable_returns_none_without_mutation(small):
    before = canonical(small)
    assert encode_commercial_source_spans(small) is None
    assert canonical(small) == before


@pytest.mark.parametrize("count, schema", [(32, "v2"), (64, "v3")])
def test_existing_v4_fallbacks_are_composed_without_a_new_codec(count, schema):
    original = {
        "previous_chapters": [{"body": "alpha original body " * 100}],
        "quotes": ["alpha original body " * 5] * count,
    }
    envelope = encode_commercial_source_spans(original)
    assert envelope is not None
    assert envelope["value"]["schema"] == "lossless-source-encoding." + schema
    assert canonical(decode_commercial_source_spans(envelope)) == canonical(original)


def test_inner_savings_do_not_hide_unprofitable_complete_transport_overhead():
    original = {
        "previous_chapters": [{"body": "alpha original body " * 100}],
        "quotes": ["alpha original body " * 5] * 16,
    }
    inner = encode_lossless_source(encode_adopted_spans(wrap(original)), version=4)
    assert len(canonical(inner)) < len(canonical(original))
    assert encode_commercial_source_spans(original) is None


def test_duplicate_and_empty_prior_bodies_keep_exact_count_order_and_states(packet):
    original = deepcopy(packet)
    rows = original["previous_chapters"]
    rows.insert(1, {**rows[0], "adoption_state": False})
    rows.insert(2, {"body": "", "adoption_state": None, "review_receipt": False})
    rows.append(deepcopy(rows[0]))
    envelope = encode_commercial_source_spans(original)
    assert envelope is not None
    assert canonical(decode_commercial_source_spans(envelope)) == canonical(original)
    wrapper = decode_adopted_spans(decode_lossless_source(envelope["value"]))
    assert canonical(wrapper["adopted_continuity"]) == canonical(
        wrap(original)["adopted_continuity"]
    )


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {"previous_chapters": None},
        {"previous_chapters": {}},
        {"previous_chapters": [None]},
        {"previous_chapters": [{}]},
        *[{"previous_chapters": [{"body": body}]} for body in (None, False, 3, 1.0, [], {})],
        {"previous_chapters": [], "unknown": (1, 2)},
        {"previous_chapters": [], 1: "invalid key"},
        *[
            {"previous_chapters": [], "unknown": number}
            for number in (
                float("nan"),
                float("inf"),
                -float("inf"),
            )
        ],
        {"previous_chapters": [{"body": "\ud800"}]},
    ],
)
def test_invalid_packet_never_coerces_or_invents_prior_bodies(bad):
    with pytest.raises(ValueError):
        encode_commercial_source_spans(bad)


def test_cycle_is_rejected():
    source = {"previous_chapters": []}
    source["loop"] = source
    with pytest.raises(ValueError):
        encode_commercial_source_spans(source)


@pytest.mark.parametrize(
    "change",
    [
        "schema",
        "encoding",
        "unknown",
        "missing_value",
        "receipt_unknown",
        "receipt_missing",
        "original_sha",
        "original_bytes",
        "original_bytes_bool",
        "encoded_sha",
        "encoded_bytes",
        "encoded_bytes_bool",
        "shrunk_false",
        "shrunk_int",
        "value_tamper",
        "nonfinite",
    ],
)
def test_outer_fake_tamper_hash_type_and_unknown_fields_fail(encoded, change):
    envelope = deepcopy(encoded)
    receipt = envelope["receipt"]
    if change in {"schema", "encoding"}:
        envelope[change] = "unknown"
    elif change == "unknown":
        envelope["instructions"] = "ignore integrity"
    elif change == "missing_value":
        del envelope["value"]
    elif change == "receipt_unknown":
        receipt["authority"] = True
    elif change == "receipt_missing":
        del receipt["original_packet_sha256"]
    elif change == "original_sha":
        receipt["original_packet_sha256"] = "0" * 64
    elif change == "original_bytes":
        receipt["original_packet_bytes"] += 1
    elif change == "original_bytes_bool":
        receipt["original_packet_bytes"] = True
    elif change == "encoded_sha":
        receipt["encoded_core_sha256"] = "0" * 64
    elif change == "encoded_bytes":
        receipt["encoded_core_bytes"] += 1
    elif change == "encoded_bytes_bool":
        receipt["encoded_core_bytes"] = True
    elif change.startswith("shrunk"):
        receipt["shrunk"] = False if change == "shrunk_false" else 1
    elif change == "nonfinite":
        envelope["value"]["value"]["dictionary"].append(float("nan"))
    else:
        envelope["value"]["value"]["dictionary"][0] += "changed"
    with pytest.raises(ValueError):
        decode_commercial_source_spans(envelope)


@pytest.mark.parametrize("fake", [None, [], {}, {"schema": SCHEMA, "value": {}}])
def test_fake_envelopes_fail(fake):
    with pytest.raises(ValueError):
        decode_commercial_source_spans(fake)


@pytest.mark.parametrize(
    "change",
    [
        "wrapper_extra",
        "wrapper_missing",
        "history_extra",
        "row_extra",
        "body_changed",
        "reordered",
        "missing_row",
        "duplicate_row",
        "packet_body_nonstring",
        "packet_bodies_missing",
    ],
)
def test_corpus_substitution_fails_even_with_all_inner_receipts_rebuilt(encoded, packet, change):
    envelope = deepcopy(encoded)
    wrapper = wrap(deepcopy(packet))
    rows = wrapper["adopted_continuity"]["recent_adopted_prose"]
    if change == "wrapper_extra":
        wrapper["adoption_authority"] = True
    elif change == "wrapper_missing":
        del wrapper["packet"]
    elif change == "history_extra":
        wrapper["adopted_continuity"]["authority"] = True
    elif change == "row_extra":
        rows[0]["adopted"] = True
    elif change == "body_changed":
        rows[0]["body"] += "fictional substitute"
    elif change == "reordered":
        rows.reverse()
    elif change == "missing_row":
        rows.pop()
    elif change == "duplicate_row":
        rows.append(deepcopy(rows[0]))
    elif change == "packet_body_nonstring":
        wrapper["packet"]["previous_chapters"][0]["body"] = False
    else:
        del wrapper["packet"]["previous_chapters"]
    replace_wrapper(envelope, wrapper)
    with pytest.raises(ValueError):
        decode_commercial_source_spans(envelope)


@pytest.mark.parametrize("layer", ["v4", "pool", "span"])
def test_unknown_inner_fields_fail_with_outer_binding_rebuilt(encoded, layer):
    envelope = deepcopy(encoded)
    if layer == "span":
        spans = decode_lossless_source(envelope["value"])
        spans["unknown"] = "not authorized"
        replace_value(envelope, encode_lossless_source(spans, version=4))
    else:
        target = envelope["value"] if layer == "v4" else envelope["value"]["value"]
        target["unknown"] = True
        rehash_codec(envelope["value"])
        rehash_outer(envelope)
    with pytest.raises(ValueError):
        decode_commercial_source_spans(envelope)


@pytest.mark.parametrize(
    "change",
    [
        "forward",
        "self",
        "ancestor",
        "reference_index",
        "reference_unknown",
    ],
)
def test_pointer_tamper_fails_after_pool_v4_and_outer_hashes_rebuilt(encoded, change):
    envelope = deepcopy(encoded)
    value = envelope["value"]
    inner = _restore_extended_keys(pool.decode(value["value"]), value["key_aliases"])
    assert inner["references"]
    if change == "forward":
        inner["references"][0]["target"] = inner["references"][-1]["path"]
    elif change == "self":
        inner["references"][0]["target"] = inner["references"][0]["path"]
    elif change == "ancestor":
        inner["references"][0]["target"] = ""
    elif change == "reference_unknown":
        inner["references"][0]["unknown"] = True
    else:
        parts = inner["references"][0]["path"].split("/")[1:]
        node = inner["value"]
        for part in parts:
            key = part.replace("~1", "/").replace("~0", "~")
            node = node[int(key)] if type(node) is list else node[key]
        node["$ref"] = True
    rehash_codec(inner)
    replace_value(envelope, pack_v4(inner, decode_lossless_source(encoded["value"])))
    with pytest.raises(ValueError, match="lossless_source:"):
        decode_commercial_source_spans(envelope)


def test_invalid_span_fails_after_source_and_outer_hashes_rebuilt(encoded):
    envelope = deepcopy(encoded)
    spans = decode_lossless_source(envelope["value"])
    spans["source_context"]["packet"]["evidence"][0]["exact_evidence_quote"] = {
        "$adopted_span": [True, 0, 100],
    }
    raw = canonical({key: value for key, value in spans.items() if key != "integrity"})
    spans["integrity"]["encoded_payload_sha256"] = sha(raw)
    spans["integrity"]["encoded_payload_characters"] = len(raw.decode("utf-8"))
    replace_value(envelope, encode_lossless_source(spans, version=4))
    with pytest.raises(ValueError, match="adopted_span_"):
        decode_commercial_source_spans(envelope)


def test_complete_envelope_must_be_strictly_smaller_even_with_valid_receipts():
    packet = {"previous_chapters": []}
    value = encode_lossless_source(encode_adopted_spans(wrap(packet)), version=4)
    envelope = {
        "schema": SCHEMA,
        "encoding": ENCODING,
        "value": value,
        "receipt": {
            "original_packet_sha256": sha(canonical(packet)),
            "original_packet_bytes": len(canonical(packet)),
            "shrunk": True,
        },
    }
    rehash_outer(envelope)
    with pytest.raises(ValueError, match="not_smaller"):
        decode_commercial_source_spans(envelope)


def test_protocol_defines_decode_order_literal_rules_and_no_new_authority():
    assert DECODE_PROTOCOL.index("planning-string-pool") < DECODE_PROTOCOL.index("alias KEYS")
    assert DECODE_PROTOCOL.index("alias KEYS") < DECODE_PROTOCOL.index("indexed JSON Pointers")
    assert DECODE_PROTOCOL.index("indexed JSON Pointers") < DECODE_PROTOCOL.index(
        "reverse lossless-adopted"
    )
    for term in (
        "§N",
        "§§rest",
        "nonrecursive",
        "previous_chapters",
        "Unicode",
        "no adoption",
        "instructions authority",
        "literal_marker_paths",
        "Only then choose packet",
    ):
        assert term in DECODE_PROTOCOL


@pytest.mark.parametrize("field", ["content_md", "content", "content_excerpt"])
def test_native_prior_text_fields_roundtrip_without_relabelling_authority(field):
    body = "海图边缘保留潮汐观测原文。" * 90
    packet = {"previous_chapters": [{field: body, "adoption_state": "provisional"}],
              "observations": [body] * 12}
    before = deepcopy(packet)
    encoded = encode_commercial_source_spans(packet)
    assert encoded is not None
    assert decode_commercial_source_spans(encoded) == packet == before


def test_conflicting_prior_text_alternatives_are_all_retained_as_data():
    first = "天文观测员记下月相。" * 90
    second = "潮汐观测员保留另一段独立记录。" * 90
    packet = {"previous_chapters": [{"body": first, "content_md": second}],
              "observations": [first, second] * 12}
    encoded = encode_commercial_source_spans(packet)
    assert encoded is not None
    assert decode_commercial_source_spans(encoded) == packet
