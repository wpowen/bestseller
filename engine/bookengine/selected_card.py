"""User-selected story facts outrank concept tournaments and name suggestions."""

import json
from .q1_schema import model_value
from .storage import save

RULE = "用户已选定故事。以下卡片事实只可扩写，禁止替换、重新选题或改名（包括名字黑名单）。"


def selected_card(book):
    try:
        card = json.loads(book.user_input)
    except (ValueError, TypeError):
        return {}
    return card if isinstance(card, dict) and all(isinstance(card.get(k), str) for k in ("title", "premise")) else {}


def facts(client, card):
    source = json.dumps(card, ensure_ascii=False)
    schema = dict(
        name="",
        identity="",
        setting="",
        object="",
        keywords={k: [""] for k in ("identity", "setting", "object", "rule")},
    )

    def validate(value):
        if not isinstance(value, dict):
            raise ValueError("须返回卡片事实对象")
        for key in ("name", "identity", "setting", "object"):
            text = value.get(key)
            if not isinstance(text, str) or (text and text not in source):
                raise ValueError("事实必须逐字来自卡片：" + key)
        if len(value.get("name", "")) > 8:
            raise ValueError("只提取姓名，不能把职业并入姓名")
        keywords = value.get("keywords", {})
        for key in ("identity", "setting", "object", "rule"):
            terms = keywords.get(key)
            if (
                not isinstance(terms, list)
                or not terms
                or any(not isinstance(t, str) or not t or t not in source for t in terms)
            ):
                raise ValueError("每组关键词须为卡片原文的非空词组：" + key)

    return client.stage(
        "selected-card-facts",
        RULE + "逐字提取主角姓名name（没有姓名留空，后续允许命名）、职业identity、开场地点setting、核心物件object。"
        "未说明的字段留空，不编造。keywords给身份、地点、物件、规则各1至4个关键原文词组；无该项时用卡上“无”或对应原文。"
        "规则词组必须覆盖能力、限制和代价。只返回JSON。卡片：" + source,
        json.dumps(schema, ensure_ascii=False),
        parser=model_value,
        validate=validate,
        max_attempts=6,
        cap=1800,
    )


def differences(bible, card, locked):
    hero = next((c for c in bible.get("characters", []) if c.get("role") == "protagonist"), {})
    errors = []
    if bible.get("title") != card["title"]:
        errors.append("title")
    if locked["name"] and hero.get("name") != locked["name"]:
        errors.append("name")
    targets = dict(identity=hero, setting=bible.get("world"), object=bible.get("power"), rule=bible.get("power"))
    for key, target in targets.items():
        text = json.dumps(target, ensure_ascii=False)
        if any(term not in text for term in locked["keywords"][key]):
            errors.append(key)
    for key in ("opening", "hook"):
        if card.get(key) and bible.get(key) != card[key]:
            errors.append(key)
    if card.get("axes") and bible.get("axes") != card["axes"]:
        errors.append("axes")
    return errors


def enforce(bible, threads, card, locked, drift=()):
    hero = next(c for c in bible["characters"] if c["role"] == "protagonist")
    if locked["name"] and hero["name"] != locked["name"]:
        old = hero["name"]
        content = json.dumps([bible, threads], ensure_ascii=False)
        # Swap a colliding supporting name, including references, before assigning the hero.
        if any(c["name"] == locked["name"] for c in bible["characters"] if c is not hero):
            content = content.replace(locked["name"], "__support_name__")
        content = content.replace(old, locked["name"]).replace("__support_name__", old)
        bible, threads = json.loads(content)
        hero = next(c for c in bible["characters"] if c["role"] == "protagonist")
    hero["identity"] = locked["identity"]
    if locked["identity"]:
        hero["occupation"] = locked["identity"]
    bible.update(title=card["title"], premise=card["premise"], summary=card["premise"], selected_card=card)
    for key in ("opening", "hook", "axes"):
        if key in card:
            bible[key] = card[key]
    if drift:
        # Failed designs must not keep a second mechanism or location beside the fixed fields.
        bible["world"] = dict(levels=bible["world"].get("levels", []), rules=[card["premise"]])
        bible["power"] = {}
        hero.update(desire=card["premise"], goal=card["premise"], address=hero["name"])
        for volume in bible.get("volumes", []):
            for key in list(volume):
                if isinstance(volume[key], str):
                    volume[key] = card["premise"]
        for line in threads.get("threads", []):
            for beat in line.get("beats", []):
                beat["description"] = card.get("engine") or card["premise"]
    bible["world"]["setting"] = locked["setting"]
    rule = card.get("golden_finger_rule", "无")
    bible["power"].update(object=locked["object"], rule=rule)
    if drift:
        bible["power"].update(ability=rule, cost=rule, limit=rule)
    return bible, threads


def record(out, attempts, corrected):
    save(
        out / "card-alignment.json",
        dict(
            attempts=attempts, corrected=corrected, policy="卡片优先；名字精确匹配，其余逐项关键词检查；不代替读者验收"
        ),
    )
