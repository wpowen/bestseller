#!/usr/bin/env python3
"""Diagnostic harness for the AI-flavor investigation (goal: 去AI味).

Runs the *existing* span detector on a batch of chapters AND, in parallel,
counts hits for the model-ism patterns the goal explicitly names but the
current rule set does NOT yet cover. The point is to quantify the gap
before any fix, and to re-run after each round to show the delta.

Usage:
    python scripts/ai_flavor_diagnose.py output/shilouyan-bench-v1/chapter-*.md

Outputs a per-chapter and aggregate table to stdout (and JSON if --json).
No files written unless --json PATH given. Read-only on chapters.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

# Make `src` importable without install.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bestseller.services.ai_flavor.detector import (  # noqa: E402
    _find_dialogue_ranges,
    _is_in_ranges,
    _QUOTE_PAIRS_CN,
    detect,
)

# ── Candidate probes for goal-named, currently-UNCOVERED model-isms ────────
# Each probe is (key, human_label, compiled_regex). We count *narration-only*
# hits (skip matches inside dialogue quotes) to mirror the detector's policy.

_PROBES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "not_x_but_y",
        "不是X，而是Y（否定式下定义）",
        re.compile(r"(?<![不也])不是[^。！？，,\n]{1,18}[，,][^。！？\n]{0,6}而是"),
    ),
    (
        "not_only_but",
        "不只是/不仅X，而是/更是Y",
        re.compile(r"(?:不只是|不仅仅?是?)[^。！？\n]{1,24}(?:而是|更是|更)"),
    ),
    (
        "terse_adj_tag",
        "「冷。」他说 —— 单字情绪+说话标签",
        re.compile(r"[“\"][^”\"]{0,6}[”\"]\s*[，,。]?\s*(?:他|她|[一-鿿]{2,3})(?:说|道)(?:[。，,]|$)"),
    ),
    (
        "micro_action_combo",
        "复合微动作模板（眼皮掀了一下又…/瞳孔缩了一下）",
        re.compile(
            r"(?:眼皮|眼睛|瞳孔|喉结|睫毛|呼吸|心跳|嘴角|手指|指尖|肩膀|后背)"
            r"(?:微微|猛地|轻轻)?(?:掀|动|颤|缩|抖|滞|紧|松|停|顿|跳|沉)"
            r"了?(?:一下|一瞬|半拍|半分)?"
        ),
    ),
    (
        "solo_short_line",
        "独行短句装腔（整段≤12字单句成段）",
        # handled separately (paragraph-level), placeholder regex never used
        re.compile(r"$^"),
    ),
    (
        "conclusion_first",
        "结论先行（抽象判断句起头：这/那是一种…）",
        re.compile(r"(?:这|那)(?:不|就)?是一(?:种|个|场|份)[^。！？\n]{2,20}[。！？]"),
    ),
)


def _count_narration_hits(text: str, pattern: re.Pattern[str]) -> int:
    ranges = _find_dialogue_ranges(text, _QUOTE_PAIRS_CN)
    n = 0
    for m in pattern.finditer(text):
        if _is_in_ranges(m.start(), ranges):
            continue
        n += 1
    return n


def _count_solo_short_lines(text: str, max_chars: int = 12) -> int:
    """Count paragraphs that are a single short sentence (装腔独行句)."""
    n = 0
    for para in text.split("\n"):
        s = para.strip()
        if not s or s.startswith("#"):
            continue
        # one sentence: no internal terminator except a trailing one
        core = s.rstrip("。！？…")
        visible = sum(1 for c in core if not c.isspace())
        if visible == 0 or visible > max_chars:
            continue
        # exclude pure dialogue lines (they're legitimate)
        if s[0] in "“\"「『":
            continue
        # single sentence only (no mid-sentence terminator)
        if re.search(r"[。！？…].", core):
            continue
        n += 1
    return n


def diagnose_chapter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    report = detect(text, language="zh", chapter_number=0)
    probe_counts: dict[str, int] = {}
    for key, _label, pattern in _PROBES:
        if key == "solo_short_line":
            probe_counts[key] = _count_solo_short_lines(text)
        else:
            probe_counts[key] = _count_narration_hits(text, pattern)
    return {
        "file": path.name,
        "chars": len(text),
        "detector_score": round(report.overall_score, 1),
        "detector_block": len(report.block_spans),
        "detector_warn": len(report.warn_spans),
        "detector_info": len(report.info_spans),
        "probes": probe_counts,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("globs", nargs="+", help="chapter glob(s) or paths")
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args()

    paths: list[Path] = []
    for g in args.globs:
        matched = sorted(glob.glob(g))
        paths.extend(Path(p) for p in matched)
    paths = [p for p in paths if p.is_file()]
    if not paths:
        print("no files matched", file=sys.stderr)
        return 2

    rows = [diagnose_chapter(p) for p in paths]

    # Header
    probe_keys = [k for k, _, _ in _PROBES]
    print(f"{'file':<20}{'score':>6}{'blk':>4}{'wrn':>4}  | " + "".join(f"{k[:10]:>12}" for k in probe_keys))
    print("-" * (38 + 12 * len(probe_keys)))
    agg = {k: 0 for k in probe_keys}
    agg_score = 0.0
    for r in rows:
        line = f"{r['file']:<20}{r['detector_score']:>6}{r['detector_block']:>4}{r['detector_warn']:>4}  | "
        line += "".join(f"{r['probes'][k]:>12}" for k in probe_keys)
        print(line)
        agg_score += r["detector_score"]
        for k in probe_keys:
            agg[k] += r["probes"][k]
    print("-" * (38 + 12 * len(probe_keys)))
    n = len(rows)
    total_line = f"{'TOTAL/'+str(n)+'ch':<20}{agg_score/n:>6.1f}{'':>4}{'':>4}  | "
    total_line += "".join(f"{agg[k]:>12}" for k in probe_keys)
    print(total_line)
    print()
    print("Probe legend:")
    for key, label, _ in _PROBES:
        print(f"  {key:<20} {label}")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"rows": rows, "aggregate": agg, "mean_score": agg_score / n}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
