"""Small promise-repair inputs and atomic exact-paragraph edit validation."""

import json
import re

from .promise_io import precise_ask


def _sections(pending, allowed, bible, text):
    # Keep complete chapter text, power rules and the exact obligation sources;
    # unrelated volumes, canon and the duplicated opening_contract are not input.
    fields = ("id", "kind", "requirement", "source", "name", "speaker", "address", "origin")
    obligations = [{k: item[k] for k in fields if k in item} for item in pending]
    names = {i.get(k) for i in pending for k in ("name", "speaker")}
    characters = [
        {k: c[k] for k in ("name", "address", "address_target", "address_origin") if k in c}
        for c in bible.get("characters", [])
        if c.get("name") in names
    ]
    facts = dict(title=bible.get("title", ""), power=bible.get("power", {}), characters=characters)
    return [
        ("缺失承诺", json.dumps(obligations, ensure_ascii=False)),
        ("允许修改的段", json.dumps(allowed, ensure_ascii=False)),
        ("只读圣经", json.dumps(facts, ensure_ascii=False)),
        ("只读正文", text),
    ]


def _apply(response, text, spans, targets):
    if isinstance(response, dict) and "context_unknown" in response:
        return text, [], "context_unknown"
    edits = response.get("edits") if isinstance(response, dict) else None
    if not isinstance(edits, list):
        return text, [], "invalid_edit_schema"
    if not edits:
        return text, [], "empty_edits"
    patches, used = [], set()
    for edit in edits:
        if not isinstance(edit, dict):
            return text, [], "invalid_edit_schema"
        idx, replacement = edit.get("id"), edit.get("text")
        if type(idx) is not int or idx not in targets or idx in used:
            return text, [], "invalid_target_id"
        if not isinstance(replacement, str) or not replacement.strip():
            return text, [], "empty_replacement"
        span = spans[idx - 1]
        if edit.get("original") != span.group():
            return text, [], "original_mismatch"
        if re.search(r"\n\s*\n", replacement):
            return text, [], "multiple_paragraphs"
        used.add(idx)
        patches.append((span.start(), span.end(), replacement))
    candidate = text
    for start, end, replacement in sorted(patches, reverse=True):
        candidate = candidate[:start] + replacement + candidate[end:]
    return candidate, edits, "valid" if candidate != text else "unchanged_edits"


def targeted_edits(ask, client, prefix, text, spans, targets, pending, bible):
    allowed = [dict(id=i, original=s.group()) for i, s in enumerate(spans, 1) if i in targets]
    sections = _sections(pending, allowed, bible, text)
    attempts = []
    candidate, edits = text, []
    for attempt in range(2):
        key = prefix + "-promise-repair" + ("-retry-1" if attempt else "")
        response = precise_ask(
            ask,
            client,
            key,
            "只改允许的段落，兑现缺失承诺或称呼来历。保留现有事件、"
            "人物、地点、过渡和末场结果。返回edits，每项包含id/original/text；"
            "id必须对应允许段号，original必须逐字复制该段完整原文；每项text只含一段。",
            sections,
            '{"edits":[{"id":1,"original":"原段","text":"改段"}]}',
            cap=4500,
        )
        candidate, edits, reason = _apply(response, text, spans, targets)
        row = dict(key=key, reason=reason, targets=sorted(targets))
        if reason == "context_unknown":
            row["context_unknown"] = response["context_unknown"]
        attempts.append(row)
        if reason == "valid":
            break
        sections = _sections(pending, allowed, bible, text) + [("上次失败", reason)]
    return candidate, edits, attempts
