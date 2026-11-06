"""Quote-grounded chapter handoffs and opening-only repairs."""

import json
import re

from .text import _paragraph_spans
from .stage_transition import stage_transition, _transition_quotes, _ELAPSED, _SCENE
from .handoff_places import same_place
from .boundary_state import handoff_reference, advance_boundary_state, with_ending_candidate


OPENING_RULE = (
    "开头300字内：写出从参考地点到本章地点的移动过程；或者写出明确的时间或场景切换并交代本章地点。"
    "如‘抵达本章地点时’的入场时间句也可明确切换场景。同一处地点的不同说法可直接承接。"
    "不臆造人物曾在上章末场；参考视角位置未知时明确本章地点。"
)


def boundary_facts(raw, text):
    """Missing or ungrounded observations stay unknown, never inherit old facts."""
    spans = _paragraph_spans(text)
    opening = text[: spans[min(1, len(spans) - 1)][1]] if spans else ""
    ending = text[-1200:]
    result = {}
    for side, passage in (("opening", opening), ("end", ending)):
        location, quote = raw.get(side + "_location"), raw.get(side + "_quote")
        valid = (
            isinstance(location, str)
            and 0 < len(location.strip()) <= 80
            and isinstance(quote, str)
            and bool(quote.strip())
            and quote in passage
            and location in quote
        )
        result[side + "_location"] = location.strip() if valid else None
        result[side + "_quote"] = quote if valid else None
    quotes = raw.get("present_quotes")
    quotes = quotes if isinstance(quotes, dict) else {}
    present = raw.get("present")
    result["present"] = (
        [
            name
            for name in dict.fromkeys(n for n in present if isinstance(n, str) and n)
            if isinstance(quotes.get(name), str)
            and bool(quotes[name])
            and quotes[name] in ending
            and name in quotes[name]
        ]
        if isinstance(present, list)
        else []
    )
    result["present_status"] = (
        "grounded" if isinstance(present, list) and len(result["present"]) == len(present) else "unknown"
    )
    for side, passage in (("opening", opening), ("end", ending)):
        name, quote = raw.get(side + "_pov"), raw.get(side + "_pov_quote")
        valid = (
            isinstance(name, str)
            and bool(name)
            and isinstance(quote, str)
            and bool(quote)
            and name in quote
            and quote in passage
        )
        result[side + "_pov"] = name if valid else None
        result[side + "_pov_quote"] = quote if valid else None
    result["character_locations"] = (
        [
            row
            for row in raw.get("character_locations", [])
            if isinstance(row, dict)
            and all(isinstance(row.get(k), str) and row[k] for k in ("name", "location", "quote"))
            and row["name"] in row["quote"]
            and row["location"] in row["quote"]
            and row["quote"] in text
        ]
        if isinstance(raw.get("character_locations"), list)
        else []
    )
    return result


def opening_issue(previous, facts, text, bible=None):
    if previous.get("end_pov") or previous.get("pov_locations"):
        previous = handoff_reference(previous, facts)
    old, new = previous.get("end_location"), facts.get("opening_location")
    if not old or not new or same_place(old, new, bible, previous.get("known_locations", [])):
        return None
    opening = text[:300]
    quotes = _transition_quotes(opening)
    if new in opening and any(
        any(same_place(old, place, bible) and place in q for place in [old, *previous.get("known_locations", [])])
        or _ELAPSED.search(q)
        or _SCENE.search(q)
        or re.search(r"(?:追进|赶到|抵达|钻进|拐进|回到)" + re.escape(new) + r"时[，,]", q)
        for q in quotes
    ):
        return None
    return dict(kind="location_break", previous=old, opening=new, present=previous.get("present", []))


def opening_patch(text, raw):
    """Replace at most the first two paragraphs, retaining the suffix verbatim."""
    count, paragraphs = raw.get("replace_count"), raw.get("paragraphs")
    spans = _paragraph_spans(text)
    if (
        type(count) is not int
        or not 1 <= count <= min(2, len(spans))
        or not isinstance(paragraphs, list)
        or len(paragraphs) != count
        or any(not isinstance(p, str) or not p.strip() or "\n" in p or "\r" in p or len(p) > 1200 for p in paragraphs)
    ):
        return text
    return "\n\n".join(p.strip() for p in paragraphs) + text[spans[count - 1][1] :]


