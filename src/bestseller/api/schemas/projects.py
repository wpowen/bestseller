from __future__ import annotations

from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, Field


class GenreSelection(BaseModel):
    """Structured 题材 selection from the canonical taxonomy.

    ``genre``/``sub_genre`` accept canonical keys or free-form labels;
    ``tags`` are trope/流派 tags (0–8). Resolved downstream via
    ``services.genre_taxonomy.resolve_selection``.
    """

    channel: str | None = None
    genre: str | None = None
    sub_genre: str | None = None
    tags: list[str] = Field(default_factory=list)
    facets: dict[str, str] = Field(default_factory=dict)
    template_key: str | None = None


class ProjectCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    genre: str = Field(min_length=1, max_length=100)
    target_word_count: int = Field(ge=10_000)
    target_chapters: int = Field(ge=1)
    audience: str | None = None
    premise: str | None = None
    writing_preset: str | None = None
    # Canonical taxonomy selection (optional; back-compatible with `genre`).
    channel: str | None = None
    sub_genre: str | None = None
    tags: list[str] = Field(default_factory=list)


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
