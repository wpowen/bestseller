from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.domain.project import ChapterCreate, ProjectCreate, SceneCardCreate, VolumeCreate
from bestseller.infra.db.models import (
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    SceneCardModel,
    StyleGuideModel,
    VolumeModel,
)
from bestseller.services.writing_profile import (
    build_project_metadata,
    resolve_project_create_writing_profile,
)
from bestseller.settings import AppSettings


async def get_project_by_slug(session: AsyncSession, slug: str) -> ProjectModel | None:
    return await session.scalar(select(ProjectModel).where(ProjectModel.slug == slug))


async def create_project(
    session: AsyncSession,
    payload: ProjectCreate,
    settings: AppSettings,
) -> ProjectModel:
    existing = await get_project_by_slug(session, payload.slug)
    if existing is not None:
        raise ValueError(f"Project slug '{payload.slug}' already exists.")

    writing_profile = resolve_project_create_writing_profile(payload)
    project = ProjectModel(
        slug=payload.slug,
        title=payload.title,
        language=payload.language,
        genre=payload.genre,
        sub_genre=payload.sub_genre,
        target_word_count=payload.target_word_count,
        target_chapters=payload.target_chapters,
        audience=payload.audience,
        project_type=payload.project_type.value,
        metadata_json=build_project_metadata(payload, writing_profile),
    )
    session.add(project)
    await session.flush()

    style = StyleGuideModel(
        project_id=project.id,
        pov_type=writing_profile.style.pov_type or settings.generation.pov,
        tense=writing_profile.style.tense,
        tone_keywords=writing_profile.style.tone_keywords or [payload.genre],
        prose_style=writing_profile.style.prose_style,
        sentence_style=writing_profile.style.sentence_style,
        info_density=writing_profile.style.info_density,
        dialogue_ratio=writing_profile.style.dialogue_ratio,
        taboo_words=writing_profile.style.taboo_words,
        taboo_topics=writing_profile.style.taboo_topics,
        reference_works=writing_profile.style.reference_works,
        custom_rules=writing_profile.style.custom_rules,
    )
    session.add(style)
    await session.flush()
    return project


async def list_projects(session: AsyncSession) -> list[ProjectModel]:
    result = await session.scalars(select(ProjectModel).order_by(ProjectModel.created_at.desc()))
    return list(result)


async def create_or_get_volume(
    session: AsyncSession,
    project_id: Any,
    payload: VolumeCreate,
) -> VolumeModel:
    existing = await session.scalar(
        select(VolumeModel).where(
            VolumeModel.project_id == project_id,
            VolumeModel.volume_number == payload.volume_number,
        )
    )
    if existing is not None:
        return existing

    volume = VolumeModel(
        project_id=project_id,
        volume_number=payload.volume_number,
        title=payload.title,
        theme=payload.theme,
        goal=payload.goal,
        obstacle=payload.obstacle,
        target_word_count=payload.target_word_count,
        target_chapter_count=payload.target_chapter_count,
        status=payload.status.value,
    )
    session.add(volume)
    await session.flush()
    return volume


async def create_chapter(
    session: AsyncSession,
    project_slug: str,
    payload: ChapterCreate,
) -> ChapterModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    existing = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == payload.chapter_number,
        )
    )
    if existing is not None:
        raise ValueError(f"Chapter {payload.chapter_number} already exists for '{project_slug}'.")

    volume = await create_or_get_volume(
        session,
        project.id,
        VolumeCreate(volume_number=payload.volume_number, title=f"Volume {payload.volume_number}"),
    )
    chapter = ChapterModel(
        project_id=project.id,
        volume_id=volume.id,
        chapter_number=payload.chapter_number,
        title=payload.title,
        chapter_goal=payload.chapter_goal,
        opening_situation=payload.opening_situation,
        main_conflict=payload.main_conflict,
        hook_type=payload.hook_type,
        hook_description=payload.hook_description,
        target_word_count=payload.target_word_count,
        status=payload.status.value,
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
    )
    session.add(chapter)
    await session.flush()
    return chapter


async def create_scene_card(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    payload: SceneCardCreate,
) -> SceneCardModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")

    existing = await session.scalar(
        select(SceneCardModel).where(
            SceneCardModel.chapter_id == chapter.id,
            SceneCardModel.scene_number == payload.scene_number,
        )
    )
    if existing is not None:
        raise ValueError(
            f"Scene {payload.scene_number} already exists in chapter {chapter_number}."
        )

    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=payload.scene_number,
        scene_type=payload.scene_type,
        title=payload.title,
        time_label=payload.time_label,
        participants=payload.participants,
        purpose=payload.purpose,
        entry_state=payload.entry_state,
        exit_state=payload.exit_state,
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        target_word_count=payload.target_word_count,
        status=payload.status.value,
    )
    session.add(scene)
    await session.flush()
    return scene


async def import_planning_artifact(
    session: AsyncSession,
    project_slug: str,
    payload: PlanningArtifactCreate,
) -> PlanningArtifactVersionModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    version_filters = [
        PlanningArtifactVersionModel.project_id == project.id,
        PlanningArtifactVersionModel.artifact_type == payload.artifact_type.value,
    ]
    if payload.scope_ref_id is None:
        version_filters.append(PlanningArtifactVersionModel.scope_ref_id.is_(None))
    else:
        version_filters.append(PlanningArtifactVersionModel.scope_ref_id == payload.scope_ref_id)

    version_stmt = select(func.coalesce(func.max(PlanningArtifactVersionModel.version_no), 0)).where(
        *version_filters
    )
    next_version = int((await session.scalar(version_stmt)) or 0) + 1

    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=payload.artifact_type.value,
        scope_ref_id=payload.scope_ref_id,
        version_no=next_version,
        status="approved",
        schema_version="1.0",
        content=payload.content,
        notes=payload.notes,
    )
    session.add(artifact)
    await session.flush()
    return artifact


def load_json_file(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
