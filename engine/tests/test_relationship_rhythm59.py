"""Instruction 59 B4: relationship contracts and explicit climax exemptions."""

import json

from bookengine.chapter_rhythm import (
    chapter_type,
    observed_chapter_type,
    relationship_contract,
    relationship_outline_issues,
    rhythm_issues,
    rhythm_slots,
)
from bookengine.planning_policy import hard_replan_issues
from bookengine.q1_prepare import _ask
from bookengine.relationship_rhythm import repair_relationship_rhythm
from bookengine.rhythm_writer import writer_rhythm_instruction
from bookengine.structure_planning import structure_issues


def relationship_outline(number=4):
    return {
        "number": number,
        "type": "情感",
        "rhythm_type": "情感",
        "relationship_change": {
            "people": ["顾立谦", "霜团"],
            "cause": "顾立谦承认借力会夺走温暖记忆",
            "before": "霜团不再主动靠近",
            "after": "霜团允许他在明确边界内借力",
        },
        "scenes": [
            {
                "characters": ["顾立谦", "霜团"],
                "goal": "坦白隐瞒",
                "action": "顾立谦承认代价并交出拒绝权",
                "result": "霜团愿意重新靠近",
            },
            {
                "characters": ["顾立谦", "霜团"],
                "goal": "重定借力边界",
                "action": "霜团主动选择是否继续",
                "result": "两者从疏离转为谨慎信任",
            },
        ],
    }


def test_relationship_outline_requires_people_cause_before_and_after():
    invalid = relationship_outline()
    invalid.pop("relationship_change")

    bible = {"structure_version": 1, "characters": [{"name": "顾立谦"}, {"name": "霜团"}]}
    assert any(issue["kind"] == "rhythm_relationship_contract" for issue in rhythm_issues(bible, [], [invalid]))
    assert not [
        issue
        for issue in rhythm_issues(bible, [], [relationship_outline()])
        if issue["kind"] == "rhythm_relationship_contract"
    ]


def test_writer_receives_exact_relationship_change_and_no_combat_contract():
    instruction = writer_rhythm_instruction(relationship_outline())

    assert all(text in instruction for text in ("顾立谦、霜团", "承认借力", "不再主动靠近", "明确边界"))
    assert "不安排正面打斗" in instruction
    assert "变化前" in instruction and "变化后" in instruction and "主线" in instruction


def test_relationship_outline_rejects_combat_actions_and_duplicated_choices_before_writing():
    invalid = relationship_outline()
    invalid["scenes"][0].update(
        action="他挥刀撞开暗门并带同伴躲避追杀",
        choice="他挥刀撞开暗门并带同伴躲避追杀",
    )

    issues = relationship_outline_issues(invalid)
    rhythm = rhythm_issues({"structure_version": 1, "characters": [{"name": "甲"}, {"name": "乙"}]}, [], [invalid])

    assert {issue["reason"] for issue in issues} >= {"combat_main_action", "choice_duplicates_action"}
    assert any(issue["kind"] == "rhythm_relationship_scene" for issue in rhythm)
    assert any(issue["kind"] == "rhythm_relationship_scene" for issue in hard_replan_issues(rhythm))


def test_relationship_outline_accepts_interpersonal_actions_and_distinct_choices():
    outline = relationship_outline()
    outline["scenes"][0]["choice"] = "霜团听完后明确说出仍可同行的边界"
    outline["scenes"][1]["choice"] = "顾立谦接受她随时叫停借力"

    assert relationship_outline_issues(outline) == []


