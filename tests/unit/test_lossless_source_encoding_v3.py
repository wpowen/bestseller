"""Pure key dictionaries preserve all authored keys/values and strict V2 proofs."""

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
from bestseller.services.lossless_source_encoding import (
    KEY_DICTIONARY_SCHEMA,
    decode_lossless_source,
    encode_lossless_source,
)


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _data():
    return {
        "A": {"B": {"Z": {"未知作者自定义键/雪~e\u0301": "A B C $ref 🌕 e\u0301"}}},
        "different_characters": [
            {
                "a_very_long_original_provenance_property": f"row-{i}",
                "a_very_long_original_temporal_scope_property": i,
                "另一项完全保留的作者自定义键/🌕": f"雪-{i}",
                "another_long_original_source_ownership_property": [False, None, i],
            }
            for i in range(160)
        ],
        "mirrored_prose": ["完整原文🌕 e\u0301 literal /pointer and end.\n" * 40] * 8,
        "literal": [{"$ref": 0}, {"$ref": "/not_present"}, {"C": "字面字段和值"}],
    }


def _rename(value, aliases):
    if type(value) is dict:
        return {aliases.get(key, key): _rename(child, aliases) for key, child in value.items()}
    if type(value) is list:
        return [_rename(child, aliases) for child in value]
    return value


def _rebind(outer):
    core = {key: value for key, value in outer.items() if key != "receipt"}
    outer["receipt"].update(encoded_sha256=_sha(core), encoded_bytes=len(_json(core).encode()))


def test_v3_restores_exact_unchanged_v2_and_full_unicode_authored_source():
    value = _data()
    before = _json(value)
    v1, v2 = encode_lossless_source(value), encode_lossless_source(value, version=2)
    v3 = encode_lossless_source(value, version=3)
    assert v3["schema"] == KEY_DICTIONARY_SCHEMA
    assert v3["encoding"] == "key-dictionary"
    assert len(_json(v3).encode()) < len(_json(v2).encode()) < len(before.encode())
    assert _json(_rename(v3["value"], v3["key_aliases"])) == _json(v2)
    assert _json(decode_lossless_source(json.loads(_json(v3)))) == before
    assert set(v3["key_aliases"]).isdisjoint({"A", "B", "C", "Z"})
    restored = decode_lossless_source(v3)
    assert restored["literal"] == value["literal"]
    assert restored["A"] == value["A"]
    assert v1 == encode_lossless_source(value, version=1)
    assert v2 == encode_lossless_source(value, version=2)
    assert _json(value) == before
    reversed_keys = dict(reversed(list(value.items())))
    assert encode_lossless_source(reversed_keys, version=3) == v3
    reordered = deepcopy(v3)
    reordered["key_aliases"] = dict(reversed(list(reordered["key_aliases"].items())))
    assert _json(reordered) == _json(v3)  # JSON object insertion order is not source meaning.
    assert decode_lossless_source(reordered) == value


