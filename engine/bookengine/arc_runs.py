"""Instruction 80: one antagonist carried a whole stage, and finished outcomes were planned again.

In 《扫雪剑起》 (books/webui/24ddbcc8-…) 孟长夜 was the direct opponent of chapters 31–35 and 37–40, and
闻白帝 of chapters 45–49; the re-test book (380d3542-…) had 韩阙 for chapters 15–20. Readers felt the same fight
written over and over. The 剑心 was shattered in chapter 45 and again in chapter 49.

The run check is deterministic and only names chapters in the current batch; the rewrite keeps the
antagonist in the background. The planner context lists what recent chapters already used and finished.
"""

import json
from pathlib import Path

from .q1_schema import integer, rows


RUN_LIMIT = 3
RECENT = 8
RULE = (
    "同一个对手最多连续三章当正面阻碍。把这一章的正面阻碍换成别的：另一个人、队伍内部的分歧、"
    "资源或时间的压力、或人物自己必须做的选择；这个对手可以在背景里推进计划，但本章不正面出手。"
    "保留本章章型、到期义务与章末结果。"
)
ALLY_ROLES = ("support", "romance")
SCHEDULE_HEADING = "本批各章的正面阻碍安排"
SCHEDULE_RULE = (
    "阶段反派最多连续三章正面出手。标了“不正面出手”的章，opponent字段和两场场景的obstacle都不能是他本人，"
    "从给出的备选里选一个当正面阻碍；他可以在背景里推进计划，章末结果和到期义务不变。"
)
PLANNER_HEADING = "最近几章已经用过的对手、地点与破局办法"
PLANNER_RULE = (
    "同一个对手最多连续三章当正面阻碍；同一种破局办法（借地形、借落剑、借同一件器物）最多连用两章。"
    "表里已经攻破、摧毁、夺回或解决的目标，后面章节不能再攻破一次；确实没拿下的，章纲里写明上次为什么没成。"
)


def antagonist_names(bible):
    return [
        row["name"]
        for row in rows(bible.get("antagonists"))
        if isinstance(row.get("name"), str) and row["name"].strip()
    ]


def direct_opponent(row, names):
    """The antagonist a chapter plan names as its opponent; '' when none or ambiguous."""
    value = row.get("opponent")
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) if value else ""
    found = [name for name in names if name in text]
    return found[0] if len(found) == 1 else ""


def _protected(row, name):
    """Chapters that must keep the antagonist on stage: his scheduled exit or a planned villain move."""
    resolution = row.get("opponent_resolution")
    if isinstance(resolution, dict) and resolution.get("name") == name:
        return True
    return any(
        duty.get("kind") == "villain_move" and name in (duty.get("names") or [])
        for duty in rows(row.get("story_duties"))
    )


def _exit_chapters(bible):
    return {
        integer(row.get("exit")): row.get("name") for row in rows(bible.get("antagonists")) if integer(row.get("exit"))
    }


def stage_row(bible, number):
    return next(
        (row for row in rows(bible.get("volumes")) if integer(row.get("start")) <= number <= integer(row.get("end"))),
        {},
    )


def alternatives(bible, number):
    """Concrete pressure sources other than the stage antagonist himself, for chapters he stays off stage."""
    stage = stage_row(bible, number)
    name, place = stage.get("antagonist"), stage.get("location")
    stage_number = integer(stage.get("number"))
    allies = [
        card["name"]
        for card in rows(bible.get("characters"))
        if card.get("role") in ALLY_ROLES
        and isinstance(card.get("name"), str)
        and integer(card.get("exit_volume"), stage_number) >= stage_number
    ]
    # Rotate so consecutive off-stage chapters do not all lean on the same ally.
    allies = [allies[(number + index) % len(allies)] for index in range(min(2, len(allies)))] if allies else []
    options = [f"{ally}与主角在做法上的分歧，或{ally}提出的要求" for ally in allies]
    if isinstance(place, str) and place.strip():
        options.append(f"{place.strip()}本身的险阻、时限或缺口")
    if isinstance(name, str) and name:
        options.append(f"{name}手下的人或他事先布下的局（他本人不出面）")
    options.append("主角自己必须当场作出的取舍")
    return options


