#!/usr/bin/env python3
"""Compare outline-specificity baseline stages."""
# ruff: noqa: ANN401

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def extract_scores(data: Any) -> list[float]:
    items = data if isinstance(data, list) else data.get("items") or data.get("results") or []
    scored: list[float] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        value = (
            item.get("specificity_score")
            or item.get("score")
            or (item.get("metrics") or {}).get("specificity")
        )
        if isinstance(value, (int, float)):
            scored.append(float(value))
    return scored


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--threshold-avg", type=float, default=0.80)
    parser.add_argument("--threshold-min", type=float, default=0.60)
    args = parser.parse_args()

    final_avg: float | None = None
    final_min: float | None = None
    for stage in ("before", "after", "expanded", "final"):
        path = Path(
            f"output/{args.slug}/audits/outline-specificity-baseline-"
            f"{args.baseline}-{stage}.json"
        )
        if not path.exists():
            print(f"{stage}: NOT FOUND")
            continue
        scores = extract_scores(json.loads(path.read_text(encoding="utf-8")))
        if not scores:
            print(f"{stage}: no scores extracted")
            continue
        avg = sum(scores) / len(scores)
        low = min(scores)
        high = max(scores)
        below_min = sum(1 for score in scores if score < args.threshold_min)
        print(
            f"{stage}: n={len(scores)} avg={avg:.3f} "
            f"min={low:.3f} max={high:.3f} below_min={below_min}"
        )
        if stage == "final":
            final_avg = avg
            final_min = low

    if final_avg is None or final_min is None:
        print("FAIL no final baseline", file=sys.stderr)
        return 2
    if final_avg < args.threshold_avg or final_min < args.threshold_min:
        print(
            f"FAIL final.avg={final_avg:.3f} need>={args.threshold_avg}; "
            f"final.min={final_min:.3f} need>={args.threshold_min}",
            file=sys.stderr,
        )
        return 1
    print("OK outline specificity final passes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
