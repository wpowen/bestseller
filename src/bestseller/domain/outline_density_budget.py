from __future__ import annotations

from pydantic import BaseModel, Field


class OutlineDensityBudget(BaseModel, frozen=True):
    max_new_reveals: int = Field(default=2, ge=0)
    max_new_terms: int = Field(default=1, ge=0)
    max_new_named_characters: int = Field(default=2, ge=0)
    max_total_density_units: int = Field(default=5, ge=1)


class OutlineDensityInput(BaseModel, frozen=True):
    chapter_no: int = Field(ge=1)
    new_reveals: tuple[str, ...] = ()
    new_terms: tuple[str, ...] = ()
    new_named_characters: tuple[str, ...] = ()

    @property
    def density_units(self) -> int:
        return (
            len(self.new_reveals) * 2
            + len(self.new_terms)
            + len(self.new_named_characters)
        )
