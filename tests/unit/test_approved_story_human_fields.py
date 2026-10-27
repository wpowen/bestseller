"""Approved character content must survive compilation without policy filler."""
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services.planner import _compile_source_bound_book_spec


@pytest.mark.parametrize("genre,desire,flaw", [
    ("悬疑", "不愿把救命恩人当成陌生嫌疑人", "把感恩误当作对方无辜的证据"),
    ("言情", "想被伴侣当作平等的人而不是被照顾的人", "以拒绝帮助掩饰害怕被抛弃"),
    ("玄幻", "让师妹不再为自己的失败受罚", "把保护变成替别人决定"),
    ("职场", "保住团队对自己的信任", "为了不让人失望而隐瞒困难"),
])
def test_approved_desire_and_flaw_survive_without_inventing_inner_life(genre, desire, flaw):
    project = ProjectModel(
        id=uuid4(), slug="human-fields-test", title="测试", genre=genre,
        language="zh-CN", target_chapters=20, target_word_count=52000,
        metadata_json={"book_design_snapshot_status": "locked", "book_design_snapshot": {
            "snapshot_id": "test", "source_hash": "a" * 64,
            "protagonist": {"name": "林照"},
            "approved_story_design": {
                "source_contract_hash": "b" * 64, "source_champion_id": "candidate",
                "hook_card": {"one_liner": "她必须作出自己的选择", "private_desire": desire,
                              "protagonist_flaw": flaw, "emotional_promise": "心疼与期待"},
                "story_spine": {"wants": "完成约定", "stakes": "失去朋友的信任"},
                "seriality_proof": {"repeatable_story_unit": "选择带来关系变化"},
            },
        }},
    )
    book = _compile_source_bound_book_spec(project, "approved seed")
    person = book["protagonist"]
    assert person["name"] == "林照"
    assert person["external_goal"] == "完成约定"
    assert person["outer_motivation"] == desire
    assert person["weakness"] == flaw
    assert person["internal_need"] == ""
    assert person["secret"] == ""
    assert person["archetype"] != "source-bound operator"
    assert book["theme_statement"] == ""
    assert "protagonist.internal_need" in book["_meta"]["approved_story_design_missing"]
    assert "theme_statement" in book["_meta"]["approved_story_design_missing"]
    assert book["stakes"]["personal"] == "失去朋友的信任"

    approved = project.metadata_json["book_design_snapshot"]["approved_story_design"]
    approved["story_spine"].update({
        "internal_need": "接受亲近之人也有自己的选择", "secret": "曾隐瞒一封来信",
        "theme_statement": "保护一个人是否有权替他决定？",
        "question": "她能在失去信任前公开来信吗？",
    })
    enriched = _compile_source_bound_book_spec(project, "approved seed")
    assert enriched["protagonist"]["internal_need"] == approved["story_spine"]["internal_need"]
    assert enriched["protagonist"]["secret"] == "曾隐瞒一封来信"
    assert enriched["theme_statement"] == approved["story_spine"]["theme_statement"]
    assert enriched["dramatic_question"] == approved["story_spine"]["question"]
