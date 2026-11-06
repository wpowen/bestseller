"""Small boundary-only recheck after deterministic proof of an opening repair."""

from copy import deepcopy
import json
import re

from .checks import high_confidence
from .handoff_places import location_table, same_place
from .prose_repetition import clean_repetitions
from .stage_transition import _transition_quotes, stage_transition
from .text import _paragraph_spans


def _number(text):
    if text.isdecimal():
        return int(text)
    digits = "零一二三四五六七八九"
    text = text.replace("两", "二").replace("〇", "零")
    if "十" in text:
        left, right = text.split("十")
        return (digits.index(left) if left else 1) * 10 + (digits.index(right) if right else 0)
    return digits.index(text)


def _literal_events(state, text, changed, bible, facts):
    """Feed literal physical presence/absolute clocks into the existing hard checks.

    This is deliberately bounded to the replacement paragraphs. It does not
    claim semantic event extraction or put inferred events in the saved state.
    Previously extracted events still take part in the hard-conflict check.
    """
    names = set(state.get("characters", {})) | set(state.get("_dead", []))
    names.update(row["name"] for row in bible.get("characters", []) if isinstance(row, dict) and row.get("name"))
    places = location_table(bible, [facts.get("opening_location"), state.get("end_location")])["names"]
    places = [p for p in places if p]
    places += [row["location"] for row in state.get("characters", {}).values() if row.get("location")]
    result = []
    digit = r"[零〇一二两三四五六七八九]"
    numeral = rf"(?:\d{{1,3}}|{digit}?十{digit}?|{digit})"
    for index, (start, end) in enumerate(_paragraph_spans(text), 1):
        if start >= changed:
            break
        paragraph = text[start:end]
        for name in sorted(names):
            # Only plain physical-presence phrases; the shared checker rejects
            # dialogue, recollection, plans, negation and sequential movement.
            pattern = (
                re.escape(name) + r"(?:正|仍|却|还|同时)?(?:出现|站|坐|留|待|守|躺|跪)?在([^。！？，,；;\n]{1,80})"
            )
            found = [m[1] for m in re.finditer(pattern, paragraph)]
            if not found:
                continue
            for place in dict.fromkeys([*places, *found]):
                if place in paragraph:
                    result.append(dict(kind="character", name=name, location=place, action="stay", paragraph=index))
        for match in re.finditer(
            r"第(" + numeral + r")天[^。！？\n]{0,12}?(" + numeral + r")(?:点|时)(?:整|钟)?", paragraph
        ):
            minute = re.match(r"(" + numeral + r")分", paragraph[match.end() :])
            day, hour = _number(match[1]), _number(match[2])
            minutes = _number(minute[1]) if minute else 0
            if hour < 24 and minutes < 60:
                result.append(dict(kind="time", day=day, minute=hour * 60 + minutes, paragraph=index))
    return result


def _boundaries(client, prefix, text, ask):
    shape = {
        side + field: None for side in ("opening", "end") for field in ("_location", "_quote", "_pov", "_pov_quote")
    }
    saved_prefix = getattr(client, "prefix", "")
    if client is not None:
        client.prefix = ""
    try:
        result = ask(
            client,
            prefix + "-opening-boundary-recheck",
            "只重抽开头300字与结尾1200字的当前地点与视角，不抽整章状态、事件、兑现或解法。"
            "地点须是对应逐字引文内的连续原文；视角引文须含人物原名。回忆、对白、计划中的地点不算。"
            "找不到填null，不沿用规划或旧状态。返回JSON。",
            [("开头", text[:300]), ("结尾", text[-1200:])],
            json.dumps(shape),
            cap=1200,
            input_limit=4000,
            max_attempts=1,
        )
    finally:
        if client is not None:
            client.prefix = saved_prefix
    return result if isinstance(result, dict) else {}


