"""Quote-grounded chapter handoffs and opening-only repairs."""

import re

from .text import _paragraph_spans


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
    issue = opening_issue(state, facts, text)
    note = dict(
        status="unknown" if not facts.get("opening_location") or not state.get("end_location") else "checked",
        issue=issue,
    )
    if not issue:
        return text, raw, failed, facts, note
    spans = _paragraph_spans(text)
    opening = text[: spans[min(1, len(spans) - 1)][1]]
    patch = ask(
        client,
        prefix + "-opening-repair",
        "只重写开头一到两段，paragraphs条数必须等于replace_count。先承接上章地点和在场人物，再补上移动。"
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
        from .checks import high_confidence

        if (
            not invalid
            and observed.get("opening_location")
            and not opening_issue(state, observed, candidate)
            and not high_confidence(state, checked.get("events", []), candidate)
        ):
            return candidate, checked, False, observed, dict(note, status="repaired")
    return text, raw, failed, facts, dict(note, status="rolled_back")
