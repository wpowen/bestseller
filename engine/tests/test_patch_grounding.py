"""Finish and due-payoff patches must not spend facts from later chapters."""

import json
from pathlib import Path

from bookengine.hook_momentum import repair_after_draft
from bookengine.q1_chapter import amend
from bookengine.repair_placement import place_guarded_patch


TEXT = "\n\n".join(
    (
        "顾寒扫完石阶，周厉把旧册压在袖中。山门执事正在回廊尽头走近。",
        "顾寒握住扫帚，左臂旧伤发麻。他没有追上周厉，只记住了旧册上的方炉暗印。",
        "周厉护着旧册退到石阶边，执事的脚步声越来越近。",
    )
)
OUTLINE = {
    "number": 1,
    "ending_type": "迫近危险",
    "payoff_due": True,
    "payoff_type": "身份抬升",
    "payoff": "扫雪所得第一式首次在真人压力下奏效，顾寒守住扫帚和杂役身份。",
    "ending_content": "周厉守住旧册不让他细看，山门执事仍在逼近，顾寒尚未脱险",
    "scenes": [
        {
            "where": "山门外侧石阶",
            "goal": "扫完最后一级石阶并试探剑招",
            "action": "顾寒借竹扫帚点地转腕，把积雪卷上石壁",
            "result": "周厉暂时退开，旧册封皮露出",
        }
    ],
}
RAW = {
    "state": {"dashboard": {"status": "杂役"}, "objects": [{"name": "旧册", "owner": "周厉"}]},
    "costs": [{"content": "左臂旧伤发麻", "quote": "左臂旧伤发麻"}],
    "patch": {},
}


def test_finish_patch_rejects_future_identity_and_resolution(tmp_path):
    dangerous = {
        "after": 3,
        "text": "周厉被押入焚册房，顾寒抢回旧册并在执事牌上添名，正式成为外门弟子。",
    }
    candidate, note = place_guarded_patch(TEXT, dangerous, raw=RAW, outline=OUTLINE, keep_final=True)
    assert candidate == TEXT
    assert note["status"] == "rejected"
    assert note["reason"] == "future_fact"
    assert {row["kind"] for row in note["findings"]} == {"identity_transition", "conflict_resolution"}


def test_finish_patch_accepts_current_scene_grounded_result():
    grounded = {
        "after": 3,
        "text": "顾寒压住左臂的麻意，握紧扫帚。执事脚步声越来越近，他把方炉暗印记在心里。",
    }
    candidate, note = place_guarded_patch(TEXT, grounded, raw=RAW, outline=OUTLINE, keep_final=True)
    assert candidate != TEXT
    assert note["status"] == "placed" and note["findings"] == []
    assert candidate.split("\n\n")[-2] == grounded["text"]
    assert candidate.split("\n\n")[-1] == TEXT.split("\n\n")[-1]


def test_guard_is_topic_neutral_and_rejects_unseen_identity_transition():
    text = "阿澈收起票据，公会的铜钟刚好敲过午时。"
    candidate, note = place_guarded_patch(
        text,
        {"after": 1, "text": "阿澈正式成为公会记录员。"},
        raw={"state": {"status": "学徒"}},
        outline={"number": 1, "payoff_due": True, "payoff_type": "身份抬升"},
        keep_final=False,
    )
    assert candidate == text
    assert note["findings"][0]["kind"] == "identity_transition"


def test_other_person_identity_does_not_authorize_protagonist_leapfrog():
    text = "沈孟遥已经是外门弟子，顾寒仍是杂役。"
    candidate, note = place_guarded_patch(
        text,
        {"after": 1, "text": "沈孟遥是外门弟子，顾寒正式成为外门弟子。"},
        outline={"number": 1},
        keep_final=False,
    )
    assert candidate == text
    assert note["reason"] == "future_fact"
    assert any(row["kind"] == "identity_transition" for row in note["findings"])


def test_existing_identity_is_not_mistaken_for_a_future_transition():
    text = "阿澈已经是公会记录员。\n\n铜钟在午后响了一声。"
    candidate, note = place_guarded_patch(
        text,
        {"after": 1, "text": "阿澈正式成为公会记录员，接过票据。"},
        keep_final=False,
    )
    assert candidate != text
    assert note["status"] == "placed"


def test_mention_negation_and_intention_are_not_event_completion():
    text = "顾寒守住旧册，没有抢回它，只打算查清路径。\n\n山门外风雪未停。"
    candidate, note = place_guarded_patch(
        text,
        {"after": 1, "text": "‘你要抢回旧册吗？’顾寒没有抢回旧册，只打算查清路径。"},
        keep_final=False,
    )
    assert candidate != text
    assert note["status"] == "placed"


