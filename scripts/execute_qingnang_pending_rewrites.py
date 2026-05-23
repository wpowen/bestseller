"""Execute pending rewrite tasks for 《青囊不语问阴阳》 only."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any
from uuid import UUID

from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import ChapterModel, ProjectModel, RewriteTaskModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.drafts import (  # noqa: E402
    format_chapter_heading,
    sanitize_novel_markdown_content,
)
from bestseller.services.exports import write_markdown_output  # noqa: E402
from bestseller.services.reviews import rewrite_chapter_from_task  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

PROJECT_SLUG = "exorcist-detective-1778051012"


def _chapter_markdown_has_heading(content_md: str, chapter_number: int) -> bool:
    for line in content_md.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith(f"# 第{chapter_number}章") or stripped.startswith(f"# Chapter {chapter_number}")
    return False


def _sync_chapter_file(output_base_dir: Path, project: ProjectModel, chapter: ChapterModel, content_md: str) -> str:
    clean = sanitize_novel_markdown_content(content_md, language=project.language)
    chapter_number = int(chapter.chapter_number)
    if not _chapter_markdown_has_heading(clean, chapter_number):
        clean = f"{format_chapter_heading(chapter_number, chapter.title, language=project.language)}\n\n{clean}"
    path = output_base_dir / project.slug / f"chapter-{chapter_number:03d}.md"
    write_markdown_output(path, clean)
    return str(path)


async def _select_tasks(
    *,
    repair_source: str | None,
    chapters: set[int],
    limit: int,
) -> list[dict[str, Any]]:
    settings = load_settings()
    async with session_scope(settings) as session:
        project = (await session.scalars(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))).one()
        stmt = (
            select(RewriteTaskModel, ChapterModel)
            .join(ChapterModel, ChapterModel.id == RewriteTaskModel.trigger_source_id)
            .where(RewriteTaskModel.project_id == project.id)
            .where(RewriteTaskModel.status.in_(["pending", "queued"]))
            .order_by(RewriteTaskModel.priority.asc(), ChapterModel.chapter_number.asc(), RewriteTaskModel.created_at.asc())
        )
        if repair_source:
            stmt = stmt.where(RewriteTaskModel.metadata_json["repair_source"].as_string() == repair_source)
        if chapters:
            stmt = stmt.where(ChapterModel.chapter_number.in_(sorted(chapters)))
        rows = (await session.execute(stmt)).all()
        selected = rows[:limit] if limit > 0 else rows
        return [
            {
                "task_id": str(task.id),
                "chapter_number": int(chapter.chapter_number),
                "priority": int(task.priority),
                "repair_source": (task.metadata_json or {}).get("repair_source"),
                "title": (task.metadata_json or {}).get("title") or chapter.title,
            }
            for task, chapter in selected
        ]


async def _execute_task(task_id: str, *, timeout_seconds: float | None) -> dict[str, Any]:
    settings = load_settings()
    task_uuid = UUID(task_id)
    async with session_scope(settings) as session:
        project = (await session.scalars(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))).one()
        task = await session.get(RewriteTaskModel, task_uuid)
        if task is None or task.status not in {"pending", "queued"}:
            return {"task_id": task_id, "skipped": True, "reason": "task_not_pending"}
        chapter = await session.scalar(select(ChapterModel).where(ChapterModel.id == task.trigger_source_id))
        if chapter is None:
            return {"task_id": task_id, "skipped": True, "reason": "chapter_missing"}
        coro = rewrite_chapter_from_task(
            session,
            PROJECT_SLUG,
            int(chapter.chapter_number),
            rewrite_task_id=task_uuid,
            settings=settings,
        )
        if timeout_seconds and timeout_seconds > 0:
            draft, rewrite_task = await asyncio.wait_for(coro, timeout=timeout_seconds)
        else:
            draft, rewrite_task = await coro
        payload: dict[str, Any] = {
            "task_id": task_id,
            "chapter_number": int(chapter.chapter_number),
            "status": rewrite_task.status,
            "word_count": getattr(draft, "word_count", None),
            "repair_source": (task.metadata_json or {}).get("repair_source"),
        }
        if rewrite_task.status == "completed":
            payload["export_path"] = _sync_chapter_file(Path(settings.output.base_dir), project, chapter, draft.content_md)
        else:
            payload["error_log"] = rewrite_task.error_log
        return payload


async def run(
    *,
    repair_source: str | None,
    chapters: set[int],
    limit: int,
    dry_run: bool,
    timeout_seconds: float | None,
) -> dict[str, Any]:
    selected = await _select_tasks(repair_source=repair_source, chapters=chapters, limit=limit)
    if dry_run:
        return {"project_slug": PROJECT_SLUG, "dry_run": True, "selected": selected}
    results: list[dict[str, Any]] = []
    for task in selected:
        try:
            results.append(await _execute_task(task["task_id"], timeout_seconds=timeout_seconds))
        except TimeoutError:
            results.append({"task_id": task["task_id"], "chapter_number": task["chapter_number"], "status": "timeout"})
        except Exception as exc:
            results.append(
                {
                    "task_id": task["task_id"],
                    "chapter_number": task["chapter_number"],
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {"project_slug": PROJECT_SLUG, "dry_run": False, "selected": selected, "results": results}


def _parse_chapters(raw: str) -> set[int]:
    chapters: set[int] = set()
    for part in raw.split(","):
        value = part.strip()
        if value:
            chapters.add(int(value))
    return chapters


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=None, help="Filter metadata repair_source.")
    parser.add_argument("--chapters", default="", help="Comma-separated chapter numbers.")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=900)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                run(
                    repair_source=args.source,
                    chapters=_parse_chapters(args.chapters),
                    limit=args.limit,
                    dry_run=args.dry_run,
                    timeout_seconds=args.timeout_seconds,
                )
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
