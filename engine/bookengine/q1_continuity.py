"""Quote-grounded chapter handoffs and opening-only repairs."""

import re

from .text import _paragraph_spans
from .stage_transition import stage_transition


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
    return result


def opening_issue(previous, facts, text):
    old, new = previous.get("end_location"), facts.get("opening_location")
    if not old or not new or old == new:
        return None
    spans = _paragraph_spans(text)
    opening = text[: spans[min(1, len(spans) - 1)][1]] if spans else ""
    if (
        old in opening
        and new in opening
        and re.search(r"离开|走出|走进|抵达|赶到|来到|穿过|沿.{0,12}(?:走|行)|从.{0,25}(?:到|往|去)", opening)
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
                "找不到返回null。present仅列结尾实际在场者并逐人引文。返回JSON。",
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
            facts = boundary_facts(raw, text)
    transition = stage_transition(bible, outline.get("number"), text, state.get("end_location"), outline=outline)
    issue = transition if transition and transition["issue"] else opening_issue(state, facts, text)
    if transition and transition["status"] == "present":
        issue = None
    known = (
        bool(facts.get("opening_location") and state.get("end_location"))
        or (transition and transition["status"] != "unknown")
        or bool(outline.get("number") == 1 and facts.get("opening_location") and facts.get("end_location"))
    )
    note = dict(
        status="checked" if known else "unknown",
        issue=issue,
    )
    note["boundary_retried"] = retried
    note["missing"] = [k for k in ("opening_location", "end_location") if not facts[k]]
    if outline.get("number", 0) > 1 and not state.get("end_location"):
        note["missing"].append("previous_end")
    if note["missing"]:
        note["status"] = "handoff_unknown"
    if transition:
        note["transition"] = transition
    if not issue:
        return text, raw, failed, facts, note
    spans = _paragraph_spans(text)
    if not spans:
        return text, raw, failed, facts, dict(note, status="rolled_back")
    opening = text[: spans[min(1, len(spans) - 1)][1]]
    patch = ask(
        client,
        prefix + "-opening-repair",
        "只重写开头一到两段，paragraphs条数必须等于replace_count。先承接上章地点和在场人物，"
        "在开头300字内补上移动、时间流逝或明确场景切换。"
        "不得改变或复述后文，不改变人物事实、时序和冲突结果。返回JSON。",
        [
            ("断裂与上章约束", str(issue)),
            ("只可修改的开头", opening),
            ("后文只读", text[len(opening) : len(opening) + 600]),
        ],
        '{"replace_count":1,"paragraphs":["替换开头段"]}',
        cap=2000,
        input_limit=10000,
    )
    candidate = opening_patch(text, patch if isinstance(patch, dict) else {})
    if candidate != text:
        checked, invalid, _ = extract(
            client, prefix + "-opening-recheck", candidate, state, bible, outline, [], final=True
        )
        observed = boundary_facts(checked, candidate)
        rechecked = stage_transition(
            bible, outline.get("number"), candidate, state.get("end_location"), outline=outline
        )
        from .checks import high_confidence

        continuous = (
            rechecked["status"] == "present"
            if transition and transition["status"] == "missing" and rechecked
            else bool(observed.get("opening_location")) and not opening_issue(state, observed, candidate)
        )
        if not invalid and continuous and not high_confidence(state, checked.get("events", []), candidate):
            repaired = dict(note, status="repaired")
            if rechecked:
                repaired["transition"] = rechecked
            return candidate, checked, False, observed, repaired
    return text, raw, failed, facts, dict(note, status="rolled_back")
