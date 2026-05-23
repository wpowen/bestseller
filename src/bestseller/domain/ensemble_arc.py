from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class IntersectionPoint(BaseModel, frozen=True):
    chapter: int = Field(ge=1)
    effect_on_mainline: str = Field(min_length=1)


class EnsembleCharacterArc(BaseModel, frozen=True):
    owner_id: UUID
    arc_kind: Literal["redemption", "fall", "loyalty", "vengeance", "transformation", "stoic"]
    private_goal: str = Field(min_length=1)
    private_obstacle: str = Field(min_length=1)
    private_payoff: str = Field(min_length=1)
    pov_chapters: list[int] = Field(default_factory=list)
    intersect_main: list[IntersectionPoint] = Field(default_factory=list)
    standalone_value: str = Field(min_length=1)
    final_state: str = ""


class EnsembleArcKernel(BaseModel, frozen=True):
    arcs: list[EnsembleCharacterArc] = Field(default_factory=list)
    coverage_target: float = Field(default=0.1, ge=0, le=0.25)
    applicable_categories: list[str] = Field(default_factory=list)

