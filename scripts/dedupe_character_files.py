#!/usr/bin/env python
"""Archive duplicate Obsidian character files after merging useful text."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import shutil
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.material_entity_registry import canonical_character_name  # noqa: E402


def main() -> int:
    args = _parse_args()
    people_dir = args.project_dir / "obsidian-vault" / "人物"
    if not people_dir.exists():
        raise SystemExit(f"Missing people dir: {people_dir}")
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(people_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        groups[canonical_character_name(path.stem)].append(path)

    archive_root = args.project_dir / "_archive" / f"dedupe-{args.archive_name}"
    for canonical, paths in sorted(groups.items()):
        if len(paths) < 2:
            continue
        primary = _choose_primary(canonical, paths)
        duplicates = [path for path in paths if path != primary]
        print(f"{'DRY-RUN' if args.dry_run else 'DEDUP'} {canonical}: keep {primary.name}")
        for duplicate in duplicates:
            print(f"  archive {duplicate.name}")
        if args.dry_run:
            continue
        primary_text = primary.read_text(encoding="utf-8", errors="ignore")
        merged = _merge_duplicate_notes(primary_text, duplicates)
        primary.write_text(merged, encoding="utf-8")
        for duplicate in duplicates:
            target = archive_root / "obsidian-vault" / "人物" / duplicate.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(duplicate), str(target))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--archive-name", default=datetime.now().date().isoformat())
    parser.add_argument("--apply", dest="dry_run", action="store_false")
    parser.set_defaults(dry_run=True)
    return parser.parse_args()


def _choose_primary(canonical: str, paths: list[Path]) -> Path:
    for path in paths:
        if path.stem == canonical:
            return path
    return sorted(paths, key=lambda path: (len(path.name), path.name))[0]


def _merge_duplicate_notes(primary_text: str, duplicates: list[Path]) -> str:
    sections: list[str] = []
    for duplicate in duplicates:
        text = duplicate.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            continue
        sections.append(f"### {duplicate.name}\n\n{text}")
    if not sections:
        return primary_text
    if "## 合并来源" in primary_text:
        return primary_text.rstrip() + "\n\n" + "\n\n".join(sections) + "\n"
    return primary_text.rstrip() + "\n\n## 合并来源\n\n" + "\n\n".join(sections) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
