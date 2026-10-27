"""Pure explicit patch values against actual planning schemas; no model or DB."""

from copy import deepcopy
import hashlib
import json

import pytest

from bestseller.services import planning_field_corrections as fields
from bestseller.services.source_bound_planning_submission import (
    VOLUME_NATIVE_POLICY,
    _validate_wire,
    planning_submission_contract,
    wire_diagnostics,
)

pytestmark = pytest.mark.unit


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def test_real_volume_schema_all_six_failures_need_explicit_replacement_values():
    schema = planning_submission_contract(
        "volume_plan", version=VOLUME_NATIVE_POLICY,
    )["json_schema"]
    value = {"volumes": [{
        "volume_number": "1", "volume_title": "冰原观测日志", "chapter_count_target": "4",
        "volume_goal": "核对原始记录", "volume_resolution": {"goal_achieved": "true"},
        "key_reveals": {"item": ["原始提案甲", "原始提案乙"]},
        "foreshadowing_planted": {"item": ["原始提案丙"]},
        "foreshadowing_paid_off": {"item": ["原始提案丁"]},
        "unknown_authored": {"verbatim": "保留 §0 与 $ref 原文字面量\n 尾行", "unset": None},
    }]}
    before, schema_before = deepcopy(value), deepcopy(schema)
    contract = fields.correction_contract(value, schema)
    # Values represent a returned model-authored patch; no automatic unwrap,
    # integer conversion, boolean conversion or default is under test here.
    patch = {
        "$/volumes/0/volume_number": 2, "$/volumes/0/chapter_count_target": 5,
        "$/volumes/0/volume_resolution/goal_achieved": True,
        "$/volumes/0/key_reveals": ["重新提出的模型值甲", "重新提出的模型值乙"],
        "$/volumes/0/foreshadowing_planted": ["重新提出的模型值丙"],
        "$/volumes/0/foreshadowing_paid_off": ["重新提出的模型值丁"],
    }
    assert len(wire_diagnostics(value, schema, schema)) == 6
    assert set(contract["required"]) == set(patch)
    with pytest.raises(ValueError):
        _validate_wire(value, schema, schema)
    result, receipt = fields.apply_corrections(value, schema, contract, patch)
    assert wire_diagnostics(result, schema, schema) == []
    _validate_wire(result, schema, schema)
    expected = deepcopy(before)
    for path, replacement in patch.items():
        if path.endswith("/volume_resolution/goal_achieved"):
            expected["volumes"][0]["volume_resolution"]["goal_achieved"] = replacement
        else:
            expected["volumes"][0][path.rsplit("/", 1)[1]] = replacement
    assert result == expected and value == before and schema == schema_before
    assert "opening_state" not in result["volumes"][0]  # No model defaults materialized.
    assert receipt["raw_payload_sha256"] == sha(before)
    assert receipt["patch_payload_sha256"] == sha(patch)
    assert receipt["result_payload_sha256"] == sha(result)
    assert receipt["original_schema_sha256"] == sha(schema)
    assert receipt["correction_contract_sha256"] == sha(contract)
    assert receipt["corrected_paths"] == sorted(patch)
    assert all(receipt[key] is False for key in (
        "single_provider_payload_claimed", "patch_origin_verified", "quality_approval_granted",
        "source_authority_granted",
    ))
    assert fields.apply_corrections(value, schema, contract, patch) == (result, receipt)
    result["volumes"][0]["key_reveals"].append("only returned tree changes")
    assert len(patch["$/volumes/0/key_reveals"]) == 2 and value == before


