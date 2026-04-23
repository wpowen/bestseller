"""Admin CLI for the multi-dimensional material library.

This is the manual escape hatch for the :mod:`bestseller.services.library_curator`
module.  Use it to:

* Audit the library and print which ``(dimension, genre)`` buckets are
  under-filled (default behaviour; no DB writes, no LLM calls).
* Fill specific gaps on demand (``--fill``) — scope can be narrowed by
  ``--dimension``, ``--genre``, ``--sub-genre`` flags.
* Pre-warm the library across every seed genre the plan knows about
  (``--fill --all-genres``) — typically used once before enabling the
  ``enable_material_library`` feature flag to avoid cold-start.

Usage
-----

    # Default: audit only, human-readable table
    python scripts/curate_library.py

    # Audit + fill one specific bucket
    python scripts/curate_library.py \\
        --dimension power_systems --genre 仙侠 --fill

    # Pre-warm the library for every (dimension, genre) in the plan
    python scripts/curate_library.py --fill --all-genres

    # Cap LLM spend
    python scripts/curate_library.py --fill --max-gaps 3 --max-fills-per-run 4

    # JSON output (for piping into jq / dashboards)
    python scripts/curate_library.py --format json

Safety / invariants
-------------------
* **Audit-only is the default.**  Passing ``--fill`` is the only way to
  trigger Research Agent runs, which consume LLM budget.
* **Per-sweep budget caps.**  ``--max-gaps`` and ``--max-fills-per-run``
  bound how much LLM work can happen per invocation.
* **Search client auto-build.**  If neither ``TAVILY_API_KEY`` nor
  ``SERPER_API_KEY`` is set, :func:`build_search_client` returns a
  :class:`NoopSearchClient` — fills will still run but will emit far
  less because the agent has no web-search tool.
* **Commits per sweep.**  The script commits the transaction once after
  the full sweep (all fills) succeeds — individual fill failures are
  logged but don't abort the sweep.

Output
------

Human-readable table (default) or JSON (``--format json``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Path shim — scripts/ isn't a package; mirror the pattern used by other scripts.
_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.library_curator import (  # noqa: E402
    CURATOR_COVERAGE_PLAN,
    CoverageTarget,
    CurationReport,
    _MATERIAL_DIMENSIONS,
    _default_coverage_plan,
    run_curation,
)
from bestseller.services.search_client import build_search_client  # noqa: E402
from bestseller.settings import get_settings  # noqa: E402

logger = logging.getLogger("curate_library")


# ── Plan filtering ─────────────────────────────────────────────────────


def _build_plan_from_flags(
    *,
    dimension: str | None,
    genre: str | None,
    sub_genre: str | None,
    min_entries: int,
    all_genres: bool,
) -> tuple[CoverageTarget, ...]:
    """Produce the coverage plan scoped by CLI flags.

    * If ``--all-genres`` is passed we use the default plan (genre
      baselines + 3 seed genres) intersected with any dimension/genre/
      sub-genre filters.
    * Otherwise we build a single target from the flags — the degenerate
      case when the operator wants to top-up one bucket.
    """

    if all_genres:
        plan = list(_default_coverage_plan(min_entries=min_entries))
    else:
        if dimension is None and genre is None:
            # No scope + no --all-genres: audit default module plan.
            plan = list(CURATOR_COVERAGE_PLAN)
        elif dimension is None:
            # Genre-wide: every dimension for this genre.
            plan = [
                CoverageTarget(
                    dimension=dim,
                    genre=genre,
                    sub_genre=sub_genre,
                    min_entries=min_entries,
                )
                for dim in _MATERIAL_DIMENSIONS
            ]
        elif genre is None:
            # Dimension-wide, genre-agnostic: just the generic baseline.
            plan = [
                CoverageTarget(
                    dimension=dimension,
                    genre=None,
                    sub_genre=None,
                    min_entries=min_entries,
                )
            ]
        else:
            plan = [
                CoverageTarget(
                    dimension=dimension,
                    genre=genre,
                    sub_genre=sub_genre,
                    min_entries=min_entries,
                )
            ]

    if dimension is not None:
        plan = [t for t in plan if t.dimension == dimension]
    if genre is not None:
        plan = [t for t in plan if t.genre == genre]
    if sub_genre is not None:
        plan = [t for t in plan if t.sub_genre == sub_genre]

    if min_entries != CURATOR_COVERAGE_PLAN[0].min_entries:
        # Override min_entries on every target to honour the CLI flag.
        plan = [
            CoverageTarget(
                dimension=t.dimension,
                genre=t.genre,
                sub_genre=t.sub_genre,
                min_entries=min_entries,
            )
            for t in plan
        ]

    return tuple(plan)


# ── Reporting ──────────────────────────────────────────────────────────


def _report_as_rows(report: CurationReport) -> list[dict[str, Any]]:
    """Flatten the CurationReport into a row-per-target summary."""

    gap_by_key: dict[tuple[str, str | None, str | None], Any] = {
        (g.dimension, g.genre, g.sub_genre): g for g in report.audit.gaps
    }
    fill_by_key: dict[tuple[str, str | None, str | None], Any] = {
        (f.gap.dimension, f.gap.genre, f.gap.sub_genre): f for f in report.fills
    }

    rows: list[dict[str, Any]] = []
    for sat in report.audit.satisfied:
        rows.append(
            {
                "dimension": sat.dimension,
                "genre": sat.genre,
                "sub_genre": sat.sub_genre,
                "status": "satisfied",
                "active_count": sat.active_count,
                "min_required": sat.min_required,
                "emitted": 0,
                "rejected_taboos": 0,
            }
        )
    for key, gap in gap_by_key.items():
        fill = fill_by_key.get(key)
        rows.append(
            {
                "dimension": gap.dimension,
                "genre": gap.genre,
                "sub_genre": gap.sub_genre,
                "status": "filled" if fill else "gap",
                "active_count": gap.report.active_count,
                "min_required": gap.report.min_required,
                "emitted": fill.emitted_count if fill else 0,
                "rejected_taboos": len(fill.rejected_taboos) if fill else 0,
            }
        )
    return rows


def _print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("(plan was empty — nothing to audit)")
        return
    headers = [
        "dimension",
        "genre",
        "sub_genre",
        "status",
        "active_count",
        "min_required",
        "emitted",
        "rejected_taboos",
    ]
    widths = {h: max(len(h), max(len(str(r.get(h, ""))) for r in rows)) for h in headers}
    header_line = "  ".join(h.ljust(widths[h]) for h in headers)
    sep = "  ".join("-" * widths[h] for h in headers)
    print(header_line)
    print(sep)
    for row in rows:
        print("  ".join(str(row.get(h, "")).ljust(widths[h]) for h in headers))
    total_emitted = sum(int(r["emitted"]) for r in rows)
    total_rejected = sum(int(r["rejected_taboos"]) for r in rows)
    gaps = sum(1 for r in rows if r["status"] in {"gap", "filled"})
    print()
    print(
        f"Summary: {len(rows)} targets, {gaps} gaps, "
        f"{total_emitted} entries emitted, {total_rejected} taboo rejects."
    )


def _print_json(report: CurationReport) -> None:
    payload = {
        "targets_checked": report.audit.targets_checked,
        "gaps": [
            {
                "dimension": g.dimension,
                "genre": g.genre,
                "sub_genre": g.sub_genre,
                "active_count": g.report.active_count,
                "min_required": g.report.min_required,
            }
            for g in report.audit.gaps
        ],
        "satisfied": [
            {
                "dimension": s.dimension,
                "genre": s.genre,
                "sub_genre": s.sub_genre,
                "active_count": s.active_count,
                "min_required": s.min_required,
            }
            for s in report.audit.satisfied
        ],
        "fills": [
            {
                "dimension": f.gap.dimension,
                "genre": f.gap.genre,
                "sub_genre": f.gap.sub_genre,
                "emitted": f.emitted_count,
                "rejected_taboos": [
                    {"slug": slug, "pattern": pattern}
                    for slug, pattern in f.rejected_taboos
                ],
                "exit_reason": f.exit_reason,
            }
            for f in report.fills
        ],
        "total_emitted": report.total_emitted,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# ── Main ───────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit + (optionally) fill the multi-dimensional material library.",
    )
    parser.add_argument(
        "--dimension",
        choices=list(_MATERIAL_DIMENSIONS),
        help="Scope the audit/fill to a single dimension.",
    )
    parser.add_argument(
        "--genre",
        help="Scope to a single genre (e.g. '仙侠', '都市修仙', '科幻').",
    )
    parser.add_argument(
        "--sub-genre",
        dest="sub_genre",
        help="Scope to a sub-genre (e.g. 'upgrade').",
    )
    parser.add_argument(
        "--all-genres",
        action="store_true",
        help="Use the default plan — every dimension × every seed genre.",
    )
    parser.add_argument(
        "--min-entries",
        type=int,
        default=10,
        help="Minimum entries per (dimension, genre) bucket. Default: 10.",
    )
    parser.add_argument(
        "--fill",
        action="store_true",
        help="Invoke the Research Agent to fill the gaps. Without this flag only audit runs.",
    )
    parser.add_argument(
        "--max-gaps",
        type=int,
        default=None,
        help="Cap on how many gaps to fill this sweep.",
    )
    parser.add_argument(
        "--max-fills-per-run",
        type=int,
        default=None,
        help="Cap on per-gap entries emitted.",
    )
    parser.add_argument(
        "--ttl-days",
        type=int,
        default=None,
        help="Consider entries older than this many days as 'stale' when auditing.",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format. Default: table.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging (verbose LLM + HTTP traces).",
    )
    return parser.parse_args(argv)


async def _async_main(args: argparse.Namespace) -> int:
    plan = _build_plan_from_flags(
        dimension=args.dimension,
        genre=args.genre,
        sub_genre=args.sub_genre,
        min_entries=args.min_entries,
        all_genres=args.all_genres,
    )
    if not plan:
        logger.error(
            "curate_library: resolved plan is empty — check your --dimension/--genre flags."
        )
        return 2

    settings = get_settings()
    search_client = build_search_client(env=dict(os.environ))
    try:
        async with session_scope(settings) as session:
            report = await run_curation(
                session,
                settings,
                plan=plan,
                search_client=search_client,
                fill=args.fill,
                max_gaps=args.max_gaps,
                max_fills_per_run=args.max_fills_per_run,
                ttl_days=args.ttl_days,
            )
    finally:
        try:
            await search_client.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("search_client.close() raised: %s", exc)

    if args.format == "json":
        _print_json(report)
    else:
        _print_table(_report_as_rows(report))

    # Exit non-zero only if there are still unfilled gaps after a fill
    # run — makes it easy to wire into a CI/pre-flight check.
    unfilled = len(report.audit.gaps) - len(report.fills)
    if args.fill and unfilled > 0:
        logger.warning("curate_library: %d gaps remain unfilled after sweep.", unfilled)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
