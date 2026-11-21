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
        issues.append(dict(kind="opponent_run", opponent=name, chapters=run, targets=[candidates[0]], requirement=RULE))
    return issues


def _recent_rows(out, start):
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
        for row in _recent_rows(out, start)
    ]
    table = [row for row in table if any(value for key, value in row.items() if key != "章号")]
    return [(PLANNER_HEADING, dict(规则=PLANNER_RULE, 最近章节=table))] if table else []
