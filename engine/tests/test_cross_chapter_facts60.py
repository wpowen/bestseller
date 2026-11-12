"""Instruction 60: cross-chapter facts survive repairs and later drafting."""

import json

from bookengine.cast_consistency import repair_cast_consistency
from bookengine.cross_chapter_facts import (
    cross_chapter_checkup,
    guard_all_repairs,
    merge_cross_chapter_delta,
)
from bookengine.opening_styles import repair_opening_style
from bookengine.permanent_facts import (
    merge_permanent_delta,
    permanent_state_findings,
    repair_permanent_states,
    writer_permanent_facts,
)
from bookengine.plot_progression import repair_plot_replay
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
CH31_COST = (
    "他盯着掌中死灰的印痕，低声道：“此后不能再持弟子身份进山门，不能调用宗门功法，也不能凭首席一系的名号调遣同门。”"
)
CH33_REPEAT = (
    "经过旧魂灯一役，木心鸣的气息仍在他胸口缓慢震动，可顾长风清楚，留在木手里的青衡宗身份印记"
    "已薄得像一层将裂的纸。那些印记原本能稳定高阶转接，也能替他把反噬推给宗门阵堂，如今却连半点回应都没有。"
)
CH37_REPEAT = "那不是反接成功后的充盈，而是青衡宗烙在傀儡术中的身份权限被抽空。"
CH39_RESTORE = (
    "那是内门弟子身份留下的最后一层权限。只要它还在，他便能调用一次宗门功法，也能在山门前重新取得通行和调遣同门的资格。"
)


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


def _permanent_state():
    delta = {
        "permanent_states": [
            {
                "name": "顾长风",
                "category": "身份与权限",
                "subject": "青衡宗内门弟子身份、宗门功法与同门调遣权限",
                "change": "lost",
                "summary": "永久失去青衡宗弟子身份、宗门功法和调遣同门权限",
                "quote": CH31_COST,
            }
        ]
    }
    return merge_permanent_delta({}, delta, CH31_COST, 31)


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


def test_c1_final_guard_rolls_back_only_bad_paragraph_and_keeps_good_gender_repair():
    original_bad = "韩朔言被擒，偏道恢复安静。"
    original_gender = "薛欣妍仍站在阵中，禁军不再听从她的调动。"
    repaired_gender = "薛欣妍仍站在阵中，禁军不再听从他的调动。"
    original = original_bad + "\n\n" + original_gender
    candidate = CH15_REWRITE + "\n\n" + repaired_gender
    bible = {"characters": [{"name": "薛欣妍", "gender": "男", "role": "antagonist"}]}

    def ask(_client, _key, _instruction, sections, _schema, **_kwargs):
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

    text, note = guard_all_repairs(ask, None, "ch15-final", original, candidate, _event_state(), 15, bible=bible)

    assert text == original_bad + "\n\n" + repaired_gender
    assert note["status"] == "partially_rolled_back"
    assert note["rolled_back_paragraphs"] == [{"candidate": 1, "original": 1}]
    assert note["deterministic_checks"]["gender_pronoun"] == 0


def test_c2_quote_grounded_permanent_loss_enters_ledger_and_writer_context():
    state = _permanent_state()
    saved = state["continuity_ledger"]["permanent_states"][0]

    assert saved["chapter"] == 31 and saved["quote"] == CH31_COST and saved["change"] == "lost"
    facts = writer_permanent_facts(state)
    assert facts == [
        "已经永久失去，不可再次失去或无依据恢复：顾长风的青衡宗内门弟子身份、宗门功法与同门调遣权限（第31章）"
    ]


