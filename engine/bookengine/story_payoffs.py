"""Ground scheduled promise payoffs and opponent exits in adopted chapter prose."""

import json
import re
from copy import deepcopy

from .checks import high_confidence
from .promise_io import precise_ask
from .retired_opponents import opponent_hits
from .text import _sentences
from tools.book_checkup import clean_prose


REVIEW_SCHEMA = json.dumps(
    {"items": [{"id": "", "status": "unknown", "evidence": [{"chapter": 1, "quote": ""}], "reason": ""}]},
    ensure_ascii=False,
)

_ESCAPE = re.compile(r"逃(?:走|离|脱)|脱身|退入|遁入|消失(?:在|于)|离开|撤离|远去|没有恋战|不再恋战|无心恋战")
_ESCAPE_BLOCK = re.compile(r"(?:不得|不能|不可|不许).{0,8}(?:逃(?:走|离|脱)|脱身|退入|遁入|消失|离开|撤离|远去)")


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


def _resolution_pattern(outline):
    resolution = outline.get("opponent_resolution") if isinstance(outline, dict) else None
    return _RESOLUTION_WORDS.get(resolution.get("type")) if isinstance(resolution, dict) else None


def _escape_conflict(text, outline):
    resolution = outline.get("opponent_resolution") if isinstance(outline, dict) else None
    if not isinstance(resolution, dict) or resolution.get("type") not in _RESOLUTION_WORDS:
        return False
    name, pattern = resolution.get("name"), _resolution_pattern(outline)
    return bool(
        isinstance(name, str)
        and name in text
        and _ESCAPE.search(text)
        and not _ESCAPE_BLOCK.search(text)
        and not pattern.search(text)
    )


def conflicting_payoff_paragraphs(text, outline):
    """Locate prose that lets an opponent escape a terminal scheduled outcome."""
    return [
        {"id": index, "quote": span.group().strip(), "reason": "opponent_escape"}
        for index, span in enumerate(_tail_spans(text), 1)
        if _escape_conflict(span.group(), outline)
    ]


def prepare_writer_payoffs(outline):
    """Remove contradictory escape instructions before the writing call."""
    current = deepcopy(outline)
    resolution = current.get("opponent_resolution") if isinstance(current, dict) else None
    if not isinstance(resolution, dict) or resolution.get("type") not in _RESOLUTION_WORDS:
        return current
    replacement = (
        f"{resolution.get('name')}{resolution.get('type')}，并让{resolution.get('outcome')}成为不可逆的现场结果。"
    )

    def clean(value):
        if isinstance(value, dict):
            return {key: clean(child) for key, child in value.items()}
        if isinstance(value, list):
            return [clean(child) for child in value]
        if isinstance(value, str) and _escape_conflict(value, current):
            return replacement
        return value

    return clean(current)


def writer_payoff_instruction(outline):
    """Put due events in the untruncated writer instruction, ahead of optional hooks."""
    items = due_story_payoffs(outline)
    if not items:
        return ""
    requirements = "\n".join(f"- {item['requirement']}" for item in items)
    return (
        "\n本章必须发生（优先于普通悬念、章末反转和后续伏笔）：\n"
        f"{requirements}\n"
        "正文不得把这些结果改成对手逃走、消失、脱身或继续掌控资源；必须写出人物行动、现场反应和直接后果。"
    )


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


def _apply_payoff_edits(text, spans, response, targets, required):
    edits = response.get("edits") if isinstance(response, dict) else None
    if not isinstance(edits, list) or not edits or any(not isinstance(edit, dict) for edit in edits):
        return text, [], "invalid_edit_schema"
    indexes = [edit.get("id") for edit in edits]
    if any(type(index) is not int or index not in targets for index in indexes) or len(set(indexes)) != len(indexes):
        return text, [], "invalid_target_id"
    if not required.issubset(indexes):
        return text, [], "conflict_not_rewritten"
    candidate = text
    for edit in sorted(edits, key=lambda row: row["id"], reverse=True):
        span, replacement = spans[edit["id"] - 1], edit.get("text")
        if edit.get("original") != span.group():
            return text, [], "original_mismatch"
        if not isinstance(replacement, str) or not replacement.strip() or re.search(r"\n\s*\n", replacement):
            return text, [], "invalid_replacement"
        candidate = candidate[: span.start()] + replacement.strip() + candidate[span.end() :]
    return candidate, edits, "valid" if candidate != text else "unchanged"


def repair_story_payoffs(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    before = review_story_payoffs(client, prefix, text, outline, ask)
    note = {"status": before["status"], "before": before, "after": before, "edits": []}
    missing = [item for item in before["items"] if item["status"] == "missing"]
    if before["status"] != "unfulfilled" or not missing:
        return text, raw, failed, note
    spans = _tail_spans(text)
    conflicts = conflicting_payoff_paragraphs(text, outline)
    note["conflicts_before"] = conflicts
    conflict_ids = {row["id"] for row in conflicts}
    start = max(1, min(conflict_ids) - 2) if conflict_ids else max(1, len(spans) - 1)
    targets = set(range(start, len(spans) + 1))
    allowed = [{"id": index, "original": spans[index - 1].group()} for index in sorted(targets)]
    response = precise_ask(
        ask,
        client,
        prefix + "-conflict-repair",
        "改写每个冲突段，让缺失事件在现场自然发生；必要时连同其后直到章末的后果一起重写，不能只在末段追加。"
        "保留不冲突的人物、地点和既有行动。返回一到多项edit，每项id和original逐字对应允许段，text只含一个完整自然段。",
        [
            ("缺失事件", json.dumps(missing, ensure_ascii=False)),
            ("必须改写的冲突段落", json.dumps(conflicts, ensure_ascii=False)),
            ("可修改的冲突段落及其后果", json.dumps(allowed, ensure_ascii=False)),
            ("只读正文", text),
        ],
        '{"edits":[{"id":1,"original":"原段","text":"替换后的自然段"}]}',
        cap=5000,
    )
    candidate, edits, reason = _apply_payoff_edits(text, spans, response, targets, conflict_ids)
    note.update(edits=edits, repair_reason=reason)
    if reason != "valid" or clean_prose(candidate)[0] != candidate:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_conflict_rewrite")
    remaining = conflicting_payoff_paragraphs(candidate, outline)
    note["conflicts_after"] = remaining
    if remaining:
        return text, raw, failed, dict(note, status="rolled_back", reason="conflicting_paragraphs_remain")
    after = review_story_payoffs(client, prefix + "-recheck", candidate, outline, ask)
    note["after"] = after
    if after["status"] != "checked":
        return text, raw, failed, dict(note, status="rolled_back", reason="payoff_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-payoff-final-state", candidate, state, bible, outline, [], final=True
    )
    continuity_failures = []
    if invalid:
        continuity_failures.append("invalid_final_state")
    if high_confidence(state, checked.get("events", []), candidate):
        continuity_failures.append("high_confidence_continuity_conflict")
    if opponent_hits(candidate, bible, outline["number"]):
        continuity_failures.append("retired_opponent_conflict")
    if continuity_failures:
        return (
            text,
            raw,
            failed,
            dict(
                note,
                status="rolled_back",
                reason="final_state_or_continuity_failed",
                continuity_failures=continuity_failures,
            ),
        )
    return candidate, checked, False, dict(note, status="checked", repair="adopted")


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
