"""Read-only story-layer section of the whole-book checkup.

Counts only. None of these numbers says whether the book is good to read.
"""

from collections import Counter
from pathlib import Path
import json
import re
import yaml
from .q1_schema import integer, rows
from .story_design import SPEC, load_story_design
from .story_duties import CLOSED, all_duties, load_ledger
from .support_stakes import KINDS, classify

QUOTED = re.compile(r"“[^”]*”")
PER = 10000


def _load_yaml(path):
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _chapters(book, source):
    book = Path(book)
    pattern = "final/chapter-*.md" if source == "final" and (book / "final").is_dir() else "chapter-*.body.md"
    texts = {}
    for path in sorted(book.glob(pattern)):
        found = re.search(r"chapter-(\d+)", path.name)
        if found:
            texts[int(found.group(1))] = path.read_text(encoding="utf-8")
    return texts


def thread_summary(threads):
    beats = [
        str(beat.get("description") or "").strip()
        for line in rows(threads.get("threads") if isinstance(threads, dict) else threads)
        for beat in rows(line.get("beats"))
    ]
    filled = [text for text in beats if text]
    return dict(beats=len(beats), empty=len(beats) - len(filled), distinct=len(set(filled)))


def reader_metrics(texts, bible):
    body = "\n".join(texts.values())
    size = max(1, len(body))
    words = SPEC["metrics"]
    cast = []
    for card in rows(bible.get("characters")):
        name = card.get("name")
        if not name:
            continue
        speaking = re.compile(re.escape(name) + r"[^。“”\n]{0,12}[：:，]?“")
        cast.append(
            dict(
                name=name,
                role=card.get("role"),
                chapters=sum(name in text for text in texts.values()),
                speech_tags=sum(len(speaking.findall(text)) for text in texts.values()),
            )
        )
    return dict(
        characters=len(body),
        dialogue_share=round(sum(len(x) for x in QUOTED.findall(body)) / size, 3),
        interior_per_10k=round(sum(body.count(word) for word in words["interior"]) * PER / size, 1),
        institutional_per_10k=round(sum(body.count(word) for word in words["institutional"]) * PER / size, 1),
        cast=cast,
        never_on_page=[row["name"] for row in cast if row["chapters"] == 0],
    )


def duty_summary(design, bible, ledger, written, texts=None):
    """Where every scheduled obligation stands after the chapters written so far."""
    counts = Counter()
    by_kind = {}
    open_items = []
    support = []
    for duty in all_duties(design, bible):
        entry = ledger.get(duty["id"], {})
        if entry.get("status") in CLOSED:
            status = entry["status"]
        elif duty["chapter"] > written:
            status = "not_due"
        elif entry.get("status") == "missing":
            status = "missing"
        else:
            status = "not_issued"
        if duty["kind"] in KINDS:
            observed = entry.get("goal_state", "unknown")
            if texts is not None and entry.get("status") in CLOSED:
                chapter = entry.get("chapter")
                text = texts.get(chapter, "")
                evidence = entry.get("evidence") or []
                if not all(proof.get("chapter") == chapter and proof.get("quote") in text for proof in evidence):
                    evidence = []
                verified, observed = classify(
                    dict(kind=duty["kind"], owner=duty["names"][0], target=duty.get("target", "")),
                    observed,
                    evidence,
                    text,
                )
                status = "done" if verified == "fulfilled" else "unverified"
            support.append(
                dict(
                    id=duty["id"],
                    owner=duty["names"][0],
                    desire=duty["desire"],
                    chapter=entry.get("chapter"),
                    scheduled=duty["chapter"],
                    status=status,
                    goal_state=observed,
                )
            )
        counts[status] += 1
        by_kind.setdefault(duty["kind"], Counter())[status] += 1
        if status in ("missing", "not_issued"):
            open_items.append(
                dict(
                    id=duty["id"],
                    kind=duty["kind"],
                    scheduled=duty["chapter"],
                    status=status,
                    tried=entry.get("attempts", []),
                )
            )
    return dict(
        total=sum(counts.values()),
        status=dict(counts),
        by_kind={kind: dict(value) for kind, value in by_kind.items()},
        open=open_items,
        support_stakes=support,
    )


def story_report(book, source="final"):
    book = Path(book)
    bible = _load_yaml(book / "story_bible.yaml")
    threads = _load_yaml(book / "threads.yaml")
    design = load_story_design(book)
    texts = _chapters(book, source)
    gate_path = book / "design-gate.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8")) if gate_path.exists() else {}
    report = dict(
        reference_only=True,
        note="只统计设计是否齐全、排期是否写进正文和几项文字比例；不评价好不好看，不代替读者验收",
        design_present=bool(design),
        design_gate=dict(status=gate.get("status", "not_run"), final_issues=gate.get("final_issues", [])),
        design_gaps=(design.get("generation") or {}).get("remaining_gaps", []) if design else [],
        threads=thread_summary(threads),
        reader_metrics=reader_metrics(texts, bible) if texts else {},
    )
    if design:
        report["duties"] = duty_summary(design, bible, load_ledger(book), max(texts, default=0), texts)
        report["schedule"] = dict(
            foreshadows=[
                {k: row.get(k) for k in ("id", "anchor", "plant", "reminders", "payoff")}
                for row in rows(design.get("foreshadows"))
            ],
            truth_reveals=[
                {k: row.get(k) for k in ("layer", "chapter", "anchor")} for row in rows(design.get("truth_reveals"))
            ],
            arc=[{k: row.get(k) for k in ("kind", "chapter")} for row in rows(design.get("arc"))],
            total=integer(design.get("total")),
        )
    return report