@pytest.mark.parametrize(
    "fault",
    [
        "alias_unicode",
        "alias_long",
        "duplicate_target",
        "unused",
        "collision",
        "swapped_targets",
        "inner_hash",
        "original_hash",
        "encoded_hash",
        "bool_size",
        "extra",
        "missing_mapping",
    ],
)
def test_dictionary_rejects_forgery_and_collisions_even_with_rehashed_outer_core(fault):
    encoded = encode_lossless_source(_data(), version=3)
    aliases = encoded["key_aliases"]
    first, second = list(aliases)[:2]
    if fault in {"alias_unicode", "alias_long"}:
        aliases["é" if fault == "alias_unicode" else "XY"] = aliases.pop(first)
    elif fault == "duplicate_target":
        aliases[second] = aliases[first]
    elif fault == "unused":
        aliases["q"] = "unused_original_long_key"
    elif fault == "collision":
        encoded["value"][first] = "aliased"
        encoded["value"][aliases[first]] = "unaliased conflicting field"
    elif fault == "swapped_targets":
        aliases[first], aliases[second] = aliases[second], aliases[first]
    elif fault == "inner_hash":
        encoded["receipt"]["inner_sha256"] = "0" * 64
    elif fault == "original_hash":
        encoded["receipt"]["original_sha256"] = "0" * 64
    elif fault == "bool_size":
        encoded["receipt"]["inner_bytes"] = True
    elif fault == "extra":
        encoded["unexpected"] = "must reject"
    elif fault == "missing_mapping":
        aliases.pop(first)
    if fault != "encoded_hash":
        _rebind(encoded)
    else:
        encoded["receipt"]["encoded_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="lossless_source:"):
        decode_lossless_source(encoded)


def test_restored_inner_reference_must_pass_all_existing_v2_validation():
    outer = encode_lossless_source(_data(), version=3)
    aliases = outer["key_aliases"]
    inner = _rename(outer["value"], aliases)
    path = inner["references"][0]["path"]
    node = inner["value"]
    for segment in path[1:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        node = node[int(key)] if type(node) is list else node[key]
    node["$ref"] = True
    core = {key: value for key, value in inner.items() if key != "receipt"}
    inner["receipt"].update(encoded_sha256=_sha(core), encoded_bytes=len(_json(core).encode()))
    outer["value"] = _rename(inner, {target: alias for alias, target in aliases.items()})
    outer["receipt"].update(inner_sha256=_sha(inner), inner_bytes=len(_json(inner).encode()))
    _rebind(outer)
    with pytest.raises(ValueError, match="reference_index"):
        decode_lossless_source(outer)


@pytest.mark.parametrize("value", [{}, {"A": "literal"}, {"$ref": 0}, ["雪", True, None]])
def test_v3_returns_unchanged_v2_when_full_wrapper_would_not_shrink(value):
    assert encode_lossless_source(value, version=3) == encode_lossless_source(value, version=2)


def test_v3_cannot_be_forged_as_a_larger_wrapper():
    inner = encode_lossless_source({"small": "value"}, version=2)
    core = {
        "schema": KEY_DICTIONARY_SCHEMA,
        "encoding": "key-dictionary",
        "key_aliases": {"X": "value"},
        "value": _rename(inner, {"value": "X"}),
    }
    outer = {
        **core,
        "receipt": {
            "original_sha256": inner["receipt"]["original_sha256"],
            "original_bytes": inner["receipt"]["original_bytes"],
            "inner_sha256": _sha(inner),
            "inner_bytes": len(_json(inner).encode()),
            "encoded_sha256": _sha(core),
            "encoded_bytes": len(_json(core).encode()),
            "shrunk": True,
        },
    }
    with pytest.raises(ValueError, match="not_smaller"):
        decode_lossless_source(outer)


def test_commercial_v3_is_overflow_only_and_cannot_prove_custody_or_another_source(monkeypatch):
    source = evidence.assemble_commercial_sources(
        chapter_number=1,
        content_md="A new expedition🌕.",
        source={"approved_world_authority": _data()},
        previous_chapters=[],
    )
    system, user = "Review the full packet.", "SOURCE\n" + source.text
    tools = review_submission_tools(source)
    first_wire, first = prepare_commercial_review_transport(
        system, user, sources=source, tools=tools, output_tokens=8192
    )
    assert first_wire == user and first["encoding"] == "identity"
    budget = first["capacity"]["reserved_input_tokens"] + 8192 - 1
    for _ in range(3):
        monkeypatch.setattr(evidence, "TOTAL_TOKEN_BUDGET", budget)
        wire, receipt = prepare_commercial_review_transport(
            system, user, sources=source, tools=tools, output_tokens=8192
        )
        if receipt["encoding"] == "key-dictionary":
            break
        budget = receipt["capacity"]["reserved_input_tokens"] + 8192 - 1
    assert receipt["encoding"] == "key-dictionary"
    assert receipt["capacity"]["budget_passed"] and not receipt["capacity"]["truncated"]
    assert "normal native report field names" in wire
    assert receipt["original_packet_sha256"] == source.receipt["assembled_source_sha256"]
    verify_commercial_review_transport(wire, sources=source, receipt=receipt)
    other = evidence.assemble_commercial_sources(
        chapter_number=1,
        content_md="Different source.",
        source={"approved_world_authority": _data()},
        previous_chapters=[],
    )
    with pytest.raises(CommercialReviewTransportError, match="source_binding"):
        verify_commercial_review_transport(wire, sources=other, receipt=receipt)
    with pytest.raises(CommercialReviewTransportError, match="wire_prompt_hash"):
        verify_commercial_review_transport(
            wire + " forged instruction", sources=source, receipt=receipt
        )
    # Duplicate alias keys in physical JSON must fail before dict construction,
    # even if the caller has recomputed the outer wire hashes.
    packet = wire[receipt["packet_start"] : receipt["packet_end"]]
    aliases = json.loads(packet)["key_aliases"]
    alias = next(iter(aliases))
    entry = _json(alias) + ":" + _json(aliases[alias])
    duplicated = packet.replace(_json(aliases), "{" + entry + "," + _json(aliases)[1:], 1)
    bad = deepcopy(receipt)
    bad_wire = wire[: bad["packet_start"]] + duplicated + wire[bad["packet_end"] :]
    bad.update(
        wire_packet_sha256=hashlib.sha256(duplicated.encode()).hexdigest(),
        wire_prompt_sha256=hashlib.sha256(bad_wire.encode()).hexdigest(),
        packet_end=bad["packet_start"] + len(duplicated),
    )
    with pytest.raises(CommercialReviewTransportError, match="duplicate_json_key"):
        verify_commercial_review_transport(bad_wire, sources=source, receipt=bad)
