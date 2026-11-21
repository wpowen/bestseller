"""Instruction 81: the opponent-run rewrite kept the same antagonist.

In the 《扫雪剑起》 re-run (books/webui/5d560fa1-…) the run check flagged 韩正谊 for 23–26 and 独孤鹤 for 31–34;
both paid rewrites (responses 286 and 340) still named him as the opponent, because every stage has a single
antagonist and the planner had no other pressure source to use. The planner now gets a per-chapter schedule
and named alternatives; the rewrite requirement lists the same alternatives.
"""

import json

from bookengine.arc_runs import (
    RUN_LIMIT,
    alternatives,
    opponent_run_issues,
    opponent_schedule,
    planner_obstacle_section,
)

BIBLE = dict(
    antagonists=[dict(name="韩正谊", stage=3, exit=30), dict(name="独孤鹤", stage=4, exit=40)],
    characters=[
        dict(name="顾寒", role="protagonist"),
        dict(name="沈青梧", role="romance", exit_volume=5),
        dict(name="陆大有", role="support", exit_volume=5),
        dict(name="白昀舒", role="support", exit_volume=3),
    ],
    volumes=[
        dict(number=3, start=21, end=30, antagonist="韩正谊", location="山门碑林"),
        dict(
            number=4,
            start=31,
            end=40,
            antagonist="独孤鹤",
            location="青梧剑冢",
            promise_payoffs=[dict(chapter=39, target="独孤鹤")],
        ),
    ],
)


def _off(plan):
    return [row["章号"] for row in plan if "不正面出手" in row["正面阻碍"]]


def _longest_front_run(plan):
    best = run = 0
    for row in plan:
        run = 0 if "不正面出手" in row["正面阻碍"] else run + 1
        best = max(best, run)
    return best


def test_schedule_keeps_runs_at_three_and_forced_chapters_on_stage(tmp_path):
    plan = opponent_schedule(BIBLE, tmp_path, 31, 40, villain_chapters={32, 35})
    assert _longest_front_run(plan) <= RUN_LIMIT
    off = _off(plan)
    assert off and not {32, 35, 39, 40}.intersection(off)


def test_schedule_continues_a_run_from_saved_outlines(tmp_path):
    folder = tmp_path / "writing_outlines"
    folder.mkdir()
    for number in (23, 24, 25):
        (folder / f"{number:03d}.json").write_text(
            json.dumps(dict(number=number, opponent="韩正谊"), ensure_ascii=False)
        )
    plan = opponent_schedule(BIBLE, tmp_path, 26, 30)
    assert _off(plan)[0] == 26
    assert "沈青梧" in plan[0]["正面阻碍"] or "陆大有" in plan[0]["正面阻碍"]
    [(heading, body)] = planner_obstacle_section(BIBLE, tmp_path, 26, 30)
    assert "正面阻碍" in heading and "不能是他本人" in body["规则"]


def test_alternatives_name_allies_place_and_henchmen_but_not_retired_allies():
    options = alternatives(BIBLE, 34)
    text = "；".join(options)
    assert "青梧剑冢" in text and "独孤鹤手下" in text and "白昀舒" not in text
    assert any("沈青梧" in o or "陆大有" in o for o in options)
    assert alternatives(BIBLE, 33) != alternatives(BIBLE, 34)


def test_rewrite_requirement_forbids_the_name_and_lists_alternatives():
    history = [dict(number=n, opponent="独孤鹤") for n in (31, 32, 33)]
    [issue] = opponent_run_issues(BIBLE, history, [dict(number=34, opponent="独孤鹤")])
    assert "opponent不得写独孤鹤" in issue["requirement"] and "青梧剑冢" in issue["requirement"]


def test_no_section_without_antagonists_or_when_nothing_is_off_stage(tmp_path):
    assert planner_obstacle_section(dict(antagonists=[]), tmp_path, 1, 5) == []
    assert planner_obstacle_section(BIBLE, tmp_path, 31, 33) == []


def test_structure_repair_target_names_the_meta_word():
    from bookengine.text_hygiene import _repair_targets, inspect_text

    text = "沈青梧取出上章撕存的血布。\n\n顾寒点头。"
    findings = inspect_text(text)
    [target] = [row for row in _repair_targets(text, findings, {}) if "structure" in row["kind"]]
    assert target["words"] == ["上章"] and "先前" in target["fix"]
