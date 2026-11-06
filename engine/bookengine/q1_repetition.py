"""One bounded repair for dependent repetitions left by earlier chapter edits."""

import json

from .checks import high_confidence
from .precision import _sentences, apply as sentence_edits
from .prose_repetition import clean_repetitions
from .q1_continuity import boundary_facts, opening_issue
from .retired_opponents import opponent_hits
from .stage_transition import stage_transition
from tools.book_checkup import clean_prose


def repair_repetitions(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    before = clean_repetitions(text, remove_safe=False)
    pending = before["needs_repair"]
    note = dict(status="checked", before=before["counts"], after=before["counts"])
    if not pending:
        return text, raw, failed, note
    hits = [
        dict(id=i, start=a, end=b, text=text[a:b])
        for i, (a, b) in enumerate(_sentences(text), 1)
        if any(a < row["end"] and b > row["start"] for row in pending)
    ]
    patch = ask(
        client,
        prefix + "-repetition-repair",
        "只修后一次复述的候选句S编号，返回edits。保留前后主语、指代、事实与过渡；"
        "不能把复述改成新的事件，不得改其他句。无法安全处理则返回空edits。",
        [
            ("重复与依赖", json.dumps(pending, ensure_ascii=False)),
            ("可修改的句S编号", json.dumps(hits, ensure_ascii=False)),
            ("只读正文", text),
        ],
        '{"edits":[{"id":1,"text":"替换句"}]}',
        cap=2500,
        input_limit=10000,
    )
    candidate, edits = sentence_edits(
        text, patch.get("edits", []) if isinstance(patch, dict) else [], hits, allow_empty=True, allow_style=True
    )
    candidate = clean_prose(candidate)[0]
    after = clean_repetitions(candidate)
    candidate = after["cleaned"]
    counts = clean_repetitions(candidate, remove_safe=False)["counts"]
    fields = ("sentence_repetitions", "adjacent_paragraph_pairs")
    reduced = all(counts[k] <= before["counts"][k] for k in fields) and any(
        counts[k] < before["counts"][k] for k in fields
    )
    old_transition = stage_transition(bible, outline.get("number"), text, state.get("end_location"), outline=outline)
    new_transition = stage_transition(
        bible, outline.get("number"), candidate, state.get("end_location"), outline=outline
    )
    continuous = not (
        old_transition
        and old_transition["status"] == "present"
        and (not new_transition or new_transition["status"] != "present")
    )
    if (
        edits
        and candidate != text
        and reduced
        and continuous
        and not opponent_hits(candidate, bible, outline.get("number", 0))
    ):
        checked, invalid, _ = extract(
            client, prefix + "-repetition-recheck", candidate, state, bible, outline, [], final=True
        )
        old_issue = opening_issue(state, boundary_facts(raw, text), text)
        new_issue = opening_issue(state, boundary_facts(checked, candidate), candidate)
        if (
            not invalid
            and not high_confidence(state, checked.get("events", []), candidate)
            and not (not old_issue and new_issue and not (new_transition and new_transition["status"] == "present"))
        ):
            return candidate, checked, False, dict(note, status="repaired", after=counts, edits=edits)
    return text, raw, failed, dict(note, status="rolled_back")
