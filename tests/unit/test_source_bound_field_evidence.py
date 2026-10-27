"""Typed coverage proves JSON observations, never an authored constraint or grade."""

from __future__ import annotations

import copy

import pytest

from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.source_bound_field_evidence import (
    SCHEMA,
    compile_field_presence_options,
    field_presence_evidence_matches,
    resolve_field_presence_evidence,
)


def _resolve(payload, field="cast.antagonists", side="candidate"):
    options = compile_field_presence_options(payload, fields=[field], side=side)
    entry = {
        "quote_id": options[0]["quote_id"],
        "field": field,
        "source_relation": "Observed field state; semantic acceptability is unresolved.",
    }
    return resolve_field_presence_evidence(entry, options, payload=payload, side=side)


def test_empty_baseline_to_absent_candidate_is_observation_not_a_quote():
    baseline = {"cast_spec": {"antagonists": []}, "world_spec": {}}
    candidate = {"cast_spec": {}, "world_spec": {}}
    before = copy.deepcopy((baseline, candidate))
    source = _resolve(baseline, side="source")
    derived = _resolve(candidate)

    assert source["quote_id"].startswith("sp_")
    assert derived["quote_id"].startswith("cp_")
    assert source["schema"] == derived["schema"] == SCHEMA
    assert source["present"] is True
    assert source["value_type"] == "array"
    assert source["value_hash"] == content_hash([])
    assert derived["present"] is False
    assert derived["value_type"] == "absent"
    assert derived["value_hash"] is None
    assert derived["json_pointer"] == "/cast_spec/antagonists"
    for evidence, payload, side in (
        (source, baseline, "source"),
        (derived, candidate, "candidate"),
    ):
        assert "quote" not in evidence
        assert not {"authority", "verdict", "grade", "approved", "value"} & evidence.keys()
        assert field_presence_evidence_matches(
            evidence,
            payload=payload,
            field="cast.antagonists",
            side=side,
        )
    assert (baseline, candidate) == before
    assert "antagonists" not in candidate["cast_spec"]


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (None, "null"),
        ([], "array"),
        ({}, "object"),
        (False, "boolean"),
        (True, "boolean"),
        (0, "integer"),
        (1, "integer"),
        (0.0, "number"),
        (1.5, "number"),
        ("", "string"),
        ("observatory", "string"),
        ([False, 0, None], "array"),
        ({"observations": []}, "object"),
    ],
)
def test_present_json_values_have_exact_distinct_state(value, kind):
    payload = {"world_spec": {"observation": value}}
    evidence = _resolve(payload, "world.observation")
    absent = _resolve({"world_spec": {}}, "world.observation")
    assert evidence["present"] is True
    assert evidence["value_type"] == kind
    assert evidence["value_hash"] == content_hash(value)
    assert evidence["value_hash"] is not None
    assert evidence["quote_id"] != absent["quote_id"]
    assert field_presence_evidence_matches(
        evidence,
        payload=payload,
        field="world.observation",
        side="candidate",
    )


def test_bool_zero_float_and_absence_never_collapse():
    states = [
        _resolve({"cast_spec": {"antagonists": value}}) for value in (False, 0, 0.0, None, [], {})
    ]
    states.append(_resolve({"cast_spec": {}}))
    assert len({state["quote_id"] for state in states}) == 7
    assert len({state["value_hash"] for state in states}) == 7


def test_observatory_metadata_and_raw_unknown_grade_preserved_with_stable_ids():
    payload = {
        "world_spec": {"dome_open": False, "exposure_count": 0},
        "cast_spec": {},
        "review": {"decision": "unknown", "grade": 0.31},
        "_meta": {"literal_author_note": "Do not claim observed means approved."},
    }
    before = copy.deepcopy(payload)
    first = compile_field_presence_options(
        payload,
        fields=["world.exposure_count", "world.dome_open", "world.dome_open"],
        side="candidate",
    )
    reordered = dict(reversed(list(payload.items())))
    second = compile_field_presence_options(
        reordered,
        fields=["world.dome_open", "world.exposure_count"],
        side="candidate",
    )
    assert first == second
    assert len(first) == 2
    assert payload == before
    changed = copy.deepcopy(payload)
    changed["_meta"]["literal_author_note"] += " Changed."
    assert not field_presence_evidence_matches(
        _resolve(payload, "world.dome_open"),
        payload=changed,
        field="world.dome_open",
        side="candidate",
    )


def test_hash_matches_actual_planner_and_shared_policy_without_coercion():
    from bestseller.services.planner import _approved_payload_hash

    payload = {"world_spec": {"记录": [False, 0, None, {}]}, "cast_spec": {}}
    option = compile_field_presence_options(payload, fields=["world.记录"], side="source")[0]
    assert option["payload_hash"] == content_hash(payload) == _approved_payload_hash(payload)


