"""Quote-grounded countable-item and ordered-level state."""

from copy import deepcopy
import json
import re

from .precision import _sentences, apply as sentence_edits


_CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_OUT = {1: "一", 2: "两", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
_NOUNS = "弦|箭|钥匙|令牌|药瓶|丹药|符箓|绳|针|刀|剑|枪|灯|珠|石|匣"
_COUNT = re.compile(
    rf"(?<!第)(?P<num>[一二两三四五六七八九十\d]+)(?:(?P<unit>根|枚|件|把|瓶|颗|块|张)"
    rf"(?P<middle>[\u3400-\u9fff]{{0,8}}?)(?:的)?(?P<noun>{_NOUNS})|(?P<direct>弦))"
)
_RESTORED = re.compile(r"修复|补上|补入|换上|接回|归还|夺回|寻回|添入|新增|恢复")
_COUNT_STATE = re.compile(r"剩余|只剩|仅存|只余|完好|尚能发声|残余|共有|共计|恢复为")
_BREAKTHROUGH = re.compile(r"踏入|突破|跨入|晋入|迈入|境界名号")
_HISTORICAL = re.compile(r"曾经|当年|回忆|传说|假如|若能|尚未|未曾|尚远|还远|未到")


def state_value_contract():
    return (
        "quantity_states只记关键可数物品本章正文明确的当前数量或完好/损坏变化，"
        "给owner/item/total/intact/damaged/quote；没有明确数字不记。"
        "level_states只记人物当前等级的确认或变化，给name/track/value/change/quote；"
        "change只用confirmed/breakthrough/demotion。两类quote都必须是正文连续逐字原文。",
        {"quantity_states": [], "level_states": []},
    )


def _number(value):
    if isinstance(value, int):
        return value
    value = str(value)
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return (10 if not left else _CN.get(left, 0) * 10) + (0 if not right else _CN.get(right, 0))
    return _CN.get(value)


def _item_key(item):
    item = re.sub(r"(?:完好|完整|残余|剩余|仅存|尚能发声|可用|损坏|断裂|古琴|琴身)", "", str(item or ""))
    return next((noun for noun in _NOUNS.split("|") if item.endswith(noun)), item[-4:])


def _ladder(bible):
    canon = bible.get("canon", {}).get("ladder", {}).get("strength", []) if isinstance(bible, dict) else []
    world = bible.get("world", {}).get("levels", []) if isinstance(bible, dict) else []
    values = canon or world
    return [str(row.get("name") if isinstance(row, dict) else row) for row in values if row]


def _protagonist(bible):
    return next(
        (
            row.get("name")
            for row in bible.get("characters", [])
            if isinstance(row, dict) and row.get("role") == "protagonist" and row.get("name")
        ),
        "",
    )


def _quote(row, text):
    quote = row.get("quote") if isinstance(row, dict) else None
    return quote.strip() if isinstance(quote, str) and quote.strip() and quote.strip() in text else ""


def _ledger(state):
    result = deepcopy(state) if isinstance(state, dict) else {}
    ledger = deepcopy(result.get("continuity_ledger")) if isinstance(result.get("continuity_ledger"), dict) else {}
    ledger["quantity_states"] = ledger.get("quantity_states") if isinstance(ledger.get("quantity_states"), dict) else {}
    ledger["level_states"] = ledger.get("level_states") if isinstance(ledger.get("level_states"), dict) else {}
    result["continuity_ledger"] = ledger
    return result, ledger


def merge_state_value_delta(state, delta, text, bible, chapter):
    """Merge only verbatim-grounded current values."""
    result, ledger = _ledger(state)
    delta = delta if isinstance(delta, dict) else {}
    names = {row.get("name") for row in bible.get("characters", []) if isinstance(row, dict)}
    for row in delta.get("quantity_states", []) if isinstance(delta.get("quantity_states"), list) else []:
        quote = _quote(row, text)
        item = str(row.get("item") or "").strip()
        owner = str(row.get("owner") or _protagonist(bible)).strip()
        values = {key: row.get(key) for key in ("total", "intact", "damaged") if type(row.get(key)) is int}
        if (
            not quote
            or not item
            or not owner
            or owner not in names
            or not values
            or any(value < 0 for value in values.values())
        ):
            continue
        key = owner + "|" + _item_key(item)
        ledger["quantity_states"][key] = {
            "owner": owner,
            "item": item[:40],
            **values,
            "chapter": chapter,
            "quote": quote,
        }
    ladder = _ladder(bible)
    for row in delta.get("level_states", []) if isinstance(delta.get("level_states"), list) else []:
        quote = _quote(row, text)
        name = str(row.get("name") or _protagonist(bible)).strip()
        track, value = str(row.get("track") or "境界").strip(), str(row.get("value") or "").strip()
        if not quote or name not in names or value not in ladder:
            continue
        ledger["level_states"][name + "|" + track] = {
            "name": name,
            "track": track[:20],
            "value": value,
            "index": ladder.index(value),
            "chapter": chapter,
            "quote": quote,
        }
    return result


def _quantity_mentions(text):
    rows = []
    for number, (start, end) in enumerate(_sentences(text), 1):
        sentence = text[start:end]
        for match in _COUNT.finditer(sentence):
            value = _number(match["num"])
            noun = match["noun"] or match["direct"]
            context = sentence[max(0, match.start() - 16) : match.end() + 10]
            stateful = bool(
                _COUNT_STATE.search(context)
                or re.search(rf"{re.escape(match.group(0))}(?:中|尽断)", context)
                or re.search(rf"琴身[^。！？]{{0,8}}{re.escape(match.group(0))}", context)
            )
            if value is None or not stateful:
                continue
            rows.append(
                {
                    "id": number,
                    "start": start,
                    "end": end,
                    "quote": sentence,
                    "item_key": _item_key(noun),
                    "value": 0 if re.search(r"尽断|全断|全部断", sentence) else value,
                    "restored": bool(_RESTORED.search(sentence)),
                }
            )
    return rows


def _level_mentions(text, bible):
    ladder = _ladder(bible)
    rows = []
    for number, (start, end) in enumerate(_sentences(text), 1):
        sentence = text[start:end]
        levels = [level for level in ladder if level in sentence]
        if not levels or _HISTORICAL.search(sentence):
            continue
        target = levels[-1]
        breakthrough = bool(_BREAKTHROUGH.search(sentence))
        asserted = breakthrough or bool(
            re.search(
                rf"(?:以|属于|已是|当前|真正属于)[^。！？]{{0,8}}{re.escape(target)}|{re.escape(target)}的"
                rf"|[“\"]{re.escape(target)}[。！？”\"]",
                sentence,
            )
        )
        if asserted:
            rows.append(
                {
                    "id": number,
                    "start": start,
                    "end": end,
                    "quote": sentence,
                    "value": target,
                    "index": ladder.index(target),
                    "breakthrough": breakthrough,
                    "sublevel": bool(re.search(re.escape(target) + r"(?:满境|圆满|巅峰)", sentence)),
                }
            )
    return rows


def _finding(kind, chapter, row, expected, observed, name=""):
    return {
        "kind": kind,
        "chapter": chapter,
        "name": name,
        "status": "Inference",
        "reason": "正文当前数量或等级与已有状态矛盾",
        "expected": str(expected),
        "observed": str(observed),
        **{key: row[key] for key in ("id", "start", "end", "quote")},
    }


def state_value_findings(text, state, bible, chapter):
    """Detect ungrounded count increases, repeated breakthroughs, and regressions."""
    _, ledger = _ledger(state)
    findings = []
    mentions = _quantity_mentions(text)
    for record in ledger["quantity_states"].values():
        expected = record.get("intact", record.get("total"))
        if type(expected) is not int or record.get("chapter", chapter) >= chapter:
            continue
        key = _item_key(record.get("item"))
        for row in [item for item in mentions if item["item_key"] == key]:
            if row["value"] > expected and not row["restored"]:
                findings.append(_finding("quantity_increase", chapter, row, expected, row["value"], record["owner"]))
            elif row["restored"] or row["value"] < expected:
                expected = row["value"]
    ladder = _ladder(bible)
    for record in ledger["level_states"].values():
        if record.get("value") not in ladder or record.get("chapter", chapter) >= chapter:
            continue
        expected = ladder.index(record["value"])
        for row in _level_mentions(text, bible):
            if row["index"] < expected:
                findings.append(
                    _finding("level_regression", chapter, row, record["value"], row["value"], record["name"])
                )
            elif row["index"] == expected and row["breakthrough"]:
                findings.append(
                    _finding("repeated_level_breakthrough", chapter, row, record["value"], row["value"], record["name"])
                )
    return sorted(findings, key=lambda row: (row["start"], row["kind"]))


def _count_text(value):
    return _CN_OUT.get(value, str(value))


def writer_state_value_facts(state, bible):
    _, ledger = _ledger(state)
    lines = []
    for row in ledger["quantity_states"].values():
        parts = []
        if type(row.get("intact")) is int:
            parts.append("完好" + _count_text(row["intact"]) + "根")
        if type(row.get("damaged")) is int:
            parts.append("损坏" + _count_text(row["damaged"]) + "根")
        if not parts and type(row.get("total")) is int:
            parts.append("共" + _count_text(row["total"]) + "件")
        lines.append(f"当前数量：{row['owner']}的{row['item']}" + "、".join(parts))
    for row in ledger["level_states"].values():
        lines.append(f"当前{row['track']}：{row['name']}为{row['value']}")
    return lines


def repair_state_values(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    chapter = outline.get("number", 0)
    before = state_value_findings(text, state, bible, chapter)
    note = {"status": "checked", "before": before, "after": before, "edits": []}
    if not before:
        return text, raw, failed, note
    response = ask(
        client,
        prefix + "-state-values-repair",
        "只改待修句，保留人物选择、行动结果和其他事实；把数量改为有依据的当前值，消除等级倒退或重复突破。"
        "不得新增损失、代价、突破或物品。返回逐句替换。",
        [
            ("当前状态", "\n".join(writer_state_value_facts(state, bible))),
            ("待修状态句", json.dumps(before, ensure_ascii=False)),
        ],
        '{"edits":[{"id":1,"text":"替换后的完整句"}]}',
        cap=2200,
        effort="low",
        input_limit=10000,
        max_attempts=1,
    )
    spans = _sentences(text)
    allowed = [dict(id=row["id"], start=spans[row["id"] - 1][0], end=spans[row["id"] - 1][1]) for row in before]
    candidate, edits = sentence_edits(
        text,
        response.get("edits", []) if isinstance(response, dict) else [],
        allowed,
        allow_empty=False,
        allow_style=True,
    )
    after = state_value_findings(candidate, state, bible, chapter)
    note.update(after=after, edits=edits)
    if candidate == text or after:
        return text, raw, failed, dict(note, status="rolled_back", reason="state_value_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-state-values-state", candidate, state, bible, outline, [], final=True
    )
    if invalid:
        return text, raw, failed, dict(note, status="rolled_back", reason="state_value_extract_failed")
    return candidate, checked, False, dict(note, status="repaired")


def state_value_checkup(chapters, bible):
    quantity, levels = [], []
    protagonist = _protagonist(bible)
    current_counts = {}
    current_level = None
    current_level_chapter = None
    ladder = _ladder(bible)
    for chapter in sorted(chapters, key=lambda row: row.get("number", 0)):
        number, text = chapter.get("number", 0), chapter.get("text", "")
        for row in _quantity_mentions(text):
            key = row["item_key"]
            expected = current_counts.get(key)
            if expected is not None and row["value"] > expected and not row["restored"]:
                quantity.append(_finding("quantity_increase", number, row, expected, row["value"], protagonist))
            elif expected is None or row["restored"] or row["value"] < expected:
                current_counts[key] = row["value"]
        for row in _level_mentions(text, bible):
            if current_level is None:
                if row["breakthrough"]:
                    current_level = row["index"]
                    current_level_chapter = number
                continue
            if row["index"] < current_level:
                levels.append(
                    _finding("level_regression", number, row, ladder[current_level], row["value"], protagonist)
                )
            elif row["breakthrough"] and row["index"] > current_level:
                current_level = row["index"]
                current_level_chapter = number
            elif row["breakthrough"] and not row["sublevel"] and current_level_chapter != number:
                levels.append(
                    _finding(
                        "repeated_level_breakthrough", number, row, ladder[current_level], row["value"], protagonist
                    )
                )
    quantity = list({(row["chapter"], row["kind"], row["quote"]): row for row in quantity}.values())
    levels = list({(row["chapter"], row["kind"], row["observed"]): row for row in levels}.values())
    return {"quantity_state_conflicts": quantity, "level_state_conflicts": levels}
