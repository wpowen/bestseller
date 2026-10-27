"""Authored participation and explicit Cast names outrank positional recipes."""

import copy
from types import SimpleNamespace

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services import planner
from bestseller.services.commercial_planning_readiness import evaluate_commercial_planning_readiness
from bestseller.services.narrative_contracts import build_identity_manifest, validate_chapter_plan_contract
from bestseller.services.outline_field_enrichment import enrich_outline_batch_fields

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("lead,offscreen,role,story", [
    ("禾宁", "陆舟", "无名值守人", "禾宁独自读到纸条上的陆舟二字。"),
    ("Mara", "Ivo", "Night attendant", "Mara reads a note naming Ivo, who is absent."),
    ("Asha", "Tarin", "Station custodian", "Asha recalls Tarin while inspecting a relay."),
])
@pytest.mark.parametrize("number", [1, 2, 9])
def test_mentions_and_position_never_add_participants_or_emotions(lead, offscreen, role, story, number):
    chapter = {
        "chapter_number": number, "title": "The sealed note",
        "chapter_goal": story, "goal": story, "main_conflict": story,
        "hook_description": story,
        "scenes": [{"scene_number": 1, "participants": [lead], "purpose": {"story": story}},
                   {"scene_number": 2, "purpose": {"story": story}}],
    }
    enriched, stats = enrich_outline_batch_fields(
        {"chapters": [copy.deepcopy(chapter)]}, [lead, offscreen, role], protagonist=lead,
    )
    assert enriched["chapters"][0]["scenes"][0]["participants"] == [lead]
    assert "participants" not in enriched["chapters"][0]["scenes"][1]
    assert "target_emotion" not in enriched["chapters"][0]
    assert "hype_type" not in enriched["chapters"][0]
    assert "hype_intensity" not in enriched["chapters"][0]
    assert all(stats[key] == 0 for key in ("participants", "golden_solo_rescued", "golden_hype", "target_emotion"))
    batch = ChapterOutlineBatchInput.model_validate({"chapters": [chapter]})
    manifest = [{"name": lead, "role": "protagonist"}, {"name": offscreen}, {"name": role}]
    planner._enrich_generated_volume_outline_systemic_fields(batch, identity_manifest=manifest)
    planner._repair_generated_volume_outline_contract_inputs(batch, identity_manifest=manifest)
    assert planner._rescue_golden_three_solo_scenes(batch, identity_manifest=manifest) == 0
    assert [scene.participants for scene in batch.chapters[0].scenes] == [[lead], []]


def test_explicit_participants_and_low_hype_are_preserved_without_roster_truncation():
    participants = ["Mara", "The watchkeeper", "B", "C", "D", "E"]
    chapter = {"chapter_number": 1, "target_emotion": "warmth", "hype_type": "quiet relief",
               "hype_intensity": 0.2, "scenes": [{"scene_number": 1, "participants": participants}]}
    before = copy.deepcopy(chapter)
    result, _ = enrich_outline_batch_fields({"chapters": [chapter]}, ["Absent rival"])
    assert result["chapters"][0] == before


def test_named_social_ties_and_conflict_endpoints_are_registered_without_invented_locks():
    cast = {
        "protagonist": {"name": "Mara", "role": "protagonist", "gender": "female",
                        "aliases": ["Navigator"], "social_network": {
                            "superiors": [{"name": "The watchkeeper", "bond": "supervisor"}],
                            "family": [{"name": "Navigator"}],
                            "community": ["Harbor guild"], "dependencies": ["A debt to someone"]}},
        "conflict_map": [{"character_a": "Mara", "character_b": "The watchkeeper",
                          "conflict_type": "control of the register"}],
    }
    original = copy.deepcopy(cast)
    manifest = planner._chapter_outline_identity_manifest(cast)
    assert [entry["name"] for entry in manifest] == ["Mara", "The watchkeeper"]
    assert manifest[0]["gender"] == "female"
    assert manifest[1]["gender"] == "unknown"
    assert manifest[1]["pronoun_set_zh"] == manifest[1]["pronoun_set_en"] == ""
    assert manifest[1]["source_paths"] == [
        "cast_spec.protagonist.social_network.superiors[0].name",
        "cast_spec.conflict_map[0].character_b",
    ]
    assert cast == original
    assert build_identity_manifest(cast) == manifest
    batch = ChapterOutlineBatchInput.model_validate({"chapters": [{
        "chapter_number": 1, "title": "The register", "main_conflict": "The register is sealed.",
        "scenes": [{"scene_number": 1, "participants": ["Mara", "The watchkeeper"],
                    "purpose": {"story": "The watchkeeper hands Mara the register."}}],
    }]})
    planner._repair_generated_volume_outline_contract_inputs(batch, identity_manifest=manifest)
    assert batch.chapters[0].scenes[0].participants == ["Mara", "The watchkeeper"]
    report = validate_chapter_plan_contract(batch, identity_manifest=manifest)
    assert "PLAN_SCENE_UNKNOWN_PARTICIPANT" not in {v.code for v in report.violations}


