"""Instruction 65: normalized permanent, quantity, level, and pronoun state."""

from bookengine.cross_chapter_facts import cross_chapter_checkup
from bookengine.permanent_facts import (
    merge_permanent_delta,
    permanent_state_checkup,
    permanent_state_findings,
    writer_permanent_facts,
)


BIBLE = {
    "characters": [{"name": "林遥", "gender": "女", "role": "protagonist"}],
    "world": {"levels": ["松弦境", "桐弦境", "风弦境", "雷弦境", "血弦境"]},
    "canon": {"ladder": {"strength": ["松弦境", "桐弦境", "风弦境", "雷弦境", "血弦境"]}},
}
CH31_MULTI_LOSS = "少了一弦，琴面其余弦同时暗下，林遥只觉神识中大片空缺：多线发令、远程呼救、强行维持命令，全都消失了。"
CH33_MULTI_LOSS = "林遥拾起地上的古琴。失去玄安澜手中那根弦后，它再也不能同时号令多人。"
CH41_COMMAND_LOSS = "“我不能再直接命令任何人。”林遥把古琴推向顾以宁。"
CH43_COMMAND_LOSS = "以后她无法再以琴音令任何人向前、停下或退后，即使近在咫尺，也不能临时发令。"
CH45_COMMAND_LOSS = "古琴以后只能借自由阵纹修复。林遥说：“不能再命令任何人，也不能借我发令。”"
CH49_COMMAND_LOSS = "林遥能触碰琴弦，却再也不能借它命令任何人。"


def _loss_delta(name, subject, summary, quote):
    return {
        "permanent_states": [
            {
                "name": name,
                "category": "能力",
                "subject": subject,
                "change": "lost",
                "summary": summary,
                "quote": quote,
            }
        ]
    }


def test_d1_swapped_person_and_state_fields_are_normalized_to_subject_and_ability_category():
    state = merge_permanent_delta(
        {},
        _loss_delta("林遥的领民发令能力", "林遥", "永久失去直接命令任何人的能力", CH41_COMMAND_LOSS),
        CH41_COMMAND_LOSS,
        41,
        BIBLE,
    )

    saved = state["continuity_ledger"]["permanent_states"]
    assert len(saved) == 1
    assert saved[0]["name"] == "林遥"
    assert saved[0]["subject"] == "林遥的领民发令能力"
    assert saved[0]["state_keys"] == ["林遥|ability|command"]
    assert writer_permanent_facts(state, BIBLE) == ["已经永久失去，不可再次失去或无依据恢复：林遥的命令能力（第41章）"]


def test_d1_same_command_loss_with_different_wording_is_detected_without_a_model_call():
    state = merge_permanent_delta(
        {},
        _loss_delta("林遥的领民发令能力", "林遥", "永久失去直接命令任何人的能力", CH41_COMMAND_LOSS),
        CH41_COMMAND_LOSS,
        41,
        BIBLE,
    )

    for chapter, text in ((43, CH43_COMMAND_LOSS), (45, CH45_COMMAND_LOSS), (49, CH49_COMMAND_LOSS)):
        findings = permanent_state_findings(text, state, chapter, bible=BIBLE)
        assert [(row["kind"], row["name"], row["state_key"]) for row in findings] == [
            ("repeated_permanent_loss", "林遥", "林遥|ability|command")
        ]


def test_d1_offline_checkup_detects_multiline_and_general_command_losses_as_two_groups():
    chapters = [
        {"number": 31, "text": CH31_MULTI_LOSS},
        {"number": 33, "text": CH33_MULTI_LOSS},
        {"number": 41, "text": CH41_COMMAND_LOSS},
        {"number": 43, "text": CH43_COMMAND_LOSS},
        {"number": 45, "text": CH45_COMMAND_LOSS},
        {"number": 49, "text": CH49_COMMAND_LOSS},
    ]

    report = cross_chapter_checkup(chapters, bible=BIBLE)

    assert report["counts"]["permanent_state_conflicts"] == 4
    assert [row["chapter"] for row in report["permanent_state_conflicts"]] == [33, 43, 45, 49]
    assert {row["state_key"] for row in report["permanent_state_conflicts"]} == {
        "林遥|ability|multi_command",
        "林遥|ability|command",
    }


def test_d1_unregistered_non_group_subject_is_not_written_to_the_ledger():
    state = merge_permanent_delta(
        {},
        _loss_delta("并不存在的人", "陌生能力", "永久失去能力", "并不存在的人永久失去能力。"),
        "并不存在的人永久失去能力。",
        2,
        BIBLE,
    )

    assert state["continuity_ledger"]["permanent_states"] == []


def test_d1_ledger_uses_the_extracted_subject_not_an_unrelated_clause_in_the_quote():
    arm_quote = "“永久代价不只是琴。”林遥说，“我的右臂发麻，指腹没有知觉。她仍能弹琴，但再也不能借它向领民发令。”"
    state = {
        "continuity_ledger": {
            "permanent_states": [
                _loss_delta("林遥的领民发令能力", "林遥", "永久失去直接命令任何人的能力", CH41_COMMAND_LOSS)[
                    "permanent_states"
                ][0]
                | {"chapter": 41},
                _loss_delta("林遥右臂知觉", "林遥", "琴力反噬使右臂发麻并失去指腹知觉", arm_quote)["permanent_states"][
                    0
                ]
                | {"chapter": 44},
                _loss_delta("古琴强制发令能力", "林遥", "永久失去借古琴命令领民的能力", CH45_COMMAND_LOSS)[
                    "permanent_states"
                ][0]
                | {"chapter": 45},
            ]
        }
    }

    findings = permanent_state_checkup([], BIBLE, state)

    assert [(row["chapter"], row["state_key"]) for row in findings] == [(45, "林遥|ability|command")]
