from __future__ import annotations

from pydantic import BaseModel, Field


class ZeitgeistContract(BaseModel, frozen=True):
    label: str = Field(min_length=1)
    core_anxiety: str = Field(min_length=1)
    dominant_aspiration: str = Field(min_length=1)
    aesthetic_pressure: str = Field(min_length=1)
    social_mobility_rule: str = Field(min_length=1)
    volume_injections: dict[int, str] = Field(default_factory=dict)
    applicable_categories: list[str] = Field(default_factory=list)

    def injection_for_volume(self, volume: int) -> str:
        return self.volume_injections.get(volume) or self.aesthetic_pressure

