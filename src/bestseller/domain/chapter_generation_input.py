from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ChapterGenerationInputBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    project: Mapping[str, Any] = Field(default_factory=dict)
    chapter: Mapping[str, Any] = Field(default_factory=dict)
    scenes: tuple[Mapping[str, Any], ...] = ()
    methodology_blocks: Mapping[str, str] = Field(default_factory=dict)
    continuity: Mapping[str, Any] = Field(default_factory=dict)
    story_bible: Mapping[str, Any] = Field(default_factory=dict)
    chapter_contract: Mapping[str, Any] = Field(default_factory=dict)
    acceptance_contract: Mapping[str, Any] = Field(default_factory=dict)
    quality_targets: Mapping[str, Any] = Field(default_factory=dict)
    required_context_keys: tuple[str, ...] = ()
    missing_context_keys: tuple[str, ...] = ()
    schema_version: str = "chapter-generation-input.v1"

    @computed_field(return_type=bool)
    @property
    def ready(self) -> bool:
        return not self.missing_context_keys

    @computed_field(return_type=float)
    @property
    def coverage(self) -> float:
        total = len(self.required_context_keys)
        if total == 0:
            return 1.0
        return max(0.0, min(1.0, (total - len(self.missing_context_keys)) / total))
