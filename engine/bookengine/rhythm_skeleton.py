"""Plan concrete fight and non-fight content for a whole stage before detailed windows."""

from copy import deepcopy
import re

from .chapter_rhythm import FOCUS, VERSION, rhythm_slots, relationship_contract
from .opening_promises import opening_contract
from .q1_prepare import _ask
from .q1_schema import integer, rows


def _opening_targets(promises):
    """Keep the clauses of one source entry together under its explicit chapter number."""
    assigned, general = {}, []
    for source in promises if isinstance(promises, list) else [promises]:
        if not isinstance(source, str):
            continue
        chapter = None
        grouped = {}
        for item in opening_contract({"opening_promises": [source]}):
            clause = item["requirement"]
            label = re.match(r"第(\d+)章", clause)
            if label:
                chapter = int(label[1])
            if chapter is None:
                general.append(clause)
            elif chapter in (1, 2, 3):
                grouped.setdefault(chapter, []).append(clause)
        for number, clauses in grouped.items():
            assigned.setdefault(number, []).append("；".join(clauses))
    return {n: list(dict.fromkeys(v)) for n, v in assigned.items()}, list(dict.fromkeys(general))


def assigned_opening_events(promises, number):
    """Return only explicitly chapter-bound plans, without adopting historical outline events."""
    if type(number) is not int or not 1 <= number <= 3:
        return []
    assigned, _ = _opening_targets(promises)
    return assigned.get(number, [])


def opening_plan_sections(promises, start, end, *, include_general=False):
    if start > 3 or not promises:
        return []
    assigned, general = _opening_targets(promises)
    due = [{"章": n, "事件": assigned[n]} for n in sorted(assigned) if start <= n <= end]
    sections = [("开篇指定章事件：人物、动作和结果须落实在该章场景；节奏与章型围绕它安排", due)] if due else []
    if include_general and general:
        sections.append(("未指定单章的开篇承诺", general))
    return sections


def bind_opening_events(bible, chapters, *, from_detail=False):
    """Bind a planning obligation without asserting any manuscript has fulfilled it."""
    if not bible.get("opening_promises") or not any(1 <= integer(c.get("number")) <= 3 for c in chapters):
        return chapters
    assigned, _ = _opening_targets(bible["opening_promises"])
    result = []
    for row in chapters:
        due = assigned.get(integer(row.get("number")))
        if not due:
            result.append(row)
            continue
        # A raw detail is a proposal. Only a scheduled or previously adopted event is a
        # second binding obligation. The untouched planner response retains its proposal.
        prior = None if from_detail else row.get("required_event")
        values = [*due, *(prior.splitlines() if isinstance(prior, str) else [])]
        unique = {}
        for value in values:
            if key := value.strip().rstrip("。；;"):
                unique.setdefault(key, value.strip())
        view = dict(row, required_event="\n".join(unique.values()))
        note = view.get("structure_rewrite")
        if isinstance(note, str) and note:
            scope = "结构只组织本章呈现，保留指定事件的人物、动作与结果。"
            if note == f"指定结构{row.get('structure')}优先覆盖冲突旧场景，必须围绕它重新设计行动后果。":
                view["structure_rewrite"] = f"指定结构{row['structure']}。{scope}"
            elif not note.endswith(scope):
                view["structure_rewrite"] = note + scope
        result.append(view)
    return result


def _fallback(slot):
    focus = slot["rhythm_focus"]
    return {
        "number": slot["number"],
        "type": slot["rhythm_type"],
        "conflict": focus,
        "result": focus,
        "scenes": [
            {
                "goal": focus,
                "obstacle": "既有矛盾阻碍选择",
                "choice": "人物在两个有代价的选项中明确取舍",
                "action": focus,
                "result": "人物选择产生后果",
            },
            {
                "goal": "承接前一场后果",
                "obstacle": "代价或分歧仍在",
                "choice": "另一人物接受或拒绝前一场选择",
                "action": focus,
                "result": "改变下一步选择",
            },
        ],
    }


def coordinate_core_rhythm(preferred, core_slots):
    """Put unconstrained required ability uses in action chapters, preserving mandatory rhythm breaks."""
    result = deepcopy(rows(preferred))
    for slot in result:
        core = core_slots.get(integer(slot.get("number")), {})
        if not core.get("core_required") or slot.get("rhythm_reason"):
            continue
        slot.update(type="对抗", rhythm_type="对抗", rhythm_focus=FOCUS["对抗"])
    return result


