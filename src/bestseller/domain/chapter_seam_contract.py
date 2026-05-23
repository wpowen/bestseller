from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ChapterSeamContract(BaseModel, frozen=True):
    chapter_no: int = Field(ge=1)
    inherits_from_prev: list[str] = Field(default_factory=list)
    required_callbacks: list[str] = Field(default_factory=list)
    carry_forward_state: dict[str, str] = Field(default_factory=dict)
    opening_state: str = ""
    forbidden_resets: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_continuity_for_non_opening_chapter(self) -> ChapterSeamContract:
        if self.chapter_no <= 1:
            return self
        if not (self.inherits_from_prev or self.required_callbacks or self.opening_state):
            raise ValueError("chapter seam contract must carry prior-chapter continuity")
        return self


def seam_contract_from_mapping(payload: object) -> ChapterSeamContract | None:
    if isinstance(payload, ChapterSeamContract):
        return payload
    if isinstance(payload, dict):
        return ChapterSeamContract.model_validate(payload)
    return None
