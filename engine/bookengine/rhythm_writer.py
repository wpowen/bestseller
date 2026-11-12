"""Plain-language rhythm guidance passed to the prose writer."""

import json

from .chapter_rhythm import chapter_type
from .q1_schema import rows


NONCOMBAT = {"情感", "日常", "收获", "揭秘", "谋划"}
FOCUS = {
    "情感": "两场戏都围绕关系变化；调查、开门、救援或能力使用只能作背景，不能占一整场。",
    "日常": "两场戏只写休整、照料、分配或生活行进，不展开路线论证、机关破解或战斗。",
    "收获": "两场戏实际领取、试用或分配所得，不再争夺，也不展开新调查。",
    "揭秘": "两场戏都用现场线索和证据核对并推翻旧判断，不能改写成路线分工或战斗。",
    "谋划": "两场戏只比较方案、风险、代价并完成取舍与分工；救援、开门、追踪或借力等执行动作移到相邻章。",
}


def writer_main_scene_contract(outline):
    """Exact outline obligations placed before style guidance in the writer prompt."""
    return {
        "chapter_type": chapter_type(outline) or outline.get("rhythm_type") or "未定",
        "rhythm_focus": outline.get("rhythm_focus", ""),
        "hook_type": outline.get("hook_type", ""),
        "hook": outline.get("hook", ""),
        "scenes": [
            {key: scene.get(key, "") for key in ("goal", "action", "choice", "result")}
            for scene in rows(outline.get("scenes"))
        ],
        "relationship_change": outline.get("relationship_change"),
    }


def writer_rhythm_instruction(outline):
    kind = chapter_type(outline) or outline.get("rhythm_type") or "未定"
    contract = json.dumps(writer_main_scene_contract(outline), ensure_ascii=False, separators=(",", ":"))
    instruction = "本章主场景硬合同（优先于文风与局部措辞，必须由两场主场景实际完成）：" + contract
    instruction += f"\n本章章型：{kind}。"
    if kind in NONCOMBAT:
        instruction += (
            "本章不安排正面打斗；对手只能作为消息或远处压力，不得在两场戏中到场动手、追杀、突围或反杀。"
            "若章纲中的局部动作与本章章型冲突，保留必须推进的剧情事实，但不得照抄与章型冲突的动作。" + FOCUS[kind]
        )
    if kind == "情感" and isinstance(outline.get("relationship_change"), dict):
        change = outline["relationship_change"]
        people = "、".join(change.get("people", [])) if isinstance(change.get("people"), list) else ""
        instruction += (
            f"关系变化合同：人物={people}；起因={change.get('cause', '')}；"
            f"变化前={change.get('before', '')}；变化后={change.get('after', '')}。"
            "两场主场景都要把人物关系从变化前推到变化后：坦白、试探、拒绝、让步或和解是主线，"
            "对方必须当场作出不同于action的choice并产生反应，不能只在结尾补一句关系总结。"
            "追杀、闯关、受控搏斗和机关执行最多一笔带过，不能占据主场景。"
        )
    return instruction
