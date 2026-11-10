"""R3: real failed bible, recovery boundary, concrete progression and field-only repair."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from bookengine.cost_design import cost_issues, repair_costs

REAL = json.loads((Path(__file__).parent / "fixtures/promise37/eval33.json").read_text())


def cost(level, effect, previous="", loss="失去沙哑嗓音的音色，仍能正常说话", recovery=""):
    return (
        f"损失：{loss}；心理：{effect}；等级：{level}；升级：{previous + '→' + effect if previous else '首次付出个人性格代价'}"
        + recovery
    )


def bible():
    a, b = "性格变得多疑，开始查验陌生人的口信", "情感转冷，开始怀疑妹妹亲笔来信中的关心"
    return dict(
        title="借钟",
        selected_card={"title": "借钟", "golden_finger_rule": "每敲一次钟永久失去一段记忆"},
        power={"rule": "每敲一次钟永久失去一段记忆"},
        volumes=[
            dict(number=1, start=1, end=10, cost=cost(1, a), goal="救人"),
            dict(number=2, start=11, end=20, cost=cost(2, b, a), goal="找路"),
        ],
    )


def test_real_eval33_bible_costs_fail_without_mutation():
    before = deepcopy(REAL)
    issues = cost_issues(REAL)
    assert {i["stage"] for i in issues} == {1, 2, 3, 4, 5}
    assert any(i["code"] == "primary_ability" and i["stage"] == 1 for i in issues)
    assert REAL == before


def test_specific_voice_timbre_is_distinct_from_muteness_and_escalation_passes():
    assert not cost_issues(bible())


@pytest.mark.parametrize("loss", ["终身失声", "永久无法行走", "双目永久失明", "永远失去双手"])
def test_primary_ability_requires_same_stage_recovery_or_working_alternative(loss):
    b = bible()
    b["volumes"][0]["cost"] = cost(1, "性格变得多疑，开始查验陌生人的口信", loss=loss)
    assert any(i["code"] == "primary_ability" for i in cost_issues(b))
    b["volumes"][0]["cost"] += "；恢复章：8；恢复办法：在药铺安装辅助器具替代原能力"
    assert not any(i["code"] == "primary_ability" for i in cost_issues(b))
    b["volumes"][0]["cost"] = b["volumes"][0]["cost"].replace("恢复章：8", "恢复章：11")
    assert any(i["code"] == "primary_ability" for i in cost_issues(b))


def test_same_psychological_effect_cannot_be_escalated_by_inflating_level():
    b = bible()
    a = "性格变得多疑，开始查验陌生人的口信"
    b["volumes"][1]["cost"] = cost(99, a, a)
    assert any(i["code"] == "escalation" for i in cost_issues(b))
    b["volumes"][1]["cost"] = cost(1, "情感转冷，不再信任妹妹的关心", a)
    assert any(i["code"] == "escalation" for i in cost_issues(b))


def test_generic_psychology_or_ambiguous_voice_is_rejected():
    b = bible()
    b["volumes"][0]["cost"] = "损失：嗓音永逝；心理：更加痛苦；等级：1；升级：第一次"
    codes = {i["code"] for i in cost_issues(b)}
    assert {"psychological_change", "primary_ability"} <= codes


def test_targeted_cost_repair_preserves_locked_card_other_fields_and_original():
    b = bible()
    good = b["volumes"][1]["cost"]
    b["volumes"][1]["cost"] = "旧主侵蚀加重"
    before = deepcopy(b)
    calls = []

    def ask(kind, key, sections, redo):
        calls.append((kind, key, sections))
        return dict(cost=good, title="禁止换书", selected_card={}, volumes=[])

    changed, note = repair_costs(b, ask)
    assert note["status"] == "passed" and len(calls) == 1
    assert b == before and changed["selected_card"] == before["selected_card"]
    assert changed["power"] == before["power"] and changed["title"] == before["title"]
    assert changed["volumes"][0] == before["volumes"][0]
    assert changed["volumes"][1] == dict(before["volumes"][1], cost=good)
    assert "用户锁定事实，只读" in dict(calls[0][2])


def test_bad_return_is_bounded_recorded_and_does_not_replace_original():
    b = bible()
    b["volumes"][1]["cost"] = "尚未明确"
    original = deepcopy(b)
    calls = []

    def ask(*args):
        calls.append(args)
        return dict(cost="永久失声")

    changed, note = repair_costs(b, ask)
    assert len(calls) == 3
    assert changed == original and note["status"] == "failed_continued"
    assert all(i["adopted"] is False for i in note["attempts"])


def test_valid_design_does_not_call_model():
    result, note = repair_costs(bible(), lambda *a: pytest.fail("already valid"))
    assert note["status"] == "passed" and result == bible()


def test_eval57_five_empty_stages_accept_period_delimited_specific_psychology_without_category_words():
    minds = [
        "开始主动核对陌生人的承诺后才行动",
        "不再轻信同伴的安慰，转而亲自查证风险",
        "拒绝把判断交给权威，选择独自承担误判后果",
        "宁可放弃眼前捷径，也要先确认同行者能安全撤离",
        "从此主动公开自己的代价，让同伴共同决定是否继续",
    ]
    candidates = {}
    previous = ""
    for number, mind in enumerate(minds, 1):
        upgrade = "首次代价" if number == 1 else f"{previous}→{mind}"
        candidates[number] = f"损失：永久忘记一段炉火记忆。心理：{mind}。等级：{number}。升级：{upgrade}"
        previous = mind
    value = {
        "power": {"rule": "借用冷鼻会永久失去温暖记忆"},
        "volumes": [dict(number=n, start=(n - 1) * 10 + 1, end=n * 10, cost="") for n in range(1, 6)],
    }
    original = deepcopy(value)
    calls = []

    def ask(kind, key, sections, redo):
        number = int(key.rsplit("-", 1)[1])
        calls.append((number, sections, redo))
        return {"cost": candidates[number]}

    changed, note = repair_costs(value, ask)

    assert note["status"] == "passed" and len(calls) == 5
    assert [stage["cost"] for stage in changed["volumes"]] == list(candidates.values())
    assert value == original


def test_cost_retry_receives_rejected_candidate_and_remaining_errors():
    value = bible()
    value["volumes"][1]["cost"] = ""
    good = bible()["volumes"][1]["cost"]
    calls = []

    def ask(kind, key, sections, redo):
        calls.append(dict(sections))
        return {"cost": "损失：失去一段记忆。心理：更加痛苦。等级：2。升级：首次代价"} if redo == 1 else {"cost": good}

    changed, note = repair_costs(value, ask)

    assert note["status"] == "passed" and changed["volumes"][1]["cost"] == good
    assert calls[1]["上次候选，只读"] and calls[1]["上次候选仍有错误"]


def test_later_stage_cannot_claim_first_cost_when_previous_stage_is_empty():
    value = bible()
    value["volumes"][0]["cost"] = ""
    value["volumes"][1]["cost"] = "损失：永久忘记一段炉火记忆；心理：开始主动核对风险再行动；等级：2；升级：首次代价"

    issues = cost_issues(value)

    assert any(issue["stage"] == 2 and issue["code"] == "escalation" for issue in issues)
