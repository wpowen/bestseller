"""Repair chapter-number gaps for a single BestSeller project.

Symptom: ``projects.current_chapter_number = N`` but ``chapters`` contains
``planned`` rows starting at ``M > N + 1`` (200-chapter gap for 道种破虚).
Root cause: ``generate_volume_plan`` re-seeded chapter numbers from a drifted
``VOLUME_PLAN`` artifact (see plan at ``.claude/plans/nifty-percolating-beaver.md``).

This script:

1. Classifies ``chapters`` rows into **written** (status in
   drafting/review/revision/complete/drafted/reviewed) and **planned**
   (status == planned).
2. Keeps written rows at their existing ``chapter_number``.
3. Re-sequences planned rows to the contiguous range
   ``[max(written)+1 .. max(written)+len(planned)]``, preserving their
   relative order.
4. Updates ``chapters.chapter_number`` via a two-phase write that avoids
   collisions with the UNIQUE (project_id, chapter_number) constraint.
5. Loads the latest ``CHAPTER_OUTLINE_BATCH`` artifact, dedupes by
   ``chapter_number`` (last write wins), then re-numbers the planned-range
   entries to match the new ``chapter_number`` mapping.
6. Writes a **new version** of the artifact (old versions are preserved).

Usage::

    .venv/bin/python -m scripts.repair_chapter_number_gap \\
        --project-slug xianxia-upgrade-1776137730 --dry-run     # default

    .venv/bin/python -m scripts.repair_chapter_number_gap \\
        --project-slug xianxia-upgrade-1776137730 --apply

Refuses to proceed when:
  * project doesn't exist
  * no ``planned`` rows need shifting
  * the target slot range collides with other written rows
  * ``--apply`` was not explicitly set (safety default)

Idempotent after success: re-running reports "already contiguous".
"""

from __future__ import annotations

import argparse
import asyncio
import copy
from typing import Any

from sqlalchemy import select

from bestseller.domain.enums import ArtifactType, ChapterStatus
from bestseller.infra.db.models import (
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
)
from bestseller.infra.db.session import create_session_factory
from bestseller.settings import load_settings


# Statuses that indicate real content exists and must be preserved.
_WRITTEN_STATUSES: set[str] = {
    ChapterStatus.OUTLINING.value,
    ChapterStatus.DRAFTING.value,
    ChapterStatus.REVIEW.value,
    ChapterStatus.REVISION.value,
    ChapterStatus.COMPLETE.value,
}


def _iter_outline_chapters(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, dict):
        raw = content.get("chapters", [])
    elif isinstance(content, list):
        raw = content
    else:
        raw = []
    return [c for c in raw if isinstance(c, dict)]


async def _load_project(session, slug: str) -> ProjectModel:
    project = await session.scalar(
        select(ProjectModel).where(ProjectModel.slug == slug)
    )
    if project is None:
        raise SystemExit(f"❌ project not found: {slug}")
    return project


async def _load_chapters(session, project_id) -> list[ChapterModel]:
    rows = await session.execute(
        select(ChapterModel)
        .where(ChapterModel.project_id == project_id)
        .order_by(ChapterModel.chapter_number)
    )
    return list(rows.scalars())


async def _latest_outline_artifact(session, project_id) -> PlanningArtifactVersionModel | None:
    return await session.scalar(
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == ArtifactType.CHAPTER_OUTLINE_BATCH.value,
        )
        .order_by(PlanningArtifactVersionModel.version_no.desc())
        .limit(1)
    )


def _classify(chapters: list[ChapterModel]) -> tuple[list[ChapterModel], list[ChapterModel]]:
    written = [c for c in chapters if c.status in _WRITTEN_STATUSES]
    planned = [c for c in chapters if c.status == ChapterStatus.PLANNED.value]
    return written, planned


