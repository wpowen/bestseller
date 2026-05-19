"""Bootstrap legacy books into the premium state loop.

This script is for historical DB-backed books whose prose and story-bible
assets already exist, but whose project metadata predates the current premium
state snapshot / category hard-engine contracts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
for item in (_SRC,):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.autonomous_book_repair import discover_output_book_slugs  # noqa: E402
from bestseller.services.legacy_book_state_bootstrap import (  # noqa: E402
    bootstrap_legacy_project_state,
)
from bestseller.services.projects import get_project_by_slug  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--slug", help="One output/<slug> book to bootstrap.")
    target.add_argument("--all", action="store_true", help="Process all output dirs with chapters.")
    parser.add_argument(
        "--category-key",
        default=None,
        help="Override the resolved category contract.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build report without mutating DB metadata.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    return parser.parse_args()


async def _run_for_slug(
    slug: str,
    *,
    category_key: str | None,
    dry_run: bool,
) -> dict[str, object]:
    settings = load_settings()
    package_dir = Path(settings.output.base_dir) / slug
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return {"slug": slug, "status": "skipped", "error": "project_not_found_in_db"}
        report = await bootstrap_legacy_project_state(
            session,
            project,
            package_dir=package_dir,
            explicit_category_key=category_key,
            dry_run=dry_run,
        )
        out_dir = package_dir / "audits" / "legacy-state-bootstrap"
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / "report.json"
        report_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {**report.to_dict(), "report_path": str(report_path)}


def main() -> int:
    args = _parse_args()
    slugs = (
        discover_output_book_slugs(_REPO_ROOT / "output")
        if args.all
        else [str(args.slug)]
    )
    if not slugs:
        print("No books found.")
        return 1

    async def _run_all() -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for slug in slugs:
            try:
                results.append(
                    await _run_for_slug(
                        slug,
                        category_key=args.category_key,
                        dry_run=args.dry_run,
                    )
                )
            except Exception as exc:
                results.append(
                    {
                        "slug": slug,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return results

    results = asyncio.run(_run_all())
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            status = result.get("status", "unknown")
            slug = result.get("slug", "-")
            if "error" in result:
                print(f"{slug}: {status} {result['error']}")
                continue
            print(
                (
                    "{slug}: {status} category={category} "
                    "premium_before={before} premium_after={after}"
                ).format(
                    slug=slug,
                    status=status,
                    category=result.get("category_key") or "-",
                    before=result.get("premium_gate_before_passed"),
                    after=result.get("premium_gate_after_passed"),
                )
            )
            if result.get("report_path"):
                print(f"  report: {result['report_path']}")
    return 1 if any(result.get("status") == "error" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
