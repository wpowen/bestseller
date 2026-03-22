from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, ChapterStatus, ProjectStatus, SceneStatus, VolumeStatus
from bestseller.domain.inspection import (
    ProjectWorkflowOverviewRead,
    WorkflowRunRead,
    WorkflowStepRunRead,
)
from bestseller.domain.planning import PlanningArtifactDetail, PlanningArtifactSummary
from bestseller.domain.project import (
    ChapterStructureRead,
    ProjectStructureRead,
    SceneStructureRead,
    VolumeStructureRead,
)
from bestseller.domain.story_bible import (
    CharacterStateSnapshotRead,
    DeferredRevealRead,
    ExpansionGateRead,
    StoryBibleCharacterRead,
    StoryBibleFactionRead,
    StoryBibleOverview,
    StoryBibleLocationRead,
    StoryBibleRelationshipRead,
    StoryBibleWorldRuleRead,
    VolumeFrontierRead,
    WorldBackboneRead,
)
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    DeferredRevealModel,
    ExpansionGateModel,
    FactionModel,
    LocationModel,
    PlanningArtifactVersionModel,
    RelationshipModel,
    SceneCardModel,
    SceneDraftVersionModel,
    VolumeModel,
    VolumeFrontierModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
    WorldBackboneModel,
    WorldRuleModel,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.services.story_bible import get_latest_character_state


async def list_planning_artifacts(
    session: AsyncSession,
    project_slug: str,
    *,
    artifact_type: ArtifactType | None = None,
) -> list[PlanningArtifactSummary]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    stmt = select(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.project_id == project.id
    )
    if artifact_type is not None:
        stmt = stmt.where(PlanningArtifactVersionModel.artifact_type == artifact_type.value)
    stmt = stmt.order_by(
        PlanningArtifactVersionModel.artifact_type.asc(),
        PlanningArtifactVersionModel.version_no.desc(),
        PlanningArtifactVersionModel.created_at.desc(),
    )
    rows = list(await session.scalars(stmt))
    return [
        PlanningArtifactSummary(
            artifact_id=row.id,
            artifact_type=ArtifactType(row.artifact_type),
            version_no=row.version_no,
            scope_ref_id=row.scope_ref_id,
            status=row.status,
            schema_version=row.schema_version,
            created_at=row.created_at,
            notes=row.notes,
        )
        for row in rows
    ]


async def get_planning_artifact_detail(
    session: AsyncSession,
    project_slug: str,
    artifact_type: ArtifactType,
    *,
    version_no: int | None = None,
) -> PlanningArtifactDetail | None:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    stmt = select(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.project_id == project.id,
        PlanningArtifactVersionModel.artifact_type == artifact_type.value,
    )
    if version_no is not None:
        stmt = stmt.where(PlanningArtifactVersionModel.version_no == version_no)
    stmt = stmt.order_by(
        PlanningArtifactVersionModel.version_no.desc(),
        PlanningArtifactVersionModel.created_at.desc(),
    )
    row = await session.scalar(stmt.limit(1))
    if row is None:
        return None
    return PlanningArtifactDetail(
        artifact_id=row.id,
        artifact_type=ArtifactType(row.artifact_type),
        version_no=row.version_no,
        scope_ref_id=row.scope_ref_id,
        status=row.status,
        schema_version=row.schema_version,
        created_at=row.created_at,
        notes=row.notes,
        content=row.content,
    )