def test_pointer_escaping_is_exact_and_not_a_nested_path_guess():
    payload = {"cast_spec": {"a/b~c": None, "a": {"b~c": False}}}
    evidence = _resolve(payload, "cast.a/b~c")
    assert evidence["json_pointer"] == "/cast_spec/a~1b~0c"
    assert evidence["value_type"] == "null"


@pytest.mark.parametrize(
    "payload", [{}, {"cast_spec": None}, {"cast_spec": []}, {"cast_spec": "unknown"}]
)
def test_missing_or_invalid_section_is_not_evidence_of_field_absence(payload):
    with pytest.raises(ValueError):
        compile_field_presence_options(payload, fields=["cast.antagonists"], side="candidate")


@pytest.mark.parametrize("field", ["cast", "cast.", "cast.a.b", ".x", "book.theme", None])
def test_invalid_field_fails_closed(field):
    with pytest.raises(ValueError):
        compile_field_presence_options({"cast_spec": {}}, fields=[field], side="candidate")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), (1, 2), object(), {1: "x"}])
def test_non_json_cannot_get_a_coerced_hash_witness(value):
    with pytest.raises(ValueError):
        compile_field_presence_options(
            {"cast_spec": {}, "other": value},
            fields=["cast.antagonists"],
            side="candidate",
        )


@pytest.mark.parametrize(
    ("key", "forgery"),
    [
        ("present", True),
        ("present", 0),
        ("value_hash", "forged"),
        ("value_type", "null"),
        ("json_pointer", "/cast_spec/antagonist"),
        ("payload_hash", "forged"),
        ("side", "source"),
        ("schema", "legacy"),
        ("kind", "author_rule"),
        ("quote", ""),
        ("quote", "invented author quote"),
        ("approved", True),
        ("verified", True),
    ],
)
def test_model_cannot_override_or_promote_server_observation(key, forgery):
    payload = {"cast_spec": {}}
    options = compile_field_presence_options(payload, fields=["cast.antagonists"], side="candidate")
    entry = {**options[0], "source_relation": "Observed only", key: forgery}
    with pytest.raises(ValueError):
        resolve_field_presence_evidence(entry, options, payload=payload, side="candidate")
    assert not field_presence_evidence_matches(
        entry,
        payload=payload,
        field="cast.antagonists",
        side="candidate",
    )


@pytest.mark.parametrize(
    "failure",
    [
        "unknown_id",
        "duplicate_id",
        "wrong_field",
        "wrong_side",
        "stale_payload",
        "forged_catalog",
        "missing_relation",
    ],
)
def test_catalog_reference_is_current_exact_and_unambiguous(failure):
    payload = {"cast_spec": {}}
    options = compile_field_presence_options(payload, fields=["cast.antagonists"], side="candidate")
    entry = {
        "quote_id": options[0]["quote_id"],
        "field": "cast.antagonists",
        "source_relation": "Observed only",
    }
    side = "candidate"
    if failure == "unknown_id":
        entry["quote_id"] = "c0"
    elif failure == "duplicate_id":
        options.append(copy.deepcopy(options[0]))
    elif failure == "wrong_field":
        entry["field"] = "cast.antagonist"
    elif failure == "wrong_side":
        side = "source"
    elif failure == "stale_payload":
        payload["cast_spec"]["antagonists"] = []
    elif failure == "forged_catalog":
        options[0]["present"] = True
    else:
        entry["source_relation"] = "  "
    with pytest.raises(ValueError):
        resolve_field_presence_evidence(entry, options, payload=payload, side=side)


def test_mixed_catalog_keeps_text_options_and_resolved_evidence_idempotent():
    payload = {"cast_spec": {}}
    options = compile_field_presence_options(payload, fields=["cast.antagonists"], side="candidate")
    options.insert(0, {"quote_id": "c0", "field": "cast.protagonist", "quote": "literal name"})
    before = copy.deepcopy(options)
    evidence = _resolve(payload)
    resolved = resolve_field_presence_evidence(evidence, options, payload=payload, side="candidate")
    assert resolved == evidence
    assert options == before
    assert not field_presence_evidence_matches(
        options[0],
        payload=payload,
        field="cast.antagonists",
        side="candidate",
    )


def test_compact_reference_is_not_already_reproved_coverage():
    payload = {"cast_spec": {}}
    evidence = _resolve(payload)
    compact = {key: evidence[key] for key in ("quote_id", "field", "source_relation")}
    assert not field_presence_evidence_matches(
        compact,
        payload=payload,
        field="cast.antagonists",
        side="candidate",
    )
