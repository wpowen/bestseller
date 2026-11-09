"""Quote-grounded character continuity facts and bounded repairs."""

from copy import deepcopy


LEDGER_VERSION = 1
_LIFE = {
    "alive": "alive",
    "missing": "missing",
    "dead": "dead",
    "存活": "alive",
    "失踪": "missing",
    "死亡": "dead",
}
_TRAVEL = {"随行", "同行", "离队", "失踪", "留守", "死亡", "未知"}
_LIFE_TEXT = {"alive": "存活", "missing": "失踪", "dead": "死亡"}
_NUMBERS = {2: "两", 3: "三", 4: "四", 5: "五", 6: "六", 8: "八"}


def extraction_contract():
    instruction = (
        "\ncontinuity_delta只提取本章逐字引文能证明的变化，不复制旧账本、不从章纲推测。"
        "characters每项name/field/value/quote；field仅life_status/location/injury。"
        "life_status只用alive/missing/dead；location另给travel_status（随行/同行/离队/失踪/留守/死亡/未知）；"
        "injury另给part/side（左/右/双侧/无）/condition/severity。"
        "body_structures仅正文明确身体构造时给name/form/limb_count/limb_name/quote。"
        "consumed_memories只记主角已经永久献祭、再也无法完整想起的记忆，给name/summary/quote；"
        "只是回忆、想起、计划献祭或代价不明一律不记。accidents给name/part/cause/quote，只记本章实际发生的意外。"
        "所有quote必须是正文连续逐字原文，找不到引文就省略该项。"
    )
    schema = {"characters": [], "body_structures": [], "consumed_memories": [], "accidents": []}
    return instruction, schema


def _cards(bible):
    return {
        card["name"]: card
        for card in bible.get("characters", [])
        if isinstance(card, dict) and isinstance(card.get("name"), str) and card["name"].strip()
    }


def _empty_ledger(bible):
    return {
        "version": LEDGER_VERSION,
        "characters": {name: {} for name in _cards(bible)},
        "consumed_memories": [],
        "accidents": [],
    }


def _ledger(state, bible):
    saved = state.get("continuity_ledger") if isinstance(state, dict) else None
    result = deepcopy(saved) if isinstance(saved, dict) else _empty_ledger(bible)
    result["version"] = LEDGER_VERSION
    result["characters"] = result.get("characters") if isinstance(result.get("characters"), dict) else {}
    for name in _cards(bible):
        result["characters"].setdefault(name, {})
    for key in ("consumed_memories", "accidents"):
        result[key] = result.get(key) if isinstance(result.get(key), list) else []
    return result


def _quote(row, text):
    quote = row.get("quote") if isinstance(row, dict) else None
    return quote.strip() if isinstance(quote, str) and quote.strip() and quote.strip() in text else ""


def _record(value, chapter, quote):
    return {"value": value, "chapter": chapter, "quote": quote}


def _merge_character(ledger, row, text, chapter, cards):
    if not isinstance(row, dict) or row.get("name") not in cards or not (quote := _quote(row, text)):
        return
    name = row["name"]
    target = ledger["characters"].setdefault(name, {})
    field = row.get("field")
    if field == "life_status" and row.get("value") in _LIFE:
        target["life_status"] = _record(_LIFE[row["value"]], chapter, quote)
    elif field == "location" and isinstance(row.get("value"), str) and row["value"].strip():
        target["location"] = {
            **_record(row["value"].strip()[:80], chapter, quote),
            "travel_status": row.get("travel_status") if row.get("travel_status") in _TRAVEL else "未知",
        }
    elif field == "injury":
        part = row.get("part")
        side = row.get("side")
        condition = row.get("condition")
        if not all(isinstance(value, str) and value.strip() for value in (part, condition)):
            return
        side = side.strip() if isinstance(side, str) and side.strip() in {"左", "右", "双侧", "无"} else "无"
        injuries = target.setdefault("injuries", [])
        current = next(
            (item for item in injuries if item.get("part") == part.strip() and item.get("side") == side),
            None,
        )
        start = current.get("start_chapter", chapter) if current else chapter
        item = {
            "part": part.strip()[:20],
            "side": side,
            "condition": condition.strip()[:30],
            "severity": str(row.get("severity") or "未说明")[:30],
            "start_chapter": start,
            "chapter": chapter,
            "quote": quote,
        }
        if current:
            injuries[injuries.index(current)] = item
        else:
            injuries.append(item)


