"""Instruction 65: normalized permanent, quantity, level, and pronoun state."""

import json
import inspect

from bookengine.cast_consistency import cast_checkup, cast_findings, repair_cast_consistency
from bookengine.cross_chapter_facts import cross_chapter_checkup
from bookengine.permanent_facts import (
    merge_permanent_delta,
    permanent_state_checkup,
    permanent_state_findings,
    writer_permanent_facts,
)
from bookengine.state_values import (
    merge_state_value_delta,
    repair_state_values,
    state_value_checkup,
    state_value_findings,
    writer_state_value_facts,
)
from bookengine import q1_chapter


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


CH25_SEVEN = "七根弦中，那根从未被拨动的最末弦无风自鸣。"
CH27_FIVE = "古琴像认出了她的困境，剩余五根弦自行震了一次。"
CH32_SEVEN = "林遥把古琴横在身前，拇指压过仅存的七根弦。"
CH37_FIVE = "林遥把桐木古琴横在膝上。琴身只余五根弦。"
CH39_SEVEN = "林遥把桐木古琴横在膝上，指尖掠过仍完好的七弦，停在第八根空缺处。"
CH43_THREE_AND_SIX = "他将古琴横放在阵台边缘，指尖依次掠过三根尚能发声的弦。残余六弦随即震响。"
CH47_SEVEN = "琴身七根弦同时暗了一瞬。"
CH24_WIND = "识海中的境界随之松动，琴音第一次能离开琴弦后自行转辙。她踏入风弦境。"
CH25_WIND = "她踏入此前遥不可及的门槛，识海浮出新的境界名号——风弦境。"
CH31_BLOOD = "断弦激起的余波冲开林遥识海中的桎梏，她的气息骤然拔高，境界由雷弦境跨入血弦境。"
CH35_THUNDER = "林遥踏入雷弦境，近身琴音凝为雷芒。"
CH38_THUNDER = "她便以雷弦境的真元稳住台脚。"
CH40_THUNDER = "四散的余韵逐渐定成真正属于雷弦境同声阶的力量。"


def _value_state():
    text = CH27_FIVE + "\n\n" + CH31_BLOOD
    delta = {
        "quantity_states": [{"owner": "林遥", "item": "古琴琴弦", "intact": 5, "damaged": 2, "quote": CH27_FIVE}],
        "level_states": [
            {"name": "林遥", "track": "境界", "value": "血弦境", "change": "breakthrough", "quote": CH31_BLOOD}
        ],
    }
    return merge_state_value_delta({}, delta, text, BIBLE, 31)


def test_d2_d3_quote_grounded_values_enter_ledger_and_writer_gets_only_current_values():
    state = _value_state()
    ledger = state["continuity_ledger"]

    assert ledger["quantity_states"]["林遥|弦"]["intact"] == 5
    assert ledger["level_states"]["林遥|境界"]["value"] == "血弦境"
    facts = writer_state_value_facts(state, BIBLE)
    assert facts == ["当前数量：林遥的古琴琴弦完好五根、损坏两根", "当前境界：林遥为血弦境"]
    assert not any(word in "".join(facts) for word in ("必须付代价", "quantity_states", "level_states"))


def test_d2_quantity_increase_without_grounded_repair_and_same_chapter_drift_are_detected():
    state = _value_state()

    finding = state_value_findings(CH32_SEVEN, state, BIBLE, 32)
    assert [(row["kind"], row["expected"], row["observed"]) for row in finding] == [("quantity_increase", "5", "7")]
    same_chapter = state_value_findings(CH43_THREE_AND_SIX, state, BIBLE, 43)
    assert [(row["kind"], row["observed"]) for row in same_chapter] == [("quantity_increase", "6")]
    assert state_value_findings("工匠补上两根新弦，古琴恢复为七根弦。", state, BIBLE, 32) == []


def test_d3_repeated_breakthrough_and_realm_downgrade_follow_the_bible_ladder():
    wind_state = merge_state_value_delta(
        {},
        {"level_states": [{"name": "林遥", "track": "境界", "value": "风弦境", "quote": CH24_WIND}]},
        CH24_WIND,
        BIBLE,
        24,
    )
    repeated = state_value_findings(CH25_WIND, wind_state, BIBLE, 25)
    assert [(row["kind"], row["observed"]) for row in repeated] == [("repeated_level_breakthrough", "风弦境")]

    blood_state = _value_state()
    for chapter, text in ((35, CH35_THUNDER), (38, CH38_THUNDER), (40, CH40_THUNDER)):
        findings = state_value_findings(text, blood_state, BIBLE, chapter)
        assert [(row["kind"], row["expected"], row["observed"]) for row in findings] == [
            ("level_regression", "血弦境", "雷弦境")
        ]


def test_d2_d3_offline_checkup_replays_quantity_and_level_values_in_order():
    chapters = [
        {"number": 24, "text": CH24_WIND},
        {"number": 25, "text": CH25_WIND + "\n\n" + CH25_SEVEN},
        {"number": 27, "text": CH27_FIVE},
        {"number": 31, "text": CH31_BLOOD},
        {"number": 32, "text": CH32_SEVEN},
        {"number": 37, "text": CH37_FIVE},
        {"number": 38, "text": CH38_THUNDER},
        {"number": 39, "text": CH39_SEVEN},
        {"number": 40, "text": CH40_THUNDER},
        {"number": 43, "text": CH43_THREE_AND_SIX},
        {"number": 47, "text": CH47_SEVEN},
    ]

    values = state_value_checkup(chapters, BIBLE)
    assert [row["chapter"] for row in values["quantity_state_conflicts"]] == [32, 39, 43, 47]
    assert [row["chapter"] for row in values["level_state_conflicts"]] == [25, 38, 40]
    report = cross_chapter_checkup(chapters, bible=BIBLE)
    assert report["counts"]["quantity_state_conflicts"] == 4
    assert report["counts"]["level_state_conflicts"] == 3


