#!/usr/bin/env python3
"""Check simple cast promise constraints against chapter markdown."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

DEFAULT_RULES = (
    {
        "name": "grandfather_not_early",
        "chapters": range(1, 16),
        "terms": ("林家辉走", "林家辉站", "林家辉坐", "林家辉伸", "林家辉抬", "祖父站", "爷爷站"),
        "message": "林家辉/祖父 line should not appear as an active early-chapter character",
    },
    {
        "name": "wang_jianye_dead_after_ch6",
        "chapters": range(7, 501),
        "terms": ("王建业带路", "王老板带路", "王建业走在前", "王老板走在前"),
        "message": "王建业 should not continue as a living guide after ch6",
    },
    {
        "name": "father_not_direct_villain",
        "chapters": range(1, 121),
        "terms": ("林正淳就是凶手", "林正淳是凶手", "父亲就是凶手"),
        "message": (
            "林正淳 should not be directly resolved as the villain before "
            "the planned reveal"
        ),
    },
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output-base-dir", default="output")
    args = parser.parse_args()

    root = Path(args.output_base_dir) / args.slug
    cast_path = root / "story-bible" / "cast-and-promises.md"
    if not cast_path.exists():
        print(f"FAIL missing {cast_path}", file=sys.stderr)
        return 2

    violations: list[str] = []
    for path in sorted(root.glob("chapter-*.md")):
        match = re.match(r"chapter-(\d+)\.md$", path.name)
        if not match:
            continue
        chapter_no = int(match.group(1))
        text = path.read_text(encoding="utf-8")
        for rule in DEFAULT_RULES:
            if chapter_no not in rule["chapters"]:
                continue
            for term in rule["terms"]:
                if term in text:
                    violations.append(
                        f"ch{chapter_no}: {rule['name']} term={term!r} - {rule['message']}"
                    )

    if violations:
        print(f"FAIL {len(violations)} cast promise violations:", file=sys.stderr)
        for item in violations[:200]:
            print(f"  {item}", file=sys.stderr)
        return 1
    print("OK cast promise checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
