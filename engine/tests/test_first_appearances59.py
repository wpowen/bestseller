"""Instruction 59 B2: world terms and named entities need first-use grounding."""

import json

import yaml

from bookengine.first_appearances import repair_first_appearances
from bookengine.introductions import check_world_first_appearances, world_term_registry


EVAL57_CHAPTER_7 = (
    "顾立谦感觉经脉里那缕游动的热力在腕上收束成一道细纹。"
    "醒脉的散力沿着路牌沉入旧轨。\n\n"
    "“凝纹。”他低声说。\n\n"
    "醒脉入凝纹。凝纹不能替他多出一分力气，却能让粮车循纹避开深坑。"
)
EVAL57_CHAPTER_40 = "霜团所感到的是潮湿腥气；另一头契兽传来的却是一股干冷土腥，来自西南。"


def bible():
    return {
        "world": {"levels": ["醒脉", "凝纹"], "factions": [{"name": "驭兽宗", "interest": "控制契兽与驿路"}]},
        "characters": [{"name": "霜团", "identity": "幼年雪兽"}],
    }


def test_eval57_first_level_use_and_unnamed_other_beast_are_detected():
    registry = world_term_registry(bible())
    findings = check_world_first_appearances(EVAL57_CHAPTER_7, registry)
    unresolved = check_world_first_appearances(EVAL57_CHAPTER_40, registry)

    assert "第1阶" not in registry["醒脉"]["definition"]
    assert {row["term"] for row in findings if row["kind"] == "term_first_use_missing"} == {"醒脉", "凝纹"}
    assert any(row["kind"] == "unresolved_entity_reference" and row["term"] == "另一头契兽" for row in unresolved)


def test_definition_must_be_bound_at_first_use_not_added_after_the_term_is_already_used():
    registry = world_term_registry(bible())
    grounded = "他催动醒脉的散力顶住石门，门缝只挪开半寸，显然还压不住阵纹。"
    late = "醒脉的散力沉入旧轨。后来他解释，醒脉是境界第一阶。"

    assert check_world_first_appearances(grounded, registry)[0]["kind"] == "term_introduction_present"
    assert check_world_first_appearances(late, registry)[0]["kind"] == "term_first_use_missing"


def test_instruction_manual_realm_sentence_is_not_first_use_evidence():
    registry = world_term_registry(bible())
    manual = "他修习的醒脉，是宗门境界序列的第一阶；再往上的凝纹，是承接醒脉的第二阶境界。"
    dialogue = "师姐按住他的腕脉：‘你才醒脉，催不动这道阵；等凝纹后再来。’"

    assert check_world_first_appearances(manual, registry)[0]["kind"] == "term_first_use_missing"
    assert {row["kind"] for row in check_world_first_appearances(dialogue, registry)} == {"term_introduction_present"}


def test_fake_repair_inserts_grounded_term_definition_and_clears_issue(tmp_path):
    original = "醒脉的散力沉入旧轨。"
    replacement = "顾立谦催动醒脉散力去推旧轨，热力才沉入半寸便散开，仍托不起粮车。"
    calls = []

    def ask(client, key, instruction, sections, schema, **kwargs):
        calls.append(key)
        target = dict(sections)["只可修改的首次出现段"][0]
        return {"edits": [{"id": target["id"], "original": target["original"], "text": replacement}]}

    def extract(*args, **kwargs):
        return {"state": {}, "events": []}, False, 0

    changed, raw, failed, note = repair_first_appearances(
        None,
        "r2-ch7",
        original,
        {"state": {}},
        False,
        {},
        bible(),
        {"number": 7},
        tmp_path,
        extract,
        ask,
    )

    assert changed == replacement and raw["events"] == [] and failed is False
    assert note["status"] == "checked" and note["after"] == []
    assert calls == ["r2-ch7-first-appearance-repair"]


def test_realm_first_use_can_be_grounded_by_immediate_dialogue_about_its_ability():
    from bookengine.introductions import check_world_first_appearances

    text = "“牵丝境能顺着丝势借物移力，牵着哪一头，线后的东西便会被引向另一头。”他低声说完，掌心贴紧断桩。"
    findings = check_world_first_appearances(
        text,
        {"牵丝境": {"kind": "level", "definition": "修行层级；须由能力或受限结果体现"}},
    )

    assert findings[0]["kind"] == "term_introduction_present"
    assert findings[0]["status"] == "Evidence"


def test_writer_and_repair_prompts_forbid_copying_realm_definitions_as_exposition(tmp_path):
    from bookengine.first_appearances import writer_first_appearance_instruction

    instruction = writer_first_appearance_instruction(
        tmp_path,
        1,
        bible(),
        {"number": 1, "scenes": [{"action": "顾立谦尝试催动醒脉"}]},
    )

    assert "不得照抄定义" in instruction
    assert "第1阶" not in instruction
    assert "修炼动作、受限结果或人物对白" in instruction


def test_character_first_mentioned_in_dialogue_gets_identity_at_that_mention(tmp_path):
    character_bible = {"characters": [{"name": "沈照霜", "identity": "驭兽宗女药师"}]}
    original = "“你认识沈照霜？”陶七问。"
    replacement = "沈照霜是驭兽宗女药师，先前替伤员配过药。陶七问：“你认识她？”"

    def ask(client, key, instruction, sections, schema, **kwargs):
        target = dict(sections)["只可修改的首次出现段"][0]
        return {"edits": [{"id": target["id"], "original": target["original"], "text": replacement}]}

    changed, raw, failed, note = repair_first_appearances(
        None,
        "r2-ch12",
        original,
        {"state": {}},
        False,
        {},
        character_bible,
        {"number": 12},
        tmp_path,
        lambda *args, **kwargs: ({"state": {}, "events": []}, False, 0),
        ask,
    )

    assert changed == replacement and raw["events"] == [] and failed is False
    assert note["status"] == "checked" and note["after"] == []


def test_checkup_registers_term_and_unresolved_reference_findings(tmp_path):
    from tools.book_checkup import check_book

    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible(), allow_unicode=True), encoding="utf-8")
    (tmp_path / "chapter-007.body.md").write_text(EVAL57_CHAPTER_7, encoding="utf-8")
    (tmp_path / "chapter-040.body.md").write_text(EVAL57_CHAPTER_40, encoding="utf-8")
    (tmp_path / "chapters").mkdir()
    for number in (7, 40):
        (tmp_path / f"chapters/{number:03d}.json").write_text(json.dumps({"number": number}), encoding="utf-8")

    report = check_book(tmp_path, source="draft")["world_introductions"]

    assert {row["term"] for row in report["missing_terms"]} == {"醒脉", "凝纹"}
    assert [row["term"] for row in report["unresolved_entities"]] == ["另一头契兽"]
