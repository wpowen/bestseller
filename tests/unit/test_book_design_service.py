from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.book_design import (
    ensure_project_book_design_snapshot,
    planning_snapshot_lineage,
    validate_project_book_design,
)


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        genre="玄幻",
        target_chapters=50,
        target_word_count=130_000,
        metadata_json={
            "genre_intent_contract": {
                "genre_key": "xuanhuan",
                "genre_label": "玄幻",
                "channel_key": "male",
                "tone_preference": "light",
            },
            "story_spine": {"who": "陆沉，边村少年", "question": "他能活下来吗？"},
            "identity_manifest": [{"name": "陆沉", "role": "protagonist"}],
            "writing_profile": {"style": {"tone_keywords": ["轻松", "幽默"]}},
        },
    )


def test_project_snapshot_uses_approved_story_spine_identity_and_budget() -> None:
    project = _project()
    snapshot = ensure_project_book_design_snapshot(project)
    assert snapshot.protagonist.name == "陆沉"
    assert snapshot.word_budget.total_words == 130_000
    assert project.metadata_json["book_design_snapshot_hash"] == snapshot.source_hash


def test_project_snapshot_preserves_full_frontend_creation_contract() -> None:
    project = _project()
    project.metadata_json["creation_intent_contract"] = {
        "schema_version": "creation-intent.v1",
        "genre_intent": dict(project.metadata_json["genre_intent_contract"]),
        "audience_orientation": "male",
        "narrative_scale": "serial",
        "tone_preference": "light",
        "chapter_count": 50,
        "length_key": "long",
        "pov": "first_person",
        "draft_mode": False,
        "stop_after_conception": False,
        "story_enhancers": {
            "brainhole": False,
            "wild_concept": False,
            "effect_skills": [],
        },
        "language": "zh-CN",
        "project_type": "linear",
        "creation_mode": "long_serial",
        "field_sources": {
            "pov": "default",
            "tone_preference": "explicit",
            "story_enhancers": "explicit",
        },
    }

    snapshot = ensure_project_book_design_snapshot(project)

    assert snapshot.creation_intent.pov == "first_person"
    assert snapshot.creation_intent.length_key == "long"
    assert snapshot.creation_intent.story_enhancers.wild_concept is False
    assert snapshot.creation_intent.field_sources["tone_preference"] == "explicit"


def test_original_named_premise_outranks_generated_story_spine_drift() -> None:
    project = _project()
    project.metadata_json["premise"] = "裴野，登记石毫无反应的十六岁杂役，被禁荒喊出祖父的名字。"
    project.metadata_json["story_spine"] = {"who": "陆沉，边村少年"}
    project.metadata_json["identity_manifest"] = [
        {"name": "裴野", "role": "protagonist"}
    ]

    snapshot = ensure_project_book_design_snapshot(project)

    assert snapshot.protagonist.name == "裴野"

    report = validate_project_book_design(project)
    assert any(
        issue.code == "protagonist_identity_mismatch"
        and issue.asset == "story_spine"
        for issue in report.issues
    )


def test_project_design_validation_checks_each_conception_identity_surface() -> None:
    project = _project()
    project.metadata_json["premise"] = "裴野，登记石毫无反应的十六岁杂役。"
    project.metadata_json["story_spine"] = {"who": "裴野，边村少年"}
    project.metadata_json["hook_card"] = {"protagonist": "陆沉，边村少年"}
    project.metadata_json["concept_contract"] = {
        "story_spine": {"who": "陆沉，边村少年"},
        "hook_card": {"protagonist": "裴野，边村少年"},
    }
    project.metadata_json["identity_manifest"] = [
        {"name": "裴野", "role": "protagonist"}
    ]
    ensure_project_book_design_snapshot(project)

    report = validate_project_book_design(project)

    mismatched_assets = {
        issue.asset
        for issue in report.issues
        if issue.code == "protagonist_identity_mismatch"
    }
    assert mismatched_assets == {"hook_card", "concept_contract.story_spine"}


def test_project_design_validation_blocks_identity_and_tone_drift() -> None:
    project = _project()
    ensure_project_book_design_snapshot(project)
    project.metadata_json["identity_manifest"] = [{"name": "裴野", "role": "protagonist"}]
    project.metadata_json["writing_profile"] = {
        "style": {"tone_keywords": ["高压悬疑", "冷硬智斗"]}
    }
    report = validate_project_book_design(project)
    assert not report.passed
    assert {issue.code for issue in report.issues} == {
        "protagonist_identity_mismatch",
        "tone_mismatch",
    }


