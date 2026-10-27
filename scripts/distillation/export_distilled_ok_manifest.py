"""Write a JSONL manifest of sources whose book phase is complete.

Reads ``.distillation_private/corpus_prepare_state.jsonl`` by default and writes
Preparation ``status == "ok"`` is necessary but is not sufficient. A row is
exported only when its anonymized package passes
``package_book_phase_complete``; fallback artifacts therefore stay resumable
and are not reported as distilled success.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_SRC = _THIS.parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.distillation_book_aggregator import (
    package_book_phase_complete,
    package_book_phase_structurally_complete,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(".distillation_private/corpus_prepare_state.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".distillation_private/reports/distilled_ok_manifest.jsonl"),
    )
    parser.add_argument(
        "--packages-root",
        type=Path,
        default=Path("data/distillation"),
        help="Root containing source package directories (default: data/distillation).",
    )
    parser.add_argument(
        "--exclude-extensions",
        default="",
        help="Comma-separated suffixes to skip, e.g. `mobi,azw3` (for manifests of non-Kindle files only).",
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    state_path = (
        (repo_root / args.state_file).resolve()
        if not args.state_file.is_absolute()
        else args.state_file.resolve()
    )
    out_path = (
        (repo_root / args.output).resolve() if not args.output.is_absolute() else args.output.resolve()
    )
    if not state_path.is_file():
        print(f"error: state file not found: {state_path}", file=sys.stderr)
        raise SystemExit(2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    exclude = {
        x.strip().lower().removeprefix(".")
        for x in (args.exclude_extensions or "").split(",")
        if x.strip()
    }
    prepare_ok_rows = 0
    rows = 0
    structural_rows = 0
    seen_source_ids: set[str] = set()
    packages_root = (
        (repo_root / args.packages_root).resolve()
        if not args.packages_root.is_absolute()
        else args.packages_root.resolve()
    )
    with out_path.open("w", encoding="utf-8") as out:
        for line in state_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") != "ok":
                continue
            prepare_ok_rows += 1
            source_id = row.get("source_id")
            if not isinstance(source_id, str) or not source_id or source_id in seen_source_ids:
                continue
            seen_source_ids.add(source_id)
            package_dir = packages_root / str(source_id) if source_id else None
            if package_dir is None or not package_book_phase_structurally_complete(package_dir):
                continue
            structural_rows += 1
            if not package_book_phase_complete(package_dir):
                continue
            book_path = row.get("book_path")
            if not isinstance(book_path, str):
                continue
            p = Path(book_path)
            ext = p.suffix.lower().removeprefix(".") or None
            if ext and ext in exclude:
                continue
            out.write(
                json.dumps(
                    {
                        "book_path": book_path,
                        "extension": ext,
                        "source_id": source_id,
                        "chapter_count": row.get("chapter_count"),
                        "status": "book_phase_complete",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            rows += 1

    print(
        json.dumps(
            {
                "wrote": str(out_path),
                "prepare_ok_rows": prepare_ok_rows,
                "structurally_complete_rows": structural_rows,
                "trusted_distilled_rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
