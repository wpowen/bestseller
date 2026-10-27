"""Pure finite coherence proposals across unrelated sources; no PG or providers."""

from copy import deepcopy
import json
import socket

import pytest

from bestseller.services import planning_projection_consistency as projection
from bestseller.services.lossless_source_encoding import _json

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pure projection tests forbid PG/provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def case(*, producer=True, story="lunar excavation"):
    contracts = {
        "opening_contract": {
            "summary": f"Begin {story}",
            "unknown_metadata": {"summary": "freeze"},
        },
        "continuity_contract": {"summary": "Proceed after departure", "time_position": "Morning"},
        "payoff_contract": {
            "summary": "Observe the result",
            "schema_extra_field": "A short test",
        },
        "closure_contract": {"summary": "Keep the measured result", "no_serial_hook": True},
        "unlock_contract": {
            "summary": "Carry the record",
            "next_segment_trigger": "Discuss it later",
        },
        "ability_cost_contract": {"cost": "A limited resource", "source": "frozen citation"},
    }
    method = {
        "book_source_context": {"payoff": "frozen full source", "unknown": None},
        "selected_creation_contracts": {"pov": "first_person"},
        "_meta": {"summary": "opaque original receipt"},
    }
    if producer:
        method.update(fanqie_short_v2=deepcopy(contracts), fanqie_short_payoff="A short test")
    scene = {
        "scene_number": 2,
        "id": "scene-kept",
        "title": "Keep this title",
        "participants": ["Surveyor", "Witness"],
        "pov_character": "Surveyor",
        "purpose": {"story": f"Inspect {story}", "emotion": "unease", "unknown": "freeze"},
        "entry_state": {"summary": "Before departure", "unknown": {"summary": "freeze"}},
        "exit_state": {"summary": "After departure"},
        "action_sequence": ["Read the instrument", "Write the result"],
        "key_dialogue_beats": ["State the boundary", "Acknowledge it"],
        "methodology_contract": deepcopy(method),
        "target_word_count": 800,
        "unknown_extra": {"action_sequence": ["opaque history"], "flag": False},
    }
    chapter = {
        "chapter_number": 4,
        "id": "chapter-kept",
        "title": "Keep this title",
        "chapter_goal": f"Observe {story}",
        "goal": f"Observe {story}",
        "opening_situation": "Start before dawn",
        "payoff": "A short test",
        "visible_action_or_reaction": "The instrument settles",
        "tail_hook_description": "Discuss the record later",
        "pov": "first_person",
        "target_word_count": 2200,
        "volume_number": 3,
        "causal_contract": {
            "state_change": "The record exists",
            "cost_or_tradeoff": 2,
            "unknown_metadata": {"state_change": "freeze"},
        },
        "methodology_contract": deepcopy(method),
        "scenes": [scene],
        "metadata": {"goal": "opaque metadata", "n": None, "flag": False},
    }
    if producer:
        chapter["causal_contract"]["fanqie_short_v2"] = deepcopy(contracts)
    unrelated = {**deepcopy(chapter), "chapter_number": 9, "title": "Unrelated chapter"}
    candidate = {
        "batch_name": "unrelated-fiction",
        "chapters": [chapter, unrelated],
        "_meta": {"raw": "all original history", "author_selected_pov": "first_person"},
    }
    refs = [
        "/candidate_payload/chapters/0/causal_contract/state_change",
        "/candidate_payload/chapters/0/fanqie_short_payoff",
        "/approved_world_spec/rules/999",
    ]
    if producer:
        refs.insert(
            2,
            "/candidate_payload/chapters/0/causal_contract/fanqie_short_v2/"
            "payoff_contract/schema_extra_field",
        )
    observations = [
        {
            "draft_eligible": False,
            "hard_violations": [
                {
                    "code": "CONTINUITY_CONFLICT",
                    "path": "chapters[0]",
                    "source_refs": refs,
                    "evidence": "Untrusted native concern, not an approved finding",
                    "source_requirement": "The original complete source must be checked "
                    "independently",
                }
            ],
            "editorial_notes": ["Unknown protocol; no grade authority"],
            "projection_checks": [{"path": "chapters[0].scenes[0]", "status": "conflict"}],
        }
    ]
    return candidate, observations