def evaluate_handoff(state, facts, text, bible, outline):
    """Shared repair/diagnostic standard, retaining Unknown rather than inventing a route."""
    reference = handoff_reference(state, facts)
    transition = stage_transition(bible, outline.get("number"), text, reference.get("end_location"), outline=outline)
    issue = opening_issue(reference, facts, text, bible)
    if transition and transition["issue"]:
        issue = transition
    elif transition and transition["status"] == "present":
        issue = None
    missing = [k for k in ("opening_location", "end_location") if not facts.get(k)]
    if outline.get("number", 0) > 1 and not reference.get("end_location"):
        missing.append("pov_location" if reference["basis"] == "viewpoint_last_location" else "previous_end")
    if "pov_location" in missing:
        issue = None
    note = dict(status="handoff_unknown" if missing else "checked", issue=issue, missing=missing, reference=reference)
    if transition:
        note["transition"] = transition
    return note


def repair_opening(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    facts = boundary_facts(raw, text)
    retried = not facts["opening_location"] or not facts["end_location"]
    if retried:
        saved_prefix = getattr(client, "prefix", "")
        if client is not None:
            client.prefix = ""
        try:
            observed = ask(
                client,
                prefix + "-boundary-reextract",
                "只重抽开头与结尾当前地点，不推测、不沿用规划。每个地点须在对应逐字引文里；"
                "找不到返回null。present仅列结尾实际在场者并逐人引文。"
                "opening_pov/end_pov分别为开头/结尾视角人物，附opening_pov_quote/end_pov_quote逐字引文，"
                "不把仅被提及者当视角，不确定填null。返回JSON。",
                [("开头", text[:1200]), ("结尾", text[-1200:])],
                '{"opening_location":null,"opening_quote":null,"end_location":null,"end_quote":null}',
                cap=1200,
                input_limit=4000,
                max_attempts=1,
            )
        finally:
            if client is not None:
                client.prefix = saved_prefix
        if isinstance(observed, dict):
            recovered = boundary_facts(observed, text)
            for side in ("opening", "end"):
                if not facts[side + "_location"] and recovered[side + "_location"]:
                    raw = dict(raw)
                    raw.update(
                        {side + "_location": recovered[side + "_location"], side + "_quote": recovered[side + "_quote"]}
                    )
                if not facts.get(side + "_pov") and recovered.get(side + "_pov"):
                    raw = dict(
                        raw,
                        **{
                            side + "_pov": recovered[side + "_pov"],
                            side + "_pov_quote": recovered[side + "_pov_quote"],
                        },
                    )
            facts = boundary_facts(raw, text)
    facts = with_ending_candidate(facts, text, bible, state)
    note = evaluate_handoff(state, facts, text, bible, outline)
    transition, issue = note.get("transition"), note["issue"]
    note["boundary_retried"] = retried
    if not issue:
        return text, raw, failed, facts, note
    spans = _paragraph_spans(text)
    if not spans:
        return text, raw, failed, facts, dict(note, status="rolled_back")
    opening = text[: spans[min(1, len(spans) - 1)][1]]
    patch = ask(
        client,
        prefix + "-opening-repair",
        "只重写开头一到两段，paragraphs条数必须等于replace_count。"
        + OPENING_RULE
        + "不得改变或复述后文，不改变人物事实、时序和冲突结果。返回JSON。",
        [
            ("衔接验收标准", OPENING_RULE),
            (
                "上章结尾地点原文、在场人物与本章开头地点",
                json.dumps(
                    dict(
                        previous_end=state.get("end_location"),
                        reference=note["reference"],
                        present=state.get("present", []),
                        opening_location=facts.get("opening_location"),
                    ),
                    ensure_ascii=False,
                ),
            ),
            ("断裂与上章约束", str(issue)),
            ("只可修改的开头", opening),
            ("后文只读", text[len(opening) : len(opening) + 600]),
        ],
        '{"replace_count":1,"paragraphs":["替换开头段"]}',
        cap=2000,
        input_limit=10000,
    )
    candidate = opening_patch(text, patch if isinstance(patch, dict) else {})
    reason = "invalid_or_unchanged_patch"
    if candidate != text:
        checked, invalid, _ = extract(
            client, prefix + "-opening-recheck", candidate, state, bible, outline, [], final=True
        )
        observed = boundary_facts(checked, candidate)
        assessment = evaluate_handoff(state, observed, candidate, bible, outline)
        rechecked = assessment.get("transition")
        from .checks import high_confidence

        continuous = (
            rechecked["status"] == "present"
            if transition and transition["status"] == "missing" and rechecked
            else bool(observed.get("opening_location"))
            and not assessment["issue"]
            and "pov_location" not in assessment["missing"]
        )
        if not invalid and continuous and not high_confidence(state, checked.get("events", []), candidate):
            repaired = dict(note, status="repaired", recheck=assessment)
            if rechecked:
                repaired["transition"] = rechecked
            return candidate, checked, False, observed, repaired
        reason = (
            "invalid_reextraction"
            if invalid
            else "opening_still_disconnected"
            if not continuous
            else "hard_fact_conflict"
        )
    return text, raw, failed, facts, dict(note, status="rolled_back", rollback_reason=reason)
