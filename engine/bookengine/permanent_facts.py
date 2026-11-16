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
_CATEGORY_NAMES = {"ability": "能力", "identity": "身份", "permission": "权限", "limb": "肢体"}
_CONCEPT_NAMES = {
    "multi_command": "多线号令能力",
    "command": "命令能力",
    "sect_identity": "宗门身份与权限",
    "identity": "身份",
    "permission": "权限",
    "limb": "肢体",
}
_GROUP = re.compile(r"(?:宗|门|院|城|军|营|队|族|盟|会|领民|工匠|守军|众人)$")
_LOSS = re.compile(
    r"永久失去|此后不能再|再也不能|无法再|不能再(?:直接)?命令|不能再(?:以|借|持|调用|凭|同时)"
    r"|(?:多线发令|远程呼救|强行维持命令)[^。！？]{0,32}全都消失"
    r"|身份印记[\s\S]{0,180}连半点回应都没有|身份权限[^。！？]{0,24}(?:被)?抽空"
    r"|(?:权限|席位|资格)[^。！？]{0,24}(?:一并撤销|正式剥夺)"
)
_RESTORE = re.compile(
    r"最后一层(?:身份|权限)|重新取得(?:通行|资格|权限)|(?:仍|还|又)能调用"
    r"|恢复[^。！？]{0,12}(?:资格|权限|能力)|(?:重新|再次)获得"
    r"|(?:重新|仍|还|又)能[^。！？]{0,16}(?:命令|发令|号令)"
)
_CONDITIONAL = re.compile(r"若|如果|一旦|倘若|可能|或许|本可以|将会|会永久|便永久")


def _quote(row, text):
    quote = row.get("quote") if isinstance(row, dict) else None
    return quote.strip() if isinstance(quote, str) and quote.strip() and quote.strip() in text else ""


def _character_names(bible):
    if not isinstance(bible, dict):
        return set()
    return {
        row["name"]
        for row in bible.get("characters", [])
        if isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"].strip()
    }


def _actor_and_subject(row, quote, bible):
    name, subject = str(row.get("name") or "").strip(), str(row.get("subject") or "").strip()
    names = _character_names(bible)
    if names:
        if name in names:
            return name, subject
        if subject in names:
            return subject, name
        contained = [candidate for candidate in names if candidate in name or candidate in subject]
        if len(contained) == 1:
            actor = contained[0]
            description = name if name != actor else subject
            return actor, description
        group = next((value for value in (name, subject) if value in quote and _GROUP.search(value)), "")
        return (group, subject if group == name else name) if group else ("", "")
    return name, subject


def _offline_actor(quote, bible):
    names = [name for name in _character_names(bible) if name in quote]
    if len(names) == 1:
        return names[0]
    attributed = [
        name
        for name in names
        if re.search(rf"[”’\"']?{re.escape(name)}(?:说|道|问|答|把|将|取|推|按|握|看|抬|低)", quote)
    ]
    if len(attributed) == 1:
        return attributed[0]
    protagonists = {
        row.get("name")
        for row in (bible.get("characters", []) if isinstance(bible, dict) else [])
        if isinstance(row, dict) and row.get("role") == "protagonist"
    }
    main = [name for name in names if name in protagonists]
    return main[0] if len(main) == 1 else "人物" if not bible else ""


def _canonical_parts(text, category=""):
    source = str(text or "")
    category_text = str(category or "")
    if re.search(r"宗门|山门|弟子身份|内门身份|身份印记|宗门功法|宗[^。！？]{0,20}身份", source):
        return [("identity", "sect_identity")]
    if "身份" in category_text:
        token = re.sub(r"永久|失去|获得|身份|资格|的|了", "", source)
        return [("identity", re.sub(r"[^\u3400-\u9fff]", "", token)[:12] or "identity")]
    if "权限" in category_text:
        token = re.sub(r"永久|失去|获得|权限|资格|席位|权|的|了", "", source)
        return [("permission", re.sub(r"[^\u3400-\u9fff]", "", token)[:12] or "permission")]
    command = bool(re.search(r"命令|号令|发令|支配|调遣|统御", source))
    if command and re.search(r"多线|同时[^。！？]{0,12}(?:多人|多个|数处|四方)|多个方向|远程", source):
        return [("ability", "multi_command")]
    if command:
        return [("ability", "command")]
    if "身份" in source or "身份" in category_text:
        token = re.sub(r"永久|失去|获得|身份|资格|的|了", "", source)
        return [("identity", re.sub(r"[^\u3400-\u9fff]", "", token)[:12] or "identity")]
    if re.search(r"权限|资格|席位|通行权|接续权|调度权", source + category_text):
        token = re.sub(r"永久|失去|获得|权限|资格|席位|权|的|了", "", source)
        return [("permission", re.sub(r"[^\u3400-\u9fff]", "", token)[:12] or "permission")]
    if re.search(r"肢体|手臂|手掌|眼睛|腿|脚|知觉", source + category_text):
        part = next(
            (value for value in ("右臂", "左臂", "右手", "左手", "眼睛", "腿", "脚") if value in source), "limb"
        )
        return [("limb", part)]
    if "能力" in source + category_text:
        compact = re.sub(r"永久|失去|获得|能力|不能|无法|再也|任何|直接|所有|的|了", "", source)
        token = re.sub(r"[^\u3400-\u9fff]", "", compact)[:12]
        return [("ability", token)] if token else []
    return []