def _forced(bible, stage, name, villain_chapters):
    payoffs = {integer(row.get("chapter")) for row in rows(stage.get("promise_payoffs")) if row.get("target") == name}
    exits = {number for number, who in _exit_chapters(bible).items() if who == name}
    return {*villain_chapters, *payoffs, *exits}


def _before_run(out, start, name):
    previous = {integer(row.get("number")): row for row in recent_rows(out, start)}
    count = 0
    while count < RUN_LIMIT and direct_opponent(previous.get(start - 1 - count, {}), [name]) == name:
        count += 1
    return count


def opponent_schedule(bible, out, start, end, villain_chapters=()):
    """Which chapters of the window the stage antagonist may face the hero directly, at most three in a row."""
    names = antagonist_names(bible)
    plan, run, current = [], 0, ""
    for number in range(start, end + 1):
        stage = stage_row(bible, number)
        name = stage.get("antagonist")
        if name not in names:
            continue
        if name != current:
            current, run = name, _before_run(out, number, name) if number == start else 0
        exit_chapter = integer(_exit_by_name(bible).get(name))
        if exit_chapter and number > exit_chapter:
            continue
        forced = _forced(bible, stage, name, villain_chapters)
        after = 0
        while number + 1 + after in forced:
            after += 1
        front = number in forced or run + 1 + after <= RUN_LIMIT
        run = run + 1 if front else 0
        if front:
            plan.append(dict(章号=number, 正面阻碍=f"{name}可以正面出手"))
        else:
            options = "；".join(alternatives(bible, number))
            plan.append(
                dict(章号=number, 正面阻碍=f"{name}本章不正面出手，只在背景推进；opponent写成下面之一：{options}")
            )
    return plan


def planner_obstacle_section(bible, out, start, end, villain_chapters=()):
    plan = opponent_schedule(bible, out, start, end, villain_chapters)
    if not any("不正面出手" in row["正面阻碍"] for row in plan):
        return []
    return [(SCHEDULE_HEADING, dict(规则=SCHEDULE_RULE, 安排=plan))]


def _exit_by_name(bible):
    return {row.get("name"): row.get("exit") for row in rows(bible.get("antagonists"))}


def opponent_run_issues(bible, history, batch):
    names = antagonist_names(bible)
    if not names:
        return []
    plans = {integer(row.get("number")): row for row in [*history, *batch] if integer(row.get("number"))}
    who = {number: direct_opponent(row, names) for number, row in plans.items()}
    exits = _exit_chapters(bible)
    batch_numbers = {integer(row.get("number")) for row in batch}
    issues, flagged = [], set()
    for number in sorted(batch_numbers):
        name = who.get(number)
        if not name:
            continue
        run = [number]
        while who.get(run[0] - 1) == name:
            run.insert(0, run[0] - 1)
        if len(run) <= RUN_LIMIT or flagged.intersection(run):
            continue
        # Changing the first chapter past the limit splits the run; later ones are the fallback.
        candidates = [
            n
            for n in [*run[RUN_LIMIT:], *reversed(run[:RUN_LIMIT])]
            if n in batch_numbers and exits.get(n) != name and not _protected(plans[n], name)
        ]
        if not candidates:
            continue
        flagged.update(run)
        target = candidates[0]
        requirement = (
            RULE + "本章opponent不得写" + name + "，从这些里选一个当正面阻碍：" + "；".join(alternatives(bible, target))
        )
        issues.append(dict(kind="opponent_run", opponent=name, chapters=run, targets=[target], requirement=requirement))
    return issues


def recent_rows(out, start):
    if out is None:
        return []
    root = Path(out) / "writing_outlines"
    result = []
    for number in range(max(1, start - RECENT), start):
        path = root / f"{number:03d}.json"
        try:
            row = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict):
            result.append(row)
    return result


def _short(value, limit=40):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def planner_arc_section(out, start):
    """Recent opponents, places, targets and methods, so the planner can avoid a ninth chapter of the same."""
    table = [
        dict(
            章号=integer(row.get("number")),
            对手=_short(row.get("opponent"), 24),
            地点=_short(row.get("location"), 24),
            破局目标=_short(row.get("solution_target")),
            破局办法=_short(row.get("solution_method")),
        )
        for row in recent_rows(out, start)
    ]
    table = [row for row in table if any(value for key, value in row.items() if key != "章号")]
    return [(PLANNER_HEADING, dict(规则=PLANNER_RULE, 最近章节=table))] if table else []
