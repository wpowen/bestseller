from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from bestseller.api.deps import ApiKeyDep, SessionDep, SettingsDep
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    SceneCardModel,
    SceneDraftVersionModel,
    VolumeModel,
)

router = APIRouter(tags=["content"])


class VolumeItem(BaseModel):
    volume_number: int
    title: str | None
    theme: str | None
    status: str

    model_config = {"from_attributes": True}


class ChapterItem(BaseModel):
    chapter_number: int
    title: str | None = None
    status: str
    word_count: int | None = None

    model_config = {"from_attributes": True}


class ChapterContentResponse(BaseModel):
    chapter_number: int
    title: str | None
    status: str
    content: str | None  # markdown text of latest approved draft


class SceneItem(BaseModel):
    scene_number: int
    scene_type: str | None
    status: str
    word_count: int | None = None

    model_config = {"from_attributes": True}


async def _get_project_or_404(slug: str, session: SessionDep) -> ProjectModel:
    result = await session.execute(select(ProjectModel).where(ProjectModel.slug == slug))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{slug}' not found")
    return project


@router.get("/projects/{slug}/content")
async def get_full_novel(
    slug: str,
    session: SessionDep,
    _key: ApiKeyDep,
) -> dict:
    """Return full novel markdown text (all approved chapter drafts concatenated)."""
    project = await _get_project_or_404(slug, session)

    chapters = (await session.execute(
        select(ChapterModel)
        .where(ChapterModel.project_id == project.id)
        .order_by(ChapterModel.chapter_number)
    )).scalars().all()

    # Batch-load all current drafts in a single query (no N+1)
    chapter_ids = [ch.id for ch in chapters]
    drafts_by_chapter: dict = {}
    if chapter_ids:
        drafts_result = await session.execute(
            select(ChapterDraftVersionModel)
            .where(
                ChapterDraftVersionModel.chapter_id.in_(chapter_ids),
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
        drafts_by_chapter = {d.chapter_id: d for d in drafts_result.scalars()}

    parts: list[str] = []
    total_words = 0
    for chapter in chapters:
        draft = drafts_by_chapter.get(chapter.id)
        if draft and draft.content_md:
            parts.append(draft.content_md)
            total_words += draft.word_count or 0

    return {
        "project_slug": slug,
        "title": project.title,
        "total_chapters": len(chapters),
        "total_words": total_words,
        "content": "\n\n---\n\n".join(parts),
    }


@router.get("/projects/{slug}/volumes", response_model=list[VolumeItem])
async def list_volumes(
    slug: str,
    session: SessionDep,
    _key: ApiKeyDep,
) -> list[VolumeItem]:
    project = await _get_project_or_404(slug, session)
    result = await session.execute(
        select(VolumeModel)
        .where(VolumeModel.project_id == project.id)
        .order_by(VolumeModel.volume_number)
    )
    return [VolumeItem.model_validate(v) for v in result.scalars()]


@router.get("/projects/{slug}/chapters", response_model=list[ChapterItem])
async def list_chapters(
    slug: str,
    session: SessionDep,
    _key: ApiKeyDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ChapterItem]:
    project = await _get_project_or_404(slug, session)
    chapters = (await session.execute(
        select(ChapterModel)
        .where(ChapterModel.project_id == project.id)
        .order_by(ChapterModel.chapter_number)
        .offset(offset)
        .limit(limit)
    )).scalars().all()

    # Batch-load current drafts (no N+1)
    chapter_ids = [ch.id for ch in chapters]
    drafts_by_chapter: dict = {}
    if chapter_ids:
        drafts_result = await session.execute(
            select(ChapterDraftVersionModel)
            .where(
                ChapterDraftVersionModel.chapter_id.in_(chapter_ids),
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
        drafts_by_chapter = {d.chapter_id: d for d in drafts_result.scalars()}

    return [
        ChapterItem(
            chapter_number=ch.chapter_number,
            title=ch.title if hasattr(ch, "title") else None,
            status=ch.status,
            word_count=drafts_by_chapter[ch.id].word_count if ch.id in drafts_by_chapter else None,
        )
        for ch in chapters
    ]


@router.get("/projects/{slug}/chapters/{chapter_number}", response_model=ChapterContentResponse)
async def get_chapter(
    slug: str,
    chapter_number: int,
    session: SessionDep,
    _key: ApiKeyDep,
) -> ChapterContentResponse:
    project = await _get_project_or_404(slug, session)
    result = await session.execute(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    chapter = result.scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Chapter {chapter_number} not found")

    draft_result = await session.execute(
        select(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    draft = draft_result.scalar_one_or_none()

    return ChapterContentResponse(
        chapter_number=chapter.chapter_number,
        title=chapter.title if hasattr(chapter, "title") else None,
        status=chapter.status,
        content=draft.content_md if draft else None,
    )


@router.get("/projects/{slug}/chapters/{chapter_number}/scenes", response_model=list[SceneItem])
async def list_scenes(
    slug: str,
    chapter_number: int,
    session: SessionDep,
    _key: ApiKeyDep,
) -> list[SceneItem]:
    project = await _get_project_or_404(slug, session)
    chapter_result = await session.execute(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    chapter = chapter_result.scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Chapter {chapter_number} not found")

    scenes = (await session.execute(
        select(SceneCardModel)
        .where(SceneCardModel.chapter_id == chapter.id)
        .order_by(SceneCardModel.scene_number)
    )).scalars().all()

    # Batch-load scene drafts (no N+1)
    scene_ids = [sc.id for sc in scenes]
    drafts_by_scene: dict = {}
    if scene_ids:
        drafts_result = await session.execute(
            select(SceneDraftVersionModel)
            .where(
                SceneDraftVersionModel.scene_card_id.in_(scene_ids),
                SceneDraftVersionModel.is_current.is_(True),
            )
        )
        drafts_by_scene = {d.scene_card_id: d for d in drafts_result.scalars()}

    return [
        SceneItem(
            scene_number=sc.scene_number,
            scene_type=sc.scene_type,
            status=sc.status,
            word_count=drafts_by_scene[sc.id].word_count if sc.id in drafts_by_scene else None,
        )
        for sc in scenes
    ]
