"""Indexed references preserve logical sources; no DB or provider calls."""

from copy import deepcopy
import hashlib
import json

import pytest

from bestseller.services import source_bound_planning_content as capacity
from bestseller.services.lossless_source_encoding import (
    decode_lossless_source,
    encode_lossless_source,
)
from bestseller.services.planning_review_transport import prepare_planning_review_transport

pytestmark = pytest.mark.unit


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _source():
    text = '完整约束与末尾事实 / Full source 🌕 e\u0301\n"quoted". ' * 180
    return {
        "a": {"long_source_path_with_escaped_slash/and~tilde" * 3: text},
        "b": [text] * 30,
        "literal": [{"$ref": 0}, {"$ref": False}, {"$ref": "/missing"}],
        "tail": "独立尾部要求 / Independent final clause",
    }


def _rebind(encoded):
    raw = _json({key: value for key, value in encoded.items() if key != "receipt"}).encode()
    encoded["receipt"].update(
        encoded_bytes=len(raw), encoded_sha256=hashlib.sha256(raw).hexdigest()
    )


def test_explicit_indexed_encoding_is_smaller_deterministic_and_lossless():
    source = _source()
    before = _json(source)
    old = encode_lossless_source(source)
    new = encode_lossless_source(source, version=2)
    assert old == encode_lossless_source(source, version=1)
    assert old["schema"] == "lossless-source-encoding.v1"
    assert old["encoding"] == "json-pointer"
    assert new["schema"] == "lossless-source-encoding.v2"
    assert new["encoding"] == "indexed-json-pointer"
    assert new["references"] == old["references"]
    assert len(_json(old)) - len(_json(new)) > 1000
    assert new["value"]["b"] == [{"$ref": index} for index in range(30)]
    assert new["receipt"]["original_sha256"] == old["receipt"]["original_sha256"]
    for encoded in (old, new):
        restored = decode_lossless_source(json.loads(_json(encoded)))
        assert _json(restored) == before
        assert restored["literal"] == source["literal"]
    assert new == encode_lossless_source(source, version=2)
    assert _json(source) == before
    reversed_keys = dict(reversed(list(source.items())))
    assert encode_lossless_source(reversed_keys, version=2) == new
    reversed_keys["b"] = ["different", *reversed_keys["b"]]
    assert encode_lossless_source(reversed_keys, version=2) != new


@pytest.mark.parametrize("index", [True, False, -1, 30, 999, 0.0, "0", None, 1])
def test_invalid_or_wrong_entry_index_rejected_even_with_rehashed_envelope(index):
    encoded = encode_lossless_source(_source(), version=2)
    encoded["value"]["b"][0] = {"$ref": index}
    _rebind(encoded)
    with pytest.raises(ValueError, match="reference_index"):
        decode_lossless_source(encoded)


@pytest.mark.parametrize(
    "fault", ["order", "path", "duplicate", "target", "extra", "schema", "hash"]
)
def test_reference_contract_and_integrity_remain_strict(fault):
    encoded = encode_lossless_source(_source(), version=2)
    if fault == "order":
        encoded["references"].reverse()
    elif fault == "path":
        encoded["references"][0]["path"] = "/b/999"
    elif fault == "duplicate":
        encoded["references"].append(deepcopy(encoded["references"][0]))
    elif fault == "target":
        encoded["references"][0]["target"] = "/b"
    elif fault == "extra":
        encoded["value"]["b"][0]["extra"] = True
    elif fault == "schema":
        encoded["schema"] = "lossless-source-encoding.v1"
    else:
        encoded["receipt"]["original_sha256"] = "0" * 64
    _rebind(encoded)
    with pytest.raises(ValueError, match="lossless_source:"):
        decode_lossless_source(encoded)


@pytest.mark.parametrize("version", [True, False, 0, 5, "2", 2.0])
def test_codec_version_is_explicit_and_strict(version):
    with pytest.raises(ValueError, match="version"):
        encode_lossless_source({}, version=version)


def test_v2_identity_is_lossless_for_small_authored_reference():
    value = {"$ref": 0}
    encoded = encode_lossless_source(value, version=2)
    assert encoded["encoding"] == "identity" and encoded["references"] == []
    assert decode_lossless_source(encoded) == value


def test_transport_only_uses_v2_after_v1_overflow_and_checks_final_capacity(monkeypatch):
    data = _source()
    original = _json(data)
    system, prefix, suffix = "Review source data only.", "PREFIX\r\n", "\nTAIL full contract."
    user = prefix + original + suffix
    v1_user, v1_receipt = prepare_planning_review_transport(
        system, user, data=data, data_block=original
    )
    assert v1_receipt["encoding"] == "json-pointer"
    assert v1_receipt["schema"] == "planning-review-transport.v1"
    monkeypatch.setattr(capacity, "MAX_PROMPT_CHARS", len(system) + len(v1_user) - 1)
    rendered, receipt = prepare_planning_review_transport(
        system, user, data=data, data_block=original
    )
    assert receipt["encoding"] == "indexed-json-pointer"
    assert receipt["schema"] == "planning-review-transport.v2"
    assert "references[i].path" in rendered and "references[i].target" in rendered
    assert rendered.startswith(prefix) and rendered.endswith(suffix)
    envelope, end = json.JSONDecoder().raw_decode(rendered.split("source_review_data=", 1)[1])
    assert rendered.split("source_review_data=", 1)[1][end:] == suffix
    assert _json(decode_lossless_source(envelope)) == original == _json(data)
    assert receipt["data_receipt"] == envelope["receipt"]
    assert receipt["original_prompt_sha256"] == hashlib.sha256(user.encode()).hexdigest()
    assert receipt["rendered_prompt_sha256"] == hashlib.sha256(rendered.encode()).hexdigest()
    capacity.assert_source_bound_planning_prompt_capacity(system, rendered)
    monkeypatch.setattr(capacity, "MAX_PROMPT_CHARS", len(system) + len(rendered) - 1)
    later, later_receipt = prepare_planning_review_transport(system,user,data=data,data_block=original)
    assert later.startswith(prefix) and later.endswith(suffix)
    capacity.assert_source_bound_planning_prompt_capacity(system,later)
    later_envelope,_=json.JSONDecoder().raw_decode(later.split("source_review_data=",1)[1])
    if later_receipt["schema"] == "planning-review-transport.v5":
        from bestseller.services.planning_string_pool import decode
        later_envelope=decode(later_envelope)
    assert _json(decode_lossless_source(later_envelope)) == original
    monkeypatch.setattr(capacity,"MAX_PROMPT_CHARS",len(system)+len(prefix)+len(suffix))
    with pytest.raises(capacity.SourceBoundPlanningCapacityError):
        prepare_planning_review_transport(system, user, data=data, data_block=original)
    assert _json(data) == original


def test_fitting_identity_does_not_attempt_either_codec(monkeypatch):
    import bestseller.services.planning_review_transport as transport

    def forbidden(*args, **kwargs):
        raise AssertionError("fitting prompt must not be reencoded")

    monkeypatch.setattr(transport, "encode_lossless_source", forbidden)
    user = '  {"x": "完整原文"}\n'
    rendered, receipt = transport.prepare_planning_review_transport(
        "System", user, data={"x": "完整原文"}, data_block=user
    )
    assert rendered == user and receipt["encoding"] == "identity"
