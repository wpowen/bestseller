"""Whole-chapter exact/near dialogue repetition checks and bounded repair."""

from difflib import SequenceMatcher
import json

from .prose_repetition import _normalize, _sentences
from .text import _paragraph_spans


MIN_EXACT = 4
MIN_NEAR = 8
NEAR_SIMILARITY = 0.84


def _scheduled_catchphrases(bible, outline):
    due = outline.get("catchphrase_due") if isinstance(outline, dict) else []
    due = set(due) if isinstance(due, list) else set()
    return {
        _normalize(card.get("catchphrase", ""))
        for card in (bible or {}).get("characters", [])
        if isinstance(card, dict) and card.get("name") in due and card.get("catchphrase")
    }


def dialogue_repetition_hits(text, bible=None, outline=None):
    """Return every second exact/near spoken sentence, regardless of paragraph distance."""
    nodes = [node for node in _sentences(text, _paragraph_spans(text)) if node.get("quoted")]
    exempt = _scheduled_catchphrases(bible or {}, outline or {})
    result = []
    for index, current in enumerate(nodes):
        normalized = _normalize(current["text"])
        if len(normalized) < MIN_EXACT or any(phrase and phrase == normalized for phrase in exempt):
            continue
        candidates = []
        for earlier in nodes[:index]:
            old = _normalize(earlier["text"])
            if old == normalized:
                candidates.append((1.0, "exact_dialogue", earlier))
                continue
            if min(len(old), len(normalized)) < MIN_NEAR:
                continue
            length_ratio = min(len(old), len(normalized)) / max(len(old), len(normalized))
            similarity = SequenceMatcher(None, old, normalized, autojunk=False).ratio()
            if length_ratio >= 0.75 and similarity >= NEAR_SIMILARITY:
                candidates.append((similarity, "near_dialogue", earlier))
        if candidates:
            similarity, kind, earlier = max(candidates, key=lambda row: row[0])
            result.append(
                {
                    "kind": kind,
                    "text": current["text"],
                    "start": current["start"],
                    "end": current["end"],
                    "paragraph": current["paragraph"],
                    "reference_text": earlier["text"],
                    "reference_start": earlier["start"],
                    "reference_end": earlier["end"],
                    "reference_paragraph": earlier["paragraph"],
                    "similarity": round(similarity, 4),
                    "status": "Evidence" if kind == "exact_dialogue" else "Inference",
                }
            )
    grouped = []
    for row in result:
        if (
            grouped
            and grouped[-1]["paragraph"] == row["paragraph"]
            and grouped[-1]["reference_paragraph"] == row["reference_paragraph"]
            and not text[grouped[-1]["end"] : row["start"]].strip(" ，,；;：:")
        ):
            grouped[-1].update(
                kind="exact_dialogue" if grouped[-1]["kind"] == row["kind"] == "exact_dialogue" else "near_dialogue",
                text=text[grouped[-1]["start"] : row["end"]],
                end=row["end"],
                reference_text=text[grouped[-1]["reference_start"] : row["reference_end"]],
                reference_end=row["reference_end"],
                similarity=min(grouped[-1]["similarity"], row["similarity"]),
            )
        else:
            grouped.append(dict(row))
    return grouped


def _apply_edits(text, targets, value):
    supplied = value.get("edits") if isinstance(value, dict) else None
    if not isinstance(supplied, list):
        return text, []
    by_id = {row["id"]: row for row in targets}
    spans = _paragraph_spans(text)
    edits, seen = [], set()

    def wrapper(value):
        return (
            (value[0], value[-1])
            if len(value) >= 2
            and (value[0], value[-1])
            in {
                ("“", "”"),
                ("「", "」"),
                ("『", "』"),
            }
            else None
        )

    for row in supplied:
        target = by_id.get(row.get("id")) if isinstance(row, dict) else None
        replacement = row.get("text") if isinstance(row, dict) else None
        if target is None or target["id"] in seen or not isinstance(replacement, str) or "\n" in replacement:
            continue
        start, end = target["start"], target["end"]
        if not replacement.strip():
            paragraph = spans[target["paragraph"] - 1]
            passage = text[paragraph[0] : paragraph[1]].strip()
            if passage not in {f"“{target['text']}”", f"「{target['text']}」", f"『{target['text']}』"}:
                continue
            start = paragraph[0]
            end = spans[target["paragraph"]][0] if target["paragraph"] < len(spans) else paragraph[1]
        else:
            target_wrapper = wrapper(target["text"])
            replacement_wrapper = wrapper(replacement.strip())
            inner = replacement.strip()[1:-1] if replacement_wrapper else replacement.strip()
            if any(char in inner for char in "“”「」『』"):
                continue
            replacement = target_wrapper[0] + inner + target_wrapper[1] if target_wrapper else inner
        seen.add(target["id"])
        edits.append(dict(target, start=start, end=end, replacement=replacement.strip()))
    candidate = text
    for edit in sorted(edits, key=lambda row: row["start"], reverse=True):
        candidate = candidate[: edit["start"]] + edit["replacement"] + candidate[edit["end"] :]
    return candidate, edits


def repair_dialogue_repetitions(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    hits = dialogue_repetition_hits(text, bible, outline)
    note = {"status": "checked", "before": len(hits), "after": len(hits), "issues": hits, "edits": []}
    if not hits:
        return text, raw, failed, note
    targets = [dict(row, id=f"D{number}") for number, row in enumerate(hits, 1)]
    value = ask(
        client,
        prefix + "-dialogue-repetition",
        "只改第二次出现的重复对白：按当下情境改成新的自然台词；若整段只有该句且删除不损事实，可用空字符串删除。"
        "不得修改其他文字，不增人物、事实、伤病、谜团或任务。返回JSON。",
        [("重复对白", json.dumps(targets, ensure_ascii=False)), ("只读正文", text)],
        '{"edits":[{"id":"D1","text":"改写后的对白；可安全删除时为空"}]}',
        cap=1800,
        input_limit=10000,
        max_attempts=1,
    )
    candidate, edits = _apply_edits(text, targets, value)
    if len(edits) != len(targets) or candidate == text:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_dialogue_edits")
    remaining = dialogue_repetition_hits(candidate, bible, outline)
    if remaining:
        return (
            text,
            raw,
            failed,
            dict(
                note, status="rolled_back", reason="dialogue_recheck_failed", after=len(remaining), remaining=remaining
            ),
        )
    checked, invalid, _ = extract(
        client, prefix + "-dialogue-recheck", candidate, state, bible, outline, [], final=True
    )
    from .checks import high_confidence

    if invalid or high_confidence(state, checked.get("events", []), candidate):
        return text, raw, failed, dict(note, status="rolled_back", reason="fact_recheck_failed", after=0)
    return candidate, checked, False, dict(note, status="repaired", after=0, edits=edits)


def dialogue_checkup(bible, chapters):
    rows = [
        {
            "chapter": chapter["number"],
            "issues": dialogue_repetition_hits(chapter["text"], bible, chapter.get("meta", {})),
        }
        for chapter in chapters
    ]
    return {
        "count": sum(len(row["issues"]) for row in rows),
        "chapters": [row["chapter"] for row in rows if row["issues"]],
        "issues": [dict(chapter=row["chapter"], **issue) for row in rows for issue in row["issues"]],
        "exact": sum(issue["kind"] == "exact_dialogue" for row in rows for issue in row["issues"]),
        "near": sum(issue["kind"] == "near_dialogue" for row in rows for issue in row["issues"]),
        "status": "Evidence_and_Inference",
    }
