from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RevealMilestone(BaseModel, frozen=True):
    volume: int = Field(ge=1)
    fraction_revealed: float = Field(ge=0.0, le=1.0)
    reveal_kind: Literal["hint", "partial_truth", "false_lead", "full_reveal"]
    description: str = Field(min_length=1)


class MysteryAnchor(BaseModel, frozen=True):
    question: str = Field(min_length=1)
    stake_if_solved: str = Field(min_length=1)
    reveal_milestones: list[RevealMilestone] = Field(min_length=2)
    false_lead_plan: list[str] = Field(default_factory=list)
    final_payoff_chapter_range: tuple[int, int]


class MysteryAnchorKernel(BaseModel, frozen=True):
    anchors: list[MysteryAnchor] = Field(min_length=1, max_length=7)
    inter_anchor_dependencies: dict[str, list[str]] = Field(default_factory=dict)
    applicable_categories: list[str] = Field(default_factory=list)

