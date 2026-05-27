#!/usr/bin/env python3
"""Verify reveal token cadence against reveal-schedule.yaml."""

from __future__ import annotations

import argparse
from itertools import pairwise
from pathlib import Path
import re
import sys

import yaml


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output-base-dir", default="output")
    parser.add_argument("--max-hook-gap", type=int, default=15)
    args = parser.parse_args()

    root = Path(args.output_base_dir) / args.slug
    schedule = yaml.safe_load(
        (root / "story-bible" / "reveal-schedule.yaml").read_text(encoding="utf-8")
    ) or {}
    chapter_texts: dict[int, str] = {}
    for path in sorted(root.glob("chapter-*.md")):
        match = re.match(r"chapter-(\d+)\.md$", path.name)
        if match:
            chapter_texts[int(match.group(1))] = path.read_text(encoding="utf-8")

    violations: list[str] = []
    max_chapter = max(chapter_texts, default=0)
    for reveal in schedule.get("reveals") or []:
        if not isinstance(reveal, dict):
            continue
        reveal_id = str(reveal.get("id") or "").strip()
        earliest = int(reveal.get("earliest_chapter") or 1)
        tokens = [str(token) for token in reveal.get("tokens") or [] if str(token).strip()]
        if not reveal_id or not tokens or earliest > max_chapter:
            continue
        hits = [
            chapter_no
            for chapter_no, text in chapter_texts.items()
            if chapter_no >= earliest and any(token in text for token in tokens)
        ]
        if not hits:
            violations.append(f"{reveal_id}: no token hit from ch{earliest} to ch{max_chapter}")
            continue
        first = min(hits)
        if first - earliest > args.max_hook_gap:
            violations.append(f"{reveal_id}: first hit ch{first}, earliest ch{earliest}")
        ordered = sorted(hits)
        for left, right in pairwise(ordered):
            if right - left > args.max_hook_gap:
                violations.append(f"{reveal_id}: hook gap ch{left}->ch{right} ({right - left})")

    if violations:
        print(f"FAIL {len(violations)} hook cadence violations:", file=sys.stderr)
        for item in violations[:200]:
            print(f"  {item}", file=sys.stderr)
        return 1
    print("OK reveal hook cadence passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
