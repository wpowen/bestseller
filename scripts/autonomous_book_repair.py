"""Autonomous whole-book repair orchestrator.

This is the reusable entrypoint for the target workflow:

1. run deterministic book audits for one or many ``output/<slug>`` books;
2. build a quality-retrofit repair plan;
3. optionally sync that plan into DB ``rewrite_tasks``;
4. optionally execute those rewrite tasks through the existing chapter editor.

The default mode is read-only: it writes audit/report artifacts but does not
mutate DB chapters or call an LLM unless ``--create-tasks`` / ``--execute`` is
explicitly supplied.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
_SCRIPTS = _REPO_ROOT / "scripts"
for item in (_SRC, _SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from bestseller.infra.db.models import ChapterModel, RewriteTaskModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.autonomous_book_repair import (  # noqa: E402
    QualityRepairTaskSpec,
    build_quality_repair_plan,
    create_quality_retrofit_rewrite_tasks,
    discover_output_book_slugs,
    latest_quality_retrofit_csv,
    load_patch_plan,
    load_quality_retrofit_rows,
)
from bestseller.services.projects import get_project_by_slug  # noqa: E402
from bestseller.services.reviews import rewrite_chapter_from_task  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402
from quality_levers_retrofit_audit import (  # noqa: E402
    audit_one_chapter,
    discover_chapters,
    write_csv,
    write_summary,
)
from quality_levers_retrofit_patch import build_chapter_patch_plan  # noqa: E402


def _parse_priorities(value: str) -> set[str]:
    valid = {"critical", "high", "medium", "ok"}
    priorities = {part.strip() for part in value.split(",") if part.strip()}
    if not priorities:
        return {"critical", "high"}
    invalid = priorities - valid
    if invalid:
        raise SystemExit(f"Unknown priority value(s): {sorted(invalid)}")
    return priorities


def _run_audit(slug: str, *, platform: str | None) -> Path:
    chapters = discover_chapters(slug)
    if not chapters:
        raise FileNotFoundError(f"No chapters found under output/{slug}/")
    rows = [audit_one_chapter(slug, number, path, platform=platform) for number, path in chapters]
    base = _REPO_ROOT / "output" / slug / "audits" / "quality-retrofit"
    end = rows[-1].chapter_number if rows else 0
    csv_path = base / f"window-001-{end:03d}.csv"
    summary_path = base / "summary.md"
    json_path = csv_path.with_suffix(".json")
    write_csv(rows, csv_path)
    write_summary(rows, summary_path)
    json_path.write_text(
        json.dumps([row.as_dict() for row in rows], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return csv_path


def _write_patch_plan(
    slug: str,
    audit_rows: list[dict[str, str]],
    *,
    priorities: set[str],
    limit: int | None,
) -> Path:
    matching = [row for row in audit_rows if row.get("priority") in priorities]
    if limit is not None and limit > 0:
        matching = matching[:limit]
    plans = []
    for row in matching:
        chapter_number = int(row["chapter_number"])
        cause_ids = tuple(cause for cause in (row.get("cause_ids") or "").split(";") if cause)
        plan = build_chapter_patch_plan(slug, chapter_number, row["priority"], cause_ids)
        plans.append(
            {
                "slug": plan.slug,
                "chapter_number": plan.chapter_number,
                "priority": plan.priority,
                "cause_ids": list(plan.cause_ids),
                "patch_point_count": len(plan.patch_points),
                "patch_points": [
                    {
                        "cause_id": point.cause_id,
                        "location": point.location,
                        "issue_summary": point.issue_summary,
                        "snippet": point.snippet,
                        "repair_action_summary": point.repair_action_summary,
                        "expected_max_chars_delta": point.expected_max_chars_delta,
                    }
                    for point in plan.patch_points
                ],
            }
        )
    out_path = (
        _REPO_ROOT
        / "output"
        / slug
        / "audits"
        / "quality-retrofit"
        / "autonomous-patch-plan.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


async def _sync_tasks(
    slug: str,
    specs: Sequence[QualityRepairTaskSpec],
    *,
    replace_existing: bool,
) -> dict[str, object]:
    settings = load_settings()
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return {
                "db_project_found": False,
                "created": 0,
                "skipped_existing": 0,
                "superseded": 0,
                "missing_chapters": [],
                "task_ids": [],
            }
        result = await create_quality_retrofit_rewrite_tasks(
            session,
            project,
            specs,
            replace_existing=replace_existing,
        )
        return {"db_project_found": True, **result.to_dict()}


async def _execute_tasks(
    slug: str,
    task_ids: list[str],
    *,
    limit: int | None,
) -> dict[str, object]:
    settings = load_settings()
    executed = 0
    failed: list[dict[str, str]] = []
    selected = task_ids[:limit] if limit is not None and limit > 0 else task_ids
    for task_id in selected:
        try:
            async with session_scope(settings) as session:
                task = await session.get(RewriteTaskModel, UUID(task_id))
                if task is None or task.status not in {"pending", "queued"}:
                    continue
                chapter = await session.scalar(
                    select(ChapterModel).where(ChapterModel.id == task.trigger_source_id)
                )
                if chapter is None:
                    continue
                await rewrite_chapter_from_task(
                    session,
                    slug,
                    int(chapter.chapter_number),
                    rewrite_task_id=UUID(task_id),
                    settings=settings,
                )
                executed += 1
        except Exception as exc:  # noqa: BLE001 - report and keep the batch moving.
            failed.append({"task_id": task_id, "error": f"{type(exc).__name__}: {exc}"})
    return {"executed": executed, "failed": failed}


async def _run_for_slug(
    slug: str,
    *,
    platform: str | None,
    priorities: set[str],
    audit: bool,
    limit: int | None,
    create_tasks: bool,
    replace_existing: bool,
    execute: bool,
) -> dict[str, object]:
    if audit:
        csv_path = _run_audit(slug, platform=platform)
    else:
        csv_path = latest_quality_retrofit_csv(slug, output_dir=_REPO_ROOT / "output")
        if csv_path is None:
            raise FileNotFoundError(
                f"No existing quality-retrofit CSV found for {slug}; rerun without --no-audit."
            )
    audit_rows = load_quality_retrofit_rows(csv_path)
    patch_path = _write_patch_plan(slug, audit_rows, priorities=priorities, limit=limit)
    patch_plan = load_patch_plan(patch_path)
    plan = build_quality_repair_plan(
        slug=slug,
        audit_rows=audit_rows,
        patch_plan=patch_plan,
        priorities=priorities,
        limit=limit,
    )
    plan_path = (
        _REPO_ROOT
        / "output"
        / slug
        / "audits"
        / "quality-retrofit"
        / "autonomous-repair-plan.json"
    )
    plan_payload = plan.to_dict()
    plan_path.write_text(json.dumps(plan_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result: dict[str, object] = {
        "slug": slug,
        "csv_path": str(csv_path),
        "patch_plan_path": str(patch_path),
        "repair_plan_path": str(plan_path),
        "repair_plan": {
            "task_count": plan_payload["task_count"],
            "priority_counts": plan_payload["priority_counts"],
            "cause_counts": plan_payload["cause_counts"],
        },
    }
    task_sync: dict[str, object] | None = None
    if create_tasks or execute:
        task_sync = await _sync_tasks(slug, plan.specs, replace_existing=replace_existing)
        result["task_sync"] = task_sync
    if execute and task_sync:
        task_ids = [str(item) for item in task_sync.get("task_ids", [])]
        result["execution"] = await _execute_tasks(slug, task_ids, limit=limit)
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--slug", help="One output/<slug> book to repair-plan.")
    target.add_argument("--all", action="store_true", help="Process every output directory with chapters.")
    parser.add_argument("--platform", default=None, help="Platform id for quality_levers audit.")
    parser.add_argument("--priority", default="critical,high", help="Comma list: critical,high,medium,ok.")
    parser.add_argument("--limit", type=int, default=0, help="Limit matching chapters per book.")
    parser.add_argument("--no-audit", action="store_true", help="Reuse latest audit CSV.")
    parser.add_argument("--create-tasks", action="store_true", help="Write DB rewrite_tasks for DB-backed projects.")
    parser.add_argument("--replace-existing", action="store_true", help="Supersede existing pending autonomous tasks.")
    parser.add_argument("--execute", action="store_true", help="Create and execute DB rewrite_tasks via editor LLM.")
    parser.add_argument("--json", action="store_true", help="Print full JSON result.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    priorities = _parse_priorities(args.priority)
    slugs = (
        discover_output_book_slugs(_REPO_ROOT / "output")
        if args.all
        else [str(args.slug)]
    )
    if not slugs:
        print("No books found.")
        return 1

    async def _run_all() -> list[dict[str, object]]:
        results = []
        for slug in slugs:
            results.append(
                await _run_for_slug(
                    slug,
                    platform=args.platform,
                    priorities=priorities,
                    audit=not args.no_audit,
                    limit=args.limit if args.limit > 0 else None,
                    create_tasks=args.create_tasks,
                    replace_existing=args.replace_existing,
                    execute=args.execute,
                )
            )
        return results

    results = asyncio.run(_run_all())
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            plan = result["repair_plan"]
            print(
                "{slug}: tasks={tasks} priorities={priorities} causes={causes}".format(
                    slug=result["slug"],
                    tasks=plan["task_count"],  # type: ignore[index]
                    priorities=plan["priority_counts"],  # type: ignore[index]
                    causes=plan["cause_counts"],  # type: ignore[index]
                )
            )
            print(f"  repair_plan: {result['repair_plan_path']}")
            if "task_sync" in result:
                print(f"  task_sync: {result['task_sync']}")
            if "execution" in result:
                print(f"  execution: {result['execution']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
