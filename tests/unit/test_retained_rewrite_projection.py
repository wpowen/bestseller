from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from difflib import SequenceMatcher
import hashlib
import inspect
from itertools import product
import json

import pytest

from bestseller.services import retained_rewrite_projection as projection
from bestseller.services.retained_rewrite_projection import project_retained_rewrite

pytestmark = pytest.mark.unit


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _span(parent: str, quote: str, identity: str = "repair") -> dict[str, object]:
    start = parent.index(quote)
    return {
        "id": identity,
        "start": start,
        "end": start + len(quote),
        "quote": quote,
        "sha256": _hash(quote),
    }


def _project(parent, returned, spans, **overrides):
    arguments = {
        "authorized_spans": spans,
        "expected_parent_sha256": _hash(parent),
        "expected_return_sha256": _hash(returned),
    }
    arguments.update(overrides)
    return project_retained_rewrite(parent, returned, **arguments)


@pytest.fixture
def example():
    prefix = (
        "At noon the courier checked in.\r\n"
        "The register was signed in blue ink; its margins and page numbers stay as printed.\r\n"
    )
    quote = "Two plus two totals five."
    suffix = (
        "\r\nThe receipt carried a square stamp. Every other entry belongs to the prior record.\r\n"
        "Closing note: retain the address, spelling, punctuation, and all original spacing."
    )
    parent = prefix + quote + suffix
    return parent, quote, "Two plus two totals four."


def test_discards_unrelated_arrival_change_and_preserves_every_outside_byte(example):
    parent, old, new = example
    returned = parent.replace(old, new).replace("At noon", "At dusk")
    original_records = [_span(parent, old)]
    snapshot = json.dumps(original_records)

    result = _project(parent, returned, original_records)

    assert result.accepted, result.failure_codes
    assert result.candidate_text.encode() == parent.replace(old, new).encode()
    assert result.candidate_text != returned
    assert original_records == json.loads(snapshot)
    applied = result.receipt.applied_spans[0]
    assert result.candidate_text[: applied.start].encode() == parent[: applied.start].encode()
    assert (
        result.candidate_text[applied.start + len(new) :].encode() == parent[applied.end :].encode()
    )
    assert returned[applied.returned_start : applied.returned_end] == new
    assert applied.replacement_sha256 == _hash(new)
    assert result.receipt.fresh_review_required
    assert not result.receipt.automatic_promotion_allowed


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("账面两组各三枚，共计七枚。", "账面两组各三枚，共计六枚。"),  # noqa: RUF001
        ("标点应当收束，", "标点应当收束。"),  # noqa: RUF001
        ("Cafe\u0301 ☕🙂: value wrong!", "Cafe\u0301 ☕🙂: value right!"),
        ("Size: small; keep dry.", "Size: very small; keep dry."),
        ("Size: very small; keep dry.", "Size: small; keep dry."),
        ("Row A\r\nwrong\r\nRow B", "Row A\r\nright\r\nRow B"),
        ("First one; then two.", "First two; then one."),
    ],
)
def test_literal_zh_en_punctuation_unicode_crlf_and_internal_insert_delete(example, old, new):
    base, quote, _ = example
    parent = base.replace(quote, old)
    returned = parent.replace(old, new)
    result = _project(parent, returned, [_span(parent, old)])
    assert result.accepted, result.failure_codes
    assert result.candidate_text.encode("utf-8") == returned.encode("utf-8")
    assert result.receipt.parent_sha256 == _hash(parent)
    assert result.receipt.returned_sha256 == _hash(returned)
    assert result.receipt.candidate_sha256 == _hash(returned)
    assert result.receipt.changed_ratio == max(len(old.encode()), len(new.encode())) / len(
        parent.encode()
    )


def test_multiple_scopes_are_canonical_and_protected_middle_is_exact(example):
    base, first, fixed_first = example
    second, fixed_second = "Closing note", "Closing memo"
    parent = base.replace("The receipt", "🙂e\u0301 The receipt")
    returned = (
        parent.replace(first, fixed_first).replace(second, fixed_second).replace("square", "round")
    )
    spans = [_span(parent, first, "first"), _span(parent, second, "second")]
    forward = _project(parent, returned, spans)
    reverse = _project(parent, returned, spans[::-1])
    assert forward.accepted, forward.failure_codes
    assert forward == reverse
    assert forward.candidate_text == parent.replace(first, fixed_first).replace(
        second, fixed_second
    )
    assert [span.id for span in forward.receipt.applied_spans] == ["first", "second"]
    assert "🙂e\u0301" in forward.candidate_text


