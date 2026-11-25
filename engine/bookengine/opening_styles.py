"""Rolling chapter-opening categories and bounded style repair."""

import re
from tools.chapter_files import chapter_files, chapter_number

from .text import _paragraph_spans


WINDOW = 10
LIMIT = 3
_MOVEMENT = re.compile(
    r"^(?:离开|抵达|到达|进入|走出|退入|转入|折返|返回|回到|赶到|来到|沿|从)"
    r"|^[^，,。！？]{2,10}(?:离开|抵达|到达|进入|走出|退入|转入|折返|返回|回到|赶到|来到)"
    r"|^[^，,。！？]{2,16}(?:从|沿)[^，,。！？]{1,24}(?:转入|退入|折返|进入|抵达|赶到|回到)"
)


def opening_style(text):
    opening = text.lstrip()[:180]
    first = re.split(r"[。！？!?]", opening, 1)[0]
    return "movement_transition" if _MOVEMENT.search(first) else "other"


def _history(out, chapter):
    rows = []
    for path in chapter_files(out, "chapter-*.body.md"):
        number = chapter_number(path, "chapter-*.body.md")
        if chapter - WINDOW < number < chapter:
            rows.append({"number": number, "text": path.read_text("utf-8")})
    return rows


def _issue(history, chapter, text):
    category = opening_style(text)
    if category == "other":
        return None
    used = [
        row["number"]
        for row in history
        if chapter - WINDOW < row["number"] < chapter and opening_style(row["text"]) == category
    ]
    window = [*used, chapter]
    if len(window) <= LIMIT:
        return None
    return {
        "chapter": chapter,
        "category": category,
        "window_chapters": window,
        "limit": LIMIT,
        "window": WINDOW,
        "status": "Evidence",
    }


def opening_style_checkup(chapters):
    history = []
    issues = []
    for chapter in sorted(chapters, key=lambda row: row.get("number", 0)):
        if issue := _issue(history, chapter["number"], chapter.get("text", "")):
            issues.append(issue)
        history.append(chapter)
    return {
        "counts": {"over_limit": len(issues)},
        "issues": issues,
        "rules": {"window_chapters": WINDOW, "max_per_category": LIMIT},
        "authority": "deterministic_opening_category",
    }


def writer_opening_style_context(out, chapter):
    history = _history(out, chapter)
    used = sum(opening_style(row["text"]) == "movement_transition" for row in history)
    if used < LIMIT:
        return []
    return [("章首句式类别约束", f"最近10章移动衔接类已使用{used}次；达到上限，本章改用其他开场类别。")]


def repair_opening_style(client, prefix, text, raw, failed, state, bible, outline, out, extract, ask):
    """Replace one over-limit opening paragraph, then recheck style, facts, and handoff."""
    chapter = outline.get("number", 0)
    history = _history(out, chapter)
    issue = _issue(history, chapter, text)
    note = {"status": "checked", "before": issue, "after": issue}
    if not issue:
        return text, raw, failed, note
    spans = _paragraph_spans(text)
    if not spans:
        return text, raw, failed, dict(note, status="rolled_back", reason="missing_opening_paragraph")
    end = spans[0][1]
    response = ask(
        client,
        prefix + "-opening-style-repair",
        "只改第一个自然段，换成不同的章首类别；保留人物、地点、时间、移动事实与本章目标。"
        "不得复用最近超额类别，不给出或套用示例句。返回JSON。",
        [
            ("超额类别与滑窗计数", issue),
            ("待改第一段", text[:end]),
            ("本章合同与当前状态", [outline, state]),
        ],
        '{"text":"替换后的一个自然段"}',
        cap=1200,
        input_limit=6000,
        max_attempts=1,
    )
    replacement = response.get("text") if isinstance(response, dict) else None
    if not isinstance(replacement, str) or not replacement.strip() or len(_paragraph_spans(replacement)) != 1:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_opening_style_patch")
    candidate = replacement.strip() + text[end:]
    remaining = _issue(history, chapter, candidate)
    note["after"] = remaining
    if remaining:
        return text, raw, failed, dict(note, status="rolled_back", reason="opening_style_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-opening-style-state", candidate, state, bible, outline, [], final=True
    )
    if invalid:
        return text, raw, failed, dict(note, status="rolled_back", reason="opening_style_state_failed")
    from .q1_continuity import boundary_facts, evaluate_handoff

    handoff = evaluate_handoff(state, boundary_facts(checked, candidate), candidate, bible, outline)
    if handoff.get("issue") or handoff.get("missing"):
        return (
            text,
            raw,
            failed,
            dict(note, status="rolled_back", reason="opening_style_handoff_failed", handoff=handoff),
        )
    return candidate, checked, False, dict(note, status="repaired", handoff=handoff)
