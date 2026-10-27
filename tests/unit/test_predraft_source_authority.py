"""Predraft assertions require explicit author consent or guarded adopted facts."""
import copy
from types import SimpleNamespace

import pytest

from bestseller.domain.chapter_generation_input import ChapterGenerationInputBundle
from bestseller.services.chapter_generation_input_builder import build_chapter_generation_input_bundle
from bestseller.services.chapter_predraft_quality_gate import evaluate_chapter_predraft_quality

pytestmark = pytest.mark.unit


def _bundle(*, contract=None, fact=None, chapter_number=1):
    project = SimpleNamespace(id="project-1", language="en", metadata_json={
        "creation_intent_contract": contract or {}}, title="Independent fixture")
    chapter = SimpleNamespace(chapter_number=chapter_number, chapter_goal="A quiet departure", metadata_json={})
    scene = SimpleNamespace(scene_number=1, scene_type="transition", participants=["Ari"],
        purpose={"story": "Leave"}, entry_state={"identity": {"name": "Ari"}},
        exit_state={"room": "outside"}, metadata_json={})
    context = SimpleNamespace(story_bible={"rules": ["An approved boundary"]},
        previous_scene_summaries=[{"summary": "A prior candidate, not Canon"}],
        recent_timeline_events=[fact] if fact else [],
        reader_contract_block="AI_MUST_OPEN_WITH_PRESSURE")
    return build_chapter_generation_input_bundle(project=project, chapter=chapter, scenes=[scene],
        context_packet=context, target_word_count=1800)


def _clause(**updates):
    return {"source": "user", "hard": True, "path": "scenes.0.participants", "operator": "contains",
        "expected": "Author-chosen witness", **updates}


def _fact():
    return {"event_name": "Identity confirmed", "knowledge_lineage_status": "current",
        "adopted_source": {"schema": "adopted-knowledge.v1", "is_usable": True,
            "project_id": "project-1", "chapter_id": "previous-chapter",
            "source_chapter_draft_version_id": "accepted-draft", "source_body_sha256": "a" * 64,
            "adoption_state": "promoted"},
        "input_assertions": [{"path": "scenes.0.entry_state.identity.name", "operator": "equals",
                              "expected": "Established identity"}]}


def test_source_channels_v2_missing_recipes_do_not_block_or_fill_them():
    bundle = _bundle()
    before = copy.deepcopy(bundle.model_dump())
    report = evaluate_chapter_predraft_quality(bundle)
    assert not report.blocked and report.passed
    assert bundle.schema_version == "chapter-generation-input.v2"
    assert bundle.acceptance_contract["schema_version"] == "chapter-acceptance-contract.v2"
    assert bundle.scenes[0]["gate_function"] is None
    assert bundle.acceptance_contract["must_deliver"] == []
    assert not any(f.path == "scenes.methodology_contract" for f in report.findings)
    assert bundle.model_dump() == before


@pytest.mark.parametrize("source,blocked", [
    ("explicit", True), ("user_explicit", False), ("default", False), ("derived", False),
])
def test_existing_creation_language_field_only_binds_with_explicit_source(source, blocked):
    bundle = _bundle(contract={"language": "zh-CN", "field_sources": {"language": source}})
    report = evaluate_chapter_predraft_quality(bundle)
    assert report.blocked is blocked
    if blocked:
        assert report.blocking_issues[0].path == "project.language"


@pytest.mark.parametrize("chapter_number,blocked", [(1, True), (2, False)])
def test_only_applicable_explicit_hard_clause_blocks(chapter_number, blocked):
    bundle = _bundle(contract={"field_sources": {"hook_spec": "explicit"},
        "hook_spec": {"hard_constraints": [_clause(scope={"chapter_numbers": [1]})]}},
        chapter_number=chapter_number)
    report = evaluate_chapter_predraft_quality(bundle)
    assert report.blocked is blocked
    if blocked:
        assert report.blocking_issues[0].code == "PREDRAFT_EXPLICIT_REQUIREMENT_CONFLICT"


def test_ai_proposal_cannot_authorize_same_hard_clause():
    bundle = _bundle()
    acceptance = copy.deepcopy(bundle.acceptance_contract)
    acceptance["source_authority_channels"]["ai_proposals"]["hard_constraints"] = [_clause()]
    acceptance["must_deliver"] = [{"label": "closing_hook", "value": "Invent a new danger"}]
    acceptance["front_position_rules"] = {"opening_must_start_with_pressure": True}
    report = evaluate_chapter_predraft_quality(bundle.model_copy(update={"acceptance_contract": acceptance}))
    assert not report.blocked


@pytest.mark.parametrize("clause", [_clause(source="derived"), _clause(hard=False),
    _clause(operator="guess_from_prose"), _clause(path="missing.prose"),
    _clause(scope={"chapter_numbers": "first"})])
def test_unapproved_unsupported_and_unavailable_checks_never_invent_a_violation(clause):
    report = evaluate_chapter_predraft_quality(_bundle(contract={
        "field_sources": {"hook_spec": "explicit"}, "hook_spec": {"hard_constraints": [clause]}}))
    assert not report.blocked


