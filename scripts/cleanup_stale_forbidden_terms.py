#!/usr/bin/env python
"""Report or remove stale forbidden terms from a project forbidden policy."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import shutil
import sys
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


CROSS_BOOK_TERMS = {"守夜人", "北马", "陈守正", "周德昌", "张德福"}


def main() -> int:
    args = _parse_args()
    policy_path = args.policy or _default_policy(args.project_dir)
    payload = _read_yaml(policy_path)
    terms = _collect_terms(payload)
    recent_text = _recent_rejected_text(args.project_dir, args.window)
    stale = [term for term in terms if term not in recent_text]
    deprecated = sorted(
        (set(stale) & CROSS_BOOK_TERMS)
        | set(_list(payload.get("deprecated_should_remove")))
    )

    print(f"policy={policy_path}")
    print(f"terms={len(terms)} stale={len(stale)} cross_book_or_deprecated={len(deprecated)}")
    for term in stale:
        marker = " deprecated" if term in deprecated else ""
        print(f"- {term}{marker}")

    if args.dry_run:
        return 0
    backup = (
        args.project_dir
        / "_archive"
        / args.archive_name
        / policy_path.relative_to(args.project_dir)
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(policy_path, backup)
    payload["deprecated_should_remove"] = sorted(
        set(_list(payload.get("deprecated_should_remove"))) | set(deprecated)
    )
    _write_yaml(policy_path, payload)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--archive-name", default=f"{datetime.now().date().isoformat()}-rebuild")
    parser.add_argument("--apply", dest="dry_run", action="store_false")
    parser.set_defaults(dry_run=True)
    return parser.parse_args()


def _default_policy(project_dir: Path) -> Path:
    candidates = sorted((project_dir / "story-bible").glob("forbidden*policy.y*ml"))
    if not candidates:
        raise SystemExit("No forbidden policy found.")
    return candidates[0]


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _collect_terms(payload: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    terms.extend(_list(payload.get("permanent_forbidden")))
    terms.extend(_list(payload.get("deprecated_should_remove")))
    for item in payload.get("staged_forbidden") or []:
        if isinstance(item, dict):
            terms.extend(_list(item.get("terms")))
    return sorted(set(terms))


def _recent_rejected_text(project_dir: Path, window: int) -> str:
    candidates = sorted(
        project_dir.rglob("*rejected*draft*"),
        key=lambda path: path.stat().st_mtime,
    )
    texts: list[str] = []
    for path in candidates[-window:]:
        if path.is_file() and path.stat().st_size < 2_000_000:
            texts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(texts)


def _list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


if __name__ == "__main__":
    raise SystemExit(main())
