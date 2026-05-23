from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Festival(BaseModel, frozen=True):
    name: str = Field(min_length=1)
    season: str = Field(min_length=1)
    activities: list[str] = Field(default_factory=list)
    symbolism: str = Field(min_length=1)
    plot_hooks: list[str] = Field(default_factory=list)


class CalendarSystem(BaseModel, frozen=True):
    calendar_type: Literal["lunar", "solar", "fictional", "mixed"]
    major_festivals: list[Festival] = Field(default_factory=list)
    seasonal_phases: list[str] = Field(default_factory=list)
    forbidden_dates: list[str] = Field(default_factory=list)
    applicable_categories: list[str] = Field(default_factory=list)

    def festivals_for_plot_hook(self, hook: str) -> list[Festival]:
        needle = hook.strip()
        if not needle:
            return []
        return [
            festival
            for festival in self.major_festivals
            if needle in festival.plot_hooks or needle in festival.symbolism
        ]

