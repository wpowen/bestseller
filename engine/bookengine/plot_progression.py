"""Keep completed cross-chapter events from restarting as a new opening scene."""

import json
from pathlib import Path
import re

from .cross_chapter_facts import critical_events
from .precision import apply as span_edits
from .storage import load


_DONE = {
    "escape": re.compile(r"脱险|安全离开|完成撤离|全部撤出|已经撤出"),
    "destruction": re.compile(r"彻底熄灭|已经熄灭|崩毁|彻底毁灭|已经毁灭|死亡"),
}
_REPLAY = {
    "escape": re.compile(r"撤离|脱险|安全离开|引回|仍缩在出口|护送"),
    "destruction": re.compile(r"崩毁|毁灭|坍塌|下滑|禁制[^。！？]{0,12}闭合|正在崩毁"),
}


def _paragraph_spans(text, limit=None):
    rows = [(match.start(), match.end()) for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|$)", text)]
    return rows if limit is None else rows[:limit]


def _category(text, patterns):
    return next((kind for kind, pattern in patterns.items() if pattern.search(text)), "")


def _same_subject(kind, previous, current):
    if kind == "escape":
        return "药奴" in previous and "药奴" in current or "人" in previous and "人" in current
    if kind == "destruction":
        return "阵" in previous and ("阵" in current or "禁制" in current or "席位台" in current)
    return False


def plot_replay_findings(text, state, chapter):
    findings = []
    for fact in critical_events(state):
        if fact.get("chapter", chapter) >= chapter or not (kind := _category(fact.get("quote", ""), _DONE)):
            continue
        for number, (start, end) in enumerate(_paragraph_spans(text, 8), 1):
            current = text[start:end]
            if _REPLAY[kind].search(current) and _same_subject(kind, fact["quote"], current):
                findings.append(
                    {
                        "id": number,
                        "kind": "plot_replay",
                        "chapter": chapter,
                        "previous_chapter": fact["chapter"],
                        "previous_quote": fact["quote"],
                        "current_quote": current,
                        "reason": "前文已完成的事件在本章开场重新作为进行中的任务",
                        "status": "Inference",
                        "start": start,
                        "end": end,
                    }
                )
                break
    return findings


def _review(ask, client, prefix, text, state, chapter, suffix):
    facts = critical_events(state)
    spans = _paragraph_spans(text, 8)
    if not facts:
        return []
    response = ask(
        client,
        prefix + suffix,
        "核对开场是否把前文已经完成的事件重新写成正在进行。只报告确定回卷，逐字引用前文事实和本章命中段。"
        "后果延续、善后和新障碍不算回卷；不确定不报。",
        [
            ("前文已完成事件", json.dumps(facts, ensure_ascii=False)),
            (
                "本章开场编号段",
                json.dumps([{"id": i, "text": text[a:b]} for i, (a, b) in enumerate(spans, 1)], ensure_ascii=False),
            ),
        ],
        '{"findings":[{"id":1,"previous_chapter":1,"previous_quote":"前文逐字引文","current_quote":"本章逐字引文","reason":"原因"}]}',
        cap=1800,
        effort="low",
        input_limit=10000,
        max_attempts=1,
    )
    rows = response.get("findings", []) if isinstance(response, dict) else []
    grounded = []
    for row in rows if isinstance(rows, list) else []:
        number = row.get("id") if isinstance(row, dict) else None
        fact = next(
            (
                saved
                for saved in facts
                if row.get("previous_chapter") == saved.get("chapter")
                and row.get("previous_quote") == saved.get("quote")
            ),
            None,
        )
        if type(number) is int and 1 <= number <= len(spans) and fact:
            start, end = spans[number - 1]
            if row.get("current_quote") == text[start:end]:
                grounded.append(
                    {
                        **row,
                        "kind": "plot_replay",
                        "chapter": chapter,
                        "start": start,
                        "end": end,
                        "status": "agent_editorial_not_human",
                    }
                )
    deterministic = plot_replay_findings(text, state, chapter)
    return list(
        {
            (row["id"], row["previous_chapter"], row["current_quote"]): row for row in [*deterministic, *grounded]
        }.values()
    )


