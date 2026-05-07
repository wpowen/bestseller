"""Backfill death-ripple records for historically-deceased characters.

Why this exists
---------------
The ``death_ripple`` service was added after many projects had already
recorded deaths via ``feedback.extract_chapter_feedback``.  Those legacy
deaths never triggered ``apply_death_ripple``, which means:

* Survivors have no "grieving" snapshot anchored to the death chapter.
* The RelationshipModel rows for the deceased still show as active.
* The interpersonal-promise ledger was never rolled up (lapsed / inherited).

This script closes that gap: for each project, find every deceased
character with a ``death_chapter_number``, check whether the ripple has
already been applied (idempotency), and apply it when missing.

Usage
-----
    # dry-run across all projects (no DB writes)
    python scripts/backfill_death_ripples.py --dry-run

    # dry-run for one project
    python scripts/backfill_death_ripples.py --dry-run --project-slug my-novel-slug

    # apply (writes ripples and commits)
    python scripts/backfill_death_ripples.py --apply

    # apply for one project
    python scripts/backfill_death_ripples.py --apply --project-slug my-novel-slug

Safety
------
* Dry-run (default): scans and prints what WOULD be done — no DB writes.
* ``--apply``: writes, flushes, and commits once per project.
* Idempotent: ``apply_death_ripple`` already checks for prior bereavement
  events / grief snapshots, so re-running ``--apply`` is safe.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select  # noqa: E402

from bestseller.infra.db.database import get_async_session  # noqa: E402
from bestseller.infra.db.models import CharacterModel, ProjectModel  # noqa: E402
from bestseller.services.death_ripple import apply_death_ripple  # noqa: E402


async def _backfill_project(
    project: ProjectModel,
    *,
    apply: bool,
    verbose: bool,
) -> dict[str, int]:
    """Process all deceased characters in one project.

    Returns a summary dict: {"total_deceased", "ripple_applied", "skipped"}.
    """
    from bestseller.infra.db.models import RelationshipEventModel  # noqa: PLC0415

    summary = {"total_deceased": 0, "ripple_applied": 0, "skipped": 0}

    async with get_async_session() as session:
        # Load all deceased characters for this project.
        deceased_rows = list(
            await session.scalars(
                select(CharacterModel).where(
                    CharacterModel.project_id == project.id,
                    CharacterModel.alive_status == "deceased",
                    CharacterModel.death_chapter_number.isnot(None),
                )
            )
        )
        summary["total_deceased"] = len(deceased_rows)

        for deceased in deceased_rows:
            ch = int(deceased.death_chapter_number)  # type: ignore[arg-type]

            # Quick idempotency check: has at least one "ended_by_death" event
            # been written for this character?  (``apply_death_ripple`` also
            # checks per-pair, but this saves us fetching all relationships
            # when the character was already fully processed.)
            existing_event = await session.scalar(
                select(RelationshipEventModel).where(
                    RelationshipEventModel.project_id == project.id,
                    RelationshipEventModel.chapter_number == ch,
                    RelationshipEventModel.relationship_change == "ended_by_death",
                    (
                        (RelationshipEventModel.character_a_label == deceased.name)
                        | (RelationshipEventModel.character_b_label == deceased.name)
                    ),
                ).limit(1)
            )

            if existing_event is not None:
                if verbose:
                    print(
                        f"  [skip] {deceased.name} (ch{ch}) — "
                        "bereavement events already exist"
                    )
                summary["skipped"] += 1
                continue

            if not apply:
                # Dry-run: just print what we would do.
                print(
                    f"  [dry-run] would apply death ripple for "
                    f"{deceased.name!r} (ch{ch})"
                )
                summary["ripple_applied"] += 1
                continue

            # --- APPLY ---
            try:
                report = await apply_death_ripple(
                    session,
                    project_id=project.id,
                    deceased=deceased,
                    chapter_number=ch,
                )
                if verbose:
                    print(
                        f"  [applied] {deceased.name} (ch{ch}) → "
                        f"{report.grief_count} grief, "
                        f"{report.vengeance_closure_count} closure ripples"
                    )
                summary["ripple_applied"] += 1
            except Exception as exc:
                print(
                    f"  [ERROR] {deceased.name} (ch{ch}): {exc!r}",
                    file=sys.stderr,
                )

        if apply and summary["ripple_applied"] > 0:
            await session.commit()

    return summary


async def _run(
    *,
    project_slug: str | None,
    apply: bool,
    verbose: bool,
) -> None:
    from bestseller.infra.db.database import get_async_session  # noqa: PLC0415

    async with get_async_session() as session:
        stmt = select(ProjectModel)
        if project_slug:
            stmt = stmt.where(ProjectModel.slug == project_slug)
        projects = list(await session.scalars(stmt))

    if not projects:
        print("No projects found matching the filter.", file=sys.stderr)
        sys.exit(1)

    mode = "APPLY" if apply else "DRY-RUN"
    print(
        f"=== backfill_death_ripples [{mode}] — "
        f"{len(projects)} project(s) ===\n"
    )

    total_deaths = 0
    total_applied = 0
    total_skipped = 0

    for project in projects:
        print(f"Project: {project.slug} ({project.id})")
        summary = await _backfill_project(project, apply=apply, verbose=verbose)
        d = summary["total_deceased"]
        a = summary["ripple_applied"]
        s = summary["skipped"]
        status = "applied" if apply else "would apply"
        print(f"  {d} deceased | {a} {status} | {s} skipped\n")
        total_deaths += d
        total_applied += a
        total_skipped += s

    print(
        f"=== TOTAL: {total_deaths} deceased, "
        f"{total_applied} ripples {status}, "
        f"{total_skipped} already done ===\n"
    )
    if not apply:
        print(
            "Run with --apply to write the ripples to the database.\n"
            "The operation is idempotent — safe to re-run."
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be done (default safe mode — no DB writes).",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Write ripple records to the database and commit.",
    )
    parser.add_argument(
        "--project-slug",
        metavar="SLUG",
        default=None,
        help="Restrict to one project identified by its slug.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print a line per character even when skipping.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(
        _run(
            project_slug=args.project_slug,
            apply=args.apply,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
