#!/usr/bin/env python3
"""Verify that chapter first sentences do not repeat too often."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


def _first_body_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            continue
        return stripped
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--max-repeats", type=int, default=2)
    parser.add_argument("--output-base-dir", default="output")
    args = parser.parse_args()

    chapter_dir = Path(args.output_base_dir) / args.slug
    first_sentences: dict[str, list[int]] = {}
    for path in sorted(chapter_dir.glob("chapter-*.md")):
        match = re.match(r"chapter-(\d+)\.md$", path.name)
        if not match:
            continue
        chapter_no = int(match.group(1))
        first = _first_body_line(path.read_text(encoding="utf-8"))
        if first:
            first_sentences.setdefault(first, []).append(chapter_no)

    repeats = [
        (sentence, chapters)
        for sentence, chapters in first_sentences.items()
        if len(chapters) > args.max_repeats
    ]
    if not repeats:
        print(
            f"OK all first sentences within {args.max_repeats}-repeat threshold "
            f"({len(first_sentences)} unique sentences)"
        )
        return 0

    print(f"FAIL {len(repeats)} sentences exceed {args.max_repeats} repeats:", file=sys.stderr)
    for sentence, chapters in sorted(repeats, key=lambda item: -len(item[1])):
        print(f"  {len(chapters)}x chapters {chapters}: {sentence[:100]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