def test_c2_original_repeated_losses_and_groundless_restore_are_detected():
    state = _permanent_state()

    assert permanent_state_findings(CH33_REPEAT, state, 33)[0]["kind"] == "repeated_permanent_loss"
    assert permanent_state_findings(CH37_REPEAT, state, 37)[0]["kind"] == "repeated_permanent_loss"
    assert permanent_state_findings(CH39_RESTORE, state, 39)[0]["kind"] == "groundless_restore"

    report = cross_chapter_checkup(
        [
            {"number": 31, "text": CH31_COST},
            {"number": 33, "text": CH33_REPEAT},
            {"number": 37, "text": CH37_REPEAT},
            {"number": 39, "text": CH39_RESTORE},
        ]
    )
    assert report["counts"]["permanent_state_conflicts"] == 3
    assert [row["chapter"] for row in report["permanent_state_conflicts"]] == [33, 37, 39]


def test_c2_targeted_repair_is_rechecked_and_bad_recheck_rolls_back():
    replacement = "顾长风以军阵调兵纹接入双环，宗门身份与功法依旧无法使用。"

    def run(recheck_findings):
        stages = []

        def ask(_client, key, _instruction, sections, _schema, **_kwargs):
            stages.append(key)
            if key.endswith("-permanent-review"):
                return {
                    "findings": [
                        {
                            "id": 1,
                            "kind": "groundless_restore",
                            "previous_chapter": 31,
                            "previous_quote": CH31_COST,
                            "current_quote": CH39_RESTORE,
                            "reason": "已永久失去的权限凭空恢复",
                        }
                    ]
                }
            if key.endswith("-permanent-repair"):
                sentence = json.loads(dict(sections)["允许修改的命中句"])[0]
                return {"edits": [{"id": sentence["id"], "text": replacement}]}
            return {"findings": recheck_findings}

        result = repair_permanent_states(
            None,
            "r2-ch39",
            CH39_RESTORE,
            {"events": []},
            False,
            _permanent_state(),
            {},
            {"number": 39},
            lambda *_args, **_kwargs: ({"events": [], "continuity_delta": {}}, False, 0),
            ask,
        )
        return result, stages

    repaired, stages = run([])
    assert repaired[0] == replacement and repaired[-1]["status"] == "repaired"
    assert stages == ["r2-ch39-permanent-review", "r2-ch39-permanent-repair", "r2-ch39-permanent-recheck"]

    rejected, _ = run(
        [
            {
                "id": 1,
                "kind": "groundless_restore",
                "previous_chapter": 31,
                "previous_quote": CH31_COST,
                "current_quote": replacement,
                "reason": "改写后仍恢复权限",
            }
        ]
    )
    assert rejected[0] == CH39_RESTORE
    assert rejected[-1]["status"] == "rolled_back" and rejected[-1]["reason"] == "permanent_recheck_failed"


def test_instruction60_no_hit_model_call_count_uses_only_three_review_stages(tmp_path):
    state = _event_state()
    state["continuity_ledger"]["permanent_states"] = _permanent_state()["continuity_ledger"]["permanent_states"]
    text = "顾长风清点木手。"
    bible = {"characters": [{"name": "顾长风", "gender": "男", "role": "protagonist"}]}
    outline = {"number": 40}
    stages = []

    def ask(_client, key, _instruction, _sections, _schema, **_kwargs):
        stages.append(key)
        return {"findings": []}

    def no_extract(*_args, **_kwargs):
        raise AssertionError("no-hit review must not extract state")

    repair_plot_replay(None, "r2-ch40", text, {}, False, state, bible, outline, no_extract, ask)
    repair_permanent_states(None, "r2-ch40", text, {}, False, state, bible, outline, no_extract, ask)
    repair_cast_consistency(None, "r2-ch40", text, {}, False, state, bible, outline, no_extract, ask)
    repair_opening_style(None, "r2-ch40", text, {}, False, state, bible, outline, tmp_path, no_extract, ask)
    guard_all_repairs(ask, None, "r2-ch40", "顾长风查看木手。", text, state, 40, bible=bible)

    assert stages == [
        "r2-ch40-plot-replay-review",
        "r2-ch40-permanent-review",
        "r2-ch40-conflict-continuity-recheck",
    ]

    guard_all_repairs(ask, None, "r2-ch40", text, text, state, 40, bible=bible)
    assert len(stages) == 3