def test_project_design_validation_blocks_creation_and_budget_drift() -> None:
    project = _project()
    ensure_project_book_design_snapshot(project)
    project.metadata_json["genre_intent_contract"] = {
        **project.metadata_json["genre_intent_contract"],
        "tone_preference": "dark",
    }
    project.target_chapters = 51
    project.target_word_count = 140_000

    report = validate_project_book_design(project)

    assert {issue.code for issue in report.issues} >= {
        "creation_intent_mismatch",
        "chapter_budget_mismatch",
        "word_budget_mismatch",
    }

    project = _project()
    ensure_project_book_design_snapshot(project)
    project.metadata_json["genre_intent_contract"].pop("tone_preference")
    missing_field_report = validate_project_book_design(project)
    assert "creation_intent_mismatch" in {
        issue.code for issue in missing_field_report.issues
    }


def test_project_snapshot_refuses_generic_identity_and_missing_budget() -> None:
    project = _project()
    project.metadata_json = {"genre_intent_contract": project.metadata_json["genre_intent_contract"]}
    with pytest.raises(ValueError, match="named creation protagonist"):
        ensure_project_book_design_snapshot(project)

    project.metadata_json["protagonist_name"] = "裴野"
    project.target_word_count = 0
    with pytest.raises(ValueError, match="positive target_chapters"):
        ensure_project_book_design_snapshot(project)


def test_project_snapshot_preserves_legacy_project_entities_and_validates_id() -> None:
    project = _project()
    project.metadata_json["entities"] = [{"type": "location", "name": "北山"}]
    snapshot = ensure_project_book_design_snapshot(project)
    assert snapshot.entity_registry.resolve("北山", "location").canonical_name == "北山"

    tampered = snapshot.model_dump(mode="json")
    tampered["snapshot_id"] = "tampered-lineage"
    project.metadata_json["book_design_snapshot"] = tampered
    with pytest.raises(ValueError, match="id mismatch"):
        ensure_project_book_design_snapshot(project)


def test_new_book_premise_lineage_waits_for_llm_identity_then_locks_snapshot() -> None:
    project = _project()
    project.metadata_json.pop("story_spine")
    project.metadata_json.pop("identity_manifest")
    project.metadata_json["creation_intent_contract"] = {
        "schema_version": "creation-intent.v1",
        "genre_intent": dict(project.metadata_json["genre_intent_contract"]),
        "chapter_count": 50,
        "language": "zh-CN",
    }

    assert planning_snapshot_lineage(project) == {
        "source_snapshot_status": "pending_creation_identity"
    }

    project.metadata_json["creation_protagonist_name"] = "纪赊"
    lineage = planning_snapshot_lineage(project)

    assert lineage["source_snapshot_id"]
    assert project.metadata_json["book_design_snapshot"]["protagonist"]["name"] == "纪赊"


def test_authoritative_premise_repairs_legacy_snapshot_identity_drift() -> None:
    project = _project()
    stale = ensure_project_book_design_snapshot(project)
    assert stale.protagonist.name == "陆沉"
    project.metadata_json["premise"] = (
        "前世眼看就要亲政登基的废太子姬衡，醒来发现自己缩进婴儿躯壳。"
    )

    repaired = ensure_project_book_design_snapshot(project)

    assert repaired.protagonist.name == "姬衡"
    assert repaired.snapshot_id != stale.snapshot_id
    assert project.metadata_json["book_design_snapshot_repair_reason"] == (
        "authoritative_creation_identity_drift"
    )
    assert project.metadata_json["book_design_snapshot_superseded"][-1] == {
        "snapshot_id": stale.snapshot_id,
        "protagonist_name": "陆沉",
        "reason": "authoritative_creation_identity_drift",
        "replacement_name": "姬衡",
    }


def test_name_followed_by_shi_is_creation_boundary_identity() -> None:
    from bestseller.services.book_design import extract_creation_protagonist_name

    assert extract_creation_protagonist_name(
        {"premise": "余烬是青衡宗渣道倒渣杂役，七年没有挪过位置。"}
    ) == "余烬"
