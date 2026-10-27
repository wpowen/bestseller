"""Current asset instructions stay separate from losslessly retained source data."""

from copy import deepcopy
import json

import pytest
from test_source_bound_planning_content import _project

from bestseller.services import source_bound_planning_content as content


@pytest.mark.parametrize("kind", ["book_spec", "world_spec", "cast_spec", "volume_plan"])
@pytest.mark.parametrize(("language", "label"), [("zh-CN", "简体中文"), ("en-US", "English")])
def test_current_kind_root_language_and_nonempty_submission_contract_are_explicit(
    kind, language, label
):
    project = _project()
    project.language = language
    system, user = content.build_source_bound_planning_prompts(
        kind=kind, project=project, premise="source premise", projection={}
    )
    data = json.loads(user)
    contract = data["current_output_contract"]
    assert content.CONTENT_POLICY == "source-bound-planning-content.v3-kind-submission"
    assert data["protocol"] == content.CONTENT_POLICY
    assert system.startswith(f"CURRENT_ASSET={kind}\n")
    assert contract["kind"] == data["kind"] == kind
    assert contract["expected_root"] == ("array" if kind == "volume_plan" else "object")
    assert contract["mandatory_exact_dotted_fields"] == data["required_output_fields"]
    assert contract["mandatory_value_requirement"].startswith("nonempty string")
    assert contract["output_language"] == data["delivery_scope"]["language_name"] == label
    assert data["delivery_scope"]["language"] == language
    assert contract["native_submission"]["function_name"] == f"submit_{kind}"
    from bestseller.services.source_bound_planning_submission import (
        VOLUME_NATIVE_POLICY, planning_submission_contract,
    )
    if kind == "book_spec":
        assert contract["native_submission"]["argument"] == "asset_json"
    else:
        assert contract["native_submission"] == planning_submission_contract(
            kind, version=VOLUME_NATIVE_POLICY if kind == "volume_plan" else None,
        )
    assert contract["proposed_output_schema"]["duplicate_object_keys"] == "forbidden"
    assert list(data)[-1] == "current_output_contract"
    assert user.endswith(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "}"
    )
    fields = contract["proposed_output_schema"]["field_names"]
    assert all(path.split(".")[0] in fields for path in contract["mandatory_exact_dotted_fields"])
    assert "(volume_plan: array; other kinds: object)" not in system
    if kind != "volume_plan":
        assert "volume_plan" not in system


def test_book_contract_requires_new_theme_without_default_story_or_empty_seed_submission():
    project = _project()
    system, user = content.build_source_bound_planning_prompts(
        kind="book_spec",
        project=project,
        premise="source premise",
        projection={"theme_statement": "", "protagonist": {"secret": ""}},
    )
    contract = json.loads(user)["current_output_contract"]
    assert "theme_statement" in contract["mandatory_exact_dotted_fields"]
    assert "dramatic_question" in contract["mandatory_exact_dotted_fields"]
    assert "protagonist.secret" not in contract["mandatory_exact_dotted_fields"]
    assert "新提案" in system and "可选的未知心理字段可以留空" in system
    assert "未来行动、新角色、地点、冲突、个人前史与机制" in system
    assert "固定剧情" in system
    assert "持续经营形成不可替代优势" not in user


def test_archived_other_task_and_full_canonical_are_preserved_once_but_not_current_contract():
    archive = {"task": "volume_plan", "proposal": "UNIQUE_ARCHIVED_FULL_TEXT_" * 30}
    project = _project({"archived_review": archive, "legitimate_unknown_field": {"x": [1, 2, 3]}})
    before = deepcopy(project.__dict__)
    system, user = content.build_source_bound_planning_prompts(
        kind="book_spec", project=project, premise="source premise", projection={}
    )
    data = json.loads(user)
    assert (
        data["approved_sources"]["canonical_snapshot"]["content"]
        == (project._source_bound_planning_canonical["content"])
    )
    assert user.count(archive["proposal"]) == 1
    assert data["current_output_contract"]["kind"] == "book_spec"
    assert data["current_output_contract"]["expected_root"] == "object"
    assert list(data)[-1] == "current_output_contract"
    assert "旧规划任务" in system
    assert project.__dict__ == before
    assert data["approved_sources"]["explicit_controls"]["story_enhancers"]["effect_skills"] == []


def test_final_contract_serialization_keeps_exact_capacity_receipt_and_default_guard(monkeypatch):
    project = _project()
    system, user = content.build_source_bound_planning_prompts(
        kind="book_spec", project=project, premise="source premise", projection={}
    )
    assert (
        content.assert_source_bound_planning_prompt_capacity(system, user)
        == (json.loads(user)["prompt_capacity"])
    )
    monkeypatch.setattr(content, "MAX_PROMPT_CHARS", 100)
    with pytest.raises(content.SourceBoundPlanningCapacityError) as caught:
        content.build_source_bound_planning_prompts(
            kind="book_spec", project=project, premise="source premise", projection={}
        )
    assert caught.value.receipt["truncated"] is False


def test_kind_contract_does_not_relax_wrong_asset_or_missing_theme_validation():
    project = _project()
    with pytest.raises(ValueError, match="missing_fields"):
        content.validate_source_bound_planning_payload(
            {"volume_plan": [{"volume_title": "wrong asset"}]},
            kind="book_spec",
            project=project,
            projection={},
        )
    with pytest.raises(ValueError, match="theme_statement"):
        content.validate_source_bound_planning_payload(
            {
                "logline": "proposal",
                "theme_statement": "",
                "dramatic_question": "question",
                "protagonist": {"name": "于岚", "external_goal": "new goal"},
                "series_engine": {"core_serial_engine": "new proposed engine"},
            },
            kind="book_spec",
            project=project,
            projection={},
        )
