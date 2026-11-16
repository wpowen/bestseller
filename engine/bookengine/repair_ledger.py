"""Instruction 68: per-book tally of what each repair did, read from files the run already saved.

Reference only. "adopted" means the repaired text was kept; it does not mean the chapter got better.
"""

from collections import Counter
import json
from pathlib import Path
import re

from .hook_momentum import tail_regression
from .repair_additions import added_paragraph_findings
from .text import chinese_count

_STAGE = re.compile(r"^r\d+-ch\d+-(.+?)(?:-\d+)?$")
_STATUSES = {"repaired", "rolled_back", "partially_rolled_back", "checked", "kept", "skipped", "unchanged"}
_NOTE_KEYS = ("polish", "cleaning", "opening_promises", "story_payoffs", "opponent_repair", "exit_tail_repair")


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _notes(record: dict):
    """Yield (repair name, note) for every saved repair note in one chapter record."""
    for key in _NOTE_KEYS:
        value = record.get(key)
        if not isinstance(value, dict):
            continue
        if value.get("status") in _STATUSES:
            yield key, value
        for name, note in value.items():
            if isinstance(note, dict) and note.get("status") in _STATUSES:
                yield name, note


def repair_ledger(book: Path | str) -> dict:
    book = Path(book)
    calls = _load(book / "calls.json")
    calls = calls if isinstance(calls, list) else (calls or {}).get("calls", [])
    stages = Counter(m[1] for row in calls if isinstance(row, dict) and (m := _STAGE.match(str(row.get("stage")))))
    outcomes: dict[str, Counter] = {}
    reasons: dict[str, Counter] = {}
    for path in sorted((book / "chapters").glob("[0-9][0-9][0-9].json")):
        for name, note in _notes(_load(path) or {}):
            outcomes.setdefault(name, Counter())[note["status"]] += 1
            if note.get("reason"):
                reasons.setdefault(name, Counter())[str(note["reason"])] += 1
    for path in sorted((book / "steps").glob("*-momentum.json")):
        note = _load(path) or {}
        for part in ("hook", "payoff"):
            status = (note.get(part) or {}).get("status")
            if status in _STATUSES | {"not_due"}:
                outcomes.setdefault("momentum_" + part, Counter())[status] += 1
                if (note.get(part) or {}).get("reason"):
                    reasons.setdefault("momentum_" + part, Counter())[str(note[part]["reason"])] += 1
    after = Counter()
    chapters = 0
    for body in sorted(book.glob("chapter-[0-9][0-9][0-9].body.md")):
        draft = body.with_name(body.name.replace(".body.md", ".r2.draft.md"))
        if not draft.is_file():
            continue
        chapters += 1
        before, final = draft.read_text(), body.read_text()
        after["changed_chapters"] += final != before
        after["repair_paragraphs_restating_chapter"] += len(added_paragraph_findings(before, final))
        regression = tail_regression(before, final) if final != before else None
        if regression:
            after[regression] += 1
        after["chars_added"] += max(0, chinese_count(final) - chinese_count(before))
    draft_calls = sum(count for stage, count in stages.items() if stage.startswith("draft"))
    chapter_calls = sum(stages.values())
    return dict(
        reference_only=True,
        chapters_compared=chapters,
        calls=dict(
            chapter_total=chapter_calls,
            draft=draft_calls,
            after_draft=chapter_calls - draft_calls,
            by_stage=dict(stages.most_common()),
        ),  # fmt: skip
        outcomes={name: dict(counter) for name, counter in sorted(outcomes.items())},
        rollback_reasons={name: dict(counter) for name, counter in sorted(reasons.items())},
        draft_to_final=dict(after),
        status="Evidence",
        limitation="Counts of adoption and rollback; whether an adopted repair improved the chapter needs a reader.",
    )
