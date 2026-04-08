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
    conflict_clarity: float = Field(ge=0, le=1)
    emotion: float = Field(ge=0, le=1)
    emotional_movement: float = Field(ge=0, le=1)
    dialogue: float = Field(ge=0, le=1)
    style: float = Field(ge=0, le=1)
    hook: float = Field(ge=0, le=1)
    hook_strength: float = Field(ge=0, le=1)
    payoff_density: float = Field(ge=0, le=1)
    voice_consistency: float = Field(ge=0, le=1)
    character_voice_distinction: float = Field(ge=0, le=1)
    thematic_resonance: float = Field(ge=0, le=1)
    worldbuilding_integration: float = Field(ge=0, le=1)
    prose_variety: float = Field(ge=0, le=1)
    moral_complexity: float = Field(ge=0, le=1)
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
    main_plot_progression: float = Field(ge=0, le=1)
    subplot_progression: float = Field(ge=0, le=1)
    style: float = Field(ge=0, le=1)
    hook: float = Field(ge=0, le=1)
    ending_hook_effectiveness: float = Field(ge=0, le=1)
    volume_mission_alignment: float = Field(ge=0, le=1)
    pacing_rhythm: float = Field(ge=0, le=1)
    character_voice_distinction: float = Field(ge=0, le=1)
    thematic_resonance: float = Field(ge=0, le=1)
    contract_alignment: float = Field(ge=0, le=1)


class ChapterReviewResult(BaseModel):
    verdict: str = Field(min_length=1, max_length=16)
    severity_max: str = Field(min_length=1, max_length=16)
    scores: ChapterReviewScores
    findings: list[ChapterReviewFinding] = Field(default_factory=list)
    evidence_summary: dict[str, object] = Field(default_factory=dict)
    rewrite_instructions: str | None = None
