"""Optional planning method activation never derives authority from story data."""

from copy import deepcopy
import hashlib
import itertools
import json
from types import SimpleNamespace

import pytest

from bestseller.services.planning_capability_selection import (
    CAPABILITIES, CONTRACT_SCHEMA, PlanningCapabilitySelectionError,
    planning_capability_selected, resolve_planning_capability,
)

pytestmark = pytest.mark.unit


def authored(values=None, *, source="explicit"):
    return {"creation_intent_contract": {
        "field_sources": {"hook_spec": source},
        "hook_spec": {"planning_capabilities": {
            "schema": CONTRACT_SCHEMA,
            "selected": dict(zip(CAPABILITIES, values or (False,) * 4, strict=True)),
        }},
    }}


@pytest.mark.parametrize("values", list(itertools.product((False, True), repeat=4)))
def test_all_four_choices_are_independent_and_match_project_and_metadata_apis(values):
    metadata = authored(values)
    project = SimpleNamespace(metadata_json=metadata, genre="自定义类型")
    before = deepcopy(metadata)
    for name, expected in zip(CAPABILITIES, values, strict=True):
        receipt = resolve_planning_capability(project, name)
        assert receipt == resolve_planning_capability(metadata, name)
        assert receipt["selected"] is expected
        assert receipt["status"] == ("selected" if expected else "not_applicable")
        assert receipt["available"] is True
        assert planning_capability_selected(project, name) is expected
        assert len(receipt["source_contract_sha256"]) == 64
    assert metadata == before


@pytest.mark.parametrize("source", [None, "", "default", "derived", "legacy", "user", "Explicit", True])
@pytest.mark.parametrize("capability", CAPABILITIES)
def test_unknown_provenance_cannot_activate_even_complete_selected_contract(source, capability):
    metadata = authored((True,) * 4, source=source)
    before = deepcopy(metadata)
    receipt = resolve_planning_capability(metadata, capability, available=False)
    assert receipt["status"] == "not_applicable" and receipt["selected"] is False
    assert receipt["reason"] == "author_selection_provenance_unknown"
    assert metadata == before


@pytest.fixture
def hype_with_unknown_history():
    return {
        "genre": "玄幻", "genre_intent_contract": {"tags": ["爽文"]},
        "enable_story_design_kernel": True, "enable_emotion_driven_kernel": True,
        "story_design_kernel": {"source": "user", "approved": True, "plot_tree": ["old planning"]},
        "emotion_driven_kernel": {"source": "explicit", "valid": True},
        "public_emotion_kernel": {"source": "author", "audience": "cached"},
        "entry_system_kernel": {"enabled": True, "source": "author"},
        "planning_capabilities": {"schema": CONTRACT_SCHEMA, "selected": dict.fromkeys(CAPABILITIES, True)},
        "planning_capability_selection_receipt": {
            "schema": "planning-capability-selection-receipt.v1", "selected": True,
            "reason": "explicit_author_selection", "source_contract_sha256": "a" * 64,
        },
        "canon": {"fact": "An adopted bridge closure remains in force."},
        "world_model": {"identity": "preserve this factual projection"},
        "compliance_boundary_kernel": {"forbidden_translations": ["preserve actual safety boundaries"]},
        "creation_intent_contract": {
            "field_sources": {"genre_intent": "explicit", "story_enhancers": "explicit", "hook_spec": "explicit"},
            "genre_intent": {"genre_key": "xuanhuan", "tag_source": "user", "user_tags": ["爽文"]},
            "story_enhancers": {"effect_skills": ["hype_satisfaction_engine"]},
            "hook_spec": {"require_methodology_overlay": True,
                          "ideology_contract": {"version": "ideology-author-contract.v1", "fields": {}}},
        },
    }


def test_hype_and_unknown_old_top_metadata_never_authorize_whole_kernels(hype_with_unknown_history):
    metadata = hype_with_unknown_history
    before = deepcopy(metadata)
    for capability in CAPABILITIES:
        assert resolve_planning_capability(metadata, capability)["status"] == "not_applicable"
        assert planning_capability_selected(metadata, capability) is False
    assert metadata == before


@pytest.mark.parametrize("metadata", [None, {}, {"creation_intent_contract": None},
    {"creation_intent_contract": {"hook_spec": {"planning_capabilities": True}}},
    {"creation_intent_contract": {"field_sources": {}, "hook_spec": {"planning_capabilities": False}}},
    {"creation_intent_contract": {"field_sources": {"hook_spec": "explicit"}}},
])
def test_missing_selection_is_not_applicable_without_default_sources_or_history_mutation(metadata):
    before = deepcopy(metadata)
    for capability in CAPABILITIES:
        assert not planning_capability_selected(metadata, capability)
    assert metadata == before


@pytest.mark.parametrize("bad", [None, False, True, "enabled", [], {},
    dict.fromkeys(CAPABILITIES, True),
    {"schema": "planning-capabilities.v0", "selected": dict.fromkeys(CAPABILITIES, True)},
    {"schema": CONTRACT_SCHEMA, "selected": dict.fromkeys(CAPABILITIES, True), "source": "author"},
    {"schema": CONTRACT_SCHEMA},
    {"schema": CONTRACT_SCHEMA, "selected": []},
    {"schema": CONTRACT_SCHEMA, "selected": {"story_design_kernel_typo": True}},
    {"schema": CONTRACT_SCHEMA, "selected": {**dict.fromkeys(CAPABILITIES, True), "compliance_boundary_kernel": False}},
])
def test_present_explicit_malformed_contract_is_not_an_implicit_opt_out(bad):
    metadata = authored()
    metadata["creation_intent_contract"]["hook_spec"]["planning_capabilities"] = bad
    before = deepcopy(metadata)
    for capability in CAPABILITIES:
        with pytest.raises(PlanningCapabilitySelectionError):
            resolve_planning_capability(metadata, capability)
    assert metadata == before


