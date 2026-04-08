from __future__ import annotations

from uuid import UUID

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from bestseller.domain.enums import ChapterStatus, ProjectStatus, ProjectType, SceneStatus, VolumeStatus


def _dedupe_string_list(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


class MarketPositioningConfig(BaseModel):
    platform_target: str = Field(default="番茄小说", min_length=1, max_length=4000)
    content_mode: str = Field(default="中文网文长篇连载", min_length=1, max_length=4000)
    prompt_pack_key: str | None = Field(default=None, max_length=4000)
    reader_promise: str | None = Field(default=None, max_length=4000)
    selling_points: list[str] = Field(default_factory=list)
    trope_keywords: list[str] = Field(default_factory=list)
    hook_keywords: list[str] = Field(default_factory=list)
    opening_strategy: str = Field(
        default="开篇先亮出主角差异化优势、异常事件、即时利益与明确危险。",
        min_length=1,
        max_length=4000,
    )
    chapter_hook_strategy: str = Field(
        default="章节尾部必须留下强迫读者继续阅读的问题、威胁或利益诱因。",
        min_length=1,
        max_length=4000,
    )
    hook_deadline_words: int = Field(default=1500, ge=200, le=20000)
    pacing_profile: str = Field(default="fast", min_length=1, max_length=4000)
    payoff_rhythm: str = Field(default="短回报密集，长回报递延", min_length=1, max_length=4000)
    update_strategy: str = Field(default="日更连载", min_length=1, max_length=4000)

    @field_validator("selling_points", "trope_keywords", "hook_keywords")
    @classmethod
    def normalize_list_fields(cls, values: list[str]) -> list[str]:
        return _dedupe_string_list(values)


class CharacterEngineConfig(BaseModel):
    protagonist_archetype: str | None = Field(default=None, max_length=4000)
    protagonist_core_drive: str | None = Field(default=None, max_length=4000)
    golden_finger: str | None = Field(default=None, max_length=4000)
    growth_curve: str = Field(default="阶段升级，持续抬高代价与成就感", min_length=1, max_length=4000)
    romance_mode: str = Field(default="弱感情线", min_length=1, max_length=4000)
    relationship_tension: str = Field(
        default="合作与猜疑并行，关系在利益绑定和真心靠近之间摆动。",
        min_length=1,
        max_length=4000,
    )
    antagonist_mode: str = Field(default="层级递进的系统性对手", min_length=1, max_length=4000)
    ensemble_mode: str = Field(default="配角围绕主角选择形成镜像与反差", min_length=1, max_length=4000)


class WorldDesignConfig(BaseModel):
    worldbuilding_density: str = Field(default="medium", min_length=1, max_length=4000)
    info_reveal_strategy: str = Field(
        default="先冲突后解释，背景信息嵌入行动、交易、对抗和后果里释放。",
        min_length=1,
        max_length=4000,
    )
    rule_hardness: str = Field(default="hard", min_length=1, max_length=4000)
    power_system_style: str | None = Field(default=None, max_length=4000)
    mystery_density: str = Field(default="medium", min_length=1, max_length=4000)
    setting_tags: list[str] = Field(default_factory=list)

    @field_validator("setting_tags")
    @classmethod
    def normalize_setting_tags(cls, values: list[str]) -> list[str]:
        return _dedupe_string_list(values)


class StylePreferenceConfig(BaseModel):
    pov_type: str = Field(default="third-limited", min_length=1, max_length=4000)
    tense: str = Field(default="present", min_length=1, max_length=4000)
    tone_keywords: list[str] = Field(default_factory=list)
    prose_style: str = Field(default="commercial-web-serial", min_length=1, max_length=4000)
    sentence_style: str = Field(default="mixed", min_length=1, max_length=4000)
    info_density: str = Field(default="medium", min_length=1, max_length=4000)
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
        max_length=4000,
    )
    first_three_chapter_goal: str = Field(
        default="前三章完成主角卖点、世界异常、短期目标、第一轮反压和追读钩子。",
        min_length=1,
        max_length=4000,
    )
    scene_drive_rule: str = Field(
        default="每场都要有目标、阻碍、升级、信息变化和尾钩，避免纯设定说明。",
        min_length=1,
        max_length=4000,
    )
    exposition_rule: str = Field(
        default="背景设定轻解释，优先通过动作、结果、交易与冲突呈现。",
        min_length=1,
        max_length=4000,
    )
    chapter_ending_rule: str = Field(
        default="每章结尾至少留下一个未解决问题、反转或更大的危险。",
        min_length=1,
        max_length=4000,
    )
    free_chapter_strategy: str = Field(
        default="免费期强调高密度钩子、爽点和连续升级，尽量避免长时间铺垫。",
        min_length=1,
        max_length=4000,
    )