def test_receipt_inventories_all_discarded_opcodes_with_exact_offsets_and_hashes(example):
    parent, old, new = example
    returned = (
        parent.replace(old, new).replace("At noon", "At dusk").replace("square", "round") + " EXTRA"
    )
    result = _project(parent, returned, [_span(parent, old)])
    assert result.accepted, result.failure_codes
    scope = result.receipt.applied_spans[0]
    expected = []
    for tag, start, end, reply_start, reply_end in SequenceMatcher(
        None, parent, returned, autojunk=False
    ).get_opcodes():
        if tag != "equal" and not scope.start <= start <= end <= scope.end:
            expected.append(
                {
                    "tag": tag,
                    "parent_start": start,
                    "parent_end": end,
                    "returned_start": reply_start,
                    "returned_end": reply_end,
                    "parent_sha256": _hash(parent[start:end]),
                    "returned_sha256": _hash(returned[reply_start:reply_end]),
                }
            )
    assert [asdict(item) for item in result.receipt.discarded_opcodes] == expected
    assert len(expected) >= 3
    wire = json.dumps(asdict(result.receipt), sort_keys=True, separators=(",", ":"))
    assert all(text not in wire for text in (old, new, "At noon", "At dusk", "EXTRA", "square"))
    assert (
        json.dumps(
            asdict(_project(parent, returned, [_span(parent, old)]).receipt),
            sort_keys=True,
            separators=(",", ":"),
        )
        == wire
    )
    with pytest.raises(FrozenInstanceError):
        result.receipt.changed_ratio = 1.0


def test_api_and_receipt_v1_fields_are_frozen(example):
    signature = inspect.signature(project_retained_rewrite)
    assert list(signature.parameters) == [
        "parent_text",
        "returned_text",
        "authorized_spans",
        "expected_parent_sha256",
        "expected_return_sha256",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in list(signature.parameters.values())[2:]
    )
    parent, old, new = example
    result = _project(parent, parent.replace(old, new), [_span(parent, old)])
    assert list(asdict(result)) == ["accepted", "candidate_text", "receipt", "failure_codes"]
    assert set(asdict(result.receipt)) == {
        "parent_sha256",
        "returned_sha256",
        "candidate_sha256",
        "applied_spans",
        "discarded_opcodes",
        "changed_ratio",
        "schema_version",
        "offset_unit",
        "hash_encoding",
        "fresh_review_required",
        "automatic_promotion_allowed",
    }
    assert result.receipt.schema_version == "retained-rewrite-projection/v1"
    assert result.receipt.offset_unit == "unicode_code_points"
    assert result.receipt.hash_encoding == "utf-8"
    assert result.receipt.parent_sha256 == (
        "c56d75627770e3fe6697de0e3e0e86892fcf85738b7a7c14becf207e33a3d667"
    )


def test_canonical_receipt_has_frozen_hash_and_repeated_projection_is_exact(example):
    parent, old, new = example
    returned = parent.replace(old, new).replace("At noon", "At dusk")
    spans = [_span(parent, old)]
    first = _project(parent, returned, spans)
    assert first.accepted
    assert first == _project(parent, returned, spans)
    assert first.receipt.returned_sha256 == (
        "bd7dec7e16d0a1a81e411a31ed2d9ed822a53f04b67b7ba8805487e0c2251190"
    )
    assert first.receipt.candidate_sha256 == (
        "52b541302a762560f22fd72b64cfd03d43d3b63189f0625ebb81b64cbc80b381"
    )
    wire = json.dumps(asdict(first.receipt), sort_keys=True, separators=(",", ":"))
    assert _hash(wire) == "58ad60f3fd23605e4a36751693d49aaf2ed8eba4c5f5a2ce0f7308b5a1bcf34e"
    # A fresh parent does not silently inherit old-body authority or offsets.
    stale = project_retained_rewrite(
        first.candidate_text,
        returned,
        authorized_spans=spans,
        expected_parent_sha256=_hash(parent),
        expected_return_sha256=_hash(returned),
    )
    assert stale.failure_codes == ("parent_hash_mismatch",)
    assert stale.candidate_text == first.candidate_text


