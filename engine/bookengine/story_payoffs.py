"""Ground scheduled promise payoffs and opponent exits in adopted chapter prose."""

import json
import re

from .checks import high_confidence
from .promise_io import precise_ask
from .q1_continuity import boundary_facts
from .retired_opponents import opponent_hits
from .text import _sentences
from tools.book_checkup import clean_prose


REVIEW_SCHEMA = json.dumps(
    {"items": [{"id": "", "status": "unknown", "evidence": [{"chapter": 1, "quote": ""}], "reason": ""}]},
    ensure_ascii=False,
)


def due_story_payoffs(outline):
    """Return natural-language events due in this exact chapter."""
    number = outline.get("number", 0)
    items = []
    payoff = outline.get("promise_payoff")
    if isinstance(payoff, dict) and outline.get("reader_promise_id"):
        items.append(
            {
                "id": f"promise-{number}-{outline['reader_promise_id']}",
                "kind": "promise",
                "requirement": (
                    f"{payoff.get('action')}真实发生；面对{payoff.get('target')}，"
                    f"人物行动直接造成{payoff.get('result')}"
                ),
            }
        )
    resolution = outline.get("opponent_resolution")
    if isinstance(resolution, dict) and resolution.get("name"):
        items.append(
            {
                "id": f"opponent-{number}-{resolution['name']}",
                "kind": "opponent_exit",
                "requirement": (
                    f"{resolution.get('name')}{resolution.get('type')}，"
                    f"并让{resolution.get('outcome')}成为不可逆的现场结果"
                ),
            }
        )
    return items


def _verified(response, item, text, number):
    supplied = response.get("items", []) if isinstance(response, dict) else []
    matching = [row for row in supplied if isinstance(row, dict) and row.get("id") == item["id"]]
    row = matching[0] if len(matching) == 1 else {}
    evidence = row.get("evidence", [])
    grounded = (
        [
            proof
            for proof in evidence
            if isinstance(proof, dict)
            and proof.get("chapter") == number
            and isinstance(proof.get("quote"), str)
            and proof["quote"].strip()
            and proof["quote"] in text
        ]
        if isinstance(evidence, list)
        else []
    )
    status = (
        "fulfilled"
        if row.get("status") == "fulfilled" and grounded
        else "missing"
        if row.get("status") == "missing"
        else "unknown"
    )
    return dict(item, status=status, evidence=grounded, reason=row.get("reason", "缺少正文逐字证据"))


def review_story_payoffs(client, prefix, text, outline, ask):
    items = due_story_payoffs(outline)
    if not items:
        return {"status": "not_due", "items": []}
    response = precise_ask(
        ask,
        client,
        prefix + "-prose-review",
        "逐项核对这些事件是否已在本章真实发生。只引用本章逐字原文；计划、设想、转述和对手自行离开不算。"
        "不确定返回unknown，状态只用fulfilled/missing/unknown。",
        [
            ("本章必须发生的事", json.dumps(items, ensure_ascii=False)),
            ("本章正文", text),
            ("章号", str(outline["number"])),
        ],
        REVIEW_SCHEMA,
    )
    checked = [_verified(response, item, text, outline["number"]) for item in items]
    return {
        "status": "checked" if all(item["status"] == "fulfilled" for item in checked) else "unfulfilled",
        "items": checked,
        "authority": "agent_editorial_not_human",
    }


def _tail_spans(text):
    return list(re.finditer(r"\S[\s\S]*?(?=\n\s*\n|$)", text))


def _apply_tail_edit(text, spans, response, targets):
    edits = response.get("edits") if isinstance(response, dict) else None
    if not isinstance(edits, list) or len(edits) != 1 or not isinstance(edits[0], dict):
        return text, [], "invalid_edit_schema"
    edit = edits[0]
    index, replacement = edit.get("id"), edit.get("text")
    if type(index) is not int or index not in targets:
        return text, [], "invalid_target_id"
    span = spans[index - 1]
    if edit.get("original") != span.group():
        return text, [], "original_mismatch"
    if not isinstance(replacement, str) or not replacement.strip() or re.search(r"\n\s*\n", replacement):
        return text, [], "invalid_replacement"
    candidate = text[: span.start()] + replacement.strip() + text[span.end() :]
    return candidate, edits, "valid" if candidate != text else "unchanged"


