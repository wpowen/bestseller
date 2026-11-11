"""Instruction 59 B4: relationship contracts and explicit climax exemptions."""

import json

from bookengine.chapter_rhythm import chapter_type, rhythm_issues, rhythm_slots
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


def test_relationship_contract_overrides_a_conflicting_combat_label():
    outline = relationship_outline(18)
    outline.update(type="对抗", rhythm_type="对抗")

    assert chapter_type(outline) == "情感"
    instruction = writer_rhythm_instruction(outline)
    assert "本章章型：情感" in instruction
    assert "两场主场景都要让这个变化" in instruction


def test_eval57_style_planned_relationship_but_actual_combat_gets_targeted_main_scene_rewrite():
    original = "顾立谦沿车辙选路。\n\n岑烈挥刀扑来，顾立谦借冷鼻识破陷阱。"
    replacement = "顾立谦承认借力会夺走温暖记忆，把是否继续的选择交给霜团；霜团重新靠近，允许他在明确边界内借力。"

    def ask(client, key, instruction, sections, schema, **kwargs):
        targets = dict(sections)["可改主场景段"]
        return {"edits": [{"id": targets[0]["id"], "original": targets[0]["original"], "text": replacement}]}

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
        assert len(targets) == 8
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
    assert note["status"] == "checked" and len(note["edits"]) == 8


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
