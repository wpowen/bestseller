"""Calibrate the logline hard gate against REAL published bestsellers.

Why this exists
---------------
The logline gate is conjunctive: 9 core axes, each of which must independently
reach ``pass_floor`` (3.5/5), judged by an LLM explicitly told to score harshly
("毒辣，防虚高"). A book that scores 4.3/5 overall still dies if ONE axis lands
at 3.0 — which is exactly what happened to custom-xuanhuan-1784875202 on
2026-07-24.

The sibling blurb gate had the identical defect and it was caught the same way:
its bar was set at 80 until someone measured real bestsellers (斗破 71 / 诡秘 68
/ 大奉 68) and recalibrated to 68, with the note "旧值80脱离现实——真爆款简介
都到不了80". The logline gate has never had that measurement.

This script performs it: run the gate over ``config/appeal_reference_blurbs.yaml``
(real, published, commercially-proven titles). Any threshold that rejects
《斗破苍穹》is not measuring story quality — it is measuring the judge's mood.

Read-only: scores references, prints a report, changes nothing.

Usage:
  python scripts/logline_gate_calibration.py
  python scripts/logline_gate_calibration.py --genre xuanhuan --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")

from bestseller.services.logline_gate import (  # noqa: E402
    CORE_AXES,
    evaluate_logline_gate,
    load_logline_gate_config,
)
from bestseller.settings import load_settings  # noqa: E402

_REFERENCES = Path("config/appeal_reference_blurbs.yaml")

# The reference file stores marketing blurbs; the gate judges a one-line story
# premise. Feed the blurb as both — it is the same story material, and a gate
# that cannot recognise 斗破苍穹's story from its own reference text is the
# thing under test.
_GENRE_LABELS = {
    "xuanhuan": ("玄幻", "玄幻"),
    "xianxia": ("仙侠", "仙侠"),
    "urban": ("都市", "都市"),
    "history": ("历史", "历史"),
    "suspense": ("悬疑", "悬疑"),
    "scifi": ("科幻", "科幻"),
    "apocalypse": ("末世", "末世"),
    "game": ("游戏", "游戏"),
}


async def _score_reference(settings, genre_key: str, item: dict) -> dict:
    genre, sub_genre = _GENRE_LABELS.get(genre_key, (genre_key, genre_key))
    blurb = str(item.get("blurb") or "").strip()
    title = str(item.get("title") or "?")
    verdict = await evaluate_logline_gate(
        None,
        settings,
        logline=blurb,
        premise=blurb,
        genre=genre,
        sub_genre=sub_genre,
    )
    scores = dict(verdict.scores or {})
    core = {k: scores.get(k) for k in CORE_AXES}
    below = {k: v for k, v in core.items() if isinstance(v, (int, float)) and v < 3.5}
    return {
        "title": title,
        "genre": genre,
        "action": getattr(verdict.action, "value", str(verdict.action)),
        "overall": round(float(verdict.overall or 0.0), 2),
        "core_scores": core,
        "core_axes_below_pass_floor": below,
        "weakest_axis": verdict.weakest_axis,
    }


async def _run(genre_filter: str | None) -> dict:
    settings = load_settings()
    data = yaml.safe_load(_REFERENCES.read_text(encoding="utf-8")) or {}
    gate = load_logline_gate_config(None)

    rows: list[dict] = []
    for genre_key, items in data.items():
        if genre_filter and genre_key != genre_filter:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("blurb"):
                rows.append(await _score_reference(settings, genre_key, item))

    passed = [r for r in rows if r["action"] == "expand"]
    return {
        "thresholds": {
            "reject_floor": gate.get("reject_floor"),
            "pass_floor": gate.get("pass_floor"),
            "overall_floor": gate.get("overall_floor"),
        },
        "references_scored": len(rows),
        "references_passed": len(passed),
        "pass_rate": round(len(passed) / len(rows), 3) if rows else 0.0,
        "results": rows,
    }


def _render(report: dict) -> str:
    t = report["thresholds"]
    lines = [
        "logline gate calibration — REAL published bestsellers",
        f"  thresholds: reject<{t['reject_floor']} pass<{t['pass_floor']} "
        f"overall<{t['overall_floor']}",
        f"  passed: {report['references_passed']}/{report['references_scored']} "
        f"({report['pass_rate']:.0%})",
        "",
    ]
    for r in report["results"]:
        mark = "PASS" if r["action"] == "expand" else f"BLOCK({r['action']})"
        lines.append(f"  [{mark}] 《{r['title']}》 overall={r['overall']}")
        if r["core_axes_below_pass_floor"]:
            for axis, score in r["core_axes_below_pass_floor"].items():
                lines.append(f"        core axis under floor: {axis}={score}")
    if report["references_scored"] and report["pass_rate"] < 1.0:
        lines += [
            "",
            "  VERDICT: the gate rejects commercially-proven titles. A bar that "
            "blocks 《斗破苍穹》 cannot be measuring whether a story works — "
            "recalibrate against these numbers, as the blurb gate was "
            "(80 → 68 after the same measurement).",
        ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--genre", default=None, help="only this reference bucket")
    parser.add_argument("--json", dest="json_out", default=None)
    args = parser.parse_args()

    report = asyncio.run(_run(args.genre))
    print(_render(report))
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  json -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
