from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MetaLayerContract(BaseModel, frozen=True):
    layer_type: Literal["preface", "volume_epigraph", "extra", "afterword", "reader_letter"]
    placement: str = Field(min_length=1)
    narrative_function: str = Field(min_length=1)
    voice_rule: str = Field(min_length=1)
    spoiler_boundary: str = Field(min_length=1)
    payoff_targets: list[str] = Field(default_factory=list)

