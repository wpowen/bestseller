"""Find previously completed callbacks and hand the writer their literal body passages."""

from pathlib import Path
import re

from tools.chapter_files import chapter_files, chapter_number

from .longutil import WRITER_CALLBACK_HEADING as HEADING
from .q1_schema import integer, rows
from .storage import load
from .story_duties import load_ledger
from .structure_planning import stage_for_chapter
from .text import _paragraph_spans


KNOWLEDGE = (
    "原文只供写手对照，不等于本场人物都知道。人物只能根据此前亲历或正文已经交代的转述作判断。"
    "只写给读者看的旁白、他人的私下行动或想法不得变成本场人物的知识。"
    "人物若尚不知道，本章先获取线索，再据此行动；未到揭晓章只使用已露出的部分。"
)


def _history_rows(history, ranges):
    if isinstance(history, dict):
        history = history.get("events", history.get("chapters", list(history.values())))
    for row in rows(history):
        start, end = integer(row.get("start")), integer(row.get("end"))
        if 0 < start <= end:
            ranges.append((start, end))
        if isinstance(row.get("events"), list):
            yield from _history_rows(row["events"], ranges)
        elif not ({"start", "end"} & row.keys()):
            yield row


def _completed(path):
    try:
        record = load(path)
    except (OSError, ValueError):
        return {}
    return (
        record
        if isinstance(record, dict) and record.get("status") == "complete" and record.get("fact_source") == "final_body"
        else {}
    )


def _saved_events(out, ranges, before):
    paths = [
        (chapter_number(path, "*.json"), path) for path in reversed(chapter_files(Path(out) / "chapters", "*.json"))
    ]
    # Try the latest chapter with evidence in each finished range first.
    for start, end in sorted(set(ranges or [(1, before - 1)]), reverse=True):
        if end >= before:
            continue
        for number, path in paths:
            if not start <= number <= end or not (record := _completed(path)):
                continue
            ledger = (record.get("state") or {}).get("continuity_ledger") or {}
            for event in reversed(rows(ledger.get("critical_events"))):
                if event.get("chapter") == number and event.get("summary") and event.get("quote"):
                    yield dict(chapter=number, event=event["summary"], source_quote=event["quote"])
                    break
            else:
                if not record.get("summary"):
                    continue
                yield dict(chapter=number, event=record["summary"])
            break


def callbacks(history, *, out=None, before=None, limit=3):
    """Use saved volume boundaries as an index for observed chapter records."""
    ceiling = before if before is not None else float("inf")
    ranges, result = [], []
    for row in _history_rows(history, ranges):
        number = integer(row.get("chapter", row.get("number")))
        event = row.get("event") or row.get("summary")
        if 0 < number < ceiling and isinstance(event, str) and event.strip():
            item = dict(chapter=number, event=event)
            if quote := row.get("source_quote") or row.get("quote"):
                item["source_quote"] = quote
            if item not in result:
                result.append(item)
    if out is not None and len(result) < limit and (ranges or before is not None):
        for item in _saved_events(out, ranges, ceiling):
            if item["chapter"] not in {row["chapter"] for row in result}:
                result.append(item)
            if len(result) == limit:
                break
    return result[:limit]


def _passage(body, ref, *, strict):
    paragraphs = [body[start:end] for start, end in _paragraph_spans(body)]
    literal = ref.get("source_quote") or ref.get("original_excerpt")
    if literal or strict:
        literal = literal or ref.get("event")
        return next((p for p in paragraphs if isinstance(literal, str) and literal.strip() and literal in p), "")
    words = set(re.findall(r"[\u4e00-\u9fff]{2}", ref.get("event", "")))
    score, paragraph = max(
        ((sum(word in p for word in words), p) for p in paragraphs), key=lambda item: item[0], default=(0, "")
    )
    return paragraph if score >= 2 else ""


def _recall(out, refs, before, *, strict=False, require_completed=False, limit=3):
    if out is None:
        return []
    out = Path(out)
    result = []
    for ref in rows(refs):
        number = integer(ref.get("chapter"))
        if not 0 < number < before or (require_completed and not _completed(out / f"chapters/{number:03d}.json")):
            continue
        try:
            body = (out / f"chapter-{number:03d}.body.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if passage := _passage(body, ref, strict=strict):
            record = dict(chapter=number, original_excerpt=passage)
            if ref.get("due"):
                record["due"] = ref["due"]
            if ref.get("source_quote"):
                record["source_quote"] = ref["source_quote"]
            if record not in result:
                result.append(record)
        if len(result) == limit:
            break
    return result


def recall_events(out, history, chapter, total, volume_end, *, requested=None, before=None):
    """Allow a chapter to request original evidence independently of its position in a volume."""
    ceiling = min(chapter, before if before is not None else chapter)
    if requested is not None:
        return _recall(out, requested, ceiling, strict=True, require_completed=True)
    if chapter < total * 0.9 and chapter < volume_end - 2:
        return []
    return _recall(out, callbacks(history, out=out, before=ceiling), ceiling)


def _scheduled_sources(out, design, start, end):
    lines = rows(design.get("foreshadows"))
    if not lines:
        return
    ledger = load_ledger(out)
    for line in lines:
        reminders = line.get("reminders")
        chapters = [*(reminders if isinstance(reminders, list) else []), line.get("payoff")]
        due = [n for n in map(integer, chapters) if start <= n <= end]
        entry = ledger.get(f"{line.get('id')}-plant", {})
        if due and entry.get("status") == "done" and entry.get("quote"):
            yield dict(chapter=entry.get("chapter"), source_quote=entry["quote"], due=due)


def planning_callbacks(out, history, before, design, start, end):
    candidates = callbacks(history, out=out, before=before, limit=6)
    if out is None:
        return candidates
    scheduled = list(_scheduled_sources(out, design, start, end))
    passages = _recall(out, [*scheduled, *candidates], before, require_completed=True, limit=6)
    return [
        dict(
            chapter=row["chapter"],
            source_quote=row.get("source_quote") or row["original_excerpt"][:160],
            **({"due": row["due"]} if row.get("due") else {}),
        )
        for row in passages
    ]


def outline_callbacks(chapter, candidates, total, end):
    requested = rows(chapter.get("callback_events"))
    number = chapter["number"]
    due = [item for item in candidates if number in item.get("due", [])]
    if due or requested:
        chosen = {}
        for item in [*due, *requested]:
            quote = item.get("source_quote") or item.get("original_excerpt") or item.get("event")
            key = (integer(item.get("chapter")), quote if isinstance(quote, str) else "")
            if key[0] and key[1]:
                chosen.setdefault(key, item)
        return list(chosen.values())[:3]
    general = [item for item in candidates if not item.get("due")]
    return general[:3] if number == total else general[:1] if number == end else []


def writer_callback_section(out, bible, vol, outline, total):
    requested = rows(outline.get("callback_events"))
    if not requested:
        return []
    chapter = outline["number"]
    start = integer(vol.get("start")) or stage_for_chapter(bible, chapter, total)["start"]
    recalled = recall_events(out, [], chapter, total, vol.get("end", chapter), requested=requested, before=start)
    passages = dict.fromkeys((row["chapter"], row["original_excerpt"]) for row in recalled)
    return [
        (f"{HEADING}（第{number}章）", f"{KNOWLEDGE}\n\n第{number}章原文\n{passage}") for number, passage in passages
    ]
