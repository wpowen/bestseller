"""Exact untrusted selectors, without provider, storage or grade interpretation."""

from copy import deepcopy
import hashlib
import socket
from types import MappingProxyType
from uuid import uuid4

import pytest
from test_planning_admission_id_protocol import base, completion, payload, ref

from bestseller.services import llm
from bestseller.services import planning_admission_id_protocol as ids
from bestseller.services import planning_admission_ref_protocol as v4
from bestseller.services import planning_draft_admission as admission
from bestseller.services import planning_reference_observations as observations

pytestmark = pytest.mark.unit
EARLY = "/candidate_payload/chapters/0/scenes/0/entry_state"
SECOND = "/candidate_payload/chapters/1/scenes/0/action_sequence/0"
LOCAL = "/candidate_payload/chapters/3/scenes/0/dialogue/0"
WORLD = "/approved_world_spec/requirement"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Observation binding forbids DB/provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)


def current(theme="An unrelated estuary"):
    supplied = base(theme=theme)
    supplied["candidate_payload"]["chapters"].extend(
        [
            {"chapter_number": 5, "title": theme + " crossing", "scenes": [{"action": "Wait"}]},
            {
                "chapter_number": 6,
                "title": theme + " reply",
                "scenes": [
                    {
                        "dialogue": ["The observer recalls the earlier boundary"],
                        "unknown_authored": {"中文.键/~": "Literal §/$ref note", "unset": None},
                    }
                ],
            },
        ]
    )
    supplied["projection_audit_targets"] = admission.projection_audit_targets(
        supplied["candidate_payload"]
    )
    supplied["source_authority"] = admission._source_authority(supplied)
    supplied["source_authority_allowed_codes"] = v4._permissions(supplied["source_authority"])
    return ids.v5_input(supplied)


def native(supplied):
    value = payload(supplied, code="CONTINUITY_CONFLICT")
    value["hard_violations"][0].update(
        path="chapters[3].scenes[0]",
        evidence="Literal quote plus commentary; deliberately not a bound quotation.",
        source_requirement="Untrusted explanation, not a claimed source quotation.",
        source_refs=[ref(supplied, EARLY), ref(supplied, SECOND)],
        evidence_refs=[ref(supplied, LOCAL), ref(supplied, EARLY), ref(supplied, SECOND)],
    )
    return value


def bind(supplied, value=None, *, returned=None):
    return observations.bind_native_reference_observations(
        returned
        if returned is not None
        else completion(value if value is not None else native(supplied)),
        supplied,
    )


