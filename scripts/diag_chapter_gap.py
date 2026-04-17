"""Diagnose chapter-number continuity for BestSeller projects (read-only).

Scans the ``chapters`` table and the latest ``CHAPTER_OUTLINE_BATCH`` artifact
for each project, then reports any gap / duplication / status mismatch that
would prevent the writer from resuming cleanly.

Usage::

    .venv/bin/python -m scripts.diag_chapter_gap                      # all projects
    .venv/bin/python -m scripts.diag_chapter_gap --project-slug <slug>
    .venv/bin/python -m scripts.diag_chapter_gap --only-affected      # hide clean projects

Purely read-only — never writes to the database.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from typing import Any

from sqlalchemy import select

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import (
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
)
from bestseller.infra.db.session import create_session_factory
from bestseller.settings import load_settings


def _iter_outline_chapters(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, dict):
        chapters = content.get("chapters", [])
    elif isinstance(content, list):
        chapters = content
    else:
        chapters = []
    return [c for c in chapters if isinstance(c, dict)]


async def _latest_outline_batch(session, project_id) -> dict[str, Any] | None:
    row = await session.scalar(
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == ArtifactType.CHAPTER_OUTLINE_BATCH.value,
        )
        .order_by(PlanningArtifactVersionModel.version_no.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {
        "artifact_id": str(row.id),
        "version_no": row.version_no,
        "content": row.content,
    }


async def _diagnose_project(session, project: ProjectModel) -> dict[str, Any]:
    # Chapters in DB
    rows = (
        await session.execute(
            select(ChapterModel.chapter_number, ChapterModel.status)
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number)
        )
    ).all()
    numbers = [int(n) for n, _ in rows]
    statuses = Counter(s for _, s in rows)
    distinct = sorted(set(numbers))

    dup_numbers = sorted(n for n, c in Counter(numbers).items() if c > 1)

    # Gaps: missing chapter_numbers between 1 and max
    gaps: list[tuple[int, int]] = []
    if distinct:
        expected = set(range(1, max(distinct) + 1))
        missing = sorted(expected - set(distinct))
        # Condense to ranges
        if missing:
            start = prev = missing[0]
            for n in missing[1:]:
                if n == prev + 1:
                    prev = n
                    continue
                gaps.append((start, prev))
                start = prev = n
            gaps.append((start, prev))

    # Written vs planned segmentation
    written = sorted(
        n for n, s in rows
        if s in ("draft", "drafting", "review", "revision", "complete")
    )
    planned = sorted(n for n, s in rows if s == "planned")
    max_written = max(written) if written else 0
    next_needed = max_written + 1

    writer_outline_available = next_needed in set(distinct)
    writer_continuous = next_needed <= (max(written) + 1 if written else 1)

    outline = await _latest_outline_batch(session, project.id)
    outline_numbers: list[int] = []
    if outline is not None:
        for ch in _iter_outline_chapters(outline["content"]):
            n = ch.get("chapter_number")
            if isinstance(n, int):
                outline_numbers.append(n)
    outline_dups = sum(1 for _, c in Counter(outline_numbers).items() if c > 1)
    outline_distinct = len(set(outline_numbers))

    affected = bool(
        gaps
        or dup_numbers
        or (written and planned and min(planned) > max_written + 1)
        or outline_dups > 0
        or (written and not writer_outline_available)
    )

    return {
        "slug": project.slug,
        "title": project.title,
        "status": project.status,
        "target_chapters": project.target_chapters,
        "current_chapter_number": project.current_chapter_number,
        "db_total": len(numbers),
        "db_distinct": len(distinct),
        "db_duplicates": dup_numbers,
        "db_gaps": gaps,
        "status_counts": dict(statuses),
        "max_written": max_written,
        "min_planned": min(planned) if planned else None,
        "max_planned": max(planned) if planned else None,
        "next_needed": next_needed,
        "next_outline_available": writer_outline_available,
        "outline_batch_total": len(outline_numbers),
        "outline_batch_distinct": outline_distinct,
        "outline_batch_duplicates": outline_dups,
        "affected": affected,
    }


def _format_report(r: dict[str, Any]) -> str:
    marker = "🔴 AFFECTED" if r["affected"] else "✅ ok"
    lines = [
        f"{marker}  {r['slug']}  [{r['status']}]  — {r['title']}",
        f"    target={r['target_chapters']}  current_chapter_number={r['current_chapter_number']}",
        f"    DB chapters: total={r['db_total']}  distinct={r['db_distinct']}  by_status={r['status_counts']}",
        f"    written max={r['max_written']}  planned range=[{r['min_planned']}, {r['max_planned']}]  next_needed={r['next_needed']}  outline_for_next={r['next_outline_available']}",
    ]
    if r["db_duplicates"]:
        lines.append(f"    duplicates in DB: {r['db_duplicates'][:20]}{'…' if len(r['db_duplicates']) > 20 else ''}")
    if r["db_gaps"]:
        gap_str = ", ".join(f"[{a}-{b}]" for a, b in r["db_gaps"][:10])
        lines.append(f"    gaps ({len(r['db_gaps'])}): {gap_str}{'…' if len(r['db_gaps']) > 10 else ''}")
    lines.append(
        f"    CHAPTER_OUTLINE_BATCH: total={r['outline_batch_total']}  distinct={r['outline_batch_distinct']}  "
        f"duplicate_numbers={r['outline_batch_duplicates']}"
    )
    return "\n".join(lines)


async def _run(project_slug: str | None, only_affected: bool) -> None:
    settings = load_settings()
    session_factory = create_session_factory(settings)
    async with session_factory() as session:
        stmt = select(ProjectModel).order_by(ProjectModel.slug)
        if project_slug:
            stmt = stmt.where(ProjectModel.slug == project_slug)
        projects = list((await session.execute(stmt)).scalars())
        if not projects:
            print(f"No projects matched (slug={project_slug!r}).")
            return

        reports = [await _diagnose_project(session, p) for p in projects]

    affected_count = sum(1 for r in reports if r["affected"])
    print(f"Scanned {len(reports)} projects — {affected_count} affected.\n")
    for r in reports:
        if only_affected and not r["affected"]:
            continue
        print(_format_report(r))
        print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-slug", default=None)
    parser.add_argument("--only-affected", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(args.project_slug, args.only_affected))


if __name__ == "__main__":
    main()
