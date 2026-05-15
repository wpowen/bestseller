"""Run Phase-3 single-book distillation aggregation (LLM) for one source package."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS = Path(__file__).resolve()
_SRC = _THIS.parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.distillation_book_aggregator import (  # noqa: E402
    aggregate_source_package_async,
)
from bestseller.settings import get_settings  # noqa: E402


async def _run(
    *,
    package_dir: Path,
    repo_root: Path,
    private_root: Path,
    chapter_batch_size: int | None,
    write_active_artifacts: bool,
) -> int:
    settings = get_settings()
    errors_dir = (private_root / "errors").resolve()
    try:
        async with session_scope(settings) as session:
            result = await aggregate_source_package_async(
                session,
                settings,
                package_dir=package_dir,
                repo_root=repo_root,
                private_errors_dir=errors_dir,
                chapter_batch_size=chapter_batch_size,
                write_active_artifacts=write_active_artifacts,
            )
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        payload = {"package_dir": str(package_dir), "error": f"{type(exc).__name__}: {exc}"}
        errors_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        err_path = errors_dir / f"{package_dir.name}_aggregate_cli_{ts}.json"
        err_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": False, **payload}, ensure_ascii=False), file=sys.stderr)
        return 1


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
        "--chapter-batch-size",
        type=int,
        default=None,
        help="Optional fixed chapter batch size (default: 20–30 adaptive).",
    )
    parser.add_argument(
        "--write-active-artifacts",
        action="store_true",
        help=(
            "Write per-source material_entries.active.jsonl after active gate. "
            "Requires --allow-reviewed-promotion."
        ),
    )
    parser.add_argument(
        "--allow-reviewed-promotion",
        action="store_true",
        help="Required before writing active artifacts from this CLI.",
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
    if args.write_active_artifacts and not args.allow_reviewed_promotion:
        print(
            "error: --write-active-artifacts requires --allow-reviewed-promotion",
            file=sys.stderr,
        )
        raise SystemExit(2)

    code = asyncio.run(
        _run(
            package_dir=package_dir,
            repo_root=repo_root,
            private_root=private_root,
            chapter_batch_size=args.chapter_batch_size,
            write_active_artifacts=args.write_active_artifacts,
        )
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
