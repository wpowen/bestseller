"""Transactional application of an approved derived planning revision.

This module is deliberately narrow.  Review receipts are proposals until this
function has re-read the project, the authoritative pause, and the canonical
world artifact in one transaction.  It never resumes production or mutates
downstream cast/chapter rows.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, WorkflowStatus
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.services.production_control import load_control_state
from bestseller.services.projects import import_planning_artifact
from bestseller.services.world_recompile_candidate import (
    build_world_recompile_candidate,
)

_ACTIVE_WORKFLOW_STATUSES = tuple(
    status.value
    for status in (
        WorkflowStatus.PENDING,
        WorkflowStatus.QUEUED,
        WorkflowStatus.RUNNING,
        WorkflowStatus.MACHINE_BLOCKED,
    )
)
_ALLOWED_PATHS = {
    ("writing_profile", "world", "power_system_style"),
    ("growth_curve",),
}


def _path_get(value: Mapping[str, Any], path: tuple[str, ...]) -> Any:  # noqa: ANN401
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            raise ValueError(f"DERIVED_REVISION_PATH_MISSING:{'.'.join(path)}")
        current = current[key]
    return current


def _path_set(
    value: dict[str, Any], path: tuple[str, ...], replacement: Any  # noqa: ANN401
) -> None:
    current: dict[str, Any] = value
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, Mapping):
            child = {}
            current[key] = child
        current = child  # type: ignore[assignment]
    current[path[-1]] = deepcopy(replacement)


def _revision_marker(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    marker = metadata.get("derived_input_revision")
    return marker if isinstance(marker, Mapping) else None


async def _lock_project_advisory(session: AsyncSession, project_id: UUID) -> None:
    # Serialize this repair entry; the project row lock protects writes from
    # other entry points which do not participate in this advisory namespace.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('project:' || :pid, 0))"),
        {"pid": str(project_id)},
    )


async def _assert_no_live_work(session: AsyncSession, project_id: UUID) -> None:
    workflow = await session.scalar(
        select(WorkflowRunModel.id)
        .where(
            WorkflowRunModel.project_id == project_id,
            WorkflowRunModel.status.in_(_ACTIVE_WORKFLOW_STATUSES),
        )
        .limit(1)
    )
    if workflow is not None:
        raise ValueError("DERIVED_REVISION_ACTIVE_WORKFLOW")
    chapter = await session.scalar(
        select(ChapterModel.id).where(ChapterModel.project_id == project_id).limit(1)
    )
    if chapter is not None:
        raise ValueError("DERIVED_REVISION_CHAPTERS_ALREADY_EXIST")
    draft = await session.scalar(
        select(ChapterDraftVersionModel.id)
        .join(
            ChapterModel,
            ChapterModel.id == ChapterDraftVersionModel.chapter_id,
        )
        .where(ChapterModel.project_id == project_id)
        .limit(1)
    )
    if draft is not None:
        raise ValueError("DERIVED_REVISION_DRAFTS_ALREADY_EXIST")


async def apply_reviewed_derived_revision(
    session: AsyncSession,
    *,
    project_slug: str,
    report_id: UUID,
    expected_lock_version: int,
) -> dict[str, Any]:
    """Apply one independently reviewed derived revision.

    The caller owns the final commit.  A nested transaction makes all writes
    rollback together on any validation or import failure, including when the
    caller has already opened an outer transaction.
    """
    async with session.begin_nested():
        project = await session.scalar(
            select(ProjectModel)
            .where(ProjectModel.slug == project_slug)
            .with_for_update()
        )
        if project is None:
            raise ValueError("DERIVED_REVISION_PROJECT_NOT_FOUND")
        stale_lock = project.lock_version != expected_lock_version
        await _lock_project_advisory(session, project.id)

        report = await session.scalar(
            select(ReviewReportModel)
            .where(
                ReviewReportModel.id == report_id,
                ReviewReportModel.project_id == project.id,
            )
            .with_for_update()
        )
        if report is None:
            raise ValueError("DERIVED_REVISION_REPORT_NOT_FOUND")
        if (
            report.target_type != "planning_artifact"
            or report.reviewer_type != "derived_input_revision"
        ):
            raise ValueError("DERIVED_REVISION_REPORT_TARGET_MISMATCH")
        if report.verdict != "pass":
            raise ValueError("DERIVED_REVISION_REPORT_NOT_APPROVED")

        # Import lazily so the pure review contract remains usable in tests
        # before the sibling module has been merged by the main agent.
        from bestseller.services.derived_revision import (
            verify_derived_revision_report,
        )

        payload = report.structured_output
        verify_derived_revision_report(payload)
        if str(payload.get("project_id")) != str(project.id):
            raise ValueError("DERIVED_REVISION_PROJECT_MISMATCH")
        parent = payload["parent"]
        candidate = payload["candidate"]
        source = parent["source_artifact"]
        if UUID(str(source["id"])) != report.target_id:
            raise ValueError("DERIVED_REVISION_TARGET_SOURCE_MISMATCH")

        receipts = [*payload["reviews"], payload["editor_receipt"]]
        receipt_ids = [UUID(str(item["llm_run_id"])) for item in receipts]
        if len(set(receipt_ids)) != len(receipt_ids):
            raise ValueError("DERIVED_REVISION_DUPLICATE_LLM_RECEIPT")
        runs = list(
            (
                await session.scalars(
                    select(LlmRunModel).where(
                        LlmRunModel.id.in_(receipt_ids),
                        LlmRunModel.project_id == project.id,
                    )
                )
            ).all()
        )
        by_id = {run.id: run for run in runs}
        if len(by_id) != len(receipt_ids):
            raise ValueError("DERIVED_REVISION_LLM_RECEIPT_NOT_FOUND")
        for receipt in receipts:
            run = by_id[UUID(str(receipt["llm_run_id"]))]
            run_metadata = dict(run.metadata_json or {})
            expected_role = "editor" if receipt is payload["editor_receipt"] else "critic"
            if (
                run.provider != receipt.get("provider")
                or run.model_name != receipt.get("model")
                or run.finish_reason != receipt.get("finish_reason")
                or run.finish_reason in {"fallback", "length"}
                or run_metadata.get("fallback_used") is True
                or run.logical_role != expected_role
                or run.prompt_template != f"derived_input_revision_{expected_role}"
                or run.prompt_version != payload["version"]
                or (
                    receipt.get("prompt_template") is not None
                    and run.prompt_template != receipt.get("prompt_template")
                )
                or (
                    receipt.get("prompt_version") is not None
                    and run.prompt_version != receipt.get("prompt_version")
                )
            ):
                raise ValueError("DERIVED_REVISION_LLM_RECEIPT_MISMATCH")

        control = await load_control_state(session, project.id, for_update=True)
        if control.intent.value != "pause" or project.status != "paused":
            raise ValueError("DERIVED_REVISION_PAUSE_REQUIRED")
        await _assert_no_live_work(session, project.id)

        metadata = deepcopy(dict(project.metadata_json or {}))
        marker = _revision_marker(metadata)

        world = await session.scalar(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == ArtifactType.WORLD_SPEC.value,
                PlanningArtifactVersionModel.status == "approved",
            )
            .order_by(
                PlanningArtifactVersionModel.version_no.desc(),
                PlanningArtifactVersionModel.created_at.desc(),
            )
            .with_for_update()
        )
        if world is None:
            raise ValueError("DERIVED_REVISION_CANONICAL_WORLD_NOT_FOUND")
        if world.id != report.target_id:
            if (
                marker is None
                or str(marker.get("report_id")) != str(report_id)
                or str(marker.get("world_artifact_id")) != str(world.id)
            ):
                raise ValueError("DERIVED_REVISION_CANONICAL_WORLD_STALE")
        premise_id = UUID(str(parent["frozen_inputs"]["premise_artifact_id"]))
        premise = await session.scalar(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == ArtifactType.PREMISE.value,
                PlanningArtifactVersionModel.status == "approved",
            ).order_by(
                PlanningArtifactVersionModel.version_no.desc(),
                PlanningArtifactVersionModel.created_at.desc(),
            )
        )
        if premise is None:
            raise ValueError("DERIVED_REVISION_PREMISE_NOT_FOUND")
        if premise.id != premise_id:
            raise ValueError("DERIVED_REVISION_PREMISE_STALE")
        if marker is not None:
            if str(marker.get("report_id")) == str(report_id):
                latest_book = await session.scalar(
                    select(PlanningArtifactVersionModel.id)
                    .where(
                        PlanningArtifactVersionModel.project_id == project.id,
                        PlanningArtifactVersionModel.artifact_type == ArtifactType.BOOK_SPEC.value,
                        PlanningArtifactVersionModel.status == "approved",
                    )
                    .order_by(
                        PlanningArtifactVersionModel.version_no.desc(),
                        PlanningArtifactVersionModel.created_at.desc(),
                    )
                    .limit(1)
                )
                changed_paths_match = all(
                    _path_get(metadata, tuple(change["path"]))
                    == _path_get(candidate["frozen_inputs"]["metadata"], tuple(change["path"]))
                    for change in payload["changes"]
                )
                if (
                    str(marker.get("world_artifact_id")) == str(world.id)
                    and str(marker.get("book_artifact_id")) == str(latest_book)
                    and changed_paths_match
                ):
                    fresh = build_world_recompile_candidate(
                        project, premise=parent["frozen_inputs"]["premise"],
                        source_artifact=world, premise_artifact_id=str(premise.id),
                    )
                    if fresh["input_hash"] != candidate["input_hash"]:
                        raise ValueError("DERIVED_REVISION_APPLIED_INPUTS_STALE")
                    return {**dict(marker), "status": "already_applied"}
        if stale_lock:
            raise ValueError("DERIVED_REVISION_STALE_LOCK_VERSION")

        # Rebuild the parent against current source inputs.  This catches a
        # stale snapshot, metadata edit, premise replacement, or source alias
        # drift even when the receipt's own hashes are internally consistent.
        fresh_parent = build_world_recompile_candidate(
            project,
            premise=str(premise.content.get("premise", premise.content.get("text", "")))
            if isinstance(premise.content, Mapping)
            else str(premise.content),
            source_artifact=world,
            premise_artifact_id=str(premise.id),
        )
        if fresh_parent.get("input_hash") != parent.get("input_hash"):
            raise ValueError("DERIVED_REVISION_PARENT_STALE")
        if fresh_parent.get("source_hash") != parent.get("source_hash"):
            raise ValueError("DERIVED_REVISION_SOURCE_STALE")

        changes = payload.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("DERIVED_REVISION_CHANGES_MISSING")
        baseline_meta = parent["frozen_inputs"]["metadata"]
        candidate_meta = candidate["frozen_inputs"]["metadata"]
        next_metadata = deepcopy(metadata)
        for change in changes:
            path_value = change.get("path") if isinstance(change, Mapping) else None
            path = tuple(str(part) for part in path_value) if isinstance(path_value, list) else ()
            if path not in _ALLOWED_PATHS:
                raise ValueError("DERIVED_REVISION_PATH_NOT_ALLOWED")
            before = _path_get(baseline_meta, path)
            after = _path_get(candidate_meta, path)
            if change.get("before") != before or change.get("after") != after or before == after:
                raise ValueError("DERIVED_REVISION_CHANGE_MISMATCH")
            if _path_get(metadata, path) != before:
                raise ValueError("DERIVED_REVISION_CURRENT_INPUT_STALE")
            _path_set(next_metadata, path, after)

        # Make the revised source visible to the existing artifact importer so
        # its truth-version bookkeeping is based on the new inputs.  The
        # importer may add its own metadata keys; those are merged below.
        project.metadata_json = next_metadata
        book_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.BOOK_SPEC,
                content=candidate["candidate_book"],
            ),
        )
        world_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.WORLD_SPEC,
                content=candidate["candidate_world"],
            ),
        )
        marker = {
            "status": "applied",
            "report_id": str(report_id),
            "parent_input_hash": parent["input_hash"],
            "candidate_hash": candidate["candidate_hash"],
            "source_world_id": str(world.id),
            "book_artifact_id": str(book_artifact.id),
            "world_artifact_id": str(world_artifact.id),
            "projection_pending": True,
            "downstream_repair_required": (
                "rebuild planning projections before chapter materialization"
            ),
        }
        committed_metadata = deepcopy(dict(project.metadata_json or {}))
        committed_metadata["derived_input_revision"] = marker
        committed_metadata["derived_revision_projection_pending"] = True
        committed_metadata["planning_status"] = "needs_replan"
        committed_metadata["generation_resume_blocked_until_repair_audit"] = True
        committed_metadata["planning_projection_state"] = {
            "status": "needs_replan",
            "reason": "derived_input_revision_applied",
            "source_world_id": str(world_artifact.id),
        }
        project.metadata_json = committed_metadata
        project.lock_version += 1
        await session.flush()
        return {"status": "applied", **marker, "project_id": str(project.id)}


__all__ = ["apply_reviewed_derived_revision"]
