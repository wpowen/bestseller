"""Project the prepared Q1 chapter to the facts and choices a prose writer needs."""

import json

from .choice_cost import HEADING as CHOICE_COST_HEADING
from .disclosure_choices import HEADING as DISCLOSURE_HEADING
from .longmemory import compact_state
from .q1_schema import rows
from .story_payoffs import due_story_payoffs
from .text import pack


_SCENE_FIELDS = ("where", "pov", "characters", "goal", "obstacle", "choice", "action", "result")
_PLAN_FIELDS = ("number", "title", "transition", "must_respond", "closing", "callback_events", "introductions")
_PERSON_FIELDS = (
    "name",
    "identity_keywords",
    "voice",
    "gender",
    "pronoun",
    "address",
    "aliases",
    "address_origin",
    "address_target",
    "current",
)


def _present_values(row, keys):
    return {key: row[key] for key in keys if row.get(key)}


def _story_material(outline, contract, planned):
    chapter = _present_values(planned, _PLAN_FIELDS)
    chapter["场面"] = [_present_values(scene, _SCENE_FIELDS) for scene in rows(outline.get("scenes"))]
    due = [item["requirement"] for item in planned.get("本章承诺验收", [])]
    due.extend(item["text"] for item in rows(outline.get("story_duties")) if item.get("text"))
    due.extend(item["requirement"] for item in due_story_payoffs(outline))
    if due:
        chapter["本章须发生"] = list(dict.fromkeys(due))
    if contract.get("relationship_change"):
        chapter["当场关系变化"] = contract["relationship_change"]
    if contract.get("人物据以判断的现场细节"):
        chapter["现场线索"] = contract["人物据以判断的现场细节"]
    if outline.get("core_required") and outline.get("core_application"):
        application = outline["core_application"]
        scene_moves = {
            f"{scene['action'].rstrip('。；')}；{scene.get('result', '')}"
            for scene in chapter["场面"]
            if isinstance(scene.get("action"), str)
        }
        if application not in scene_moves:
            chapter["此处能力怎样使用"] = application
    growth = outline.get("growth_obligation")
    if isinstance(growth, dict) and growth.get("required_action"):
        chapter["本章能力变化"] = growth["required_action"]
    if outline.get("growth_setup"):
        chapter["能力边界"] = "本章写出当前能力受限或蓄势，不在本章升级。"
    if outline.get("upgrade_due") and outline.get("next_upgrade"):
        chapter["本章升级至"] = outline["next_upgrade"]
    if outline.get("ending"):
        chapter["收尾"] = "结清现有矛盾，写到人物行动的结果，不另起悬念。"
    elif outline.get("ending_content"):
        chapter["收尾发生的事"] = outline["ending_content"]
    elif contract.get("hook"):
        hook = contract["hook"]
        if "；具体内容：" in hook:
            hook = hook.split("；具体内容：", 1)[1].split("。只用本章", 1)[0]
        chapter["收尾发生的事"] = hook
    return chapter


def compact_prompt(book, outline, contract, sections, state, previous, pending, low, high):
    material = dict(sections)
    planned_json, _, stage_json = material["本章章纲"].partition("\n阶段退场约束：")
    planned = json.loads(planned_json)
    chapter = _story_material(outline, contract, planned)
    beats = rows(json.loads(material["本章推进线"]))
    if mandatory := [beat["event"] for beat in beats if beat.get("must_appear") and beat["event"]]:
        chapter["到期的剧情线"] = mandatory
    if later := [
        beat["event"] for beat in beats if beat.get("reveal_not_before", 0) > outline["number"] and beat["event"]
    ]:
        chapter["本章先不揭晓"] = later
    scene_text = pack(chapter)
    prior = [text for heading, text in sections if heading == "上章结尾" and text.strip() != "{}"]
    if previous:
        tail = previous.rstrip().rsplit("\n\n", 3)[-3:]
        prior = ["上章末尾原文（接写时不重述）\n" + "\n\n".join(tail)[-650:], *prior]
    if pending:
        prior.append(pending)
    for heading in (CHOICE_COST_HEADING, DISCLOSURE_HEADING, "上次交谈的原话", "人物当前事实，不得违背"):
        if material.get(heading):
            prior.append(material[heading])
    # Keep the current status of relevant objects, not a list of objects awaiting explanation.
    snapshot = compact_state(state)
    relevant_text = scene_text + (previous or "")[-650:]
    holders = {name: obj for name, obj in snapshot["objects"].items() if name in relevant_text}
    continuity = {
        **_present_values(planned, ("开场必须承接", "上章地点未知")),
        **({"时刻": snapshot["clock"]} if snapshot.get("clock") else {}),
        **({"相关物件现况": holders} if holders else {}),
    }
    if stage_json:
        stage = json.loads(stage_json)
        constraints = _present_values(stage, ("retired", "departed_locations"))
        if constraints:
            continuity["已退场的对手仅可回忆，已离开的主地点不重复作为章末舞台"] = constraints
    if continuity:
        prior.append(pack(continuity))

    dossier = json.loads(material["出场人物档案、口吻卡与当前状态"])
    people = [_present_values(person, _PERSON_FIELDS) for person in rows(dossier.get("characters"))]
    hero = next((person for person in rows(dossier.get("characters")) if "power" in person), {})
    settings = _present_values(hero, ("dashboard", "power_status", "realm_ability", "continuity"))
    if power := _present_values(hero.get("power", {}), ("name", "object", "rule_points")):
        settings["能力"] = power
    card = material.get("用户选定事实，只可扩写不可替换；规则只给要点")
    if card:
        card = json.loads(card)
        if not power and card.get("核心规则要点"):
            settings["能力规则"] = card["核心规则要点"]
        settings.update(_present_values(card, ("title",)))
        if outline["number"] == 1:
            settings.update(_present_values(card, ("premise", "opening")))

    cast = []
    if people or settings:
        cast.append(pack({"人物": people, "设定与当前能力": settings}))
    if speakers := outline.get("speaker_briefs"):
        cast.append("开口人物当场想争取的事\n" + pack(speakers))
    for heading in ("非人角色表达限制", "授权参考的统计风格特征"):
        if material.get(heading):
            cast.append(material[heading])

    instruction = (
        f"写第{outline['number']}章小说正文，目标{book.words_per_chapter}汉字，范围{low}—{high}。"
        "按材料写出人物当场的选择和结果，已发生的事以上章正文及保留事实为准。"
        "对白、动作与段落长短由场面决定；意思已经写清的地方不用再解释。"
        "不要照抄材料的句子。只交正文，用空行转场。"
    )
    return instruction, [
        ("上章与当下事实", "\n\n".join(dict.fromkeys(prior))),
        ("本章章纲", scene_text),
        ("本场人物与设定", "\n\n".join(cast)),
    ]
