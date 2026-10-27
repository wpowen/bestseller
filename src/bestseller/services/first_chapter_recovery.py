"""One revised-source outline window, then materialization, never whole-book dispatch."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel
from bestseller.services.outline_revision_recovery import rearm_outline_recovery_for_revision
from bestseller.services.pipelines import (
    _expand_volume_plan_into_rolling_windows,
    _merge_progressive_outline_batch,
    _outline_content_chapters,
    _release_approved_outline_replan_gate,
)
from bestseller.services.planner import _normalize_volume_plan_payload, generate_volume_plan
from bestseller.services.production_control import load_control_state
from bestseller.services.projects import import_planning_artifact
from bestseller.services.rolling_outline import load_rolling_outline_plan
from bestseller.services.workflows import (
    get_latest_planning_artifact,
    materialize_latest_chapter_outline_batch,
    materialize_latest_narrative_graph,
    materialize_latest_narrative_tree,
)
from bestseller.settings import AppSettings


def merge_current_attempt_outline(
    artifacts: list[PlanningArtifactVersionModel],
    *,
    expected_numbers: set[int],
    project_id: UUID,
    prior_version: int,
) -> dict[str, Any]:
    """Caller supplies ONLY artifact IDs returned by this successful planner run."""
    if not artifacts:
        raise ValueError("FIRST_CHAPTER_NO_CURRENT_OUTLINE")
    merged: list[Any] = []
    seen_numbers: set[int] = set()
    for artifact in sorted(artifacts, key=lambda a: a.version_no):
        if (
            artifact.project_id != project_id
            or artifact.status != "approved"
            or artifact.artifact_type != ArtifactType.VOLUME_CHAPTER_OUTLINE.value
            or artifact.version_no <= prior_version
        ):
            raise ValueError("FIRST_CHAPTER_OLD_OR_UNAPPROVED_OUTLINE")
        chapters = _outline_content_chapters(artifact.content)
        if not chapters:
            raise ValueError("FIRST_CHAPTER_EMPTY_OUTLINE")
        numbers = [c.get("chapter_number") for c in chapters]
        if len(set(numbers)) != len(numbers) or seen_numbers.intersection(numbers):
            raise ValueError("FIRST_CHAPTER_DUPLICATE_OUTLINE")
        if any(c.get("chapter_number") not in expected_numbers for c in chapters):
            raise ValueError("FIRST_CHAPTER_OUTLINE_OUTSIDE_WINDOW")
        seen_numbers.update(numbers)
        merged = _merge_progressive_outline_batch(merged, chapters)
    if {c.get("chapter_number") for c in merged} != expected_numbers or len(merged) != len(
        expected_numbers
    ):
        raise ValueError("FIRST_CHAPTER_INCOMPLETE_WINDOW")
    return {"batch_name": "revised-source-first-window", "chapters": merged}


async def recover_first_outline_window(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_slug: str,
    report_id: UUID,
) -> dict[str, Any]:
    """One durable attempt while operator pause remains effective.

    The existing planner performs deterministic and semantic promotion. A
    failure consumes this attempt and retains the pause; restarting this call
    cannot silently repeat a costly model run.
    """
    project = await session.scalar(
        select(ProjectModel).where(ProjectModel.slug == project_slug).with_for_update()
    )
    if project is None:
        raise ValueError("FIRST_CHAPTER_PROJECT_MISSING")
    control = await load_control_state(session, project.id, for_update=True)
    if not control.halted or project.status != "paused":
        raise ValueError("FIRST_CHAPTER_PAUSE_REQUIRED")
    metadata = deepcopy(dict(project.metadata_json or {}))
    marker = metadata.get("derived_input_revision") or {}
    if (
        marker.get("report_id") != str(report_id)
        or marker.get("projection_pending") is not False
        or marker.get("projection_status") != "completed"
    ):
        raise ValueError("FIRST_CHAPTER_PROJECTION_REQUIRED")
    previous = metadata.get("first_chapter_recovery") or {}
    if previous.get("report_id") == str(report_id):
        raise ValueError("FIRST_CHAPTER_ATTEMPT_ALREADY_RECORDED")
    inputs = {}
    for artifact_type, key in (
        (ArtifactType.BOOK_SPEC, "book_artifact_id"),
        (ArtifactType.WORLD_SPEC, "world_artifact_id"),
        (ArtifactType.CAST_SPEC, "cast_artifact_id"),
        (ArtifactType.VOLUME_PLAN, "volume_artifact_id"),
    ):
        artifact = await get_latest_planning_artifact(
            session, project_id=project.id, artifact_type=artifact_type
        )
        if artifact is None or artifact.status != "approved" or str(artifact.id) != marker.get(key):
            raise ValueError(f"FIRST_CHAPTER_STALE_PROJECTION:{artifact_type.value}")
        inputs[artifact_type.value] = deepcopy(artifact.content)
    plans = _normalize_volume_plan_payload(inputs["volume_plan"])
    windows = _expand_volume_plan_into_rolling_windows(
        plans, window_size=settings.pipeline.rolling_outline_window_size
    )
    first = windows[0]
    expected = set(range(int(first["start_chapter_number"]), int(first["end_chapter_number"]) + 1))
    if min(expected) != 1 or project.current_chapter_number:
        raise ValueError("FIRST_CHAPTER_NOT_AN_UNWRITTEN_FIRST_WINDOW")
    # Existing rolling authority must agree with the execution window; do not
    # invent a successful migration or modify the immutable creation snapshot.
    macro_data = metadata.get("macro_outline_plan")
    rolling_data = metadata.get("rolling_outline_plan")
    if not isinstance(macro_data, dict) or not isinstance(rolling_data, dict):
        raise ValueError("FIRST_CHAPTER_ROLLING_AUTHORITY_MISSING")
    _, rolling = load_rolling_outline_plan(
        macro_data,
        rolling_data,
        source_snapshot_hash=metadata["book_design_snapshot_hash"],
    )
    if rolling.window_start != min(expected) or rolling.window_end != max(expected):
        raise ValueError("FIRST_CHAPTER_ROLLING_WINDOW_MISMATCH")
    await rearm_outline_recovery_for_revision(session, project_slug, report_id)
    prior_version = int(
        await session.scalar(
            select(func.max(PlanningArtifactVersionModel.version_no)).where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type
                == ArtifactType.VOLUME_CHAPTER_OUTLINE.value,
            )
        )
        or 0
    )
    metadata = deepcopy(dict(project.metadata_json or {}))
    metadata.update(
        {
            "outline_replan_in_progress": True,
            "outline_replan_prior_outline_version": prior_version,
            "first_chapter_recovery": {
                "report_id": str(report_id),
                "status": "planning",
                "window": sorted(expected),
            },
        }
    )
    project.metadata_json = metadata
    await session.commit()
    try:
        bounded_settings = settings.model_copy(deep=True)
        bounded_settings.pipeline.outline_commercial_judge_repair_rounds = min(
            settings.pipeline.outline_commercial_judge_repair_rounds, 1
        )
        bounded_settings.pipeline.chapter_outline_repair_attempts = min(
            settings.pipeline.chapter_outline_repair_attempts, 1
        )
        async with asyncio.timeout(1200):
            result = await generate_volume_plan(
                session,
                bounded_settings,
                project_slug,
                int(first["volume_number"]),
                book_spec=inputs["book_spec"],
                world_spec=inputs["world_spec"],
                cast_spec=inputs["cast_spec"],
                volume_plan=[first],
                requested_by="derived_revision_first_window",
            )
        await session.refresh(project)
        semantic = (project.metadata_json or {}).get("outline_semantic_gate_report") or {}
        approved = (project.metadata_json or {}).get("outline_commercial_last_approved") or {}
        if semantic.get("promotion_allowed") is not True or approved.get("workflow_run_id") != str(
            result.workflow_run_id
        ):
            raise ValueError("FIRST_CHAPTER_SEMANTIC_PROMOTION_MISSING")
        ids = [
            a.artifact_id
            for a in result.artifacts
            if a.artifact_type == ArtifactType.VOLUME_CHAPTER_OUTLINE
        ]
        artifacts = list(
            await session.scalars(
                select(PlanningArtifactVersionModel).where(PlanningArtifactVersionModel.id.in_(ids))
            )
        )
        merged = merge_current_attempt_outline(
            artifacts, expected_numbers=expected, project_id=project.id, prior_version=prior_version
        )
        merged["_meta"] = {
            "revision_report_id": str(report_id),
            "source_workflow_id": str(result.workflow_run_id),
            "source_outline_artifact_ids": [str(i) for i in ids],
        }
        batch = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                content=merged,
                source_run_id=result.workflow_run_id,
            ),
        )
        await materialize_latest_chapter_outline_batch(
            session, project_slug, requested_by="derived_revision_first_window"
        )
        await materialize_latest_narrative_graph(
            session, project_slug, requested_by="derived_revision_first_window"
        )
        await materialize_latest_narrative_tree(
            session, project_slug, requested_by="derived_revision_first_window"
        )
        if not _release_approved_outline_replan_gate(
            project, max(artifacts, key=lambda a: a.version_no)
        ):
            raise ValueError("FIRST_CHAPTER_OUTLINE_RELEASE_REFUSED")
        # Structural success is distinct from permission to dispatch the book.
        project.status = "paused"
        meta = deepcopy(dict(project.metadata_json or {}))
        for key in (
            "outline_replan_required",
            "outline_replan_retry_exhausted",
            "outline_replan_auto_retry_exhausted",
        ):
            meta.pop(key, None)
        meta["first_chapter_recovery"] = {
            "report_id": str(report_id),
            "status": "outline_ready",
            "window": sorted(expected),
            "batch_id": str(batch.id),
            "workflow_run_id": str(result.workflow_run_id),
        }
        project.metadata_json = meta
        await session.commit()
        return dict(meta["first_chapter_recovery"])
    except Exception as exc:
        await session.rollback()
        await session.refresh(project)
        meta = deepcopy(dict(project.metadata_json or {}))
        meta.update(
            {
                "outline_replan_in_progress": False,
                "outline_replan_retry_exhausted": True,
                "planning_status": "needs_replan",
                "generation_resume_blocked_until_repair_audit": True,
                "first_chapter_recovery": {
                    "report_id": str(report_id),
                    "status": "failed",
                    "error": str(exc)[:2000],
                    "window": sorted(expected),
                },
            }
        )
        project.metadata_json = meta
        project.status = "paused"
        # Planner persists its own failed run. No artificial successful run.
        await session.commit()
        raise
