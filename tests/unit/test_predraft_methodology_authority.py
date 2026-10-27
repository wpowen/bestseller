from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from bestseller.services import chapter_generation_input_builder as builder
from bestseller.services.chapter_predraft_quality_gate import evaluate_chapter_predraft_quality

pytestmark = pytest.mark.unit


def inputs(mode=None, *, raw=None):
    metadata = {"source": "user", "methodology_contract_mode": "strict"}
    if mode is not None:
        metadata["creation_intent_contract"] = {
            "field_sources": {"hook_spec": "explicit"},
            "hook_spec": {"require_methodology_overlay": True, "methodology_contract_mode": mode},
        }
    project = SimpleNamespace(
        id="independent-project",
        slug="journey",
        title="Quiet journey",
        genre="travel",
        language="en",
        metadata_json=metadata,
    )
    chapter = SimpleNamespace(
        chapter_number=1,
        chapter_goal="Neri delivers a sealed letter.",
        metadata_json={
            "methodology_contract": {
                "closing_hook": "AI_CHAPTER_METHOD",
                "custom_literal": [],
            },
            "author_literal_contract": {"preserve": False, "empty": []},
        },
    )
    scene = SimpleNamespace(
        scene_number=1,
        scene_type="transition",
        time_label="Day 2, noon",
        participants=["Neri"],
        purpose={"story": "Deliver the letter."},
        entry_state={"parcel": "sealed"},
        exit_state={"parcel": "delivered"},
        metadata_json={"methodology_contract": deepcopy(raw or {})},
    )
    context = SimpleNamespace(
        story_bible={"rules": ["Sealed letters cannot be read."]},
        previous_scene_summaries=[],
        recent_timeline_events=[],
        canon_guardrails_block="KEEP_CANON",
        identity_constraint_block="KEEP_IDENTITY",
        chapter_length_block="KEEP_LENGTH",
    )
    return project, chapter, scene, context


def build(values, target=1900):
    project, chapter, scene, context = values
    return builder.build_chapter_generation_input_bundle(
        project=project,
        chapter=chapter,
        scenes=[scene],
        context_packet=context,
        target_word_count=target,
    )


@pytest.mark.parametrize("genre", ["travel", "science fiction", "historical mystery"])
@pytest.mark.parametrize("mode", [None, "off", "warn"])
def test_optional_methodology_does_not_enter_required_keys_or_block(mode, genre):
    values = inputs(mode)
    values[0].genre = genre
    before = deepcopy([value.__dict__ for value in values])
    bundle = build(values)
    assert "scenes.methodology_contract" not in bundle.required_context_keys
    assert "scenes.methodology_contract" not in bundle.missing_context_keys
    assert bundle.ready and bundle.coverage == 1.0
    report = evaluate_chapter_predraft_quality(bundle)
    assert report.passed and not report.blocked
    diagnostics = [finding for finding in report.findings if "METHODOLOGY" in finding.code]
    assert bool(diagnostics) is (mode == "warn")
    assert all(finding.severity == "minor" for finding in diagnostics)
    assert [value.__dict__ for value in values] == before


@pytest.mark.parametrize("mode", [None, "off", "warn"])
def test_unselected_raw_contract_stays_only_in_ai_proposals(mode):
    raw = {
        "signature_image": "AI_SCENE_IMAGE",
        "cut_point": "AI_SCENE_CUT",
        "gate_function": "AI_SCENE_DUTY",
        "visible_progress": "AI_METHOD_PROGRESS",
        "relationship_debts": [],
        "custom_literal": {"false": False, "empty": []},
    }
    values = inputs(mode, raw=raw)
    before = deepcopy([value.__dict__ for value in values])
    bundle = build(values)
    scene = bundle.scenes[0]
    assert scene["methodology_contract"] == {}
    assert scene["signature_image"] is None and scene["cut_point"] is None
    assert scene["gate_function"] is None
    assert scene["visible_progress"] == values[2].purpose["story"]
    assert "AI_CHAPTER_METHOD" not in json.dumps(bundle.chapter_contract)
    assert "AI_CHAPTER_METHOD" not in json.dumps(bundle.acceptance_contract["planning_intent"])
    channels = bundle.acceptance_contract["source_authority_channels"]
    assert channels["ai_proposals"]["scene_methodology_contracts"][0]["methodology_contract"] == raw
    assert channels["ai_proposals"]["chapter_contract"]["closing_hook"] == "AI_CHAPTER_METHOD"
    assert bundle.acceptance_contract["must_deliver"] == []
    assert bundle.chapter["metadata"]["author_literal_contract"] == {"preserve": False, "empty": []}
    assert bundle.methodology_blocks == {
        "canon_guardrails_block": "KEEP_CANON",
        "identity_constraint_block": "KEEP_IDENTITY",
        "chapter_length_block": "KEEP_LENGTH",
    }
    assert [value.__dict__ for value in values] == before


