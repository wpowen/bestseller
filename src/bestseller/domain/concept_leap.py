"""Concept Leap domain — cross-domain premise generation contracts.

The single most reliable way to escape "LLM mean regression" at the
*premise* level is to force concept synthesis across **disjoint** domain
pools. Bestseller mashups historically come from this exact move:

    《诡秘之主》 = lovecraft × tarot × steampunk × ritual_magic
    《雪中悍刀行》 = wuxia × politics × literati × hundred_schools
    《大奉打更人》 = detective × xianxia × imperial_court × reincarnation

The generator samples one seed from each requested pool, ranks the
resulting candidate mashups by novelty + coherence, and emits a ranked
list of candidate premises. The caller picks the winner (manually or via
a higher-level critic) and feeds it into the conception pipeline.

This module owns the *domain models* — pool definitions and candidates.
The deterministic generation logic lives in
``services.concept_leap`` (which also ships a default ``CONCEPT_POOLS``
catalogue).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConceptSeed(BaseModel):
    """A single concept token within a pool — the atomic mashup unit."""

    model_config = ConfigDict(str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list)
    saturation_score: float = Field(default=0.0, ge=0, le=1)

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [t.strip() for t in value.split(",") if t.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(t).strip() for t in value if str(t).strip()]
        return []


class ConceptPool(BaseModel):
    """A named collection of concept seeds that share a domain."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    seeds: list[ConceptSeed] = Field(default_factory=list)


class ConceptCandidate(BaseModel):
    """One cross-pool mashup candidate, scored and explainable."""

    model_config = ConfigDict(str_strip_whitespace=True)

    seeds: list[ConceptSeed]
    pools: list[str]

    novelty_score: float = Field(default=0.0, ge=0, le=1)
    coherence_score: float = Field(default=0.0, ge=0, le=1)
    saturation_penalty: float = Field(default=0.0, ge=0, le=1)
    combined_score: float = Field(default=0.0, ge=0, le=1)

    rationale: list[str] = Field(default_factory=list)
    premise_hint: str = Field(default="", max_length=2000)
    forbidden_overlap: list[str] = Field(default_factory=list)

    @property
    def signature(self) -> str:
        return " × ".join(s.key for s in self.seeds)

    def to_prompt_card(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "pools": list(self.pools),
            "seeds": [s.model_dump(mode="json") for s in self.seeds],
            "novelty_score": self.novelty_score,
            "coherence_score": self.coherence_score,
            "saturation_penalty": self.saturation_penalty,
            "combined_score": self.combined_score,
            "rationale": list(self.rationale),
            "premise_hint": self.premise_hint,
            "forbidden_overlap": list(self.forbidden_overlap),
        }


class ConceptLeapResult(BaseModel):
    """Top-K candidate mashups from one generation run."""

    model_config = ConfigDict(str_strip_whitespace=True)

    candidates: list[ConceptCandidate] = Field(default_factory=list)
    pools_sampled: list[str] = Field(default_factory=list)
    samples_evaluated: int = Field(default=0, ge=0)
    seed: int | None = None

    def best(self) -> ConceptCandidate | None:
        return self.candidates[0] if self.candidates else None