def test_relationship_local_rewrite_uses_real_planning_prompt_assembly(tmp_path):
    from bookengine.outline_diversity import repair_batch

    invalid = relationship_outline()
    invalid["scenes"][0].update(
        action="顾立谦挥刀撞开暗门并带霜团躲避追杀",
        choice="顾立谦挥刀撞开暗门并带霜团躲避追杀",
    )
    corrected = relationship_outline()
    corrected["scenes"][0]["choice"] = "霜团听完坦白，允许他在明确边界内借力"
    corrected["scenes"][1]["choice"] = "顾立谦接受她保留随时拒绝的权利"

    class FakeClient:
        model = "offline"
        provider = "offline"
        prefix = ""
        plan_suffix = ""
        relaxed = False

        def stage(self, stage, prompt, schema, **kwargs):
            self.stage_name = stage
            self.prompt = prompt
            self.schema = schema
            return {"chapters": [corrected]}

    client = FakeClient()

    def rewrite(current, issues, targets, used, attempt):
        return _ask(
            client,
            "outlines",
            "chapters-1-4-relationship",
            [
                ("关系章错误", issues),
                ("待重写章纲", [row for row in current if row["number"] in targets]),
                ("硬约束", "action与choice必须是不同的人际行为，打斗只能作背景"),
            ],
            attempt,
        )

    result = repair_batch(
        tmp_path,
        "001-004",
        [],
        [{"number": number, "type": "对抗", "scenes": [{"action": "击退追兵"}]} for number in range(1, 4)] + [invalid],
        rewrite,
        bible={"structure_version": 1, "volumes": [{"number": 1, "start": 1, "end": 4}]},
    )

    assert result[-1]["scenes"] == corrected["scenes"]
    assert invalid["scenes"][0]["action"] in client.prompt
    assert invalid["relationship_change"]["before"] in client.prompt
    assert "choice" in client.schema


def test_relationship_contract_overrides_a_conflicting_combat_label():
    outline = relationship_outline(18)
    outline.update(type="对抗", rhythm_type="对抗")

    assert chapter_type(outline) == "情感"
    instruction = writer_rhythm_instruction(outline)
    assert "本章章型：情感" in instruction
    assert "把人物关系从变化前推到变化后" in instruction


def test_saved_web_relationship_contract_shapes_are_normalized_before_use():
    chapter4 = {
        "type": "情感",
        "opening_conflict": "顾长风想以隐瞒保护木岳霖，木岳霖却拒绝被排除在真相外。",
        "relationship_change": {
            "before": "顾长风隐瞒残线，木岳霖只能猜测危险。",
            "after": "顾长风主动坦白，木岳霖取得制止其独行的资格。",
            "turning_point": "木岳霖指出保护也在剥夺他的选择。",
        },
        "scenes": [
            {"characters": ["顾长风", "木岳霖"], "action": "顾长风坦白隐瞒", "choice": "木岳霖选择追问"},
            {"characters": ["顾长风", "木岳霖"], "action": "木岳霖提出边界", "choice": "顾长风接受制止"},
        ],
    }
    chapter6 = {
        "type": "情感",
        "relationship_change": {
            "characters": ["顾长风", "木手"],
            "cause": "顾长风承认残线风险。",
            "before": "彼此不信任。",
            "after": "接受对方决定次序。",
        },
    }
    stale_chapter8 = {
        "type": "揭秘",
        "relationship_change": {
            "pair": ["顾长风", "木岳霖"],
            "before": "尚未主动承担风险。",
            "after": "形成明确分工。",
        },
    }

    assert relationship_contract(chapter4) == {
        "people": ["顾长风", "木岳霖"],
        "cause": "木岳霖指出保护也在剥夺他的选择。",
        "before": "顾长风隐瞒残线，木岳霖只能猜测危险。",
        "after": "顾长风主动坦白，木岳霖取得制止其独行的资格。",
    }
    assert relationship_contract(chapter6)["people"] == ["顾长风", "木手"]
    assert relationship_contract(stale_chapter8) is None


def test_observed_type_treats_sustained_relationship_change_as_emotional_but_not_a_battle():
    outline = relationship_outline()
    relationship_body = "\n\n".join(
        [
            "顾立谦向霜团坦白自己一直隐瞒借力代价。",
            "霜团质问他为何替自己决定去留。",
            "顾立谦承认保护也剥夺了她的选择。",
            "霜团没有原谅，只要求他今后先告诉自己。",
            "顾立谦接受她随时制止借力的权利。",
            "两人重新约定同行边界，信任与此前不同。",
        ]
    )
    battle_body = (
        relationship_body
        + "\n\n"
        + "\n\n".join(
            [
                "敌人拔剑扑来，顾立谦挥刀迎战。",
                "追兵围攻，霜团被迫突围。",
                "顾立谦斩断敌人的刀，击退三名追兵。",
                "伏兵再度冲杀，两人破阵夺门。",
                "敌人围堵出口，顾立谦反杀领头人。",
                "余下追兵追杀到山口，双方再次交战。",
                "顾立谦挥剑逼退敌人，带霜团突围。",
            ]
        )
    )

    assert observed_chapter_type({"type": "对抗"}, relationship_body, outline) == "情感"
    assert observed_chapter_type({"type": "对抗"}, battle_body, outline) == "对抗"