async def build_project_structure(
    session: AsyncSession,
    project_slug: str,
) -> ProjectStructureRead:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    volumes = list(
        await session.scalars(
            select(VolumeModel)
            .where(VolumeModel.project_id == project.id)
            .order_by(VolumeModel.volume_number.asc())
        )
    )
    chapters = list(
        await session.scalars(
            select(ChapterModel)
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )
    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.project_id == project.id)
            .order_by(SceneCardModel.chapter_id.asc(), SceneCardModel.scene_number.asc())
        )
    )
    current_scene_drafts = {
        draft.scene_card_id: draft
        for draft in await session.scalars(
            select(SceneDraftVersionModel).where(
                SceneDraftVersionModel.project_id == project.id,
                SceneDraftVersionModel.is_current.is_(True),
            )
        )
    }
    current_chapter_drafts = {
        draft.chapter_id: draft
        for draft in await session.scalars(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.project_id == project.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
    }

    scenes_by_chapter: dict[object, list[SceneCardModel]] = defaultdict(list)
    for scene in scenes:
        scenes_by_chapter[scene.chapter_id].append(scene)

    chapters_by_volume: dict[object | None, list[ChapterModel]] = defaultdict(list)
    for chapter in chapters:
        chapters_by_volume[chapter.volume_id].append(chapter)

    volume_views: list[VolumeStructureRead] = []
    for volume in volumes:
        chapter_views: list[ChapterStructureRead] = []
        for chapter in chapters_by_volume.get(volume.id, []):
            scene_views: list[SceneStructureRead] = []
            for scene in scenes_by_chapter.get(chapter.id, []):
                draft = current_scene_drafts.get(scene.id)
                scene_views.append(
                    SceneStructureRead(
                        id=scene.id,
                        scene_number=scene.scene_number,
                        title=scene.title,
                        scene_type=scene.scene_type,
                        status=SceneStatus(scene.status),
                        participants=list(scene.participants),
                        target_word_count=scene.target_word_count,
                        current_draft_version_no=draft.version_no if draft is not None else None,
                        current_word_count=draft.word_count if draft is not None else None,
                    )
                )
            chapter_draft = current_chapter_drafts.get(chapter.id)
            chapter_views.append(
                ChapterStructureRead(
                    id=chapter.id,
                    chapter_number=chapter.chapter_number,
                    title=chapter.title,
                    volume_number=volume.volume_number,
                    chapter_goal=chapter.chapter_goal,
                    status=ChapterStatus(chapter.status),
                    target_word_count=chapter.target_word_count,
                    current_word_count=chapter.current_word_count,
                    current_draft_version_no=chapter_draft.version_no if chapter_draft is not None else None,
                    scenes=scene_views,
                )
            )
        volume_views.append(
            VolumeStructureRead(
                id=volume.id,
                volume_number=volume.volume_number,
                title=volume.title,
                status=VolumeStatus(volume.status),
                target_word_count=volume.target_word_count,
                target_chapter_count=volume.target_chapter_count,
                chapters=chapter_views,
            )
        )

    return ProjectStructureRead(
        project_id=project.id,
        project_slug=project.slug,
        title=project.title,
        status=ProjectStatus(project.status),
        target_word_count=project.target_word_count,
        target_chapters=project.target_chapters,
        current_volume_number=project.current_volume_number,
        current_chapter_number=project.current_chapter_number,
        total_chapters=len(chapters),
        total_scenes=len(scenes),
        volumes=volume_views,
    )


async def build_project_workflow_overview(
    session: AsyncSession,
    project_slug: str,
) -> ProjectWorkflowOverviewRead:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    workflow_runs = list(
        await session.scalars(
            select(WorkflowRunModel)
            .where(WorkflowRunModel.project_id == project.id)
            .order_by(WorkflowRunModel.created_at.desc())
        )
    )
    workflow_run_ids = [row.id for row in workflow_runs]
    workflow_steps = (
        list(
            await session.scalars(
                select(WorkflowStepRunModel)
                .where(WorkflowStepRunModel.workflow_run_id.in_(workflow_run_ids))
                .order_by(WorkflowStepRunModel.workflow_run_id.asc(), WorkflowStepRunModel.step_order.asc())
            )
        )
        if workflow_run_ids
        else []
    )

    steps_by_run: dict[object, list[WorkflowStepRunModel]] = defaultdict(list)
    for step in workflow_steps:
        steps_by_run[step.workflow_run_id].append(step)

    runs: list[WorkflowRunRead] = []
    for row in workflow_runs:
        steps = steps_by_run.get(row.id, [])
        completed_step_count = sum(1 for item in steps if item.status == "completed")
        failed_step_count = sum(1 for item in steps if item.status == "failed")
        runs.append(
            WorkflowRunRead(
                workflow_run_id=row.id,
                workflow_type=row.workflow_type,
                status=row.status,
                scope_type=row.scope_type,
                scope_id=row.scope_id,
                requested_by=row.requested_by,
                current_step=row.current_step,
                error_message=row.error_message,
                created_at=row.created_at,
                updated_at=row.updated_at,
                metadata=dict(row.metadata_json or {}),
                step_count=len(steps),
                completed_step_count=completed_step_count,
                failed_step_count=failed_step_count,
                steps=[
                    WorkflowStepRunRead(
                        step_run_id=item.id,
                        step_name=item.step_name,
                        step_order=item.step_order,
                        status=item.status,
                        created_at=item.created_at,
                        input_ref=dict(item.input_ref or {}),
                        output_ref=dict(item.output_ref or {}),
                        error_message=item.error_message,
                    )
                    for item in steps
                ],
            )
        )

    return ProjectWorkflowOverviewRead(
        project_id=project.id,
        project_slug=project.slug,
        project_status=project.status,
        run_count=len(runs),
        completed_run_count=sum(1 for row in runs if row.status == "completed"),
        failed_run_count=sum(1 for row in runs if row.status == "failed"),
        latest_run_id=runs[0].workflow_run_id if runs else None,
        latest_run_status=runs[0].status if runs else None,
        runs=runs,
    )


async def build_story_bible_overview(
    session: AsyncSession,
    project_slug: str,
    *,
    before_chapter_number: int | None = None,
    before_scene_number: int | None = None,
) -> StoryBibleOverview:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    world_rules = list(
        await session.scalars(
            select(WorldRuleModel)
            .where(WorldRuleModel.project_id == project.id)
            .order_by(WorldRuleModel.rule_code.asc(), WorldRuleModel.name.asc())
        )
    )
    locations = list(
        await session.scalars(
            select(LocationModel)
            .where(LocationModel.project_id == project.id)
            .order_by(LocationModel.name.asc())
        )
    )
    factions = list(
        await session.scalars(
            select(FactionModel)
            .where(FactionModel.project_id == project.id)
            .order_by(FactionModel.name.asc())
        )
    )
    characters = list(
        await session.scalars(
            select(CharacterModel)
            .where(CharacterModel.project_id == project.id)
            .order_by(CharacterModel.name.asc())
        )
    )
    relationships = list(
        await session.scalars(
            select(RelationshipModel)
            .where(RelationshipModel.project_id == project.id)
            .order_by(
                RelationshipModel.established_chapter_no.asc().nullsfirst(),
                RelationshipModel.relationship_type.asc(),
            )
        )
    )
    world_backbone = await session.scalar(
        select(WorldBackboneModel).where(WorldBackboneModel.project_id == project.id)
    )
    volume_frontiers = list(
        await session.scalars(
            select(VolumeFrontierModel)
            .where(VolumeFrontierModel.project_id == project.id)
            .order_by(VolumeFrontierModel.volume_number.asc())
        )
    )
    deferred_reveals = list(
        await session.scalars(
            select(DeferredRevealModel)
            .where(DeferredRevealModel.project_id == project.id)
            .order_by(
                DeferredRevealModel.reveal_volume_number.asc(),
                DeferredRevealModel.reveal_chapter_number.asc(),
                DeferredRevealModel.reveal_code.asc(),
            )
        )
    )
    expansion_gates = list(
        await session.scalars(
            select(ExpansionGateModel)
            .where(ExpansionGateModel.project_id == project.id)
            .order_by(
                ExpansionGateModel.unlock_volume_number.asc(),
                ExpansionGateModel.unlock_chapter_number.asc(),
            )
        )
    )

    character_name_by_id = {character.id: character.name for character in characters}
    character_views: list[StoryBibleCharacterRead] = []
    for character in characters:
        latest_state = await get_latest_character_state(
            session,
            project_id=project.id,
            character_id=character.id,
            before_chapter_number=before_chapter_number,
            before_scene_number=before_scene_number,
        )
        character_views.append(
            StoryBibleCharacterRead(
                name=character.name,
                role=character.role,
                goal=character.goal,
                fear=character.fear,
                flaw=character.flaw,
                secret=character.secret,
                arc_trajectory=character.arc_trajectory,
                arc_state=(latest_state.arc_state if latest_state is not None else character.arc_state),
                power_tier=(latest_state.power_tier if latest_state is not None else character.power_tier),
                is_pov_character=character.is_pov_character,
                knowledge_state=dict(character.knowledge_state_json or {}),
                latest_state=(
                    CharacterStateSnapshotRead(
                        chapter_number=latest_state.chapter_number,
                        scene_number=latest_state.scene_number,
                        arc_state=latest_state.arc_state,
                        emotional_state=latest_state.emotional_state,
                        physical_state=latest_state.physical_state,
                        power_tier=latest_state.power_tier,
                        trust_map=dict(latest_state.trust_map or {}),
                        beliefs=list(latest_state.beliefs or []),
                        notes=latest_state.notes,
                    )
                    if latest_state is not None
                    else None
                ),
            )
        )

    return StoryBibleOverview(
        project_id=project.id,
        project_slug=project.slug,
        title=project.title,
        world_backbone=(
            WorldBackboneRead(
                title=world_backbone.title,
                core_promise=world_backbone.core_promise,
                mainline_drive=world_backbone.mainline_drive,
                protagonist_destiny=world_backbone.protagonist_destiny,
                antagonist_axis=world_backbone.antagonist_axis,
                thematic_melody=world_backbone.thematic_melody,
                world_frame=world_backbone.world_frame,
                invariant_elements=list(world_backbone.invariant_elements or []),
                stable_unknowns=list(world_backbone.stable_unknowns or []),
            )
            if world_backbone is not None
            else None
        ),
        world_rules=[
            StoryBibleWorldRuleRead(
                rule_code=rule.rule_code,
                name=rule.name,
                description=rule.description,
                story_consequence=rule.story_consequence,
                exploitation_potential=rule.exploitation_potential,
            )
            for rule in world_rules
        ],
        locations=[
            StoryBibleLocationRead(
                name=location.name,
                location_type=location.location_type,
                atmosphere=location.atmosphere,
                key_rule_codes=list(location.key_rule_codes),
                story_role=location.story_role,
            )
            for location in locations
        ],
        factions=[
            StoryBibleFactionRead(
                name=faction.name,
                goal=faction.goal,
                method=faction.method,
                relationship_to_protagonist=faction.relationship_to_protagonist,
                internal_conflict=faction.internal_conflict,
            )
            for faction in factions
        ],
        characters=character_views,
        relationships=[
            StoryBibleRelationshipRead(
                character_a=character_name_by_id.get(relationship.character_a_id, str(relationship.character_a_id)),
                character_b=character_name_by_id.get(relationship.character_b_id, str(relationship.character_b_id)),
                relationship_type=relationship.relationship_type,
                strength=float(relationship.strength if not isinstance(relationship.strength, Decimal) else relationship.strength),
                public_face=relationship.public_face,
                private_reality=relationship.private_reality,
                tension_summary=relationship.tension_summary,
                established_chapter_no=relationship.established_chapter_no,
                last_changed_chapter_no=relationship.last_changed_chapter_no,
            )
            for relationship in relationships
        ],
        volume_frontiers=[
            VolumeFrontierRead(
                volume_number=item.volume_number,
                title=item.title,
                frontier_summary=item.frontier_summary,
                expansion_focus=item.expansion_focus,
                start_chapter_number=item.start_chapter_number,
                end_chapter_number=item.end_chapter_number,
                visible_rule_codes=list(item.visible_rule_codes or []),
                active_locations=list(item.active_locations or []),
                active_factions=list(item.active_factions or []),
                active_arc_codes=list(item.active_arc_codes or []),
                future_reveal_codes=list(item.future_reveal_codes or []),
            )
            for item in volume_frontiers
        ],
        deferred_reveals=[
            DeferredRevealRead(
                reveal_code=item.reveal_code,
                label=item.label,
                category=item.category,
                summary=item.summary,
                source_volume_number=item.source_volume_number,
                reveal_volume_number=item.reveal_volume_number,
                reveal_chapter_number=item.reveal_chapter_number,
                guard_condition=item.guard_condition,
                status=item.status,
            )
            for item in deferred_reveals
        ],
        expansion_gates=[
            ExpansionGateRead(
                gate_code=item.gate_code,
                label=item.label,
                gate_type=item.gate_type,
                condition_summary=item.condition_summary,
                unlocks_summary=item.unlocks_summary,
                source_volume_number=item.source_volume_number,
                unlock_volume_number=item.unlock_volume_number,
                unlock_chapter_number=item.unlock_chapter_number,
                status=item.status,
            )
            for item in expansion_gates
        ],
    )
