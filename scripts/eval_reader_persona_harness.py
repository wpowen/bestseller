#!/usr/bin/env python3
"""Reader-persona eval harness — regression baseline for quality gates.

Usage:
  python3 scripts/eval_reader_persona_harness.py \\
    --chapter-file output/exorcist-detective-1778051012/chapter-001.md \\
    --position 1

Exits 0 when gates pass; 1 when blocking findings fire.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

from bestseller.services.chapter_duplicate_gate import check_chapter_duplicates
from bestseller.services.chapter_length_gate import check_chapter_length
from bestseller.services.chapter_word_count_truth import (
    check_word_count_metadata_truth,
    measure_chapter_body_zh_chars,
)
from bestseller.services.chapter_orchestrator import grade_chapter, prepare_chapter_context
from bestseller.services.payoff_ledger_gate import evaluate_payoff_ledger
from bestseller.services.persona_quality_gate import evaluate_persona_quality
from bestseller.services.reader_persona_calibration import compare_to_baseline


def main() -> int:
    parser = argparse.ArgumentParser(description="Reader-persona eval harness")
    parser.add_argument("--chapter-file", type=Path, required=True)
    parser.add_argument("--position", type=int, default=1)
    parser.add_argument("--slug", type=str, default="eval-harness")
    parser.add_argument("--prev-chapter-file", type=Path, default=None)
    parser.add_argument("--stored-word-count", type=int, default=0)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    text = args.chapter_file.read_text(encoding="utf-8")
    prev_text = (
        args.prev_chapter_file.read_text(encoding="utf-8")
        if args.prev_chapter_file and args.prev_chapter_file.is_file()
        else None
    )
    actual = measure_chapter_body_zh_chars(text)

    length = check_chapter_length(text, chapter_position=args.position)
    truth = check_word_count_metadata_truth(
        text,
        stored_word_count=args.stored_word_count or None,
    )
    dup = check_chapter_duplicates(
        chapter_position=args.position,
        chapter_text=text,
        prev_chapter_text=prev_text,
    )
    payoff = evaluate_payoff_ledger(text, chapter_position=args.position)

    ctx = prepare_chapter_context(
        args.slug,
        args.position,
        output_base_dir=str(_REPO / "output"),
    )
    persona = grade_chapter(
        ctx,
        text,
        output_base_dir=str(_REPO / "output"),
        persist=False,
    )
    persona_gate = evaluate_persona_quality(persona)

    calibration = [
        asdict(compare_to_baseline("commercial_pass_weighted_score", persona.weighted_score)),
        asdict(compare_to_baseline("commercial_pass_abandon_rate", persona.abandon_rate)),
    ]

    blocking: list[str] = []
    if length.has_critical:
        blocking.append(length.finding.code)
    if truth.finding.severity == "critical":
        blocking.append(truth.finding.code)
    for f in dup.findings:
        if f.severity == "critical":
            blocking.append(f.code)
    if payoff.finding.severity == "critical":
        blocking.append(payoff.finding.code)
    blocking.extend(persona_gate.auto_repair_codes)

    report = {
        "chapter_file": str(args.chapter_file),
        "actual_zh_chars": actual,
        "length": length.finding.code,
        "truth": truth.finding.code,
        "duplicate_findings": [f.code for f in dup.findings],
        "payoff": payoff.finding.code,
        "persona_weighted_score": persona.weighted_score,
        "persona_abandon_rate": persona.abandon_rate,
        "persona_blocking": list(persona_gate.auto_repair_codes),
        "blocking_codes": blocking,
        "calibration": calibration,
    }

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