@pytest.mark.parametrize("which", ["parent", "return"])
def test_body_tampering_rejects_before_alignment(example, which):
    parent, old, new = example
    returned = parent.replace(old, new)
    result = _project(
        parent, returned, [_span(parent, old)], **{f"expected_{which}_sha256": "0" * 64}
    )
    assert not result.accepted
    assert result.failure_codes == (f"{which}_hash_mismatch",)
    assert result.candidate_text == parent
    assert result.receipt.applied_spans == result.receipt.discarded_opcodes == ()


@pytest.mark.parametrize("digest", [None, True, 7, "", "0" * 63, "0" * 65, "A" * 64, "g" * 64])
def test_expected_hashes_have_strict_format(example, digest):
    parent, old, new = example
    result = _project(
        parent, parent.replace(old, new), [_span(parent, old)], expected_return_sha256=digest
    )
    assert result.failure_codes == ("invalid_expected_hash",)
    assert result.candidate_text == parent


@pytest.mark.parametrize(
    "records", [None, True, [], {}, (), "[]", '[{"id":"repair"}]', [None], [True], [[1]]]
)
def test_non_json_array_or_record_spans_reject(example, records):
    parent, old, new = example
    result = _project(parent, parent.replace(old, new), records)
    assert not result.accepted
    assert result.failure_codes[0] in {"invalid_authorized_spans", "invalid_span_record"}
    assert result.candidate_text == parent


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("start", True, "invalid_span_record"),
        ("end", False, "invalid_span_record"),
        ("start", 1.0, "invalid_span_record"),
        ("end", "4", "invalid_span_record"),
        ("start", -1, "invalid_span_bounds"),
        ("end", 10_000, "invalid_span_bounds"),
        ("id", "", "invalid_span_record"),
        ("id", "  ", "invalid_span_record"),
        ("id", 3, "invalid_span_record"),
        ("quote", "", "invalid_span_record"),
        ("quote", "wrong ref", "span_quote_mismatch"),
        ("quote", True, "invalid_span_record"),
        ("sha256", "0" * 64, "span_hash_mismatch"),
        ("sha256", False, "invalid_span_record"),
        ("sha256", "A" * 64, "invalid_span_record"),
        ("id", "\ud800", "invalid_span_record"),
    ],
)
def test_malformed_or_tampered_span_fields_reject(example, key, value, code):
    parent, old, new = example
    record = _span(parent, old)
    record[key] = value
    result = _project(parent, parent.replace(old, new), [record])
    assert result.failure_codes == (code,)
    assert result.candidate_text == parent


def test_exact_record_keys_bounds_duplicate_ids_overlap_and_codepoint_refs(example):
    parent, old, new = example
    record = _span(parent, old)
    returned = parent.replace(old, new)
    for invalid in (
        {**record, "authority": True},
        {key: value for key, value in record.items() if key != "quote"},
    ):
        assert _project(parent, returned, [invalid]).failure_codes == ("invalid_span_record",)
    for start, end in [
        (record["start"], record["start"]),
        (record["end"], record["start"]),
        (len(parent), len(parent) + 1),
    ]:
        assert _project(
            parent, returned, [{**record, "start": start, "end": end}]
        ).failure_codes == ("invalid_span_bounds",)
    assert _project(parent, returned, [record, record.copy()]).failure_codes == (
        "duplicate_span_id",
    )
    nested = _span(parent, "two totals", "nested")
    assert _project(parent, returned, [record, nested]).failure_codes == ("overlapping_spans",)
    parent = "🙂汉字" + parent
    record = _span(parent, old)
    byte_start = len(parent[: record["start"]].encode())
    invalid = {**record, "start": byte_start, "end": byte_start + len(old)}
    assert _project(parent, parent.replace(old, new), [invalid]).failure_codes == (
        "span_quote_mismatch",
    )


@pytest.mark.parametrize("quote", ["repeat", "aba"])
def test_repeated_and_overlapping_parent_quotes_reject(quote):
    parent = "repeat repeat ababa with sufficient unique trailing context."
    result = _project(parent, parent.replace(quote, "FIX", 1), [_span(parent, quote)])
    assert result.failure_codes == ("ambiguous_span_quote",)
    assert result.candidate_text == parent


