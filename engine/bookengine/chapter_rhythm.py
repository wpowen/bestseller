"""Shared future scene obligations; never classify a plan as an observed event."""

from copy import deepcopy
import re

from .q1_schema import integer, rows

VERSION = 7
BREAK_TYPES = ("收获", "情感", "揭秘", "谋划")
RULE = (
    "连续对抗最多3章，随后必须以收获/人物关系/揭秘/谋划之一为主章，日常标签不能代替。"
    "每阶段至少1章人物关系主章，情感即关系；阶段第4章前预留，短阶段在尾章落实。"
    "rhythm_type和rhythm_focus须落实到多数场景的goal/choice，不能仅改type标签或附加一场关系戏。"
)
FOCUS = {
    "情感": "以人物关系为主：围绕既有冲突中的隐瞒或分歧，坦白各自顾虑，让对方作出信任或疏远的选择并改变分工",
    "收获": "以收获为主：兑现既有行动赢得的资源或认可，安排人物实际领取、试用并选择如何分配，不重复争夺过程",
    "揭秘": "以揭秘为主：核对既有线索的矛盾，让人物推翻一个判断，以证据揭示局部真相并改变下一步选择，不提前揭底",
    "谋划": "以谋划为主：围绕既有目标提出不同方案，比较风险代价并选定分工，以有取舍的准备行动结束，暂不执行新一轮对抗",
    "对抗": "以当下阻力和行动选择推动主要冲突，保留后续收获、关系、揭秘或谋划的换气空间",
}
STRUCTURES = {"情感": "情感摊牌", "收获": "日常喘息里藏冲突", "揭秘": "误判后果", "谋划": "对峙谈判"}
_CUES = {
    "情感": r"关系|信任|坦白|隐瞒|原谅|疏远|和解|顾虑|承诺|分歧|边界|分工|心意|感情",
    "收获": r"兑现|领取|试用|分配|收获|回报|报酬|奖励|认可|庆祝",
    "揭秘": r"核对|线索|推翻|判断|证据|揭示|真相|验证|发现|秘密",
    "谋划": r"方案|风险|代价|分工|筹备|准备|计划|谋划|策略|布局",
}
_COMBAT = re.compile(r"击败|击退|围攻|围堵|拆阵|破阵|夺门|追杀|搏杀|交战|厮杀|拔剑|挥剑|偷袭|斩杀")
INTERNAL_TERMS = ("原章", "原定场景", "原定目标", "已登场同伴")
_WRITER_FIELDS = (
    "title",
    "opening_conflict",
    "conflict",
    "payoff",
    "hook",
    "ending_content",
    "rhythm_focus",
    "scenes",
)


def chapter_type(row):
    value = row.get("type", "")
    return "情感" if value == "关系" else value


def _stages(bible):
    return [s for s in rows(bible.get("volumes")) if 0 < integer(s.get("start")) <= integer(s.get("end"))]


def _focused(row, kind):
    """Conservative scene intent check, not a semantic or reader acceptance score."""
    scenes = rows(row.get("scenes"))
    if not scenes or kind not in _CUES:
        return False
    matches = []
    for scene in scenes:
        intention = "；".join(str(scene.get(k) or "") for k in ("goal", "action", "choice", "result"))
        # Ignore explicit prohibitions; a battle mentioned only in the obstacle is not the main action.
        intention = re.sub(r"(?:不再|禁止|不能|不要|不靠|暂不)[^；。！？]*", "", intention)
        matches.append(bool(re.search(_CUES[kind], intention)) and not _COMBAT.search(intention))
    return matches[0] and sum(matches) > len(matches) // 2


def _relation_seen(previous, stage):
    return any(
        integer(stage["start"]) <= n <= integer(stage["end"])
        and chapter_type(row) == "情感"
        # Completed extractions can have scene counts instead of full scene plans.
        and (_focused(row, "情感") if isinstance(row.get("scenes"), list) else True)
        for n, row in previous.items()
    )


