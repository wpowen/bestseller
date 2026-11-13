"""Permanent losses and gains: quote-grounded ledger, repair, and offline checks."""

from copy import deepcopy
import json
import re

from .precision import apply as span_edits


_CHANGES = {"lost", "gained"}
_TERMS = (
    "身份",
    "权限",
    "资格",
    "功法",
    "能力",
    "修为",
    "记忆",
    "肢体",
    "手臂",
    "眼睛",
    "寿命",
    "调遣",
    "山门",
    "印记",
)
_KINDS = {
    "repeated_permanent_loss",
    "groundless_restore",
    "repeated_permanent_gain",
    "groundless_permanent_loss",
}


def _quote(row, text):
    quote = row.get("quote") if isinstance(row, dict) else None
    return quote.strip() if isinstance(quote, str) and quote.strip() and quote.strip() in text else ""


def merge_permanent_delta(state, delta, text, chapter):
    """Merge a first quote-grounded permanent change without overwriting it later."""
    result = deepcopy(state) if isinstance(state, dict) else {}
    ledger = deepcopy(result.get("continuity_ledger")) if isinstance(result.get("continuity_ledger"), dict) else {}
    saved = ledger.get("permanent_states") if isinstance(ledger.get("permanent_states"), list) else []
    delta = delta if isinstance(delta, dict) else {}
    for row in delta.get("permanent_states", []) if isinstance(delta.get("permanent_states"), list) else []:
        if not isinstance(row, dict) or row.get("change") not in _CHANGES or not (quote := _quote(row, text)):
            continue
        fields = (row.get("name"), row.get("category"), row.get("subject"), row.get("summary"))
        if not all(isinstance(value, str) and value.strip() for value in fields):
            continue
        item = {
            "name": fields[0].strip()[:40],
            "category": fields[1].strip()[:30],
            "subject": fields[2].strip()[:100],
            "change": row["change"],
            "summary": fields[3].strip()[:120],
            "chapter": chapter,
            "quote": quote,
        }
        same = [
            old
            for old in saved
            if isinstance(old, dict) and old.get("name") == item["name"] and old.get("subject") == item["subject"]
        ]
        if not same:
            saved.append(item)
    ledger["permanent_states"] = saved[-60:]
    result["continuity_ledger"] = ledger
    return result


def permanent_states(state):
    ledger = state.get("continuity_ledger") if isinstance(state, dict) else None
    rows = ledger.get("permanent_states") if isinstance(ledger, dict) else None
    return [row for row in rows or [] if isinstance(row, dict) and row.get("quote") and row.get("chapter")]


def writer_permanent_facts(state):
    lines = []
    for row in permanent_states(state):
        boundary = "不可再次失去或无依据恢复" if row.get("change") == "lost" else "不可再次首次获得或无依据消失"
        change = "失去" if row.get("change") == "lost" else "获得"
        lines.append(
            f"已经永久{change}，{boundary}：{row.get('name')}的{row.get('subject')}（第{row.get('chapter')}章）"
        )
    return list(dict.fromkeys(lines))


def _paragraph_spans(text):
    return [(match.start(), match.end()) for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|$)", text)]


def _state_overlap(row, text):
    source = "".join(str(row.get(key) or "") for key in ("subject", "summary", "quote"))
    return {term for term in _TERMS if term in source and term in text}


def _conflict_kind(row, text):
    if not _state_overlap(row, text):
        return ""
    if row.get("change") == "lost":
        if re.search(
            r"最后一层(?:身份|权限)|重新取得(?:通行|资格|权限)|(?:仍|还|又)能调用|恢复[^。！？]{0,12}(?:资格|权限|能力)",
            text,
        ):
            return "groundless_restore"
        if re.search(
            r"身份印记[^。！？]{0,80}(?:裂|碎|连半点回应都没有)|身份权限[^。！？]{0,24}(?:抽空|耗尽|失去)",
            text,
        ):
            return "repeated_permanent_loss"
    if row.get("change") == "gained":
        if re.search(r"(?:再次|又|重新)(?:获得|得到|取得)", text):
            return "repeated_permanent_gain"
        if re.search(r"(?:凭空|忽然|不知何时)(?:消失|失去)", text):
            return "groundless_permanent_loss"
    return ""


def permanent_state_findings(text, state, chapter):
    findings = []
    spans = _paragraph_spans(text)
    for fact in permanent_states(state):
        if fact.get("chapter", chapter) >= chapter:
            continue
        for number, (start, end) in enumerate(spans, 1):
            if not (kind := _conflict_kind(fact, text[start:end])):
                continue
            findings.append(
                {
                    "id": number,
                    "kind": kind,
                    "chapter": chapter,
                    "previous_chapter": fact["chapter"],
                    "previous_quote": fact["quote"],
                    "current_quote": text[start:end],
                    "reason": "正文重复支付永久代价或让永久状态无依据逆转",
                    "status": "Inference",
                    "start": start,
                    "end": end,
                }
            )
            break
    return findings


