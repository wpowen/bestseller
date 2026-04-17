"""Remove duplicated leading chapter headings from existing chapter-NNN.md files.

Scans ``output/<project_slug>/chapter-*.md`` (or the whole ``output/`` tree)
and rewrites files whose first two non-blank lines are both canonical chapter
headings like ``# 第N章：…`` or ``# Chapter N: …``. Leaves anything else
untouched.

Usage::

    .venv/bin/python -m scripts.fix_duplicate_chapter_headings                 # all projects
    .venv/bin/python -m scripts.fix_duplicate_chapter_headings --project-slug female-no-cp-1776303225
    .venv/bin/python -m scripts.fix_duplicate_chapter_headings --dry-run

Idempotent: files that already have a single heading are skipped.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_CHAPTER_HEADING_RE = re.compile(
    r"^#{1,4}\s*(?:第\s*\d+\s*章|Chapter\s+\d+)\b",
    re.IGNORECASE,
)


def _first_two_content_lines(text: str) -> tuple[int, int] | None:
    """Return (idx1, idx2) indices of the first two non-blank lines in text."""
    lines = text.splitlines()
    first = second = None
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        if first is None:
            first = idx
        else:
            second = idx
            break
    if first is None or second is None:
        return None
    return first, second


def _dedup_heading(text: str) -> tuple[str, bool]:
    """If first two content lines are both chapter headings, drop the second
    (and any blank lines directly after it).

    Returns (new_text, was_changed).
    """
    indices = _first_two_content_lines(text)
    if indices is None:
        return text, False
    first, second = indices
    lines = text.splitlines()
    if not _CHAPTER_HEADING_RE.match(lines[first].strip()):
        return text, False
    if not _CHAPTER_HEADING_RE.match(lines[second].strip()):
        return text, False
    end = second + 1
    while end < len(lines) and not lines[end].strip():
        end += 1
    new_lines = lines[:second] + lines[end:]
    new_text = "\n".join(new_lines)
    return new_text, True


def _discover_files(base: Path, project_slug: str | None) -> list[Path]:
    if project_slug:
        return sorted((base / project_slug).glob("chapter-*.md"))
    return sorted(base.glob("*/chapter-*.md"))


def run(base_dir: Path, project_slug: str | None, dry_run: bool) -> None:
    files = _discover_files(base_dir, project_slug)
    if not files:
        print(f"No chapter files found under {base_dir} (slug={project_slug!r}).")
        return
    scanned = 0
    changed = 0
    for path in files:
        scanned += 1
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new_text, was_changed = _dedup_heading(original)
        if not was_changed:
            continue
        changed += 1
        print(f"  fix  {path}")
        if not dry_run:
            path.write_text(new_text, encoding="utf-8")
    print(f"\nScanned {scanned} file(s); {'would fix' if dry_run else 'fixed'} {changed}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", default="output", help="Output root (default: output)")
    parser.add_argument("--project-slug", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(Path(args.base_dir).resolve(), args.project_slug, args.dry_run)


if __name__ == "__main__":
    main()
