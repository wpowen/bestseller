from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, WorkflowStatus
from bestseller.domain.narrative import NarrativeGraphMaterializationResult
from bestseller.domain.narrative_tree import NarrativeTreeMaterializationResult
from bestseller.domain.project import ChapterCreate, SceneCardCreate
from bestseller.domain.story_bible import StoryBibleMaterializationResult
from bestseller.domain.workflow import (
    ChapterOutlineBatchInput,
    WorkflowMaterializationResult,
)
from bestseller.infra.db.models import (
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.services.projects import create_chapter, create_scene_card, get_project_by_slug
from bestseller.services.narrative import rebuild_narrative_graph
from bestseller.services.narrative_tree import rebuild_narrative_tree
from bestseller.services.retrieval import refresh_story_bible_retrieval_index
from bestseller.services.story_bible import (
    apply_book_spec,
    upsert_cast_spec,
    upsert_volume_plan,
    upsert_world_spec,
)
from bestseller.services.world_expansion import refresh_world_expansion_boundaries
from bestseller.settings import load_settings


WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE = "materialize_chapter_outline_batch"
WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE = "materialize_story_bible"
WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH = "materialize_narrative_graph"
WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_TREE = "materialize_narrative_tree"


async def create_workflow_run(
    session: AsyncSession,
    *,
    project_id: UUID | None,
    workflow_type: str,
    status: WorkflowStatus,
    scope_type: str | None,
    scope_id: UUID | None,
    requested_by: str,
    current_step: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkflowRunModel:
    workflow_run = WorkflowRunModel(
        project_id=project_id,
        workflow_type=workflow_type,
        status=status.value,
        scope_type=scope_type,
        scope_id=scope_id,
        requested_by=requested_by,
        current_step=current_step,
        metadata_json=metadata or {},
    )
    session.add(workflow_run)
    await session.flush()
    return workflow_run


async def create_workflow_step_run(
    session: AsyncSession,
    *,
    workflow_run_id: UUID,
    step_name: str,
    step_order: int,
    status: WorkflowStatus,
    input_ref: dict[str, Any] | None = None,
    output_ref: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> WorkflowStepRunModel:
    step_run = WorkflowStepRunModel(
        workflow_run_id=workflow_run_id,
        step_name=step_name,
        step_order=step_order,
        status=status.value,
        input_ref=input_ref or {},
        output_ref=output_ref or {},
        error_message=error_message,
    )
    session.add(step_run)
    await session.flush()
    return step_run


async def list_workflow_runs(
    session: AsyncSession,
    project_slug: str,
) -> list[WorkflowRunModel]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    result = await session.scalars(
        select(WorkflowRunModel)
        .where(WorkflowRunModel.project_id == project.id)
        .order_by(WorkflowRunModel.created_at.desc())
    )
    return list(result)


async def get_workflow_run(
    session: AsyncSession,
    workflow_run_id: UUID,
) -> WorkflowRunModel | None:
    return await session.get(WorkflowRunModel, workflow_run_id)


async def get_latest_planning_artifact(
    session: AsyncSession,
    *,
    project_id: UUID,
    artifact_type: ArtifactType,
) -> PlanningArtifactVersionModel | None:
    return await session.scalar(
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == artifact_type.value,
        )
        .order_by(
            PlanningArtifactVersionModel.version_no.desc(),
            PlanningArtifactVersionModel.created_at.desc(),
        )
        .limit(1)
    )


async def materialize_chapter_outline_batch(
    session: AsyncSession,
    project_slug: str,
    batch: ChapterOutlineBatchInput,
    *,
    requested_by: str = "system",
    source_artifact_id: UUID | None = None,
) -> WorkflowMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE,
        status=WorkflowStatus.RUNNING,
        scope_type="planning_artifact" if source_artifact_id is not None else "project",
        scope_id=source_artifact_id or project.id,
        requested_by=requested_by,
        current_step="validate_outline_batch",
        metadata={
            "batch_name": batch.batch_name,
            "chapter_count": len(batch.chapters),
            "source_artifact_id": str(source_artifact_id) if source_artifact_id else None,
        },
    )

    step_order = 1
    chapters_created = 0
    scenes_created = 0
    current_step_name = "validate_outline_batch"

    try:
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            input_ref={
                "batch_name": batch.batch_name,
                "chapter_count": len(batch.chapters),
            },
        )
        step_order += 1

        for chapter_outline in batch.chapters:
            current_step_name = f"create_chapter_{chapter_outline.chapter_number}"
            workflow_run.current_step = current_step_name

            chapter = await create_chapter(
                session,
                project_slug,
                ChapterCreate(
                    chapter_number=chapter_outline.chapter_number,
                    title=chapter_outline.title,
                    chapter_goal=chapter_outline.chapter_goal,
                    opening_situation=chapter_outline.opening_situation,
                    main_conflict=chapter_outline.main_conflict,
                    hook_type=chapter_outline.hook_type,
                    hook_description=chapter_outline.hook_description,
                    volume_number=chapter_outline.volume_number,
                    target_word_count=chapter_outline.target_word_count,
                ),
            )
            chapters_created += 1
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                input_ref={
                    "chapter_number": chapter_outline.chapter_number,
                    "scene_count": len(chapter_outline.scenes),
                },
                output_ref={
                    "chapter_id": str(chapter.id),
                    "chapter_number": chapter.chapter_number,
                },
            )
            step_order += 1

            for scene_outline in chapter_outline.scenes:
                current_step_name = (
                    f"create_scene_{chapter_outline.chapter_number}_{scene_outline.scene_number}"
                )
                workflow_run.current_step = current_step_name

                scene = await create_scene_card(
                    session,
                    project_slug,
                    chapter_outline.chapter_number,
                    SceneCardCreate(
                        scene_number=scene_outline.scene_number,
                        scene_type=scene_outline.scene_type,
                        title=scene_outline.title,
                        time_label=scene_outline.time_label,
                        participants=scene_outline.participants,
                        purpose=scene_outline.purpose,
                        entry_state=scene_outline.entry_state,
                        exit_state=scene_outline.exit_state,
                        target_word_count=scene_outline.target_word_count,
                    ),
                )
                scenes_created += 1
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name=current_step_name,
                    step_order=step_order,
                    status=WorkflowStatus.COMPLETED,
                    input_ref={
                        "chapter_number": chapter_outline.chapter_number,
                        "scene_number": scene_outline.scene_number,
                    },
                    output_ref={
                        "scene_id": str(scene.id),
                        "scene_number": scene.scene_number,
                    },
                )
                step_order += 1

        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "chapters_created": chapters_created,
            "scenes_created": scenes_created,
        }
        await session.flush()

        return WorkflowMaterializationResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            batch_name=batch.batch_name,
            chapters_created=chapters_created,
            scenes_created=scenes_created,
            source_artifact_id=source_artifact_id,
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
        raise


