#!/usr/bin/env python3
"""Scan chapter markdown for timeline anchors that violate timeline-canon.md."""
# ruff: noqa: RUF001

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any

import yaml

TIME_PATTERNS = (
    re.compile(r"([一二两三四五六七八九十百千万零〇\d]+)\s*年前"),
    re.compile(r"([甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])年"),
    re.compile(r"([一二两三四五六七八九十百千万零〇\d]+)\s*岁(?:那年|时|的时候)?"),
    re.compile(r"公元\s*(\d{3,4})\s*年"),
)


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\n(.*?)\n---", text, flags=re.DOTALL)
    if not match:
        return {}
    return yaml.safe_load(match.group(1)) or {}


def _zh_int(text: str) -> int | None:
    stripped = text.strip()
    if stripped.isdigit():
        return int(stripped)
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if stripped in digits:
        return digits[stripped]
    if "百" in stripped or "千" in stripped or "万" in stripped:
        return None
    if "十" in stripped:
        left, _, right = stripped.partition("十")
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output-base-dir", default="output")
    args = parser.parse_args()

    root = Path(args.output_base_dir) / args.slug
    canon = _frontmatter(root / "story-bible" / "timeline-canon.md")
    allowed_years_ago = {
        int(event["anchor_years_ago"])
        for event in canon.get("events") or []
        if isinstance(event, dict) and isinstance(event.get("anchor_years_ago"), int)
    }
    forbidden_years_ago = {
        int(item["years_ago"]): str(item.get("reason") or "")
        for item in canon.get("forbidden_anchors") or []
        if isinstance(item, dict) and isinstance(item.get("years_ago"), int)
    }
    allowed_year_names = {
        str(item.get("year_name")): int(item.get("anchor_years_ago"))
        for item in canon.get("allowed_year_names") or []
        if (
            isinstance(item, dict)
            and item.get("year_name")
            and isinstance(item.get("anchor_years_ago"), int)
        )
    }
    current_age = int((canon.get("protagonist") or {}).get("current_age") or 0)

    violations: list[str] = []
    for path in sorted(root.glob("chapter-*.md")):
        match = re.match(r"chapter-(\d+)\.md$", path.name)
        if not match:
            continue
        chapter_no = int(match.group(1))
        text = path.read_text(encoding="utf-8")
        for hit in TIME_PATTERNS[0].finditer(text):
            years = _zh_int(hit.group(1))
            if years is None:
                continue
            if years in forbidden_years_ago:
                violations.append(
                    f"ch{chapter_no}: forbidden {years}年前 "
                    f"({forbidden_years_ago[years]})"
                )
            elif years not in allowed_years_ago:
                violations.append(f"ch{chapter_no}: unregistered {years}年前")
        for hit in TIME_PATTERNS[1].finditer(text):
            year_name = hit.group(1)
            if year_name not in allowed_year_names:
                violations.append(f"ch{chapter_no}: unregistered year name {year_name}年")
        if current_age:
            local_years = [_zh_int(hit.group(1)) for hit in TIME_PATTERNS[0].finditer(text)]
            local_ages = [_zh_int(hit.group(1)) for hit in TIME_PATTERNS[2].finditer(text)]
            for years in [item for item in local_years if item is not None]:
                expected_age = current_age - years
                for age in [item for item in local_ages if item is not None]:
                    if abs(expected_age - age) > 1 and years in {3, 23, 30}:
                        violations.append(
                            f"ch{chapter_no}: age/year mismatch {age}岁 with {years}年前 "
                            f"(expected about {expected_age})"
                        )

    if violations:
        print(f"FAIL {len(violations)} timeline violations:", file=sys.stderr)
        for item in violations[:200]:
            print(f"  {item}", file=sys.stderr)
        return 1
    print("OK all timeline references comply with canon")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
