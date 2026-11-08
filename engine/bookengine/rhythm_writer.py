"""Plain-language rhythm guidance passed to the prose writer."""

from .chapter_rhythm import chapter_type


NONCOMBAT = {"情感", "日常", "收获", "揭秘", "谋划"}
FOCUS = {
    "情感": "两场戏都围绕关系变化，让人物的坦白、拒绝或让步改变后续选择。",
    "日常": "两场戏写休整、照料或生活选择，并让人物处境发生具体变化。",
    "收获": "两场戏写实际领取、试用或分配所得，不重复上一场争夺。",
    "揭秘": "两场戏用线索和证据推翻旧判断，让人物据此改变下一步。",
    "谋划": "两场戏比较方案、风险和代价，作出有取舍的分工，暂不执行战斗。",
}


def writer_rhythm_instruction(outline):
    kind = chapter_type(outline) or outline.get("rhythm_type") or "未定"
    instruction = f"\n本章章型：{kind}。"
    if kind in NONCOMBAT:
        instruction += (
            "本章不安排正面打斗；对手只能作为消息或远处压力，不得在两场戏中到场动手、追杀、突围或反杀。" + FOCUS[kind]
        )
    return instruction
