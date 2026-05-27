#!/usr/bin/env python3
"""Run a bounded repair/continuation closure for an in-progress book.

Unlike the whole-book closure runner, this entrypoint does not treat unfinished
target chapters as a failure. It audits a requested chapter window, syncs
framework rewrite tasks for blocking issues, and reports whether the next
planned chapters can continue after those repairs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import replace
from pathlib import Path
import sys
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
for item in (_SRC,):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.repair import run_project_repair  # noqa: E402
from bestseller.services.wip_repair_closure import (  # noqa: E402
    build_wip_repair_closure_report,
)
from bestseller.settings import load_settings  # noqa: E402


def _json_dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings()
    report_path = (
        Path(settings.output.base_dir)
        / args.slug
        / "audits"
        / "wip-repair-closure"
        / "report.json"
    )
    async with session_scope(settings) as session:
        report = await build_wip_repair_closure_report(
            session,
            settings,
            slug=args.slug,
            repair_start=args.repair_start,
            repair_end=args.repair_end,
            continuation_size=args.continuation_size,
            create_tasks=args.create_tasks,
            replace_existing=args.replace_existing,
            max_attempts_per_chapter=args.max_attempts_per_chapter,
        )
        payload = report.to_dict()
        _json_dump(report_path, payload)

        if args.execute_repair:
            task_ids = [
                str(task_id)
                for task_id in payload.get("task_sync", {}).get("task_ids", [])
            ]
            target_chapter_numbers = [
                int(task["chapter_number"])
                for task in payload.get("repair_plan", {}).get("tasks", [])
                if isinstance(task, dict) and int(task.get("chapter_number") or 0) > 0
            ]
            if not task_ids:
                payload = replace(
                    report,
                    execution={
                        "requested": True,
                        "skipped": True,
                        "reason": "no_synced_wip_rewrite_tasks",
                    },
                ).to_dict()
                _json_dump(report_path, payload)
                return {**payload, "report_path": str(report_path)}
            result = await run_project_repair(
                session,
                settings,
                args.slug,
                requested_by="wip-repair-closure",
                include_pending_rewrite_tasks=True,
                pending_rewrite_task_limit=args.round_size,
                pending_rewrite_task_ids=task_ids,
                target_chapter_numbers=target_chapter_numbers,
                export_markdown=True,
            )
            payload = replace(
                report,
                execution={
                    "requested": True,
                    "round_size": args.round_size,
                    "result": result.model_dump(mode="json"),
                },
            ).to_dict()
            _json_dump(report_path, payload)
        return {**payload, "report_path": str(report_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--repair-start", type=int, default=1)
    parser.add_argument("--repair-end", type=int, default=10)
    parser.add_argument("--continuation-size", type=int, default=0)
    parser.add_argument("--create-tasks", action="store_true")
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--execute-repair", action="store_true")
    parser.add_argument("--round-size", type=int, default=10)
    parser.add_argument("--max-attempts-per-chapter", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.repair_start <= 0 or args.repair_end < args.repair_start:
        raise SystemExit("--repair-start/--repair-end must form a positive window")
    if args.execute_repair and not args.create_tasks:
        raise SystemExit("--execute-repair requires --create-tasks")

    payload = asyncio.run(_run(args))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"{payload['status']} next={payload['next_action']} "
            f"report={payload['report_path']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
