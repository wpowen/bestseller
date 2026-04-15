from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class SceneOutlineInput(BaseModel):
    """Scene outline input with resilient parsing for LLM output variations.

    MiniMax M2.7 (and other LLMs) sometimes return non-standard field names:
      - story_task → purpose.story
      - emotion_task → purpose.emotion
      - scene_location → time_label
    The model_validator normalizes these before Pydantic field validation.
    """

    model_config = ConfigDict(populate_by_name=True)

    scene_number: int = Field(gt=0)
    # Default to "development" — LLMs sometimes omit this field entirely.
    scene_type: str = Field(
        default="development",
        max_length=4000,
        validation_alias=AliasChoices("scene_type", "type"),
    )
    title: str | None = Field(default=None, max_length=4000)
    time_label: str | None = None
    participants: list[str] = Field(default_factory=list)
    purpose: dict[str, Any] = Field(default_factory=dict)
    entry_state: dict[str, Any] = Field(default_factory=dict)
    exit_state: dict[str, Any] = Field(default_factory=dict)
    target_word_count: int = Field(default=1000, gt=0)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_fields(cls, data: Any) -> Any:
        """Map non-standard LLM field names to expected schema fields."""
        if not isinstance(data, dict):
            return data

        # ── scene_number: MiniMax uses float like 1.1, 1.2, 2.1 (chapter.scene)
        # Extract the fractional part as the scene-within-chapter ordinal.
        sn = data.get("scene_number")
        if isinstance(sn, float):
            # e.g. 5.2 → scene 2 within chapter 5
            frac = round((sn - int(sn)) * 10)
            data["scene_number"] = max(frac, 1)
        elif isinstance(sn, str):
            # "1.2" string → parse same logic
            try:
                fval = float(sn)
                frac = round((fval - int(fval)) * 10)
                data["scene_number"] = max(frac, 1)
            except ValueError:
                pass

        # story_task / emotion_task → purpose dict
        purpose = data.get("purpose")
        if not isinstance(purpose, dict):
            purpose = {}
        if "story_task" in data and "story" not in purpose:
            purpose["story"] = data.pop("story_task")
        if "emotion_task" in data and "emotion" not in purpose:
            purpose["emotion"] = data.pop("emotion_task")
        if purpose:
            data["purpose"] = purpose
        # scene_location → time_label
        if "scene_location" in data and not data.get("time_label"):
            data["time_label"] = data.pop("scene_location")
        # scene_title → title
        if "scene_title" in data and not data.get("title"):
            data["title"] = data.pop("scene_title")
        return data


class ChapterOutlineInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chapter_number: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=4000)
    chapter_goal: str = Field(
        default="推动本章剧情发展",
        validation_alias=AliasChoices("chapter_goal", "goal"),
        serialization_alias="goal",
    )
    opening_situation: str | None = None
    main_conflict: str | None = None
    hook_type: str | None = None
    hook_description: str | None = None
    volume_number: int = Field(default=1, gt=0)
    target_word_count: int = Field(default=5500, gt=0)
    scenes: list[SceneOutlineInput] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_chapter_fields(cls, data: Any) -> Any:
        """Auto-assign scene_number to scenes if LLM omitted them."""
        if not isinstance(data, dict):
            return data
        scenes = data.get("scenes")
        if isinstance(scenes, list):
            for idx, scene in enumerate(scenes):
                if isinstance(scene, dict):
                    sn = scene.get("scene_number")
                    if sn is None:
                        # Missing entirely — assign 1-based index
                        scene["scene_number"] = idx + 1
                    elif isinstance(sn, float) and sn != int(sn):
                        # Float like 5.2 — will be handled by SceneOutlineInput
                        # but if extraction gives 0, override with index
                        frac = round((sn - int(sn)) * 10)
                        if frac < 1:
                            scene["scene_number"] = idx + 1
        return data


class ChapterOutlineBatchInput(BaseModel):
    batch_name: str = Field(default="default-batch", min_length=1, max_length=4000)
    chapters: list[ChapterOutlineInput] = Field(default_factory=list)


class WorkflowMaterializationResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    batch_name: str
    chapters_created: int
    scenes_created: int
    source_artifact_id: UUID | None = None