def test_noop_and_each_missing_repair_fail_closed(example):
    parent, old, new = example
    spans = [_span(parent, old)]
    for returned in (parent, parent.replace("noon", "dusk")):
        assert _project(parent, returned, spans).failure_codes == ("missing_repair",)
    spans.append(_span(parent, "Closing note", "second"))
    result = _project(parent, parent.replace(old, new), spans)
    assert result.failure_codes == ("missing_repair",)
    assert result.receipt.applied_spans == ()
    assert result.receipt.discarded_opcodes


@pytest.mark.parametrize("side", ["left", "right", "insert_left", "insert_right"])
def test_crossing_and_boundary_insertions_are_never_assigned_by_guess(example, side):
    parent, old, new = example
    record = _span(parent, old)
    start, end = record["start"], record["end"]
    if side == "left":
        returned = parent[: start - 1] + "XYZ" + parent[end:]
    elif side == "right":
        returned = parent[:start] + "XYZ" + parent[end + 1 :]
    elif side == "insert_left":
        returned = parent[:start] + "INSERT" + new + parent[end:]
    else:
        returned = parent[:start] + new + "INSERT" + parent[end:]
    result = _project(parent, returned, [record])
    assert not result.accepted
    assert result.failure_codes == ("boundary_crossing",)
    assert result.candidate_text == parent


def test_repeated_nearby_anchor_and_duplicated_reply_anchor_reject(example):
    base, old, new = example
    parent = base.replace(old, "xx|" + old + "|yy") + " other | marker |"
    returned = parent.replace("xx|" + old + "|yy", "zz|" + new + "|ww")
    result = _project(parent, returned, [_span(parent, old)])
    assert result.failure_codes == ("ambiguous_alignment",)
    returned = base.replace(old, new) + base[: base.index(old)]
    assert not _project(base, returned, [_span(base, old)]).accepted


@pytest.mark.parametrize("unit", ["a", "汉", "🙂", "ab"])
@pytest.mark.parametrize("operation", ["insert", "delete"])
@pytest.mark.parametrize("edge", ["left", "right"])
def test_repeated_boundary_indels_reject_even_with_unique_long_anchors(
    example, unit, operation, edge
):
    base, old, _ = example
    quote = unit * 2 + "X" if edge == "left" else "X" + unit * 2
    enclosed = unit + quote if edge == "left" else quote + unit
    parent = base.replace(old, enclosed)
    repaired = (
        unit * (3 if operation == "insert" else 1) + "X"
        if edge == "left"
        else "X" + unit * (3 if operation == "insert" else 1)
    )
    # The same returned bytes can arise by changing the protected adjacent run.
    returned = parent.replace(quote, repaired)
    result = _project(parent, returned, [_span(parent, quote)])
    assert not result.accepted
    assert result.failure_codes[0] in {"ambiguous_alignment", "boundary_crossing", "missing_repair"}
    assert result.candidate_text == parent


def test_equivalent_repeat_shifts_wholly_inside_scope_are_safe(example):
    base, old, _ = example
    quote = "<aaXbb>"
    parent = base.replace(old, quote)
    returned = parent.replace(quote, "<aaaXb>")
    result = _project(parent, returned, [_span(parent, quote)])
    assert result.accepted, result.failure_codes
    assert result.candidate_text == returned


def test_exhaustive_small_repeat_cases_never_accept_an_outside_equal_cost_alignment():
    prefix = "Unique ledger heading. Preserve the preceding text and spacing exactly.\n"
    suffix = "\nUnique ledger ending. Preserve the trailing text and all original punctuation."
    checked = unsafe_cases = 0
    for characters in product("ab", repeat=6):
        parent = prefix + "".join(characters) + suffix
        start, end = len(prefix) + 1, len(prefix) + 5
        span = _span(parent, parent[start:end])
        for edit_start in range(start + 1, end):
            for edit_end in (edit_start, edit_start + 1):
                for replacement in ("", "a", "b", "ab", "ba"):
                    returned = parent[:edit_start] + replacement + parent[edit_end:]
                    if returned == parent:
                        continue
                    # Independent oracle: enumerate equal-cost single-edit
                    # representations by exact prefix/suffix equality.
                    outside_equivalent = False
                    width = edit_end - edit_start
                    for other_start in range(len(prefix), len(prefix) + 7 - width):
                        other_end = other_start + width
                        reply_end = other_start + len(replacement)
                        outside = (
                            other_start < start
                            or other_end > end
                            or (not width and other_start in (start, end))
                        )
                        if (
                            outside
                            and parent[:other_start] == returned[:other_start]
                            and parent[other_end:] == returned[reply_end:]
                        ):
                            outside_equivalent = True
                    result = _project(parent, returned, [span])
                    checked += 1
                    if outside_equivalent:
                        unsafe_cases += 1
                        assert not result.accepted
                        assert result.candidate_text == parent
    assert checked == 1536
    assert unsafe_cases == 646


