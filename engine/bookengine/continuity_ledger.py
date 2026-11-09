"""Quote-grounded character continuity facts and bounded repairs."""

from copy import deepcopy
import json
import re

from .precision import _sentences, apply as sentence_edits


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
_CN_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_MODEL_KINDS = {"injury_side", "life_status", "location_status", "consumed_memory", "limb_count"}


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


def _finding(kind, chapter, name, reason, text, number, start, end, expected="", observed=""):
    return {
        "kind": kind,
        "chapter": chapter,
        "name": name,
        "status": "Inference",
        "reason": reason,
        "expected": expected,
        "observed": observed,
        "quote": text[start:end],
        "id": number,
        "start": start,
        "end": end,
    }


def _injury_findings(text, chapter, name, record, spans):
    result = []
    for injury in record.get("injuries", []) if isinstance(record.get("injuries"), list) else []:
        side = injury.get("side")
        part = injury.get("part")
        condition = injury.get("condition")
        if side not in ("左", "右") or not isinstance(part, str) or not isinstance(condition, str):
            continue
        family = r"冻伤|冻裂|冻坏|冻僵" if "冻" in condition else re.escape(condition)
        pattern = re.compile(rf"(?:{family})的?([左右]){re.escape(part)}")
        for number, (start, end) in enumerate(spans, 1):
            sentence = text[start:end]
            if name not in sentence or not (match := pattern.search(sentence)) or match[1] == side:
                continue
            result.append(
                _finding(
                    "injury_side",
                    chapter,
                    name,
                    "正文伤处左右与既有未愈伤情冲突",
                    text,
                    number,
                    start,
                    end,
                    side + part,
                    match[1] + part,
                )
            )
    return result


def _body_findings(text, chapter, name, record, spans):
    body = record.get("body") if isinstance(record.get("body"), dict) else {}
    expected = body.get("limb_count")
    if type(expected) is not int:
        return []
    result = []
    pattern = re.compile(r"([一二两三四五六七八九]|\d+)只?(?:前蹄|后蹄|蹄|腿|足|爪)")
    for number, (start, end) in enumerate(spans, 1):
        sentence = text[start:end]
        if name not in sentence or not (match := pattern.search(sentence)):
            continue
        observed = int(match[1]) if match[1].isdigit() else _CN_NUMBERS[match[1]]
        if observed != expected:
            result.append(
                _finding(
                    "limb_count",
                    chapter,
                    name,
                    "正文肢体数量与已记录身体构造冲突",
                    text,
                    number,
                    start,
                    end,
                    str(expected),
                    str(observed),
                )
            )
    return result


def _status_findings(text, chapter, name, record, spans):
    result = []
    life = record.get("life_status") if isinstance(record.get("life_status"), dict) else {}
    location = record.get("location") if isinstance(record.get("location"), dict) else {}
    for number, (start, end) in enumerate(spans, 1):
        sentence = text[start:end]
        if name not in sentence:
            continue
        if life.get("value") == "alive" and re.search(
            r"那时" + re.escape(name) + r"还活着|" + re.escape(name) + r"生前", sentence
        ):
            result.append(
                _finding(
                    "life_status",
                    chapter,
                    name,
                    "正文暗示当前已死亡，但账本仍为存活",
                    text,
                    number,
                    start,
                    end,
                    "存活",
                    "暗示已死亡",
                )
            )
        if life.get("value") == "dead" and not re.search(r"回忆|尸体|遗体|死讯|生前", sentence):
            if re.search(re.escape(name) + r"[^。！？]{0,16}(?:说|问|答|走|跑|抬|抓|握|看|站|坐)", sentence):
                result.append(
                    _finding(
                        "life_status",
                        chapter,
                        name,
                        "已死亡人物在当前场景行动",
                        text,
                        number,
                        start,
                        end,
                        "死亡",
                        "当前行动",
                    )
                )
        travel = location.get("travel_status")
        if travel in ("随行", "同行") and re.search(r"真正的" + re.escape(name) + r"还在", sentence):
            result.append(
                _finding(
                    "location_status",
                    chapter,
                    name,
                    "随行人物被写成仍留在别处",
                    text,
                    number,
                    start,
                    end,
                    travel,
                    "留在别处",
                )
            )
    return result