def _plan_shift(
    written: list[ChapterModel],
    planned: list[ChapterModel],
) -> tuple[dict[int, int], int, int]:
    """Return (mapping old_number→new_number, first_new, last_new)."""
    max_written = max((c.chapter_number for c in written), default=0)
    # Keep planned chapters in their original chapter_number order.
    planned_sorted = sorted(planned, key=lambda c: c.chapter_number)
    mapping: dict[int, int] = {}
    first_new = max_written + 1
    for idx, ch in enumerate(planned_sorted):
        new_no = max_written + 1 + idx
        if ch.chapter_number != new_no:
            mapping[ch.chapter_number] = new_no
    last_new = max_written + len(planned_sorted)
    return mapping, first_new, last_new


def _validate_no_collision(
    chapters: list[ChapterModel],
    mapping: dict[int, int],
) -> list[str]:
    """Ensure target slots are either free or belong to the row being shifted."""
    targets = set(mapping.values())
    # Source numbers (we'll vacate these)
    sources = set(mapping.keys())
    errors: list[str] = []
    taken_by_non_shifted = {
        c.chapter_number for c in chapters if c.chapter_number not in sources
    }
    collisions = targets & taken_by_non_shifted
    if collisions:
        errors.append(
            f"target chapter_numbers {sorted(collisions)[:10]} are already "
            f"occupied by non-shifted rows — refusing to overwrite"
        )
    return errors


async def _apply_chapter_shift(session, mapping: dict[int, int], project_id) -> None:
    """Two-phase update: move into a parking range, then into the final range."""
    if not mapping:
        return
    # Park: shift every source to a negative range to avoid unique-constraint conflicts
    # during the second pass.  PostgreSQL allows negative Integer values.
    PARK_OFFSET = -1_000_000_000
    # Phase 1: source → PARK_OFFSET - source  (guaranteed distinct and negative)
    for old_no in mapping:
        row = await session.scalar(
            select(ChapterModel)
            .where(
                ChapterModel.project_id == project_id,
                ChapterModel.chapter_number == old_no,
            )
        )
        if row is None:
            raise RuntimeError(f"chapter {old_no} vanished mid-shift")
        row.chapter_number = PARK_OFFSET - old_no
    await session.flush()
    # Phase 2: parked → new_no
    for old_no, new_no in mapping.items():
        row = await session.scalar(
            select(ChapterModel)
            .where(
                ChapterModel.project_id == project_id,
                ChapterModel.chapter_number == PARK_OFFSET - old_no,
            )
        )
        if row is None:
            raise RuntimeError(f"parked chapter for old={old_no} vanished")
        row.chapter_number = new_no
    await session.flush()


