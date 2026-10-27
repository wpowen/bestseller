"""Exact program references select a finite ungraded projection, never a grade."""

from copy import deepcopy
import json
import socket
from types import SimpleNamespace

import pytest

from bestseller.services import planning_projection_consistency as projection
from bestseller.services.lossless_source_encoding import _json

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def forbid_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Reference closure tests forbid network/PG/provider")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def case(story="polar weather station"):
    contracts = {
        "opening_contract": {"summary": f"Enter the {story}"},
        "payoff_contract": {
            "summary": "Record the measurement",
            "schema_extra_field": "An earlier reading",
            "uncited_sibling": "Leave this narrative unchanged",
            "unknown_metadata": {"summary": "Opaque original metadata"},
        },
    }
    method = {
        "fanqie_short_v2": deepcopy(contracts),
        "book_source_context": {"summary": "Full protected source"},
    }
    chapter = {
        "chapter_number": 4,
        "id": "original-chapter",
        "title": "Original title",
        "pov": "first_person",
        "chapter_goal": "Keep the station operational",
        "goal": "Keep this independent authored alias",
        "payoff": "A result outside this proposal",
        "chapter_concrete_actions": ["Look at the gauge", "Record a reading"],
        "causal_contract": {
            "state_change": "A reading exists",
            "fanqie_short_v2": deepcopy(contracts),
        },
        "methodology_contract": deepcopy(method),
        "target_word_count": 1800,
        "unknown_metadata": {"summary": "Keep metadata"},
        "unregistered_field": "Keep this extra field",
        "scenes": [
            {
                "scene_number": 2,
                "id": "original-scene",
                "participants": ["Observer", "Assistant"],
                "purpose": {"story": f"Inspect the {story}"},
                "entry_state": {"summary": "Arrive before dawn"},
                "exit_state": {"summary": "Leave with the recorded reading"},
                "action_sequence": ["Compare two instruments", "Record the discrepancy"],
                "methodology_contract": deepcopy(method),
            },
            {
                "scene_number": 7,
                "participants": ["Observer"],
                "purpose": {"story": "A different scene"},
                "action_sequence": ["Do not edit this action"],
            },
        ],
    }
    other = deepcopy(chapter)
    other["chapter_number"] = 11
    candidate = {"chapters": [chapter, other], "_meta": {"original": None, "approved": False}}
    observations = [
        {
            "draft_eligible": False,
            "hard_violations": [
                {
                    "code": "CONTINUITY_CONFLICT",
                    "path": "chapters[0].scenes[0]",
                    "source_refs": [
                        "/candidate_payload/chapters/1/causal_contract/fanqie_short_v2/"
                        "payoff_contract/schema_extra_field",
                        "/candidate_payload/chapters/0/payoff",
                        "/candidate_payload/chapters/0/no_such_path",
                    ],
                    "candidate_ids": ["original-catalog-id"],
                    "evidence": "Literal evidence with untrusted commentary remains unchanged",
                }
            ],
        }
    ]
    return candidate, observations


def completion(candidate, contract, changes=None):
    values = {key: projection._at(candidate, path) for key, path in contract["id_to_path"].items()}
    for path, value in (changes or {}).items():
        key = next(key for key, bound in contract["id_to_path"].items() if path == bound)
        values[key] = value
    arguments = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    call = {
        "id": "original-native-call",
        "type": "function",
        "function": {"name": projection.TOOL, "arguments": arguments},
    }
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "tool_calls": [call],
        "raw_message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [deepcopy(call)],
            "reasoning_content": "Debug never supplies a candidate or grade",
        },
    }


def test_default_none_preserves_frozen_8j_contract_wire_and_derived_receipt_bytes():
    candidate, observations = case()
    authority = {"source": "Original complete source", "approved": False}
    default = projection.build_projection_consistency_contract(
        candidate, observations, source_authority=authority
    )
    explicit = projection.build_projection_consistency_contract(
        candidate, observations, source_authority=authority, reference_paths=None
    )
    assert _json(default) == _json(explicit)
    assert "reference_selection" not in default
    native = completion(
        candidate, default, {"/chapters/0/payoff": "An independently revised event"}
    )
    result, receipt = projection.parse_projection_consistency(
        native, candidate=candidate, contract=default
    )
    # Captured from the unmodified 8J file, SHA bec18c80...e454c, before this change.
    assert (
        projection._hash(default)
        == "0fe85bf7e02a6cc502620d544fe4db27399d7eeec020d0bc008e06925265c1cf"
    )
    assert (
        projection._hash(projection.tools_for_projection_consistency(default))
        == "c67207a7defc23b23a01d7c08f6b98d1127de0e886563540ba9604f775e34c82"
    )
    assert (
        projection._hash([result, receipt])
        == "adfedcc3c99ae36cc55f5733303aefdfa43e7034722ea997f88383705f41e45f"
    )
    assert "reference_selection" not in receipt