def completion(candidate, contract, changes=None):
    values = {
        key: deepcopy(projection._at(candidate, path))
        for key, path in contract["id_to_path"].items()
    }
    for path, value in (changes or {}).items():
        key = next(k for k, p in contract["id_to_path"].items() if p == path)
        values[key] = value
    arguments = json.dumps(values, ensure_ascii=False, indent=2)
    call = {
        "id": "native-scalar-fields",
        "type": "function",
        "function": {"name": projection.TOOL, "arguments": arguments},
    }
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "provider": "retained-provider",
        "tool_calls": [call],
        "raw_message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [deepcopy(call)],
            "reasoning_content": "debug only, never a correction",
        },
    }


@pytest.mark.parametrize(
    "producer,story",
    [
        (True, "lunar excavation"),
        (True, "medieval harbour inspection"),
        (False, "deep-sea biological survey"),
    ],
)
def test_registered_crossbook_targets_preserve_all_frozen_values_and_only_make_ungraded_proposal(
    producer,
    story,
):
    candidate, observations = case(producer=producer, story=story)
    source = {"unverified": None, "source_complete": True, "approved": False}
    before = deepcopy((candidate, observations, source))
    contract = projection.build_projection_consistency_contract(
        candidate, observations, source_authority=source
    )
    assert contract["affected_chapters"] == [{"index": 0, "chapter_number": 4}]
    assert all(p.startswith("/chapters/0/") for p in contract["paths"])
    assert "/chapters/0/payoff" in contract["paths"]
    assert "/chapters/0/visible_action_or_reaction" in contract["paths"]
    assert "/chapters/0/tail_hook_description" in contract["paths"]
    assert not any(
        "participants" in p
        or "metadata" in p
        or "book_source_context" in p
        or "selected_creation_contracts" in p
        or p.endswith(("/title", "/pov", "/id"))
        for p in contract["paths"]
    )
    change = {"/chapters/0/scenes/0/action_sequence/1": "Record the actual later observation"}
    native = completion(candidate, contract, change)
    result, receipt = projection.parse_projection_consistency(
        native, candidate=candidate, contract=contract
    )
    expected = deepcopy(candidate)
    expected["chapters"][0]["scenes"][0]["action_sequence"][1] = change[next(iter(change))]
    assert _json(result) == _json(expected)
    assert before == (candidate, observations, source)
    assert receipt["native_completion"] == native
    assert receipt["non_targets_unchanged"] and receipt["inverse_verified"]
    assert not receipt["grade_asserted"] and not receipt["source_authority_granted"]
    assert not receipt["physical_custody_verified"]
    assert receipt["semantic_validation"] == "not_evaluated"


def test_producer_copy_closure_uses_paths_even_when_existing_values_disagree():
    candidate, observations = case()
    chapter = candidate["chapters"][0]
    chapter["methodology_contract"]["fanqie_short_v2"]["payoff_contract"]["schema_extra_field"] = (
        "A conflicting long test"
    )
    contract = projection.build_projection_consistency_contract(candidate, observations)
    suffix = "/fanqie_short_v2/payoff_contract/schema_extra_field"
    copies = [p for p in contract["paths"] if p.endswith(suffix)]
    assert len(copies) == 3
    for path in copies:
        assert any(
            path in group and set(copies) <= set(group)
            for group in contract["projection_groups"].values()
        )
    # Selecting every copy is not permission to choose a winner automatically.
    first = copies[0]
    result, receipt = projection.parse_projection_consistency(
        completion(candidate, contract, {first: "A model supplied distinct test"}),
        candidate=candidate,
        contract=contract,
    )
    assert receipt["changed_paths"] == [first]
    for other in copies[1:]:
        assert projection._at(result, other) == projection._at(candidate, other)


