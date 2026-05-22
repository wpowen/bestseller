"""Per-chapter market constraint domain.

A ``ChapterMarketConstraints`` is the compiled output of
``services.market_constraint_compiler``. It converts a static
``FanqieMarketAnalysisBundle`` into **chapter-position-aware** generation
constraints that go directly into the writing prompt.

The whole point: market signals stop being "background reference reports"
and start being "you must hit at least N of these hooks before chapter K".
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChapterMarketConstraints(BaseModel):
    """Hard constraints for a single chapter derived from market signals."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chapter_position: int = Field(ge=1)
    band: str = Field(default="early", max_length=32)
    category: str = Field(default="", max_length=200)

    must_hit_hooks: list[str] = Field(default_factory=list)
    min_hooks_required: int = Field(default=0, ge=0)

    forbidden_patterns: list[str] = Field(default_factory=list)
    saturated_tropes: list[str] = Field(default_factory=list)

    optimal_chapter_length_min: int = Field(default=0, ge=0)
    optimal_chapter_length_max: int = Field(default=0, ge=0)

    must_appear_emotional_beats: list[str] = Field(default_factory=list)
    payoff_patterns: list[str] = Field(default_factory=list)
    structure_patterns: list[str] = Field(default_factory=list)

    pacing_notes: list[str] = Field(default_factory=list)
    safety_boundary: str = Field(default="", max_length=2000)

    confidence: float = Field(default=0.5, ge=0, le=1)
    rationale: list[str] = Field(default_factory=list)

    @field_validator(
        "must_hit_hooks",
        "forbidden_patterns",
        "saturated_tropes",
        "must_appear_emotional_beats",
        "payoff_patterns",
        "structure_patterns",
        "pacing_notes",
        "rationale",
        mode="before",
    )
    @classmethod
    def _coerce_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            normalized = value.replace("，", ",").replace("、", ",")
            return [item.strip() for item in normalized.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    def to_prompt_card(self) -> dict[str, Any]:
        return {
            "chapter_position": self.chapter_position,
            "band": self.band,
            "category": self.category,
            "must_hit_hooks": list(self.must_hit_hooks),
            "min_hooks_required": self.min_hooks_required,
            "forbidden_patterns": list(self.forbidden_patterns),
            "saturated_tropes": list(self.saturated_tropes),
            "length_range": [
                self.optimal_chapter_length_min,
                self.optimal_chapter_length_max,
            ],
            "must_appear_emotional_beats": list(self.must_appear_emotional_beats),
            "payoff_patterns": list(self.payoff_patterns),
            "pacing_notes": list(self.pacing_notes),
            "safety_boundary": self.safety_boundary,
            "confidence": self.confidence,
        }