def repair_plot_replay(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Rewrite only replaying opening paragraphs, recheck, and otherwise roll back."""
    chapter = outline.get("number", 0)
    before = _review(ask, client, prefix, text, state, chapter, "-plot-replay-review")
    note = {"status": "checked", "before": before, "after": before, "edits": []}
    if not before:
        return text, raw, failed, note
    spans = _paragraph_spans(text, 8)
    targets = sorted({row["id"] for row in before})
    allowed = [{"id": number, "text": text[spans[number - 1][0] : spans[number - 1][1]]} for number in targets]
    response = ask(
        client,
        prefix + "-plot-replay-repair",
        "只改命中的开场段，让叙事从已完成事件之后继续；保留人物、地点、当前新目标和本章其他段落。",
        [
            ("已完成事件", json.dumps(critical_events(state), ensure_ascii=False)),
            ("允许修改的回卷段", json.dumps(allowed, ensure_ascii=False)),
        ],
        '{"edits":[{"id":1,"text":"替换后的完整段落"}]}',
        cap=2500,
        effort="low",
        input_limit=10000,
        max_attempts=1,
    )
    candidate, edits = span_edits(
        text,
        response.get("edits", []) if isinstance(response, dict) else [],
        [{"id": number, "start": spans[number - 1][0], "end": spans[number - 1][1]} for number in targets],
        allow_empty=False,
        allow_style=True,
    )
    note["edits"] = edits
    if candidate == text:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_plot_replay_repair")
    after = _review(ask, client, prefix, candidate, state, chapter, "-plot-replay-recheck")
    note["after"] = after
    if after:
        return text, raw, failed, dict(note, status="rolled_back", reason="plot_replay_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-plot-replay-state", candidate, state, bible, outline, [], final=True
    )
    if invalid:
        return text, raw, failed, dict(note, status="rolled_back", reason="plot_replay_state_failed")
    return candidate, checked, False, dict(note, status="repaired")


def _outline_text(outline):
    fields = [outline.get("opening_conflict", "")] if isinstance(outline, dict) else []
    for scene in outline.get("scenes", []) if isinstance(outline, dict) else []:
        if isinstance(scene, dict):
            fields.extend(str(scene.get(key) or "") for key in ("goal", "action", "result"))
    return "\n".join(fields)


def _load_outlines(book, chapters):
    if book is None:
        return {}
    root = Path(book)
    return {
        row["number"]: load(path)
        for row in chapters
        if (path := root / f"writing_outlines/{row['number']:03d}.json").exists()
    }


def _evidence_quote(text, kind, patterns):
    return next(
        (text[start:end] for start, end in _paragraph_spans(text) if patterns[kind].search(text[start:end])),
        "",
    )


def plot_progression_checkup(chapters, *, outlines=None, book=None):
    """Use saved adjacent outlines for classification and prose for verbatim evidence."""
    mapped = {row.get("number"): row for row in chapters}
    outlines = outlines if isinstance(outlines, dict) else _load_outlines(book, chapters)
    issues = []
    for number in sorted(mapped):
        if number - 1 not in mapped:
            continue
        previous_outline, current_outline = outlines.get(number - 1, {}), outlines.get(number, {})
        previous_plan, current_plan = _outline_text(previous_outline), _outline_text(current_outline)
        kind = _category(previous_plan, _DONE)
        if not kind or not _REPLAY[kind].search(current_plan) or not _same_subject(kind, previous_plan, current_plan):
            continue
        previous_quote = _evidence_quote(mapped[number - 1]["text"], kind, _DONE)
        current_quote = _evidence_quote(mapped[number]["text"], kind, _REPLAY)
        if previous_quote and current_quote:
            issues.append(
                {
                    "chapters": [number - 1, number],
                    "kind": kind,
                    "previous_quote": previous_quote,
                    "quote": current_quote,
                    "status": "Inference",
                }
            )
    return {"counts": {"plot_replays": len(issues)}, "plot_replays": issues}
