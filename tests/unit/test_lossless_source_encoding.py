"""Pure lossless JSON encoding and retained-capacity checks; no PG/provider calls."""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import pytest

from bestseller.services.lossless_source_encoding import (
    decode_lossless_source,
    encode_lossless_source,
)

pytestmark = pytest.mark.unit


def encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def rebind_core(container):
    core = {k: v for k, v in container.items() if k != "receipt"}
    raw = encoded(core)
    container["receipt"].update(
        encoded_sha256=hashlib.sha256(raw).hexdigest(), encoded_bytes=len(raw)
    )
    return container


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        -1,
        1.0,
        -0.0,
        "",
        "ordinary data",
        [],
        {},
        {"$ref": "/authored/target"},
        {"array": [None, True, 1, 1.0, -0.0, '汉字\n"quote"', {"": []}]},
    ],
)
def test_small_values_are_identity_with_exact_types_and_no_input_mutation(value):
    original = deepcopy(value)
    container = encode_lossless_source(value)
    assert container["encoding"] == "identity" and container["references"] == []
    assert container["receipt"]["shrunk"] is False
    assert encoded(container["value"]) == encoded(value)
    restored = decode_lossless_source(container)
    assert type(restored) is type(value) and encoded(restored) == encoded(value)
    assert encoded(value) == encoded(original)
    assert container["receipt"]["original_sha256"] == hashlib.sha256(encoded(value)).hexdigest()
    assert container["receipt"]["original_bytes"] == len(encoded(value))
    if type(value) is float:
        assert math.copysign(1, restored) == math.copysign(1, value)


def source_fixture():
    full = "Source text remains complete, with all punctuation and endings.\n" * 40
    subtree = {"body": full, "state": [1, 1.0, False, None], "literal": {"$ref": "/absent"}}
    return {
        "first": subtree,
        "list": [deepcopy(subtree), full, full],
        "plan": {"a/b~c": full, "": deepcopy(subtree)},
    }


def test_complete_subtrees_long_strings_and_escaped_paths_roundtrip_deterministically():
    value = source_fixture()
    before = deepcopy(value)
    container = encode_lossless_source(value)
    assert container["encoding"] == "json-pointer" and container["receipt"]["shrunk"] is True
    assert len(encoded(container)) < len(encoded(value))
    assert any(r["path"] == "/list/0" for r in container["references"])
    assert any("/a~1b~0c" in r["path"] for r in container["references"])
    assert any(r["path"] == "/plan/" for r in container["references"])
    restored = decode_lossless_source(json.loads(encoded(container)))
    assert encoded(restored) == encoded(value)
    assert restored["first"]["literal"] == {"$ref": "/absent"}
    assert container == encode_lossless_source(value) and value == before
    restored["first"]["state"].append("independent")
    assert restored["list"][0]["state"] == before["list"][0]["state"]
    assert container == encode_lossless_source(before)


def test_authored_ref_collisions_and_literal_forward_self_invalid_pointers_are_data():
    value = source_fixture() | {
        "authored_forward": {"$ref": "/zz_not_present"},
        "authored_self": {"$ref": "/authored_self"},
        "authored_bad_escape": {"$ref": "/~invalid"},
        "authored_shape": {"$ref": "/first", "explanation": "literal"},
    }
    container = encode_lossless_source(value)
    declared = {r["path"] for r in container["references"]}
    assert not any(p.startswith("/authored_") for p in declared)
    assert encoded(decode_lossless_source(container)) == encoded(value)


def test_short_scalar_strings_are_never_substituted_even_in_otherwise_compressed_source():
    value = source_fixture() | {"short": ["s" * 63] * 30}
    container = encode_lossless_source(value)
    assert not any(r["path"].startswith("/short/") for r in container["references"])
    assert decode_lossless_source(container) == value


def test_no_compression_when_marker_table_and_receipt_cost_more_than_original():
    value = ["a" * 64, "a" * 64]
    container = encode_lossless_source(value)
    assert container["encoding"] == "identity"
    assert container["value"] == value and not container["references"]


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        {1: "not a JSON key"},
        ("not a JSON array",),
        {"bad": object()},
    ],
)
def test_non_json_or_nonfinite_inputs_fail_closed(value):
    with pytest.raises(ValueError, match="lossless_source:"):
        encode_lossless_source(value)


def test_python_cycles_are_rejected_but_shared_noncyclic_values_are_legal():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="cyclic_json"):
        encode_lossless_source(cycle)
    shared = source_fixture()
    assert decode_lossless_source(encode_lossless_source([shared, shared])) == [shared, shared]