def recheck_opening(client, prefix, text, candidate, patch, raw, facts, state, bible, outline, note, ask):
    from .q1_continuity import boundary_facts, evaluate_handoff, opening_issue

    spans = _paragraph_spans(candidate)
    changed = spans[patch["replace_count"] - 1][1]
    opening = candidate[: min(changed, 300)]
    reference = note["reference"]
    planned = note.get("transition") or {}
    if not reference.get("end_location") and planned.get("previous"):
        reference = dict(reference, end_location=planned["previous"], basis="planned_stage_location")
    target = facts.get("opening_location") or (note.get("transition") or {}).get("opening")
    destinations = location_table(bible, [target] if target else [])["names"]
    target = next((place for place in destinations if place in opening and same_place(place, target, bible)), target)
    proof = dict(
        status="Inference", basis="deterministic_opening_transition", quotes=_transition_quotes(opening) or [opening]
    )
    proof["reference"] = reference
    transition = stage_transition(bible, outline.get("number"), opening, reference.get("end_location"), outline=outline)
    connected = (
        bool(target and target in opening and reference.get("end_location"))
        and not opening_issue(reference, dict(opening_location=target), opening, bible)
        and not (transition and transition["issue"])
    )
    if not connected:
        return raw, facts, proof, "opening_still_disconnected"

    # Preserve the original suffix exactly; only the bounded opening_patch can
    # construct candidates. Reuse the shared repetition and hard-fact guards.
    before = clean_repetitions(text, remove_safe=False)["counts"]
    after = clean_repetitions(candidate, remove_safe=False)["counts"]
    if any(after[key] > before[key] for key in before):
        return raw, facts, proof, "introduced_repetition"
    events = _literal_events(state, candidate, changed, bible, facts)
    # Synthetic observations check against the same prior state independently;
    # they must not move characters merely by enumerating candidate places.
    if high_confidence(state, raw.get("events", []), candidate) or any(
        high_confidence(state, [event], candidate) for event in events
    ):
        return raw, facts, proof, "hard_fact_conflict"

    extracted = _boundaries(client, prefix, candidate, ask)
    observed = boundary_facts(extracted, candidate)
    retained = boundary_facts(raw, candidate)
    checked = deepcopy(raw)
    # A missing response never erases an unchanged, grounded end. A removed
    # quote never survives simply because it was valid before the patch.
    for side in ("opening", "end"):
        for field in ("location", "quote", "pov", "pov_quote"):
            key = side + "_" + field
            checked[key] = observed.get(key) or retained.get(key)
    stable_end = bool(retained.get("end_quote") and retained["end_quote"] in candidate[changed:])
    if stable_end:
        for field in ("location", "quote", "pov", "pov_quote"):
            key = "end_" + field
            checked[key] = retained.get(key) or observed.get(key)
    if facts.get("end_location") and not checked.get("end_location"):
        return raw, facts, proof, "lost_grounded_boundary"
    # Do not retain character/location observations whose quotes disappeared.
    checked["character_locations"] = retained["character_locations"]
    checked["present"] = retained["present"]
    checked["present_quotes"] = {name: raw.get("present_quotes", {}).get(name) for name in retained["present"]}
    checked["events"] = [
        event
        for event in raw.get("events", [])
        if isinstance(event, dict) and isinstance(event.get("quote"), str) and event["quote"] in candidate
    ]
    merged = boundary_facts(checked, candidate)
    assessment = evaluate_handoff(state, merged, candidate, bible, outline)
    # The pre-repair reference already resolved POV history. Missing newly
    # extracted POV fields cannot silently switch to an unrelated end location.
    if merged.get("opening_location") and (
        not same_place(target, merged["opening_location"], bible) or opening_issue(reference, merged, opening, bible)
    ):
        return raw, facts, assessment, "opening_still_disconnected"
    if assessment["issue"]:
        return raw, facts, assessment, "opening_still_disconnected"
    assessment.update(
        evidence="Inference"
        if any(not observed.get(side + "_location") for side in ("opening", "end"))
        else "Evidence",
        deterministic=proof,
        retained_ending=stable_end,
        boundary_missing=[side + "_location" for side in ("opening", "end") if not observed.get(side + "_location")],
    )
    if stable_end and observed.get("end_location") and observed["end_location"] != merged["end_location"]:
        assessment.update(evidence="Inference", discarded_end_location=observed["end_location"])
    return checked, merged, assessment, None