def _merge_body(ledger, row, text, chapter, cards):
    if not isinstance(row, dict) or row.get("name") not in cards or not (quote := _quote(row, text)):
        return
    count = row.get("limb_count")
    if type(count) is not int or not 0 < count <= 20:
        return
    form = row.get("form")
    limb = row.get("limb_name")
    if not all(isinstance(value, str) and value.strip() for value in (form, limb)):
        return
    ledger["characters"].setdefault(row["name"], {})["body"] = {
        "form": form.strip()[:30],
        "limb_count": count,
        "limb_name": limb.strip()[:20],
        "chapter": chapter,
        "quote": quote,
    }


def _protagonist(bible):
    return next((name for name, card in _cards(bible).items() if card.get("role") == "protagonist"), "")


def _merge_memories(ledger, rows, text, bible, chapter):
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not (quote := _quote(row, text)):
            continue
        summary = row.get("summary")
        name = row.get("name") or _protagonist(bible)
        if name not in _cards(bible) or not isinstance(summary, str) or not summary.strip():
            continue
        item = {"name": name, "summary": summary.strip()[:60], "chapter": chapter, "quote": quote}
        if not any(
            old.get("name") == name and old.get("summary") == item["summary"] for old in ledger["consumed_memories"]
        ):
            ledger["consumed_memories"].append(item)


def merge_continuity_delta(state, delta, text, bible, chapter):
    """Return state with only quote-grounded continuity changes merged."""
    result = deepcopy(state) if isinstance(state, dict) else {}
    ledger = _ledger(result, bible)
    delta = delta if isinstance(delta, dict) else {}
    cards = _cards(bible)
    for row in delta.get("characters", []) if isinstance(delta.get("characters"), list) else []:
        _merge_character(ledger, row, text, chapter, cards)
    for row in delta.get("body_structures", []) if isinstance(delta.get("body_structures"), list) else []:
        _merge_body(ledger, row, text, chapter, cards)
    _merge_memories(ledger, delta.get("consumed_memories", []), text, bible, chapter)
    result["continuity_ledger"] = ledger
    return result


def _person_fact(name, record):
    facts = []
    life = record.get("life_status") if isinstance(record.get("life_status"), dict) else {}
    if life.get("value") in ("missing", "dead"):
        facts.append(_LIFE_TEXT[life["value"]])
    location = record.get("location") if isinstance(record.get("location"), dict) else {}
    travel = location.get("travel_status")
    place = str(location.get("value") or "")
    if travel and travel != "未知":
        facts.append(travel + ("，" + place if place else ""))
    elif place:
        facts.append(place)
    for injury in record.get("injuries", []) if isinstance(record.get("injuries"), list) else []:
        if not isinstance(injury, dict):
            continue
        side = "" if injury.get("side") in (None, "无") else injury["side"]
        phrase = side + str(injury.get("part", "")) + str(injury.get("condition", ""))
        if phrase:
            facts.append(phrase + ("，" + str(injury["severity"]) if injury.get("severity") else ""))
    body = record.get("body") if isinstance(record.get("body"), dict) else {}
    body_fact = str(body.get("form") or "")
    if type(body.get("limb_count")) is int:
        count = _NUMBERS.get(body["limb_count"], str(body["limb_count"]))
        count_fact = f"共{count}条{body.get('limb_name', '肢体')}"
        body_fact = body_fact + ("，" if body_fact else "") + count_fact
    if body_fact:
        facts.append(body_fact)
    return f"{name}：" + "；".join(dict.fromkeys(facts)) if facts else ""


def writer_continuity_facts(state, bible, chapter):
    """Render ledger facts as short natural-language constraints for the writer."""
    ledger = _ledger(state if isinstance(state, dict) else {}, bible)
    mentioned = str(chapter.get("scenes", []))
    cards = _cards(bible)
    names = [name for name, card in cards.items() if name in mentioned or card.get("role") == "protagonist"]
    lines = [line for name in names if (line := _person_fact(name, ledger["characters"].get(name, {})))]
    memories = [
        f"{row.get('summary')}（第{row.get('chapter')}章）"
        for row in ledger["consumed_memories"]
        if isinstance(row, dict) and row.get("summary") and type(row.get("chapter")) is int
    ]
    if memories:
        lines.append("已消耗记忆，不能再次献祭或当作完整记忆想起：" + "；".join(dict.fromkeys(memories)))
    return lines
