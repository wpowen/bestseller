"""Bounded prose repair when a planned relationship chapter becomes another type."""

from .checks import high_confidence
from .chapter_rhythm import chapter_type, relationship_contract
from .promise_io import precise_ask
from .text import _paragraph_spans
from tools.book_checkup import clean_prose


def _grounded_payoff(raw, text):
    payoff = raw.get("payoff") if isinstance(raw, dict) else None
    quote = payoff.get("quote") if isinstance(payoff, dict) else None
    return bool(payoff and payoff.get("fulfilled") is True and isinstance(quote, str) and quote in text)


def _spread(indexes, limit=16):
    if len(indexes) <= limit:
        return indexes
    return sorted({indexes[round(position * (len(indexes) - 1) / (limit - 1))] for position in range(limit)})


def repair_relationship_rhythm(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    contract = relationship_contract(outline)
    before_type = chapter_type(raw if isinstance(raw, dict) else {})
    note = {
        "status": "checked",
        "planned": contract,
        "before_type": before_type,
        "after_type": before_type,
        "edits": [],
    }
    if chapter_type(outline) != "情感" or before_type == "情感" or not contract:
        return text, raw, failed, note
    spans = _paragraph_spans(text)
    people = contract["people"]
    matching = [
        index for index, (start, end) in enumerate(spans, 1) if any(person in text[start:end] for person in people)
    ]
    selected = _spread(matching)
    if len(selected) < min(16, len(spans)):
        selected = _spread(sorted(set(selected + list(range(1, len(spans) + 1)))))
    targets = [dict(id=index, original=text[spans[index - 1][0] : spans[index - 1][1]]) for index in selected]
    response = precise_ask(
        ask,
        client,
        prefix + "-relationship-rhythm-repair",
        "只改给出的主场景段，长章至少改8段（可改段不足8段则全改），使两场戏的主要变化成为指定人物关系合同；"
        "以坦白、反应和当场选择推动关系，不得把定规则、排路线或分工写成主要结果。正面打斗移到章外。"
        "保留已发生事件、地点、兑现、代价和后果，不得只把标签改成情感。",
        [("关系变化合同", contract), ("可改主场景段", targets), ("完整正文只读", text)],
        '{"edits":[{"id":1,"original":"原段","text":"替换后的完整自然段"}]}',
        cap=6500,
        required_sources=[row["original"] for row in targets],
    )
    if isinstance(response, dict) and response.get("status") == "skipped":
        return text, raw, failed, dict(note, status="skipped", reason=response.get("reason"))
    edits = response.get("edits") if isinstance(response, dict) else None
    if not isinstance(edits, list) or not edits:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_edits")
    minimum_edits = 8 if len(targets) >= 8 else len(targets)
    if len(edits) < minimum_edits:
        return text, raw, failed, dict(note, status="rolled_back", reason="insufficient_main_scene_edits")
    by_id = {row["id"]: row for row in targets}
    candidate = text
    for edit in sorted(edits, key=lambda row: row.get("id", 0), reverse=True):
        source = by_id.get(edit.get("id"))
        replacement = edit.get("text")
        if (
            not source
            or edit.get("original") != source["original"]
            or not isinstance(replacement, str)
            or not replacement.strip()
        ):
            return text, raw, failed, dict(note, status="rolled_back", reason="invalid_edit_target")
        start, end = spans[edit["id"] - 1]
        candidate = candidate[:start] + replacement.strip() + candidate[end:]
    if clean_prose(candidate)[0] != candidate or any(person not in candidate for person in people):
        return text, raw, failed, dict(note, status="rolled_back", reason="relationship_contract_not_grounded")
    checked, invalid, _ = extract(
        client, prefix + "-relationship-final-state", candidate, state, bible, outline, [], final=True
    )
    after_type = chapter_type(checked if isinstance(checked, dict) else {})
    note.update(after_type=after_type, edits=edits)
    payoff_lost = _grounded_payoff(raw, text) and not _grounded_payoff(checked, candidate)
    reason = (
        "state_recheck_failed"
        if invalid
        else "after_type_mismatch"
        if after_type != "情感"
        else "lost_grounded_payoff"
        if payoff_lost
        else "hard_fact_conflict"
        if high_confidence(state, checked.get("events", []), candidate)
        else None
    )
    if reason:
        return text, raw, failed, dict(note, status="rolled_back", reason=reason)
    return candidate, checked, False, dict(note, status="checked")
