#!/usr/bin/env python3
"""Check volume-plan-v2.yaml and reveal-schedule.yaml alignment."""
# ruff: noqa: ANN401

from __future__ import annotations

import argparse
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml


def _chapter_range(value: Any) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    if isinstance(value, str) and "-" in value:
        left, right = value.split("-", 1)
        try:
            return int(left), int(right)
        except ValueError:
            return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output-base-dir", default="output")
    parser.add_argument("--max-hook-gap", type=int, default=15)
    args = parser.parse_args()

    root = Path(args.output_base_dir) / args.slug / "story-bible"
    volume_plan = yaml.safe_load((root / "volume-plan-v2.yaml").read_text(encoding="utf-8")) or {}
    reveal_schedule = (
        yaml.safe_load((root / "reveal-schedule.yaml").read_text(encoding="utf-8"))
        or {}
    )

    volume_ranges: list[tuple[int, int, str]] = []
    unlocked_by_reveal: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for volume in volume_plan.get("volumes") or []:
        if not isinstance(volume, dict):
            continue
        label = f"volume {volume.get('volume_no', '?')}"
        rng = _chapter_range(volume.get("chapter_range"))
        if rng:
            volume_ranges.append((rng[0], rng[1], label))
        for milestone in volume.get("milestones") or []:
            if not isinstance(milestone, dict):
                continue
            mrng = _chapter_range(milestone.get("chapter_range")) or rng
            if not mrng:
                continue
            milestone_label = str(milestone.get("milestone_label") or label)
            for reveal_id in milestone.get("reveals_unlocked") or []:
                unlocked_by_reveal[str(reveal_id)].append((mrng[0], mrng[1], milestone_label))

    schedule_by_reveal: dict[str, list[int]] = defaultdict(list)
    orphan_schedule: list[str] = []
    orphan_volume: list[str] = []
    for reveal in reveal_schedule.get("reveals") or []:
        if not isinstance(reveal, dict):
            continue
        reveal_id = str(reveal.get("id") or "").strip()
        if not reveal_id:
            continue
        chapters: list[int] = []
        if isinstance(reveal.get("earliest_chapter"), int):
            chapters.append(int(reveal["earliest_chapter"]))
        for key in ("partial_at", "full_at", "chapters", "payoff_at"):
            value = reveal.get(key)
            if isinstance(value, int):
                chapters.append(value)
            elif isinstance(value, list):
                chapters.extend(int(item) for item in value if isinstance(item, int))
        schedule_by_reveal[reveal_id].extend(sorted(set(chapters)))
        if reveal_id not in unlocked_by_reveal:
            orphan_schedule.append(reveal_id)

    for reveal_id in unlocked_by_reveal:
        if reveal_id not in schedule_by_reveal:
            orphan_volume.append(reveal_id)

    out_of_volume: list[str] = []
    for reveal_id, chapters in schedule_by_reveal.items():
        for chapter in chapters:
            if not any(start <= chapter <= end for start, end, _ in volume_ranges):
                out_of_volume.append(f"{reveal_id}: ch{chapter}")

    broken_hooks: list[str] = []
    for reveal_id, chapters in schedule_by_reveal.items():
        unique = sorted(set(chapters))
        for left, right in pairwise(unique):
            gap = right - left
            if gap > args.max_hook_gap:
                broken_hooks.append(f"{reveal_id}: ch{left} -> ch{right} (gap={gap})")

    ok = True
    if orphan_schedule:
        ok = False
        print("FAIL reveal-schedule IDs not unlocked by volume-plan:")
        for item in orphan_schedule:
            print(f"  {item}")
    if orphan_volume:
        ok = False
        print("FAIL volume-plan reveals missing from reveal-schedule:")
        for item in orphan_volume:
            print(f"  {item}")
    if out_of_volume:
        ok = False
        print("FAIL scheduled chapters outside volume-plan ranges:")
        for item in out_of_volume:
            print(f"  {item}")
    if broken_hooks:
        ok = False
        print(f"FAIL hook gaps > {args.max_hook_gap}:")
        for item in broken_hooks:
            print(f"  {item}")

    if ok:
        print("OK volume-plan and reveal-schedule aligned")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