def test_authorized_strict_still_requires_methodology_even_if_missing_stamp_is_tampered():
    bundle = build(inputs("strict"))
    assert "scenes.methodology_contract" in bundle.required_context_keys
    assert "scenes.methodology_contract" in bundle.missing_context_keys
    assert not bundle.ready
    for candidate in (bundle, bundle.model_copy(update={"missing_context_keys": ()})):
        report = evaluate_chapter_predraft_quality(candidate)
        assert report.blocked
        assert any(
            finding.code == "PREDRAFT_METHODOLOGY_CONTRACT_MISSING"
            for finding in report.blocking_issues
        )


def test_selected_strict_contract_and_real_explicit_sources_are_preserved():
    raw = {"signature_image": "AUTHOR_IMAGE", "cut_point": "AUTHOR_CUT", "relationship_debts": []}
    values = inputs("strict", raw=raw)
    before = deepcopy([value.__dict__ for value in values])
    bundle = build(values)
    assert bundle.ready
    assert bundle.scenes[0]["methodology_contract"] == raw
    assert bundle.scenes[0]["signature_image"] == "AUTHOR_IMAGE"
    creation = bundle.acceptance_contract["source_authority_channels"][
        "explicit_creator_requirements"
    ]["creation_intent_contract"]
    assert creation["field_sources"] == {"hook_spec": "explicit"}
    assert creation["hook_spec"] == values[0].metadata_json["creation_intent_contract"]["hook_spec"]
    assert not evaluate_chapter_predraft_quality(bundle).blocked
    assert [value.__dict__ for value in values] == before


@pytest.mark.parametrize("source", [None, "user", "default", "derived", "legacy"])
def test_forged_or_unknown_canonical_source_cannot_grant_strict(source):
    values = inputs("strict")
    values[0].metadata_json["creation_intent_contract"]["field_sources"]["hook_spec"] = source
    bundle = build(values)
    assert bundle.ready
    assert "scenes.methodology_contract" not in bundle.required_context_keys
    assert not evaluate_chapter_predraft_quality(bundle).blocked


@pytest.mark.parametrize(
    "missing,key",
    [
        ("purpose", "scenes.purpose"),
        ("entry_state", "scenes.entry_exit"),
        ("exit_state", "scenes.entry_exit"),
        ("bible", "story_bible"),
    ],
)
def test_real_scene_and_bible_requirements_remain_when_methodology_is_off(missing, key):
    values = inputs()
    if missing == "bible":
        values[3].story_bible = {}
    else:
        setattr(values[2], missing, {})
    bundle = build(values)
    assert key in bundle.required_context_keys and key in bundle.missing_context_keys
    assert not bundle.ready


def test_word_count_and_adopted_identity_assertions_remain_hard():
    values = inputs()
    bad_target = build(values, target=0)
    assert any(
        f.code == "PREDRAFT_TARGET_WORD_COUNT_INVALID"
        for f in evaluate_chapter_predraft_quality(bad_target).blocking_issues
    )
    values[3].recent_timeline_events = [
        {
            "adopted_source": {
                "schema": "adopted-knowledge.v1",
                "is_usable": True,
                "project_id": "independent-project",
                "chapter_id": "prior-chapter",
                "source_chapter_draft_version_id": "adopted-draft",
                "source_body_sha256": "a" * 64,
                "adoption_state": "promoted",
            },
            "input_assertions": [
                {
                    "path": "scenes.0.participants",
                    "operator": "contains",
                    "expected": "Established identity",
                }
            ],
        }
    ]
    assert any(
        f.code == "PREDRAFT_ADOPTED_FACT_CONFLICT"
        for f in evaluate_chapter_predraft_quality(build(values)).blocking_issues
    )


@pytest.mark.parametrize(
    "family",
    [
        "scene_contract_materialized_",
        "chapter_contract_materialized_",
        "chapter_scene_contract_materialized_",
    ],
)
def test_runtime_cache_families_do_not_enter_effective_input_or_change_it(family):
    values = inputs(raw={"signature_image": "KEEP_LITERAL"})
    values[1].metadata_json["unknown_contract"] = {"runtime": "A literal author word", "empty": []}
    baseline = build(values).model_dump(mode="json")
    runtime = {
        family + "projection": {"methodology_contract": {"stakes": "DERIVED_CACHE_ONLY"}},
        family + "bindings": {"field": {"value": "CACHE_ONLY"}},
        family + "history": ["HISTORY_ONLY"],
        family + "at": "TIMESTAMP_ONLY",
        family + "contract": {"source": "CACHE_ONLY"},
    }
    values[1].metadata_json.update(deepcopy(runtime))
    values[2].metadata_json.update(deepcopy(runtime))
    values[2].metadata_json["methodology_contract"].update(deepcopy(runtime))
    before = deepcopy([value.__dict__ for value in values])
    assert build(values).model_dump(mode="json") == baseline
    filtered = builder._scene_metadata_payload(values[2])
    assert all(not key.startswith(family) for key in filtered)
    assert [value.__dict__ for value in values] == before