def _requirement(bible, previous, number):
    stage = next((s for s in _stages(bible) if integer(s["start"]) <= number <= integer(s["end"])), None)
    if stage and number >= min(integer(stage["start"]) + 3, integer(stage["end"])):
        if not _relation_seen(previous, stage):
            return ("情感",), "rhythm_stage_relation"
    if all(chapter_type(previous.get(n, {})) == "对抗" for n in range(number - 3, number)):
        return BREAK_TYPES, "rhythm_combat_run"
    return (), ""


def rhythm_slots(bible, start, end, history=(), preferred=()):
    """Use the same obligation function as validation, carrying real cross-window history."""
    previous = {integer(r.get("number")): r for r in history if integer(r.get("number")) < start}
    preferred = {integer(r.get("number")): r for r in preferred}
    result = []
    for n in range(start, end + 1):
        allowed, reason = _requirement(bible, previous, n)
        kind = chapter_type(preferred.get(n, {})) or "对抗"
        if allowed and kind not in allowed:
            kind = allowed[0]
        slot = dict(
            number=n,
            rhythm_type=kind,
            rhythm_focus=FOCUS.get(kind, "保留本章主场景行动与人物选择"),
            rhythm_contract_version=VERSION,
            rhythm_reason=reason,
        )
        result.append(slot)
        # Slots are reservations only; this synthetic scene records intent, not an observed relationship.
        previous[n] = dict(type=kind, scenes=[dict(goal=slot["rhythm_focus"])])
    return result


def rhythm_issues(bible, history, batch):
    targets = {integer(r.get("number")) for r in batch}
    combined = {integer(r.get("number")): r for r in [*history, *batch]}
    previous, issues = {}, []
    for n, row in sorted(combined.items()):
        kind = chapter_type(row)
        if n in targets:
            if not any(field in row for field in ("type", "rhythm_type", "scenes", "opening_conflict")):
                previous[n] = row
                continue
            allowed, reason = _requirement(bible, previous, n)
            if allowed and kind not in allowed:
                issues.append(dict(kind=reason, targets=[n], allowed=list(allowed), requirement=RULE))
            elif kind in BREAK_TYPES and not _focused(row, kind):
                issues.append(dict(kind="rhythm_scene_focus", targets=[n], allowed=[kind], requirement=RULE))
            terms = internal_terms(row)
            if terms:
                issues.append(
                    dict(
                        kind="rhythm_internal_language",
                        targets=[n],
                        terms=terms,
                        allowed=[kind] if kind else [],
                        requirement="程序内部用语不得进入交给写手的章纲。" + RULE,
                    )
                )
        previous[n] = row
    return issues


def internal_terms(row):
    """Return forbidden planner-only terms from fields passed to the prose writer."""
    text = repr({field: row.get(field) for field in _WRITER_FIELDS if field in row})
    return [term for term in INTERNAL_TERMS if term in text]


def repair_rhythm(bible, history, batch):
    """Keep unresolved plans unchanged; model-backed replanning is handled by the caller."""
    current = deepcopy(batch)
    decisions = [
        dict(
            number=number,
            reason=[issue for issue in rhythm_issues(bible, history, current) if number in issue["targets"]],
            action="kept_original",
            authority="planning_instruction_not_observed_fact",
        )
        for number in sorted(
            {target for issue in rhythm_issues(bible, history, current) for target in issue.get("targets", [])}
        )
    ]
    return current, decisions


def apply_contract(chapter):
    """Keep accepted main-scene intent through normalization, overriding conflicting structure slots."""
    kind = chapter_type(chapter)
    chapter.update(
        type=kind,
        rhythm_type=kind,
        rhythm_focus=chapter.get("rhythm_focus") or FOCUS.get(kind, "保留本章主场景行动与人物选择"),
        rhythm_contract_version=VERSION,
    )
    if kind in BREAK_TYPES:
        chapter.update(
            structure=STRUCTURES[kind],
            structure_rewrite="本章主场景优先约束：" + chapter["rhythm_focus"],
        )
    return chapter
