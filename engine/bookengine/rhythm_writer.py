"""Plain-language rhythm guidance passed to the prose writer."""

from .chapter_rhythm import chapter_type


NONCOMBAT = {"情感", "日常", "收获", "揭秘", "谋划"}
FOCUS = {
    "情感": "两场戏都围绕关系变化；调查、开门、救援或能力使用只能作背景，不能占一整场。",
    "日常": "两场戏只写休整、照料、分配或生活行进，不展开路线论证、机关破解或战斗。",
    "收获": "两场戏实际领取、试用或分配所得，不再争夺，也不展开新调查。",
    "揭秘": "两场戏都用现场线索和证据核对并推翻旧判断，不能改写成路线分工或战斗。",
    "谋划": "两场戏只比较方案、风险、代价并完成取舍与分工；救援、开门、追踪或借力等执行动作移到相邻章。",
}


def writer_rhythm_instruction(outline):
    kind = chapter_type(outline) or outline.get("rhythm_type") or "未定"
    instruction = f"\n本章章型：{kind}。"
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
            "两场主场景都要让这个变化通过选择与反应发生，不能只在结尾补一句关系总结。"
        )
    return instruction
