"""Transactional paragraph repair of opening promises, with final-state recheck."""

import re

from .checks import high_confidence
from .opening_promises import review_opening
from .q1_continuity import boundary_facts, opening_issue
from .retired_opponents import opponent_hits
from .prose_repetition import clean_repetitions
from .prose_similarity import analyze_prose
from tools.book_checkup import clean_prose
from .q1_cast import cast_findings, remaining_cast_problems
from .stage_transition import stage_transition
from .promise_repair_edits import targeted_edits


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
    candidate, edits, attempts = targeted_edits(ask, client, prefix, text, spans, targets, pending, bible)
    note["attempts"] = attempts
    if candidate == text:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_or_empty_targeted_edits")
    candidate = clean_prose(candidate)[0]
    after = review_opening(client, prefix + "-recheck", candidate, bible, number, earlier, ask)
    note["after"] = after
    if after["status"] not in ("checked", "pending"):
        return text, raw, failed, dict(note, status="rolled_back", reason="promise_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-promise-final-state", candidate, state, bible, outline, [], final=True
    )
    old_facts, new_facts = boundary_facts(raw, text), boundary_facts(checked, candidate)
    old_issue = opening_issue(state, old_facts, text)
    new_issue = opening_issue(state, new_facts, candidate)
    retained_boundaries = all(
        not old_facts[k] or new_facts[k] == old_facts[k] for k in ("opening_location", "end_location")
    )
    old_transition = stage_transition(bible, number, text, state.get("end_location"), outline=outline)
    new_transition = stage_transition(bible, number, candidate, state.get("end_location"), outline=outline)
    old_repeats = clean_repetitions(text, remove_safe=False)["counts"]
    new_repeats = clean_repetitions(candidate, remove_safe=False)["counts"]
    old_similarity = analyze_prose(text, bible=bible)["counts"]
    new_similarity = analyze_prose(candidate, bible=bible)["counts"]
    regressed = (
        not old_issue
        and bool(new_issue)
        or old_transition
        and old_transition["status"] == "present"
        and (not new_transition or new_transition["status"] != "present")
    )
    if (
        invalid
        or not retained_boundaries
        or any(new_similarity[k] > old_similarity[k] for k in ("near_paragraph_pairs", "card_explanations"))
        or high_confidence(state, checked.get("events", []), candidate)
        or regressed
        or opponent_hits(candidate, bible, number)
        or not remaining_cast_problems(cast_findings(candidate, bible, state))
        <= remaining_cast_problems(cast_findings(text, bible, state))
        or any(new_repeats[k] > old_repeats[k] for k in ("sentence_repetitions", "adjacent_paragraph_pairs"))
    ):
        return text, raw, failed, dict(note, status="rolled_back", reason="final_state_or_existing_guard_failed")
    return candidate, checked, False, dict(note, status="repaired", edits=edits)
