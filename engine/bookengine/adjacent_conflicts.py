"""Find copied scene mechanisms before writing the next chapter."""

from difflib import SequenceMatcher
from pathlib import Path
import re

from .storage import load


RULE = (
    "相邻章可以延续同一人物、地点与能力，但后一章要从前章后果出发，安排不同的当场目的、阻碍、选择及结果。"
    "禁止只换台阶、地名或措辞又写一次相同的受阻、解法和代价。保留人物、伏笔、到期兑现、能力规则与章末既定结果。"
)


def _scenes(row):
    scenes = row.get("scenes")
    if not isinstance(scenes, list):
        scenes = row.get("planned_scenes", [])
    return [scene for scene in scenes if isinstance(scene, dict)]


def _texts(row, *, actions):
    fields = ("action", "choice") if actions else ("conflict", "opening_conflict")
    sources = _scenes(row) if actions else [row]
    return list(
        dict.fromkeys(
            value.strip() for source in sources for field in fields if isinstance(value := source.get(field), str)
        )
    )


def _copied(left, right):
    a, b = (re.sub(r"[^\w\u3400-\u9fff]", "", value) for value in (left, right))
    shorter = min(len(a), len(b))
    if shorter < 18:
        return False
    if a == b:
        return True
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    return matcher.find_longest_match().size >= 24 and matcher.ratio() >= 0.75


def adjacent_conflict_issues(history, batch):
    """Report only substantial copied actions or conflict descriptions in neighboring plans."""
    plans = {
        row["number"]: row for row in [*history, *batch] if isinstance(row, dict) and type(row.get("number")) is int
    }
    issues = []
    for row in batch:
        number = row.get("number")
        if type(number) is not int or number - 1 not in plans:
            continue
        previous = plans[number - 1]
        pairs = (
            (left, right)
            for actions in (True, False)
            for left in _texts(previous, actions=actions)
            for right in _texts(row, actions=actions)
        )
        repeated = next(((left, right) for left, right in pairs if _copied(left, right)), None)
        if repeated:
            issues.append(
                dict(
                    kind="adjacent_conflict",
                    targets=[number],
                    chapters=[number - 1, number],
                    previous=repeated[0][:220],
                    current=repeated[1][:220],
                    requirement=RULE,
                )
            )
    return issues


def planned_conflict(row):
    return {
        "章号": row.get("number"),
        "已记录结果": str(row.get("summary") or "")[:200],
        "场景行动": [str(scene.get("action") or scene.get("choice") or "")[:120] for scene in _scenes(row)[:2]],
    }


def previous_conflict_context(out, chapter):
    """Give the writer a compact reference to the preceding completed chapter."""
    if chapter < 2:
        return []
    number = chapter - 1
    path = Path(out) / f"chapters/{number:03d}.json"
    previous = load(path) if path.is_file() else {}
    if (
        previous.get("fact_source") != "final_body"
        or previous.get("status") != "complete"
        or previous.get("state_failed")
    ):
        return []
    outline = Path(out) / f"writing_outlines/{number:03d}.json"
    plan = load(outline) if outline.is_file() else {}
    summary = planned_conflict(dict(plan, number=number, summary=previous.get("summary")))
    lines = [f"第{number}章实际结果：{summary['已记录结果']}。"]
    if summary["场景行动"]:
        lines.append("上章原定动作（仅供避重）： " + "；".join(summary["场景行动"]))
    return [("上章冲突及写作避重约束", "\n".join([*lines, RULE, "正文直接从后果推进。"]))]
