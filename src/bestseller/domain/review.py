from __future__ import annotations

from pydantic import BaseModel, Field


class SceneReviewFinding(BaseModel):
    category: str = Field(min_length=1, max_length=64)
    severity: str = Field(min_length=1, max_length=16)
    message: str = Field(min_length=1)


class SceneReviewScores(BaseModel):
    overall: float = Field(ge=0, le=1)
    goal: float = Field(ge=0, le=1)
    conflict: float = Field(ge=0, le=1)
    emotion: float = Field(ge=0, le=1)
    dialogue: float = Field(ge=0, le=1)
    style: float = Field(ge=0, le=1)
    hook: float = Field(ge=0, le=1)
    contract_alignment: float = Field(ge=0, le=1)


class SceneReviewResult(BaseModel):
    verdict: str = Field(min_length=1, max_length=16)
    severity_max: str = Field(min_length=1, max_length=16)
    scores: SceneReviewScores
    findings: list[SceneReviewFinding] = Field(default_factory=list)
    evidence_summary: dict[str, object] = Field(default_factory=dict)
    rewrite_instructions: str | None = None


class ChapterReviewFinding(BaseModel):
    category: str = Field(min_length=1, max_length=64)
    severity: str = Field(min_length=1, max_length=16)
    message: str = Field(min_length=1)


class ChapterReviewScores(BaseModel):
    overall: float = Field(ge=0, le=1)
    goal: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    coherence: float = Field(ge=0, le=1)
    continuity: float = Field(ge=0, le=1)
    style: float = Field(ge=0, le=1)
    hook: float = Field(ge=0, le=1)
    contract_alignment: float = Field(ge=0, le=1)


class ChapterReviewResult(BaseModel):
    verdict: str = Field(min_length=1, max_length=16)
    severity_max: str = Field(min_length=1, max_length=16)
    scores: ChapterReviewScores
    findings: list[ChapterReviewFinding] = Field(default_factory=list)
    evidence_summary: dict[str, object] = Field(default_factory=dict)
    rewrite_instructions: str | None = None
