from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

_FUNCTIONAL_TITLE_PREFIXES_ZH = {
    "暗潮",
    "盲区",
    "裂痕",
    "回声",
    "风眼",
    "余烬",
    "伏线",
    "变局",
    "断点",
    "逆流",
    "边界",
    "悬灯",
    "浮标",
    "锈迹",
    "夜隙",
    "残局",
    "沉渊",
    "灰幕",
    "雾锁",
    "棱线",
    "铁壁",
    "荒火",
    "冷锋",
    "碎影",
}
_FUNCTIONAL_TITLE_SUFFIXES_ZH = {
    "初现",
    "入局",
    "投石",
    "试探",
    "铺火",
    "露锋",
    "破冰",
    "起手",
    "掀幕",
    "落子",
    "追索",
    "摸底",
    "拆解",
    "寻隙",
    "探针",
    "回查",
    "溯源",
    "揭层",
    "织网",
    "破壁",
    "加压",
    "围拢",
    "失衡",
    "封锁",
    "死线",
    "逼近",
    "绞杀",
    "窒息",
    "崩弦",
    "缩网",
    "反咬",
    "逆转",
    "偏航",
    "脱钩",
    "换轨",
    "回火",
    "翻盘",
    "倒戈",
    "破局",
    "重铸",
    "爆裂",
    "截断",
    "崩口",
    "闯线",
    "归零",
    "掀牌",
    "决堤",
    "焚天",
    "碎锁",
    "终幕",
}


def _looks_like_functional_chapter_title(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 8:
        return False
    return any(text.startswith(prefix) for prefix in _FUNCTIONAL_TITLE_PREFIXES_ZH) and any(
        text.endswith(suffix) for suffix in _FUNCTIONAL_TITLE_SUFFIXES_ZH
    )


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
    target_word_count: int = Field(default=700, gt=0)

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

        # story_task / emotion_task and newer planner aliases -> purpose dict
        purpose = data.get("purpose")
        if isinstance(purpose, str):
            purpose = {"story": purpose}
        elif not isinstance(purpose, dict):
            purpose = {}
        story_parts: list[str] = []
        for key in (
            "story_task",
            "story_emotion_task",
            "scene_purpose",
            "scene_goal",
            "plot_task",
        ):
            value = data.pop(key, None)
            if isinstance(value, str) and value.strip():
                story_parts.append(value.strip())
        if story_parts and "story" not in purpose:
            purpose["story"] = "；".join(story_parts)
        if "emotion_task" in data and "emotion" not in purpose:
            purpose["emotion"] = data.pop("emotion_task")
        if "aesthetic_goal" in data:
            aesthetic_goal = data.pop("aesthetic_goal")
            if isinstance(aesthetic_goal, str) and aesthetic_goal.strip():
                if "emotion" not in purpose:
                    purpose["emotion"] = aesthetic_goal.strip()
                elif "story" in purpose and aesthetic_goal not in str(purpose["story"]):
                    purpose["story"] = f"{purpose['story']}；{aesthetic_goal.strip()}"
        if "philosophical_anchor" in data:
            philosophical_anchor = data.pop("philosophical_anchor")
            if isinstance(philosophical_anchor, str) and philosophical_anchor.strip():
                if "story" not in purpose:
                    purpose["story"] = philosophical_anchor.strip()
                else:
                    purpose["story"] = f"{purpose['story']}；{philosophical_anchor.strip()}"
        if purpose:
            data["purpose"] = purpose
        # scene_location / scene_setting -> time_label
        for key in ("scene_location", "scene_setting", "setting", "location", "place"):
            if key in data and not data.get("time_label"):
                data["time_label"] = data.pop(key)
                break
        # participant aliases
        for key in ("active_characters", "characters", "cast", "participant_names"):
            if key not in data or data.get("participants"):
                continue
            raw_participants = data.pop(key)
            if isinstance(raw_participants, str):
                data["participants"] = [
                    item.strip()
                    for item in raw_participants.replace("，", ",").replace("、", ",").split(",")
                    if item.strip()
                ]
            elif isinstance(raw_participants, list):
                participants: list[str] = []
                for item in raw_participants:
                    if isinstance(item, str) and item.strip():
                        participants.append(item.strip())
                    elif isinstance(item, dict):
                        name = item.get("name") or item.get("character")
                        if isinstance(name, str) and name.strip():
                            participants.append(name.strip())
                data["participants"] = participants
        # scene_title → title
        if "scene_title" in data and not data.get("title"):
            data["title"] = data.pop("scene_title")
        return data


class ChapterOutlineInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chapter_number: int = Field(gt=0)
    title: str | None = Field(
        default=None,
        max_length=4000,
        validation_alias=AliasChoices("title", "chapter_title"),
    )
    chapter_goal: str = Field(
        default="推动本章剧情发展",
        validation_alias=AliasChoices("chapter_goal", "goal"),
        serialization_alias="goal",
    )
    opening_situation: str | None = None
    main_conflict: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "main_conflict",
            "chapter_main_conflict",
            "conflict",
            "core_conflict",
        ),
    )
    hook_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("hook_type", "chapter_hook_type"),
    )
    hook_description: str | None = None
    causal_contract: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "causal_contract",
            "causality_contract",
            "chapter_causal_skeleton",
            "causal_skeleton",
            "reader_desire_chain",
        ),
    )
    volume_number: int = Field(default=1, gt=0)
    target_word_count: int = Field(default=2200, gt=0)
    scenes: list[SceneOutlineInput] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_chapter_fields(cls, data: Any) -> Any:
        """Normalize common LLM aliases before schema validation."""
        if not isinstance(data, dict):
            return data

        story_title = data.get("chapter_title") or data.get("subtitle")
        if story_title and (
            not data.get("title") or _looks_like_functional_chapter_title(data.get("title"))
        ):
            data["title"] = story_title

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
