#!/usr/bin/env python
"""Replace known deprecated material references with canonical active names."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import shutil
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.material_entity_registry import build_entity_registry  # noqa: E402
from bestseller.services.material_reference_scanner import scan_material_references  # noqa: E402

DEFAULT_REPLACEMENTS = {
    "林逸": "林渊",
    "裴镜渊": "镜影林渊",
}


def main() -> int:
    args = _parse_args()
    project_dir = args.project_dir.resolve()
    registry = build_entity_registry(project_dir)
    problems = scan_material_references(project_dir, registry)
    replacements = dict(DEFAULT_REPLACEMENTS)
    replacements.update(_parse_replacements(args.replace))

    changed: dict[Path, list[tuple[str, str]]] = {}
    review: list[str] = []
    for problem in problems:
        if problem.problem != "deprecated":
            continue
        replacement = replacements.get(problem.referenced_name)
        if not replacement:
            review.append(f"{problem.file}:{problem.line_no} {problem.referenced_name}")
            continue
        path = project_dir / problem.file
        changed.setdefault(path, []).append((problem.referenced_name, replacement))

    archive_dir = project_dir / "_archive" / args.archive_name
    for path, replacements_for_file in changed.items():
        text = path.read_text(encoding="utf-8")
        next_text = text
        for old, new in replacements_for_file:
            next_text = next_text.replace(old, new)
        if next_text == text:
            continue
        print(f"{'DRY-RUN' if args.dry_run else 'FIX'} {path.relative_to(project_dir)}")
        for old, new in sorted(set(replacements_for_file)):
            print(f"  {old} -> {new}")
        if not args.dry_run:
            backup = archive_dir / path.relative_to(project_dir)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            path.write_text(next_text, encoding="utf-8")

    if review:
        print("\nNeeds manual review:")
        for item in review:
            print(f"- {item}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument(
        "--replace",
        action="append",
        default=[],
        help="Extra OLD=NEW replacement. Can be passed more than once.",
    )
    parser.add_argument(
        "--archive-name",
        default=f"{datetime.now().date().isoformat()}-rebuild",
    )
    parser.add_argument("--apply", dest="dry_run", action="store_false")
    parser.set_defaults(dry_run=True)
    return parser.parse_args()


def _parse_replacements(values: list[str]) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Invalid --replace value {value!r}; expected OLD=NEW")
        old, new = value.split("=", 1)
        replacements[old.strip()] = new.strip()
    return replacements


if __name__ == "__main__":
    raise SystemExit(main())
