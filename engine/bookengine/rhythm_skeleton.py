"""Plan concrete fight and non-fight content for a whole stage before detailed windows."""

from copy import deepcopy

from .chapter_rhythm import FOCUS, VERSION, rhythm_slots
from .q1_prepare import _ask
from .q1_schema import integer, rows


def _fallback(slot):
    focus = slot["rhythm_focus"]
    return {
        "number": slot["number"],
        "type": slot["rhythm_type"],
        "conflict": focus,
        "result": focus,
        "scenes": [
            {"goal": focus, "obstacle": "既有矛盾阻碍选择", "action": focus, "result": "人物选择产生后果"},
            {"goal": "承接前一场后果", "obstacle": "代价或分歧仍在", "action": focus, "result": "改变下一步选择"},
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


def bind_stage_content(chapters, skeleton):
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
        chapter.update(
            type=kind,
            rhythm_type=kind,
            rhythm_focus=anchor.get("content_requirement") or chapter.get("rhythm_focus", ""),
            opening_conflict=conflict,
            conflict=conflict,
        )
        detailed = rows(chapter.get("scenes"))
        bound = []
        for index, scene_anchor in enumerate(rows(anchor.get("scenes"))[:2]):
            scene = deepcopy(detailed[index]) if index < len(detailed) else {}
            action = scene_anchor.get("action", "")
            scene.update(
                goal=scene_anchor.get("goal", ""),
                obstacle=scene_anchor.get("obstacle", ""),
                choice=action,
                action=action,
                result=scene_anchor.get("result", ""),
            )
            bound.append(scene)
        chapter["scenes"] = bound or detailed
        result.append(chapter)
    return result


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
        result.append(
            {
                "number": row["number"],
                "type": row.get("type", ""),
                "rhythm_type": row.get("rhythm_type", row.get("type", "")),
                "rhythm_focus": str(row.get("content_requirement", ""))[:100],
                "rhythm_contract_version": VERSION,
                "conflict": str(row.get("conflict", ""))[:80],
                "result": str(row.get("result", ""))[:80],
                "scenes": [
                    {field: str(scene.get(field, ""))[:40] for field in ("goal", "obstacle", "action", "result")}
                    for scene in rows(row.get("scenes"))[:2]
                ],
            }
        )
    return result