def repair_story_payoffs(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    before = review_story_payoffs(client, prefix, text, outline, ask)
    note = {"status": before["status"], "before": before, "after": before, "edits": []}
    missing = [item for item in before["items"] if item["status"] == "missing"]
    if before["status"] != "unfulfilled" or not missing:
        return text, raw, failed, note
    spans = _tail_spans(text)
    targets = set(range(max(1, len(spans) - 1), len(spans) + 1))
    allowed = [{"id": index, "original": spans[index - 1].group()} for index in sorted(targets)]
    response = precise_ask(
        ask,
        client,
        prefix + "-tail-repair",
        "只改一个允许的结尾段，让缺失事件在现场自然发生。保留人物、地点、既有行动和前文事实；"
        "返回一项edit，id和original逐字对应允许段，text只含一个完整自然段。",
        [
            ("缺失事件", json.dumps(missing, ensure_ascii=False)),
            ("只允许修改的结尾段", json.dumps(allowed, ensure_ascii=False)),
            ("只读正文", text),
        ],
        '{"edits":[{"id":1,"original":"原段","text":"替换后的自然段"}]}',
        cap=3500,
    )
    candidate, edits, reason = _apply_tail_edit(text, spans, response, targets)
    note.update(edits=edits, repair_reason=reason)
    if reason != "valid" or clean_prose(candidate)[0] != candidate:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_tail_rewrite")
    after = review_story_payoffs(client, prefix + "-recheck", candidate, outline, ask)
    note["after"] = after
    if after["status"] != "checked":
        return text, raw, failed, dict(note, status="rolled_back", reason="payoff_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-payoff-final-state", candidate, state, bible, outline, [], final=True
    )
    old_boundary, new_boundary = boundary_facts(raw, text), boundary_facts(checked, candidate)
    boundary_lost = any(
        old_boundary[key] and new_boundary[key] != old_boundary[key] for key in ("opening_location", "end_location")
    )
    if (
        invalid
        or boundary_lost
        or high_confidence(state, checked.get("events", []), candidate)
        or opponent_hits(candidate, bible, outline["number"])
    ):
        return text, raw, failed, dict(note, status="rolled_back", reason="final_state_or_continuity_failed")
    return candidate, checked, False, dict(note, status="repaired")


_RESOLUTION_WORDS = {
    "被杀": re.compile(r"被杀|身亡|死(?:了|亡)?|斩杀|击毙|毙命"),
    "被擒": re.compile(r"被擒|被捕|被绑|捆住|押下|关入|囚禁|戴上镣铐"),
    "被废": re.compile(r"被废|废掉|修为尽失|筋脉尽断|再无战力"),
    "当众击败后失势": re.compile(r"当众击败|失势|失去(?:权力|人手|地位|资源)|无人再听"),
}


def _surface_resolution(text, resolution):
    name = resolution.get("name")
    pattern = _RESOLUTION_WORDS.get(resolution.get("type"))
    if not isinstance(name, str) or not name or pattern is None:
        return None
    return next(
        (
            text[start:end]
            for start, end in _sentences(text)
            if name in text[start:end] and pattern.search(text[start:end])
        ),
        None,
    )


def _saved_item(meta, item_id, text):
    found = []

    def visit(value):
        if isinstance(value, dict):
            for row in value.get("items", []) if isinstance(value.get("items"), list) else []:
                if isinstance(row, dict) and row.get("id") == item_id:
                    found.append(row)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(meta.get("story_payoffs", {}) if isinstance(meta, dict) else {})
    for row in reversed(found):
        grounded = [
            proof
            for proof in row.get("evidence", [])
            if isinstance(proof, dict)
            and isinstance(proof.get("quote"), str)
            and proof["quote"].strip()
            and proof["quote"] in text
        ]
        if row.get("status") == "fulfilled" and grounded:
            return "fulfilled", grounded
    if any(row.get("status") == "missing" for row in found):
        return "missing", []
    return None, []


def story_payoff_checkup(bible, chapters):
    """Expose grounded saved reviews, with conservative surface inference for scheduled exits."""
    mapped = {row.get("number"): row for row in chapters if isinstance(row, dict)}
    results = []
    for stage in bible.get("volumes", []) if isinstance(bible.get("volumes"), list) else []:
        scheduled = {}
        for payoff in stage.get("promise_payoffs", []) if isinstance(stage.get("promise_payoffs"), list) else []:
            if isinstance(payoff, dict):
                scheduled.setdefault(payoff.get("chapter"), {})["reader_promise_id"] = payoff.get("promise_id")
                scheduled[payoff.get("chapter")]["promise_payoff"] = payoff
        resolution = stage.get("opponent_resolution")
        if isinstance(resolution, dict):
            scheduled.setdefault(resolution.get("chapter"), {})["opponent_resolution"] = resolution
        for number, fields in scheduled.items():
            chapter = mapped.get(number, {})
            text, meta = chapter.get("text", ""), chapter.get("meta", {})
            for item in due_story_payoffs(dict(number=number, **fields)):
                result, evidence = _saved_item(meta, item["id"], text)
                status = "Evidence" if result == "fulfilled" else "Inference" if result == "missing" else "Unknown"
                if result is None and item["kind"] == "opponent_exit" and text:
                    quote = _surface_resolution(text, fields["opponent_resolution"])
                    result, evidence, status = (
                        ("fulfilled", [{"chapter": number, "quote": quote}], "Inference")
                        if quote
                        else (
                            "missing",
                            [],
                            "Inference",
                        )
                    )
                results.append(
                    dict(
                        chapter=number,
                        **item,
                        result=result or "Unknown",
                        status=status,
                        evidence=evidence,
                        authority="agent_editorial_not_human" if status != "Evidence" else "grounded_saved_review",
                    )
                )
    return results