async def materialize_latest_chapter_outline_batch(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
) -> WorkflowMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    artifact = await get_latest_planning_artifact(
        session,
        project_id=project.id,
        artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
    )
    if artifact is None:
        raise ValueError(
            f"Project '{project_slug}' does not have a stored chapter outline batch artifact."
        )

    batch = ChapterOutlineBatchInput.model_validate(artifact.content)
    return await materialize_chapter_outline_batch(
        session,
        project_slug,
        batch,
        requested_by=requested_by,
        source_artifact_id=artifact.id,
    )


async def materialize_story_bible(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
    book_spec_content: dict[str, Any] | None = None,
    world_spec_content: dict[str, Any] | None = None,
    cast_spec_content: dict[str, Any] | None = None,
    volume_plan_content: dict[str, Any] | list[dict[str, Any]] | None = None,
    source_artifact_ids: dict[str, UUID] | None = None,
) -> StoryBibleMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    artifact_ids = dict(source_artifact_ids or {})
    requested_payloads = {
        "book_spec": book_spec_content,
        "world_spec": world_spec_content,
        "cast_spec": cast_spec_content,
        "volume_plan": volume_plan_content,
    }
    applied_artifacts = [name for name, payload in requested_payloads.items() if payload is not None]
    if not applied_artifacts:
        raise ValueError("No story bible content was provided.")

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        status=WorkflowStatus.RUNNING,
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="load_story_bible",
        metadata={
            "project_slug": project_slug,
            "applied_artifacts": applied_artifacts,
            "source_artifact_ids": {key: str(value) for key, value in artifact_ids.items()},
        },
    )

    step_order = 1
    counts = {
        "world_rules_upserted": 0,
        "locations_upserted": 0,
        "factions_upserted": 0,
        "characters_upserted": 0,
        "relationships_upserted": 0,
        "state_snapshots_created": 0,
        "volumes_upserted": 0,
        "world_backbones_upserted": 0,
        "volume_frontiers_upserted": 0,
        "deferred_reveals_upserted": 0,
        "expansion_gates_upserted": 0,
    }
    current_step_name = "load_story_bible"

    try:
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            input_ref={
                "applied_artifacts": applied_artifacts,
                "source_artifact_ids": {key: str(value) for key, value in artifact_ids.items()},
            },
        )
        step_order += 1

        if book_spec_content is not None:
            current_step_name = "apply_book_spec"
            workflow_run.current_step = current_step_name
            await apply_book_spec(session, project, book_spec_content)
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "artifact_type": ArtifactType.BOOK_SPEC.value,
                    "source_artifact_id": str(artifact_ids["book_spec"]) if "book_spec" in artifact_ids else None,
                },
            )
            step_order += 1

        if world_spec_content is not None:
            current_step_name = "apply_world_spec"
            workflow_run.current_step = current_step_name
            world_counts = await upsert_world_spec(session, project, world_spec_content)
            for key, value in world_counts.items():
                counts[key] += value
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    **world_counts,
                    "artifact_type": ArtifactType.WORLD_SPEC.value,
                    "source_artifact_id": str(artifact_ids["world_spec"]) if "world_spec" in artifact_ids else None,
                },
            )
            step_order += 1

        if cast_spec_content is not None:
            current_step_name = "apply_cast_spec"
            workflow_run.current_step = current_step_name
            cast_counts = await upsert_cast_spec(session, project, cast_spec_content)
            for key, value in cast_counts.items():
                counts[key] += value
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    **cast_counts,
                    "artifact_type": ArtifactType.CAST_SPEC.value,
                    "source_artifact_id": str(artifact_ids["cast_spec"]) if "cast_spec" in artifact_ids else None,
                },
            )
            step_order += 1

        if volume_plan_content is not None:
            current_step_name = "apply_volume_plan"
            workflow_run.current_step = current_step_name
            volume_counts = await upsert_volume_plan(session, project, volume_plan_content)
            for key, value in volume_counts.items():
                counts[key] += value
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    **volume_counts,
                    "artifact_type": ArtifactType.VOLUME_PLAN.value,
                    "source_artifact_id": str(artifact_ids["volume_plan"]) if "volume_plan" in artifact_ids else None,
                },
            )
            step_order += 1

        if any(payload is not None for payload in (book_spec_content, world_spec_content, cast_spec_content, volume_plan_content)):
            current_step_name = "refresh_world_expansion_boundaries"
            workflow_run.current_step = current_step_name
            boundary_counts = await refresh_world_expansion_boundaries(session, project=project)
            for key, value in boundary_counts.items():
                counts[key] += value
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref=boundary_counts,
            )
            step_order += 1

        if any(payload is not None for payload in (world_spec_content, cast_spec_content, volume_plan_content)):
            current_step_name = "refresh_story_bible_retrieval"
            workflow_run.current_step = current_step_name
            retrieval_chunk_count = await refresh_story_bible_retrieval_index(session, load_settings(), project.id)
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={"retrieval_chunk_count": retrieval_chunk_count},
            )
            step_order += 1

        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            **counts,
        }
        await session.flush()

        return StoryBibleMaterializationResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            applied_artifacts=applied_artifacts,
            source_artifact_ids=artifact_ids,
            **counts,
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
        raise