def bind_stage_content(chapters, skeleton, bible=None):
    """Expand a stage skeleton without letting detailed outlines replace its main actions."""
    anchors = {integer(row.get("number")): row for row in rows(skeleton)}
    result = []
    for source in rows(chapters):
        chapter = deepcopy(source)
        anchor = anchors.get(integer(chapter.get("number")))
        if not anchor:
            result.append(chapter)
            continue
        kind = anchor.get("rhythm_type") or anchor.get("type") or chapter.get("type", "")
        conflict = anchor.get("conflict") or chapter.get("opening_conflict") or chapter.get("conflict", "")
        relationship = anchor.get("relationship_change", chapter.get("relationship_change"))
        if kind == "情感" and not relationship_contract(anchor) and relationship_contract(chapter):
            relationship = chapter["relationship_change"]
        chapter.update(
            type=kind,
            rhythm_type=kind,
            rhythm_focus=anchor.get("content_requirement") or chapter.get("rhythm_focus", ""),
            opening_conflict=conflict,
            conflict=conflict,
            relationship_change=deepcopy(relationship),
        )
        detailed = rows(chapter.get("scenes"))
        bound = []
        for index, scene_anchor in enumerate(rows(anchor.get("scenes"))[:2]):
            scene = deepcopy(detailed[index]) if index < len(detailed) else {}
            scene.update(
                goal=scene_anchor.get("goal", ""),
                obstacle=scene_anchor.get("obstacle", ""),
                choice=scene_anchor.get("choice", ""),
                action=scene_anchor.get("action", ""),
                result=scene_anchor.get("result", ""),
            )
            bound.append(scene)
        chapter["scenes"] = bound or detailed
        result.append(chapter)
    return bind_opening_events(bible or {}, result, from_detail=True)


def stage_content_skeleton(client, bible, volume, history, preferred):
    """Return the complete stage skeleton and the detailed window's rhythm slots."""
    preferred = rows(preferred)
    numbers = [integer(row.get("number")) for row in preferred if integer(row.get("number"))]
    if not numbers:
        return [], []
    stage_start = integer(volume.get("start"), min(numbers))
    stage_end = integer(volume.get("end"), max(numbers))
    stage_preferred = [
        dict(row, type=row.get("type") or row.get("rhythm_type"))
        for row in [*rows(history), *preferred]
        if stage_start <= integer(row.get("number")) <= stage_end
    ]
    slots = rhythm_slots(bible, stage_start, stage_end, history, stage_preferred)
    raw = {}
    if getattr(client, "model", None) and getattr(client, "provider", None):
        raw = _ask(
            client,
            "rhythm_skeleton",
            f"rhythm-skeleton-{stage_start}-{stage_end}",
            [
                *opening_plan_sections(bible.get("opening_promises"), stage_start, stage_end),
                ("指定阶段与卷目标", {**deepcopy(volume), "start": stage_start, "end": stage_end}),
                ("整阶段节奏槽位，类型必须落实为内容", slots),
                ("已发生事实与前章摘要，只能承接不能改写", rows(history)[-10:]),
                ("固定故事摘要与人物", [bible.get("summary", ""), bible.get("characters", [])]),
            ],
        )
    source = rows(raw.get("chapters")) if isinstance(raw, dict) else rows(raw)
    mapped = {integer(row.get("number")): row for row in source}
    skeleton = []
    for slot in slots:
        number = slot["number"]
        row = dict(_fallback(slot), **deepcopy(mapped.get(number, {})))
        row.update(
            number=number,
            type=slot["rhythm_type"],
            rhythm_type=slot["rhythm_type"],
            content_requirement=FOCUS.get(slot["rhythm_type"], slot["rhythm_focus"]),
            _model_planned=number in mapped,
        )
        if slot["rhythm_type"] != "情感":
            row.pop("relationship_change", None)
        row["scenes"] = rows(row.get("scenes"))[:2]
        skeleton.append(row)
    wanted = set(numbers)
    return skeleton, [slot for slot in slots if slot["number"] in wanted]


def window_stage_content(skeleton, start, end):
    """Compact the saved stage skeleton for one detailed-outline request."""
    result = []
    for row in skeleton:
        if not start <= integer(row.get("number")) <= end:
            continue
        value = {
            "number": row["number"],
            "type": row.get("type", ""),
            "rhythm_type": row.get("rhythm_type", row.get("type", "")),
            "rhythm_focus": str(row.get("content_requirement", ""))[:100],
            "rhythm_contract_version": VERSION,
            "conflict": str(row.get("conflict", ""))[:80],
            "result": str(row.get("result", ""))[:80],
            "scenes": [
                {field: str(scene.get(field, ""))[:40] for field in ("goal", "obstacle", "choice", "action", "result")}
                for scene in rows(row.get("scenes"))[:2]
            ],
        }
        if row.get("relationship_change"):
            value["relationship_change"] = deepcopy(row["relationship_change"])
        result.append(value)
    return result
