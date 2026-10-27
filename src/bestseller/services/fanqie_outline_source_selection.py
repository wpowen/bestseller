"""Select an ordinary full outline only after actual current hard admission."""

from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionBudgetModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import AppSettings

SELECTION_KEY = "fanqie_outline_source_selection"
HISTORY_KEY = "fanqie_outline_source_selection_history"


def _need(value: object, code: str) -> None:
    if not value:
        raise ValueError("fanqie_outline_selection:" + code)


async def _owner(slug: str) -> None:
    from bestseller.services.workflows import _PIPELINE_OWNER

    binding = _PIPELINE_OWNER.get()
    _need(binding is not None and binding[0] == slug, "acquired_native_owner_required")
    await binding[2]()


async def admit_and_select_outline_source(
    session: AsyncSession, settings: AppSettings, *, project_id: UUID | str,
    artifact_id: UUID | str, materialization_workflow_id: UUID | str,
    requested_by: str = "system",
) -> dict[str, Any]:
    """Pay/reuse the native assessor before locks; atomically bind its source.

    Ordinary selection never claims full-revision producer identity. The common
    reader still verifies the real paid grade and every current source binding.
    Caller owns the final marker commit. No provider work occurs under locks.
    """
    from bestseller.services.budget import current_execution_scope
    from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
    from bestseller.services.materialized_outline_admission_read import (
        require_materialized_outline_source_admission,
    )
    from bestseller.services.materialized_outline_source_recovery import (
        _read_sources,
        admission_workflow_id,
        assess_materialized_outline_source,
    )
    from bestseller.services.retry_ledger import (
        assert_execution_scope_open,
        project_execution_scope,
    )

    _need(not session.new and not session.dirty and not session.deleted, "clean_session_required")
    project_id, artifact_id = UUID(str(project_id)), UUID(str(artifact_id))
    materialization_workflow_id = UUID(str(materialization_workflow_id))
    project = await session.get(ProjectModel, project_id, populate_existing=True)
    _need(project is not None, "project_missing")
    slug = project.slug
    await _owner(slug)
    _need(current_execution_scope() == project_execution_scope(project), "original_scope_required")
    receipt = await assess_materialized_outline_source(
        session, settings, project_slug=slug, artifact_id=artifact_id,
        materialization_workflow_id=materialization_workflow_id, requested_by=requested_by,
    )
    _need(receipt.get("draft_eligible") is True, "current_hard_admission_rejected")
    await _owner(slug)
    # Control -> Project -> Control covers the legacy first-STOP/FK race.
    await session.scalar(select(BookProductionControlModel).where(
        BookProductionControlModel.project_id == project_id).with_for_update()
        .execution_options(populate_existing=True))
    project = await session.scalar(select(ProjectModel).where(ProjectModel.id == project_id)
        .with_for_update().execution_options(populate_existing=True))
    control = await session.scalar(select(BookProductionControlModel).where(
        BookProductionControlModel.project_id == project_id).with_for_update()
        .execution_options(populate_existing=True))
    _need(project is not None and (control is None or control.desired_state == "run"), "halted")
    scope = project_execution_scope(project)
    _need(current_execution_scope() == scope, "scope_changed")
    chapters = list(await session.scalars(select(ChapterModel).where(
        ChapterModel.project_id == project_id).order_by(
            ChapterModel.chapter_number, ChapterModel.id)
        .with_for_update().execution_options(populate_existing=True)))
    numbers = [row.chapter_number for row in chapters]
    _need(numbers == list(range(1, project.target_chapters + 1)),
          "complete_chapter_coverage")
    scenes = list(await session.scalars(select(SceneCardModel).where(
        SceneCardModel.project_id == project_id).order_by(SceneCardModel.chapter_id,
            SceneCardModel.scene_number, SceneCardModel.id)
        .with_for_update().execution_options(populate_existing=True)))
    drafts = list(await session.scalars(select(ChapterDraftVersionModel).where(
        ChapterDraftVersionModel.project_id == project_id).with_for_update()
        .execution_options(populate_existing=True)))
    artifact = await session.scalar(select(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.id == artifact_id).with_for_update()
        .execution_options(populate_existing=True))
    await session.scalar(select(ExecutionBudgetModel).where(ExecutionBudgetModel.scope_id == scope)
        .with_for_update().execution_options(populate_existing=True))
    await assert_execution_scope_open(session, scope)
    stable_id = admission_workflow_id(project_id, artifact_id)
    await session.scalar(select(WorkflowRunModel.id).where(WorkflowRunModel.id == stable_id)
        .with_for_update())
    fresh, source, _candidate, _foundation, binding = await _read_sources(
        session, settings, project_slug=slug, artifact_id=artifact_id,
        materialization_workflow_id=materialization_workflow_id,
    )
    _need(artifact is not None and artifact.project_id == project_id
          and content_hash(artifact.content) == content_hash(source.content), "source_changed")
    await _owner(slug)
    verified = await require_materialized_outline_source_admission(
        session, settings, project=fresh, artifact=source, chapter=chapters[0],
        scenes=[row for row in scenes if row.chapter_id == chapters[0].id],
    )
    _need(isinstance(verified, dict) and verified.get("workflow_run_id") == str(stable_id)
          and (verified.get("source_admission") or {}).get("draft_eligible") is True,
          "verified_current_hard_admission_required")
    _need(all(content_hash(verified.get(key)) == content_hash(value)
              for key, value in binding.items()), "admission_binding_changed")
    markers = {}
    for chapter in chapters:
        marker = {"artifact_id": str(artifact_id), "workflow_run_id": str(stable_id),
            "projection_hash": outline_prefix_projection_hash(chapter,
                [row for row in scenes if row.chapter_id == chapter.id]),
            "source_content_sha256": content_hash(source.content)}
        metadata = deepcopy(chapter.metadata_json or {})
        _need(not any(key in metadata for key in (
            "outline_source_admission", "outline_prefix_readiness")),
            "conflicting_selection_kinds")
        previous = metadata.get(SELECTION_KEY)
        if previous != marker:
            if previous is not None:
                from bestseller.services.rewrite_outline_binding import validate_source_plan_binding

                validate_source_plan_binding(previous)
            _need(not drafts, "existing_draft_source_cannot_be_reselected")
            history = metadata.get(HISTORY_KEY, [])
            _need(isinstance(history, list), "selection_history_malformed")
            history = deepcopy(history)
            if previous is not None and previous not in history:
                history.append(previous)
            chapter.metadata_json = {**metadata, HISTORY_KEY: history, SELECTION_KEY: marker}
        markers[str(chapter.id)] = marker
    await session.flush()
    return {"schema": "fanqie-outline-source-selection.v1", "project_id": str(project_id),
        "source_artifact_id": str(artifact_id), "workflow_run_id": str(stable_id),
        "selection_markers": markers, "dispatch_authorized": False,
        "adoption_authorized": False, "credit_released": False}
