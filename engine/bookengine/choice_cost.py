"""Offer a verified consequence from the adjacent chapter to a scene dealing with the same object."""

from pathlib import Path
import re

from .opening_choice import continues_choice_view
from .q1_schema import rows


HEADING = "上章取舍在本场留下的后果"
_SUBJECT = re.compile(r"([\u3400-\u9fff]{2,16}?)(?:当即|随即|从此|已经|再也|重新|再度|彻底|只剩|便会|就会|无法|再难)")
_CLAUSE = re.compile(r"[，,。；;！？!?\n]")


def choice_cost_context(out, prior, previous, outline):
    number = outline["number"] - 1
    if (
        out is None
        or prior.get("number") != number
        or prior.get("status") != "complete"
        or prior.get("fact_source") != "final_body"
        or prior.get("state_failed")
    ):
        return []
    note = prior.get("polish")
    for key in ("plot_progression", "adjacent_semantic"):
        note = note.get(key) if isinstance(note, dict) else None
    if not isinstance(note, dict) or note.get("status") != "repaired" or note.get("previous_chapter") != number - 1:
        return []
    before, after = note.get("before"), note.get("after")
    if not isinstance(before, dict) or before.get("kind") != "skipped_choice" or not isinstance(after, dict):
        return []
    if after.get("status") != "clear" or after.get("preserves_facts") is not True:
        return []
    choice, action, cost = before.get("prior_choice"), after.get("new_action_quote"), after.get("consequence_quote")
    if not all(isinstance(s, str) and len(s.strip()) >= 6 and "\n" not in s for s in (choice, action, cost)):
        return []
    if not previous or not all(q in previous for q in (action, cost)):
        return []
    try:
        if previous.strip() != (Path(out) / f"chapter-{number:03d}.body.md").read_text(encoding="utf-8").strip():
            return []
    except (OSError, UnicodeError):
        return []
    scenes = rows(outline.get("scenes"))
    if not scenes:
        return []
    actor = next(
        (p for p in (scenes[0].get("characters") or []) if isinstance(p, str) and p and action.startswith(p)), ""
    )
    if not actor or not continues_choice_view(outline, scenes[0], actor):
        return []
    subjects = [
        m[1]
        for clause in _CLAUSE.split(cost)
        if (m := _SUBJECT.match(clause))
        and m[1] in choice
        and not any(p in m[1] for p in prior.get("present") or [] if isinstance(p, str) and p)
    ]
    for i, scene in enumerate(scenes, 1):
        if actor not in (scene.get("characters") or []) or scene.get("pov", actor) != actor:
            continue
        where = scene.get("where") or ""
        moves = [scene.get(k) for k in ("goal", "obstacle", "choice", "action")]
        moves.extend(x.get("goal") for x in rows(scene.get("speaker_intents")))
        for noun in subjects:
            for size in range(len(noun), 1, -1):
                target = noun[:size]
                # A shortened name (e.g. 阵脉 from 阵脉连接点) also needs independent location evidence.
                if (size < len(noun) or size == 2) and target not in where:
                    continue
                goal = next((s for s in moves if isinstance(s, str) and target in s), None)
                if goal:
                    return [
                        (
                            HEADING,
                            f"第{number}章已发生的行动与后果是「{action}」「{cost}」。第{i}场已有的行动是「{goal.rstrip('。')}」。"
                            f"若这里指同一处{target}，且当时的变化仍限制这个行动，把限制落在已有的阻碍或选择里。已解除或无关时略过。"
                            "沿用章纲中已有的因果，不重演上章的选择，不另造危机。",
                        )
                    ]
    return []
