from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class NarrativeTreeNodeRead(BaseModel):
    id: UUID
    node_path: str = Field(min_length=1, max_length=4000)
    parent_path: str | None = Field(default=None, max_length=4000)
    depth: int = Field(ge=1)
    node_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=4000)
    summary: str | None = None
    body_md: str = Field(min_length=1)
    source_type: str = Field(min_length=1, max_length=64)
    source_ref_id: UUID | None = None
    scope_level: str = Field(min_length=1, max_length=32)
    scope_volume_number: int | None = Field(default=None, ge=1)
    scope_chapter_number: int | None = Field(default=None, ge=1)
    scope_scene_number: int | None = Field(default=None, ge=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class NarrativeTreeMaterializationResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    node_count: int = Field(default=0, ge=0)
    node_type_counts: dict[str, int] = Field(default_factory=dict)


class NarrativeTreeOverview(BaseModel):
    project_id: UUID
    project_slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    nodes: list[NarrativeTreeNodeRead] = Field(default_factory=list)


class NarrativeTreeSearchHit(BaseModel):
    node_path: str = Field(min_length=1, max_length=4000)
    node_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=4000)
    summary: str | None = None
    score: float = Field(ge=0.0)
    source_type: str = Field(min_length=1, max_length=64)
    scope_level: str = Field(min_length=1, max_length=32)
    metadata: dict[str, object] = Field(default_factory=dict)


class NarrativeTreeSearchResult(BaseModel):
    project_id: UUID
    query_text: str = Field(min_length=1)
    preferred_paths: list[str] = Field(default_factory=list)
    hits: list[NarrativeTreeSearchHit] = Field(default_factory=list)