def test_invalid_references_remain_exact_and_optional_source_metadata_cannot_grant_authority():
    candidate, observations = case()
    contract = projection.build_projection_consistency_contract(
        candidate, observations, source_authority={"approved": True, "grade": "pass"}
    )
    assert contract["observations"] == observations
    assert (
        contract["invalid_references"][0]["value"]
        == observations[0]["hard_violations"][0]["source_refs"][1]
    )
    assert contract["references"][-1]["status"] == "external_unverified"
    assert contract["source_authority_granted"] is False
    assert contract == projection.build_projection_consistency_contract(
        candidate, observations, source_authority={"approved": True, "grade": "pass"}
    )
    tool = projection.tools_for_projection_consistency(contract)[0]
    assert tool["function"]["name"] == projection.TOOL
    assert tool["function"]["parameters"]["additionalProperties"] is False
    assert list(contract["id_to_path"]) == tool["function"]["parameters"]["required"]


def test_unknown_contract_metadata_cannot_become_mutable_from_a_native_hint():
    candidate, observations = case()
    ref = "/candidate_payload/chapters/0/causal_contract/unknown_metadata/state_change"
    observations[0]["hard_violations"][0]["source_refs"] = [ref]
    contract = projection.build_projection_consistency_contract(candidate, observations)
    assert contract["references"][0]["value"] == ref
    assert contract["references"][0]["status"] == "frozen_reference"
    assert not any("unknown_metadata" in p for p in contract["paths"])
    assert projection.tools_for_projection_consistency(contract)


@pytest.mark.parametrize(
    "leaf", ["schema_extra_field", "unregistered_observation", "另一个叙事字段"]
)
def test_arbitrary_existing_contract_leaf_requires_exact_reference_and_closes_producer_copies(leaf):
    candidate, observations = case(story="remote mountain observatory")
    observations[0]["hard_violations"][0]["source_refs"] = []
    roots = [
        candidate["chapters"][0]["causal_contract"],
        candidate["chapters"][0]["methodology_contract"],
        candidate["chapters"][0]["scenes"][0]["methodology_contract"],
    ]
    for n, root in enumerate(roots):
        body = root["fanqie_short_v2"]["payoff_contract"]
        body[leaf] = f"Observation {n} differs"
        body["uncited_sibling"] = "Same value does not establish a binding"
    suffix = "/fanqie_short_v2/payoff_contract/" + leaf
    frozen = projection.build_projection_consistency_contract(candidate, observations)
    assert not any(p.endswith(suffix) for p in frozen["paths"])
    exact = "/candidate_payload/chapters/0/causal_contract" + suffix
    observations[0]["hard_violations"][0]["source_refs"] = [exact]
    contract = projection.build_projection_consistency_contract(candidate, observations)
    copies = [p for p in contract["paths"] if p.endswith(suffix)]
    assert len(copies) == 3
    assert contract["exact_reference_bindings"][0]["value"] == exact
    assert contract["exact_reference_bindings"][0]["paths"] == sorted(copies)
    assert not any(p.endswith("/uncited_sibling") for p in contract["paths"])
    assert not any(p.startswith("/chapters/1/") for p in contract["paths"])
    projection.tools_for_projection_consistency(contract)


@pytest.mark.parametrize("value", ["A different inference", 12, 2.5, False])
def test_exact_scalar_in_registered_causal_namespace_preserves_type_and_uncited_children(value):
    candidate, observations = case(producer=False, story="orchard irrigation")
    body = candidate["chapters"][0]["causal_contract"]
    body["schema_extra_field"] = value
    body["uncited_sibling"] = value
    observations[0]["hard_violations"][0]["source_refs"] = [
        "/candidate_payload/chapters/0/causal_contract/schema_extra_field"
    ]
    contract = projection.build_projection_consistency_contract(candidate, observations)
    path = "/chapters/0/causal_contract/schema_extra_field"
    assert path in contract["paths"]
    assert "/chapters/0/causal_contract/uncited_sibling" not in contract["paths"]
    key = next(k for k, p in contract["id_to_path"].items() if p == path)
    assert contract["parameters"]["properties"][key]["type"] == projection._TYPES[type(value)]


