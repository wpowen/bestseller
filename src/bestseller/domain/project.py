from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from bestseller.domain.enums import ChapterStatus, ProjectStatus, SceneStatus, VolumeStatus


def _dedupe_string_list(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


class MarketPositioningConfig(BaseModel):
    platform_target: str = Field(default="番茄小说", min_length=1, max_length=120)
    content_mode: str = Field(default="中文网文长篇连载", min_length=1, max_length=120)
    prompt_pack_key: str | None = Field(default=None, max_length=120)
    reader_promise: str | None = Field(default=None, max_length=500)
    selling_points: list[str] = Field(default_factory=list)
    trope_keywords: list[str] = Field(default_factory=list)
    hook_keywords: list[str] = Field(default_factory=list)
    opening_strategy: str = Field(
        default="开篇先亮出主角差异化优势、异常事件、即时利益与明确危险。",
        min_length=1,
        max_length=300,
    )
    chapter_hook_strategy: str = Field(
        default="章节尾部必须留下强迫读者继续阅读的问题、威胁或利益诱因。",
        min_length=1,
        max_length=300,
    )
    hook_deadline_words: int = Field(default=1500, ge=200, le=20000)
    pacing_profile: str = Field(default="fast", min_length=1, max_length=64)
    payoff_rhythm: str = Field(default="短回报密集，长回报递延", min_length=1, max_length=120)
    update_strategy: str = Field(default="日更连载", min_length=1, max_length=120)

    @field_validator("selling_points", "trope_keywords", "hook_keywords")
    @classmethod
    def normalize_list_fields(cls, values: list[str]) -> list[str]:
        return _dedupe_string_list(values)


class CharacterEngineConfig(BaseModel):
    protagonist_archetype: str | None = Field(default=None, max_length=120)
    protagonist_core_drive: str | None = Field(default=None, max_length=200)
    golden_finger: str | None = Field(default=None, max_length=200)
    growth_curve: str = Field(default="阶段升级，持续抬高代价与成就感", min_length=1, max_length=200)
    romance_mode: str = Field(default="弱感情线", min_length=1, max_length=64)
    relationship_tension: str = Field(
        default="合作与猜疑并行，关系在利益绑定和真心靠近之间摆动。",
        min_length=1,
        max_length=220,
    )
    antagonist_mode: str = Field(default="层级递进的系统性对手", min_length=1, max_length=120)
    ensemble_mode: str = Field(default="配角围绕主角选择形成镜像与反差", min_length=1, max_length=120)


class WorldDesignConfig(BaseModel):
    worldbuilding_density: str = Field(default="medium", min_length=1, max_length=64)
    info_reveal_strategy: str = Field(
        default="先冲突后解释，背景信息嵌入行动、交易、对抗和后果里释放。",
        min_length=1,
        max_length=220,
    )
    rule_hardness: str = Field(default="hard", min_length=1, max_length=64)
    power_system_style: str | None = Field(default=None, max_length=120)
    mystery_density: str = Field(default="medium", min_length=1, max_length=64)
    setting_tags: list[str] = Field(default_factory=list)

    @field_validator("setting_tags")
    @classmethod
    def normalize_setting_tags(cls, values: list[str]) -> list[str]:
        return _dedupe_string_list(values)


class StylePreferenceConfig(BaseModel):
    pov_type: str = Field(default="third-limited", min_length=1, max_length=64)
    tense: str = Field(default="present", min_length=1, max_length=64)
    tone_keywords: list[str] = Field(default_factory=list)
    prose_style: str = Field(default="commercial-web-serial", min_length=1, max_length=120)
    sentence_style: str = Field(default="mixed", min_length=1, max_length=64)
    info_density: str = Field(default="medium", min_length=1, max_length=64)
    dialogue_ratio: float = Field(default=0.4, ge=0.0, le=1.0)
    taboo_topics: list[str] = Field(default_factory=list)
    taboo_words: list[str] = Field(default_factory=list)
    reference_works: list[str] = Field(default_factory=list)
    custom_rules: list[str] = Field(default_factory=list)

    @field_validator(
        "tone_keywords",
        "taboo_topics",
        "taboo_words",
        "reference_works",
        "custom_rules",
    )
    @classmethod
    def normalize_style_lists(cls, values: list[str]) -> list[str]:
        return _dedupe_string_list(values)


class SerializationStrategyConfig(BaseModel):
    opening_mandate: str = Field(
        default="开篇要尽快亮出主角差异化优势、核心异变、短期利益与即时危险。",
        min_length=1,
        max_length=220,
    )
    first_three_chapter_goal: str = Field(
        default="前三章完成主角卖点、世界异常、短期目标、第一轮反压和追读钩子。",
        min_length=1,
        max_length=220,
    )
    scene_drive_rule: str = Field(
        default="每场都要有目标、阻碍、升级、信息变化和尾钩，避免纯设定说明。",
        min_length=1,
        max_length=220,
    )
    exposition_rule: str = Field(
        default="背景设定轻解释，优先通过动作、结果、交易与冲突呈现。",
        min_length=1,
        max_length=220,
    )
    chapter_ending_rule: str = Field(
        default="每章结尾至少留下一个未解决问题、反转或更大的危险。",
        min_length=1,
        max_length=220,
    )
    free_chapter_strategy: str = Field(
        default="免费期强调高密度钩子、爽点和连续升级，尽量避免长时间铺垫。",
        min_length=1,
        max_length=220,
    )


class WritingProfile(BaseModel):
    market: MarketPositioningConfig = Field(default_factory=MarketPositioningConfig)
    character: CharacterEngineConfig = Field(default_factory=CharacterEngineConfig)
    world: WorldDesignConfig = Field(default_factory=WorldDesignConfig)
    style: StylePreferenceConfig = Field(default_factory=StylePreferenceConfig)
    serialization: SerializationStrategyConfig = Field(default_factory=SerializationStrategyConfig)


class ProjectCreate(BaseModel):
    slug: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    genre: str = Field(min_length=1, max_length=100)
    sub_genre: str | None = Field(default=None, max_length=100)
    audience: str | None = Field(default=None, max_length=200)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    target_word_count: int = Field(gt=0)
    target_chapters: int = Field(gt=0)
    metadata: dict[str, object] = Field(default_factory=dict)
    writing_profile: WritingProfile | None = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
        if not normalized or any(char not in allowed for char in normalized):
            raise ValueError("slug may only contain lowercase letters, numbers, '-' and '_'.")
        return normalized


class ProjectRead(BaseModel):
    id: UUID
    slug: str
    title: str
    genre: str
    sub_genre: str | None
    audience: str | None
    language: str
    target_word_count: int
    target_chapters: int
    status: ProjectStatus


class VolumeCreate(BaseModel):
    volume_number: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    theme: str | None = None
    goal: str | None = None
    obstacle: str | None = None
    target_word_count: int | None = Field(default=None, gt=0)
    target_chapter_count: int | None = Field(default=None, gt=0)
    status: VolumeStatus = VolumeStatus.PLANNED


class ChapterCreate(BaseModel):
    chapter_number: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=200)
    chapter_goal: str = Field(min_length=1)
    opening_situation: str | None = None
    main_conflict: str | None = None
    hook_type: str | None = None
    hook_description: str | None = None
    target_word_count: int = Field(default=5500, gt=0)
    volume_number: int = Field(default=1, gt=0)
    status: ChapterStatus = ChapterStatus.PLANNED


