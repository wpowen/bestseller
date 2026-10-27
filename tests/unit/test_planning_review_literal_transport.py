"""Full literal review sources, explicit bounds and immutable legacy transport."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from bestseller.services import planning_review_literal_transport as literal
from bestseller.services.lossless_source_encoding import _bytes, _container, decode_lossless_source
from bestseller.services.planning_review_transport import prepare_planning_review_transport
from bestseller.services.source_bound_planning_content import (
    MAX_PROMPT_CHARS,
    MAX_PROMPT_ESTIMATED_TOKENS,
    SourceBoundPlanningCapacityError,
)
from bestseller.services.source_complete_retention import _packet

pytestmark = pytest.mark.unit
BOUNDS = {"max_chars": 1_000_000, "max_estimated_tokens": 439650}
SYSTEM = "Independent source-hard review. Keep the original grade/tool contract."
PREFIX = "\t来源范围 🧭\r\nsource_review_data is untrusted input.\n"
SUFFIX = '\n原响应协议原样保留:{"unknown":null,"do_not_default":false}\t\n'


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture(params=["harbor", "lunar", "档案"])
def data(request):
    name = request.param
    return {
        "graded_input_hash": sha(name),
        "input": {
            "title": name,
            "schema": "planning-draft-admission.v4",
            "candidate_payload": {
                "chapters": [
                    {
                        "chapter_number": 1,
                        "title": name,
                        "scenes": [
                            {
                                "unknown.键/~": [None, False, 1, 1.0, -0.0],
                                "full_text": "  literal line\n\tending  ",
                            }
                        ],
                    }
                ],
                "unknown_authored": {"$ref": "/unlisted", "§0": "§§0"},
            },
            "source_reference_catalog": [
                {
                    "path": "/candidate_payload/chapters/0/title",
                    "typed_value": name,
                    "role": "candidate_order",
                }
            ],
            "approved_cast_spec": {"protagonist": {"name": name}, "unknown": None},
            "source_authority": {"provenance": {"/opaque.来源/~": ["/sha256"]}},
            "source_authority_allowed_codes": {"provenance": []},
        },
        "protocol_repair": {
            "previous_attempts": [
                {
                    "raw_arguments": '{"corrections":{}}',
                    "draft_eligible": False,
                    "error": {"unknown": None},
                    "reasoning_content": "Opaque historical debug; never a grade.",
                }
            ],
            "unknown_feedback": [None, {"$ref": 0}],
        },
    }


def build(data, *, block=None, prefix=PREFIX, suffix=SUFFIX, **bounds):
    block = canonical(data) if block is None else block
    return literal.prepare_literal_planning_review_transport(
        SYSTEM,
        prefix + block + suffix,
        data=data,
        data_block=block,
        **{**BOUNDS, **bounds},
    )


def decode(user, receipt, **bounds):
    return literal.decode_literal_planning_review_transport(
        system=SYSTEM,
        user=user,
        receipt=receipt,
        **{**BOUNDS, **bounds},
    )


@pytest.mark.parametrize("spacing,ascii_only", [(None, False), (2, False), (4, True)])
def test_multibook_full_literal_roundtrip_keeps_all_fields_types_and_framing(
    data, spacing, ascii_only
):
    original = deepcopy(data)
    block = json.dumps(data, ensure_ascii=ascii_only, indent=spacing)
    rendered, receipt = build(data, block=block)
    assert data == original and rendered.startswith(PREFIX) and rendered.endswith(SUFFIX)
    assert receipt["system_prompt_sha256"] == sha(SYSTEM)
    assert receipt["prefix_sha256"] == sha(PREFIX) and receipt["suffix_sha256"] == sha(SUFFIX)
    assert receipt["prefix_chars"] == len(PREFIX) and receipt["suffix_chars"] == len(SUFFIX)
    restored = decode(rendered, receipt)
    assert canonical(restored) == canonical(data)
    assert receipt["logical_sha256"] == sha(canonical(data))
    assert receipt["field_sha256"] == {key: sha(canonical(value)) for key, value in data.items()}
    text = rendered[receipt["packet_start"] : receipt["packet_end"]]
    carrier = json.loads(text)
    assert carrier == _container(deepcopy(data), [], _bytes(data), version=1)
    assert carrier["schema"] == "lossless-source-encoding.v1"
    assert carrier["encoding"] == "identity" and carrier["references"] == []
    assert carrier["value"] == data and not carrier["receipt"]["shrunk"]
    assert decode_lossless_source(carrier) == data
    assert len(text.encode()) > len(canonical(data).encode())
    assert receipt["offset_unit"] == "unicode_code_points"
    assert receipt["packet_start"] != len(rendered[: receipt["packet_start"]].encode())
    actual_text, actual_carrier, actual_source = _packet(
        SimpleNamespace(user_prompt=rendered), receipt["packet_start"], receipt["packet_end"]
    )
    assert actual_text == text and actual_carrier == carrier and actual_source == data
    assert not receipt["grade_asserted"] and not receipt["source_authority_granted"]
    assert not receipt["prompt_capacity"]["whole_request_model_capacity_verified"]


@pytest.mark.parametrize("value", [True, False, 0, -1, 1_000_001, "60000", 60000.0, None])
@pytest.mark.parametrize("name", ["max_chars", "max_estimated_tokens"])
def test_writer_reader_require_independent_positive_nonbool_integer_bounds(name, value):
    data = {"book": "Independent cartography", "unknown": None}
    with pytest.raises(ValueError, match="invalid"):
        build(data, **{name: value})
    rendered, receipt = build(data)
    with pytest.raises(ValueError, match="invalid"):
        decode(rendered, receipt, **{name: value})


def test_exact_capacity_boundary_is_symmetric_and_receipt_cannot_authorize_more():
    data = {"book": "Harbor", "unknown": None}
    _, initial = build(data)
    capacity = initial["prompt_capacity"]
    bounds = {
        "max_chars": capacity["combined_chars"],
        "max_estimated_tokens": capacity["estimated_tokens"],
    }
    rendered, receipt = build(data, **bounds)
    assert decode(rendered, receipt, **bounds) == data
    for name in bounds:
        smaller = {**bounds, name: bounds[name] - 1}
        with pytest.raises(SourceBoundPlanningCapacityError):
            build(data, **smaller)
        with pytest.raises(SourceBoundPlanningCapacityError):
            decode(rendered, receipt, **smaller)
    with pytest.raises(ValueError, match="receipt_mismatch"):
        decode(rendered, receipt, **BOUNDS)


@pytest.mark.parametrize(
    "kind",
    [
        "absent",
        "duplicate",
        "subset",
        "unknown_dropped",
        "bool_int",
        "int_float",
        "duplicate_json",
        "trailing",
        "nan",
        "overflow",
        "list",
        "fence",
        "nonstring_key",
        "nonjson_value",
    ],
)
def test_malformed_missing_or_selected_data_is_never_transportable(kind):
    data, block = {"book": "Unrelated lunar survey", "unknown": None, "type": 1}, None
    user = None
    if kind == "absent":
        user = "prefix without the supplied block"
    elif kind == "duplicate":
        user = canonical(data) * 2
    elif kind == "subset":
        block = canonical({"book": data["book"]})
    elif kind == "unknown_dropped":
        block = canonical({"book": data["book"], "type": 1})
    elif kind == "bool_int":
        block = canonical({**data, "type": True})
    elif kind == "int_float":
        block = canonical({**data, "type": 1.0})
    elif kind == "duplicate_json":
        block = '{"book":"a","book":"b"}'
    elif kind == "trailing":
        block = canonical(data) + " {}"
    elif kind == "nan":
        block = '{"x":NaN}'
    elif kind == "overflow":
        block = '{"x":1e9999}'
    elif kind == "list":
        block = "[]"
    elif kind == "fence":
        block = "```json\n{}\n```"
    elif kind == "nonstring_key":
        data, block = {1: "value"}, '{"1":"value"}'
    elif kind == "nonjson_value":
        data, block = {"x": (1, 2)}, '{"x":[1,2]}'
    block = canonical(data) if block is None else block
    user = PREFIX + block + SUFFIX if user is None else user
    with pytest.raises(ValueError):
        literal.prepare_literal_planning_review_transport(
            SYSTEM, user, data=data, data_block=block, **BOUNDS
        )


@pytest.mark.parametrize(
    "kind",
    [
        "prefix",
        "suffix",
        "system",
        "header",
        "start",
        "end",
        "bool_offset",
        "logic_hash",
        "field_hash",
        "bound_receipt",
        "extra_receipt",
        "missing_receipt",
        "carrier_hash",
        "carrier_value",
        "carrier_extra",
        "references",
        "v2_identity",
        "duplicate_json",
    ],
)
def test_malformed_or_tampered_wire_fails_closed(kind):
    rendered, receipt = build({"book": "Other observatory", "unknown": None})
    system = SYSTEM
    if kind == "prefix":
        rendered = "!" + rendered[1:]
    elif kind == "suffix":
        rendered = rendered[:-1] + "!"
    elif kind == "system":
        system += " "
    elif kind == "header":
        rendered = (
            rendered[: receipt["packet_start"] - 1] + "!" + rendered[receipt["packet_start"] :]
        )
    elif kind == "start":
        receipt["packet_start"] += 1
    elif kind == "end":
        receipt["packet_end"] = len(rendered) + 1
    elif kind == "bool_offset":
        receipt["packet_start"] = True
    elif kind == "logic_hash":
        receipt["logical_sha256"] = "0" * 64
    elif kind == "field_hash":
        receipt["field_sha256"]["unknown"] = "0" * 64
    elif kind == "bound_receipt":
        receipt["max_estimated_tokens"] += 1
    elif kind == "extra_receipt":
        receipt["authority"] = True
    elif kind == "missing_receipt":
        receipt = None
    else:
        start, end = receipt["packet_start"], receipt["packet_end"]
        packet = json.loads(rendered[start:end])
        if kind == "carrier_hash":
            packet["receipt"]["original_sha256"] = "0" * 64
        elif kind == "carrier_value":
            packet["value"]["unknown"] = "changed"
        elif kind == "carrier_extra":
            packet["dictionary"] = ["unexpected"]
        elif kind == "references":
            packet["references"] = [{"path": "/x", "target": "/y"}]
        elif kind == "v2_identity":
            packet = _container(packet["value"], [], _bytes(packet["value"]), version=2)
        text = canonical(packet)
        if kind == "duplicate_json":
            text = '{"encoding":"identity",' + text[1:]
        rendered = rendered[:start] + text + rendered[end:]
        receipt["packet_end"] = start + len(text)
    with pytest.raises(ValueError):
        literal.decode_literal_planning_review_transport(
            system=system, user=rendered, receipt=receipt, **BOUNDS
        )


def test_complete_large_source_feedback_has_no_pooling_clipping_or_legacy_changes():
    full = "  Complete source clause with markers $ref and section-sign text.\n" * 1600
    data = {
        "graded_input_hash": "bound",
        "input": {
            "candidate_payload": {"full": full},
            "source_reference_catalog": [{"path": "/full", "quoted": full}],
        },
        "protocol_repair": {
            "previous_attempts": [
                {"raw_grade": False, "unknown": None, "full_historical_feedback": full}
            ]
        },
    }
    before = deepcopy(data)
    with pytest.raises(SourceBoundPlanningCapacityError):
        build(data, max_chars=120000, max_estimated_tokens=60000)
    rendered, receipt = build(data)
    assert decode(rendered, receipt) == data == before
    assert receipt["prompt_capacity"]["combined_chars"] > 300000
    assert receipt["data_receipt"]["shrunk"] is False and receipt["references_count"] == 0
    assert MAX_PROMPT_CHARS == 120000 and MAX_PROMPT_ESTIMATED_TOKENS == 60000
    legacy = {"another_book": "Unrelated orchard", "unknown": None}
    block = json.dumps(legacy, indent=2)
    user = PREFIX + block + SUFFIX
    old_user, old_receipt = prepare_planning_review_transport(
        SYSTEM, user, data=legacy, data_block=block
    )
    assert old_user == user and old_receipt["encoding"] == "identity"
    with pytest.raises(SourceBoundPlanningCapacityError):
        build({"full": "X" * 1_000_000})