@pytest.mark.parametrize("story", ["polar weather station", "medieval harbour", "urban clinic"])
def test_only_selected_scalars_and_exact_producer_copies_are_opened(story):
    candidate, observations = case(story)
    seed = "/chapters/0/causal_contract/fanqie_short_v2/payoff_contract/schema_extra_field"
    action = "/chapters/0/scenes/0/action_sequence/1"
    refs = [action, seed]
    before = deepcopy((candidate, observations, refs))
    authority = {"approved": True, "claim": "Still only an unverified binding"}
    contract = projection.build_projection_consistency_contract(
        candidate, observations, source_authority=authority, reference_paths=refs
    )
    copies = {
        "/chapters/0/" + root + "/fanqie_short_v2/payoff_contract/schema_extra_field"
        for root in ("causal_contract", "methodology_contract", "scenes/0/methodology_contract")
    }
    assert set(contract["paths"]) == {action, *copies}
    assert contract["affected_chapters"] == [{"index": 0, "chapter_number": 4}]
    selection = contract["reference_selection"]
    assert selection["schema"] == projection.REFERENCE_SELECTION
    assert selection["reference_paths"] == refs
    assert selection["reference_paths_sha256"] == projection._hash(refs)
    assert selection["closure_paths_sha256"] == projection._hash(contract["paths"])
    assert selection["closure_groups_sha256"] == projection._hash(contract["projection_groups"])
    assert not selection["source_authority_granted"]
    assert not contract["source_authority_granted"] and not contract["grade_asserted"]
    assert contract["observations"] == observations and contract["source_authority"] == authority
    assert contract["invalid_references"][0]["value"].endswith("/no_such_path")
    assert contract["uncovered_references"] == []
    assert all(row["status"] == "unselected_reference" for row in contract["references"])
    result, receipt = projection.parse_projection_consistency(
        completion(candidate, contract, {action: "Record a new observed discrepancy"}),
        candidate=candidate,
        contract=contract,
    )
    expected = deepcopy(candidate)
    expected["chapters"][0]["scenes"][0]["action_sequence"][1] = "Record a new observed discrepancy"
    assert _json(result) == _json(expected)
    assert receipt["changed_paths"] == [action]
    assert receipt["non_targets_unchanged"] and receipt["inverse_verified"]
    assert receipt["reference_selection"] == selection
    assert receipt["full_domain_readiness_and_new_admission_required"]
    assert not receipt["grade_asserted"] and not receipt["source_authority_granted"]
    assert not receipt["physical_custody_verified"]
    assert before == (candidate, observations, refs)


def test_copies_with_different_current_values_are_not_automatically_synchronized():
    candidate, observations = case()
    chapter = candidate["chapters"][0]
    chapter["methodology_contract"]["fanqie_short_v2"]["payoff_contract"]["schema_extra_field"] = (
        "Later"
    )
    seed = "/chapters/0/causal_contract/fanqie_short_v2/payoff_contract/schema_extra_field"
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[seed]
    )
    assert len(contract["paths"]) == 3
    result, receipt = projection.parse_projection_consistency(
        completion(candidate, contract, {seed: "An independently submitted observation"}),
        candidate=candidate,
        contract=contract,
    )
    assert receipt["changed_paths"] == [seed]
    for path in set(contract["paths"]) - {seed}:
        assert projection._at(result, path) == projection._at(candidate, path)


