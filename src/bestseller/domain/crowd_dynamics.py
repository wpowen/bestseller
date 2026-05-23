from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CrowdScene(BaseModel, frozen=True):
    crowd_size_class: Literal["small", "medium", "large", "massive"]
    initial_mood: str = Field(min_length=1)
    triggering_event: str = Field(min_length=1)
    mood_arc: list[str] = Field(min_length=2)
    rumor_seed: str | None = None
    factional_split: list[str] = Field(default_factory=list)
    resolution: Literal["dispersed", "violent", "co-opted", "leader_emerges"]

    @model_validator(mode="after")
    def _require_real_mood_movement(self) -> CrowdScene:
        if len({mood.strip() for mood in self.mood_arc if mood.strip()}) < 2:
            raise ValueError("crowd scene mood_arc must move through at least two moods")
        return self