@pytest.mark.parametrize("theme", ["A coastal archive", "另一座山城", "Station Delta"])
def test_complete_native_false_is_only_untrusted_lossless_observations(theme, monkeypatch):
    supplied = current(theme)
    value = native(supplied)
    # The real v5 parser still rejects this exact native submission.
    with pytest.raises(ValueError, match="candidate_evidence_unbound"):
        ids.parse_reference_submission(
            completion(value), supplied=supplied, contract=ids.reference_contract(supplied)
        )
    value["opaque_unknown"] = {"flag": False, "null": None, "$ref": "literal", "count": 0}
    value["hard_violations"][0]["unknown_note"] = {"original": "  preserve text\n"}
    returned = completion(value)
    before = deepcopy((supplied, returned))

    def no_grade(*args, **kwargs):
        raise AssertionError("Observations must not call grade or quote validators")

    monkeypatch.setattr(ids, "parse_reference_submission", no_grade)
    monkeypatch.setattr(ids, "_quoted", no_grade)
    monkeypatch.setattr(admission, "_grade", no_grade)
    result = bind(supplied, returned=MappingProxyType(returned))
    assert result["native_payload"] == value
    assert result["native_payload"]["draft_eligible"] is False
    assert result["candidate_reference_paths"] == sorted(
        [
            EARLY.removeprefix("/candidate_payload"),
            SECOND.removeprefix("/candidate_payload"),
            LOCAL.removeprefix("/candidate_payload"),
        ]
    )
    raw = returned["tool_calls"][0]["function"]["arguments"]
    assert result["raw_arguments_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert "raw_arguments" not in result
    assert (
        not {"parsed_payload", "raw_arguments", "original_content"}
        & result["native_decode_receipt"].keys()
    )
    assert result["native_payload_sha256"] == ids._sha(value)
    assert result["supplied_sha256"] == ids._sha(supplied)
    assert result["grade_asserted"] is result["canon_asserted"] is False
    assert result["old_grade_reinterpreted"] is result["author_authority_granted"] is False
    assert result["semantic_validation_performed"] is False
    assert result["issues_dropped"] == 0 and result["native_text_modified"] is False
    assert (supplied, returned) == before
    audit = result["resolved_references"][0]
    assert [row["id"] for row in audit["source_refs"]] == value["hard_violations"][0]["source_refs"]
    assert [row["id"] for row in audit["evidence_refs"]] == value["hard_violations"][0][
        "evidence_refs"
    ]
    for kind in ("source_refs", "evidence_refs"):
        for row in audit[kind]:
            assert row["value_sha256"] == ids._sha(row["value"])
            assert row["catalog_row_sha256"] == ids._sha(
                supplied["source_reference_catalog"]["catalog"][row["id"]]
            )


def test_external_authority_retains_exact_original_ids_roles_and_no_edit_path():
    supplied = current()
    value = native(supplied)
    value["hard_violations"][0].update(
        code="ABILITY_RULE_CONFLICT", source_refs=[ref(supplied, WORLD)]
    )
    result = bind(supplied, value)
    row = result["resolved_references"][0]["source_refs"][0]
    assert row["id"] == ref(supplied, WORLD) and row["pointer"] == WORLD
    assert row["role"] == "world_rule" and "ABILITY_RULE_CONFLICT" in row["allowed_codes"]
    assert "candidate_reference_path" not in row
    assert not any(path.startswith("/approved_") for path in result["candidate_reference_paths"])


def test_multiple_violations_and_cross_array_duplicates_preserve_every_entry():
    supplied = current()
    value = native(supplied)
    value["hard_violations"].append(deepcopy(value["hard_violations"][0]))
    result = bind(supplied, value)
    assert result["native_payload"] == value and len(result["resolved_references"]) == 2
    assert len(result["candidate_reference_paths"]) == 3


def test_unknown_key_pointers_and_scalar_types_are_preserved_exactly():
    supplied = current()
    pointer = "/candidate_payload/chapters/3/scenes/0/unknown_authored/中文.键~1~0"
    value = native(supplied)
    value["hard_violations"][0]["evidence_refs"].append(ref(supplied, pointer))
    result = bind(supplied, value)
    assert pointer.removeprefix("/candidate_payload") in result["candidate_reference_paths"]
    assert result["resolved_references"][0]["evidence_refs"][-1]["value"] == "Literal §/$ref note"


@pytest.mark.parametrize("field", ["source_refs", "evidence_refs"])
@pytest.mark.parametrize(
    "bad", [[], None, "r000001", ["r999999"], [EARLY], ["candidate:chapters.0"]]
)
def test_unknown_ids_aliases_and_malformed_arrays_fail_whole_binding(field, bad):
    supplied = current()
    value = native(supplied)
    value["hard_violations"][0][field] = bad
    with pytest.raises(ValueError):
        bind(supplied, value)


@pytest.mark.parametrize("field", ["source_refs", "evidence_refs"])
def test_duplicate_id_within_either_array_is_rejected(field):
    supplied = current()
    value = native(supplied)
    value["hard_violations"][0][field].append(value["hard_violations"][0][field][0])
    with pytest.raises(ValueError, match="duplicate_reference"):
        bind(supplied, value)


@pytest.mark.parametrize(
    "field,pointer",
    [
        ("source_refs", "/candidate_payload/chapters/0/scenes/0/_meta/raw"),
        ("source_refs", "/candidate_payload/chapters/0/scenes/0/unknown_authored/null"),
        ("evidence_refs", WORLD),
    ],
)
def test_provenance_unasserted_or_non_candidate_evidence_cannot_authorize_selector(field, pointer):
    supplied = current()
    value = native(supplied)
    value["hard_violations"][0][field] = [ref(supplied, pointer)]
    with pytest.raises(ValueError):
        bind(supplied, value)


def test_candidate_order_is_not_ability_rule_authority():
    supplied = current()
    value = native(supplied)
    value["hard_violations"][0]["code"] = "ABILITY_RULE_CONFLICT"
    with pytest.raises(ValueError, match="source_rights"):
        bind(supplied, value)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("code", "UNKNOWN_NEW_CODE"),
        ("path", "chapters[99]"),
        ("path", "chapters[3].scenes[0].dialogue"),
        ("path", "/chapters/3/scenes/0"),
    ],
)
def test_unknown_codes_and_non_exact_scope_are_not_repaired(field, bad):
    supplied = current()
    value = native(supplied)
    value["hard_violations"][0][field] = bad
    with pytest.raises(ValueError):
        bind(supplied, value)


