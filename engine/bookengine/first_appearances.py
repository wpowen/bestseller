"""Writer context, bounded repair and checkup for first-use world terms."""

import json
from pathlib import Path

from .introductions import check_introductions, check_world_first_appearances, world_term_registry
from .promise_io import precise_ask
from .text import _paragraph_spans
from tools.book_checkup import clean_prose


def _prior_text(out, number):
    parts = []
    for path in sorted(Path(out).glob("chapter-*.body.md")):
        try:
            chapter = int(path.name.removeprefix("chapter-").removesuffix(".body.md"))
        except ValueError:
            continue
        if chapter < number:
            parts.append(path.read_text("utf-8"))
    return "\n".join(parts)


def _character_names(bible):
    return [
        card["name"]
        for card in bible.get("characters", [])
        if isinstance(bible.get("characters"), list)
        if isinstance(card, dict) and isinstance(card.get("name"), str) and card["name"]
    ]


def _character_cards(bible):
    return [card for card in bible.get("characters", []) if isinstance(card, dict) and card.get("name")]


def _character_definition(card):
    for field in ("identity", "background", "origin", "身份", "背景", "来历"):
        value = card.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "人物卡中的既定身份"


def due_first_appearances(out, number, bible, outline):
    registry = world_term_registry(bible)
    prior = _prior_text(out, number)
    planned = json.dumps(outline, ensure_ascii=False)
    due = [dict(term=term, **card) for term, card in registry.items() if term in planned and term not in prior]
    due.extend(
        dict(term=card["name"], kind="character", definition=_character_definition(card))
        for card in _character_cards(bible)
        if card["name"] in planned and card["name"] not in prior
    )
    return due


def writer_first_appearance_instruction(out, number, bible, outline):
    due = due_first_appearances(out, number, bible, outline)
    if not due:
        return ""
    return (
        "\n本章首次使用以下世界术语时，第一次写出它的句子就用动作、对白或一句叙述说明它是什么；"
        "不得先裸用、后补定义：\n" + "\n".join(f"- {row['term']}：{row['definition']}" for row in due)
    )


def _problems(text, bible, prior, *, include_characters=True):
    world = [
        row
        for row in check_world_first_appearances(
            text,
            world_term_registry(bible),
            previous_text=prior,
            character_names=_character_names(bible),
        )
        if row["status"] != "Evidence"
    ]
    if not include_characters:
        return world
    cards = _character_cards(bible)
    seen = [card["name"] for card in cards if card["name"] in prior]
    characters = [
        row
        for row in check_introductions(text, cards, previously_seen=seen)
        if row.get("kind") == "missing_introduction"
    ]
    return world + characters


def _targets(text, issues):
    spans = _paragraph_spans(text)
    indexes = {
        index for index, (start, end) in enumerate(spans, 1) if any(start <= issue["start"] < end for issue in issues)
    }
    return spans, [
        dict(id=index, original=text[start:end]) for index, (start, end) in enumerate(spans, 1) if index in indexes
    ]


def repair_first_appearances(client, prefix, text, raw, failed, state, bible, outline, out, extract, ask):
    prior = _prior_text(out, outline["number"])
    # The finish extraction can already carry a cast edit through the older
    # amend/recheck path. Do not pay for a second character rewrite after that
    # path attempted (and possibly rolled back) the same introduction.
    attempted_cast_edit = isinstance(raw, dict) and bool(raw.get("edits"))
    before = _problems(text, bible, prior, include_characters=not attempted_cast_edit)
    note = {"status": "checked" if not before else "unfulfilled", "before": before, "after": before, "edits": []}
    if not before:
        return text, raw, failed, note
    spans, targets = _targets(text, before)
    response = precise_ask(
        ask,
        client,
        prefix + "-first-appearance-repair",
        "只改首次出现所在段：术语或人物第一次出现的句子就用自然动作、对白或一句叙述交代所给定义/身份；"
        "指称不明时改用已有人物或生物原名，不造新实体。保留段内事件、人物、数字和结果。",
        [
            ("缺失项及圣经定义", before),
            ("已有角色", _character_names(bible)),
            ("只可修改的首次出现段", targets),
        ],
        '{"edits":[{"id":1,"original":"原段","text":"替换后的完整自然段"}]}',
        cap=5000,
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
    after = _problems(candidate, bible, prior, include_characters=not attempted_cast_edit)
    note.update(after=after, edits=edits)
    if after or clean_prose(candidate)[0] != candidate:
        return text, raw, failed, dict(note, status="rolled_back", reason="first_appearance_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-first-appearance-final-state", candidate, state, bible, outline, [], final=True
    )
    if invalid:
        return text, raw, failed, dict(note, status="rolled_back", reason="final_state_failed")
    return candidate, checked, False, dict(note, status="checked")


def first_appearance_checkup(bible, chapters):
    registry = world_term_registry(bible)
    names = _character_names(bible)
    previous = ""
    findings = []
    for chapter in sorted(chapters, key=lambda row: row["number"]):
        rows = check_world_first_appearances(chapter["text"], registry, previous, names)
        findings.extend(dict(chapter=chapter["number"], **row) for row in rows)
        previous += "\n" + chapter["text"]
    return {
        "missing_terms": [row for row in findings if row["kind"] == "term_first_use_missing"],
        "introduced_terms": [row for row in findings if row["kind"] == "term_introduction_present"],
        "unresolved_entities": [row for row in findings if row["kind"] == "unresolved_entity_reference"],
        "basis": "first literal use in saved prose; deterministic local binding",
        "status": "Inference",
    }
