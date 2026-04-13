from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectConsistencyFinding(BaseModel):
    category: str = Field(min_length=1, max_length=64)
    severity: str = Field(min_length=1, max_length=16)
    message: str = Field(min_length=1)


class ProjectConsistencyScores(BaseModel):
    overall: float = Field(ge=0, le=1)
    chapter_coverage: float = Field(ge=0, le=1)
    chapter_sequence: float = Field(default=1.0, ge=0, le=1)
    scene_knowledge: float = Field(ge=0, le=1)
    canon_coverage: float = Field(ge=0, le=1)
    timeline_coverage: float = Field(ge=0, le=1)
    revision_pressure: float = Field(ge=0, le=1)
    export_readiness: float = Field(ge=0, le=1)
    main_plot_progression: float = Field(ge=0, le=1)
    mystery_balance: float = Field(ge=0, le=1)
    emotional_continuity: float = Field(ge=0, le=1)
    character_arc_progression: float = Field(ge=0, le=1)
    world_rule_consistency: float = Field(ge=0, le=1)
    antagonist_pressure: float = Field(ge=0, le=1)
    supporting_cast_depth: float = Field(ge=0, le=1)
    subplot_health: float = Field(ge=0, le=1)
    resolution_completeness: float = Field(ge=0, le=1)


class ProjectConsistencyResult(BaseModel):
    verdict: str = Field(min_length=1, max_length=16)
    severity_max: str = Field(min_length=1, max_length=16)
    scores: ProjectConsistencyScores
    findings: list[ProjectConsistencyFinding] = Field(default_factory=list)
    evidence_summary: dict[str, object] = Field(default_factory=dict)
    recommended_actions: list[str] = Field(default_factory=list)