def test_evidence_must_include_current_scope_but_can_retain_earlier_references():
    supplied = current()
    value = native(supplied)
    value["hard_violations"][0]["evidence_refs"] = [ref(supplied, EARLY)]
    with pytest.raises(ValueError, match="evidence_scope"):
        bind(supplied, value)


@pytest.mark.parametrize(
    "change",
    [
        "body",
        "catalog_value",
        "catalog_role",
        "catalog_rights",
        "targets",
        "scope",
        "missing_catalog",
    ],
)
def test_changed_current_body_or_stored_catalog_fails_closed(change):
    supplied = current()
    returned = completion(native(supplied))
    if change == "body":
        supplied["candidate_payload"]["chapters"][3]["scenes"][0]["dialogue"][0] += " changed"
    elif change.startswith("catalog_"):
        row = supplied["source_reference_catalog"]["catalog"][ref(supplied, LOCAL)]
        row[
            {
                "catalog_value": "value_sha256",
                "catalog_role": "role",
                "catalog_rights": "allowed_codes",
            }[change]
        ] = {
            "catalog_value": "0" * 64,
            "catalog_role": "world_rule",
            "catalog_rights": ["ABILITY_RULE_CONFLICT"],
        }[change]
    elif change == "targets":
        supplied["projection_audit_targets"][0]["projection_hash"] = "0" * 64
    elif change == "scope":
        supplied["project_id"] = str(uuid4())
    else:
        supplied.pop("source_reference_catalog")
    with pytest.raises(ValueError):
        bind(supplied, returned=returned)


@pytest.mark.parametrize("field,value", [("project_id", None), ("workflow_run_id", "not-uuid")])
def test_scope_ids_are_required_even_if_untrusted_native_is_complete(field, value):
    supplied = current()
    returned = completion(native(supplied))
    supplied[field] = value
    with pytest.raises(ValueError, match="scope_identity"):
        bind(supplied, returned=returned)


@pytest.mark.parametrize(
    "change",
    [
        "raw_none",
        "raw_conflict",
        "raw_content",
        "two_tools",
        "wrong_tool",
        "truncated",
        "duplicate_json",
        "json_content",
        "no_tools",
    ],
)
def test_real_native_framing_is_strict_and_nullable_raw_is_honest(change):
    supplied = current()
    returned = completion(native(supplied))
    if change == "raw_none":
        returned["raw_message"] = None
        result = bind(supplied, returned=returned)
        assert result["native_decode_receipt"]["raw_message_present"] is False
        assert result["native_decode_receipt"]["raw_message_status"] == "missing"
        return
    if change == "raw_conflict":
        returned["raw_message"]["tool_calls"][0]["function"]["arguments"] = "{}"
    elif change == "raw_content":
        returned["raw_message"]["content"] = "conflicting content"
    elif change == "two_tools":
        returned["tool_calls"].append(deepcopy(returned["tool_calls"][0]))
    elif change == "wrong_tool":
        returned["tool_calls"][0]["function"]["name"] = "another_function"
    elif change == "truncated":
        returned["finish_reason"] = "length"
    elif change == "duplicate_json":
        raw = returned["tool_calls"][0]["function"]["arguments"]
        returned["tool_calls"][0]["function"]["arguments"] = raw[:-1] + ',"hard_violations":[]}'
        returned["raw_message"] = None
    elif change == "json_content":
        returned["content"] = "{}"
        returned["raw_message"]["content"] = "{}"
    else:
        returned["tool_calls"] = None
    with pytest.raises(ValueError):
        bind(supplied, returned=returned)


def test_bad_late_id_rejects_every_observation_without_mutation():
    supplied = current()
    value = native(supplied)
    value["hard_violations"].append(deepcopy(value["hard_violations"][0]))
    value["hard_violations"][-1]["evidence_refs"].append("r999999")
    before = deepcopy((supplied, value))
    with pytest.raises(ValueError, match="reference_not_in_catalog"):
        bind(supplied, value)
    assert (supplied, value) == before


def test_no_violations_and_arbitrary_native_extras_remain_untrusted_not_grade():
    supplied = current()
    value = {"hard_violations": [], "unknown": {"native": False}, "projection_checks": []}
    result = bind(supplied, value)
    assert result["native_payload"] == value
    assert result["candidate_reference_paths"] == result["resolved_references"] == []
    assert result["grade_asserted"] is False
