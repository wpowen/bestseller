"""One guarded model repair for repetitions and excessive card explanations."""

import json

from .checks import high_confidence
from .precision import _sentences, apply as sentence_edits
from .prose_repetition import clean_repetitions
from .prose_similarity import analyze_prose
from .q1_continuity import boundary_facts, opening_issue
from .retired_opponents import opponent_hits
from .stage_transition import stage_transition
from tools.book_checkup import clean_prose


def repair_repetitions(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    before = clean_repetitions(text, remove_safe=False)
    similarity = analyze_prose(text, bible=bible)
    pending = before["needs_repair"] + similarity["needs_repair"]
    if outline.get("number") == 1:
        spans = {(row["start"], row["end"]) for row in pending}
        pending.extend(
            dict(row, action="repair", reasons=["card_explanation"])
            for row in similarity["records"]
            if row["kind"] == "card_explanation" and row["paragraph"] == 1 and (row["start"], row["end"]) not in spans
        )
    counts_before = dict(before["counts"], **similarity["counts"])
    note = dict(status="checked", before=counts_before, after=counts_before, similarity=similarity)
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
        "只修后一次复述或人物卡式说明的候选句S编号，返回edits。说明句用当场行动表达身份，保留必要身份事实。"
        "近似复述只合并后一次冗余，不删独有信息。保留前后主语、指代、事实与过渡；"
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
    counts = dict(
        clean_repetitions(candidate, remove_safe=False)["counts"], **analyze_prose(candidate, bible=bible)["counts"]
    )
    fields = ("sentence_repetitions", "adjacent_paragraph_pairs", "near_paragraph_pairs", "card_explanations")
    reduced = all(counts[k] <= counts_before[k] for k in fields) and any(counts[k] < counts_before[k] for k in fields)
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
        old_facts, new_facts = boundary_facts(raw, text), boundary_facts(checked, candidate)
        old_issue = opening_issue(state, old_facts, text)
        new_issue = opening_issue(state, new_facts, candidate)
        retained_boundaries = all(not old_facts[k] or new_facts[k] for k in ("opening_location", "end_location"))
        if (
            not invalid
            and retained_boundaries
            and not high_confidence(state, checked.get("events", []), candidate)
            and not (not old_issue and new_issue and not (new_transition and new_transition["status"] == "present"))
        ):
            return candidate, checked, False, dict(note, status="repaired", after=counts, edits=edits)
    return text, raw, failed, dict(note, status="rolled_back")