def test_current_entity_object_result_is_allowed():
    text = "顾寒借墙反震击退周厉，旧册仍在周厉手中。\n\n山门外风雪未停。"
    candidate, note = place_guarded_patch(
        text,
        {"after": 1, "text": "顾寒击退周厉后，压住左臂的麻意。"},
        keep_final=False,
    )
    assert candidate != text
    assert note["status"] == "placed"


def test_incapacitation_requires_the_same_entity_not_anyone_falling():
    text = "敌人倒下了，顾寒仍握着扫帚。\n\n山门外风雪未停。"
    candidate, note = place_guarded_patch(
        text,
        {"after": 1, "text": "顾寒因力竭倒下。"},
        keep_final=False,
    )
    assert candidate == text
    assert note["reason"] == "future_fact"


def test_raw_summary_cannot_upgrade_a_true_quote_into_a_false_event():
    text = "他看见册子。\n\n山门外风雪未停。"
    candidate, note = place_guarded_patch(
        text,
        {"after": 1, "text": "顾寒夺回旧册。"},
        raw={"events": [{"quote": "他看见册子。", "summary": "顾寒夺回旧册"}]},
        keep_final=False,
    )
    assert candidate == text
    assert note["reason"] == "future_fact"


def test_event_support_requires_subject_and_object_in_one_clause():
    text = "顾寒旁观。周厉夺回旧册。\n\n山门外风雪未停。"
    candidate, note = place_guarded_patch(
        text,
        {"after": 1, "text": "顾寒夺回旧册。"},
        keep_final=False,
    )
    assert candidate == text
    assert note["reason"] == "future_fact"


def test_future_scheduled_reward_cannot_be_authorized_by_reward_type():
    text = "顾寒守在石阶边。\n\n山门外风雪未停。"
    candidate, note = place_guarded_patch(
        text,
        {"after": 1, "text": "顾寒夺回三枚灵石。"},
        outline={"number": 1, "payoff_due": True, "payoff_type": "得到回报"},
        future_outline={"number": 2, "payoff": "顾寒夺回三枚灵石。"},
        keep_final=False,
    )
    assert candidate == text
    assert note["findings"][0]["reason"] == "planned_for_later_chapter"


def test_amend_records_rejected_finish_patch_without_adopting_it():
    dangerous = {"after": 3, "text": "顾寒抢回旧册，正式成为外门弟子。"}
    changed, note = amend(TEXT, {"patch": dangerous}, {}, {}, OUTLINE)
    assert changed == TEXT
    assert note["patch"] is False
    assert note["patch_guard"]["reason"] == "future_fact"


def test_due_payoff_repair_uses_the_same_guard():
    dangerous = {"after": 3, "text": "顾寒抢回旧册，正式成为外门弟子。"}
    stages = []

    def ask(_client, stage, *_args, **_kwargs):
        stages.append(stage)
        assert stage.endswith("momentum-payoff-repair")
        return {"patch": dangerous}

    def extract(_client, stage, candidate, *_args, **_kwargs):
        stages.append(stage)
        return {"payoff": {"fulfilled": False, "type": "无兑现"}}, False, 0

    result = repair_after_draft(
        None,
        "r2-ch1",
        TEXT,
        dict(RAW, payoff={"fulfilled": False, "type": "无兑现"}),
        False,
        {},
        {},
        OUTLINE,
        {},
        [],
        extract,
        ask,
    )
    assert result[0] == TEXT
    assert result[3]["payoff"]["status"] == "rolled_back"
    assert result[3]["payoff"]["reason"] == "future_fact"
    assert stages == ["r2-ch1-momentum-payoff-repair", "r2-ch1-momentum-payoff-recheck"]


def test_saved_24dd_finish_patch_replay_if_available():
    root = Path(__file__).resolve().parents[2] / "books/webui/24ddbcc8-b588-4a84-885c-dab09edc95a2"
    draft = root / "chapter-001.r2.draft.md"
    response = root / "steps/r2-ch1-finish.json"
    outline = root / "writing_outlines/001.json"
    if not all(path.is_file() for path in (draft, response, outline)):
        return
    candidate, note = place_guarded_patch(
        draft.read_text(),
        json.loads(response.read_text())["patch"],
        raw=json.loads(response.read_text()),
        outline=json.loads(outline.read_text()),
        max_chars=400,
    )
    assert candidate == draft.read_text()
    assert note["reason"] == "future_fact"