def test_d2_d3_targeted_repair_rechecks_and_rolls_back_if_value_still_conflicts():
    original = CH32_SEVEN
    good = "林遥把古琴横在身前，拇指压过仅存的五根弦。"

    def run(replacement):
        stages = []

        def ask(_client, key, _instruction, sections, _schema, **_kwargs):
            stages.append(key)
            targets = json.loads(dict(sections)["待修状态句"])
            return {"edits": [{"id": targets[0]["id"], "text": replacement}]}

        result = repair_state_values(
            None,
            "r2-ch32",
            original,
            {"state": {}, "continuity_delta": {}},
            False,
            _value_state(),
            BIBLE,
            {"number": 32},
            lambda *_args, **_kwargs: ({"state": {}, "continuity_delta": {}}, False, 0),
            ask,
        )
        return result, stages

    repaired, stages = run(good)
    assert repaired[0] == good and repaired[-1]["status"] == "repaired"
    assert stages == ["r2-ch32-state-values-repair"]

    rejected, _ = run(CH32_SEVEN)
    assert rejected[0] == original
    assert rejected[-1]["status"] == "rolled_back"
    assert rejected[-1]["reason"] == "state_value_recheck_failed"


def test_d3_targeted_level_repair_rechecks_to_zero():
    replacement = "她便以血弦境的真元稳住台脚。"

    def ask(_client, _key, _instruction, sections, _schema, **_kwargs):
        target = json.loads(dict(sections)["待修状态句"])[0]
        return {"edits": [{"id": target["id"], "text": replacement}]}

    repaired, _, failed, note = repair_state_values(
        None,
        "r2-ch38",
        CH38_THUNDER,
        {"state": {}, "continuity_delta": {}},
        False,
        _value_state(),
        BIBLE,
        {"number": 38},
        lambda *_args, **_kwargs: ({"state": {}, "continuity_delta": {}}, False, 0),
        ask,
    )

    assert repaired == replacement
    assert failed is False and note["status"] == "repaired" and note["after"] == []


def test_d2_d3_clean_chapter_adds_zero_model_calls():
    calls = []
    result = repair_state_values(
        None,
        "r2-ch32",
        "林遥把古琴收入琴囊，继续赶路。",
        {"state": {}, "continuity_delta": {}},
        False,
        _value_state(),
        BIBLE,
        {"number": 32},
        lambda *_args, **_kwargs: ({}, False, 0),
        lambda *_args, **_kwargs: calls.append(True),
    )

    assert result[-1]["status"] == "checked"
    assert calls == []


D4_BIBLE = {
    "characters": [
        {"name": "林遥", "gender": "女", "role": "protagonist"},
        {"name": "顾以宁", "gender": "女", "role": "support"},
        {"name": "姬子翎", "gender": "女", "role": "antagonist"},
    ]
}
D4_DRAFT = "姬子翎不需要真正得到所有人的同意，他只需拿走几个人的声音，再把剩下的沉默涂成顺从。"
D4_FINAL = (
    "林遥望着核上的十二道环形纹，低声道：“姬子翎不必真正得到所有人的同意。"
    "他只要拿走几个人的声音，就能把剩下的沉默涂成顺从。”\n\n"
    "公开争论仍从远处传来，姬子翎也仍能听见。林遥站在众人的议论声里，终于看清其中的手段："
    "他只需截取彼此的矛盾，替所有人判定谁才是多数。即便众人已经退出旧阵位，他也能把那些沉默重新涂成顺从。"
)


def _d4_evidence(text):
    return [
        row
        for row in cast_findings(text, D4_BIBLE, chapter=38)
        if row["kind"] == "gender_pronoun" and row["status"] == "Evidence"
    ]


def test_d4_named_subject_inside_and_outside_quotes_binds_every_wrong_pronoun_to_ji_ziling():
    assert [(row["name"], row["observed"], row["expected"]) for row in _d4_evidence(D4_DRAFT)] == [
        ("姬子翎", "他", "她")
    ]

    rows = _d4_evidence(D4_FINAL)
    assert [(row["name"], row["observed"], row["expected"]) for row in rows] == [
        ("姬子翎", "他", "她"),
        ("姬子翎", "他", "她"),
        ("姬子翎", "他", "她"),
    ]
    assert not [row for row in rows if row["name"] == "林遥"]
    report = cast_checkup(D4_BIBLE, [{"number": 38, "text": D4_FINAL}])
    assert report["evidence_counts"]["gender_pronoun"] == 3


def test_d4_targeted_leaf_repair_fixes_all_pronouns_and_shared_recheck_passes():
    repaired, _, failed, note = repair_cast_consistency(
        None,
        "r2-ch38",
        D4_FINAL,
        {"events": []},
        False,
        {},
        D4_BIBLE,
        {"number": 38},
        lambda *_args, **_kwargs: ({"events": []}, False, 0),
        lambda *_args, **_kwargs: {"edits": []},
    )

    assert repaired.count("她只") == 2 and "她也能" in repaired
    assert _d4_evidence(repaired) == []
    assert failed is False and note["status"] == "repaired"


def test_d4_cast_repair_runs_after_other_content_rewriters():
    source = inspect.getsource(q1_chapter.chapter)
    cast = source.index("repair_cast_consistency")

    assert cast > source.index("repair_chapter_type")
    assert cast > source.index("repair_explanation_density")