def test_network_prose_and_extra_name_fields_do_not_create_identities():
    cast = {"protagonist": {"name": "Mara", "role": "protagonist", "social_network": {
        "superiors": "Someone described in prose", "community": ["A named guild"],
    }, "background": "Remember Ivo", "unapproved_extra": {"name": "Extra person"}}}
    assert [entry["name"] for entry in build_identity_manifest(cast)] == ["Mara"]


@pytest.mark.parametrize("container", ["protagonist", "antagonist", "supporting_cast"])
@pytest.mark.parametrize("relation", ["family", "mentors", "peers", "superiors", "subordinates", "enemies"])
def test_explicit_relation_names_are_supported_for_every_cast_container(container, relation):
    character = {"name": "Mara", "role": "protagonist", "social_network": {
        relation: {"name": "Registry keeper"},
    }}
    cast = {container: [character] if container == "supporting_cast" else character}
    entry = next(entry for entry in build_identity_manifest(cast) if entry["name"] == "Registry keeper")
    prefix = "cast_spec.supporting_cast[0]" if container == "supporting_cast" else f"cast_spec.{container}"
    assert entry["source_paths"] == [f"{prefix}.social_network.{relation}.name"]


def test_no_hook_type_is_chosen_from_chapter_position_without_matching_source_text():
    batch = ChapterOutlineBatchInput.model_validate({"chapters": [{
        "chapter_number": 1, "title": "Still water", "scenes": [],
    }]})
    planner._enrich_generated_volume_outline_systemic_fields(batch, identity_manifest=[])
    assert batch.chapters[0].hook_type is None


def test_unknown_names_stay_visible_and_blocked_after_both_repair_passes():
    manifest = [{"name": "Mara", "role": "protagonist"}]
    batch = ChapterOutlineBatchInput.model_validate({"chapters": [{
        "chapter_number": 1, "title": "The register", "scenes": [{
            "scene_number": 1, "participants": ["Mara", "Maro"],
            "purpose": {"story": "Mara reads a note naming an absent rival."},
        }],
    }]})
    planner._repair_generated_volume_outline_contract_inputs(batch, identity_manifest=manifest)
    fake_report = SimpleNamespace(blocking_violations=[
        SimpleNamespace(code="PLAN_SCENE_UNKNOWN_PARTICIPANT", metadata={"chapter_number": 1, "scene_number": 1}),
        SimpleNamespace(code="PLAN_SCENE_PURPOSE_CHARACTER_NOT_IN_PARTICIPANTS",
                        metadata={"chapter_number": 1, "scene_number": 1, "character": "Absent rival"}),
    ])
    planner._repair_generated_volume_outline_contract_blocks(batch, fake_report, identity_manifest=manifest)
    assert batch.chapters[0].scenes[0].participants == ["Mara", "Maro"]
    report = validate_chapter_plan_contract(batch, identity_manifest=manifest)
    assert "PLAN_SCENE_UNKNOWN_PARTICIPANT" in {v.code for v in report.violations}


@pytest.mark.parametrize("enforced,severity", [(False, "medium"), (True, "critical")])
def test_solo_pressure_is_advisory_without_an_explicit_applicable_contract(enforced, severity):
    report = evaluate_commercial_planning_readiness(
        [{"chapter_number": 1, "main_conflict": "Quiet solitude.",
          "scenes": [{"scene_number": 1, "participants": ["Mara"]}]}],
        require_package_artifacts=False, enforce_solo_pressure_contract=enforced,
    )
    finding = next(f for f in report.findings if f.code == "golden_three_solo_scene_chain")
    assert finding.severity == severity
    assert finding.evidence["explicit_solo_pressure_contract"] is enforced
