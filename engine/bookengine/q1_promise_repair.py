"""Transactional paragraph repair of opening promises, with final-state recheck."""

import json
import re

from .checks import high_confidence
from .opening_promises import review_opening
from .q1_continuity import boundary_facts, opening_issue
from .retired_opponents import opponent_hits
from .prose_repetition import clean_repetitions
from .q1_cast import cast_findings, remaining_cast_problems
from .stage_transition import stage_transition


def repair(client, prefix, text, raw, failed, state, bible, outline, earlier, extract, ask):
    number = outline.get("number", 0)
    before = review_opening(client, prefix, text, bible, number, earlier, ask)
    note = dict(status=before["status"], before=before, after=before)
    if before["status"] != "unfulfilled":
        return text, raw, failed, note
    pending = [i for i in before["items"] if i["status"] in ("missing", "unknown")]
    spans = list(re.finditer(r"\S[\s\S]*?(?=\n\s*\n|$)", text))
    targets = set()
    for item in pending:
        ids = item.get("paragraphs")
        if isinstance(ids, list):
            targets.update(i for i in ids if type(i) is int and 1 <= i <= len(spans))
        if item["kind"] == "address_origin":
            targets.update(i for i, s in enumerate(spans, 1) if s.start() <= item["start"] < s.end())
    if not targets:
        targets = set(range(1, min(2, len(spans)) + 1))
    allowed = [dict(id=i, original=s.group()) for i, s in enumerate(spans, 1) if i in targets]
    response = ask(
        client,
        prefix + "-promise-repair",
        "只改允许的段落，兑现缺失承诺或称呼来历。保留现有事件、"
        "人物、地点、过渡和末场结果。返回edits，每项包含id/original/text；original必须逐字一致。",
        [
            ("缺失承诺", json.dumps(pending, ensure_ascii=False)),
            ("允许修改的段", json.dumps(allowed, ensure_ascii=False)),
            ("只读圣经", json.dumps(bible, ensure_ascii=False)),
            ("只读正文", text),
        ],
        '{"edits":[{"id":1,"original":"原段","text":"改段"}]}',
        cap=4500,
        input_limit=14000,
    )
    edits = response.get("edits", []) if isinstance(response, dict) else []
    candidate, used = text, set()
    valid = isinstance(edits, list) and bool(edits)
    patches = []
    for edit in edits if isinstance(edits, list) else []:
        if not isinstance(edit, dict):
            valid = False
            break
        idx, replacement = edit.get("id"), edit.get("text")
        if (
            type(idx) is not int
            or idx not in targets
            or idx in used
            or not isinstance(replacement, str)
            or not replacement.strip()
        ):
            valid = False
            break
        span = spans[idx - 1]
        if edit.get("original") != span.group() or "\n\n" in replacement:
            valid = False
            break
        used.add(idx)
        patches.append((span.start(), span.end(), replacement))
    if valid:
        for a, b, replacement in sorted(patches, reverse=True):
            candidate = candidate[:a] + replacement + candidate[b:]
    if not valid or candidate == text:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_or_empty_targeted_edits")
    after = review_opening(client, prefix + "-recheck", candidate, bible, number, earlier, ask)
    note["after"] = after
    if after["status"] != "checked":
        return text, raw, failed, dict(note, status="rolled_back", reason="promise_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-promise-final-state", candidate, state, bible, outline, [], final=True
    )
    old_issue = opening_issue(state, boundary_facts(raw, text), text)
    new_issue = opening_issue(state, boundary_facts(checked, candidate), candidate)
    old_transition = stage_transition(bible, number, text, state.get("end_location"), outline=outline)
    new_transition = stage_transition(bible, number, candidate, state.get("end_location"), outline=outline)
    old_repeats = clean_repetitions(text, remove_safe=False)["counts"]
    new_repeats = clean_repetitions(candidate, remove_safe=False)["counts"]
    regressed = (
        not old_issue
        and bool(new_issue)
        or old_transition
        and old_transition["status"] == "present"
        and (not new_transition or new_transition["status"] != "present")
    )
    if (
        invalid
        or high_confidence(state, checked.get("events", []), candidate)
        or regressed
        or opponent_hits(candidate, bible, number)
        or not remaining_cast_problems(cast_findings(candidate, bible, state))
        <= remaining_cast_problems(cast_findings(text, bible, state))
        or any(new_repeats[k] > old_repeats[k] for k in ("sentence_repetitions", "adjacent_paragraph_pairs"))
    ):
        return text, raw, failed, dict(note, status="rolled_back", reason="final_state_or_existing_guard_failed")
    return candidate, checked, False, dict(note, status="repaired", edits=edits)