@pytest.mark.parametrize("shape", ["book", "array", "scalar"])
def test_unrelated_roots_and_enum_targets_preserve_all_other_content(shape):
    entry_schema = {"type": "object", "properties": {
        "count": {"type": "integer"}, "mode": {"type": "string", "enum": ["observe", "wait"]},
    }}
    entry = {"count": "3", "mode": "invalid", "name": "Orchid Observatory",
             "unknown": [{"$ref": "literal", "unset": None}, "order-2", "order-3"]}
    if shape == "book":
        value = {"unrelated_book": entry, "untouched": {"§0": [False, None]}}
        schema = {"type": "object", "properties": {"unrelated_book": entry_schema}}
        patch = {"$/unrelated_book/count": 7, "$/unrelated_book/mode": "wait"}
    elif shape == "array":
        value, schema = [entry], {"type": "array", "items": entry_schema}
        patch = {"$/0/count": 7, "$/0/mode": "wait"}
    else:
        value, schema, patch = "7", {"type": "integer"}, {"$": 7}
    before = deepcopy(value)
    contract = fields.correction_contract(value, schema)
    result, receipt = fields.apply_corrections(value, schema, contract, patch)
    assert value == before and receipt["corrected_paths"] == sorted(patch)
    _validate_wire(result, schema, schema)
    if shape != "scalar":
        result_entry = result["unrelated_book"] if shape == "book" else result[0]
        assert result_entry == {**entry, "count": 7, "mode": "wait"}


@pytest.mark.parametrize("schema,raw,invalid,valid", [
    ({"type": "integer"}, True, False, 1),
    ({"type": "boolean"}, 0, 1, True),
    ({"enum": [1]}, True, False, 1),
])
def test_boolean_and_integer_are_never_interchanged(schema, raw, invalid, valid):
    contract = fields.correction_contract(raw, schema)
    with pytest.raises(ValueError):
        fields.apply_corrections(raw, schema, contract, {"$": invalid})
    result, _ = fields.apply_corrections(raw, schema, contract, {"$": valid})
    assert type(result) is type(valid) and result == valid


@pytest.mark.parametrize("fault", ["extra", "missing", "wrong_type", "changed_contract"])
def test_patch_must_cover_every_exact_target_without_adding_or_coercing(fault):
    value = {"a": "1", "b": "2", "original_text": "不允许改写"}
    schema = {"type": "object", "properties": {
        "a": {"type": "integer"}, "b": {"type": "integer"},
    }}
    before = deepcopy(value)
    contract = fields.correction_contract(value, schema)
    patch = {"$/a": 1, "$/b": 2}
    if fault == "extra":
        patch["$/original_text"] = "擅自改写"
    elif fault == "missing":
        patch.pop("$/b")
    elif fault == "wrong_type":
        patch["$/b"] = "2"
    else:
        contract["properties"]["$/b"] = {"type": "string"}
    with pytest.raises(ValueError):
        fields.apply_corrections(value, schema, contract, patch)
    assert value == before


@pytest.mark.parametrize("value,schema,reason", [
    ({}, {"type": "object", "required": ["missing"]}, "not_patchable"),
    ({"x": ""}, {"properties": {"x": {"type": "string", "minLength": 1}}}, "not_patchable"),
    ({"box": {"n": "bad"}}, {"properties": {
        "box": {"type": "array", "properties": {"n": {"type": "integer"}}},
    }}, "overlapping"),
    (1, {"oneOf": [{"type": "integer"}, {"type": "number"}]}, "not_patchable"),
    ({"x": 1}, {"properties": {"x": {"type": "integer"}}}, "no_failed_fields"),
])
def test_missing_unknown_overlap_and_clean_values_cannot_authorize_a_patch(value, schema, reason):
    before = deepcopy(value)
    with pytest.raises(ValueError, match=reason):
        fields.correction_contract(value, schema)
    assert value == before


@pytest.mark.parametrize("diagnostic", ["schema_type:$/missing:integer", "schema_unrecognized:$/x"])
def test_missing_diagnostic_path_and_unknown_error_are_rejected(monkeypatch, diagnostic):
    monkeypatch.setattr(fields, "wire_diagnostics", lambda *_: [diagnostic])
    with pytest.raises(ValueError):
        fields.correction_contract({"x": "bad"}, {"properties": {"x": {"type": "integer"}}})


def test_schema_definitions_keep_their_constraints_and_patch_is_not_a_default():
    schema = {"type": "object", "properties": {"count": {"$ref": "#/$defs/Count"}},
              "$defs": {"Count": {"type": "integer", "minimum": 2, "maximum": 5}}}
    value = {"count": "4"}
    contract = fields.correction_contract(value, schema)
    for invalid in ("4", True, 0, 6):
        with pytest.raises(ValueError):
            fields.apply_corrections(value, schema, contract, {"$/count": invalid})
    assert fields.apply_corrections(value, schema, contract, {"$/count": 4})[0] == {"count": 4}