_IF_VALID_GENRES = {"都市逆袭", "修仙升级", "悬疑生存", "职场商战", "末日爽文"}
_IF_VALID_ROLES = {"盟友", "宿敌", "红颜", "师尊", "家族", "中立", "反派"}


class IFStatConfig(BaseModel):
    combat: int = Field(default=10, ge=0, le=100)
    fame: int = Field(default=5, ge=0, le=100)
    strategy: int = Field(default=20, ge=0, le=100)
    wealth: int = Field(default=5, ge=0, le=100)
    charm: int = Field(default=10, ge=0, le=100)
    darkness: int = Field(default=0, ge=0, le=100)
    destiny: int = Field(default=30, ge=0, le=100)


class IFCharacterDraft(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    role: str = Field(description="盟友|宿敌|红颜|师尊|家族|中立|反派")
    description: str = Field(default="", max_length=4000)

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in _IF_VALID_ROLES:
            raise ValueError(f"role must be one of {_IF_VALID_ROLES}")
        return v


class InteractiveFictionConfig(BaseModel):
    enabled: bool = False

    # Concept 参数（对应 story-factory concept.json 字段）
    if_genre: str = Field(default="修仙升级", max_length=20)
    target_chapters: int = Field(default=100, ge=10, le=2000)
    free_chapters: int = Field(default=20, ge=5, le=100)
    premise: str = Field(default="", max_length=4000)
    protagonist: str = Field(default="", max_length=4000)
    core_conflict: str = Field(default="", max_length=4000)
    tone: str = Field(default="爽快、热血、有悬念", max_length=4000)
    arc_structure: list[str] = Field(default_factory=list)
    key_characters: list[IFCharacterDraft] = Field(default_factory=list)
    initial_stats: IFStatConfig = Field(default_factory=IFStatConfig)

    # Generation 参数（控制 Prompt 内容）
    chapter_text_length: str = Field(default="2500-3500", max_length=20)
    choice_nodes_per_chapter: str = Field(default="2-3", max_length=10)
    text_node_length: str = Field(default="150-250", max_length=20)
    arc_batch_size: int = Field(default=15, ge=5, le=100)
    parallel_chapter_batch: int = Field(default=8, ge=1, le=20)

    # Acts 结构（全书幕数）
    act_count: int = Field(default=5, ge=2, le=8)

    # 卷结构（Volume）— 长篇核心机制
    # volume_size=0 表示不启用卷层级（适合 <200 章短篇）
    volume_size: int = Field(default=100, ge=0, le=500)

    # 硬分支控制
    enable_branches: bool = Field(default=False)
    branch_count: int = Field(default=2, ge=0, le=4)
    branch_chapter_span: int = Field(default=30, ge=10, le=80)

    # 上下文模式
    context_mode: Literal["basic", "tiered", "full"] = Field(default="tiered")
    snapshot_interval: int = Field(default=50, ge=25, le=100)

    # 爽点密度
    power_moment_interval: int = Field(default=5, ge=3, le=10)

    @field_validator("if_genre")
    @classmethod
    def validate_genre(cls, v: str) -> str:
        if v not in _IF_VALID_GENRES:
            raise ValueError(f"if_genre must be one of {_IF_VALID_GENRES}")
        return v


class WritingProfile(BaseModel):
    market: MarketPositioningConfig = Field(default_factory=MarketPositioningConfig)
    character: CharacterEngineConfig = Field(default_factory=CharacterEngineConfig)
    world: WorldDesignConfig = Field(default_factory=WorldDesignConfig)
    style: StylePreferenceConfig = Field(default_factory=StylePreferenceConfig)
    serialization: SerializationStrategyConfig = Field(default_factory=SerializationStrategyConfig)
    interactive_fiction: InteractiveFictionConfig = Field(default_factory=InteractiveFictionConfig)


class ProjectCreate(BaseModel):
    slug: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=1, max_length=4000)
    genre: str = Field(min_length=1, max_length=4000)
    sub_genre: str | None = Field(default=None, max_length=4000)
    audience: str | None = Field(default=None, max_length=4000)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    target_word_count: int = Field(gt=0)
    target_chapters: int = Field(gt=0)
    project_type: ProjectType = ProjectType.LINEAR
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
    project_type: ProjectType = ProjectType.LINEAR


class VolumeCreate(BaseModel):
    volume_number: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=4000)
    theme: str | None = None
    goal: str | None = None
    obstacle: str | None = None
    target_word_count: int | None = Field(default=None, gt=0)
    target_chapter_count: int | None = Field(default=None, gt=0)
    status: VolumeStatus = VolumeStatus.PLANNED


class ChapterCreate(BaseModel):
    chapter_number: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=4000)
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
    scene_type: str = Field(min_length=1, max_length=4000)
    title: str | None = Field(default=None, max_length=4000)
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
