"""Pause other books and focus repair execution on 《青囊不语问阴阳》."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import (  # noqa: E402
    BookGenerationScheduleModel,
    ProjectModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

TARGET_SLUG = "exorcist-detective-1778051012"
FOCUS_REASON = "focus_qingnang_deepseek_repair_20260523"


def _metadata_with_pause(raw: dict[str, Any] | None, *, previous_status: str, now: str) -> dict[str, Any]:
    metadata = dict(raw or {})
    focus = dict(metadata.get("focus_pause") or {})
    focus.setdefault("previous_status", previous_status)
    focus.update(
        {
            "reason": FOCUS_REASON,
            "paused_at": now,
            "preserve_data": True,
            "resume_note": "Restore previous_status and pending task statuses after qingnang repair closes.",
        }
    )
    metadata["focus_pause"] = focus
    metadata["production_paused"] = True
    metadata["production_pause_reason"] = FOCUS_REASON
    return metadata


def _metadata_with_focus(raw: dict[str, Any] | None, *, now: str) -> dict[str, Any]:
    metadata = dict(raw or {})
    metadata["focused_repair"] = {
        "active": True,
        "reason": FOCUS_REASON,
        "provider": "deepseek",
        "started_at": now,
        "priority": "only_book_allowed_to_run",
    }
    metadata["production_paused"] = False
    metadata.pop("production_pause_reason", None)
    return metadata


def _pause_task_metadata(raw: dict[str, Any] | None, *, previous_status: str, now: str) -> dict[str, Any]:
    metadata = dict(raw or {})
    metadata.setdefault(
        "focus_pause",
        {
            "previous_status": previous_status,
            "reason": FOCUS_REASON,
            "paused_at": now,
            "preserve_data": True,
        },
    )
    return metadata


def _pause_web_tasks(path: Path, *, now: str, apply: bool) -> dict[str, int]:
    if not path.exists():
        return {"seen": 0, "paused": 0}
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks") if isinstance(payload, dict) else payload
    if not isinstance(tasks, list):
        return {"seen": 0, "paused": 0}
    paused = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if task.get("project_slug") == TARGET_SLUG:
            continue
        if task.get("status") not in {"queued", "running"}:
            continue
        task.setdefault("focus_pause", {"previous_status": task.get("status"), "paused_at": now, "reason": FOCUS_REASON})
        task["status"] = "cancelled"
        task["cancel_requested"] = True
        task["current_stage"] = "paused_for_qingnang_focus"
        task["error"] = "Paused without data deletion while qingnang repair is prioritized."
        task["updated_at"] = now
        paused += 1
    if apply and paused:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"seen": len(tasks), "paused": paused}


async def run(*, apply: bool) -> dict[str, Any]:
    settings = load_settings()
    now = datetime.now(UTC).isoformat()
    summary: dict[str, Any] = {
        "target_slug": TARGET_SLUG,
        "applied": apply,
        "reason": FOCUS_REASON,
        "projects_paused": [],
        "rewrite_tasks_paused": 0,
        "workflows_paused": 0,
        "schedules_cancelled": 0,
        "target_pending_rewrite_tasks": 0,
    }
    async with session_scope(settings) as session:
        projects = (await session.scalars(select(ProjectModel).order_by(ProjectModel.slug))).all()
        project_by_id = {project.id: project for project in projects}
        target = next(project for project in projects if project.slug == TARGET_SLUG)
        if apply:
            target.metadata_json = _metadata_with_focus(target.metadata_json, now=now)
            target.status = "revising"
        for project in projects:
            if project.slug == TARGET_SLUG:
                continue
            summary["projects_paused"].append({"slug": project.slug, "previous_status": project.status})
            if apply:
                project.metadata_json = _metadata_with_pause(project.metadata_json, previous_status=project.status, now=now)
                project.status = "paused"

        rewrite_tasks = (
            await session.scalars(
                select(RewriteTaskModel).where(RewriteTaskModel.status.in_(["pending", "queued"]))
            )
        ).all()
        for task in rewrite_tasks:
            project = project_by_id.get(task.project_id)
            if project is not None and project.slug == TARGET_SLUG:
                summary["target_pending_rewrite_tasks"] += 1
                continue
            summary["rewrite_tasks_paused"] += 1
            if apply:
                previous_status = task.status
                task.metadata_json = _pause_task_metadata(task.metadata_json, previous_status=previous_status, now=now)
                task.status = "paused"

        workflows = (
            await session.scalars(
                select(WorkflowRunModel).where(WorkflowRunModel.status.in_(["pending", "queued", "running", "in_progress"]))
            )
        ).all()
        for workflow in workflows:
            project = project_by_id.get(workflow.project_id)
            if project is not None and project.slug == TARGET_SLUG:
                continue
            summary["workflows_paused"] += 1
            if apply:
                previous_status = workflow.status
                workflow.metadata_json = _pause_task_metadata(workflow.metadata_json, previous_status=previous_status, now=now)
                workflow.status = "paused"
                workflow.current_step = "paused_for_qingnang_focus"

        schedules = (
            await session.scalars(
                select(BookGenerationScheduleModel).where(BookGenerationScheduleModel.status == "pending")
            )
        ).all()
        for schedule in schedules:
            if schedule.project_slug == TARGET_SLUG:
                continue
            summary["schedules_cancelled"] += 1
            if apply:
                payload = dict(schedule.payload or {})
                payload["focus_pause"] = {"previous_status": schedule.status, "reason": FOCUS_REASON, "paused_at": now}
                schedule.payload = payload
                schedule.status = "cancelled"
                schedule.error_message = "Paused without data deletion while qingnang repair is prioritized."

    web_tasks_path = Path(settings.output.base_dir) / ".web_tasks.json"
    summary["web_tasks"] = _pause_web_tasks(web_tasks_path, now=now, apply=apply)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply=args.apply)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
