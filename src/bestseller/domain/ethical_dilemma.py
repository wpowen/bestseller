from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class EthicalDilemmaSlot(BaseModel, frozen=True):
    chapter_window: tuple[int, int]
    dilemma_kind: Literal[
        "one_vs_many",
        "belief_vs_kin",
        "law_vs_compassion",
        "loyalty_vs_truth",
        "self_vs_collective",
        "short_term_vs_long",
    ]
    competing_values: tuple[str, str]
    involved_characters: list[UUID] = Field(default_factory=list)
    intended_choice: Literal["A", "B", "abstain", "open"]
    consequence_for_unchosen: str = Field(min_length=1)

    @model_validator(mode="after")
    def _require_real_value_collision(self) -> EthicalDilemmaSlot:
        start, end = self.chapter_window
        if start < 1 or end < start:
            raise ValueError("chapter_window must be a positive inclusive range")
        a, b = (value.strip() for value in self.competing_values)
        if not a or not b or a == b:
            raise ValueError("ethical dilemma requires two distinct competing values")
        return self


class EthicalDilemmaKernel(BaseModel, frozen=True):
    slots: list[EthicalDilemmaSlot] = Field(default_factory=list)
    minimum_cadence_chapters: int = Field(default=12, ge=1)
    applicable_categories: list[str] = Field(default_factory=list)

