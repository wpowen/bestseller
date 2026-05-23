from __future__ import annotations

from pydantic import BaseModel, Field


class ReligiousOrganization(BaseModel, frozen=True):
    name: str = Field(min_length=1)
    deities: list[str] = Field(default_factory=list)
    core_doctrine: str = Field(min_length=1)
    ritual_calendar: list[str] = Field(default_factory=list)
    hierarchy: list[str] = Field(default_factory=list)
    sacred_sites: list[str] = Field(default_factory=list)
    conflict_with: list[str] = Field(default_factory=list)
    schism_history: str | None = None
    applicable_categories: list[str] = Field(default_factory=list)

