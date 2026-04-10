from __future__ import annotations

from pydantic import BaseModel, Field


class PlanValidationFinding(BaseModel):
    """A single finding from plan validation — an issue or recommendation."""

    category: str = Field(min_length=1, max_length=64)
    severity: str = Field(min_length=1, max_length=16)  # "critical" | "warning" | "info"
    message: str = Field(min_length=1)
    suggestion: str | None = None


class PlanValidationResult(BaseModel):
    """Result of validating a novel plan against genre-specific rubrics."""

    genre_category: str = Field(min_length=1, max_length=64)
    overall_pass: bool
    score: float = Field(ge=0, le=1)
    findings: list[PlanValidationFinding] = Field(default_factory=list)
    rubric_checks: dict[str, bool] = Field(default_factory=dict)