def test_extract_prompt_defines_verbal_relationship_conflict_separately_from_hostile_combat():
    import yaml
    from bookengine.q1_extract import DATA

    prompt = yaml.safe_load((DATA.parent / "growth_extract.yaml").read_text())["prompt"]

    assert "言语冲突和信任变化" in prompt
    assert "敌对势力" in prompt
    assert "正面冲突" in prompt


def test_eval57_style_planned_relationship_but_actual_combat_gets_targeted_main_scene_rewrite():
    original = "顾立谦沿车辙选路。\n\n岑烈挥刀扑来，顾立谦借冷鼻识破陷阱。"
    replacement = "顾立谦承认借力会夺走温暖记忆，把是否继续的选择交给霜团；霜团重新靠近，允许他在明确边界内借力。"

    def ask(client, key, instruction, sections, schema, **kwargs):
        targets = dict(sections)["可改主场景段"]
        return {"edits": [{"id": row["id"], "original": row["original"], "text": replacement} for row in targets]}

    def extract(*args, **kwargs):
        return {"state": {}, "events": [], "type": "情感"}, False, 0

    changed, raw, failed, note = repair_relationship_rhythm(
        None,
        "r2-ch4",
        original,
        {"state": {}, "events": [], "type": "对抗"},
        False,
        {},
        {},
        relationship_outline(),
        extract,
        ask,
    )

    assert replacement in changed and raw["type"] == "情感" and failed is False
    assert note["status"] == "checked"


def test_relationship_repair_sends_full_body_and_targets_both_main_scenes():
    paragraphs = [f"顾立谦与霜团在第{number}段讨论退路。" for number in range(1, 11)]
    original = "\n\n".join(paragraphs)

    def ask(client, key, instruction, sections, schema, **kwargs):
        supplied = dict(sections)
        targets = supplied["可改主场景段"]
        assert supplied["完整正文只读"] == original
        assert len(targets) == 10
        return {
            "edits": [
                {
                    "id": row["id"],
                    "original": row["original"],
                    "text": f"顾立谦向霜团坦白代价，霜团在第{index}处主动选择继续同行。",
                }
                for index, row in enumerate(targets, 1)
            ]
        }

    changed, raw, failed, note = repair_relationship_rhythm(
        None,
        "r2-ch18",
        original,
        {"state": {}, "events": [], "type": "对抗"},
        False,
        {},
        {},
        relationship_outline(18),
        lambda *args, **kwargs: ({"state": {}, "events": [], "type": "情感"}, False, 0),
        ask,
    )

    assert changed != original and raw["type"] == "情感" and failed is False
    assert note["status"] == "checked" and len(note["edits"]) == 10


def test_relationship_repair_keeps_grounded_edits_when_one_model_original_is_wrong():
    paragraphs = [f"顾立谦与霜团在第{number}段讨论退路。" for number in range(1, 11)]
    original = "\n\n".join(paragraphs)

    def ask(client, key, instruction, sections, schema, **kwargs):
        targets = dict(sections)["可改主场景段"]
        edits = [
            {
                "id": row["id"],
                "original": row["original"],
                "text": f"顾立谦坦白隐瞒，霜团在第{row['id']}段明确选择继续同行。",
            }
            for row in targets
        ]
        edits[3]["original"] = edits[3]["original"].replace("讨论", "商量")
        return {"edits": edits}

    changed, raw, failed, note = repair_relationship_rhythm(
        None,
        "r2-ch18",
        original,
        {"state": {}, "events": [], "type": "对抗"},
        False,
        {},
        {},
        relationship_outline(18),
        lambda *args, **kwargs: ({"state": {}, "events": [], "type": "情感"}, False, 0),
        ask,
    )

    assert changed != original and raw["type"] == "情感" and failed is False
    assert note["status"] == "checked" and note["rejected_edits"] == [{"id": 4, "reason": "original_mismatch"}]


def final_bible(exemption=None):
    stages = [
        {"number": 1, "start": 1, "end": 10},
        {"number": 2, "start": 11, "end": 20},
    ]
    if exemption is not None:
        stages[-1]["combat_run_exemption"] = exemption
    return {"volumes": stages}