def continuity_findings(text, state, bible, chapter):
    """Return deterministic, sentence-located contradictions against the ledger."""
    ledger = _ledger(state if isinstance(state, dict) else {}, bible)
    spans = _sentences(text)
    findings = []
    for name, record in ledger["characters"].items():
        if not isinstance(record, dict):
            continue
        findings += _injury_findings(text, chapter, name, record, spans)
        findings += _body_findings(text, chapter, name, record, spans)
        findings += _status_findings(text, chapter, name, record, spans)
    for memory in ledger["consumed_memories"]:
        if not isinstance(memory, dict) or chapter <= memory.get("chapter", chapter):
            continue
        summary = memory.get("summary")
        if not isinstance(summary, str) or not summary:
            continue
        for number, (start, end) in enumerate(spans, 1):
            if summary in text[start:end]:
                findings.append(
                    _finding(
                        "consumed_memory",
                        chapter,
                        memory.get("name", ""),
                        "已消耗记忆再次被献祭或作为完整记忆出现",
                        text,
                        number,
                        start,
                        end,
                        "不可再次献祭或完整想起",
                        summary,
                    )
                )
    unique = {(row["kind"], row["name"], row["id"]): row for row in findings}
    return sorted(unique.values(), key=lambda row: (row["start"], row["kind"], row["name"]))


def _model_findings(value, spans, text, chapter):
    result = []
    rows = value.get("findings", []) if isinstance(value, dict) else []
    for row in rows if isinstance(rows, list) else []:
        number = row.get("id") if isinstance(row, dict) else None
        if type(number) is not int or not 1 <= number <= len(spans) or row.get("kind") not in _MODEL_KINDS:
            continue
        start, end = spans[number - 1]
        result.append(
            _finding(
                row["kind"],
                chapter,
                str(row.get("name") or ""),
                str(row.get("reason") or "小模型复核候选"),
                text,
                number,
                start,
                end,
            )
        )
    return result


def _adopt_state(state, raw, text, bible, chapter):
    merged = merge_continuity_delta(state, raw.get("continuity_delta"), text, bible, chapter)
    state.clear()
    state.update(merged)


def repair_continuity_ledger(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Run one semantic review, apply only located edits, then deterministically recheck."""
    chapter = outline.get("number", 0)
    before = continuity_findings(text, state, bible, chapter)
    facts = writer_continuity_facts(state, bible, outline)
    note = {"status": "checked", "before": before, "after": before, "model_findings": []}
    if not facts:
        _adopt_state(state, raw, text, bible, chapter)
        return text, raw, failed, note
    spans = _sentences(text)
    response = ask(
        client,
        prefix + "-continuity-ledger",
        "对照当前事实复核正文。只报告确定矛盾：左右伤处、生死、随行位置、已消耗记忆重复使用、肢体数量。"
        "findings用句号id；只改命中句，保留情节结果和人物口吻。不确定不改。返回JSON。",
        [
            ("当前事实", "\n".join(facts)),
            ("确定性矛盾句", json.dumps(before, ensure_ascii=False)),
            (
                "正文句号",
                json.dumps([{"id": i, "text": text[a:b]} for i, (a, b) in enumerate(spans, 1)], ensure_ascii=False),
            ),
        ],
        '{"findings":[{"id":1,"kind":"injury_side","name":"人物","reason":"原因"}],"edits":[{"id":1,"text":"替换句"}]}',
        cap=2500,
        effort="low",
        input_limit=10000,
        max_attempts=1,
    )
    model = _model_findings(response, spans, text, chapter)
    targets = {row["id"]: row for row in [*before, *model]}
    allowed = [dict(id=number, start=spans[number - 1][0], end=spans[number - 1][1]) for number in targets]
    candidate, edits = sentence_edits(
        text,
        response.get("edits", []) if isinstance(response, dict) else [],
        allowed,
        allow_empty=False,
        allow_style=True,
    )
    after = continuity_findings(candidate, state, bible, chapter)
    note.update(model_findings=model, after=after, edits=edits)
    if not targets:
        _adopt_state(state, raw, text, bible, chapter)
        return text, raw, failed, note
    if candidate == text or after:
        return text, raw, failed, dict(note, status="rolled_back", reason="continuity_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-continuity-state", candidate, state, bible, outline, [], final=True
    )
    if invalid:
        return text, raw, failed, dict(note, status="rolled_back", reason="state_recheck_failed")
    _adopt_state(state, checked, candidate, bible, chapter)
    return candidate, checked, False, dict(note, status="repaired")
