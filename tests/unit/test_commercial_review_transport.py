"""Pure commercial transport tests; no paid calls, DB or grade aliases."""

from copy import deepcopy
import hashlib
import json

import pytest

from bestseller.services import commercial_source_evidence as evidence
from bestseller.services.commercial_review_tool import review_submission_tools
from bestseller.services.commercial_review_transport import (
    CommercialReviewTransportError,
    prepare_commercial_review_transport,
    verify_commercial_review_transport,
)
from bestseller.services.lossless_source_encoding import decode_lossless_source


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _sources(large=False):
    text = "导航员记录月相🌕。完整原文、空格和末尾证据均保留。\n" * (180 if large else 1)
    return evidence.assemble_commercial_sources(
        chapter_number=2,
        content_md="新港口启航🌕。\n\nThe navigator chooses another course.",
        previous_chapters=[
            {"chapter_number": 1, "content_md": "上一航程已结束。\n\nThe old map is sealed."}
        ],
        source={
            "a_long_source_path_" * 8: text,
            "source_copies": [text] * (30 if large else 1),
            "authored_refs": [{"$ref": 0}, {"$ref": "/missing"}],
            "approved_creation_contracts": {
                "clauses": [
                    {
                        "source": "user",
                        "hard": True,
                        "text": "不得改写已封存的旧航程。",
                        "scope": {"chapter_numbers": [2]},
                    }
                ]
            },
        },
    )


def _prepare(
    sources, *, system="独立审查来源与正文。", tail="\nFinal response contract unchanged."
):
    user = "完整来源包:\n" + sources.text + tail
    wire, receipt = prepare_commercial_review_transport(
        system,
        user,
        sources=sources,
        tools=review_submission_tools(sources),
        output_tokens=8192,
    )
    return user, wire, receipt


def test_fitting_prompt_is_byte_exact_identity_and_catalog_unchanged():
    sources = _sources()
    before = deepcopy(sources)
    user, wire, receipt = _prepare(sources)
    assert wire == user and receipt["encoding"] == "identity"
    assert receipt["capacity"]["budget_passed"] and receipt["capacity"]["truncated"] is False
    verify_commercial_review_transport(wire, sources=sources, receipt=receipt)
    assert sources == before


def test_actual_assembler_v1_overflow_preserves_complete_body_previous_catalog_and_clauses():
    sources = _sources(large=True)
    before = deepcopy(sources)
    user, wire, receipt = _prepare(sources)
    assert receipt["encoding"] == "json-pointer"
    assert len(wire) < len(user)
    encoded = json.loads(wire[receipt["packet_start"] : receipt["packet_end"]])
    restored = decode_lossless_source(encoded)
    assert _json(restored) == sources.text
    assert restored["current_body"] == json.loads(sources.text)["current_body"]
    assert restored["previous_chapters"] == json.loads(sources.text)["previous_chapters"]
    assert restored["citation_catalog"] == json.loads(sources.text)["citation_catalog"]
    assert restored["source_context"]["authored_refs"] == [{"$ref": 0}, {"$ref": "/missing"}]
    for entry in sources.catalog.values():
        if entry["kind"] in {"current", "prior"}:
            row = next(row for row in restored["citation_catalog"] if row["id"] == entry["id"])
            body = evidence.resolve_commercial_source_pointer(restored, row["source_ref"])
            assert body[entry["start"] : entry["end"]] == entry["text"]
    assert any(row["kind"] == "constraint" for row in sources.catalog.values())
    assert receipt["original_packet_sha256"] == sources.receipt["assembled_source_sha256"]
    verify_commercial_review_transport(wire, sources=sources, receipt=receipt)
    assert wire.endswith("\nFinal response contract unchanged.")
    assert sources == before


def test_v2_only_when_v1_still_over_budget_including_tools_and_output(monkeypatch):
    sources = _sources(large=True)
    _, v1, first = _prepare(sources)
    assert first["encoding"] == "json-pointer"
    cap = first["capacity"]
    monkeypatch.setattr(
        evidence,
        "TOTAL_TOKEN_BUDGET",
        cap["reserved_input_tokens"] + cap["output_reserve_tokens"] - 1,
    )
    user, wire, receipt = _prepare(sources)
    assert receipt["encoding"] == "indexed-json-pointer"
    assert len(wire) < len(v1) < len(user)
    assert receipt["capacity"]["tool_schema_estimated_tokens"] > 0
    assert receipt["capacity"]["output_reserve_tokens"] == 8192
    assert receipt["capacity"]["budget_passed"]
    verify_commercial_review_transport(wire, sources=sources, receipt=receipt)
    assert (wire, receipt) == _prepare(sources)[1:]


