from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from bestseller.domain.enums import ChapterStatus, ProjectStatus, SceneStatus, VolumeStatus


class ProjectCreate(BaseModel):
    slug: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    genre: str = Field(min_length=1, max_length=100)
    sub_genre: str | None = Field(default=None, max_length=100)
    audience: str | None = Field(default=None, max_length=200)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    target_word_count: int = Field(gt=0)
    target_chapters: int = Field(gt=0)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
        if not normalized or any(char not in allowed for char in normalized):
            raise ValueError("slug may only contain lowercase letters, numbers, '-' and '_'.")
        return normalized


class ProjectRead(BaseModel):
    id: UUID
    slug: str
    title: str
    genre: str
    sub_genre: str | None
    audience: str | None
    language: str
    target_word_count: int
    target_chapters: int
    status: ProjectStatus


class VolumeCreate(BaseModel):
    volume_number: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    theme: str | None = None
    goal: str | None = None
    obstacle: str | None = None
    target_word_count: int | None = Field(default=None, gt=0)
    target_chapter_count: int | None = Field(default=None, gt=0)
    status: VolumeStatus = VolumeStatus.PLANNED


class ChapterCreate(BaseModel):
    chapter_number: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=200)
    chapter_goal: str = Field(min_length=1)
    opening_situation: str | None = None
    main_conflict: str | None = None
    hook_type: str | None = None
    hook_description: str | None = None
    target_word_count: int = Field(default=3000, gt=0)
    volume_number: int = Field(default=1, gt=0)
    status: ChapterStatus = ChapterStatus.PLANNED


class SceneCardCreate(BaseModel):
    scene_number: int = Field(gt=0)
    scene_type: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=200)
    time_label: str | None = None
    participants: list[str] = Field(default_factory=list)
    purpose: dict[str, object] = Field(default_factory=dict)
    entry_state: dict[str, object] = Field(default_factory=dict)
    exit_state: dict[str, object] = Field(default_factory=dict)
    target_word_count: int = Field(default=1000, gt=0)
    status: SceneStatus = SceneStatus.PLANNED


class SceneStructureRead(BaseModel):
    id: UUID
    scene_number: int = Field(gt=0)
    title: str | None = None
    scene_type: str = Field(min_length=1)
    status: SceneStatus
    participants: list[str] = Field(default_factory=list)
    target_word_count: int = Field(ge=0)
    current_draft_version_no: int | None = None
    current_word_count: int | None = None


class ChapterStructureRead(BaseModel):
    id: UUID
    chapter_number: int = Field(gt=0)
    title: str | None = None
    volume_number: int | None = None
    chapter_goal: str = Field(min_length=1)
    status: ChapterStatus
    target_word_count: int = Field(ge=0)
    current_word_count: int = Field(ge=0)
    current_draft_version_no: int | None = None
    scenes: list[SceneStructureRead] = Field(default_factory=list)


class VolumeStructureRead(BaseModel):
    id: UUID
    volume_number: int = Field(gt=0)
    title: str = Field(min_length=1)
    status: VolumeStatus
    target_word_count: int | None = None
    target_chapter_count: int | None = None
    chapters: list[ChapterStructureRead] = Field(default_factory=list)


class ProjectStructureRead(BaseModel):
    project_id: UUID
    project_slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: ProjectStatus
    target_word_count: int = Field(ge=0)
    target_chapters: int = Field(ge=0)
    current_volume_number: int = Field(ge=0)
    current_chapter_number: int = Field(ge=0)
    total_chapters: int = Field(ge=0)
    total_scenes: int = Field(ge=0)
    volumes: list[VolumeStructureRead] = Field(default_factory=list)