def test_upstream_guarded_adopted_identity_assertion_stays_hard_and_original_is_retained():
    fact = _fact()
    before = copy.deepcopy(fact)
    bundle = _bundle(fact=fact)
    report = evaluate_chapter_predraft_quality(bundle)
    assert report.blocked
    assert report.blocking_issues[0].code == "PREDRAFT_ADOPTED_FACT_CONFLICT"
    channels = bundle.acceptance_contract["source_authority_channels"]
    assert channels["established_facts"]["recent_timeline_events"] == [before]
    assert fact == before


@pytest.mark.parametrize("fault", ["no_guard", "wrong_project", "candidate", "superseded", "missing_hash"])
def test_unguarded_timeline_is_reference_and_cannot_become_a_hard_fact(fault):
    fact = _fact()
    if fault == "no_guard":
        fact["adopted_source"].pop("is_usable")
    elif fault == "wrong_project":
        fact["adopted_source"]["project_id"] = "another-project"
    elif fault == "candidate":
        fact["adopted_source"]["adoption_state"] = "candidate"
    elif fault == "superseded":
        fact["knowledge_lineage_status"] = "superseded"
    else:
        fact["adopted_source"].pop("source_body_sha256")
    bundle = _bundle(fact=fact)
    channels = bundle.acceptance_contract["source_authority_channels"]
    assert channels["established_facts"]["recent_timeline_events"] == []
    assert channels["prior_prose_reference"]["recent_timeline_events"] == [fact]
    assert channels["prior_prose_reference"]["previous_scene_summaries"]
    assert not evaluate_chapter_predraft_quality(bundle).blocked


def test_legacy_v1_is_readable_and_never_relabelled_as_v2():
    old = ChapterGenerationInputBundle(chapter={"chapter_number": 1, "goal": "An existing goal"},
        quality_targets={"target_word_count": 1800},
        acceptance_contract={"schema_version": "chapter-acceptance-contract.v1", "must_deliver": []})
    before = old.model_dump()
    report = evaluate_chapter_predraft_quality(old)
    assert not report.blocked
    assert any(f.code == "PREDRAFT_SOURCE_AUTHORITY_UNKNOWN" for f in report.findings)
    assert old.model_dump() == before and old.schema_version.endswith("v1")


def test_completely_empty_bundle_is_blocked_by_structure_not_missing_recipe():
    report = evaluate_chapter_predraft_quality(ChapterGenerationInputBundle())
    assert report.blocked and not report.passed
    assert {f.code for f in report.blocking_issues} == {
        "PREDRAFT_CHAPTER_NUMBER_INVALID", "PREDRAFT_TARGET_WORD_COUNT_INVALID", "PREDRAFT_WRITABLE_PLAN_MISSING"}


@pytest.mark.parametrize("field,value,code", [
    ("chapter_number", 0, "PREDRAFT_CHAPTER_NUMBER_INVALID"),
    ("chapter_number", True, "PREDRAFT_CHAPTER_NUMBER_INVALID"),
    ("chapter_number", "1", "PREDRAFT_CHAPTER_NUMBER_INVALID"),
    ("target_word_count", 0, "PREDRAFT_TARGET_WORD_COUNT_INVALID"),
    ("target_word_count", None, "PREDRAFT_TARGET_WORD_COUNT_INVALID"),
    ("target_word_count", True, "PREDRAFT_TARGET_WORD_COUNT_INVALID"),
    ("target_word_count", "1800", "PREDRAFT_TARGET_WORD_COUNT_INVALID"),
])
def test_invalid_typed_task_inputs_still_block(field, value, code):
    bundle = _bundle()
    if field == "chapter_number":
        bundle = bundle.model_copy(update={"chapter": {**bundle.chapter, field: value}})
    else:
        bundle = bundle.model_copy(update={"quality_targets": {**bundle.quality_targets, field: value}})
    assert code in {f.code for f in evaluate_chapter_predraft_quality(bundle).blocking_issues}


@pytest.mark.parametrize("requires_scenes", [False, True])
def test_scenes_required_only_when_current_task_declares_them(requires_scenes):
    bundle = _bundle().model_copy(update={"scenes": (),
        "required_context_keys": ("scenes",) if requires_scenes else ()})
    report = evaluate_chapter_predraft_quality(bundle)
    assert report.blocked is requires_scenes


def test_no_goal_with_real_scene_plan_is_writable_without_a_recipe():
    bundle = _bundle().model_copy(update={"chapter": {"chapter_number": 1}, "chapter_contract": {},
        "acceptance_contract": {"schema_version": "chapter-acceptance-contract.v2"}})
    assert not evaluate_chapter_predraft_quality(bundle).blocked


def test_authority_labels_are_not_a_substitute_for_a_writable_goal():
    bundle = _bundle().model_copy(update={"chapter": {"chapter_number": 1}, "scenes": (),
        "required_context_keys": (), "chapter_contract": {"authority": "planning_intent_not_hard"},
        "acceptance_contract": {"schema_version": "chapter-acceptance-contract.v2"}})
    assert "PREDRAFT_WRITABLE_PLAN_MISSING" in {
        f.code for f in evaluate_chapter_predraft_quality(bundle).blocking_issues}