def test_reordered_outside_anchor_evidence_and_reordered_scopes_fail_closed(example):
    parent, old, new = example
    left, right = (
        "The receipt carried a square stamp.",
        "Every other entry belongs to the prior record.",
    )
    returned = parent.replace(old, new).replace(f"{left} {right}", f"{right} {left}")
    result = _project(parent, returned, [_span(parent, old)])
    assert not result.accepted
    assert result.failure_codes == ("ambiguous_alignment",)
    assert result.candidate_text == parent
    first, second = "Closing note", old
    returned = parent.replace(first, "SWAP").replace(second, first).replace("SWAP", new)
    assert not _project(
        parent, returned, [_span(parent, old), _span(parent, first, "other")]
    ).accepted


def test_outside_only_unique_move_is_discarded_without_vetoing_anchored_local_repair(example):
    base, old, new = example
    outside = "UNIQUE OUTSIDE TOKEN"
    parent = (
        "Header: " + outside + ".\n"
        "The next line stays in place and supplies a unique unchanged separator.\n"
        "Destination marker: empty.\n" + base
    )
    returned = (
        parent.replace(outside, "retired")
        .replace("Destination marker: empty", "Destination marker: " + outside)
        .replace(old, new)
    )
    result = _project(parent, returned, [_span(parent, old)])
    assert result.accepted, result.failure_codes
    assert result.candidate_text == parent.replace(old, new)
    assert result.receipt.discarded_opcodes
    applied = result.receipt.applied_spans[0]
    assert all(op.parent_end < applied.start for op in result.receipt.discarded_opcodes)
    assert _project(parent, returned, [_span(parent, old)]) == result


@pytest.mark.parametrize("copies", [1, 2])
@pytest.mark.parametrize("source_inside", [True, False])
def test_unique_changed_fragments_crossing_authorization_owners_reject_move_and_duplication(
    example, copies, source_inside
):
    base, old, _new = example
    moved, other = "移交令", "ΩΩ"
    first, second = (moved, other) if source_inside else (other, moved)
    parent = base.replace(old, first) + "\nUNIQUE OUTSIDE ADDRESS: " + second + "."
    returned = parent.replace(moved, "ξ").replace(other, moved * copies)
    result = _project(parent, returned, [_span(parent, first)])
    assert result.failure_codes == ("reorder_conflict",)
    assert result.candidate_text == parent
    assert result.receipt.applied_spans == ()


def test_short_unique_transfer_cannot_hide_behind_a_longer_repeated_fragment():
    first, second = "REPEATEDLONG|MOVE", "ββ"
    parent = (
        "PREAMBLE: REPEATEDLONG remains in this immutable register heading.\n"
        + first
        + "\nSeparator: All intervening ledger entries retain their exact words and punctuation.\n"
        + second
        + "\nClosing note: retain every trailing character, stamp, signature and address."
    )
    returned = parent.replace(first, "ξ").replace(second, "REPEATEDLONG!MOVE")
    result = _project(
        parent, returned, [_span(parent, first, "first"), _span(parent, second, "second")]
    )
    assert result.failure_codes == ("reorder_conflict",)
    assert result.candidate_text == parent
    assert result.receipt.applied_spans == ()


def test_large_scope_and_expansion_obey_existing_byte_ratio_limit(example):
    parent, old, new = example
    for quote, replacement in [(parent[:120], "X"), (old, new * 10)]:
        returned = parent.replace(quote, replacement)
        result = _project(parent, returned, [_span(parent, quote)])
        assert result.failure_codes == ("changed_ratio_exceeded",)
        assert result.receipt.changed_ratio > 0.25
        assert result.candidate_text == parent


