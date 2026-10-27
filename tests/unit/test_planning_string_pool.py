"""Generic literal-safe transport; no paid artifacts, databases or providers."""
from copy import deepcopy
import json
import random
import string

import pytest

from bestseller.services import planning_string_pool as pool
from bestseller.services.lossless_source_encoding import encode_lossless_source, decode_lossless_source
from bestseller.services.planning_review_transport import prepare_planning_review_transport
from bestseller.services.source_bound_planning_content import (
    SourceBoundPlanningCapacityError, assert_source_bound_planning_prompt_capacity,
)

pytestmark = pytest.mark.unit


def test_literals_arrays_numbers_and_authored_refs_are_exact():
    original = {"§0": ["§0", "§§literal", "§00", {"$ref": "/not-codec-data"},
                         None, True, False, 1, 1.25, "汉字🌑é\n"],
                "rows": [{"id": i, "state": "untrusted_planning_candidate"} for i in range(30)]}
    before = deepcopy(original)
    wire = pool.encode(original)
    assert pool.decode(json.loads(pool.canonical(wire))) == original == before
    assert "§0" in wire["value"] and wire["dictionary"]
    assert pool.encode(before) == wire
    assert pool.decode(wire)["§0"][3] == {"$ref": "/not-codec-data"}


@pytest.mark.parametrize("malformation", ["hash", "dictionary", "negative", "leadingzero", "missing", "unicode"])
def test_bad_reference_or_changed_data_never_partially_restores(malformation):
    wire = pool.encode({"body": ["a repeated literal with enough length"] * 30})
    if malformation == "hash":
        wire["original_sha256"] = "0" * 64
    elif malformation == "dictionary":
        wire["dictionary"].append(wire["dictionary"][0])
    else:
        wire["value"]["body"][0] = {"negative": "§-1", "leadingzero": "§00",
            "missing": "§999999", "unicode": "§１"}[malformation]
    with pytest.raises(ValueError):
        pool.decode(wire)


def review_data():
    rng = random.Random(789163)
    source = "".join(rng.choices(string.ascii_letters + string.digits + " ", k=106000))
    return {"authority_and_baseline": {"authored_literal": {"$ref": "/outside"}, "source": source},
            "derived_candidates": {"observations": [{"id": i,
                "classification": "untrusted_planning_candidate", "purpose": "source_fidelity_review_only",
                "authority": "not_an_authored_fact_or_approval"} for i in range(200)]},
            "source_quote_options": [{"quote_id": "s0", "quote": source[-100:]}],
            "candidate_quote_options": [{"quote_id": "c0", "field": "world.observations", "quote": "candidate"}],
            "changed_fields": ["world.observations"],
            "explicit_author_controls": {"clause": "不得改写最后一条真实来源。"}}


def test_overflow_keeps_all_sections_headers_quote_catalogs_and_final_source():
    data = review_data()
    block = "\n".join(f"{k}={pool.canonical(v)}" for k, v in data.items())
    prefix, suffix, system = "Review the immutable original data.\r\n", "\nReturn exact quote_ids only.\n", "No invented authority."
    user = prefix + block + suffix
    v3 = encode_lossless_source(data, version=3)
    with pytest.raises(SourceBoundPlanningCapacityError):
        assert_source_bound_planning_prompt_capacity(system, prefix + pool.canonical(v3) + suffix)
    wire, receipt = prepare_planning_review_transport(system,user,data=data,data_block=block)
    assert receipt["schema"] == "planning-review-transport.v5"
    assert wire.startswith(prefix) and wire.endswith(suffix)
    section = wire[len(prefix):].split("source_review_data=", 1)[1]
    encoded, end = json.JSONDecoder().raw_decode(section)
    assert section[end:] == suffix
    assert decode_lossless_source(pool.decode(encoded)) == data
    assert assert_source_bound_planning_prompt_capacity(system, wire)["combined_chars"] <= 120000


def test_noncompressible_overflow_is_still_rejected():
    data = {"unique": "different source text" * 9000}
    block = pool.canonical(data)
    with pytest.raises(SourceBoundPlanningCapacityError):
        prepare_planning_review_transport("system",block,data=data,data_block=block)


def test_shared_v4_codec_is_exact_and_tamper_rejects():
    data = review_data()
    data["literal"] = {"$ref": "/does-not-expand", "marker": "§0", "empty": None}
    encoded = encode_lossless_source(data,version=4)
    assert encoded["schema"] == "lossless-source-encoding.v4"
    assert decode_lossless_source(json.loads(pool.canonical(encoded))) == data
    changed = deepcopy(encoded)
    changed["value"]["dictionary"][0] += "forgery"
    with pytest.raises(ValueError):
        decode_lossless_source(changed)


def test_default_shared_planning_entry_tries_v3_and_v4_before_any_send():
    from bestseller.services import source_bound_planning_content as content
    from test_source_bound_planning_content import _project, _compile

    project = _project()
    book,world,cast,volumes = _compile(project)
    logical = content.decode_source_bound_planning_document(json.loads(
        content.build_source_bound_planning_prompts(kind="volume_plan",project=project,
            premise="Independent observatory.",projection=volumes,
            book_spec=book,world_spec=world,cast_spec=cast)[1]))
    logical["unknown_source_payload"] = review_data()
    logical["unknown_source_payload"]["authority_and_baseline"]["source"] = (
        logical["unknown_source_payload"]["authority_and_baseline"]["source"][:95000])
    old_v3 = encode_lossless_source(logical,version=3)
    with pytest.raises(SourceBoundPlanningCapacityError):
        content.assert_source_bound_planning_prompt_capacity("system",pool.canonical(old_v3))
    wire=content._overflow_planning_prompt("system",logical)
    assert json.loads(wire)["planning_document"]["schema"] == "lossless-source-encoding.v4"
    assert content.decode_source_bound_planning_document(json.loads(wire)) == logical
