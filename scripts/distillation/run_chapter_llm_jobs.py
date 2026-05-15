"""Execute Phase-2 distillation: LLM chapter_card extraction for one source package."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_SRC = _THIS.parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.distillation_chapter_llm import (  # noqa: E402
    append_chapter_card_jsonl,
    chapter_card_keys_missing_craft,
    existing_chapter_card_keys,
    extract_chapter_card_for_job,
    iter_pending_jobs,
    load_chapter_card_schema,
    upsert_chapter_card_jsonl,
)
from bestseller.settings import get_settings  # noqa: E402


async def _run(
    *,
    package_dir: Path,
    repo_root: Path,
    private_root: Path,
    limit: int | None,
    max_chapter_chars: int | None,
    refresh_missing_craft_observations: bool,
) -> int:
    settings = get_settings()
    schema = load_chapter_card_schema(repo_root)
    out_path = package_dir / "chapter_cards.jsonl"
    done_keys = existing_chapter_card_keys(out_path)
    refresh_keys = (
        chapter_card_keys_missing_craft(out_path) if refresh_missing_craft_observations else set()
    )
    failures = 0
    processed = 0

    for job in iter_pending_jobs(
        package_dir,
        existing_keys=done_keys,
        refresh_keys=refresh_keys,
        limit=limit,
    ):
        try:
            async with session_scope(settings) as session:
                row = await extract_chapter_card_for_job(
                    session,
                    settings,
                    repo_root=repo_root,
                    private_root=private_root,
                    job=job,
                    schema=schema,
                    max_chapter_chars=max_chapter_chars,
                )
            key = (str(row.get("source_id") or ""), int(row.get("abs_chapter_no") or 0))
            if key in refresh_keys:
                upsert_chapter_card_jsonl(out_path, row)
                refresh_keys.discard(key)
            else:
                append_chapter_card_jsonl(out_path, row)
            done_keys.add(key)
            processed += 1
            print(
                json.dumps(
                    {"ok": True, "job_id": job.get("job_id"), "abs_chapter_no": job.get("abs_chapter_no")},
                    ensure_ascii=False,
                )
            )
        except Exception as exc:  # noqa: BLE001 — batch driver surfaces per-row errors
            failures += 1
            print(
                json.dumps(
                    {
                        "ok": False,
                        "job_id": job.get("job_id"),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
    print(json.dumps({"processed": processed, "failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-dir",
        type=Path,
        required=True,
        help="Path to data/distillation/source-NNNN",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--private-root", type=Path, default=Path(".distillation_private"))
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of new chapter jobs to process (default: all pending).",
    )
    parser.add_argument(
        "--max-chapter-chars",
        type=int,
        default=None,
        help="Truncate chapter_text in the prompt after N characters (default: no truncation).",
    )
    parser.add_argument(
        "--refresh-missing-craft-observations",
        action="store_true",
        help="Re-run existing chapter cards that lack craft_observations and replace those rows.",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    private_root = (
        (repo_root / args.private_root).resolve()
        if not args.private_root.is_absolute()
        else args.private_root.resolve()
    )
    package_dir = args.package_dir.resolve()
    if not package_dir.is_dir():
        print(f"error: package dir not found: {package_dir}", file=sys.stderr)
        raise SystemExit(2)

    code = asyncio.run(
        _run(
            package_dir=package_dir,
            repo_root=repo_root,
            private_root=private_root,
            limit=args.limit,
            max_chapter_chars=args.max_chapter_chars,
            refresh_missing_craft_observations=args.refresh_missing_craft_observations,
        )
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