@pytest.mark.parametrize(
    "key", ["source_binding", "unknown_metadata", "identity", "participants", "pov", "title"]
)
def test_exact_reference_cannot_unlock_technical_or_identity_contract_children(key):
    candidate, observations = case()
    body = candidate["chapters"][0]["causal_contract"]["fanqie_short_v2"]["payoff_contract"]
    body[key] = {"schema_extra_field": "This must stay frozen"}
    path = (
        "/chapters/0/causal_contract/fanqie_short_v2/payoff_contract/" + key + "/schema_extra_field"
    )
    observations[0]["hard_violations"][0]["source_refs"] = ["/candidate_payload" + path]
    contract = projection.build_projection_consistency_contract(candidate, observations)
    assert path not in contract["paths"]
    assert contract["references"][0]["status"] == "frozen_reference"
    assert not contract["exact_reference_bindings"]


def test_missing_exact_reference_never_guesses_existing_leaf_or_unlocks_unknown_namespace():
    candidate, observations = case()
    candidate["chapters"][0]["unknown_extra"] = {"schema_extra_field": "Keep exact"}
    refs = [
        "/candidate_payload/chapters/0/causal_contract/fanqie_short_v2/payoff_contract/schema_extra_fiel",
        "/candidate_payload/chapters/0/unknown_extra/schema_extra_field",
    ]
    observations[0]["hard_violations"][0]["source_refs"] = refs
    contract = projection.build_projection_consistency_contract(candidate, observations)
    assert contract["invalid_references"][0]["value"] == refs[0]
    assert contract["uncovered_references"][0]["value"] == refs[1]
    assert not any(p.endswith("/schema_extra_field") for p in contract["paths"])
    with pytest.raises(ValueError, match="uncovered_projection"):
        projection.tools_for_projection_consistency(contract)