def test_only_explicit_reasoned_final_climax_can_exceed_three_confrontations():
    history = [{"number": 15, "type": "情感"}] + [{"number": number, "type": "对抗"} for number in (16, 17, 18)]
    ordinary = rhythm_slots(final_bible(), 19, 20, history, [{"number": 19, "type": "对抗"}])
    exempt = rhythm_slots(
        final_bible({"chapters": [17, 18, 19, 20], "reason": "终局反派与主线必须在祖窟连战收束"}),
        19,
        20,
        history,
        [{"number": 19, "type": "对抗"}],
    )

    assert ordinary[0]["rhythm_type"] != "对抗"
    assert exempt[0]["rhythm_type"] == "对抗" and exempt[0]["combat_exempt"] is True


def test_invalid_exemption_is_a_structure_issue():
    invalid = final_bible({"chapters": [17, 19, 20], "reason": "高潮"})
    invalid.update(
        reader_promise_contract_version=0,
        antagonists=[],
    )

    issues = structure_issues(invalid, 20)

    assert any("combat_run_exemption" in issue for issue in issues)


def test_null_optional_exemption_is_absent_not_an_invalid_request():
    bible = final_bible()
    for stage in bible["volumes"]:
        stage["combat_run_exemption"] = None
    bible.update(reader_promise_contract_version=0, antagonists=[])

    issues = structure_issues(bible, 20)

    assert not any("combat_run_exemption" in issue for issue in issues)


def test_checkup_separates_approved_and_unapproved_excess_runs(tmp_path):
    from tools.book_checkup import check_book
    import yaml

    bible = final_bible({"chapters": [17, 18, 19, 20], "reason": "终局连续对抗收束主线"})
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible, allow_unicode=True), encoding="utf-8")
    (tmp_path / "chapters").mkdir()
    for number in range(16, 21):
        (tmp_path / f"chapter-{number:03d}.body.md").write_text("两人迎战追兵。", encoding="utf-8")
        (tmp_path / f"chapters/{number:03d}.json").write_text(
            json.dumps({"number": number, "type": "对抗"}, ensure_ascii=False), encoding="utf-8"
        )

    rhythm = check_book(tmp_path, source="draft")["chapter_rhythm"]

    assert rhythm["approved_excess_runs"] == [[17, 18, 19, 20]]
    assert rhythm["unapproved_excess_runs"] == [[16, 17, 18, 19, 20]]


def test_checkup_uses_grounded_relationship_body_rule_for_distribution_and_runs(tmp_path):
    from tools.book_checkup import check_book
    import yaml

    (tmp_path / "story_bible.yaml").write_text(
        yaml.safe_dump({"volumes": [{"number": 1, "start": 1, "end": 4}]}, allow_unicode=True)
    )
    (tmp_path / "chapters").mkdir()
    (tmp_path / "writing_outlines").mkdir()
    for number in range(1, 5):
        body = "敌人拔剑扑来，顾立谦挥刀迎战。"
        outline = {"number": number, "type": "对抗"}
        if number == 4:
            body = "\n\n".join(
                [
                    "顾立谦向霜团坦白自己一直隐瞒借力代价。",
                    "霜团质问他为何替自己决定去留。",
                    "顾立谦承认保护也剥夺了她的选择。",
                    "霜团没有原谅，只要求他今后先告诉自己。",
                    "顾立谦接受她随时制止借力的权利。",
                    "两人重新约定同行边界，信任与此前不同。",
                ]
            )
            outline = relationship_outline(number)
        (tmp_path / f"chapter-{number:03d}.body.md").write_text(body)
        (tmp_path / f"chapters/{number:03d}.json").write_text(
            json.dumps({"number": number, "type": "对抗"}, ensure_ascii=False)
        )
        (tmp_path / f"writing_outlines/{number:03d}.json").write_text(json.dumps(outline, ensure_ascii=False))

    rhythm = check_book(tmp_path, source="draft")["chapter_rhythm"]

    assert rhythm["distribution"] == {"关系": 1, "对抗": 3}
    assert rhythm["stages"][0]["relationship_chapters"] == [4]
    assert rhythm["longest_confrontation"] == 3
    assert rhythm["excess_runs"] == []
