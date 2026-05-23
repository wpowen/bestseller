from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


BeatType = Literal["opening", "action", "dialogue", "interior", "reveal", "cliff"]
EndingFormat = Literal["action", "image", "reveal"]


class BeatCamera(BaseModel):
    location: str = ""
    time: str = ""
    weather: str = ""


class BeatDialoguePlan(BaseModel):
    count: int = Field(default=0, ge=0)
    speaker: str = ""
    intent: str = ""
    forbidden_explicit: list[str] = Field(default_factory=list)


class SceneBeat(BaseModel):
    beat_id: str
    beat_type: BeatType
    camera: BeatCamera = Field(default_factory=BeatCamera)
    characters_present: list[str] = Field(default_factory=list)
    external_event: list[str] = Field(default_factory=list)
    interior_reaction: list[str] = Field(default_factory=list)
    sensory_anchor: dict[str, str] = Field(default_factory=dict)
    dialogue_lines: BeatDialoguePlan = Field(default_factory=BeatDialoguePlan)
    beat_payoff: list[str] = Field(default_factory=list)
    banned_devices: list[str] = Field(default_factory=list)
    word_budget: tuple[int, int] = (350, 550)
    ending_format: EndingFormat | None = None

    @model_validator(mode="after")
    def _validate_visible_event(self) -> "SceneBeat":
        if self.beat_type in {"opening", "action", "reveal", "cliff"} and not self.external_event:
            raise ValueError(f"{self.beat_id} requires at least one visible event")
        if self.beat_type == "cliff" and self.ending_format is None:
            raise ValueError("cliff beat requires ending_format")
        return self


class SceneBeatSheet(BaseModel):
    chapter_number: int = Field(ge=1)
    scene_number: int = Field(ge=1)
    beats: list[SceneBeat] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_last_beat(self) -> "SceneBeatSheet":
        last = self.beats[-1]
        if last.beat_type != "cliff":
            raise ValueError("last beat must be a cliff beat")
        if not last.external_event:
            raise ValueError("last beat must end on an in-scene action/image/reveal")
        return self

    @property
    def toxic_design_terms(self) -> tuple[str, ...]:
        return (
            "本章",
            "本卷",
            "这一章",
            "章末",
            "卷末",
            "钩子",
            "长线",
            "主线",
            "副线",
            "卖点",
            "承诺",
            "hook",
            "selling",
            "promise",
        )


__all__ = [
    "BeatCamera",
    "BeatDialoguePlan",
    "BeatType",
    "EndingFormat",
    "SceneBeat",
    "SceneBeatSheet",
]
