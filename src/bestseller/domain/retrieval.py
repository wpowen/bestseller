from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    source_type: str = Field(min_length=1)
    source_id: UUID
    chunk_index: int = Field(ge=0)
    score: float = Field(ge=0.0)
    chunk_text: str = Field(min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class RetrievalSearchResult(BaseModel):
    project_id: UUID
    query_text: str = Field(min_length=1)
    chunks: list[RetrievedChunk] = Field(default_factory=list)