def _review(ask, client, prefix, text, state, chapter, suffix):
    facts = permanent_states(state)
    spans = _paragraph_spans(text)
    if not facts:
        return []
    response = ask(
        client,
        prefix + suffix,
        "对照永久状态，只报告正文中再次支付同一永久代价、再次首次获得，或无依据恢复、消失。"
        "逐字引用账本和本章命中段；只返回指定类别，不确定不报。",
        [
            ("永久状态账本", json.dumps(facts, ensure_ascii=False)),
            (
                "本章编号段",
                json.dumps([{"id": i, "text": text[a:b]} for i, (a, b) in enumerate(spans, 1)], ensure_ascii=False),
            ),
        ],
        '{"findings":[{"id":1,"kind":"groundless_restore","previous_chapter":1,"previous_quote":"前文逐字引文","current_quote":"本章逐字引文","reason":"原因"}]}',
        cap=1800,
        effort="low",
        input_limit=10000,
        max_attempts=1,
    )
    rows = response.get("findings", []) if isinstance(response, dict) else []
    grounded = []
    for row in rows if isinstance(rows, list) else []:
        number = row.get("id") if isinstance(row, dict) else None
        fact = next(
            (
                saved
                for saved in facts
                if row.get("previous_chapter") == saved.get("chapter")
                and row.get("previous_quote") == saved.get("quote")
            ),
            None,
        )
        if type(number) is int and 1 <= number <= len(spans) and fact and row.get("kind") in _KINDS:
            start, end = spans[number - 1]
            if row.get("current_quote") == text[start:end]:
                grounded.append(
                    {**row, "chapter": chapter, "start": start, "end": end, "status": "agent_editorial_not_human"}
                )
    deterministic = permanent_state_findings(text, state, chapter)
    return list(
        {(row["id"], row["kind"], row["previous_chapter"]): row for row in [*deterministic, *grounded]}.values()
    )


def repair_permanent_states(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Target permanent-state conflicts, recheck semantically, and otherwise roll back."""
    chapter = outline.get("number", 0)
    before = _review(ask, client, prefix, text, state, chapter, "-permanent-review")
    note = {"status": "checked", "before": before, "after": before, "edits": []}
    if not before:
        return text, raw, failed, note
    spans = _paragraph_spans(text)
    targets = sorted({row["id"] for row in before})
    allowed = [{"id": number, "text": text[spans[number - 1][0] : spans[number - 1][1]]} for number in targets]
    response = ask(
        client,
        prefix + "-permanent-repair",
        "只改命中段，保留本章行动结果、人物口吻和其他事实；消除重复支付或无依据逆转。返回逐段替换。",
        [
            ("永久状态", json.dumps(permanent_states(state), ensure_ascii=False)),
            ("允许修改的命中句", json.dumps(allowed, ensure_ascii=False)),
        ],
        '{"edits":[{"id":1,"text":"替换后的完整段落"}]}',
        cap=2500,
        effort="low",
        input_limit=10000,
        max_attempts=1,
    )
    candidate, edits = span_edits(
        text,
        response.get("edits", []) if isinstance(response, dict) else [],
        [{"id": number, "start": spans[number - 1][0], "end": spans[number - 1][1]} for number in targets],
        allow_empty=False,
        allow_style=True,
    )
    note["edits"] = edits
    if candidate == text:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_permanent_repair")
    after = _review(ask, client, prefix, candidate, state, chapter, "-permanent-recheck")
    note["after"] = after
    if after:
        return text, raw, failed, dict(note, status="rolled_back", reason="permanent_recheck_failed")
    checked, invalid, _ = extract(client, prefix + "-permanent-state", candidate, state, bible, outline, [], final=True)
    if invalid:
        return text, raw, failed, dict(note, status="rolled_back", reason="permanent_state_recheck_failed")
    return candidate, checked, False, dict(note, status="repaired")


def _offline_facts(chapters):
    facts = []
    for chapter in chapters:
        for start, end in _paragraph_spans(chapter.get("text", "")):
            quote = chapter["text"][start:end]
            if "永久" not in quote and "此后不能再" not in quote:
                continue
            terms = [term for term in _TERMS if term in quote]
            if terms:
                facts.append(
                    {
                        "name": "人物",
                        "category": "永久状态",
                        "subject": "、".join(terms),
                        "change": "lost" if re.search(r"不能再|永久失去|再也不能", quote) else "gained",
                        "summary": quote[:120],
                        "chapter": chapter["number"],
                        "quote": quote,
                    }
                )
    return facts


def permanent_state_checkup(chapters):
    state = {"continuity_ledger": {"permanent_states": _offline_facts(chapters)}}
    issues = []
    for chapter in chapters:
        issues.extend(permanent_state_findings(chapter.get("text", ""), state, chapter.get("number", 0)))
    return list({(row["chapter"], row["kind"], row["current_quote"]): row for row in issues}.values())
