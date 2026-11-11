import re
import json
from pathlib import Path
import yaml
from .text import _paragraph_spans, pack, chinese_count

_RULES = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/ending_rules.yaml").read_text())


def ending_candidates(text, bible, outline=None):
    spans = _paragraph_spans(text) if isinstance(text, str) else []
    if not spans:
        return []
    start = spans[-2:][0][0]
    tail, known = text[start:], text[:start] + pack(bible) + pack(outline)
    if not re.search(_RULES["suspense"], tail):
        return []
    return [
        dict(
            kind=kind, text=m[0], start=start + m.start(), end=start + m.end(), known=m["value"] in known, context=tail
        )
        for kind, pattern in _RULES["signals"].items()
        for m in re.finditer(pattern, tail)
    ]


def apply_tail(text, value):
    """Legacy two-paragraph replacement, preserving prefix and separators."""
    spans = _paragraph_spans(text) if isinstance(text, str) else []
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if isinstance(value, str):
        value = [value[a:b] for a, b in _paragraph_spans(value)]
    if len(spans) < 2 or not isinstance(value, (list, tuple)) or len(value) != 2:
        return text, False
    if any(not isinstance(p, str) or not p.strip() or len(_paragraph_spans(p)) != 1 for p in value):
        return text, False
    if re.search(_RULES["rewrite_block"], "\n\n".join(value)):
        return text, False
    result = text
    for (a, b), replacement in reversed(list(zip(spans[-2:], value))):
        result = result[:a] + replacement + result[b:]
    return result, result != text


def scene_context(text):
    """Original 1-based P ids; offset excludes the last scene delimiter."""
    spans = _paragraph_spans(text)
    marks = list(re.finditer(_RULES["scene_boundary"], text))
    floor = marks[-1].end() if marks else len(text) / 2
    rows = [
        {"pid": i, "text": text[max(a, int(floor)) if marks else a : b]}
        for i, (a, b) in enumerate(spans, 1)
        if (b > floor if marks else i >= 2 and a >= floor) and text[max(a, int(floor)) : b].strip()
    ]
    return dict(start=rows[0]["pid"] if rows else None, paragraphs=rows, boundary=bool(marks), offset=int(floor))


def apply_scene(text, value):
    """Replace only a valid final scene; retain all earlier bytes."""
    ctx = scene_context(text)
    start = value.get("start") if isinstance(value, dict) else None
    replacement = value.get("text") if isinstance(value, dict) else None
    if type(start) is not int or start not in [p["pid"] for p in ctx["paragraphs"]]:
        return text, False
    if ctx["boundary"] and start != ctx["start"]:
        return text, False
    if not isinstance(replacement, str) or not replacement.strip():
        return text, False
    parts = [replacement[a:b] for a, b in _paragraph_spans(replacement)]
    if chinese_count(replacement) > _RULES["scene_limit"] or any(
        chinese_count(p) > _RULES["paragraph_limit"] for p in parts
    ):
        return text, False
    if re.search(_RULES["rewrite_block"], replacement) or re.search(_RULES["scene_boundary"], replacement):
        return text, False
    if re.search(_RULES["scene_exposition"], replacement) or not re.search(_RULES["scene_dialogue"], replacement, re.S):
        return text, False
    offset = max(_paragraph_spans(text)[start - 1][0], ctx["offset"] if ctx["boundary"] else 0)
    result = text[:offset] + replacement
    return result, result != text


def english_hits(text):
    """Offsets in the FINAL merged body; position labels alone are allowed."""
    return [
        dict(text=m[0], start=m.start(), end=m.end())
        for m in re.finditer(_RULES["english_fragment"], text)
        if not re.fullmatch(_RULES["position_label"], m[0])
    ]


def final_closure_hits(text):
    """Return deterministic new-mystery/identity/task/stigma signals in the final tail."""
    spans = _paragraph_spans(text) if isinstance(text, str) else []
    if not spans:
        return []
    rules = _RULES["final_closure"]
    tail_spans = spans[-int(rules["tail_paragraphs"]) :]
    start = tail_spans[0][0]
    tail = text[start:]
    return [
        {
            "kind": kind,
            "text": match[0],
            "start": start + match.start(),
            "end": start + match.end(),
            "paragraph": next(i for i, (a, b) in enumerate(spans, 1) if a <= start + match.start() < b),
        }
        for kind in ("new_mystery", "new_identity", "new_task", "new_stigma")
        for match in re.finditer(rules[kind], tail)
    ]


def final_closure_checkup(chapters):
    if not chapters:
        return {"count": 0, "chapter": None, "issues": [], "status": "Unknown"}
    chapter = max(chapters, key=lambda row: row["number"])
    issues = final_closure_hits(chapter["text"])
    return {
        "count": len(issues),
        "chapter": chapter["number"],
        "issues": issues,
        "status": "Evidence",
        "basis": "deterministic final-tail patterns; reader judgment still required",
    }


def repair_final_closure(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Rewrite only the blocked final tail and adopt it only after deterministic and fact rechecks."""
    hits = final_closure_hits(text) if outline.get("ending") else []
    note = {"status": "checked", "before": len(hits), "after": len(hits), "issues": hits}
    if not hits:
        return text, raw, failed, note
    spans = _paragraph_spans(text)
    first_paragraph = min(row["paragraph"] for row in hits)
    start = spans[first_paragraph - 1][0]
    contract = outline.get("ending_promise_response")
    value = ask(
        client,
        prefix + "-final-closure",
        "只改末章给出的尾段，删去新谜团、新身份、新追查任务和新污名。"
        "改成回应书名承诺和人物成长落点的现场动作或对白；不改前文，不增事实、人物、物件或后续任务。返回JSON。",
        [
            ("书名与末章契约", json.dumps({"title": bible.get("title"), "contract": contract}, ensure_ascii=False)),
            ("只读前文", text[max(0, start - 1200) : start]),
            ("待改尾段", text[start:]),
        ],
        '{"text":"改写后的末章尾段"}',
        cap=1600,
        input_limit=4000,
        max_attempts=1,
    )
    replacement = value.get("text") if isinstance(value, dict) else None
    if (
        not isinstance(replacement, str)
        or not replacement.strip()
        or len(replacement) > 1600
        or len(_paragraph_spans(replacement)) > 20
    ):
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_closure_patch")
    candidate = text[:start] + replacement.strip()
    remaining = final_closure_hits(candidate)
    if remaining:
        return (
            text,
            raw,
            failed,
            dict(
                note, status="rolled_back", reason="closure_recheck_failed", after=len(remaining), remaining=remaining
            ),
        )
    checked, invalid, _ = extract(
        client, prefix + "-final-closure-recheck", candidate, state, bible, outline, [], final=True
    )
    from .checks import high_confidence

    if invalid or high_confidence(state, checked.get("events", []), candidate):
        return text, raw, failed, dict(note, status="rolled_back", reason="fact_recheck_failed", after=0)
    return candidate, checked, False, dict(note, status="repaired", after=0, start_paragraph=first_paragraph)