async def materialize_latest_story_bible(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
) -> StoryBibleMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    artifacts: dict[str, PlanningArtifactVersionModel] = {}
    for artifact_type in (
        ArtifactType.BOOK_SPEC,
        ArtifactType.WORLD_SPEC,
        ArtifactType.CAST_SPEC,
        ArtifactType.VOLUME_PLAN,
    ):
        artifact = await get_latest_planning_artifact(
            session,
            project_id=project.id,
            artifact_type=artifact_type,
        )
        if artifact is not None:
            artifacts[artifact_type.value] = artifact

    if not artifacts:
        raise ValueError(f"Project '{project_slug}' does not have any stored story bible artifacts.")

    return await materialize_story_bible(
        session,
        project_slug,
        requested_by=requested_by,
        book_spec_content=artifacts.get(ArtifactType.BOOK_SPEC.value).content
        if ArtifactType.BOOK_SPEC.value in artifacts
        else None,
        world_spec_content=artifacts.get(ArtifactType.WORLD_SPEC.value).content
        if ArtifactType.WORLD_SPEC.value in artifacts
        else None,
        cast_spec_content=artifacts.get(ArtifactType.CAST_SPEC.value).content
        if ArtifactType.CAST_SPEC.value in artifacts
        else None,
        volume_plan_content=artifacts.get(ArtifactType.VOLUME_PLAN.value).content
        if ArtifactType.VOLUME_PLAN.value in artifacts
        else None,
        source_artifact_ids={key: artifact.id for key, artifact in artifacts.items()},
    )