def _normalized_row(row, quote, chapter, bible=None):
    original_name = str(row.get("name") or "").strip()
    name, subject = _actor_and_subject(row, quote, bible)
    summary = str(row.get("summary") or "").strip()
    if not name or not subject or not summary:
        return None
    parts = _canonical_parts(" ".join((subject, summary)), row.get("category"))
    if not parts:
        parts = _canonical_parts(" ".join((subject, summary, quote)), row.get("category"))
    if not parts:
        return None
    keys = [f"{name}|{category}|{concept}" for category, concept in parts]
    return {
        "name": name[:40],
        "category": _CATEGORY_NAMES[parts[0][0]],
        "subject": subject[:100],
        "change": row["change"],
        "summary": summary[:120],
        "chapter": chapter,
        "quote": quote,
        "state_keys": keys,
        "fields_normalized": bool(row.get("fields_normalized")) or name != original_name,
    }


def merge_permanent_delta(state, delta, text, chapter, bible=None):
    """Merge a first quote-grounded permanent change without overwriting it later."""
    result = deepcopy(state) if isinstance(state, dict) else {}
    ledger = deepcopy(result.get("continuity_ledger")) if isinstance(result.get("continuity_ledger"), dict) else {}
    saved = ledger.get("permanent_states") if isinstance(ledger.get("permanent_states"), list) else []
    delta = delta if isinstance(delta, dict) else {}
    for row in delta.get("permanent_states", []) if isinstance(delta.get("permanent_states"), list) else []:
        if not isinstance(row, dict) or row.get("change") not in _CHANGES or not (quote := _quote(row, text)):
            continue
        item = _normalized_row(row, quote, chapter, bible)
        if not item:
            continue
        same = [
            old
            for old in saved
            if isinstance(old, dict)
            and old.get("change") == item["change"]
            and set(old.get("state_keys", [])) & set(item["state_keys"])
        ]
        if not same:
            saved.append(item)
    ledger["permanent_states"] = saved[-60:]
    result["continuity_ledger"] = ledger
    return result


def permanent_states(state, bible=None):
    ledger = state.get("continuity_ledger") if isinstance(state, dict) else None
    rows = ledger.get("permanent_states") if isinstance(ledger, dict) else None
    result = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("quote") or not row.get("chapter"):
            continue
        normalized = _normalized_row(row, row["quote"], row["chapter"], bible)
        result.append(normalized or row)
    return result


def writer_permanent_facts(state, bible=None):
    lines = []
    for row in permanent_states(state, bible):
        boundary = "不可再次失去或无依据恢复" if row.get("change") == "lost" else "不可再次首次获得或无依据消失"
        change = "失去" if row.get("change") == "lost" else "获得"
        keys = row.get("state_keys", [])
        concept = keys[0].rsplit("|", 1)[-1] if len(keys) == 1 else ""
        subject = (
            _CONCEPT_NAMES.get(concept, row.get("subject")) if row.get("fields_normalized") else row.get("subject")
        )
        lines.append(f"已经永久{change}，{boundary}：{row.get('name')}的{subject}（第{row.get('chapter')}章）")
    return list(dict.fromkeys(lines))


def _paragraph_spans(text):
    return [(match.start(), match.end()) for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|$)", text)]


def _matching_keys(row, text):
    current = {f"{row.get('name')}|{category}|{concept}" for category, concept in _canonical_parts(text)}
    return set(row.get("state_keys", [])) & current


def _change_snippets(text, pattern):
    snippets = re.split(r"(?<=[。！？!?])", text)
    result = [snippet for snippet in snippets if pattern.search(snippet) and not _CONDITIONAL.search(snippet)]
    if not result and pattern.search(text) and not _CONDITIONAL.search(text):
        result.append(text)
    return result


