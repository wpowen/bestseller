"""Retrofit audit — score already-finished chapters against quality_levers detectors.

Usage::

    python scripts/quality_levers_retrofit_audit.py \\
        --slug exorcist-detective-1778428166 \\
        [--platform qimao] \\
        [--limit 0]

For every chapter under ``output/<slug>/`` (or the database when ``--from-db``
is set) this script runs the deterministic detectors that ship with the
``quality_levers`` package and writes a per-chapter audit row to:

* ``output/<slug>/audits/quality-retrofit/window-N-M.csv``
* ``output/<slug>/audits/quality-retrofit/summary.md``

The detectors used are:

* word count vs the platform pacing envelope (``evaluate_word_count``)
* pulse-density / heart-rate words (``compute_pulse_density``)
* AI-voice banned patterns (``scan_banned_patterns``)
* abstract sensory adjectives (``scan_abstract_sensory_terms``)
* psychological-dumping paragraphs (``detect_psychological_dumping``)
* rhythm anchor coverage (``audit_rhythm``)
* emotion-label violations (``audit_emotion_labels``)

The output is a flat CSV that can be sorted in a spreadsheet or fed into a
``Layer 2`` surgical-patch loop (each detector failure points to a specific
``rejection_repair_playbook`` cause id).

Cost: zero LLM calls. Runtime: a few seconds per book.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Allow ``python scripts/quality_levers_retrofit_audit.py`` to import from
# ``src/`` without requiring an editable install.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from bestseller.services.quality_levers import (  # noqa: E402
    audit_chapter,
    audit_emotion_labels,
    audit_rhythm,
)


_CHAPTER_FILE_RE = re.compile(r"chapter-(\d+)\.md$")


@dataclass
class ChapterAuditRow:
    """One row in the retrofit CSV."""

    slug: str
    chapter_number: int
    char_count: int
    word_count_passed: bool
    word_count_reason: str
    pulse_density: float
    pulse_passed: bool
    banned_pattern_hits: int
    banned_patterns_passed: bool
    banned_pattern_breakdown: str
    abstract_sensory_hits: int
    abstract_sensory_passed: bool
    abstract_sensory_words: str
    dumping_hits: int
    dumping_passed: bool
    rhythm_total_anchors: int
    rhythm_passed: bool
    emotion_label_hits: int
    emotion_label_passed: bool
    failure_count: int
    cause_ids: tuple[str, ...] = field(default_factory=tuple)
    priority: str = "ok"  # "critical" | "high" | "medium" | "ok"

    def as_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "chapter_number": self.chapter_number,
            "char_count": self.char_count,
            "word_count_passed": self.word_count_passed,
            "word_count_reason": self.word_count_reason,
            "pulse_density": round(self.pulse_density, 2),
            "pulse_passed": self.pulse_passed,
            "banned_pattern_hits": self.banned_pattern_hits,
            "banned_patterns_passed": self.banned_patterns_passed,
            "banned_pattern_breakdown": self.banned_pattern_breakdown,
            "abstract_sensory_hits": self.abstract_sensory_hits,
            "abstract_sensory_passed": self.abstract_sensory_passed,
            "abstract_sensory_words": self.abstract_sensory_words,
            "dumping_hits": self.dumping_hits,
            "dumping_passed": self.dumping_passed,
            "rhythm_total_anchors": self.rhythm_total_anchors,
            "rhythm_passed": self.rhythm_passed,
            "emotion_label_hits": self.emotion_label_hits,
            "emotion_label_passed": self.emotion_label_passed,
            "failure_count": self.failure_count,
            "cause_ids": ";".join(self.cause_ids),
            "priority": self.priority,
        }


def discover_chapters(slug: str) -> list[tuple[int, Path]]:
    """Return ``[(chapter_number, path), …]`` for every chapter file."""

    base = _REPO_ROOT / "output" / slug
    if not base.exists():
        return []
    rows: list[tuple[int, Path]] = []
    for path in sorted(base.glob("chapter-*.md")):
        m = _CHAPTER_FILE_RE.search(path.name)
        if not m:
            continue
        rows.append((int(m.group(1)), path))
    return rows


def audit_one_chapter(slug: str, chapter_number: int, path: Path, *, platform: str | None) -> ChapterAuditRow:
    """Score one chapter against every deterministic detector."""

    text = path.read_text(encoding="utf-8")
    bundle = audit_chapter(text, platform=platform)
    rhythm = audit_rhythm(text)
    emotion = audit_emotion_labels(text)

    banned_breakdown = ";".join(
        f"{hit.pattern_id}:{hit.count}" for hit in bundle.banned_patterns.hits
    )
    abstract_words = ";".join(
        f"{word}:{count}" for word, count in bundle.abstract_sensory.hits
    )

    failures: list[str] = []
    cause_ids: list[str] = []
    if not bundle.word_count.passed:
        failures.append("word_count")
        # The repair playbook doesn't have a word_count cause; surface
        # ``flat_narration`` as a coarse fallback when length is too short
        # (under-density) and skip for over-length (no playbook entry).
        if bundle.word_count.reason.startswith("underflow"):
            cause_ids.append("flat_narration")
    if not bundle.pulse.passed:
        failures.append("pulse")
        cause_ids.append("weak_attraction")
    if not bundle.banned_patterns.passed:
        failures.append("banned_patterns")
        cause_ids.append("ai_voice")
    if not bundle.abstract_sensory.passed:
        failures.append("abstract_sensory")
        cause_ids.append("weak_prose")
    if not bundle.dumping.passed:
        failures.append("dumping")
        cause_ids.append("weak_immersion")
    if not rhythm.passed:
        failures.append("rhythm")
        cause_ids.append("flat_narration")
    if not emotion.passed:
        failures.append("emotion_labels")
        cause_ids.append("weak_prose")

    priority = _failure_priority(bundle, dumping_hits=bundle.dumping.total_hits)

    return ChapterAuditRow(
        slug=slug,
        chapter_number=chapter_number,
        char_count=bundle.word_count.chars,
        word_count_passed=bundle.word_count.passed,
        word_count_reason=bundle.word_count.reason,
        pulse_density=bundle.pulse.density_per_300_chars,
        pulse_passed=bundle.pulse.passed,
        banned_pattern_hits=bundle.banned_patterns.total_hits,
        banned_patterns_passed=bundle.banned_patterns.passed,
        banned_pattern_breakdown=banned_breakdown,
        abstract_sensory_hits=bundle.abstract_sensory.total_hits,
        abstract_sensory_passed=bundle.abstract_sensory.passed,
        abstract_sensory_words=abstract_words,
        dumping_hits=bundle.dumping.total_hits,
        dumping_passed=bundle.dumping.passed,
        rhythm_total_anchors=rhythm.total_anchors,
        rhythm_passed=rhythm.passed,
        emotion_label_hits=emotion.total_hits,
        emotion_label_passed=emotion.passed,
        failure_count=len(failures),
        cause_ids=tuple(dict.fromkeys(cause_ids)),
        priority=priority,
    )


def _failure_priority(bundle, *, dumping_hits: int) -> str:
    """Translate detector results into a coarse triage priority."""

    if not bundle.banned_patterns.passed and bundle.banned_patterns.total_hits >= 3:
        return "critical"
    if dumping_hits >= 2:
        return "critical"
    if not bundle.word_count.passed:
        return "high"
    if not bundle.banned_patterns.passed or dumping_hits >= 1:
        return "high"
    if not bundle.pulse.passed or not bundle.abstract_sensory.passed:
        return "medium"
    return "ok"


def write_csv(rows: Iterable[ChapterAuditRow], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    rows_list = list(rows)
    if not rows_list:
        return
    fieldnames = list(rows_list[0].as_dict().keys())
    with target.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows_list:
            writer.writerow(row.as_dict())


def write_summary(rows: list[ChapterAuditRow], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    by_priority = {"critical": 0, "high": 0, "medium": 0, "ok": 0}
    for row in rows:
        by_priority[row.priority] = by_priority.get(row.priority, 0) + 1
    cause_freq: dict[str, int] = {}
    for row in rows:
        for cause in row.cause_ids:
            cause_freq[cause] = cause_freq.get(cause, 0) + 1
    cause_lines = "\n".join(
        f"- `{cause}` × {count}" for cause, count in sorted(cause_freq.items(), key=lambda kv: -kv[1])
    )
    critical_chapters = [row for row in rows if row.priority == "critical"]
    critical_chapters.sort(key=lambda r: r.chapter_number)
    critical_lines = "\n".join(
        f"- ch{row.chapter_number:03d}: causes={','.join(row.cause_ids) or '—'} "
        f"banned={row.banned_pattern_hits} dumping={row.dumping_hits} "
        f"abstract={row.abstract_sensory_hits}"
        for row in critical_chapters[:30]
    )
    total = max(1, len(rows))
    md = (
        f"# Quality Levers Retrofit Audit · summary\n\n"
        f"Total chapters scanned: **{len(rows)}**\n\n"
        f"## Priority distribution\n\n"
        f"| priority | chapters | share |\n"
        f"|----------|---------:|------:|\n"
        f"| critical | {by_priority.get('critical', 0)} | "
        f"{100 * by_priority.get('critical', 0) / total:.1f}% |\n"
        f"| high     | {by_priority.get('high', 0)} | "
        f"{100 * by_priority.get('high', 0) / total:.1f}% |\n"
        f"| medium   | {by_priority.get('medium', 0)} | "
        f"{100 * by_priority.get('medium', 0) / total:.1f}% |\n"
        f"| ok       | {by_priority.get('ok', 0)} | "
        f"{100 * by_priority.get('ok', 0) / total:.1f}% |\n\n"
        f"## Top cause_ids (Layer 2 surgical patch targets)\n\n"
        f"{cause_lines or '_no failing causes_'}\n\n"
        f"## Critical chapters (first 30)\n\n"
        f"{critical_lines or '_no critical chapters_'}\n"
    )
    target.write_text(md, encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retrofit audit for already-finished chapters."
    )
    parser.add_argument(
        "--slug",
        required=True,
        help="Project slug; chapters are read from output/<slug>/chapter-*.md",
    )
    parser.add_argument(
        "--platform",
        default=None,
        help=(
            "Platform id (qimao / qidian / tomato). When omitted the word-count "
            "gate uses the framework default (≥5000)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only audit the first N chapters (0 = all).",
    )
    parser.add_argument(
        "--out-csv",
        default=None,
        help=(
            "Override output CSV path. Default: "
            "output/<slug>/audits/quality-retrofit/window-001-XXX.csv"
        ),
    )
    parser.add_argument(
        "--out-summary",
        default=None,
        help=(
            "Override summary Markdown path. Default: "
            "output/<slug>/audits/quality-retrofit/summary.md"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also emit a sibling .json file with the raw per-chapter rows.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    chapters = discover_chapters(args.slug)
    if not chapters:
        print(f"No chapters found under output/{args.slug}/", file=sys.stderr)
        return 1
    if args.limit and args.limit > 0:
        chapters = chapters[: args.limit]

    rows: list[ChapterAuditRow] = []
    for chapter_number, path in chapters:
        try:
            rows.append(
                audit_one_chapter(
                    args.slug,
                    chapter_number,
                    path,
                    platform=args.platform,
                )
            )
        except Exception as exc:  # noqa: BLE001 — surface in the row log
            print(
                f"  ⚠ ch{chapter_number:03d}: detector exception — {exc!r}",
                file=sys.stderr,
            )

    base = _REPO_ROOT / "output" / args.slug / "audits" / "quality-retrofit"
    end = rows[-1].chapter_number if rows else 0
    csv_path = Path(args.out_csv) if args.out_csv else base / f"window-001-{end:03d}.csv"
    summary_path = Path(args.out_summary) if args.out_summary else base / "summary.md"
    write_csv(rows, csv_path)
    write_summary(rows, summary_path)

    if args.json:
        json_path = csv_path.with_suffix(".json")
        json_path.write_text(
            json.dumps([row.as_dict() for row in rows], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    total = max(1, len(rows))
    critical = sum(1 for r in rows if r.priority == "critical")
    high = sum(1 for r in rows if r.priority == "high")
    medium = sum(1 for r in rows if r.priority == "medium")
    ok = sum(1 for r in rows if r.priority == "ok")
    print(
        f"Audited {len(rows)} chapters → "
        f"critical={critical} ({100*critical/total:.1f}%) "
        f"high={high} ({100*high/total:.1f}%) "
        f"medium={medium} ({100*medium/total:.1f}%) "
        f"ok={ok} ({100*ok/total:.1f}%)"
    )
    print(f"CSV     : {csv_path}")
    print(f"Summary : {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
