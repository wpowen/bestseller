"""State extraction contract shared by the continuity ledger."""

from .state_values import state_value_contract


def extraction_contract():
    instruction = (
        "\ncontinuity_delta只提取本章逐字引文能证明的变化，不复制旧账本、不从章纲推测。"
        "characters每项name/field/value/quote；field仅life_status/location/injury。"
        "life_status只用alive/missing/dead；location另给travel_status（随行/同行/离队/失踪/留守/死亡/未知）；"
        "injury另给part/side（左/右/双侧/无）/condition/severity。"
        "body_structures仅正文明确身体构造时给name/form/limb_count/limb_name/quote。"
        "consumed_memories只记主角已经永久献祭、再也无法完整想起的记忆，给name/summary/quote；"
        "只是回忆、想起、计划献祭或代价不明一律不记。accidents给name/part/cause/quote，只记本章实际发生的意外。"
        "critical_events只记本章已经真实完成、后文不可否定或改写的关键行动与结果，给summary/quote。"
        "permanent_states只记永久失去或获得的身份、能力、肢体、权限，给name/category/subject/change/summary/quote；"
        "change只用lost/gained。"
        "所有quote必须是正文连续逐字原文，找不到引文就省略该项。"
    )
    schema = {
        "characters": [],
        "body_structures": [],
        "consumed_memories": [],
        "accidents": [],
        "critical_events": [],
        "permanent_states": [],
    }
    value_instruction, value_schema = state_value_contract()
    schema.update(value_schema)
    return instruction + value_instruction, schema
