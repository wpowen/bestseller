"""Backfill character lifecycle metadata onto historical chapters.

Why this exists
---------------
Migration 0020 added ``alive_status`` / ``stance`` / ``power_tier`` columns to
``characters`` and ``character_state_snapshots``.  Live writing (chapter
pipeline) now populates them correctly via the post-chapter feedback
extraction, but historical chapters written before the migration have those
columns left at the server default (``alive='alive'``, ``stance=NULL``,
``power_tier`` as whatever drifted in from the old extraction path).

This script replays ``extract_chapter_feedback`` on every finalised historical
chapter so the lifecycle columns reflect what actually happened in the prose.
It does **not** rewrite the prose — only the metadata tables
(``character_state_snapshots`` + targeted ``characters`` column updates) are
touched.

Usage
-----
    # Dry-run on a single project (default — no DB writes)
    python scripts/backfill_character_lifecycle.py \\
        --project-slug xianxia-upgrade

    # Apply to one project, resume-safe (skips chapters with snapshots)
    python scripts/backfill_character_lifecycle.py \\
        --project-slug xianxia-upgrade --apply

    # Apply to every historical project (excludes projects still planning)
    python scripts/backfill_character_lifecycle.py --apply --all

    # Cap the number of chapters per project (useful for smoke tests)
    python scripts/backfill_character_lifecycle.py \\
        --project-slug xianxia-upgrade --apply --max-chapters 3

    # Force-replay chapters even if snapshots already exist (creates
    # additional snapshot rows — rare; used for experiment reruns)
    python scripts/backfill_character_lifecycle.py \\
        --project-slug xianxia-upgrade --apply --force

Safety / invariants
-------------------
* **Idempotent by default.**  A chapter is considered "already backfilled"
  when ``character_state_snapshots`` has at least one row for that chapter
  *and* at least one of those rows has a non-null ``alive_status`` or
  ``stance`` (i.e. a row produced by the new extraction schema).  Without
  ``--force`` those chapters are skipped.

* **Dry-run is the default.**  Without ``--apply`` no commits happen — the
  script enters a nested savepoint per chapter and rolls it back.

* **Per-chapter checkpoint commits.**  With ``--apply`` each chapter's
  extraction commits in its own transaction so a crash mid-book does not
  lose earlier chapters.

* **Per-project cost cap.**  ``--max-chapters`` bounds spend; combined with
  the dry-run default this is hard to misfire.

Output
------
CSV to stdout (and optionally ``--csv PATH``):

    project_slug, chapter_number, status, characters_updated, snapshots_created, notes
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Path shim — ``scripts/`` isn't a package; mirror replay_scorecard_phase3.py.
_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from bestseller.settings import get_settings  # noqa: E402
from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterStateSnapshotModel,
    ProjectModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.feedback import extract_chapter_feedback  # noqa: E402

logger = logging.getLogger("backfill_character_lifecycle")

# Chapter statuses considered "final enough" to backfill against.
# A chapter in revision/in_progress may have its content_md churn, so we
# skip those — only chapters that have at least reached ``written`` are
# considered stable.
FINAL_ENOUGH_STATUSES = {"written", "reviewed", "finalized", "passed", "completed", "revision"}


@dataclass(frozen=True)
class BackfillRow:
    project_slug: str
    chapter_number: int
    status: str
    characters_updated: int
    snapshots_created: int
    notes: str


@dataclass
class BackfillSummary:
    rows: list[BackfillRow] = field(default_factory=list)
    skipped_already_done: int = 0
    skipped_no_content: int = 0
    errored: int = 0


async def _chapter_has_new_schema_snapshots(
    session: AsyncSession, chapter_id: Any
) -> bool:
    """Return True when the chapter already has snapshots populated by the
    post-migration schema (at least one with ``alive_status`` or ``stance``
    set)."""

    stmt = select(func.count()).select_from(CharacterStateSnapshotModel).where(
        CharacterStateSnapshotModel.chapter_id == chapter_id,
        (
            CharacterStateSnapshotModel.alive_status.is_not(None)
            | CharacterStateSnapshotModel.stance.is_not(None)
        ),
    )
    count = await session.scalar(stmt)
    return bool(count and count > 0)


async def _load_chapter_md(session: AsyncSession, chapter_id: Any) -> str | None:
    """Return the current chapter draft markdown (or ``None`` if absent)."""

    stmt = (
        select(ChapterDraftVersionModel.content_md)
        .where(
            ChapterDraftVersionModel.chapter_id == chapter_id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .limit(1)
    )
    return await session.scalar(stmt)


async def _backfill_one_chapter(
    session: AsyncSession,
    settings: Any,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    force: bool,
) -> BackfillRow:
    # Idempotency gate
    if not force and await _chapter_has_new_schema_snapshots(session, chapter.id):
        return BackfillRow(
            project_slug=project.slug,
            chapter_number=chapter.chapter_number,
            status="skip-already-done",
            characters_updated=0,
            snapshots_created=0,
            notes="",
        )

    chapter_md = await _load_chapter_md(session, chapter.id)
    if not chapter_md:
        return BackfillRow(
            project_slug=project.slug,
            chapter_number=chapter.chapter_number,
            status="skip-no-content",
            characters_updated=0,
            snapshots_created=0,
            notes="no current chapter_draft_versions row",
        )

    # Snapshot count before so we can diff.
    before_stmt = select(func.count()).select_from(CharacterStateSnapshotModel).where(
        CharacterStateSnapshotModel.chapter_id == chapter.id
    )
    before_count = int(await session.scalar(before_stmt) or 0)

    try:
        result = await extract_chapter_feedback(
            session,
            settings,
            project_id=project.id,
            chapter=chapter,
            chapter_md=chapter_md,
        )
    except Exception as exc:  # noqa: BLE001 — we want script to keep going
        logger.exception(
            "extract_chapter_feedback failed for %s ch=%d",
            project.slug,
            chapter.chapter_number,
        )
        return BackfillRow(
            project_slug=project.slug,
            chapter_number=chapter.chapter_number,
            status="error",
            characters_updated=0,
            snapshots_created=0,
            notes=f"{type(exc).__name__}: {exc}"[:240],
        )

    await session.flush()
    after_count = int(await session.scalar(before_stmt) or 0)
    snapshots_created = max(0, after_count - before_count)

    characters_updated = len(getattr(result, "character_updates", []) or [])
    notes_parts: list[str] = []
    deceased = getattr(result, "deaths_recorded", None)
    if deceased:
        notes_parts.append(f"deaths={','.join(deceased) if isinstance(deceased, list) else deceased}")
    stance_changes = getattr(result, "stance_changes", None)
    if stance_changes:
        notes_parts.append(f"stance_changes={len(stance_changes)}")

    return BackfillRow(
        project_slug=project.slug,
        chapter_number=chapter.chapter_number,
        status="applied",
        characters_updated=characters_updated,
        snapshots_created=snapshots_created,
        notes=" ".join(notes_parts),
    )


async def _select_projects(
    session: AsyncSession,
    *,
    slug: str | None,
    include_all: bool,
) -> list[ProjectModel]:
    if slug:
        stmt = select(ProjectModel).where(ProjectModel.slug == slug)
    elif include_all:
        stmt = select(ProjectModel).order_by(ProjectModel.created_at.asc())
    else:
        # Default: writing / completed projects (skip pure planning shells).
        stmt = (
            select(ProjectModel)
            .where(ProjectModel.status.in_(("writing", "completed")))
            .order_by(ProjectModel.created_at.asc())
        )
    return list((await session.execute(stmt)).scalars())


async def _backfill_project(
    project_slug: str,
    *,
    apply: bool,
    force: bool,
    max_chapters: int | None,
    slug_filter: str | None,
    include_all: bool,
) -> BackfillSummary:
    settings = get_settings()
    summary = BackfillSummary()

    async with session_scope(settings) as session:
        project = (
            await session.execute(
                select(ProjectModel).where(ProjectModel.slug == project_slug)
            )
        ).scalar_one_or_none()
        if project is None:
            logger.warning("project %s not found; skipping", project_slug)
            return summary

        chapters_stmt = (
            select(ChapterModel)
            .where(
                ChapterModel.project_id == project.id,
                ChapterModel.status.in_(FINAL_ENOUGH_STATUSES),
            )
            .order_by(ChapterModel.chapter_number.asc())
        )
        if max_chapters is not None:
            chapters_stmt = chapters_stmt.limit(max_chapters)
        chapters = list((await session.execute(chapters_stmt)).scalars())
        logger.info(
            "project=%s chapters_to_scan=%d (status in %s)",
            project.slug,
            len(chapters),
            sorted(FINAL_ENOUGH_STATUSES),
        )

        for chapter in chapters:
            # Per-chapter checkpoint: savepoint in both modes, commit only
            # when --apply is set. Dry-run explicitly rolls back the
            # savepoint so nothing leaks into the outer transaction.
            savepoint = await session.begin_nested()
            row: BackfillRow | None = None
            try:
                row = await _backfill_one_chapter(
                    session,
                    settings,
                    project=project,
                    chapter=chapter,
                    force=force,
                )
            finally:
                if apply and row is not None and row.status == "applied":
                    try:
                        await savepoint.commit()
                    except Exception:
                        logger.exception(
                            "savepoint commit failed for %s ch=%d",
                            project.slug,
                            chapter.chapter_number,
                        )
                else:
                    # Dry-run, skipped, or errored chapters leave no writes.
                    if savepoint.is_active:
                        await savepoint.rollback()

            if row is None:
                # Something blew up before we could build a row — treat as
                # an error row so the CSV/summary reflects it.
                row = BackfillRow(
                    project_slug=project.slug,
                    chapter_number=chapter.chapter_number,
                    status="error",
                    characters_updated=0,
                    snapshots_created=0,
                    notes="inner exception (see logs)",
                )

            if row.status == "skip-already-done":
                summary.skipped_already_done += 1
            elif row.status == "skip-no-content":
                summary.skipped_no_content += 1
            elif row.status == "error":
                summary.errored += 1
            summary.rows.append(row)

            if apply and row.status == "applied":
                await session.commit()

    return summary


async def _run(
    slug_filter: str | None,
    include_all: bool,
    apply: bool,
    force: bool,
    max_chapters: int | None,
    csv_path: Path | None,
) -> int:
    settings = get_settings()
    async with session_scope(settings) as session:
        projects = await _select_projects(session, slug=slug_filter, include_all=include_all)

    if not projects:
        logger.error("no matching projects; nothing to do")
        return 1

    total_summary = BackfillSummary()
    for project in projects:
        proj_summary = await _backfill_project(
            project.slug,
            apply=apply,
            force=force,
            max_chapters=max_chapters,
            slug_filter=slug_filter,
            include_all=include_all,
        )

        total_summary.rows.extend(proj_summary.rows)
        total_summary.skipped_already_done += proj_summary.skipped_already_done
        total_summary.skipped_no_content += proj_summary.skipped_no_content
        total_summary.errored += proj_summary.errored

    _emit_csv(total_summary.rows, csv_path)
    _emit_summary(total_summary, apply=apply, dry_run=not apply)
    return 0


def _emit_csv(rows: list[BackfillRow], csv_path: Path | None) -> None:
    if csv_path is None:
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["project_slug", "chapter_number", "status", "characters_updated", "snapshots_created", "notes"]
        )
        for row in rows:
            writer.writerow(
                [row.project_slug, row.chapter_number, row.status, row.characters_updated, row.snapshots_created, row.notes]
            )
    logger.info("csv written to %s (rows=%d)", csv_path, len(rows))


def _emit_summary(summary: BackfillSummary, *, apply: bool, dry_run: bool) -> None:
    applied = sum(1 for r in summary.rows if r.status == "applied")
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"\n[backfill-lifecycle] {mode} summary:")
    print(f"  chapters processed: {len(summary.rows)}")
    print(f"  applied:            {applied}")
    print(f"  skipped (already):  {summary.skipped_already_done}")
    print(f"  skipped (no text):  {summary.skipped_no_content}")
    print(f"  errors:             {summary.errored}")
    if dry_run:
        print("  (no rows committed — pass --apply to persist)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-slug", help="backfill a single project by slug")
    parser.add_argument("--all", action="store_true", help="include every project regardless of status")
    parser.add_argument("--apply", action="store_true", help="commit writes (default is dry-run)")
    parser.add_argument("--force", action="store_true", help="re-run even if chapter already has new-schema snapshots")
    parser.add_argument("--max-chapters", type=int, default=None, help="cap chapters per project (smoke-test safety)")
    parser.add_argument("--csv", type=Path, default=None, help="path to write per-chapter CSV (optional)")
    parser.add_argument("--log-level", default="INFO", help="logging level (default INFO)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    return asyncio.run(
        _run(
            slug_filter=args.project_slug,
            include_all=args.all,
            apply=args.apply,
            force=args.force,
            max_chapters=args.max_chapters,
            csv_path=args.csv,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