async def materialize_narrative_graph(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
    volume_plan_content: dict[str, Any] | list[dict[str, Any]] | None = None,
    source_artifact_ids: dict[str, UUID] | None = None,
) -> NarrativeGraphMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    artifact_ids = dict(source_artifact_ids or {})
    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH,
        status=WorkflowStatus.RUNNING,
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="load_narrative_sources",
        metadata={
            "project_slug": project_slug,
            "source_artifact_ids": {key: str(value) for key, value in artifact_ids.items()},
        },
    )

    step_order = 1
    current_step_name = "load_narrative_sources"
    try:
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            input_ref={
                "source_artifact_ids": {key: str(value) for key, value in artifact_ids.items()},
                "uses_volume_plan": volume_plan_content is not None,
            },
        )
        step_order += 1

        current_step_name = "rebuild_narrative_graph"
        workflow_run.current_step = current_step_name
        counts = await rebuild_narrative_graph(
            session,
            project=project,
            volume_plan_content=volume_plan_content,
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref=counts,
        )
        step_order += 1

        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            **counts,
        }
        await session.flush()

        return NarrativeGraphMaterializationResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            source_artifact_ids=artifact_ids,
            **counts,
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
        raise


async def materialize_latest_narrative_graph(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
) -> NarrativeGraphMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    source_artifact_ids: dict[str, UUID] = {}
    volume_plan_content = None
    volume_plan_artifact = await get_latest_planning_artifact(
        session,
        project_id=project.id,
        artifact_type=ArtifactType.VOLUME_PLAN,
    )
    if volume_plan_artifact is not None:
        volume_plan_content = volume_plan_artifact.content
        source_artifact_ids[ArtifactType.VOLUME_PLAN.value] = volume_plan_artifact.id

    return await materialize_narrative_graph(
        session,
        project_slug,
        requested_by=requested_by,
        volume_plan_content=volume_plan_content,
        source_artifact_ids=source_artifact_ids,
    )


async def materialize_narrative_tree(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
) -> NarrativeTreeMaterializationResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_TREE,
        status=WorkflowStatus.RUNNING,
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="rebuild_narrative_tree",
        metadata={"project_slug": project_slug},
    )
    step_order = 1
    current_step_name = "rebuild_narrative_tree"
    try:
        counts = await rebuild_narrative_tree(session, project=project)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref=counts,
        )
        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            **counts,
        }
        await session.flush()
        return NarrativeTreeMaterializationResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            node_count=int(counts.get("node_count", 0)),
            node_type_counts=dict(counts.get("node_type_counts", {})),
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
        raise


async def materialize_latest_narrative_tree(
    session: AsyncSession,
    project_slug: str,
    *,
    requested_by: str = "system",
) -> NarrativeTreeMaterializationResult:
    return await materialize_narrative_tree(
        session,
        project_slug,
        requested_by=requested_by,
    )
