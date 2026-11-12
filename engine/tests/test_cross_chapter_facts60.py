"""Instruction 60: cross-chapter facts survive repairs and later drafting."""

import json

from bookengine.cross_chapter_facts import cross_chapter_checkup, guard_all_repairs, merge_cross_chapter_delta
from bookengine.story_payoffs import repair_story_payoffs


CH11_EVENT = "他没有与它硬扯，而是让傀儡的指尖搭上转接点，借那一握，将主控线反接。"
CH15_DENIAL = (
    "隔着数重山壁，一块顾长风曾从那里捡回的断线木牌自行翻面，牌背浮出从未有过的血色字迹；"
    "那字迹如被无形刀锋刻成，赫然显出方才被木纹与反接银线掩去的真相——顾长风伪装成失控傀儡"
    "混入死士之间，并非要把控制线反接，而是趁沈展尧挑碎车轴线结、逼戒律弟子回身拦截的混乱空档，"
    "猛然扯断韩朔言临时牵制死士的一条银线。"
)
CH15_REWRITE = "远处，偏道废料场方向，一堆早已废弃的旧木器忽然彼此牵动，咔咔碰撞起来。" + CH15_DENIAL

CH15_CONFIRMATION = "不是断线，而是反接。"
CH14_CONDITION = "若控制线反接失败，他们就会失去退路。"


def _event_state():
    delta = {
        "critical_events": [
            {
                "summary": "顾长风借傀儡将主控线反接",
                "quote": CH11_EVENT,
            }
        ]
    }
    return merge_cross_chapter_delta({}, delta, CH11_EVENT, 11)


def test_c1_original_quotes_detect_later_denial_of_completed_action():
    report = cross_chapter_checkup(
        [
            {"number": 11, "text": CH11_EVENT},
            {"number": 15, "text": CH15_REWRITE},
        ]
    )

    assert report["counts"]["rewritten_prior_events"] == 1
    issue = report["rewritten_prior_events"][0]
    assert issue["chapters"] == [11, 15]
    assert issue["previous_quote"] == CH11_EVENT
    assert issue["quote"] == CH15_DENIAL


def test_c1_conditions_and_not_but_confirmation_are_not_completed_event_rewrites():
    report = cross_chapter_checkup(
        [
            {"number": 11, "text": CH11_EVENT},
            {"number": 14, "text": CH14_CONDITION},
            {"number": 15, "text": CH15_CONFIRMATION},
        ]
    )

    assert report["counts"]["rewritten_prior_events"] == 0


def test_c1_conflict_repair_that_reveals_a_different_past_is_rolled_back():
    original = "韩朔言没有恋战，退入暗道。"
    outline = {
        "number": 15,
        "opponent_resolution": {"name": "韩朔言", "type": "被擒", "outcome": "失去继续作恶能力"},
    }
    calls = []

    def ask(_client, key, instruction, sections, _schema, **_kwargs):
        calls.append((key, instruction))
        if key == "ch15-prose-review":
            item = json.loads(dict(sections)["本章必须发生的事"])[0]
            return {"items": [{"id": item["id"], "status": "missing", "evidence": []}]}
        if key.endswith("-conflict-repair"):
            target = json.loads(dict(sections)["可修改的冲突段落及其后果"])[0]
            return {"edits": [{"id": target["id"], "original": target["original"], "text": CH15_REWRITE}]}
        if key.endswith("-conflict-continuity-recheck"):
            facts = json.loads(dict(sections)["前文已发生的关键事件"])
            assert facts[0]["quote"] == CH11_EVENT
            return {
                "findings": [
                    {
                        "previous_chapter": 11,
                        "previous_quote": CH11_EVENT,
                        "current_quote": CH15_REWRITE,
                        "reason": "新增段落否定前文已完成的反接行动",
                    }
                ]
            }
        item = json.loads(dict(sections)["本章必须发生的事"])[0]
        return {
            "items": [{"id": item["id"], "status": "fulfilled", "evidence": [{"chapter": 15, "quote": CH15_REWRITE}]}]
        }

    result = repair_story_payoffs(
        None,
        "ch15",
        original,
        {"events": []},
        False,
        _event_state(),
        {},
        outline,
        lambda *_args, **_kwargs: ({"events": []}, False, 0),
        ask,
    )

    assert result[0] == original
    assert result[-1]["status"] == "rolled_back"
    assert result[-1]["reason"] == "rewrites_prior_event"
    repair_prompt = next(instruction for key, instruction in calls if key.endswith("-conflict-repair"))
    assert "揭示" in repair_prompt and "前文已发生" in repair_prompt


def test_c1_final_guard_covers_rewrites_added_by_any_repair_stage():
    original = "韩朔言被擒，偏道恢复安静。"
    candidate = original + "\n\n" + CH15_REWRITE

    def ask(_client, key, _instruction, sections, _schema, **_kwargs):
        assert key == "ch15-final-conflict-continuity-recheck"
        facts = json.loads(dict(sections)["前文已发生的关键事件"])
        return {
            "findings": [
                {
                    "previous_chapter": 11,
                    "previous_quote": facts[0]["quote"],
                    "current_quote": CH15_REWRITE,
                    "reason": "修复新增文字改写前文行动",
                }
            ]
        }

    text, note = guard_all_repairs(ask, None, "ch15-final", original, candidate, _event_state(), 15)

    assert text == original
    assert note["status"] == "rolled_back" and note["reason"] == "rewrites_prior_event"