def _conflict_kind(row, text):
    if not _matching_keys(row, text):
        return ""
    if row.get("change") == "lost":
        if _change_snippets(text, _RESTORE):
            return "groundless_restore"
        if _change_snippets(text, _LOSS):
            return "repeated_permanent_loss"
    if row.get("change") == "gained":
        if re.search(r"(?:再次|又|重新)(?:获得|得到|取得)", text):
            return "repeated_permanent_gain"
        if re.search(r"(?:凭空|忽然|不知何时)(?:消失|失去)", text):
            return "groundless_permanent_loss"
    return ""


def permanent_state_findings(text, state, chapter, bible=None):
    findings = []
    spans = _paragraph_spans(text)
    for fact in permanent_states(state, bible):
        if fact.get("chapter", chapter) >= chapter:
            continue
        for number, (start, end) in enumerate(spans, 1):
            if not (kind := _conflict_kind(fact, text[start:end])):
                continue
            matching = sorted(_matching_keys(fact, text[start:end]))
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
                    "name": fact.get("name", ""),
                    "state_key": matching[0] if matching else "",
                    "start": start,
                    "end": end,
                }
            )
            break
    return findings


def _review(ask, client, prefix, text, state, chapter, suffix, bible=None):
    facts = permanent_states(state, bible)
    spans = _paragraph_spans(text)
    if not facts:
        return []
    deterministic = permanent_state_findings(text, state, chapter, bible)
    if not deterministic:
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
    return list(
        {(row["id"], row["kind"], row["previous_chapter"]): row for row in [*deterministic, *grounded]}.values()
    )


def repair_permanent_states(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Target permanent-state conflicts, recheck semantically, and otherwise roll back."""
    chapter = outline.get("number", 0)
    before = _review(ask, client, prefix, text, state, chapter, "-permanent-review", bible)
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
            ("永久状态", json.dumps(permanent_states(state, bible), ensure_ascii=False)),
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
    after = _review(ask, client, prefix, candidate, state, chapter, "-permanent-recheck", bible)
    note["after"] = after
    if after:
        return text, raw, failed, dict(note, status="rolled_back", reason="permanent_recheck_failed")
    checked, invalid, _ = extract(client, prefix + "-permanent-state", candidate, state, bible, outline, [], final=True)
    if invalid:
        return text, raw, failed, dict(note, status="rolled_back", reason="permanent_state_recheck_failed")
    return candidate, checked, False, dict(note, status="repaired")


def _ledger_checkup(state, bible):
    first = {}
    issues = []
    for row in sorted(permanent_states(state, bible), key=lambda item: item.get("chapter", 0)):
        for key in row.get("state_keys", []):
            identity = (key, row.get("change"))
            previous = first.get(identity)
            if previous:
                issues.append(
                    {
                        "id": 1,
                        "kind": "repeated_permanent_loss" if row.get("change") == "lost" else "repeated_permanent_gain",
                        "chapter": row.get("chapter"),
                        "previous_chapter": previous.get("chapter"),
                        "previous_quote": previous.get("quote"),
                        "current_quote": row.get("quote"),
                        "reason": "同一主体的同类永久状态被重复入账",
                        "status": "Inference",
                        "name": row.get("name", ""),
                        "state_key": key,
                    }
                )
            else:
                first[identity] = row
    return list(
        {
            (row["chapter"], row["kind"], row.get("state_key"), row.get("previous_chapter")): row for row in issues
        }.values()
    )


def permanent_state_checkup(chapters, bible=None, state=None):
    if permanent_states(state or {}, bible):
        return _ledger_checkup(state, bible)
    state = {"continuity_ledger": {"permanent_states": []}}
    issues = []
    for chapter in sorted(chapters, key=lambda row: row.get("number", 0)):
        number, text = chapter.get("number", 0), chapter.get("text", "")
        for start, end in _paragraph_spans(text):
            quote = text[start:end]
            losses = _change_snippets(quote, _LOSS)
            restores = _change_snippets(quote, _RESTORE)
            if not (losses or restores):
                continue
            for snippet in [*losses, *restores]:
                issues.extend(permanent_state_findings(snippet, state, number, bible))
            actor = _offline_actor(quote, bible)
            if not actor or not losses:
                continue
            loss = "".join(losses)
            parts = _canonical_parts(loss)
            if not parts:
                continue
            category, concept = parts[0]
            delta = {
                "permanent_states": [
                    {
                        "name": actor,
                        "category": _CATEGORY_NAMES[category],
                        "subject": _CONCEPT_NAMES.get(concept, concept),
                        "change": "lost",
                        "summary": loss[:120],
                        "quote": loss,
                    }
                ]
            }
            state = merge_permanent_delta(state, delta, loss, number, bible)
    return list(
        {
            (row["chapter"], row["kind"], row.get("state_key"), row.get("previous_chapter")): row for row in issues
        }.values()
    )