def produced_case():
    from bestseller.domain.fanqie_short import FanqieShortBeat, FanqieShortBeatSheet
    from bestseller.services.fanqie_segment_projection import project_segment_outline

    sheet = FanqieShortBeatSheet(
        beats=[
            FanqieShortBeat(
                segment_number=1,
                beat_role="observational",
                purpose="Inspect a harbour signal",
                payoff="Keep the incomplete reading",
                emotional_turn="A new uncertainty",
                opening_contract={
                    "key_dialogue_beats": ["Ask for the reading", "Name the instrument limitation"],
                    "signature_image": "A lantern beside the gauge",
                },
            )
        ]
    )
    candidate = project_segment_outline(
        SimpleNamespace(target_chapters=1, target_word_count=2000, metadata_json={}), sheet
    )
    observations = [
        {
            "hard_violations": [
                {
                    "code": "CONTINUITY_CONFLICT",
                    "path": "chapters[0].scenes[0]",
                    "source_refs": [],
                }
            ]
        }
    ]
    return candidate, observations


@pytest.mark.parametrize("direction", ["scene", "contract", "both"])
def test_actual_producer_dialogue_family_closes_only_the_exact_list_index(direction):
    candidate, observations = produced_case()
    scene = candidate["chapters"][0]["scenes"][0]
    # This same-named flat methodology field has no mapping in that producer.
    scene["methodology_contract"]["key_dialogue_beats"] = ["Not a proved copy", "Keep frozen"]
    local = "/chapters/0/scenes/0/key_dialogue_beats/1"
    copies = {
        "/chapters/0/" + root + "/fanqie_short_v2/opening_contract/key_dialogue_beats/1"
        for root in ("causal_contract", "methodology_contract", "scenes/0/methodology_contract")
    }
    reference = sorted(copies)[0]
    paths = [local] if direction == "scene" else [reference]
    if direction == "both":
        paths = [local, reference]
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=paths
    )
    assert set(contract["paths"]) == {local, *copies}
    assert len(contract["projection_groups"]) == 1
    assert all(not path.endswith("/0") for path in contract["paths"])
    assert "/chapters/0/scenes/0/methodology_contract/key_dialogue_beats/1" not in contract["paths"]
    result, receipt = projection.parse_projection_consistency(
        completion(candidate, contract, {local: "State the independently observed limit"}),
        candidate=candidate,
        contract=contract,
    )
    assert receipt["changed_paths"] == [local]
    assert result["chapters"][0]["scenes"][0]["methodology_contract"]["key_dialogue_beats"] == [
        "Not a proved copy",
        "Keep frozen",
    ]


def test_actual_producer_scalar_methodology_copy_and_contract_copies_are_closed():
    candidate, observations = produced_case()
    path = "/chapters/0/scenes/0/signature_image"
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[path]
    )
    assert len(contract["paths"]) == 5
    assert path in contract["paths"]
    assert "/chapters/0/scenes/0/methodology_contract/signature_image" in contract["paths"]
    assert all(p.endswith("/signature_image") for p in contract["paths"])


def test_multiple_scenes_do_not_invent_a_producer_correspondence():
    candidate, observations = produced_case()
    other = deepcopy(candidate["chapters"][0]["scenes"][0])
    other["scene_number"] = 2
    candidate["chapters"][0]["scenes"].append(other)
    path = "/chapters/0/scenes/0/key_dialogue_beats/1"
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[path]
    )
    assert contract["paths"] == [path]


def test_present_non_scalar_producer_copy_fails_instead_of_leaving_a_half_closed_family():
    candidate, observations = produced_case()
    method = candidate["chapters"][0]["methodology_contract"]
    method["fanqie_short_v2"]["opening_contract"]["key_dialogue_beats"][1] = {
        "summary": "Shape changed"
    }
    with pytest.raises(ValueError, match="reference_closure_scalar"):
        projection.build_projection_consistency_contract(
            candidate, observations, reference_paths=["/chapters/0/scenes/0/key_dialogue_beats/1"]
        )


@pytest.mark.parametrize("value", ["Existing narrative", 7, 2.5, False])
def test_exact_scalar_types_escaped_paths_and_uncited_siblings_are_preserved(value):
    candidate, observations = case()
    state = candidate["chapters"][0]["scenes"][0]["entry_state"]
    state["generic/field~name"] = value
    state["uncited"] = value
    path = "/chapters/0/scenes/0/entry_state/generic~1field~0name"
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[path]
    )
    assert contract["paths"] == [path]
    assert contract["parameters"]["properties"]["f0001"]["type"] == projection._TYPES[type(value)]
    assert list(
        projection.tools_for_projection_consistency(contract)[0]["function"]["parameters"][
            "properties"
        ]
    ) == ["f0001"]