@pytest.mark.parametrize(
    "fault",
    [
        "container_extra",
        "schema",
        "mode_type",
        "mode",
        "references_type",
        "reference_extra",
        "duplicate",
        "malformed_path",
        "malformed_target",
        "missing_path",
        "array_out_of_bounds",
        "noncanonical_array_index",
        "forward",
        "self",
        "ancestor",
        "missing_target",
        "marker",
        "table_order",
        "receipt_extra",
        "encoded_hash",
        "encoded_size",
        "original_hash",
        "original_size",
        "bool_size",
        "shrunk",
        "nonfinite",
    ],
)
def test_malformed_or_forged_reference_envelopes_are_rejected_even_with_rebound_core(fault):
    container = encode_lossless_source(source_fixture())
    refs = container["references"]
    if fault == "container_extra":
        container["extra"] = True
    elif fault == "schema":
        container["schema"] = "unknown"
    elif fault == "mode_type":
        container["encoding"] = []
    elif fault == "mode":
        container["encoding"] = "identity"
    elif fault == "references_type":
        container["references"] = {}
    elif fault == "reference_extra":
        refs[0]["extra"] = True
    elif fault == "duplicate":
        refs.append(deepcopy(refs[0]))
    elif fault == "malformed_path":
        refs[0]["path"] = "/bad~2escape"
    elif fault == "malformed_target":
        refs[0]["target"] = "#/fragment"
    elif fault == "missing_path":
        refs[0]["path"] = "/not-present"
    elif fault == "array_out_of_bounds":
        refs[0]["path"] = "/list/999"
    elif fault == "noncanonical_array_index":
        refs[0]["path"] = "/list/00"
    elif fault in {"forward", "self", "ancestor", "missing_target"}:
        path = refs[0]["path"]
        target = {"forward": "/plan", "self": path, "ancestor": "", "missing_target": "/missing"}[
            fault
        ]
        # First fixture reference is the repeated subtree at /list/0.
        assert path == "/list/0"
        refs[0]["target"] = target
        container["value"]["list"][0] = {"$ref": target}
    elif fault == "marker":
        container["value"]["list"][0]["extra"] = True
    elif fault == "table_order":
        refs.reverse()
    elif fault == "receipt_extra":
        container["receipt"]["extra"] = True
    elif fault == "encoded_hash":
        container["receipt"]["encoded_sha256"] = "0" * 64
    elif fault == "encoded_size":
        container["receipt"]["encoded_bytes"] += 1
    elif fault == "original_hash":
        container["receipt"]["original_sha256"] = "0" * 64
    elif fault == "original_size":
        container["receipt"]["original_bytes"] += 1
    elif fault == "bool_size":
        container["receipt"]["original_bytes"] = True
    elif fault == "shrunk":
        container["receipt"]["shrunk"] = False
    elif fault == "nonfinite":
        container["value"]["invalid"] = float("nan")
    if fault not in {"encoded_hash", "encoded_size", "nonfinite"}:
        rebind_core(container)
    with pytest.raises(ValueError):
        decode_lossless_source(container)


def test_references_inside_replaced_subtree_cannot_be_hidden_or_interpreted_twice():
    container = encode_lossless_source(source_fixture())
    container["references"].append({"path": "/list/0/body", "target": "/first/body"})
    rebind_core(container)
    with pytest.raises(ValueError, match="reference_path_missing"):
        decode_lossless_source(container)


def test_actual_captured_source_roundtrip_and_historical_capacity_fail_closed(monkeypatch):
    from bestseller.services import prompt_compiler
    # Retain this historical capacity diagnosis without pinning the current
    # monotonic estimator to the old mixed-script ratio discontinuity.
    monkeypatch.setattr(prompt_compiler, "estimate_prompt_tokens", prompt_compiler.estimate_legacy_prompt_tokens)
    from bestseller.services.prompt_compiler import PromptBudgetError
    from bestseller.services.review_source_context import prepare_source_complete_prompt

    path = (
        Path(__file__).resolve().parents[2]
        / "reports/continuous-recovery-2026-10-16"
        / ("20261017T161015909513Z-rewrite-source-capacity-diagnostic.json")
    )
    if not path.exists():
        pytest.skip("Optional retained local source fixture absent")
    before = path.read_bytes()
    captured = json.loads(before)
    source = captured["source_context"]
    container = encode_lossless_source(source)
    assert container["receipt"]["shrunk"]
    assert len(encoded(container)) < len(encoded(source))
    assert encoded(decode_lossless_source(json.loads(encoded(container)))) == encoded(source)
    assert container["receipt"]["original_sha256"] == hashlib.sha256(encoded(source)).hexdigest()
    assert source["editable_plan"] == decode_lossless_source(container)["editable_plan"]
    for source_value in (source, container):
        with pytest.raises(PromptBudgetError, match="source integrity cannot fit") as failure:
            prepare_source_complete_prompt(
                system_prompt=captured["system"],
                user_prompt=captured["user"],
                source_context=source_value,
                output_tokens=captured["output_reserve"],
            )
        assert failure.value.required_keys == ("review_adopted_sources", "review_original_task")
    assert captured["output_reserve"] == 8832
    assert path.read_bytes() == before