@pytest.mark.parametrize("bad", [None, "true", "false", 0, 1, 0.0, [], {}, [True]])
def test_nonbool_in_any_of_four_fields_invalidates_the_contract(bad):
    metadata = authored()
    metadata["creation_intent_contract"]["hook_spec"]["planning_capabilities"]["selected"]["public_emotion_kernel"] = bad
    # Do not validate only the requested field and miss a malformed sibling.
    with pytest.raises(PlanningCapabilitySelectionError, match="selected_value_invalid"):
        planning_capability_selected(metadata, "story_design_kernel")


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_explicit_true_requires_availability_while_explicit_false_does_not(capability):
    assert resolve_planning_capability(authored(), capability, available=False)["reason"] == "explicitly_unselected"
    with pytest.raises(PlanningCapabilitySelectionError, match="selected_but_unavailable"):
        planning_capability_selected(authored((True,) * 4), capability, available=False)


@pytest.mark.parametrize("bad", [None, 0, 1, "true", [], {}])
def test_availability_is_a_real_boolean_not_a_truthy_override(bad):
    with pytest.raises(PlanningCapabilitySelectionError, match="availability_invalid"):
        resolve_planning_capability(authored(), CAPABILITIES[0], available=bad)


@pytest.mark.parametrize("name", ["story_engine_v2", "compliance_boundary_kernel", "canon", "world_model", "hype_satisfaction_engine", "unknown", "", None])
def test_unknown_or_independent_fact_safety_capability_is_not_silently_disabled(name):
    with pytest.raises(PlanningCapabilitySelectionError, match="capability_unknown"):
        resolve_planning_capability({}, name)


def test_source_digest_binds_only_actual_selection_and_provenance_not_runtime_or_other_story_inputs():
    metadata = authored((True, False, False, False))
    receipt = resolve_planning_capability(metadata, CAPABILITIES[0])
    selection = metadata["creation_intent_contract"]["hook_spec"]["planning_capabilities"]
    raw = {"hook_spec_source": "explicit", "planning_capabilities_present": True,
           "planning_capabilities": selection}
    expected = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert receipt["source_contract_sha256"] == expected
    metadata.update(chapter_critic_cycles={"attempts": 2}, updated_at="later",
                    story_design_kernel={"source": "generated", "different": True})
    metadata["creation_intent_contract"]["hook_spec"]["unrelated_author_fact"] = "The river was closed."
    metadata["creation_intent_contract"]["creative_direction"] = "A quiet family story."
    assert resolve_planning_capability(metadata, CAPABILITIES[0]) == receipt
    selection["selected"]["public_emotion_kernel"] = True
    changed = resolve_planning_capability(metadata, CAPABILITIES[0])
    assert changed["source_contract_sha256"] != expected and changed["selected"] is True
    metadata["creation_intent_contract"]["field_sources"]["hook_spec"] = "derived"
    unknown = resolve_planning_capability(metadata, CAPABILITIES[0])
    assert unknown["source_contract_sha256"] != changed["source_contract_sha256"]
    assert unknown["selected"] is False


def test_unknown_malformed_history_is_preserved_without_interpretation():
    metadata = authored(source="legacy")
    metadata["creation_intent_contract"]["hook_spec"]["planning_capabilities"] = {"bad_old_schema": [1, "yes"]}
    original = deepcopy(metadata)
    assert resolve_planning_capability(metadata, CAPABILITIES[0])["status"] == "not_applicable"
    assert metadata == original


@pytest.mark.parametrize("hook", [None, [], "author supplied malformed hook"])
def test_explicit_malformed_hook_container_raises(hook):
    metadata = authored()
    metadata["creation_intent_contract"]["hook_spec"] = hook
    with pytest.raises(PlanningCapabilitySelectionError, match="explicit_hook_invalid"):
        resolve_planning_capability(metadata, CAPABILITIES[0])


def test_returned_receipts_do_not_alias_source_or_authorize_future_calls():
    metadata = authored((True, False, False, False))
    original = deepcopy(metadata)
    receipt = resolve_planning_capability(metadata, CAPABILITIES[0])
    receipt.update(selected=False, source_contract_sha256="forged")
    assert planning_capability_selected(metadata, CAPABILITIES[0]) is True
    assert metadata == original


@pytest.mark.parametrize("selected", [{}, {"story_design_kernel": True}, {"story_design_kernel": False}])
def test_explicit_subset_or_empty_selection_never_fills_or_activates_omitted_capabilities(selected):
    metadata = authored()
    metadata["creation_intent_contract"]["hook_spec"]["planning_capabilities"]["selected"] = deepcopy(selected)
    before = deepcopy(metadata)
    for capability in CAPABILITIES:
        receipt = resolve_planning_capability(metadata, capability)
        assert receipt["selected"] is selected.get(capability, False)
        if capability not in selected:
            assert receipt["reason"] == "not_explicitly_selected"
            assert receipt["status"] == "not_applicable"
            assert not planning_capability_selected(metadata, capability, available=False)
    assert metadata == before


@pytest.mark.parametrize("value", [None, "true", 1, 0])
def test_partial_selection_still_rejects_nonboolean_value(value):
    metadata = authored()
    metadata["creation_intent_contract"]["hook_spec"]["planning_capabilities"]["selected"] = {"story_design_kernel": value}
    with pytest.raises(PlanningCapabilitySelectionError, match="selected_value_invalid"):
        resolve_planning_capability(metadata, "public_emotion_kernel")