@pytest.mark.parametrize(
    "paths",
    [
        [],
        "not-a-list",
        [None],
        [True],
        ["/chapters/0/payoff", "/chapters/0/payoff"],
        ["/candidate_payload/chapters/0/payoff"],
        ["/chapters/00/payoff"],
        ["/chapters/99/payoff"],
        ["/chapters/0/scenes/0/action_sequence/01"],
        ["/chapters/0/scenes/0/action_sequence"],
        ["/chapters/0/scenes/0/entry_state"],
        ["/chapters/0/scenes/0/purpose/stroy"],
        ["/chapters/0/title"],
        ["/chapters/0/id"],
        ["/chapters/0/pov"],
        ["/chapters/0/scenes/0/participants/0"],
        ["/chapters/0/target_word_count"],
        ["/chapters/0/unknown_metadata/summary"],
        ["/chapters/0/methodology_contract/book_source_context/summary"],
        ["/chapters/0/unregistered_field"],
        ["/chapters/0/causal_contract/fanqie_short_v2/payoff_contract/unknown_metadata/summary"],
    ],
)
def test_invalid_non_scalar_unregistered_protected_or_guessed_pointers_fail_closed(paths):
    candidate, observations = case()
    before = deepcopy(candidate)
    with pytest.raises(ValueError, match="planning_projection_consistency:"):
        projection.build_projection_consistency_contract(
            candidate, observations, reference_paths=paths
        )
    assert candidate == before


@pytest.mark.parametrize(
    "fault",
    [
        "paths",
        "mode",
        "closure_hash",
        "groups_hash",
        "candidate",
        "observations",
        "source",
        "remove_mode",
    ],
)
def test_parser_rebuild_binds_the_mode_all_sources_and_exact_reference_paths(fault):
    candidate, observations = case()
    path = "/chapters/0/scenes/0/action_sequence/1"
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[path]
    )
    native = completion(candidate, contract, {path: "An actual different action"})
    if fault == "paths":
        contract["reference_selection"]["reference_paths"] = ["/chapters/0/payoff"]
    elif fault == "mode":
        contract["reference_selection"]["schema"] = "unregistered-mode"
    elif fault == "closure_hash":
        contract["reference_selection"]["closure_paths_sha256"] = "0" * 64
    elif fault == "groups_hash":
        contract["reference_selection"]["closure_groups_sha256"] = "0" * 64
    elif fault == "candidate":
        candidate["chapters"][1]["payoff"] += " late change to frozen chapter"
    elif fault == "observations":
        contract["observations"][0]["hard_violations"][0]["evidence"] += " changed"
    elif fault == "source":
        contract["source_authority"] = {"new": "unverified"}
    else:
        del contract["reference_selection"]
    with pytest.raises(ValueError):
        projection.parse_projection_consistency(native, candidate=candidate, contract=contract)


@pytest.mark.parametrize(
    "fault", ["extra_field", "missing_field", "object", "bool_as_int", "raw_conflict", "unchanged"]
)
def test_narrow_native_still_requires_every_typed_field_and_raw_evidence(fault):
    candidate, observations = case()
    candidate["chapters"][0]["causal_contract"]["observed_count"] = 2
    path = "/chapters/0/causal_contract/observed_count"
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[path]
    )
    native = completion(candidate, contract, {path: 3})
    args = {"f0001": 3}
    if fault == "extra_field":
        args["f0002"] = "Do not edit an unbound field"
    elif fault == "missing_field":
        args.clear()
    elif fault == "object":
        args["f0001"] = {"summary": "Not a scalar"}
    elif fault == "bool_as_int":
        args["f0001"] = True
    elif fault == "unchanged":
        args["f0001"] = 2
    native["tool_calls"][0]["function"]["arguments"] = json.dumps(args)
    if fault != "raw_conflict":
        native["raw_message"]["tool_calls"] = deepcopy(native["tool_calls"])
    before = deepcopy(candidate)
    with pytest.raises(ValueError):
        projection.parse_projection_consistency(native, candidate=candidate, contract=contract)
    assert candidate == before
