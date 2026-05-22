"""Reader Persona domain — virtual reader simulation contracts.

The simulator runs N personas against a chapter draft to produce per-persona
scores + abandon-probability estimates. The aggregated result is fed into
the next chapter's prompt so that generation closes the feedback loop
without needing real readers.

Personas are intentionally over-specified: each has explicit weights for
hook density, pacing, novelty tolerance, prose tolerance, emotional weight,
and an abandon threshold. This keeps the simulator deterministic and
auditable; LLM-augmented persona qualitative comments are optional and
opt-in via the simulator service.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PersonaWeights(BaseModel):
    """Linear weights a persona applies to chapter-level signal channels."""

    model_config = ConfigDict(str_strip_whitespace=True)

    hook_density: float = Field(default=1.0, ge=0)
    pacing: float = Field(default=1.0, ge=0)
    novelty: float = Field(default=1.0, ge=0)
    prose_quality: float = Field(default=1.0, ge=0)
    emotional_impact: float = Field(default=1.0, ge=0)
    consistency: float = Field(default=1.0, ge=0)
    payoff_density: float = Field(default=1.0, ge=0)


class ReaderPersona(BaseModel):
    """A single virtual reader profile."""

    model_config = ConfigDict(str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)

    weights: PersonaWeights = Field(default_factory=PersonaWeights)
    abandon_threshold: float = Field(default=0.45, ge=0, le=1)
    saturated_trope_tolerance: float = Field(default=0.5, ge=0, le=1)
    sensitivity: dict[str, float] = Field(default_factory=dict)

    population_share: float = Field(default=1.0, ge=0)


class PersonaScore(BaseModel):
    """The result of scoring a single chapter against a single persona."""

    model_config = ConfigDict(str_strip_whitespace=True)

    persona_key: str
    persona_label: str
    overall_score: float = Field(ge=0, le=1)
    abandon_probability: float = Field(ge=0, le=1)
    channel_scores: dict[str, float] = Field(default_factory=dict)
    concerns: list[str] = Field(default_factory=list)
    likes: list[str] = Field(default_factory=list)
    next_chapter_demand: str = Field(default="", max_length=2000)

    @field_validator("concerns", "likes", mode="before")
    @classmethod
    def _coerce_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value if str(item).strip()]


class PersonaSimulationResult(BaseModel):
    """Aggregated multi-persona simulation result for a single chapter."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chapter_position: int = Field(ge=1)
    per_persona: list[PersonaScore] = Field(default_factory=list)

    weighted_score: float = Field(default=0.0, ge=0, le=1)
    abandon_rate: float = Field(default=0.0, ge=0, le=1)
    high_risk_personas: list[str] = Field(default_factory=list)

    aggregated_concerns: list[str] = Field(default_factory=list)
    next_chapter_directives: list[str] = Field(default_factory=list)

    def to_feedback_card(self) -> dict[str, Any]:
        return {
            "chapter_position": self.chapter_position,
            "weighted_score": self.weighted_score,
            "abandon_rate": self.abandon_rate,
            "high_risk_personas": list(self.high_risk_personas),
            "aggregated_concerns": list(self.aggregated_concerns),
            "next_chapter_directives": list(self.next_chapter_directives),
            "per_persona": [p.model_dump(mode="json") for p in self.per_persona],
        }