def _repair_outline_content(
    content: Any,
    mapping: dict[int, int],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Dedup by chapter_number (last wins), then renumber shifted entries."""
    entries = _iter_outline_chapters(content)
    stats = {
        "raw_entries": len(entries),
        "dropped_missing_number": 0,
        "dedup_removed": 0,
        "renumbered": 0,
    }
    by_number: dict[int, dict[str, Any]] = {}
    for ch in entries:
        n = ch.get("chapter_number")
        if not isinstance(n, int):
            stats["dropped_missing_number"] += 1
            continue
        if n in by_number:
            stats["dedup_removed"] += 1
        by_number[n] = copy.deepcopy(ch)
    # Apply mapping
    new_map: dict[int, dict[str, Any]] = {}
    for n, ch in by_number.items():
        target = mapping.get(n, n)
        if target != n:
            ch["chapter_number"] = target
            stats["renumbered"] += 1
        new_map[target] = ch
    ordered = [new_map[k] for k in sorted(new_map)]
    batch_name = (
        content.get("batch_name") if isinstance(content, dict) else None
    ) or "progressive-merged-outline"
    new_content = {"batch_name": batch_name, "chapters": ordered}
    stats["final_entries"] = len(ordered)
    return new_content, stats


async def _write_new_outline_version(
    session,
    project_id,
    prev: PlanningArtifactVersionModel,
    new_content: dict[str, Any],
) -> int:
    # Compute next version_no for (project_id, artifact_type, scope_ref_id=NULL)
    max_ver = await session.scalar(
        select(PlanningArtifactVersionModel.version_no)
        .where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == prev.artifact_type,
        )
        .order_by(PlanningArtifactVersionModel.version_no.desc())
        .limit(1)
    )
    next_ver = int(max_ver or 0) + 1
    new_row = PlanningArtifactVersionModel(
        project_id=project_id,
        artifact_type=prev.artifact_type,
        scope_ref_id=None,
        version_no=next_ver,
        status="approved",
        schema_version=prev.schema_version,
        content=new_content,
        source_run_id=None,
        notes="repaired by scripts/repair_chapter_number_gap.py",
        created_by="repair_script",
    )
    session.add(new_row)
    await session.flush()
    return next_ver


async def _run(slug: str, apply: bool) -> int:
    settings = load_settings()
    factory = create_session_factory(settings)
    async with factory() as session:
        project = await _load_project(session, slug)
        chapters = await _load_chapters(session, project.id)
        if not chapters:
            print(f"ℹ️  project {slug} has no chapters — nothing to repair.")
            return 0
        written, planned = _classify(chapters)
        max_written = max((c.chapter_number for c in written), default=0)

        print(f"Project: {project.slug}  ({project.title})")
        print(f"  target_chapters={project.target_chapters}  current_chapter_number={project.current_chapter_number}")
        print(f"  chapters in DB: total={len(chapters)}  written={len(written)}  planned={len(planned)}")
        print(f"  max(written)={max_written}  max(planned)={max((c.chapter_number for c in planned), default=0)}")

        mapping, first_new, last_new = _plan_shift(written, planned)
        needs_shift = bool(mapping)

        if needs_shift:
            print(
                f"\nShift plan:  {len(mapping)} planned chapters will move to "
                f"[{first_new} .. {last_new}]  (was min={min(mapping)} max={max(mapping)})"
            )
            items = list(mapping.items())
            head, tail = items[:5], items[-5:]
            if len(items) > 10:
                for old_no, new_no in head:
                    print(f"  {old_no} → {new_no}")
                print(f"  … ({len(items) - 10} more) …")
                for old_no, new_no in tail:
                    print(f"  {old_no} → {new_no}")
            else:
                for old_no, new_no in items:
                    print(f"  {old_no} → {new_no}")

            errors = _validate_no_collision(chapters, mapping)
            if errors:
                print("\n🔴 REFUSING to apply — collisions detected:")
                for e in errors:
                    print(f"  - {e}")
                return 2
        else:
            print("\n✅ Chapter numbers already contiguous — no DB shift needed.")

        # Artifact repair preview (no commit yet)
        art = await _latest_outline_artifact(session, project.id)
        new_content: dict[str, Any] | None = None
        outline_dirty = False
        if art is not None:
            candidate, stats = _repair_outline_content(art.content, mapping)
            print(
                f"\nCHAPTER_OUTLINE_BATCH v{art.version_no} repair preview: "
                f"{stats['raw_entries']} entries → {stats['final_entries']} "
                f"(dedup_removed={stats['dedup_removed']}, renumbered={stats['renumbered']}, "
                f"dropped_missing_number={stats['dropped_missing_number']})"
            )
            outline_dirty = (
                stats["dedup_removed"] > 0
                or stats["renumbered"] > 0
                or stats["dropped_missing_number"] > 0
            )
            if outline_dirty:
                new_content = candidate
            else:
                print("  outline already clean — will not write a new version.")
        else:
            print("\nℹ️  No CHAPTER_OUTLINE_BATCH artifact found.")

        if not needs_shift and not outline_dirty:
            print("\n✅ Nothing to repair.")
            return 0

        if not apply:
            print("\n📝 DRY RUN — pass --apply to execute.")
            return 0

        # Apply using the session's existing auto-begun transaction
        try:
            if needs_shift:
                await _apply_chapter_shift(session, mapping, project.id)
            if art is not None and new_content is not None:
                new_ver = await _write_new_outline_version(session, project.id, art, new_content)
                print(f"  wrote CHAPTER_OUTLINE_BATCH version_no={new_ver}")
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        print("\n✅ Applied.  Re-run with --dry-run to verify idempotency.")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-slug", required=True)
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True, help="(default) report without writing")
    g.add_argument("--apply", action="store_true", default=False, help="actually write the changes")
    args = parser.parse_args()
    rc = asyncio.run(_run(args.project_slug, apply=args.apply))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
