from __future__ import annotations

from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    genre: str = Field(min_length=1, max_length=100)
    target_word_count: int = Field(ge=10_000)
    target_chapters: int = Field(ge=1)
    audience: str | None = None
    premise: str | None = None
    writing_preset: str | None = None


class ProjectResponse(BaseModel):
    id: UUID
    slug: str
    title: str
    genre: str
    target_word_count: int
    target_chapters: int
    current_chapter_number: int
    status: str
    project_type: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int
