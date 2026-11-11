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
    selected = [
        index for index, (start, end) in enumerate(spans, 1) if any(person in text[start:end] for person in people)
    ][:2]
    if not selected:
        selected = list(range(1, min(2, len(spans)) + 1))
    targets = [dict(id=index, original=text[spans[index - 1][0] : spans[index - 1][1]]) for index in selected]
    response = precise_ask(
        ask,
        client,
        prefix + "-relationship-rhythm-repair",
        "只改给出的主场景段，使两场戏的主要变化成为指定人物关系合同；正面打斗移到章外。"
        "保留已发生事件、地点、兑现、代价和后果，不得只把标签改成情感。",
        [("关系变化合同", contract), ("可改主场景段", targets)],
        '{"edits":[{"id":1,"original":"原段","text":"替换后的完整自然段"}]}',
        cap=5500,
        required_sources=[row["original"] for row in targets],
    )
    if isinstance(response, dict) and response.get("status") == "skipped":
        return text, raw, failed, dict(note, status="skipped", reason=response.get("reason"))
    edits = response.get("edits") if isinstance(response, dict) else None
    if not isinstance(edits, list) or not edits:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_edits")
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
    if invalid or after_type != "情感" or payoff_lost or high_confidence(state, checked.get("events", []), candidate):
        return text, raw, failed, dict(note, status="rolled_back", reason="relationship_recheck_failed")
    return candidate, checked, False, dict(note, status="checked")
