"""Supersede obsolete pending rewrite tasks for 《青囊不语问阴阳》 only.

The repair run creates narrow manual/current-draft fixes after some older
DeepSeek tasks were queued. If workers resume, those stale tasks can overwrite
the repaired canon state. This script only touches the target project and keeps
all rows for audit history by moving selected pending/queued tasks to
``superseded`` with a metadata note.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import ChapterModel, ProjectModel, RewriteTaskModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

PROJECT_SLUG = "exorcist-detective-1778051012"
DEFAULT_SOURCES = {
    "qingnang_ch51_75_recovery_20260523",
    "qingnang_narrative_richness_20260523",
}
DEFAULT_CHAPTERS = {51, 62, 63, 64, 69, 70, 71}


def _parse_csv_set(raw: str, *, cast: type = str) -> set[Any]:
    values: set[Any] = set()
    for part in raw.split(","):
        value = part.strip()
        if value:
            values.add(cast(value))
    return values


async def _run(
    *,
    sources: set[str],
    chapters: set[int],
    all_target_pending: bool,
    dry_run: bool,
) -> dict[str, Any]:
    settings = load_settings()
    selected: list[dict[str, Any]] = []
    async with session_scope(settings) as session:
        project = (
            await session.scalars(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        ).one()
        stmt = (
            select(RewriteTaskModel, ChapterModel)
            .outerjoin(ChapterModel, ChapterModel.id == RewriteTaskModel.trigger_source_id)
            .where(RewriteTaskModel.project_id == project.id)
            .where(RewriteTaskModel.status.in_(["pending", "queued"]))
            .order_by(RewriteTaskModel.priority, ChapterModel.chapter_number, RewriteTaskModel.created_at)
        )
        if not all_target_pending:
            stmt = stmt.where(ChapterModel.chapter_number.in_(sorted(chapters)))
        rows = (await session.execute(stmt)).all()
        for task, chapter in rows:
            metadata = dict(task.metadata_json or {})
            repair_source = metadata.get("repair_source")
            if not all_target_pending and repair_source not in sources:
                continue
            chapter_number = int(chapter.chapter_number) if chapter is not None else None
            selected.append(
                {
                    "task_id": str(task.id),
                    "chapter_number": chapter_number,
                    "trigger_type": task.trigger_type,
                    "status_before": task.status,
                    "priority": int(task.priority),
                    "repair_source": repair_source,
                    "title": metadata.get("title"),
                }
            )
            if dry_run:
                continue
            metadata["superseded_reason"] = (
                "Covered by current targeted canon/narrative repair pass; "
                "kept for audit history, not safe to execute after gate pass."
            )
            metadata["superseded_by"] = "supersede_qingnang_obsolete_rewrite_tasks.py"
            task.metadata_json = metadata
            task.status = "superseded"
    return {
        "project_slug": PROJECT_SLUG,
        "dry_run": dry_run,
        "sources": sorted(sources),
        "chapters": sorted(chapters),
        "all_target_pending": all_target_pending,
        "superseded_count": 0 if dry_run else len(selected),
        "selected": selected,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sources",
        default=",".join(sorted(DEFAULT_SOURCES)),
        help="Comma-separated metadata repair_source values.",
    )
    parser.add_argument(
        "--chapters",
        default=",".join(str(ch) for ch in sorted(DEFAULT_CHAPTERS)),
        help="Comma-separated chapter numbers.",
    )
    parser.add_argument(
        "--all-target-pending",
        action="store_true",
        help="Supersede every pending/queued rewrite task for the target project only.",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    payload = asyncio.run(
        _run(
            sources=_parse_csv_set(args.sources, cast=str),
            chapters=_parse_csv_set(args.chapters, cast=int),
            all_target_pending=args.all_target_pending,
            dry_run=not args.apply,
        )
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