def test_exact_quarter_byte_limit_accepts_and_one_more_byte_rejects():
    old, new = "0123456789abcdefghijklmno", "ABCDEFGHIJKLMNOPQRSTUVWXY"
    parent = "L" * 40 + old + "." * 35
    accepted = _project(parent, parent.replace(old, new), [_span(parent, old)])
    assert accepted.accepted, accepted.failure_codes
    assert accepted.receipt.changed_ratio == 0.25
    rejected = _project(parent, parent.replace(old, new + "Z"), [_span(parent, old)])
    assert rejected.failure_codes == ("changed_ratio_exceeded",)
    assert rejected.receipt.changed_ratio == 0.26
    assert rejected.candidate_text == parent


@pytest.mark.parametrize("edge", ["start", "end"])
def test_document_edge_sentinels_allow_unambiguous_scopes(example, edge):
    parent, old, new = example
    parent = parent.replace(old, "")
    parent = old + parent if edge == "start" else parent + old
    result = _project(parent, parent.replace(old, new), [_span(parent, old)])
    assert result.accepted, result.failure_codes
    assert result.candidate_text == parent.replace(old, new)


def test_empty_replacement_does_not_become_an_implicit_deletion(example):
    parent, old, _ = example
    result = _project(parent, parent.replace(old, ""), [_span(parent, old)])
    assert result.failure_codes == ("empty_replacement",)
    assert result.candidate_text == parent


@pytest.mark.parametrize(
    "parent,returned,code",
    [
        (None, "x", "invalid_text"),
        (True, "x", "invalid_text"),
        ("x", b"x", "invalid_text"),
        ("", "x", "empty_text"),
        ("x", " ", "empty_text"),
        ("\ud800", "x", "invalid_utf8"),
    ],
)
def test_invalid_bodies_are_structured_failures(parent, returned, code):
    result = project_retained_rewrite(
        parent,
        returned,
        authorized_spans=[],
        expected_parent_sha256="0" * 64,
        expected_return_sha256="0" * 64,
    )
    assert result.failure_codes == (code,)
    assert result.candidate_text == (parent if type(parent) is str else None)


def test_resource_limits_precede_unbounded_alignment(example, monkeypatch):
    parent, old, new = example
    monkeypatch.setattr(projection, "_MAX_TEXT_CHARS", len(parent) - 1)
    assert _project(parent, parent.replace(old, new), [_span(parent, old)]).failure_codes == (
        "resource_limit_exceeded",
    )
    monkeypatch.setattr(projection, "_MAX_TEXT_CHARS", 100_000)
    monkeypatch.setattr(projection, "_MAX_MATCH_PAIRS", 1)
    assert _project(parent, parent.replace(old, new), [_span(parent, old)]).failure_codes == (
        "resource_limit_exceeded",
    )


def test_span_and_opcode_limits_and_long_ids_are_fail_closed(example, monkeypatch):
    parent, old, new = example
    returned = parent.replace(old, new)
    record = _span(parent, old)
    assert _project(parent, returned, [{**record, "id": "x" * 257}]).failure_codes == (
        "invalid_span_record",
    )
    assert _project(parent, returned, [record] * 129).failure_codes == ("resource_limit_exceeded",)
    monkeypatch.setattr(projection, "_MAX_OPCODES", 1)
    result = _project(parent, returned, [record])
    assert result.failure_codes == ("resource_limit_exceeded",)
    assert result.receipt.discarded_opcodes
    assert result.receipt.applied_spans == ()


def test_final_application_delegates_to_patch_with_protected_complement(example, monkeypatch):
    parent, old, new = example
    original = projection.apply_rewrite_patch
    observed = []

    def apply(text, patch):
        observed.append(patch)
        return original(text, patch)

    monkeypatch.setattr(projection, "apply_rewrite_patch", apply)
    result = _project(parent, parent.replace(old, new), [_span(parent, old)])
    assert result.accepted
    assert len(observed) == 1
    patch = observed[0]
    assert patch.max_changed_ratio == 0.25
    assert [(span.start, span.end) for span in patch.protected_spans] == [
        (0, parent.index(old)),
        (parent.index(old) + len(old), len(parent)),
    ]
    assert all(
        span.content_hash == _hash(parent[span.start : span.end]) for span in patch.protected_spans
    )
