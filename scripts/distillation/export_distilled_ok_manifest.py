"""Write a JSONL manifest of successfully prepared distillation sources (Phase 1 ``ok``).

Reads ``.distillation_private/corpus_prepare_state.jsonl`` by default and writes
``.distillation_private/reports/distilled_ok_manifest.jsonl`` with one row per
``status == "ok"`` line (path, extension, source_id, chapter_count). Paths may
contain original filenames; keep the report under ``.distillation_private/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()


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
    rows = 0
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
                        "source_id": row.get("source_id"),
                        "chapter_count": row.get("chapter_count"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            rows += 1

    print(json.dumps({"wrote": str(out_path), "ok_rows": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
