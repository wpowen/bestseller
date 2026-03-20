from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class SceneOutlineInput(BaseModel):
    scene_number: int = Field(gt=0)
    scene_type: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=200)
    time_label: str | None = None
    participants: list[str] = Field(default_factory=list)
    purpose: dict[str, Any] = Field(default_factory=dict)
    entry_state: dict[str, Any] = Field(default_factory=dict)
    exit_state: dict[str, Any] = Field(default_factory=dict)
    target_word_count: int = Field(default=1000, gt=0)


class ChapterOutlineInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chapter_number: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=200)
    chapter_goal: str = Field(
        min_length=1,
        validation_alias=AliasChoices("chapter_goal", "goal"),
        serialization_alias="goal",
    )
    opening_situation: str | None = None
    main_conflict: str | None = None
    hook_type: str | None = None
    hook_description: str | None = None
    volume_number: int = Field(default=1, gt=0)
    target_word_count: int = Field(default=3000, gt=0)
    scenes: list[SceneOutlineInput] = Field(default_factory=list)


class ChapterOutlineBatchInput(BaseModel):
    batch_name: str = Field(default="default-batch", min_length=1, max_length=200)
    chapters: list[ChapterOutlineInput] = Field(default_factory=list)


class WorkflowMaterializationResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    batch_name: str
    chapters_created: int
    scenes_created: int
    source_artifact_id: UUID | None = None