@pytest.mark.parametrize(
    "fault",
    [
        "wire",
        "packet_hash",
        "source_hash",
        "catalog",
        "offset_bool",
        "offset_oob",
        "instruction",
        "original_hash",
        "codec",
    ],
)
def test_physical_wire_binding_rejects_tampering(fault):
    sources = _sources(large=True)
    _, wire, receipt = _prepare(sources)
    if fault == "wire":
        wire += " changed"
    elif fault == "packet_hash":
        receipt["wire_packet_sha256"] = "0" * 64
    elif fault == "source_hash":
        receipt["original_packet_sha256"] = "0" * 64
    elif fault == "catalog":
        receipt["catalog_sha256"] = "0" * 64
    elif fault == "offset_bool":
        receipt["packet_start"] = True
    elif fault == "offset_oob":
        receipt["packet_end"] = len(wire) + 1
    elif fault == "instruction":
        start = receipt["packet_start"]
        wire = wire[: start - 2] + "X" + wire[start - 1 :]
        receipt["wire_prompt_sha256"] = _sha(wire)
    elif fault == "original_hash":
        receipt["original_prompt_sha256"] = "0" * 64
    else:
        receipt["codec_receipt"]["original_sha256"] = "0" * 64
    with pytest.raises((CommercialReviewTransportError, ValueError)):
        verify_commercial_review_transport(wire, sources=sources, receipt=receipt)


def test_decoded_packet_must_equal_expected_full_source_even_if_rebound_wire_hashes():
    sources = _sources(large=True)
    _, wire, receipt = _prepare(sources)
    # A separately valid codec envelope for altered data cannot satisfy the original source proof.
    from bestseller.services.lossless_source_encoding import encode_lossless_source

    changed = json.loads(sources.text)
    changed["current_body"] += " invented fact"
    other = encode_lossless_source(changed)
    packet = _json(other)
    start, end = receipt["packet_start"], receipt["packet_end"]
    wire = wire[:start] + packet + wire[end:]
    receipt.update(
        wire_prompt_sha256=_sha(wire),
        wire_packet_sha256=_sha(packet),
        packet_end=start + len(packet),
        codec_receipt=other["receipt"],
    )
    with pytest.raises(CommercialReviewTransportError, match="decoded_packet_mismatch"):
        verify_commercial_review_transport(wire, sources=sources, receipt=receipt)


def test_complete_retry_and_tools_cannot_bypass_budget_or_retention_limit():
    sources = _sources(large=True)
    with pytest.raises(CommercialReviewTransportError, match="input_budget_exceeded") as error:
        _prepare(sources, tail="\nRepair feedback fully retained:" + "结" * 121000)
    assert error.value.receipt["capacity"]["budget_passed"] is False
    assert error.value.receipt["capacity"]["truncated"] is False
    with pytest.raises(CommercialReviewTransportError, match="input_budget_exceeded"):
        prepare_commercial_review_transport(
            "System",
            sources.text,
            sources=sources,
            tools=[{"huge": "甲" * 121000}],
            output_tokens=8192,
        )


@pytest.mark.parametrize("count", [0, 2])
def test_source_packet_must_have_one_exact_occurrence(count):
    sources = _sources()
    with pytest.raises(CommercialReviewTransportError, match="source_packet_not_unique"):
        prepare_commercial_review_transport(
            "System", sources.text * count, sources=sources, tools=None, output_tokens=8192
        )


def test_noncompressible_source_does_not_grant_budget_or_drop_tail():
    source = "".join(hashlib.sha256(str(index).encode()).hexdigest() for index in range(2400))
    sources = evidence.assemble_commercial_sources(
        chapter_number=1,
        content_md="A new beginning.",
        previous_chapters=[],
        source={"complete": source, "last": "Must retain final source."},
    )
    with pytest.raises(CommercialReviewTransportError, match="input_budget_exceeded"):
        _prepare(sources)
