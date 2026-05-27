#!/usr/bin/env python3
"""Batch-regenerate chapters through the existing chapter pipeline."""
# ruff: noqa: S603

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys


def parse_chapter_spec(spec: str) -> list[int]:
    chapters: set[int] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start = int(left)
            end = int(right)
            if end < start:
                raise ValueError(f"Invalid chapter range: {part}")
            chapters.update(range(start, end + 1))
        else:
            chapters.add(int(part))
    return sorted(chapters)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--chapters", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output-base-dir", default="output")
    parser.add_argument("--pause-every", type=int, default=0)
    parser.add_argument("--pause-audit-script", default=None)
    parser.add_argument("--chapter-first", action="store_true")
    parser.add_argument(
        "--cli",
        default=None,
        help="CLI executable, defaults to BESTSELLER_CLI or bestseller",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    chapters = parse_chapter_spec(args.chapters)
    project_dir = Path(args.output_base_dir) / args.project
    backup_dir = project_dir / "rejected-drafts" / args.batch_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    log_path = backup_dir / "retrofit.log"

    print(f"Retrofitting {len(chapters)} chapters, batch={args.batch_id}")
    print(f"Reason: {args.reason}")
    print(f"Backup dir: {backup_dir}")
    if args.dry_run:
        print("DRY RUN: backups and pipeline calls are skipped")

    cli = args.cli or os.environ.get("BESTSELLER_CLI") or "bestseller"
    for index, chapter_no in enumerate(chapters, start=1):
        old_md = project_dir / f"chapter-{chapter_no:03d}.md"
        backup_md = backup_dir / f"chapter-{chapter_no:03d}.md"
        if not args.dry_run and old_md.exists():
            shutil.copy2(old_md, backup_md)

        cmd = [
            cli,
            "chapter",
            "pipeline",
            args.project,
            str(chapter_no),
            "--export-markdown",
            "--supersede-pending-rewrites",
            "--requested-by",
            f"retrofit:{args.batch_id}",
        ]
        if args.chapter_first:
            cmd.append("--chapter-first")

        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n=== ch{chapter_no:03d} {datetime.now(UTC).isoformat()} ===\n"
                f"reason={args.reason}\ncmd={' '.join(cmd)}\n"
            )

        if args.dry_run:
            print(f"DRY ch{chapter_no:03d}: {' '.join(cmd)}")
            continue

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(result.stdout)
            handle.write(result.stderr)
        if result.returncode != 0:
            print(f"FAIL ch{chapter_no:03d} retrofit failed, see {log_path}", file=sys.stderr)
            return 1
        print(f"OK ch{chapter_no:03d} retrofitted ({index}/{len(chapters)})")

        if args.pause_every and index % args.pause_every == 0 and args.pause_audit_script:
            audit = subprocess.run(
                [sys.executable, args.pause_audit_script, "--slug", args.project],
                check=False,
            )
            if audit.returncode != 0:
                print("FAIL pause audit failed, stopping batch", file=sys.stderr)
                return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