class SceneCardCreate(BaseModel):
    scene_number: int = Field(gt=0)
    scene_type: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=200)
    time_label: str | None = None
    participants: list[str] = Field(default_factory=list)
    purpose: dict[str, object] = Field(default_factory=dict)
    entry_state: dict[str, object] = Field(default_factory=dict)
    exit_state: dict[str, object] = Field(default_factory=dict)
    target_word_count: int = Field(default=1000, gt=0)
    status: SceneStatus = SceneStatus.PLANNED


class SceneStructureRead(BaseModel):
    id: UUID
    scene_number: int = Field(gt=0)
    title: str | None = None
    scene_type: str = Field(min_length=1)
    status: SceneStatus
    participants: list[str] = Field(default_factory=list)
    target_word_count: int = Field(ge=0)
    current_draft_version_no: int | None = None
    current_word_count: int | None = None


class ChapterStructureRead(BaseModel):
    id: UUID
    chapter_number: int = Field(gt=0)
    title: str | None = None
    volume_number: int | None = None
    chapter_goal: str = Field(min_length=1)
    status: ChapterStatus
    target_word_count: int = Field(ge=0)
    current_word_count: int = Field(ge=0)
    current_draft_version_no: int | None = None
    scenes: list[SceneStructureRead] = Field(default_factory=list)


class VolumeStructureRead(BaseModel):
    id: UUID
    volume_number: int = Field(gt=0)
    title: str = Field(min_length=1)
    status: VolumeStatus
    target_word_count: int | None = None
    target_chapter_count: int | None = None
    chapters: list[ChapterStructureRead] = Field(default_factory=list)


class ProjectStructureRead(BaseModel):
    project_id: UUID
    project_slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: ProjectStatus
    target_word_count: int = Field(ge=0)
    target_chapters: int = Field(ge=0)
    current_volume_number: int = Field(ge=0)
    current_chapter_number: int = Field(ge=0)
    total_chapters: int = Field(ge=0)
    total_scenes: int = Field(ge=0)
    volumes: list[VolumeStructureRead] = Field(default_factory=list)