@pytest.mark.parametrize(
    "fault",
    [
        "candidate_body",
        "identity",
        "scene_count",
        "observations",
        "source_authority",
        "parameters",
        "target_path",
    ],
)
def test_current_candidate_observation_and_contract_tampering_are_rejected(fault):
    candidate, observations = case()
    contract = projection.build_projection_consistency_contract(candidate, observations)
    native = completion(candidate, contract, {"/chapters/0/payoff": "A distinct verified event"})
    if fault == "candidate_body":
        candidate["chapters"][0]["payoff"] += " independently changed"
    elif fault == "identity":
        candidate["chapters"][0]["scenes"][0]["participants"].append("Foreign identity")
    elif fault == "scene_count":
        candidate["chapters"][0]["scenes"].append(deepcopy(candidate["chapters"][0]["scenes"][0]))
    elif fault == "observations":
        contract["observations"][0]["draft_eligible"] = True
    elif fault == "source_authority":
        contract["source_authority"] = {"new_authority": "unverified"}
    elif fault == "parameters":
        contract["parameters"]["additionalProperties"] = True
    else:
        contract["id_to_path"]["f0001"] = "/chapters/0/title"
    before = deepcopy(candidate)
    with pytest.raises(ValueError):
        projection.parse_projection_consistency(native, candidate=candidate, contract=contract)
    assert candidate == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "extra",
        "object",
        "array",
        "bool_int",
        "unchanged",
        "whitespace_only",
        "punctuation_only",
        "duplicate",
        "nonfinite",
        "fenced",
        "text_fallback",
        "two_calls",
        "length",
        "missing_raw",
        "raw_conflict",
        "legacy_function",
    ],
)
def test_native_scalar_submission_is_strict_and_never_reads_reasoning(fault):
    candidate, observations = case()
    contract = projection.build_projection_consistency_contract(candidate, observations)
    native = completion(candidate, contract, {"/chapters/0/payoff": "A distinct observed result"})
    args = json.loads(native["tool_calls"][0]["function"]["arguments"])
    key = next(k for k, p in contract["id_to_path"].items() if p == "/chapters/0/payoff")
    if fault == "missing":
        args.pop(key)
    elif fault == "extra":
        args["title"] = "Unauthorized rename"
    elif fault in {"object", "array"}:
        args[key] = {} if fault == "object" else []
    elif fault == "bool_int":
        number = next(
            k
            for k, p in contract["id_to_path"].items()
            if p == "/chapters/0/causal_contract/cost_or_tradeoff"
        )
        args[number] = True
    elif fault in {"unchanged", "whitespace_only", "punctuation_only"}:
        native = completion(
            candidate,
            contract,
            {
                "/chapters/0/payoff": candidate["chapters"][0]["payoff"]
                + ("  " if fault == "whitespace_only" else "!")
            }
            if fault != "unchanged"
            else {},
        )
        args = json.loads(native["tool_calls"][0]["function"]["arguments"])
    elif fault == "text_fallback":
        native["content"], native["tool_calls"] = json.dumps(args), []
    elif fault == "two_calls":
        native["tool_calls"] *= 2
    elif fault == "length":
        native["finish_reason"] = "length"
    elif fault == "missing_raw":
        native["raw_message"] = None
    elif fault == "raw_conflict":
        native["raw_message"]["tool_calls"][0]["function"]["arguments"] = "{}"
    elif fault == "legacy_function":
        native["function_call"] = {"name": projection.TOOL, "arguments": json.dumps(args)}
    text = json.dumps(args)
    if fault == "duplicate":
        text = text[:-1] + ',"' + key + '":"duplicate"}'
    elif fault == "nonfinite":
        text = text[:-1] + ',"unknown":NaN}'
    elif fault == "fenced":
        text = "```json\n" + text + "\n```"
    if native["tool_calls"]:
        native["tool_calls"][0]["function"]["arguments"] = text
    if native.get("raw_message") and fault != "raw_conflict":
        native["raw_message"]["tool_calls"] = deepcopy(native["tool_calls"])
    before = deepcopy(candidate)
    with pytest.raises(ValueError):
        projection.parse_projection_consistency(native, candidate=candidate, contract=contract)
    assert candidate == before


def test_float_to_integer_substitution_is_not_a_same_typed_scalar():
    candidate, observations = case(producer=False)
    candidate["chapters"][0]["causal_contract"]["cost_or_tradeoff"] = 2.0
    contract = projection.build_projection_consistency_contract(candidate, observations)
    native = completion(candidate, contract, {"/chapters/0/causal_contract/cost_or_tradeoff": 3})
    with pytest.raises(ValueError, match="scalar_type_changed"):
        projection.parse_projection_consistency(native, candidate=candidate, contract=contract)


def test_real_completion_model_is_read_only_native_evidence_not_an_authority_receipt():
    from bestseller.services.llm import LLMCompletionResult

    candidate, observations = case(producer=False)
    contract = projection.build_projection_consistency_contract(candidate, observations)
    native = completion(candidate, contract, {"/chapters/0/payoff": "A new observed outcome"})
    native["model_name"] = "offline-fixture-no-provider"
    result = LLMCompletionResult.model_validate(native)
    before = result.model_dump(mode="json")
    _, receipt = projection.parse_projection_consistency(
        result, candidate=candidate, contract=contract
    )
    assert receipt["native_completion"] == before == result.model_dump(mode="json")
    assert receipt["before_range_body_sha256"] == projection._hash(candidate["chapters"])
    assert receipt["observations_sha256"] == projection._hash(observations)
    assert receipt["raw_arguments"] == native["tool_calls"][0]["function"]["arguments"]
    assert not receipt["grade_asserted"] and not receipt["physical_custody_verified"]
