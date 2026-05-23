from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

MaterialCategory = Literal["food", "clothing", "tool", "ornament", "music", "vehicle"]


class MaterialPaletteItem(BaseModel, frozen=True):
    category: MaterialCategory
    name: str = Field(min_length=1)
    sensory_hook: str = Field(min_length=1)
    class_signal: str = Field(min_length=1)


class CulturalTextureModule(BaseModel, frozen=True):
    palette: list[MaterialPaletteItem] = Field(min_length=8)
    daily_rituals: list[str] = Field(default_factory=list)
    taboo_behaviors: list[str] = Field(default_factory=list)
    aesthetic_zeitgeist: str = Field(min_length=1)
    applicable_categories: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_palette_diversity(self) -> CulturalTextureModule:
        categories = {item.category for item in self.palette}
        if len(categories) < 4:
            raise ValueError("cultural palette must cover at least 4 material categories")
        return self

