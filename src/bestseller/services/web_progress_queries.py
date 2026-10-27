"""Small read projections for frequently polled Web progress."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import WorkflowRunModel, WorkflowStepRunModel


async def load_execution_workflow_summary(
    session: AsyncSession,
    project_id: UUID,
) -> dict[str, object]:
    """Preserve all run statuses while bounding events and excluding payloads.

    The full workflow endpoint continues to own historical metadata and inputs.
    Step timestamps are aggregated independently of the recent-event window so
    an older run with a fresh step still supplies the same liveness evidence.
    """
    step_stats = (
        select(
            WorkflowStepRunModel.workflow_run_id.label("run_id"),
            func.max(
                case(
                    (
                        WorkflowStepRunModel.created_at
                        <= datetime.now(UTC) + timedelta(seconds=300),
                        WorkflowStepRunModel.created_at,
                    ),
                    else_=None,
                )
            ).label("latest_step_at"),
            func.sum(case((WorkflowStepRunModel.status == "failed", 1), else_=0)).label(
                "failed_step_count"
            ),
        )
        .join(WorkflowRunModel, WorkflowRunModel.id == WorkflowStepRunModel.workflow_run_id)
        .where(WorkflowRunModel.project_id == project_id)
        .group_by(WorkflowStepRunModel.workflow_run_id)
        .subquery()
    )
    result = await session.execute(
        select(
            WorkflowRunModel.id.label("workflow_run_id"),
            WorkflowRunModel.workflow_type,
            WorkflowRunModel.status,
            WorkflowRunModel.current_step,
            WorkflowRunModel.error_message,
            WorkflowRunModel.created_at,
            WorkflowRunModel.updated_at,
            WorkflowRunModel.metadata_json["planner_last_heartbeat_at"]
            .as_string()
            .label("planner_heartbeat_at"),
            step_stats.c.latest_step_at,
            step_stats.c.failed_step_count,
        )
        .outerjoin(step_stats, step_stats.c.run_id == WorkflowRunModel.id)
        .where(WorkflowRunModel.project_id == project_id)
        .order_by(WorkflowRunModel.created_at.desc())
        .execution_options(autoflush=False)
    )
    runs = []
    for item in result.mappings():
        row = dict(item)
        row["metadata"] = {"planner_last_heartbeat_at": row.pop("planner_heartbeat_at")}
        row["failed_step_count"] = int(row["failed_step_count"] or 0)
        for key in ("created_at", "updated_at", "latest_step_at"):
            row[key] = row[key].isoformat().replace("+00:00", "Z") if row[key] else None
        runs.append(row)
    recent = await session.execute(
        select(
            WorkflowStepRunModel.step_name.label("name"),
            WorkflowStepRunModel.status,
            WorkflowStepRunModel.created_at,
            WorkflowRunModel.workflow_type.label("run_type"),
            WorkflowStepRunModel.error_message.label("error"),
        )
        .join(WorkflowRunModel, WorkflowRunModel.id == WorkflowStepRunModel.workflow_run_id)
        .where(WorkflowRunModel.project_id == project_id)
        .order_by(
            WorkflowStepRunModel.created_at.desc(),
            WorkflowRunModel.created_at.desc(),
            WorkflowStepRunModel.step_order.asc(),
        )
        .limit(18)
        .execution_options(autoflush=False)
    )
    events = []
    for item in recent.mappings():
        row = dict(item)
        row["created_at"] = (
            row["created_at"].isoformat().replace("+00:00", "Z") if row["created_at"] else None
        )
        events.append(row)
    return {"runs": runs, "run_count": len(runs), "recent_steps": events}
