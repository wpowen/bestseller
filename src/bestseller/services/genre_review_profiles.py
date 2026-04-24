"""Genre-specific review profiles for the novel generation quality system.

Each genre category defines custom scoring weights, signal keywords, finding
messages, plan rubrics, and LLM prompt overrides that let the review and
planning pipelines apply domain-appropriate standards.

The public API is:
    resolve_genre_review_profile(genre, sub_genre, genre_preset_key)
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class GenreReviewWeights(BaseModel):
    """Per-dimension scoring weights applied to scene-level reviews."""

    goal: float = Field(default=1.0, ge=0.0)
    conflict: float = Field(default=1.0, ge=0.0)
    conflict_clarity: float = Field(default=1.0, ge=0.0)
    emotion: float = Field(default=1.0, ge=0.0)
    emotional_movement: float = Field(default=1.0, ge=0.0)
    dialogue: float = Field(default=1.0, ge=0.0)
    style: float = Field(default=1.0, ge=0.0)
    voice_consistency: float = Field(default=1.0, ge=0.0)
    hook: float = Field(default=1.0, ge=0.0)
    hook_strength: float = Field(default=1.0, ge=0.0)
    payoff_density: float = Field(default=1.0, ge=0.0)
    contract_alignment: float = Field(default=1.0, ge=0.0)
    pacing_alignment: float = Field(default=1.0, ge=0.0)
    subplot_presence: float = Field(default=1.0, ge=0.0)
    scene_sequel_alignment: float = Field(default=1.0, ge=0.0)
    methodology_compliance: float = Field(default=0.8, ge=0.0)


class GenreChapterReviewWeights(BaseModel):
    """Per-dimension scoring weights applied to chapter-level reviews."""

    goal: float = Field(default=1.0, ge=0.0)
    coverage: float = Field(default=1.0, ge=0.0)
    coherence: float = Field(default=1.0, ge=0.0)
    continuity: float = Field(default=1.0, ge=0.0)
    main_plot_progression: float = Field(default=1.0, ge=0.0)
    subplot_progression: float = Field(default=1.0, ge=0.0)
    style: float = Field(default=1.0, ge=0.0)
    hook: float = Field(default=1.0, ge=0.0)
    ending_hook_effectiveness: float = Field(default=1.0, ge=0.0)
    volume_mission_alignment: float = Field(default=1.0, ge=0.0)
    pacing_rhythm: float = Field(default=1.0, ge=0.0)
    character_voice_distinction: float = Field(default=1.0, ge=0.0)
    thematic_resonance: float = Field(default=1.0, ge=0.0)
    contract_alignment: float = Field(default=1.0, ge=0.0)


class GenreSignalKeywords(BaseModel):
    """Keyword banks used by heuristic scoring to detect genre-relevant signals."""

    conflict_terms_zh: list[str] = Field(default_factory=list)
    conflict_terms_en: list[str] = Field(default_factory=list)
    emotion_terms_zh: list[str] = Field(default_factory=list)
    emotion_terms_en: list[str] = Field(default_factory=list)
    hook_terms_zh: list[str] = Field(default_factory=list)
    hook_terms_en: list[str] = Field(default_factory=list)
    info_terms_zh: list[str] = Field(default_factory=list)
    info_terms_en: list[str] = Field(default_factory=list)


class GenreFindingMessages(BaseModel):
    """Diagnostic messages surfaced when a dimension scores below threshold."""

    conflict_low_zh: str = Field(
        default="冲突呈现仍偏概述，缺少更具体的对抗动作和压力升级。",
    )
    conflict_low_en: str = Field(
        default="Conflict remains too summarized; add concrete opposition actions and escalating pressure.",
    )
    conflict_clarity_low_zh: str = Field(
        default="冲突被提到了，但双方立场、代价和选择边界还不够清楚。",
    )
    conflict_clarity_low_en: str = Field(
        default="The conflict is mentioned but both sides' stakes, costs, and decision boundaries are unclear.",
    )
    emotion_low_zh: str = Field(
        default="情绪变化被直接说明较多，缺少体感、动作和反应层面的表达。",
    )
    emotion_low_en: str = Field(
        default="Emotional shifts are too often told; use physical sensation, action, and reaction to convey them.",
    )
    emotional_movement_low_zh: str = Field(
        default="情绪线没有形成明确位移，人物的前后心理状态还不够可感。",
    )
    emotional_movement_low_en: str = Field(
        default="The emotional arc lacks clear displacement; the character's before/after psychological state is not palpable.",
    )
    dialogue_low_zh: str = Field(
        default="缺少有效对话支撑，人物之间的对抗还没有被真正演出来。",
    )
    dialogue_low_en: str = Field(
        default="Dialogue is insufficient to carry the confrontation; character opposition has not been dramatized.",
    )
    hook_low_zh: str = Field(
        default="场景尾钩不够硬，读者很难被自然推向下一场或下一章。",
    )
    hook_low_en: str = Field(
        default="The scene's ending hook is not compelling enough; readers will not feel pulled into the next scene.",
    )
    payoff_low_zh: str = Field(
        default="当前场景的信息释放和短回报偏弱，还没有形成足够明确的阅读收益。",
    )
    payoff_low_en: str = Field(
        default="Information release and short-term payoff are weak; the scene does not deliver clear reading reward.",
    )
    voice_low_zh: str = Field(
        default="文本语气和成品网文叙述感不够稳定，仍有策划说明腔或语感漂移。",
    )
    voice_low_en: str = Field(
        default="Narrative voice is unstable; traces of planning-speak or tonal drift remain.",
    )
    contract_low_zh: str = Field(
        default="当前场景没有充分兑现 scene contract。",
    )
    contract_low_en: str = Field(
        default="The scene has not adequately delivered on the scene contract.",
    )


class GenrePlanRubric(BaseModel):
    """Structural requirements enforced during plan validation."""

    required_checks: list[str] = Field(default_factory=list)
    min_antagonist_forces: int = Field(default=1, ge=0)
    require_power_system_tiers: bool = Field(default=False)
    require_relationship_milestones: bool = Field(default=False)
    require_clue_chain: bool = Field(default=False)
    require_theme_per_volume: bool = Field(default=True)
    min_key_reveals_per_volume: int = Field(default=1, ge=0)
    require_foreshadowing: bool = Field(default=True)
    llm_evaluation_prompt_zh: str | None = Field(default=None)
    llm_evaluation_prompt_en: str | None = Field(default=None)


class GenrePlannerPrompts(BaseModel):
    """LLM system / instruction prompts injected into each planning stage."""

    book_spec_system_zh: str = Field(default="")
    book_spec_system_en: str = Field(default="")
    book_spec_instruction_zh: str = Field(default="")
    book_spec_instruction_en: str = Field(default="")
    world_spec_system_zh: str = Field(default="")
    world_spec_system_en: str = Field(default="")
    world_spec_instruction_zh: str = Field(default="")
    world_spec_instruction_en: str = Field(default="")
    cast_spec_system_zh: str = Field(default="")
    cast_spec_system_en: str = Field(default="")
    cast_spec_instruction_zh: str = Field(default="")
    cast_spec_instruction_en: str = Field(default="")
    volume_plan_system_zh: str = Field(default="")
    volume_plan_system_en: str = Field(default="")
    volume_plan_instruction_zh: str = Field(default="")
    volume_plan_instruction_en: str = Field(default="")
    outline_system_zh: str = Field(default="")
    outline_system_en: str = Field(default="")
    outline_instruction_zh: str = Field(default="")
    outline_instruction_en: str = Field(default="")


class GenreJudgePrompts(BaseModel):
    """LLM system / instruction prompts injected into scene and chapter reviews."""

    scene_review_system_zh: str = Field(default="")
    scene_review_system_en: str = Field(default="")
    scene_review_instruction_zh: str = Field(default="")
    scene_review_instruction_en: str = Field(default="")
    chapter_review_system_zh: str = Field(default="")
    chapter_review_system_en: str = Field(default="")
    chapter_review_instruction_zh: str = Field(default="")
    chapter_review_instruction_en: str = Field(default="")
    scene_rewrite_system_zh: str = Field(default="")
    scene_rewrite_system_en: str = Field(default="")
    scene_rewrite_instruction_zh: str = Field(default="")
    scene_rewrite_instruction_en: str = Field(default="")
    chapter_rewrite_system_zh: str = Field(default="")
    chapter_rewrite_system_en: str = Field(default="")
    chapter_rewrite_instruction_zh: str = Field(default="")
    chapter_rewrite_instruction_en: str = Field(default="")


class GenreReviewProfile(BaseModel):
    """Complete genre-specific configuration bundle."""

    category_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1)
    scene_weights: GenreReviewWeights = Field(default_factory=GenreReviewWeights)
    chapter_weights: GenreChapterReviewWeights = Field(
        default_factory=GenreChapterReviewWeights,
    )
    scene_threshold_override: float | None = Field(default=None, ge=0.0, le=1.0)
    chapter_threshold_override: float | None = Field(default=None, ge=0.0, le=1.0)
    signal_keywords: GenreSignalKeywords = Field(default_factory=GenreSignalKeywords)
    finding_messages: GenreFindingMessages = Field(
        default_factory=GenreFindingMessages,
    )
    plan_rubric: GenrePlanRubric = Field(default_factory=GenrePlanRubric)
    planner_prompts: GenrePlannerPrompts = Field(
        default_factory=GenrePlannerPrompts,
    )
    judge_prompts: GenreJudgePrompts = Field(default_factory=GenreJudgePrompts)


# ---------------------------------------------------------------------------
# Genre preset key -> category mapping
# ---------------------------------------------------------------------------

_GENRE_TO_CATEGORY_MAP: dict[str, str] = {
    # action-progression
    "apocalypse-supply": "action-progression",
    "apocalypse-rule": "action-progression",
    "xianxia-upgrade": "action-progression",
    "urban-power-reversal": "action-progression",
    "beast-taming-upgrade": "action-progression",
    "urban-xiuxian-2-0": "action-progression",
    "litrpg-progression": "action-progression",
    "urban-fantasy": "action-progression",
    "gamelit-isekai": "action-progression",
    "apocalypse-survival": "action-progression",
    "ya-fantasy": "action-progression",
    "portal-fantasy": "action-progression",
    "cultivation-western": "action-progression",
    "monster-evolution": "action-progression",
    "superhero-fiction": "action-progression",
    # relationship-driven
    "female-growth-romance": "relationship-driven",
    "palace-revenge": "relationship-driven",
    "dark-romance": "relationship-driven",
    "romantasy": "relationship-driven",
    "enemies-to-lovers": "relationship-driven",
    "reverse-harem": "relationship-driven",
    "mafia-romance": "relationship-driven",
    "paranormal-romance": "relationship-driven",
    "cozy-fantasy": "relationship-driven",
    "slow-burn-romance": "relationship-driven",
    # suspense-mystery
    "suspense-detective": "suspense-mystery",
    "rule-horror": "suspense-mystery",
    "folk-mystery": "suspense-mystery",
    "human-nature-game": "suspense-mystery",
    "infinite-flow": "suspense-mystery",
    "psychological-thriller": "suspense-mystery",
    "cozy-mystery": "suspense-mystery",
    "detective-procedural": "suspense-mystery",
    "thriller-conspiracy": "suspense-mystery",
    # strategy-worldbuilding
    "history-hegemony": "strategy-worldbuilding",
    "starsea-war": "strategy-worldbuilding",
    "urban-blacktech": "strategy-worldbuilding",
    "historical-research-travel": "strategy-worldbuilding",
    "epic-fantasy": "strategy-worldbuilding",
    "space-opera": "strategy-worldbuilding",
    "military-scifi": "strategy-worldbuilding",
    # esports-competition
    "game-esports": "esports-competition",
    # female-growth-ncp
    "female-no-cp": "female-growth-ncp",
    # base-building
    "apocalypse-basebuilding": "base-building",
    "rebirth-business": "base-building",
    # eastern-aesthetic
    "eastern-aesthetic-fantasy": "eastern-aesthetic",
}


# ---------------------------------------------------------------------------
# Raw profile data for all 9 categories (including "default")
# ---------------------------------------------------------------------------

_GENRE_REVIEW_PROFILES: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------
    # DEFAULT (fallback for unknown genres)
    # ------------------------------------------------------------------
    "default": {
        "name": "通用 / Default",
        "description": "通用审稿配置，所有维度权重均衡，适用于未匹配到专门类型的作品。",
        "scene_weights": {},
        "chapter_weights": {},
        "scene_threshold_override": None,
        "chapter_threshold_override": None,
        "signal_keywords": {},
        "finding_messages": {},
        "plan_rubric": {
            "required_checks": [],
            "min_antagonist_forces": 1,
            "require_power_system_tiers": False,
            "require_relationship_milestones": False,
            "require_clue_chain": False,
            "require_theme_per_volume": True,
            "min_key_reveals_per_volume": 1,
            "require_foreshadowing": True,
            "llm_evaluation_prompt_zh": None,
            "llm_evaluation_prompt_en": None,
        },
        "planner_prompts": {},
        "judge_prompts": {},
    },
    # ------------------------------------------------------------------
    # ACTION-PROGRESSION
    # ------------------------------------------------------------------
    "action-progression": {
        "name": "升级流 / Action-Progression",
        "description": (
            "以主角实力成长和战斗升级为核心驱动力的类型，"
            "包括仙侠、末日、都市异能、LitRPG 等强调打怪升级和境界突破的子类型。"
        ),
        "scene_weights": {
            "conflict": 1.4,
            "conflict_clarity": 1.3,
            "hook_strength": 1.3,
            "payoff_density": 1.2,
            "emotion": 0.8,
            "emotional_movement": 0.85,
        },
        "chapter_weights": {
            "main_plot_progression": 1.3,
            "ending_hook_effectiveness": 1.3,
            "pacing_rhythm": 1.2,
            "continuity": 1.1,
        },
        "scene_threshold_override": None,
        "chapter_threshold_override": None,
        "signal_keywords": {
            "conflict_terms_zh": [
                "突破", "对轰", "碾压", "越阶", "反杀",
                "围剿", "血拼", "逼退", "破防", "断后",
            ],
            "conflict_terms_en": [
                "breakthrough", "clash", "overpower", "rank-skip", "counter-kill",
                "siege", "bloodbath", "force-back", "break-through", "last-stand",
            ],
            "emotion_terms_zh": [
                "杀意", "怒吼", "嗜血", "狠厉", "不甘",
                "豪迈", "悲壮", "热血", "震慑", "压迫",
            ],
            "emotion_terms_en": [
                "killing intent", "roar", "bloodlust", "ruthless", "defiance",
                "heroic", "tragic valor", "burning blood", "intimidation", "oppression",
            ],
            "hook_terms_zh": [
                "新境界", "异变", "宝物", "秘境开启",
                "围堵升级", "更强的对手", "禁地",
            ],
            "hook_terms_en": [
                "new realm", "mutation", "treasure", "secret realm opens",
                "siege escalation", "stronger foe", "forbidden zone",
            ],
            "info_terms_zh": [
                "升级", "突破", "机缘", "传承", "功法", "秘密",
            ],
            "info_terms_en": [
                "level-up", "breakthrough", "fortuitous encounter", "legacy",
                "cultivation technique", "secret",
            ],
        },
        "finding_messages": {
            "conflict_low_zh": (
                "战斗冲突缺少阶段感和压力递进，对抗动作过于笼统。"
                "升级流的场景需要让读者清晰感受到每一轮对碰的力量差距在变化。"
            ),
            "conflict_low_en": (
                "Combat conflict lacks phased escalation and pressure build-up. "
                "Action-progression scenes must make the reader feel the shifting power gap with each exchange."
            ),
            "conflict_clarity_low_zh": (
                "冲突的胜负条件和双方底牌不够明确，读者无法判断当前局面谁占上风、代价是什么。"
            ),
            "conflict_clarity_low_en": (
                "Win conditions and hidden cards are unclear; "
                "readers cannot judge who has the upper hand or what the cost is."
            ),
            "emotion_low_zh": (
                "战斗场景的情绪强度不足，缺少杀意、压迫感或背水一战的紧张体感。"
            ),
            "emotion_low_en": (
                "The combat scene lacks emotional intensity; "
                "killing intent, oppression, or backs-against-the-wall tension is missing."
            ),
            "emotional_movement_low_zh": (
                "角色在战斗前后的心态没有形成清晰位移，"
                "缺少从犹豫到决绝、从弱势到反超的内心变化。"
            ),
            "emotional_movement_low_en": (
                "The character's mentality does not shift clearly before and after the fight; "
                "the internal arc from hesitation to resolve, or from disadvantage to turnabout, is absent."
            ),
            "dialogue_low_zh": (
                "对话没有体现出对抗的张力，缺少挑衅、威胁或关键信息释放。"
            ),
            "dialogue_low_en": (
                "Dialogue does not convey confrontational tension; "
                "provocation, threats, or key information reveals are missing."
            ),
            "hook_low_zh": (
                "场景尾部没有抛出足够硬的下一级威胁或新的升级契机，"
                "读者缺乏继续追更的牵引力。"
            ),
            "hook_low_en": (
                "The scene ending does not present a hard enough next-level threat or upgrade opportunity; "
                "readers lack a reason to continue."
            ),
            "payoff_low_zh": (
                "升级成果或战利品释放不够明确，场景结束后读者没有获得可感知的阅读回报。"
            ),
            "payoff_low_en": (
                "Upgrade rewards or loot drops are not explicit enough; "
                "the reader does not perceive a tangible payoff at scene's end."
            ),
            "voice_low_zh": (
                "战斗叙事腔调不稳定，间歇出现说明文语感，破坏了紧凑的战斗节奏。"
            ),
            "voice_low_en": (
                "Combat narrative voice is unstable; explanatory tone intermittently breaks the tight battle rhythm."
            ),
            "contract_low_zh": (
                "当前场景没有兑现 scene contract 中承诺的战斗升级节点或关键对碰。"
            ),
            "contract_low_en": (
                "The scene has not delivered the combat escalation or key clash promised by the scene contract."
            ),
        },
        "plan_rubric": {
            "required_checks": [
                "power_tier_escalation",
                "antagonist_evolution",
                "conflict_phase_variety",
            ],
            "min_antagonist_forces": 2,
            "require_power_system_tiers": True,
            "require_relationship_milestones": False,
            "require_clue_chain": False,
            "require_theme_per_volume": True,
            "min_key_reveals_per_volume": 1,
            "require_foreshadowing": True,
            "llm_evaluation_prompt_zh": (
                "请检查规划中是否包含清晰的力量体系分层（境界/等级/装备层）、"
                "主角升级引擎（每卷的核心突破路径），以及至少两组持续升级的对手势力。"
                "如果缺少上述任何一项，给出具体修改建议。"
            ),
            "llm_evaluation_prompt_en": (
                "Verify that the plan includes a clear power-tier system (realms/levels/gear tiers), "
                "a protagonist upgrade engine (core breakthrough path per volume), "
                "and at least two continuously escalating antagonist forces. "
                "If any are missing, provide concrete revision suggestions."
            ),
        },
        "planner_prompts": {
            "book_spec_system_zh": (
                "你是一位专精升级流网文的策划总监。你熟悉仙侠、末日求生、都市异能和 LitRPG 等类型的读者预期。"
                "你的核心职责是确保书籍规格中包含完整的力量体系分层、清晰的升级引擎和阶梯式的对手设计。"
                "所有规划决策必须服务于读者的爽点节奏和追更动力。"
            ),
            "book_spec_system_en": (
                "You are a senior editor specializing in action-progression web fiction. "
                "You understand reader expectations for xianxia, apocalypse survival, urban power fantasy, and LitRPG. "
                "Your core duty is to ensure the book spec contains a complete power-tier system, "
                "a clear upgrade engine, and tiered antagonist design. "
                "All planning decisions must serve reader satisfaction rhythm and binge-reading momentum."
            ),
            "book_spec_instruction_zh": (
                "在书籍规格中必须明确以下要素：\n"
                "1. 力量体系的分层（从低到高的完整境界/等级列表，每层的代表能力和天花板）\n"
                "2. 主角的升级引擎（靠什么核心资源/功法/系统实现突破，节奏如何）\n"
                "3. 对手梯队设计（至少两组势力，从小Boss到终极Boss的完整梯队）\n"
                "4. 每个等级突破对应的爽点回报（实力展示、碾压快感、战利品收获）\n"
                "5. 卖点前置策略——在前三章内必须让读者看到主角与众不同的升级优势。"
            ),
            "book_spec_instruction_en": (
                "The book spec must define:\n"
                "1. Power tier hierarchy (full list of realms/levels from low to high, representative abilities and ceiling for each)\n"
                "2. Protagonist upgrade engine (core resources/techniques/systems for breakthroughs, pacing)\n"
                "3. Antagonist tiers (at least two factions, full ladder from minor bosses to ultimate antagonist)\n"
                "4. Payoff rewards per breakthrough (power display, domination, loot)\n"
                "5. Early hook strategy — the protagonist's unique advantage must be visible within the first three chapters."
            ),
            "world_spec_system_zh": (
                "你是负责升级流网文世界观设计的策划专家。"
                "你需要确保世界设定能自然支撑力量体系的分层、资源的稀缺性和区域难度的递进。"
                "世界观不是装饰，而是升级节奏和冲突来源的底层架构。"
            ),
            "world_spec_system_en": (
                "You are a worldbuilding specialist for action-progression fiction. "
                "Ensure the world naturally supports power-tier layering, resource scarcity, and zone-difficulty progression. "
                "Worldbuilding is not decoration — it is the architecture driving upgrade pacing and conflict sources."
            ),
            "world_spec_instruction_zh": (
                "世界设定必须包含：\n"
                "1. 地图/区域的难度分层（安全区→危险区→禁地），与力量体系挂钩\n"
                "2. 核心资源分布（升级材料、功法传承、稀有机缘的分布逻辑）\n"
                "3. 势力版图（各大势力的控制范围、等级上限和争夺焦点）\n"
                "4. 规则体系（修炼突破的硬性条件和限制，避免无限通胀）"
            ),
            "world_spec_instruction_en": (
                "The world spec must include:\n"
                "1. Zone difficulty tiers (safe → dangerous → forbidden) linked to the power system\n"
                "2. Core resource distribution (upgrade materials, technique legacies, rare opportunities)\n"
                "3. Faction territory map (control ranges, tier caps, contest points)\n"
                "4. Rule constraints (hard conditions and limits for breakthroughs, preventing infinite inflation)"
            ),
            "cast_spec_system_zh": (
                "你是升级流网文的角色设计专家。"
                "角色设计的核心目标是为每一阶段的升级冲突提供足够匹配的对手和盟友。"
                "主角必须有清晰的实力成长弧和人格成长弧，配角必须有对主角形成压力或提供助力的功能定位。"
            ),
            "cast_spec_system_en": (
                "You are a character designer for action-progression fiction. "
                "The core goal is to provide sufficiently matched opponents and allies for each upgrade phase. "
                "The protagonist needs clear power and personality growth arcs; "
                "supporting characters must have functional roles applying pressure or providing aid."
            ),
            "cast_spec_instruction_zh": (
                "角色列表必须包含：\n"
                "1. 主角的初始实力位置和最终目标（起点低、上限高、差异化优势明确）\n"
                "2. 至少两组递进对手（每组有 2-3 个阶段代表，从该阶段的精英到Boss）\n"
                "3. 关键盟友/师傅/对手转化角色（提供升级关键节点的推力或阻力）\n"
                "4. 每个重要角色的实力定位（以力量体系中的境界/等级标注）"
            ),
            "cast_spec_instruction_en": (
                "The cast list must include:\n"
                "1. Protagonist's starting power level and ultimate goal (low start, high ceiling, clear unique advantage)\n"
                "2. At least two progressive antagonist groups (2-3 phase representatives each, from tier elites to bosses)\n"
                "3. Key allies/mentors/rival-turnover characters (providing thrust or resistance at upgrade nodes)\n"
                "4. Power-level annotation for every major character (using the tier system)"
            ),
            "volume_plan_system_zh": (
                "你是升级流网文的卷级规划师。每一卷必须围绕一个核心升级阶段设计，"
                "包含该阶段的目标境界、核心对手、关键战斗和升级成果。"
                "卷与卷之间的难度和规模必须有清晰的阶梯递进感。"
            ),
            "volume_plan_system_en": (
                "You are a volume-level planner for action-progression fiction. "
                "Each volume must be designed around a core upgrade phase, "
                "including the target tier, core antagonist, key battles, and upgrade payoff. "
                "Difficulty and scale must escalate clearly from volume to volume."
            ),
            "volume_plan_instruction_zh": (
                "每卷规划必须明确：\n"
                "1. 本卷的升级目标（主角要突破到什么境界/等级）\n"
                "2. 本卷核心对手和势力冲突（是谁在阻挡主角升级？压力来源是什么？）\n"
                "3. 关键战斗节点（至少 2-3 场有阶段意义的战斗/对碰）\n"
                "4. 升级回报（突破后获得什么新能力、新地位、新资源）\n"
                "5. 本卷末尾的钩子（指向下一卷更大的威胁或更高的升级诱惑）"
            ),
            "volume_plan_instruction_en": (
                "Each volume plan must define:\n"
                "1. Upgrade target (what tier/level the protagonist aims to reach)\n"
                "2. Core antagonist and faction conflict (who blocks the upgrade? what is the pressure source?)\n"
                "3. Key battle nodes (at least 2-3 phase-significant fights)\n"
                "4. Upgrade payoff (new abilities, status, resources after breakthrough)\n"
                "5. Volume-end hook (pointing to a larger threat or higher upgrade lure in the next volume)"
            ),
            "outline_system_zh": (
                "你是升级流网文的章节大纲师。你的工作是把卷级规划拆解成具体的章节序列，"
                "确保每一章都有明确的战斗推进或升级进展，避免出现连续两章以上的纯铺垫或信息交代。"
            ),
            "outline_system_en": (
                "You are a chapter outliner for action-progression fiction. "
                "Your job is to break volume plans into a concrete chapter sequence, "
                "ensuring every chapter has clear combat advancement or upgrade progress. "
                "Avoid two or more consecutive chapters of pure setup or exposition."
            ),
            "outline_instruction_zh": (
                "章节大纲必须遵守：\n"
                "1. 每章至少包含一个冲突升级节点或升级进展事件\n"
                "2. 战斗章节和准备/恢复章节交替出现，节奏不能连续平缓\n"
                "3. 每章结尾必须有硬钩子（新威胁、异变、升级契机）\n"
                "4. 每 3-5 章必须出现一个可感知的升级回报或阶段胜利\n"
                "5. Boss 战必须有铺垫-试探-正面交锋-逆转/突破的完整结构"
            ),
            "outline_instruction_en": (
                "The chapter outline must follow:\n"
                "1. Every chapter has at least one conflict escalation node or upgrade progress event\n"
                "2. Battle and preparation/recovery chapters alternate; pacing cannot flatten for consecutive chapters\n"
                "3. Every chapter ending must have a hard hook (new threat, mutation, upgrade opportunity)\n"
                "4. Every 3-5 chapters must deliver a perceivable upgrade payoff or phase victory\n"
                "5. Boss fights must have a complete structure: buildup - probing - direct clash - reversal/breakthrough"
            ),
        },
        "judge_prompts": {
            "scene_review_system_zh": (
                "你是升级流网文的场景审稿专家。你的评估标准以战斗节奏、力量碰撞的紧凑性和升级回报的可感知度为核心。"
                "当场景是战斗场景时，重点评估对碰阶段感、压力递进和反转时机。"
                "当场景是准备/升级场景时，重点评估信息释放密度和对下一场战斗的悬念铺设。"
            ),
            "scene_review_system_en": (
                "You are a scene review specialist for action-progression fiction. "
                "Your evaluation criteria center on combat rhythm, power-collision intensity, and perceivable upgrade payoff. "
                "For combat scenes, focus on clash phasing, pressure escalation, and reversal timing. "
                "For preparation/upgrade scenes, focus on information release density and suspense building for the next battle."
            ),
            "scene_review_instruction_zh": (
                "评估当前场景时请关注：\n"
                "1. 如果是战斗场景：对碰是否有阶段感？双方底牌和力量差距是否清晰？是否有至少一个反转或压力峰值？\n"
                "2. 如果是升级场景：升级过程是否有阻碍和突破的戏剧性？获得的能力是否被具象展示？\n"
                "3. 尾钩是否指向更大的威胁或更高的升级诱惑？\n"
                "4. 整体节奏是否避免了冗长的内心独白或说明文式的力量解说？"
            ),
            "scene_review_instruction_en": (
                "When evaluating this scene, focus on:\n"
                "1. Combat scenes: Do clashes have phased structure? Are both sides' cards and power gap clear? Is there at least one reversal or pressure peak?\n"
                "2. Upgrade scenes: Does the process have dramatic obstacles and breakthroughs? Are acquired abilities concretely demonstrated?\n"
                "3. Does the ending hook point to a greater threat or higher upgrade lure?\n"
                "4. Does the overall rhythm avoid lengthy inner monologues or expository power explanations?"
            ),
            "chapter_review_system_zh": (
                "你是升级流网文的章节审稿专家。章节级评估关注的是一章之内战斗节奏的完整性、"
                "场景之间的推进逻辑，以及章末钩子对下一章追读欲望的牵引力。"
            ),
            "chapter_review_system_en": (
                "You are a chapter review specialist for action-progression fiction. "
                "Chapter-level evaluation focuses on combat-rhythm completeness within a chapter, "
                "scene-to-scene progression logic, and chapter-ending hook pull toward the next chapter."
            ),
            "chapter_review_instruction_zh": (
                "评估当前章节时请关注：\n"
                "1. 本章是否围绕一个清晰的升级/战斗阶段展开？\n"
                "2. 场景之间的衔接是否有逻辑推力（因果、压力升级、新信息触发）？\n"
                "3. 章节末尾是否留下了让读者无法停下来的悬念或升级期待？\n"
                "4. 如果本章包含战斗，战斗的阶段完整性如何（铺垫-交锋-结果-后果）？"
            ),
            "chapter_review_instruction_en": (
                "When evaluating this chapter, focus on:\n"
                "1. Does the chapter revolve around a clear upgrade/combat phase?\n"
                "2. Are scene transitions driven by causal logic (cause-effect, pressure escalation, new info triggers)?\n"
                "3. Does the chapter ending leave a cliffhanger or upgrade anticipation that compels continued reading?\n"
                "4. If the chapter contains combat, how complete is the battle arc (setup - clash - outcome - aftermath)?"
            ),
            "scene_rewrite_system_zh": (
                "你是升级流网文的场景重写专家。重写时必须强化战斗碰撞的力量感、"
                "升级节奏的紧凑性和尾钩的硬度。不要削弱冲突力度去追求文学性。"
            ),
            "scene_rewrite_system_en": (
                "You are a scene rewrite specialist for action-progression fiction. "
                "Rewrites must strengthen power-collision impact, upgrade-pacing tightness, and hook hardness. "
                "Do not sacrifice conflict intensity for literary refinement."
            ),
            "scene_rewrite_instruction_zh": (
                "重写时优先补强：\n"
                "1. 战斗的力量碰撞感（动作具象化、招式有反馈、伤害可感知）\n"
                "2. 升级的兑现感（突破后的新能力立刻在场景内被演示或对比）\n"
                "3. 尾钩的硬度（用更大的威胁、更高的悬念或更诱人的升级线索收尾）\n"
                "4. 删除冗余的力量体系解说，改为通过对战动作间接展示。"
            ),
            "scene_rewrite_instruction_en": (
                "When rewriting, prioritize:\n"
                "1. Power-collision impact (concrete combat actions, move feedback, perceivable damage)\n"
                "2. Upgrade payoff (new abilities demonstrated or contrasted immediately in-scene)\n"
                "3. Hook hardness (close with a larger threat, higher suspense, or more enticing upgrade clue)\n"
                "4. Remove redundant power-system exposition; show through combat actions instead."
            ),
            "chapter_rewrite_system_zh": (
                "你是升级流网文的章节重写专家。重写时确保章节的升级阶段完整、"
                "场景衔接紧凑、结尾钩子足够硬。保持战斗场景的密度和爽感。"
            ),
            "chapter_rewrite_system_en": (
                "You are a chapter rewrite specialist for action-progression fiction. "
                "Ensure the chapter's upgrade phase is complete, scene transitions are tight, "
                "and the ending hook is hard enough. Maintain combat scene density and satisfaction."
            ),
            "chapter_rewrite_instruction_zh": (
                "重写时优先补强：\n"
                "1. 确保本章的核心战斗/升级事件完整落地（有铺垫、有高潮、有结果）\n"
                "2. 修复场景之间的断裂感（补充因果衔接或压力传递）\n"
                "3. 强化章末钩子（必须让读者对下一章的威胁或升级充满期待）\n"
                "4. 如果战斗密度不足，考虑在准备场景中加入小规模冲突或试探战。"
            ),
            "chapter_rewrite_instruction_en": (
                "When rewriting, prioritize:\n"
                "1. Ensure the chapter's core battle/upgrade event lands completely (setup, climax, outcome)\n"
                "2. Fix scene-transition gaps (add causal links or pressure transfer)\n"
                "3. Strengthen the chapter-end hook (readers must anticipate the next chapter's threat or upgrade)\n"
                "4. If combat density is low, add small-scale skirmishes or probing fights in preparation scenes."
            ),
        },
    },
    # ------------------------------------------------------------------
    # RELATIONSHIP-DRIVEN
    # ------------------------------------------------------------------
    "relationship-driven": {
        "name": "情感驱动 / Relationship-Driven",
        "description": (
            "以人物关系推进和情感变化为核心的类型，"
            "包括言情、宫斗、暗黑浪漫、Romantasy 等重视情感线和人物内心世界的子类型。"
        ),
        "scene_weights": {
            "emotion": 1.5,
            "emotional_movement": 1.4,
            "dialogue": 1.3,
            "conflict": 0.7,
            "conflict_clarity": 0.8,
            "hook": 0.9,
        },
        "chapter_weights": {
            "character_voice_distinction": 1.3,
            "thematic_resonance": 1.2,
            "pacing_rhythm": 1.1,
            "ending_hook_effectiveness": 1.1,
        },
        "scene_threshold_override": None,
        "chapter_threshold_override": None,
        "signal_keywords": {
            "conflict_terms_zh": [
                "冷战", "质问", "误解", "拉扯", "选择",
                "放手", "心墙", "试探", "靠近", "心结",
            ],
            "conflict_terms_en": [
                "cold war", "confrontation", "misunderstanding", "push-pull", "choice",
                "letting go", "emotional wall", "testing", "drawing closer", "grudge",
            ],
            "emotion_terms_zh": [
                "心跳", "回避", "窒息", "温热", "眼眶",
                "颤抖", "失落", "心酸", "心软", "微烫",
            ],
            "emotion_terms_en": [
                "heartbeat", "avoidance", "suffocation", "warmth", "tear-brimmed",
                "trembling", "loss", "heartache", "softening", "searing warmth",
            ],
            "hook_terms_zh": [
                "误会加深", "靠近一步", "选择", "秘密曝光",
                "第三个人", "不告而别",
            ],
            "hook_terms_en": [
                "deepening misunderstanding", "one step closer", "choice",
                "secret exposed", "third person", "vanishing without goodbye",
            ],
            "info_terms_zh": [
                "过去", "身世", "秘密", "真相", "承诺", "背叛",
            ],
            "info_terms_en": [
                "past", "origin", "secret", "truth", "promise", "betrayal",
            ],
        },
        "finding_messages": {
            "conflict_low_zh": (
                "关系冲突过于表面化，缺少真正触动人物核心诉求和内心防线的对抗。"
                "言情类冲突的本质是价值观和情感需求的碰撞，不是吵架。"
            ),
            "conflict_low_en": (
                "Relationship conflict is too superficial; it fails to touch characters' core needs and emotional defenses. "
                "In romance, conflict is about clashing values and emotional needs, not arguments."
            ),
            "conflict_clarity_low_zh": (
                "人物之间的矛盾根源不够清楚，读者看不出是什么让他们无法在一起或难以和解。"
            ),
            "conflict_clarity_low_en": (
                "The root of the interpersonal conflict is unclear; "
                "readers cannot see what keeps them apart or prevents reconciliation."
            ),
            "emotion_low_zh": (
                "情感表达过于直白，缺少通过身体反应、微表情、环境对照等手法来传递内心波澜。"
                "好的情感戏应该让读者自己感受到心动或心痛，而不是被告知。"
            ),
            "emotion_low_en": (
                "Emotional expression is too blunt; use body reactions, micro-expressions, and environmental contrast "
                "to convey inner turmoil. Good romance lets readers feel the flutter or ache, not be told about it."
            ),
            "emotional_movement_low_zh": (
                "人物的情感状态在场景前后没有发生可感知的变化。"
                "每个关键场景都应该让关系距离有一个明确的推近或拉远。"
            ),
            "emotional_movement_low_en": (
                "The character's emotional state does not perceptibly shift across the scene. "
                "Every key scene should push the relationship perceptibly closer or further apart."
            ),
            "dialogue_low_zh": (
                "对话缺少潜台词层次，人物直接说出自己的感受而不是通过言语背后的含义让读者自己体会。"
            ),
            "dialogue_low_en": (
                "Dialogue lacks subtext; characters state feelings directly instead of conveying them through what is left unsaid."
            ),
            "hook_low_zh": (
                "场景尾部没有制造出足够的情感悬念，缺少让读者牵挂人物关系走向的牵引力。"
            ),
            "hook_low_en": (
                "The scene ending does not create enough emotional suspense; "
                "it lacks pull that makes readers worry about where the relationship is heading."
            ),
            "payoff_low_zh": (
                "情感互动的阶段性回报不足，读者没有在这个场景中感受到关系的真正推进或新的情感冲击。"
            ),
            "payoff_low_en": (
                "Emotional interaction payoff is insufficient; "
                "readers do not feel a real relationship advance or new emotional impact in this scene."
            ),
            "voice_low_zh": (
                "叙述声音缺乏亲密感和辨识度，角色的内心独白和叙述腔调没有体现出独特的情感性格。"
            ),
            "voice_low_en": (
                "The narrative voice lacks intimacy and distinctiveness; "
                "character interiority and tonal personality are not coming through."
            ),
            "contract_low_zh": (
                "当前场景没有兑现 scene contract 中承诺的关系推进节点或情感冲击。"
            ),
            "contract_low_en": (
                "The scene has not delivered the relationship milestone or emotional impact promised by the scene contract."
            ),
        },
        "plan_rubric": {
            "required_checks": [
                "relationship_milestone_progression",
                "emotional_arc_explicit",
            ],
            "min_antagonist_forces": 1,
            "require_power_system_tiers": False,
            "require_relationship_milestones": True,
            "require_clue_chain": False,
            "require_theme_per_volume": True,
            "min_key_reveals_per_volume": 1,
            "require_foreshadowing": True,
            "llm_evaluation_prompt_zh": (
                "请检查规划中是否包含清晰的关系里程碑序列（从初遇到心动到考验到确认关系），"
                "每个里程碑是否有对应的情感引擎（推动关系变化的核心事件或矛盾），"
                "以及是否存在至少一条持续施加压力的外部或内部阻力线。"
            ),
            "llm_evaluation_prompt_en": (
                "Verify that the plan includes a clear relationship-milestone sequence "
                "(from first meeting to attraction to trial to commitment), "
                "each milestone has a corresponding emotional engine (core event or conflict driving the change), "
                "and there is at least one continuous external or internal resistance line."
            ),
        },
        "planner_prompts": {
            "book_spec_system_zh": (
                "你是一位专精情感驱动型小说的策划总监。你深谙言情、宫斗、暗黑浪漫和 Romantasy 的读者心理。"
                "你的核心职责是确保书籍规格中包含完整的关系里程碑路线图、情感引擎设计和人物内心成长弧线。"
                "所有规划必须服务于读者的情感代入和关系追踪体验。"
            ),
            "book_spec_system_en": (
                "You are a senior editor specializing in relationship-driven fiction. "
                "You understand reader psychology for romance, palace intrigue, dark romance, and romantasy. "
                "Your core duty is to ensure the book spec contains a complete relationship-milestone roadmap, "
                "emotional engine design, and character inner growth arcs. "
                "All planning must serve reader emotional immersion and relationship-tracking experience."
            ),
            "book_spec_instruction_zh": (
                "在书籍规格中必须明确以下要素：\n"
                "1. 关系里程碑路线图（初遇→心动→试探→靠近→阻碍→考验→确认→危机→重聚等关键节点）\n"
                "2. 情感引擎（是什么核心矛盾在推动关系变化？内心创伤？身份冲突？外部阻力？）\n"
                "3. CP 的化学反应设计（两人的性格差异如何制造张力和互补？互动中的核心甜点/虐点是什么？）\n"
                "4. 内心成长弧（主角从什么情感状态出发，最终要抵达什么状态？）\n"
                "5. 外部阻力线（至少一条持续对关系施压的外部势力或社会规则）"
            ),
            "book_spec_instruction_en": (
                "The book spec must define:\n"
                "1. Relationship milestone roadmap (meeting → attraction → testing → closeness → obstacle → trial → commitment → crisis → reunion)\n"
                "2. Emotional engine (what core conflict drives relationship change? Inner wounds? Identity clash? External pressure?)\n"
                "3. Chemistry design (how personality differences create tension and complementarity; core sweet/angsty beats)\n"
                "4. Inner growth arc (protagonist's emotional starting point and destination)\n"
                "5. External resistance line (at least one continuous external force or social rule pressuring the relationship)"
            ),
            "world_spec_system_zh": (
                "你是情感驱动型小说的世界观设计师。世界设定的核心功能是为关系制造压力和机会——"
                "社会规则如何限制感情？权力结构如何制造身份差距？环境如何成为情感的隐喻或催化剂？"
            ),
            "world_spec_system_en": (
                "You are a worldbuilding specialist for relationship-driven fiction. "
                "The world's core function is to create pressure and opportunity for relationships — "
                "how do social rules restrict feelings? How does power structure create identity gaps? "
                "How does the environment serve as emotional metaphor or catalyst?"
            ),
            "world_spec_instruction_zh": (
                "世界设定必须包含：\n"
                "1. 社会规则/阶层结构（如何制造身份差距和关系阻碍）\n"
                "2. 情感表达的文化约束（什么是允许的？什么是禁忌的？）\n"
                "3. 关键场景空间（适合制造亲密互动或冲突对峙的核心场所）\n"
                "4. 权力关系图谱（谁能影响主角的选择？谁是潜在的阻碍者或助力者？）"
            ),
            "world_spec_instruction_en": (
                "The world spec must include:\n"
                "1. Social rules / class structure (creating identity gaps and relationship obstacles)\n"
                "2. Cultural constraints on emotional expression (what is permitted? what is forbidden?)\n"
                "3. Key scene spaces (core locations for intimate interaction or confrontational standoffs)\n"
                "4. Power-relation map (who can influence the protagonist's choices? potential blockers or enablers?)"
            ),
            "cast_spec_system_zh": (
                "你是情感驱动型小说的角色设计专家。"
                "角色设计的核心是确保主要CP之间的化学反应有层次，配角在情感线上有明确的功能定位——"
                "催化剂、搅局者、镜像角色还是避风港。"
            ),
            "cast_spec_system_en": (
                "You are a character designer for relationship-driven fiction. "
                "The core goal is to ensure layered chemistry between the main couple, "
                "and that supporting characters have clear functional roles on the emotional line — "
                "catalyst, disruptor, mirror character, or safe harbor."
            ),
            "cast_spec_instruction_zh": (
                "角色列表必须包含：\n"
                "1. 主 CP 的性格-情感档案（核心诉求、内心创伤、防御机制、软肋）\n"
                "2. CP 互动的核心张力点（性格差异、身份冲突、价值观碰撞）\n"
                "3. 关键配角的情感功能定位（每个配角对主线关系的推动或阻碍作用）\n"
                "4. 情感对手/第三者的定位和退出时机设计"
            ),
            "cast_spec_instruction_en": (
                "The cast list must include:\n"
                "1. Main couple's personality-emotion profiles (core needs, inner wounds, defense mechanisms, soft spots)\n"
                "2. Core tension points in their interaction (personality clashes, identity conflicts, value collisions)\n"
                "3. Key supporting characters' emotional function (each character's role in advancing or blocking the main relationship)\n"
                "4. Emotional rival / third-party positioning and exit timing"
            ),
            "volume_plan_system_zh": (
                "你是情感驱动型小说的卷级规划师。每一卷围绕一个关系阶段展开，"
                "必须有明确的情感里程碑目标和推动关系变化的核心事件序列。"
            ),
            "volume_plan_system_en": (
                "You are a volume-level planner for relationship-driven fiction. "
                "Each volume revolves around a relationship phase, "
                "with a clear emotional milestone target and a core event sequence driving change."
            ),
            "volume_plan_instruction_zh": (
                "每卷规划必须明确：\n"
                "1. 本卷的关系里程碑目标（从什么关系距离推进到什么关系距离）\n"
                "2. 情感引擎事件（是什么事件推动了这次关系变化？）\n"
                "3. 核心甜点/虐点（本卷的标志性情感场景是什么？）\n"
                "4. 阻力升级（本卷新增了什么阻碍关系推进的因素？）\n"
                "5. 卷末情感悬念（读者在本卷结束时最担心什么或最期待什么？）"
            ),
            "volume_plan_instruction_en": (
                "Each volume plan must define:\n"
                "1. Relationship milestone target (from what distance to what distance)\n"
                "2. Emotional engine events (what events drive this relationship change?)\n"
                "3. Core sweet/angsty beats (what are the signature emotional scenes?)\n"
                "4. Resistance escalation (what new factors block relationship progress?)\n"
                "5. Volume-end emotional suspense (what is the reader most worried about or looking forward to?)"
            ),
            "outline_system_zh": (
                "你是情感驱动型小说的章节大纲师。确保每一章都在关系维度上有可感知的推进，"
                "避免连续多章关系距离完全没有变化。"
            ),
            "outline_system_en": (
                "You are a chapter outliner for relationship-driven fiction. "
                "Ensure every chapter makes perceivable progress on the relationship dimension. "
                "Avoid multiple consecutive chapters where relationship distance does not change at all."
            ),
            "outline_instruction_zh": (
                "章节大纲必须遵守：\n"
                "1. 每章至少有一个关系推进或情感波动事件\n"
                "2. 甜虐节奏交替，不能连续三章以上全甜或全虐\n"
                "3. 每章结尾必须在情感层面留下悬念（误会加深？靠近一步？秘密即将曝光？）\n"
                "4. 关键对话场景必须标注潜台词层次（表面在说什么，内心真实诉求是什么）\n"
                "5. 每一卷的情感高潮场景必须有充分的铺垫（至少 2-3 章的情绪蓄力）"
            ),
            "outline_instruction_en": (
                "The chapter outline must follow:\n"
                "1. Every chapter has at least one relationship advance or emotional fluctuation event\n"
                "2. Sweet and angsty beats alternate; no more than three consecutive chapters of all-sweet or all-angst\n"
                "3. Every chapter ending must leave emotional suspense (misunderstanding deepened? one step closer? secret about to be exposed?)\n"
                "4. Key dialogue scenes must note subtext layers (what is being said vs. the real inner need)\n"
                "5. Emotional climax scenes must have adequate buildup (at least 2-3 chapters of emotional charging)"
            ),
        },
        "judge_prompts": {
            "scene_review_system_zh": (
                "你是情感驱动型小说的场景审稿专家。评估的核心维度是情感位移、对话潜台词的层次感和关系距离的推进。"
                "好的情感场景不是角色直接说 '我喜欢你'，而是通过行为、沉默、回避和不经意的靠近让读者自己感受到心动。"
            ),
            "scene_review_system_en": (
                "You are a scene review specialist for relationship-driven fiction. "
                "Core dimensions are emotional displacement, dialogue subtext layering, and relationship-distance progression. "
                "A good emotional scene does not have characters say 'I like you' directly — "
                "it lets readers feel the pull through actions, silence, avoidance, and inadvertent closeness."
            ),
            "scene_review_instruction_zh": (
                "评估当前场景时请关注：\n"
                "1. 人物在场景前后的情感状态是否发生了可感知的变化？\n"
                "2. 对话是否有潜台词层次？是否通过言不由衷、试探、回避来传递真实情感？\n"
                "3. 身体语言和环境描写是否在配合情感氛围？\n"
                "4. 关系距离是否有明确的推近或拉远？读者能感受到吗？"
            ),
            "scene_review_instruction_en": (
                "When evaluating this scene, focus on:\n"
                "1. Does the character's emotional state change perceptibly across the scene?\n"
                "2. Does dialogue have subtext? Are real feelings conveyed through evasion, testing, or saying the opposite?\n"
                "3. Do body language and setting descriptions support the emotional atmosphere?\n"
                "4. Is there a clear push closer or pull further in relationship distance? Can readers feel it?"
            ),
            "chapter_review_system_zh": (
                "你是情感驱动型小说的章节审稿专家。章节级评估关注关系里程碑的推进是否可感知、"
                "情感节奏是否张弛有度、角色声音是否有辨识度。"
            ),
            "chapter_review_system_en": (
                "You are a chapter review specialist for relationship-driven fiction. "
                "Chapter-level evaluation focuses on perceivable relationship-milestone progress, "
                "emotional rhythm balance, and character voice distinctiveness."
            ),
            "chapter_review_instruction_zh": (
                "评估当前章节时请关注：\n"
                "1. 本章在关系维度上推进了多少？读者能明确感受到关系距离的变化吗？\n"
                "2. 情感节奏是否张弛有度（有紧张也有舒缓，有心动也有心痛）？\n"
                "3. 两位主角的声音是否有足够的辨识度？对话能分辨出是谁说的吗？\n"
                "4. 章末是否留下了让读者牵挂关系走向的悬念？"
            ),
            "chapter_review_instruction_en": (
                "When evaluating this chapter, focus on:\n"
                "1. How much did the relationship advance? Can readers clearly feel the distance change?\n"
                "2. Is the emotional rhythm balanced (tension and relief, flutter and ache)?\n"
                "3. Do both leads have distinct voices? Can you tell who is speaking from dialogue alone?\n"
                "4. Does the chapter ending leave suspense about where the relationship is heading?"
            ),
            "scene_rewrite_system_zh": (
                "你是情感驱动型小说的场景重写专家。重写时必须强化情感位移、对话潜台词和关系推进。"
                "不要把情感直接说出来，而是通过行动、反应和沉默来演。"
            ),
            "scene_rewrite_system_en": (
                "You are a scene rewrite specialist for relationship-driven fiction. "
                "Rewrites must strengthen emotional displacement, dialogue subtext, and relationship progression. "
                "Do not state emotions directly — show them through actions, reactions, and silence."
            ),
            "scene_rewrite_instruction_zh": (
                "重写时优先补强：\n"
                "1. 情感位移（确保人物在场景结束时的心理状态和开始时不同）\n"
                "2. 对话潜台词（让角色说出来的话和心里想的不一样，读者通过上下文自己领会）\n"
                "3. 身体语言配合（用微动作、视线、呼吸变化来传递情感而不是直接陈述）\n"
                "4. 场景氛围（利用环境、光线、温度等感官元素烘托情感基调）"
            ),
            "scene_rewrite_instruction_en": (
                "When rewriting, prioritize:\n"
                "1. Emotional displacement (character's state at scene end differs from start)\n"
                "2. Dialogue subtext (what characters say differs from what they feel; reader infers from context)\n"
                "3. Body language support (use micro-actions, gaze, breathing changes to convey emotion instead of stating it)\n"
                "4. Scene atmosphere (leverage setting, light, temperature, and sensory elements to set emotional tone)"
            ),
            "chapter_rewrite_system_zh": (
                "你是情感驱动型小说的章节重写专家。重写时确保章节内的关系推进可感知、"
                "情感节奏张弛有度、角色声音有辨识度。"
            ),
            "chapter_rewrite_system_en": (
                "You are a chapter rewrite specialist for relationship-driven fiction. "
                "Ensure perceivable relationship progression, balanced emotional rhythm, "
                "and distinct character voices throughout the chapter."
            ),
            "chapter_rewrite_instruction_zh": (
                "重写时优先补强：\n"
                "1. 关系推进的可感知度（本章结束时，读者必须能说清楚两人的距离变了多少）\n"
                "2. 情感节奏控制（不能全程高压或全程甜蜜，需要有起伏和喘息空间）\n"
                "3. 角色声音区分（两位主角的内心独白和对话风格必须有明显差异）\n"
                "4. 章末悬念（修复或强化关系层面的悬念，让读者无法不翻下一章）"
            ),
            "chapter_rewrite_instruction_en": (
                "When rewriting, prioritize:\n"
                "1. Perceivable relationship progress (at chapter's end, readers must be able to tell how much the distance changed)\n"
                "2. Emotional rhythm control (cannot be all-tension or all-sweet; needs peaks and breathing room)\n"
                "3. Voice distinction (both leads' interiority and dialogue style must be noticeably different)\n"
                "4. Chapter-end suspense (repair or strengthen relationship-level suspense so readers must turn the page)"
            ),
        },
    },
    # ------------------------------------------------------------------
    # SUSPENSE-MYSTERY
    # ------------------------------------------------------------------
    "suspense-mystery": {
        "name": "悬疑推理 / Suspense-Mystery",
        "description": (
            "以信息层层释放和真相逐步揭露为核心的类型，"
            "包括悬疑侦探、规则怪谈、民俗怪谈、无限流等重视逻辑线和悬念管理的子类型。"
        ),
        "scene_weights": {
            "hook_strength": 1.4,
            "payoff_density": 1.3,
            "conflict_clarity": 1.2,
            "emotion": 0.8,
            "conflict": 0.9,
        },
        "chapter_weights": {
            "ending_hook_effectiveness": 1.4,
            "main_plot_progression": 1.2,
            "coherence": 1.2,
            "pacing_rhythm": 1.1,
        },
        "scene_threshold_override": None,
        "chapter_threshold_override": None,
        "signal_keywords": {
            "conflict_terms_zh": [
                "对峙", "盘问", "谎言", "威胁", "试探",
                "揭穿", "反咬", "逼供", "沉默", "暗示",
            ],
            "conflict_terms_en": [
                "standoff", "interrogation", "lie", "threat", "probing",
                "exposure", "counter-accusation", "coercion", "silence", "insinuation",
            ],
            "emotion_terms_zh": [
                "恐惧", "不安", "怀疑", "背脊发凉", "心悸",
                "窒息", "绝望", "惊愕", "警觉", "毛骨悚然",
            ],
            "emotion_terms_en": [
                "fear", "unease", "suspicion", "chill down the spine", "palpitation",
                "suffocation", "despair", "shock", "alertness", "creeping dread",
            ],
            "hook_terms_zh": [
                "新证据", "失踪", "反转", "监控", "不在场",
                "共犯", "第二现场",
            ],
            "hook_terms_en": [
                "new evidence", "disappearance", "reversal", "surveillance",
                "alibi", "accomplice", "second crime scene",
            ],
            "info_terms_zh": [
                "矛盾", "证词", "时间线", "缺失", "伪装",
                "误导", "嫌疑", "关键细节", "指纹", "动机",
            ],
            "info_terms_en": [
                "contradiction", "testimony", "timeline", "missing piece", "disguise",
                "misdirection", "suspicion", "key detail", "fingerprint", "motive",
            ],
        },
        "finding_messages": {
            "conflict_low_zh": (
                "悬疑场景的对抗张力不足，角色之间缺少盘问、试探或信息博弈的紧张感。"
            ),
            "conflict_low_en": (
                "Suspense-scene confrontational tension is weak; "
                "characters lack the strain of interrogation, probing, or information warfare."
            ),
            "conflict_clarity_low_zh": (
                "当前的信息对抗不够清晰，读者无法判断谁掌握了什么信息、谁在撒谎。"
            ),
            "conflict_clarity_low_en": (
                "The information standoff is unclear; "
                "readers cannot judge who knows what or who is lying."
            ),
            "emotion_low_zh": (
                "悬疑氛围不够浓郁，缺少通过环境、身体反应和节奏变化营造的不安感和压迫感。"
            ),
            "emotion_low_en": (
                "Suspense atmosphere is thin; use environment, physical reactions, "
                "and pacing shifts to build unease and oppression."
            ),
            "emotional_movement_low_zh": (
                "角色面对新线索或新威胁时的心理变化不够可感，认知冲击没有被充分演绎。"
            ),
            "emotional_movement_low_en": (
                "The character's psychological shift in response to new clues or threats is not palpable; "
                "the cognitive impact has not been fully dramatized."
            ),
            "dialogue_low_zh": (
                "审讯/对话场景缺少信息攻防的层次，双方没有在对话中暗藏线索或设置陷阱。"
            ),
            "dialogue_low_en": (
                "Interrogation/dialogue scenes lack layered information offense-defense; "
                "neither side is hiding clues or setting traps in conversation."
            ),
            "hook_low_zh": (
                "场景尾部的信息悬念不够硬，缺少能让读者迫切想知道下一步真相的新线索或反转。"
            ),
            "hook_low_en": (
                "The scene-ending information hook is too soft; "
                "it lacks a new clue or twist that compels readers to seek the next piece of truth."
            ),
            "payoff_low_zh": (
                "信息释放密度偏低，场景内没有揭示足够的新线索、新矛盾或关键细节来推进推理线。"
            ),
            "payoff_low_en": (
                "Information release density is low; the scene does not reveal enough new clues, "
                "contradictions, or key details to advance the deduction line."
            ),
            "voice_low_zh": (
                "叙事声音缺乏悬疑类型特有的克制和暗示感，语气过于平淡或过于直白。"
            ),
            "voice_low_en": (
                "The narrative voice lacks the restraint and suggestiveness characteristic of suspense; "
                "the tone is too flat or too blunt."
            ),
            "contract_low_zh": (
                "当前场景没有兑现 scene contract 中承诺的关键线索揭示或真相推进。"
            ),
            "contract_low_en": (
                "The scene has not delivered the key clue revelation or truth advancement promised by the scene contract."
            ),
        },
        "plan_rubric": {
            "required_checks": [
                "clue_chain_exists",
                "misdirection_planned",
                "information_escalation",
            ],
            "min_antagonist_forces": 1,
            "require_power_system_tiers": False,
            "require_relationship_milestones": False,
            "require_clue_chain": True,
            "require_theme_per_volume": True,
            "min_key_reveals_per_volume": 2,
            "require_foreshadowing": True,
            "llm_evaluation_prompt_zh": (
                "请检查规划中是否包含完整的线索链（从初始线索到最终真相的推演路径）、"
                "至少一条精心设计的误导线（让读者误判的假线索或假嫌疑人），"
                "以及信息释放的节奏设计（每卷内线索的密度递增是否合理）。"
            ),
            "llm_evaluation_prompt_en": (
                "Verify the plan includes a complete clue chain (deduction path from initial clue to final truth), "
                "at least one carefully designed misdirection line (false clues or suspects), "
                "and information-release pacing (reasonable density escalation within each volume)."
            ),
        },
        "planner_prompts": {
            "book_spec_system_zh": (
                "你是一位专精悬疑推理类小说的策划总监。你深谙侦探推理、怪谈、心理悬疑和无限流的叙事逻辑。"
                "你的核心职责是确保书籍规格中包含完整的分层真相结构、线索链设计和误导策略。"
                "所有规划必须经得起逻辑推敲，同时保持读者的好奇心和推理参与感。"
            ),
            "book_spec_system_en": (
                "You are a senior editor specializing in suspense-mystery fiction. "
                "You understand narrative logic for detective stories, horror tales, psychological thrillers, and infinite loops. "
                "Your core duty is to ensure the book spec has a layered truth structure, clue chain design, and misdirection strategy. "
                "All planning must withstand logical scrutiny while maintaining reader curiosity and deductive engagement."
            ),
            "book_spec_instruction_zh": (
                "在书籍规格中必须明确以下要素：\n"
                "1. 分层真相结构（表层真相→中层真相→底层真相，每层对应的揭示时机）\n"
                "2. 线索链设计（从第一条线索到最终真相的完整推演路径，包括关键转折点）\n"
                "3. 误导策略（至少一条精心设计的假线索或假嫌疑人，以及读者发现被误导的时机）\n"
                "4. 信息控制规则（什么时候释放什么级别的信息，读者始终知道比角色多还是少）\n"
                "5. 逻辑自洽检查清单（所有谜题的答案是否在文中有足够的线索支撑）"
            ),
            "book_spec_instruction_en": (
                "The book spec must define:\n"
                "1. Layered truth structure (surface truth → middle truth → deep truth, with reveal timing for each)\n"
                "2. Clue chain design (complete deduction path from first clue to final truth, including turning points)\n"
                "3. Misdirection strategy (at least one designed false clue or suspect, and when readers discover the misdirection)\n"
                "4. Information control rules (when to release what level of info; does the reader know more or less than the character?)\n"
                "5. Logical consistency checklist (are all puzzle answers supported by sufficient in-text clues?)"
            ),
            "world_spec_system_zh": (
                "你是悬疑推理小说的世界观设计师。世界设定的核心功能是为谜题提供舞台——"
                "封闭空间如何制造压力？规则体系如何成为推理的基础？时间和空间的限制如何增加难度？"
            ),
            "world_spec_system_en": (
                "You are a worldbuilding specialist for suspense-mystery fiction. "
                "The world's core function is to provide a stage for puzzles — "
                "how do enclosed spaces create pressure? How do rule systems form the basis of deduction? "
                "How do time and space constraints increase difficulty?"
            ),
            "world_spec_instruction_zh": (
                "世界设定必须包含：\n"
                "1. 空间结构（封闭/半封闭环境的设计，出入口、监控、隐藏区域）\n"
                "2. 规则体系（如果是规则类，规则的完整定义、已知部分和隐藏部分）\n"
                "3. 时间线框架（事件发生的时间序列，关键时间窗口和不在场证明的验证条件）\n"
                "4. 信息不对称设计（不同角色掌握不同的信息片段，读者可以通过对比发现矛盾）"
            ),
            "world_spec_instruction_en": (
                "The world spec must include:\n"
                "1. Spatial structure (enclosed/semi-enclosed environment design, entry/exit, surveillance, hidden areas)\n"
                "2. Rule system (if rule-based: complete rule definition, known vs. hidden portions)\n"
                "3. Timeline framework (chronological event sequence, critical time windows, alibi verification conditions)\n"
                "4. Information asymmetry design (different characters hold different information fragments; readers can spot contradictions by comparing)"
            ),
            "cast_spec_system_zh": (
                "你是悬疑推理小说的角色设计专家。每个角色都是一个信息节点——"
                "他们各自知道什么、隐瞒什么、能证明什么、会误导什么。"
            ),
            "cast_spec_system_en": (
                "You are a character designer for suspense-mystery fiction. "
                "Every character is an information node — "
                "what they know, what they conceal, what they can prove, and what they misdirect."
            ),
            "cast_spec_instruction_zh": (
                "角色列表必须包含：\n"
                "1. 每个嫌疑人的动机、手段、机会（三要素完整标注）\n"
                "2. 每个角色掌握的关键信息片段和他们的谎言/遗漏\n"
                "3. 角色之间的关系网络（信任、怀疑、利益、秘密共享）\n"
                "4. 侦探/主角的推理风格和认知盲区"
            ),
            "cast_spec_instruction_en": (
                "The cast list must include:\n"
                "1. Every suspect's motive, means, and opportunity (complete triad annotation)\n"
                "2. Key information fragments each character holds and their lies/omissions\n"
                "3. Relationship network (trust, suspicion, interest, secret sharing)\n"
                "4. Detective/protagonist's deduction style and cognitive blind spots"
            ),
            "volume_plan_system_zh": (
                "你是悬疑推理小说的卷级规划师。每一卷围绕一层真相展开，"
                "线索释放密度必须递增，误导和反转要精确安排在情绪峰值附近。"
            ),
            "volume_plan_system_en": (
                "You are a volume-level planner for suspense-mystery fiction. "
                "Each volume revolves around one truth layer; clue release density must escalate, "
                "and misdirections/twists must be timed near emotional peaks."
            ),
            "volume_plan_instruction_zh": (
                "每卷规划必须明确：\n"
                "1. 本卷要揭示的真相层级（表层/中层/底层的哪一部分）\n"
                "2. 线索释放节奏（前半段铺线索，中段制造误导，后半段连续反转）\n"
                "3. 关键反转点的位置和触发条件\n"
                "4. 本卷的核心谜题和解题的关键线索\n"
                "5. 卷末信息悬念（揭示了一层真相的同时，暴露出更深层的谜团）"
            ),
            "volume_plan_instruction_en": (
                "Each volume plan must define:\n"
                "1. Truth layer to reveal (which part of surface/middle/deep truth)\n"
                "2. Clue release pacing (first half plants clues, middle creates misdirection, second half delivers twists)\n"
                "3. Key twist positions and trigger conditions\n"
                "4. Core puzzle and critical solving clue for this volume\n"
                "5. Volume-end information suspense (revealing one truth layer while exposing a deeper mystery)"
            ),
            "outline_system_zh": (
                "你是悬疑推理小说的章节大纲师。每一章都必须释放至少一条新线索或新矛盾，"
                "信息密度不能低于读者的推理参与阈值。"
            ),
            "outline_system_en": (
                "You are a chapter outliner for suspense-mystery fiction. "
                "Every chapter must release at least one new clue or contradiction; "
                "information density must not fall below the reader's deductive engagement threshold."
            ),
            "outline_instruction_zh": (
                "章节大纲必须遵守：\n"
                "1. 每章至少释放一条新线索、新矛盾或新嫌疑\n"
                "2. 信息释放的节奏由松到紧，越接近真相揭示，线索密度越高\n"
                "3. 每章结尾必须有信息层面的硬钩子（新证据？失踪？证词矛盾？）\n"
                "4. 误导线索的植入必须自然，不能让读者感觉是作者在刻意欺骗\n"
                "5. 关键推理场景必须让读者有参与感——线索已经给够了，但拼图还差一块"
            ),
            "outline_instruction_en": (
                "The chapter outline must follow:\n"
                "1. Every chapter releases at least one new clue, contradiction, or suspect lead\n"
                "2. Information pacing goes from loose to tight; closer to the reveal, denser the clues\n"
                "3. Every chapter ending has an information-level hard hook (new evidence? disappearance? testimony contradiction?)\n"
                "4. Misdirection must be planted naturally, never feeling like deliberate authorial deception\n"
                "5. Key deduction scenes must give readers agency — clues are sufficient but one puzzle piece remains missing"
            ),
        },
        "judge_prompts": {
            "scene_review_system_zh": (
                "你是悬疑推理小说的场景审稿专家。评估的核心维度是信息释放密度、悬疑氛围的营造和认知误导的有效性。"
                "好的悬疑场景应该让读者在阅读过程中不断修正自己的推理假设。"
            ),
            "scene_review_system_en": (
                "You are a scene review specialist for suspense-mystery fiction. "
                "Core dimensions are information release density, suspense atmosphere construction, and misdirection effectiveness. "
                "A good suspense scene should make readers continuously revise their deductive hypotheses while reading."
            ),
            "scene_review_instruction_zh": (
                "评估当前场景时请关注：\n"
                "1. 信息释放密度是否足够？本场景是否揭示了至少一个新线索或新矛盾？\n"
                "2. 悬疑氛围是否通过环境、节奏和人物反应自然营造？\n"
                "3. 如果存在误导，误导是否自然，读者会在真相揭示时感到 '原来如此' 而非 '这不公平'？\n"
                "4. 信息的释放是否服务于推理线的推进？是否避免了无意义的信息噪声？"
            ),
            "scene_review_instruction_en": (
                "When evaluating this scene, focus on:\n"
                "1. Is information release density sufficient? Does this scene reveal at least one new clue or contradiction?\n"
                "2. Is the suspense atmosphere naturally built through environment, pacing, and character reactions?\n"
                "3. If misdirection is present, is it natural? Will readers feel 'of course!' rather than 'that's unfair!' at the reveal?\n"
                "4. Does information release serve deduction-line progression? Is meaningless noise avoided?"
            ),
            "chapter_review_system_zh": (
                "你是悬疑推理小说的章节审稿专家。章节级评估关注信息推进的完整性、"
                "推理参与感的维持和章末悬念的牵引力。"
            ),
            "chapter_review_system_en": (
                "You are a chapter review specialist for suspense-mystery fiction. "
                "Chapter-level evaluation focuses on information progression completeness, "
                "maintained deductive engagement, and chapter-ending suspense pull."
            ),
            "chapter_review_instruction_zh": (
                "评估当前章节时请关注：\n"
                "1. 本章在信息维度上推进了多少？读者的认知地图是否有明确更新？\n"
                "2. 推理参与感是否保持？读者是否有足够线索形成自己的假设？\n"
                "3. 章末悬念是否足够硬？是否揭示了一个新的谜团或推翻了一个已有假设？\n"
                "4. 场景之间的信息传递是否连贯？是否存在逻辑断裂？"
            ),
            "chapter_review_instruction_en": (
                "When evaluating this chapter, focus on:\n"
                "1. How much information progression? Has the reader's cognitive map been clearly updated?\n"
                "2. Is deductive engagement maintained? Do readers have enough clues to form their own hypotheses?\n"
                "3. Is the chapter-end suspense hard enough? Does it reveal a new mystery or topple an existing assumption?\n"
                "4. Is information transfer between scenes coherent? Are there logic gaps?"
            ),
            "scene_rewrite_system_zh": (
                "你是悬疑推理小说的场景重写专家。重写时必须强化信息释放密度、悬疑氛围和线索的可推理性。"
                "避免直接揭示答案，而是通过细节暗示让读者自己推导。"
            ),
            "scene_rewrite_system_en": (
                "You are a scene rewrite specialist for suspense-mystery fiction. "
                "Rewrites must strengthen information density, suspense atmosphere, and clue deducibility. "
                "Avoid direct reveals; use detail-based hints that let readers deduce on their own."
            ),
            "scene_rewrite_instruction_zh": (
                "重写时优先补强：\n"
                "1. 信息密度（确保每个场景至少有一条可推理的新线索，用细节而非直白陈述传递）\n"
                "2. 悬疑氛围（通过环境描写、人物异常反应和节奏放缓来营造不安感）\n"
                "3. 人物信息博弈（审讯或对话中必须有攻防层次，而非简单的问答）\n"
                "4. 尾钩的信息冲击力（用新证据、新矛盾或关键发现作为场景收束）"
            ),
            "scene_rewrite_instruction_en": (
                "When rewriting, prioritize:\n"
                "1. Information density (at least one deducible new clue per scene, conveyed through detail, not exposition)\n"
                "2. Suspense atmosphere (build unease through environment, anomalous character reactions, and pacing shifts)\n"
                "3. Character information warfare (interrogation/dialogue must have attack-defense layers, not simple Q&A)\n"
                "4. Hook information impact (close with new evidence, contradiction, or pivotal discovery)"
            ),
            "chapter_rewrite_system_zh": (
                "你是悬疑推理小说的章节重写专家。重写时确保章节的信息推进完整、"
                "推理参与感持续、章末悬念有力。"
            ),
            "chapter_rewrite_system_en": (
                "You are a chapter rewrite specialist for suspense-mystery fiction. "
                "Ensure complete information progression, sustained deductive engagement, "
                "and a powerful chapter-end hook."
            ),
            "chapter_rewrite_instruction_zh": (
                "重写时优先补强：\n"
                "1. 信息推进（确保本章在信息维度上有明确的新发现或旧假设的颠覆）\n"
                "2. 推理参与感（给读者足够的线索去形成和修正自己的假设）\n"
                "3. 章末悬念（用信息层面的硬钩子收尾，让读者无法不继续阅读）\n"
                "4. 场景衔接逻辑（确保信息在场景之间自然流动，避免逻辑断裂）"
            ),
            "chapter_rewrite_instruction_en": (
                "When rewriting, prioritize:\n"
                "1. Information progression (ensure clear new discovery or overthrow of prior assumption)\n"
                "2. Deductive engagement (give readers enough clues to form and revise their hypotheses)\n"
                "3. Chapter-end suspense (close with an information-level hard hook that compels continued reading)\n"
                "4. Scene transition logic (ensure information flows naturally between scenes without logic gaps)"
            ),
        },
    },
    # ------------------------------------------------------------------
    # STRATEGY-WORLDBUILDING
    # ------------------------------------------------------------------
    "strategy-worldbuilding": {
        "name": "谋略经营 / Strategy-Worldbuilding",
        "description": (
            "以势力博弈、战略选择和宏大世界构建为核心的类型，"
            "包括历史争霸、星海战争、都市黑科技、史诗奇幻等重视谋略深度和世界观厚度的子类型。"
        ),
        "scene_weights": {
            "voice_consistency": 1.2,
            "payoff_density": 1.2,
            "conflict": 1.1,
            "hook": 0.9,
        },
        "chapter_weights": {
            "main_plot_progression": 1.3,
            "volume_mission_alignment": 1.2,
            "coherence": 1.1,
            "thematic_resonance": 1.1,
        },
        "scene_threshold_override": None,
        "chapter_threshold_override": None,
        "signal_keywords": {
            "conflict_terms_zh": [
                "谈判", "结盟", "背叛", "兵力", "布局",
                "攻防", "离间", "反间", "权衡", "博弈",
            ],
            "conflict_terms_en": [
                "negotiation", "alliance", "betrayal", "force deployment", "positioning",
                "offense-defense", "sowing discord", "counter-intelligence", "trade-off", "game theory",
            ],
            "emotion_terms_zh": [
                "野心", "权欲", "孤独", "沉重", "坚定",
                "绝望", "觉悟", "豪情", "隐忍", "决绝",
            ],
            "emotion_terms_en": [
                "ambition", "power hunger", "solitude", "weight of command", "resolve",
                "despair", "awakening", "heroic spirit", "endurance", "ruthless determination",
            ],
            "hook_terms_zh": [
                "新势力", "叛变", "密报", "战略转折",
                "资源发现", "科技突破", "外交危机",
            ],
            "hook_terms_en": [
                "new faction", "mutiny", "secret intelligence", "strategic turning point",
                "resource discovery", "tech breakthrough", "diplomatic crisis",
            ],
            "info_terms_zh": [
                "势力", "版图", "资源", "科技", "人口", "军事",
            ],
            "info_terms_en": [
                "faction", "territory", "resource", "technology", "population", "military",
            ],
        },
        "finding_messages": {
            "conflict_low_zh": (
                "势力博弈的策略深度不足，冲突更像是直接对撞而非经过算计的谋略交锋。"
            ),
            "conflict_low_en": (
                "Strategic depth in faction conflict is insufficient; "
                "the clash feels like a direct collision rather than a calculated strategic maneuver."
            ),
            "conflict_clarity_low_zh": (
                "博弈各方的利益诉求和筹码不够清晰，读者难以判断当前局面的战略意义。"
            ),
            "conflict_clarity_low_en": (
                "The interests and leverage of each side are unclear; "
                "readers cannot judge the strategic significance of the current situation."
            ),
            "emotion_low_zh": (
                "决策者的内心挣扎和权力孤独感表达不足，缺少战略选择背后的人性层面。"
            ),
            "emotion_low_en": (
                "The decision-maker's inner struggle and loneliness of power are underexpressed; "
                "the human dimension behind strategic choices is missing."
            ),
            "emotional_movement_low_zh": (
                "角色在战略决策前后的心态变化不够明显，权力博弈没有对人物造成可感知的内心影响。"
            ),
            "emotional_movement_low_en": (
                "Character mentality does not shift enough before and after strategic decisions; "
                "the power game has not caused perceptible inner impact."
            ),
            "dialogue_low_zh": (
                "谈判或谋略对话缺少信息博弈的层次，对话内容过于直白，缺少暗藏锋芒。"
            ),
            "dialogue_low_en": (
                "Negotiation or strategy dialogue lacks information-warfare layering; "
                "content is too blunt, lacking hidden edges."
            ),
            "hook_low_zh": (
                "场景尾部没有抛出足够有战略意义的新变量——新势力、新科技、新危机。"
            ),
            "hook_low_en": (
                "The scene ending does not introduce a strategically significant new variable — "
                "new faction, new technology, new crisis."
            ),
            "payoff_low_zh": (
                "战略布局的阶段性成果没有被充分展示，读者看不出当前的谋略操作带来了什么实际收益。"
            ),
            "payoff_low_en": (
                "Phased results of strategic planning are not adequately shown; "
                "readers cannot see what practical gains the current maneuver achieved."
            ),
            "voice_low_zh": (
                "叙事腔调缺乏策略类作品特有的沉稳和厚重感，语气过于轻浮或过于解说。"
            ),
            "voice_low_en": (
                "The narrative voice lacks the gravitas and weight characteristic of strategy fiction; "
                "tone is too flippant or too expository."
            ),
            "contract_low_zh": (
                "当前场景没有兑现 scene contract 中承诺的势力格局变化或战略推进。"
            ),
            "contract_low_en": (
                "The scene has not delivered the faction dynamics shift or strategic advancement promised by the scene contract."
            ),
        },
        "plan_rubric": {
            "required_checks": [
                "faction_progression",
                "worldbuilding_depth_check",
            ],
            "min_antagonist_forces": 2,
            "require_power_system_tiers": False,
            "require_relationship_milestones": False,
            "require_clue_chain": False,
            "require_theme_per_volume": True,
            "min_key_reveals_per_volume": 1,
            "require_foreshadowing": True,
            "llm_evaluation_prompt_zh": (
                "请检查规划中是否包含至少三个势力的动态博弈关系、"
                "每个势力的核心利益诉求和资源禀赋，以及战略决策的代价设计（不存在无代价的胜利）。"
            ),
            "llm_evaluation_prompt_en": (
                "Verify the plan includes dynamic game-theory relationships among at least three factions, "
                "each faction's core interests and resource endowments, "
                "and cost design for strategic decisions (no cost-free victories)."
            ),
        },
        "planner_prompts": {
            "book_spec_system_zh": (
                "你是一位专精谋略经营类小说的策划总监。你熟悉历史争霸、星战策略和都市科技流的叙事模式。"
                "你的核心职责是确保书籍规格中包含完整的势力版图设计、战略博弈框架和世界观深度。"
                "所有规划必须让每个战略选择都有代价，让世界观服务于冲突而非成为装饰。"
            ),
            "book_spec_system_en": (
                "You are a senior editor specializing in strategy-worldbuilding fiction. "
                "You understand narrative modes for historical hegemony, star-war strategy, and urban tech fiction. "
                "Your core duty is to ensure complete faction territory design, strategic game framework, and worldbuilding depth. "
                "Every strategic choice must have a cost; worldbuilding must serve conflict, not decoration."
            ),
            "book_spec_instruction_zh": (
                "在书籍规格中必须明确以下要素：\n"
                "1. 势力版图（至少三个主要势力的领地、资源、军事实力和核心利益）\n"
                "2. 博弈框架（势力之间的合纵连横逻辑，谁和谁有结盟可能？谁和谁必然冲突？）\n"
                "3. 战略选择的代价机制（每个重大决策必须有明确的机会成本或附带损失）\n"
                "4. 世界观深度检查点（历史背景、科技水平、文化差异如何影响战略格局）\n"
                "5. 主角的战略定位（是势力首领？谋士？还是从底层崛起的新势力？）"
            ),
            "book_spec_instruction_en": (
                "The book spec must define:\n"
                "1. Faction territory map (at least three major factions with territories, resources, military power, core interests)\n"
                "2. Game framework (alliance/opposition logic; who may ally? who must clash?)\n"
                "3. Cost mechanism for strategic choices (every major decision has explicit opportunity cost or collateral)\n"
                "4. Worldbuilding depth checkpoints (historical background, tech level, cultural differences affecting strategic landscape)\n"
                "5. Protagonist's strategic position (faction leader? advisor? rising new force from the bottom?)"
            ),
            "world_spec_system_zh": (
                "你是谋略经营类小说的世界观设计师。世界设定是博弈的棋盘——"
                "地理决定战略纵深，资源分布决定争夺焦点，科技水平决定战术可能性。"
            ),
            "world_spec_system_en": (
                "You are a worldbuilding specialist for strategy fiction. "
                "The world is the game board — geography determines strategic depth, "
                "resource distribution determines contest focuses, tech level determines tactical possibilities."
            ),
            "world_spec_instruction_zh": (
                "世界设定必须包含：\n"
                "1. 地理/空间战略要素（关键据点、贸易通道、战略纵深）\n"
                "2. 资源经济体系（稀缺资源的类型、分布和争夺逻辑）\n"
                "3. 科技/军事体系（各势力的军事差异化和技术代差）\n"
                "4. 政治/文化体系（外交规则、联盟条件、文明差异对战略的影响）"
            ),
            "world_spec_instruction_en": (
                "The world spec must include:\n"
                "1. Geographic/spatial strategic elements (key strongholds, trade routes, strategic depth)\n"
                "2. Resource economy (scarce resource types, distribution, and contest logic)\n"
                "3. Tech/military system (military differentiation and tech gaps between factions)\n"
                "4. Political/cultural system (diplomatic rules, alliance conditions, civilization differences affecting strategy)"
            ),
            "cast_spec_system_zh": (
                "你是谋略经营类小说的角色设计专家。角色是棋手——"
                "每个重要角色代表一种战略思维方式，他们的碰撞就是策略的碰撞。"
            ),
            "cast_spec_system_en": (
                "You are a character designer for strategy fiction. "
                "Characters are chess players — each represents a strategic mindset; "
                "their clashes are clashes of strategy."
            ),
            "cast_spec_instruction_zh": (
                "角色列表必须包含：\n"
                "1. 每个势力核心决策者的战略风格（激进/保守/投机/平衡）\n"
                "2. 主角的谋略特长和性格弱点（不允许全能型，必须有盲点）\n"
                "3. 关键谋士/将领角色的专业分工和与主角的互补关系\n"
                "4. 对手阵营中至少一个值得尊敬的对手（有自己的正当理由和合理战略）"
            ),
            "cast_spec_instruction_en": (
                "The cast list must include:\n"
                "1. Strategic style for each faction's core decision-maker (aggressive/conservative/opportunistic/balanced)\n"
                "2. Protagonist's strategic strengths and personality weaknesses (no omnipotent types; must have blind spots)\n"
                "3. Key advisors/generals with specialized roles complementing the protagonist\n"
                "4. At least one respectable opponent (with legitimate reasons and sound strategy)"
            ),
            "volume_plan_system_zh": (
                "你是谋略经营类小说的卷级规划师。每一卷围绕一个战略阶段展开——"
                "势力格局如何变化？主角的战略目标是什么？代价是什么？"
            ),
            "volume_plan_system_en": (
                "You are a volume planner for strategy fiction. "
                "Each volume revolves around a strategic phase — "
                "how does the faction landscape change? What is the protagonist's strategic goal? What is the cost?"
            ),
            "volume_plan_instruction_zh": (
                "每卷规划必须明确：\n"
                "1. 本卷的战略目标（扩张领土？巩固内部？化解外部危机？）\n"
                "2. 势力格局变化（本卷结束时，各势力的实力对比和关系如何变化？）\n"
                "3. 关键博弈事件（至少 2-3 场有战略意义的谈判、战役或政治事件）\n"
                "4. 代价清单（为了达成目标，主角付出了什么代价？失去了什么？）\n"
                "5. 卷末新格局悬念（新的不稳定因素出现，下一卷的冲突种子已经种下）"
            ),
            "volume_plan_instruction_en": (
                "Each volume plan must define:\n"
                "1. Strategic goal (territory expansion? internal consolidation? external crisis resolution?)\n"
                "2. Faction landscape changes (power balance and relationship shifts by volume's end)\n"
                "3. Key game events (at least 2-3 strategically significant negotiations, battles, or political events)\n"
                "4. Cost ledger (what did the protagonist sacrifice? what was lost?)\n"
                "5. Volume-end new-landscape suspense (new destabilizing factors; next volume's conflict seeds are planted)"
            ),
            "outline_system_zh": (
                "你是谋略经营类小说的章节大纲师。确保每章都有战略推进意义，"
                "避免出现与大局无关的日常过渡章节。"
            ),
            "outline_system_en": (
                "You are a chapter outliner for strategy fiction. "
                "Ensure every chapter has strategic advancement significance; "
                "avoid transition chapters unrelated to the larger picture."
            ),
            "outline_instruction_zh": (
                "章节大纲必须遵守：\n"
                "1. 每章至少包含一个有战略意义的事件（谈判、战役、情报获取、势力变动）\n"
                "2. 大局信息释放和角色行动交替，避免纯战略分析或纯行动的单一节奏\n"
                "3. 每章结尾必须有战略层面的悬念（新变量、新威胁、意外结盟或背叛）\n"
                "4. 世界观细节必须通过事件自然展开，不能出现大段百科式说明\n"
                "5. 战役章节必须有战略目标-执行-结果-后果的完整逻辑链"
            ),
            "outline_instruction_en": (
                "The chapter outline must follow:\n"
                "1. Every chapter includes at least one strategically significant event\n"
                "2. Alternate grand-picture information and character action; avoid monotone pure-analysis or pure-action chapters\n"
                "3. Every chapter ending has strategic-level suspense (new variable, threat, unexpected alliance/betrayal)\n"
                "4. Worldbuilding details must emerge naturally through events, not encyclopedic exposition blocks\n"
                "5. Battle chapters must have a complete logic chain: strategic objective - execution - result - aftermath"
            ),
        },
        "judge_prompts": {
            "scene_review_system_zh": (
                "你是谋略经营类小说的场景审稿专家。评估的核心维度是战略深度、权力动态变化和世界观服务于情节的有效性。"
                "好的谋略场景让读者感受到棋局在移动，而不仅仅是人物在说话。"
            ),
            "scene_review_system_en": (
                "You are a scene review specialist for strategy-worldbuilding fiction. "
                "Core dimensions are strategic depth, power-dynamics shifts, and worldbuilding's effectiveness in serving plot. "
                "A good strategy scene makes readers feel the game board shifting, not just characters talking."
            ),
            "scene_review_instruction_zh": (
                "评估当前场景时请关注：\n"
                "1. 场景中的战略行动是否有明确的目标、代价和后果？\n"
                "2. 权力格局是否在场景中发生了可感知的变化？\n"
                "3. 世界观元素是否自然融入事件，还是像百科词条一样堆砌？\n"
                "4. 角色的战略决策是否体现了其性格和战略风格的一致性？"
            ),
            "scene_review_instruction_en": (
                "When evaluating this scene, focus on:\n"
                "1. Do strategic actions have clear goals, costs, and consequences?\n"
                "2. Does the power landscape perceptibly shift during the scene?\n"
                "3. Are worldbuilding elements naturally woven into events, or piled like encyclopedia entries?\n"
                "4. Do character strategic decisions reflect consistent personality and strategic style?"
            ),
            "chapter_review_system_zh": (
                "你是谋略经营类小说的章节审稿专家。章节级评估关注战略推进的完整性、"
                "势力格局变化的可感知度和世界观深度的有效利用。"
            ),
            "chapter_review_system_en": (
                "You are a chapter review specialist for strategy-worldbuilding fiction. "
                "Chapter-level evaluation focuses on strategic progression completeness, "
                "perceivable faction-landscape change, and effective worldbuilding depth utilization."
            ),
            "chapter_review_instruction_zh": (
                "评估当前章节时请关注：\n"
                "1. 本章是否推动了一个战略阶段的实质进展？\n"
                "2. 各势力的实力对比或关系是否有可感知的变化？\n"
                "3. 世界观的利用是否有效——信息释放是否服务于冲突推进？\n"
                "4. 章末是否呈现了一个新的战略变量或不稳定因素？"
            ),
            "chapter_review_instruction_en": (
                "When evaluating this chapter, focus on:\n"
                "1. Does it drive substantive progress in a strategic phase?\n"
                "2. Is there perceivable change in faction power balance or relationships?\n"
                "3. Is worldbuilding used effectively — does information serve conflict progression?\n"
                "4. Does the chapter ending present a new strategic variable or instability?"
            ),
            "scene_rewrite_system_zh": (
                "你是谋略经营类小说的场景重写专家。重写时必须强化战略决策的深度和代价感，"
                "让世界观细节为冲突服务而非成为负担。"
            ),
            "scene_rewrite_system_en": (
                "You are a scene rewrite specialist for strategy-worldbuilding fiction. "
                "Rewrites must deepen strategic decision-making and cost perception; "
                "worldbuilding details must serve conflict, not become a burden."
            ),
            "scene_rewrite_instruction_zh": (
                "重写时优先补强：\n"
                "1. 战略决策的深度（决策的利弊分析、机会成本和潜在风险必须被演绎，而非简单陈述）\n"
                "2. 权力动态的可感知变化（通过事件结果让读者感受到势力格局在移动）\n"
                "3. 世界观的有机融入（把百科式说明改为通过角色行动和对话自然展示）\n"
                "4. 战略张力（让对手的谋略也有合理性，避免主角单方面碾压的无聊感）"
            ),
            "scene_rewrite_instruction_en": (
                "When rewriting, prioritize:\n"
                "1. Strategic decision depth (pros/cons, opportunity cost, risks must be dramatized, not stated)\n"
                "2. Perceivable power-dynamic shifts (readers sense the landscape moving through event outcomes)\n"
                "3. Organic worldbuilding (convert encyclopedia blocks to natural revelation through action and dialogue)\n"
                "4. Strategic tension (opponent strategies must be reasonable; avoid boring one-sided protagonist dominance)"
            ),
            "chapter_rewrite_system_zh": (
                "你是谋略经营类小说的章节重写专家。重写时确保章节有完整的战略推进弧、"
                "势力格局的可感知变化和有效的世界观利用。"
            ),
            "chapter_rewrite_system_en": (
                "You are a chapter rewrite specialist for strategy-worldbuilding fiction. "
                "Ensure the chapter has a complete strategic progression arc, "
                "perceivable faction-landscape change, and effective worldbuilding utilization."
            ),
            "chapter_rewrite_instruction_zh": (
                "重写时优先补强：\n"
                "1. 战略推进弧（确保本章的战略事件有起因-执行-结果-后果的完整链条）\n"
                "2. 势力格局变化（明确展示本章事件对大局的影响）\n"
                "3. 世界观利用（删除无关的设定堆砌，保留服务于冲突推进的世界观细节）\n"
                "4. 章末战略悬念（确保读者看到新的不稳定因素或战略转折点）"
            ),
            "chapter_rewrite_instruction_en": (
                "When rewriting, prioritize:\n"
                "1. Strategic progression arc (ensure a complete chain: cause - execution - result - aftermath)\n"
                "2. Faction landscape change (clearly show the chapter's impact on the big picture)\n"
                "3. Worldbuilding utilization (remove irrelevant setting dumps; keep conflict-serving details)\n"
                "4. Chapter-end strategic suspense (ensure readers see new instabilities or strategic turning points)"
            ),
        },
    },
    # ------------------------------------------------------------------
    # ESPORTS-COMPETITION
    # ------------------------------------------------------------------
    "esports-competition": {
        "name": "电竞竞技 / Esports-Competition",
        "description": (
            "以电子竞技赛事和团队竞争为核心的类型，"
            "强调比赛节奏、战术博弈、团队配合和选手成长。"
        ),
        "scene_weights": {
            "hook": 1.3,
            "conflict": 1.3,
            "dialogue": 1.2,
            "payoff_density": 1.1,
        },
        "chapter_weights": {
            "ending_hook_effectiveness": 1.3,
            "main_plot_progression": 1.2,
            "pacing_rhythm": 1.2,
            "character_voice_distinction": 1.1,
        },
        "scene_threshold_override": None,
        "chapter_threshold_override": None,
        "signal_keywords": {
            "conflict_terms_zh": [
                "团战", "Gank", "翻盘", "逆风", "BP",
                "节奏", "对线", "入侵", "包夹", "指挥",
            ],
            "conflict_terms_en": [
                "teamfight", "gank", "comeback", "behind", "ban-pick",
                "tempo", "laning", "invasion", "flank", "shotcalling",
            ],
            "emotion_terms_zh": [
                "紧张", "亢奋", "默契", "崩溃", "信任",
                "压力", "不甘", "热血", "冷静", "爆发",
            ],
            "emotion_terms_en": [
                "tension", "excitement", "synergy", "breakdown", "trust",
                "pressure", "frustration", "burning spirit", "composure", "explosion",
            ],
            "hook_terms_zh": [
                "决赛", "新对手", "伤病", "转会", "新战术",
                "淘汰赛", "复仇",
            ],
            "hook_terms_en": [
                "finals", "new rival", "injury", "transfer", "new tactic",
                "elimination match", "revenge",
            ],
            "info_terms_zh": [
                "战术", "阵容", "数据", "训练", "版本", "战队",
            ],
            "info_terms_en": [
                "tactic", "lineup", "stats", "training", "meta", "team",
            ],
        },
        "finding_messages": {
            "conflict_low_zh": (
                "比赛场景的对抗张力不足，缺少战术攻防和局势反转的紧凑节奏。"
            ),
            "conflict_low_en": (
                "Match-scene tension is weak; tactical offense-defense exchanges and momentum swings are missing."
            ),
            "conflict_clarity_low_zh": (
                "比赛中的战术博弈不够清晰，读者看不出双方的策略意图和关键决策。"
            ),
            "conflict_clarity_low_en": (
                "Tactical game-play in the match is unclear; readers cannot discern strategic intent or key decisions."
            ),
            "emotion_low_zh": (
                "选手的竞技情绪不够鲜明——紧张、亢奋、不甘、信任等竞技特有的情绪体感缺失。"
            ),
            "emotion_low_en": (
                "Player competitive emotions lack vividness — tension, excitement, frustration, "
                "and trust unique to esports are missing."
            ),
            "emotional_movement_low_zh": (
                "选手在比赛前后的心态变化不明显，缺少从压力到释放或从自信到崩溃的内心位移。"
            ),
            "emotional_movement_low_en": (
                "Player mentality does not shift perceptibly before and after the match; "
                "the arc from pressure to release or confidence to collapse is absent."
            ),
            "dialogue_low_zh": (
                "团队沟通和赛后对话缺乏辨识度和功能性，没有体现出不同位置/角色的沟通特征。"
            ),
            "dialogue_low_en": (
                "Team communication and post-match dialogue lack distinctiveness; "
                "different roles' communication characteristics are not reflected."
            ),
            "hook_low_zh": (
                "比赛场景的结尾缺少足够的竞技悬念——下一场对手？新战术？队伍危机？"
            ),
            "hook_low_en": (
                "Match-scene ending lacks sufficient competitive suspense — "
                "next opponent? new tactic? team crisis?"
            ),
            "payoff_low_zh": (
                "比赛结果或战术成功的成就感不够充分，读者缺少获胜或进步的爽感体验。"
            ),
            "payoff_low_en": (
                "Victory or tactical success payoff is not satisfying enough; "
                "readers lack the thrill of winning or advancing."
            ),
            "voice_low_zh": (
                "叙事声音缺乏电竞竞技的活力和专业感，语言风格不够贴近电竞解说和选手心态。"
            ),
            "voice_low_en": (
                "Narrative voice lacks the energy and professionalism of esports; "
                "language style does not match commentary tone or player mindset."
            ),
            "contract_low_zh": (
                "当前场景没有兑现 scene contract 中承诺的比赛节点或竞技高潮。"
            ),
            "contract_low_en": (
                "The scene has not delivered the match milestone or competitive climax promised by the scene contract."
            ),
        },
        "plan_rubric": {
            "required_checks": [
                "tournament_arc_exists",
                "team_dynamic_progression",
            ],
            "min_antagonist_forces": 2,
            "require_power_system_tiers": False,
            "require_relationship_milestones": False,
            "require_clue_chain": False,
            "require_theme_per_volume": True,
            "min_key_reveals_per_volume": 1,
            "require_foreshadowing": True,
            "llm_evaluation_prompt_zh": (
                "请检查规划中是否有完整的赛事弧线（从预选赛到决赛的赛程设计）、"
                "团队成长线（队员之间的信任建立和默契提升），以及至少两个有分量的对手战队。"
            ),
            "llm_evaluation_prompt_en": (
                "Verify the plan has a complete tournament arc (from qualifiers to finals), "
                "team growth line (trust building and synergy development among members), "
                "and at least two formidable rival teams."
            ),
        },
        "planner_prompts": {
            "book_spec_system_zh": (
                "你是一位专精电竞竞技类小说的策划总监。你熟悉 MOBA、FPS、RTS 等电竞类型的赛事体系和选手文化。"
                "你的核心职责是确保书籍规格中包含完整的赛事体系、团队成长路线和战术进化逻辑。"
            ),
            "book_spec_system_en": (
                "You are a senior editor specializing in esports fiction. "
                "You understand tournament systems and player culture for MOBA, FPS, RTS, and other competitive genres. "
                "Your core duty is to ensure the book spec has a complete tournament system, team growth route, and tactical evolution logic."
            ),
            "book_spec_instruction_zh": (
                "书籍规格必须明确：赛事体系（赛程、赛制、晋级规则）、"
                "团队成长路线（从磨合到默契到巅峰的团队弧线）、"
                "核心战术的进化（从初始战术到最终战术的演变路径）、"
                "以及至少两个有深度的对手战队设计。"
            ),
            "book_spec_instruction_en": (
                "The book spec must define: tournament system (schedule, format, advancement rules), "
                "team growth route (from friction to synergy to peak), "
                "core tactic evolution path, and at least two deeply designed rival teams."
            ),
            "world_spec_system_zh": (
                "你是电竞小说的世界观设计师。"
                "游戏规则和赛事体系是这个类型的世界观核心，必须清晰、一致且有趣。"
            ),
            "world_spec_system_en": (
                "You are a worldbuilding specialist for esports fiction. "
                "Game rules and tournament systems are the core worldbuilding; they must be clear, consistent, and engaging."
            ),
            "world_spec_instruction_zh": (
                "世界设定必须包含：游戏核心规则（简化但可信的比赛机制）、"
                "赛事生态（俱乐部体系、转会市场、粉丝文化）、"
                "训练体系（日常训练内容、战术研究方法）。"
            ),
            "world_spec_instruction_en": (
                "World spec must include: core game rules (simplified but credible match mechanics), "
                "esports ecosystem (club system, transfer market, fan culture), "
                "training system (daily training content, tactical research methods)."
            ),
            "cast_spec_system_zh": (
                "你是电竞小说的角色设计专家。每个选手必须有独特的竞技风格、性格特征和成长弧线。"
                "团队是一个整体，角色设计必须考虑配合化学反应。"
            ),
            "cast_spec_system_en": (
                "You are a character designer for esports fiction. "
                "Each player must have a unique playstyle, personality, and growth arc. "
                "The team is a unit; character design must consider team chemistry."
            ),
            "cast_spec_instruction_zh": (
                "角色列表必须包含：每个队员的游戏位置、竞技风格和性格特征，"
                "队员之间的化学反应设计，以及对手战队核心选手的竞技特长和心理特征。"
            ),
            "cast_spec_instruction_en": (
                "Cast must include: each player's game role, playstyle, and personality; "
                "team chemistry design; and rival team core players' competitive strengths and psychological profiles."
            ),
            "volume_plan_system_zh": (
                "你是电竞小说的卷级规划师。每一卷围绕一个赛事阶段展开，"
                "必须有明确的赛事目标、团队挑战和竞技成长。"
            ),
            "volume_plan_system_en": (
                "You are a volume planner for esports fiction. "
                "Each volume revolves around a tournament phase with clear competitive goals, team challenges, and growth."
            ),
            "volume_plan_instruction_zh": (
                "每卷规划必须明确：赛事阶段（预选/小组/淘汰/决赛）、"
                "核心对手和关键比赛、团队面临的内外挑战、"
                "以及选手个人成长节点。"
            ),
            "volume_plan_instruction_en": (
                "Each volume must define: tournament phase (qualifiers/groups/elimination/finals), "
                "core opponents and key matches, internal/external team challenges, "
                "and individual player growth milestones."
            ),
            "outline_system_zh": (
                "你是电竞小说的章节大纲师。确保比赛章节和训练/日常章节节奏交替，"
                "比赛章节内必须有完整的赛事弧线。"
            ),
            "outline_system_en": (
                "You are a chapter outliner for esports fiction. "
                "Alternate match chapters with training/daily chapters; "
                "match chapters must have complete competitive arcs."
            ),
            "outline_instruction_zh": (
                "章节大纲必须遵守：比赛章节有完整的赛前-比赛-赛后弧线，"
                "训练章节要有可感知的进步或困难，"
                "每章结尾都有竞技悬念。"
            ),
            "outline_instruction_en": (
                "Outline must follow: match chapters have complete pre-match/match/post-match arcs, "
                "training chapters show perceivable progress or difficulty, "
                "every chapter ending has competitive suspense."
            ),
        },
        "judge_prompts": {
            "scene_review_system_zh": (
                "你是电竞小说的场景审稿专家。评估的核心是比赛节奏的紧凑性、战术博弈的可读性和团队化学反应的表现。"
            ),
            "scene_review_system_en": (
                "You are a scene reviewer for esports fiction. "
                "Core criteria: match-rhythm tightness, tactical game-play readability, and team chemistry portrayal."
            ),
            "scene_review_instruction_zh": (
                "请关注：比赛节奏是否紧凑？战术攻防是否清晰可读？"
                "选手情绪和团队互动是否鲜活？尾钩是否有竞技悬念？"
            ),
            "scene_review_instruction_en": (
                "Focus on: Is the match rhythm tight? Is tactical offense-defense clear and readable? "
                "Are player emotions and team dynamics vivid? Does the ending hook have competitive suspense?"
            ),
            "chapter_review_system_zh": (
                "你是电竞小说的章节审稿专家。章节评估关注赛事推进、团队成长和竞技悬念的维持。"
            ),
            "chapter_review_system_en": (
                "You are a chapter reviewer for esports fiction. "
                "Focus on tournament progression, team growth, and sustained competitive suspense."
            ),
            "chapter_review_instruction_zh": (
                "请关注：本章是否推进了赛事进程？团队关系是否有变化？"
                "竞技悬念是否维持？比赛描写是否有可读性？"
            ),
            "chapter_review_instruction_en": (
                "Focus on: Does the chapter advance the tournament? Do team dynamics evolve? "
                "Is competitive suspense maintained? Are match descriptions readable?"
            ),
            "scene_rewrite_system_zh": (
                "你是电竞小说的场景重写专家。重写时强化比赛的节奏感和战术可读性，"
                "让读者仿佛在看一场精彩的电竞直播。"
            ),
            "scene_rewrite_system_en": (
                "You are a scene rewrite specialist for esports fiction. "
                "Strengthen match rhythm and tactical readability; "
                "make readers feel they are watching an exciting esports broadcast."
            ),
            "scene_rewrite_instruction_zh": (
                "重写时补强：比赛节奏（攻防交替、高潮点明确）、"
                "战术可读性（让非玩家读者也能理解战局）、"
                "选手情绪（竞技场上的紧张、亢奋、不甘）。"
            ),
            "scene_rewrite_instruction_en": (
                "Prioritize: match rhythm (alternating offense-defense, clear climax points), "
                "tactical readability (even non-gamer readers understand the situation), "
                "player emotions (competitive tension, excitement, frustration)."
            ),
            "chapter_rewrite_system_zh": (
                "你是电竞小说的章节重写专家。确保章节的赛事弧线完整、团队化学反应鲜活、竞技悬念硬朗。"
            ),
            "chapter_rewrite_system_en": (
                "You are a chapter rewrite specialist for esports fiction. "
                "Ensure complete match arcs, vivid team chemistry, and hard competitive suspense."
            ),
            "chapter_rewrite_instruction_zh": (
                "重写时补强：赛事弧线完整性、团队互动的感染力、章末竞技悬念的牵引力。"
            ),
            "chapter_rewrite_instruction_en": (
                "Prioritize: match arc completeness, team interaction impact, "
                "chapter-end competitive suspense pull."
            ),
        },
    },
    # ------------------------------------------------------------------
    # FEMALE-GROWTH-NCP (female growth without romance)
    # ------------------------------------------------------------------
    "female-growth-ncp": {
        "name": "女频成长无CP / Female Growth No-CP",
        "description": (
            "以女性主角的自我成长和事业奋斗为核心，"
            "不依赖恋爱关系推动情节，强调主角的内心强大和独立价值。"
        ),
        "scene_weights": {
            "emotional_movement": 1.3,
            "conflict_clarity": 1.2,
            "dialogue": 1.1,
            "style": 1.1,
        },
        "chapter_weights": {
            "main_plot_progression": 1.2,
            "character_voice_distinction": 1.2,
            "thematic_resonance": 1.2,
            "pacing_rhythm": 1.1,
        },
        "scene_threshold_override": None,
        "chapter_threshold_override": None,
        "signal_keywords": {
            "conflict_terms_zh": [
                "打压", "排挤", "质疑", "偏见", "不公",
                "对抗", "证明", "突破", "逆境", "崛起",
            ],
            "conflict_terms_en": [
                "suppression", "exclusion", "doubt", "bias", "injustice",
                "resistance", "proof", "breakthrough", "adversity", "rise",
            ],
            "emotion_terms_zh": [
                "不甘", "坚定", "委屈", "释然", "觉醒",
                "骄傲", "孤独", "温暖", "愤怒", "平静",
            ],
            "emotion_terms_en": [
                "defiance", "resolve", "grievance", "letting go", "awakening",
                "pride", "loneliness", "warmth", "anger", "serenity",
            ],
            "hook_terms_zh": [
                "新机会", "背叛", "真相", "选择", "危机",
                "成长", "独立",
            ],
            "hook_terms_en": [
                "new opportunity", "betrayal", "truth", "choice", "crisis",
                "growth", "independence",
            ],
            "info_terms_zh": [
                "身世", "真相", "过去", "秘密", "能力", "价值",
            ],
            "info_terms_en": [
                "origin", "truth", "past", "secret", "ability", "worth",
            ],
        },
        "finding_messages": {
            "conflict_low_zh": (
                "成长冲突力度不足，缺少真正威胁到主角核心利益或自我认知的对抗事件。"
            ),
            "conflict_low_en": (
                "Growth conflict is too mild; events threatening the protagonist's core interests or self-identity are lacking."
            ),
            "conflict_clarity_low_zh": (
                "阻碍主角成长的核心矛盾不够清晰，读者看不出主角在对抗什么、需要克服什么。"
            ),
            "conflict_clarity_low_en": (
                "The core obstacle to growth is unclear; readers cannot see what the protagonist is fighting or must overcome."
            ),
            "emotion_low_zh": (
                "情感表达缺乏深度，主角的内心挣扎和成长痛感没有被充分演绎。"
            ),
            "emotion_low_en": (
                "Emotional expression lacks depth; the protagonist's inner struggle and growth pain are not fully dramatized."
            ),
            "emotional_movement_low_zh": (
                "主角在场景前后的心理状态没有明显变化，成长感不够可感知。"
            ),
            "emotional_movement_low_en": (
                "The protagonist's psychological state does not shift perceptibly; growth is not palpable."
            ),
            "dialogue_low_zh": (
                "对话缺少主角独特的性格和态度表达，人物声音辨识度不够。"
            ),
            "dialogue_low_en": (
                "Dialogue lacks the protagonist's unique personality and attitude; character voice is not distinctive enough."
            ),
            "hook_low_zh": (
                "场景尾部缺少驱动主角继续成长或面对新挑战的悬念和动力。"
            ),
            "hook_low_en": (
                "The scene ending lacks suspense or motivation driving the protagonist toward continued growth or new challenges."
            ),
            "payoff_low_zh": (
                "主角的成长成果不够可感知——她变强了、变独立了、获得了认可，读者应该清楚地看到。"
            ),
            "payoff_low_en": (
                "Growth payoff is not palpable — readers should clearly see that she has grown stronger, more independent, or gained recognition."
            ),
            "voice_low_zh": (
                "叙事声音缺乏主角独特的内心质感，语气过于中性，没有体现出角色的个性和态度。"
            ),
            "voice_low_en": (
                "Narrative voice lacks the protagonist's unique inner texture; "
                "tone is too neutral, not reflecting the character's personality and attitude."
            ),
            "contract_low_zh": (
                "当前场景没有兑现 scene contract 中承诺的成长节点或关键抉择。"
            ),
            "contract_low_en": (
                "The scene has not delivered the growth milestone or key choice promised by the scene contract."
            ),
        },
        "plan_rubric": {
            "required_checks": [
                "growth_arc_explicit",
                "independence_milestones",
            ],
            "min_antagonist_forces": 1,
            "require_power_system_tiers": False,
            "require_relationship_milestones": False,
            "require_clue_chain": False,
            "require_theme_per_volume": True,
            "min_key_reveals_per_volume": 1,
            "require_foreshadowing": True,
            "llm_evaluation_prompt_zh": (
                "请检查规划中是否包含清晰的主角成长弧线（从依附到独立的心理成长路径）、"
                "每个阶段的成长里程碑，以及主角面对的核心阻碍力量。"
            ),
            "llm_evaluation_prompt_en": (
                "Verify the plan includes a clear protagonist growth arc (psychological path from dependence to independence), "
                "milestones for each phase, and the core obstacles the protagonist faces."
            ),
        },
        "planner_prompts": {
            "book_spec_system_zh": (
                "你是一位专精女性成长类小说的策划总监。你关注的核心是主角的内心成长弧线、"
                "独立价值的逐步确立和不依赖恋爱关系的情节推进力。"
            ),
            "book_spec_system_en": (
                "You are a senior editor for female growth fiction. "
                "Your core focus is the protagonist's inner growth arc, gradual independence, "
                "and plot advancement that does not rely on romantic relationships."
            ),
            "book_spec_instruction_zh": (
                "书籍规格必须明确：主角的成长弧线（从什么起点到什么终点）、"
                "每个阶段的成长里程碑、核心阻碍力量的设计，"
                "以及主角独立价值如何被逐步证明和展示。"
            ),
            "book_spec_instruction_en": (
                "Book spec must define: protagonist growth arc (from what starting point to what destination), "
                "growth milestones per phase, core obstacle design, "
                "and how the protagonist's independent value is progressively proven."
            ),
            "world_spec_system_zh": (
                "你是女性成长小说的世界观设计师。世界设定的核心功能是为主角的成长制造阻碍和机会。"
            ),
            "world_spec_system_en": (
                "You are a worldbuilding specialist for female growth fiction. "
                "The world's core function is to create obstacles and opportunities for the protagonist's growth."
            ),
            "world_spec_instruction_zh": (
                "世界设定必须包含对主角成长构成阻碍的社会规则、权力结构或文化偏见，"
                "以及可以被主角利用来突破困境的资源和机会。"
            ),
            "world_spec_instruction_en": (
                "World spec must include social rules, power structures, or cultural biases that obstruct growth, "
                "and resources/opportunities the protagonist can leverage to break through."
            ),
            "cast_spec_system_zh": (
                "你是女性成长小说的角色设计专家。配角的设计必须服务于主角的成长——"
                "有人施加压力，有人提供支持，有人成为镜像对照。"
            ),
            "cast_spec_system_en": (
                "You are a character designer for female growth fiction. "
                "Supporting characters must serve the protagonist's growth — "
                "some apply pressure, some provide support, some serve as mirrors."
            ),
            "cast_spec_instruction_zh": (
                "角色列表必须包含：主角的核心性格和成长盲区、"
                "至少一个施加压力的对手、一个提供成长助力的角色、"
                "和一个反映主角另一种人生可能的镜像角色。"
            ),
            "cast_spec_instruction_en": (
                "Cast must include: protagonist's core personality and growth blind spots, "
                "at least one pressure-applying antagonist, one growth-supporting character, "
                "and one mirror character reflecting an alternate life path."
            ),
            "volume_plan_system_zh": (
                "你是女性成长小说的卷级规划师。每一卷围绕一个成长阶段展开。"
            ),
            "volume_plan_system_en": (
                "You are a volume planner for female growth fiction. Each volume revolves around a growth phase."
            ),
            "volume_plan_instruction_zh": (
                "每卷必须明确：成长目标、核心挑战、里程碑事件和成长回报。"
            ),
            "volume_plan_instruction_en": (
                "Each volume must define: growth goal, core challenge, milestone events, and growth payoff."
            ),
            "outline_system_zh": (
                "你是女性成长小说的章节大纲师。确保每一章都在成长维度上有推进。"
            ),
            "outline_system_en": (
                "You are a chapter outliner for female growth fiction. Ensure every chapter advances on the growth dimension."
            ),
            "outline_instruction_zh": (
                "每章至少有一个成长推进事件或内心觉醒时刻，"
                "每章结尾必须有驱动主角继续前行的悬念或动力。"
            ),
            "outline_instruction_en": (
                "Every chapter must have at least one growth event or inner awakening moment; "
                "every chapter ending needs suspense or motivation driving the protagonist forward."
            ),
        },
        "judge_prompts": {
            "scene_review_system_zh": (
                "你是女性成长小说的场景审稿专家。评估的核心是主角的内心成长是否可感知、"
                "角色声音是否有辨识度、成长冲突是否触及核心。"
            ),
            "scene_review_system_en": (
                "You are a scene reviewer for female growth fiction. "
                "Core criteria: perceivable inner growth, distinct character voice, growth conflict touching the core."
            ),
            "scene_review_instruction_zh": (
                "请关注：主角在场景中是否有可感知的心理变化？"
                "冲突是否触及了她的核心诉求？对话是否体现了她的独特声音？"
            ),
            "scene_review_instruction_en": (
                "Focus on: Does the protagonist have perceivable psychological change? "
                "Does conflict touch her core needs? Does dialogue reflect her unique voice?"
            ),
            "chapter_review_system_zh": (
                "你是女性成长小说的章节审稿专家。章节评估关注成长推进、角色声音和主题共鸣。"
            ),
            "chapter_review_system_en": (
                "You are a chapter reviewer for female growth fiction. "
                "Focus on growth progression, character voice, and thematic resonance."
            ),
            "chapter_review_instruction_zh": (
                "请关注：本章是否推进了主角的成长？主题是否有共鸣？角色声音是否鲜明？"
            ),
            "chapter_review_instruction_en": (
                "Focus on: Does the chapter advance growth? Does the theme resonate? Is the character voice distinct?"
            ),
            "scene_rewrite_system_zh": (
                "你是女性成长小说的场景重写专家。重写时强化主角的内心成长感和角色声音辨识度。"
            ),
            "scene_rewrite_system_en": (
                "You are a scene rewrite specialist for female growth fiction. "
                "Strengthen inner growth perception and character voice distinctiveness."
            ),
            "scene_rewrite_instruction_zh": (
                "重写时补强：主角的心理位移、对话的个性化、成长冲突的深度。"
            ),
            "scene_rewrite_instruction_en": (
                "Prioritize: protagonist psychological displacement, dialogue personalization, growth conflict depth."
            ),
            "chapter_rewrite_system_zh": (
                "你是女性成长小说的章节重写专家。确保章节有完整的成长弧线和有力的主题表达。"
            ),
            "chapter_rewrite_system_en": (
                "You are a chapter rewrite specialist for female growth fiction. "
                "Ensure complete growth arc and powerful thematic expression."
            ),
            "chapter_rewrite_instruction_zh": (
                "重写时补强：成长弧线的完整性、主题共鸣的力度、角色声音的辨识度。"
            ),
            "chapter_rewrite_instruction_en": (
                "Prioritize: growth arc completeness, thematic resonance strength, character voice distinctiveness."
            ),
        },
    },
    # ------------------------------------------------------------------
    # BASE-BUILDING
    # ------------------------------------------------------------------
    "base-building": {
        "name": "基建经营 / Base-Building",
        "description": (
            "以领地建设、资源经营和团队发展为核心的类型，"
            "包括末日基建和重生经商等强调经营成果和发展满足感的子类型。"
        ),
        "scene_weights": {
            "payoff_density": 1.3,
            "voice_consistency": 1.1,
        },
        "chapter_weights": {
            "main_plot_progression": 1.2,
            "volume_mission_alignment": 1.2,
            "pacing_rhythm": 1.1,
        },
        "scene_threshold_override": None,
        "chapter_threshold_override": None,
        "signal_keywords": {
            "conflict_terms_zh": [
                "资源争夺", "外敌", "内部矛盾", "物资短缺", "竞争",
                "扩张", "防御", "谈判", "贸易", "危机",
            ],
            "conflict_terms_en": [
                "resource contest", "external threat", "internal conflict", "shortage",
                "competition", "expansion", "defense", "negotiation", "trade", "crisis",
            ],
            "emotion_terms_zh": [
                "成就感", "满足", "焦虑", "期待", "自豪",
                "无奈", "振奋", "踏实", "责任", "温暖",
            ],
            "emotion_terms_en": [
                "achievement", "satisfaction", "anxiety", "anticipation", "pride",
                "helplessness", "elation", "groundedness", "responsibility", "warmth",
            ],
            "hook_terms_zh": [
                "新资源", "危机", "扩张机会", "技术突破",
                "新成员", "外敌来袭", "贸易路线",
            ],
            "hook_terms_en": [
                "new resource", "crisis", "expansion opportunity", "tech breakthrough",
                "new member", "enemy raid", "trade route",
            ],
            "info_terms_zh": [
                "资源", "产出", "建设", "规划", "人口", "技术",
            ],
            "info_terms_en": [
                "resource", "output", "construction", "planning", "population", "technology",
            ],
        },
        "finding_messages": {
            "conflict_low_zh": (
                "经营场景缺少足够的挑战和压力，基建过程过于顺利，缺少需要克服的障碍。"
            ),
            "conflict_low_en": (
                "Building scenes lack sufficient challenge; base-building proceeds too smoothly without obstacles to overcome."
            ),
            "conflict_clarity_low_zh": (
                "当前经营决策面临的核心问题不够清晰，读者看不出主角在做什么取舍。"
            ),
            "conflict_clarity_low_en": (
                "The core problem facing the current management decision is unclear; readers cannot see what trade-offs are being made."
            ),
            "emotion_low_zh": (
                "经营成果带来的成就感和满足感表达不足，读者没有分享到建设的乐趣和自豪。"
            ),
            "emotion_low_en": (
                "The achievement and satisfaction from management results are underexpressed; "
                "readers do not share the building joy and pride."
            ),
            "emotional_movement_low_zh": (
                "角色面对经营挑战和成果时的心态变化不明显，缺少从焦虑到成就的内心弧线。"
            ),
            "emotional_movement_low_en": (
                "Character mentality does not shift perceptibly through management challenges and achievements; "
                "the anxiety-to-accomplishment arc is missing."
            ),
            "dialogue_low_zh": (
                "团队讨论和决策对话缺乏功能性和趣味性，没有体现出不同角色的专业视角。"
            ),
            "dialogue_low_en": (
                "Team discussions and decision dialogues lack function and interest; "
                "different characters' professional perspectives are not reflected."
            ),
            "hook_low_zh": (
                "场景尾部缺少对下一阶段建设或新挑战的期待感和悬念。"
            ),
            "hook_low_en": (
                "Scene ending lacks anticipation or suspense for the next building phase or new challenge."
            ),
            "payoff_low_zh": (
                "建设成果的展示不够充分——数据变化、环境改善或团队壮大应该让读者清楚感受到。"
            ),
            "payoff_low_en": (
                "Building results are not adequately showcased — data changes, environment improvements, "
                "or team growth should be clearly perceivable."
            ),
            "voice_low_zh": (
                "叙事声音缺乏经营类作品特有的踏实感和生活气息。"
            ),
            "voice_low_en": (
                "Narrative voice lacks the groundedness and everyday atmosphere characteristic of base-building fiction."
            ),
            "contract_low_zh": (
                "当前场景没有兑现 scene contract 中承诺的经营节点或建设成果。"
            ),
            "contract_low_en": (
                "The scene has not delivered the management milestone or construction payoff promised by the scene contract."
            ),
        },
        "plan_rubric": {
            "required_checks": [
                "base_development_progression",
                "resource_challenge_design",
            ],
            "min_antagonist_forces": 1,
            "require_power_system_tiers": False,
            "require_relationship_milestones": False,
            "require_clue_chain": False,
            "require_theme_per_volume": True,
            "min_key_reveals_per_volume": 1,
            "require_foreshadowing": True,
            "llm_evaluation_prompt_zh": (
                "请检查规划中是否包含清晰的基地发展阶段（从简陋到完善的建设路径）、"
                "每个阶段的资源挑战和建设回报，以及至少一条外部威胁线。"
            ),
            "llm_evaluation_prompt_en": (
                "Verify the plan has clear base-development phases (path from rudimentary to complete), "
                "resource challenges and construction rewards per phase, "
                "and at least one external threat line."
            ),
        },
        "planner_prompts": {
            "book_spec_system_zh": (
                "你是一位专精基建经营类小说的策划总监。你熟悉末日基建和重生经商的读者期待——"
                "清晰的发展路线图、阶段性成果的满足感和持续的经营挑战。"
            ),
            "book_spec_system_en": (
                "You are a senior editor for base-building fiction. "
                "You understand reader expectations for apocalypse base-building and rebirth business — "
                "clear development roadmap, phased-achievement satisfaction, and ongoing management challenges."
            ),
            "book_spec_instruction_zh": (
                "书籍规格必须明确：基地发展路线图、每阶段的资源挑战和建设回报、"
                "外部威胁线的设计、以及经营决策中的取舍机制。"
            ),
            "book_spec_instruction_en": (
                "Book spec must define: base development roadmap, resource challenges and rewards per phase, "
                "external threat line design, and trade-off mechanisms in management decisions."
            ),
            "world_spec_system_zh": (
                "你是基建经营小说的世界观设计师。世界设定为经营提供舞台——"
                "资源分布、环境条件和外部势力共同构成经营的约束和机会。"
            ),
            "world_spec_system_en": (
                "You are a worldbuilding specialist for base-building fiction. "
                "The world provides the stage — resource distribution, environmental conditions, "
                "and external forces form constraints and opportunities."
            ),
            "world_spec_instruction_zh": (
                "世界设定必须包含：资源分布和获取方式、环境约束（气候/地形/灾害）、"
                "周边势力和贸易网络。"
            ),
            "world_spec_instruction_en": (
                "World spec must include: resource distribution and acquisition methods, "
                "environmental constraints (climate/terrain/disasters), "
                "neighboring factions and trade networks."
            ),
            "cast_spec_system_zh": (
                "你是基建经营小说的角色设计专家。团队成员是经营的核心资产，"
                "每个角色必须有独特的专业技能和性格。"
            ),
            "cast_spec_system_en": (
                "You are a character designer for base-building fiction. "
                "Team members are core assets; each must have unique expertise and personality."
            ),
            "cast_spec_instruction_zh": (
                "角色列表必须包含：每个核心成员的专业分工和性格特征、"
                "团队内部的合作与矛盾、以及外部势力的关键人物。"
            ),
            "cast_spec_instruction_en": (
                "Cast must include: each core member's specialization and personality, "
                "internal team cooperation and friction, and key external faction figures."
            ),
            "volume_plan_system_zh": (
                "你是基建经营小说的卷级规划师。每一卷围绕一个建设阶段展开。"
            ),
            "volume_plan_system_en": (
                "You are a volume planner for base-building fiction. Each volume revolves around a construction phase."
            ),
            "volume_plan_instruction_zh": (
                "每卷必须明确：建设阶段目标、资源挑战、核心经营事件和建设回报。"
            ),
            "volume_plan_instruction_en": (
                "Each volume must define: construction phase goal, resource challenges, "
                "core management events, and building payoff."
            ),
            "outline_system_zh": (
                "你是基建经营小说的章节大纲师。确保建设推进和冲突挑战交替出现。"
            ),
            "outline_system_en": (
                "You are a chapter outliner for base-building fiction. "
                "Ensure construction progress alternates with conflict challenges."
            ),
            "outline_instruction_zh": (
                "每章至少有一个建设成果展示或经营挑战事件，"
                "避免连续多章纯建设没有冲突。"
            ),
            "outline_instruction_en": (
                "Every chapter has at least one construction showcase or management challenge; "
                "avoid multiple consecutive chapters of pure building without conflict."
            ),
        },
        "judge_prompts": {
            "scene_review_system_zh": (
                "你是基建经营小说的场景审稿专家。评估的核心是建设成果的可感知度和经营决策的戏剧性。"
            ),
            "scene_review_system_en": (
                "You are a scene reviewer for base-building fiction. "
                "Core criteria: perceivable construction payoff and dramatic management decisions."
            ),
            "scene_review_instruction_zh": (
                "请关注：建设成果是否可感知？经营决策是否有取舍？场景是否有足够的挑战性？"
            ),
            "scene_review_instruction_en": (
                "Focus on: Is construction payoff perceivable? Do management decisions involve trade-offs? "
                "Is there sufficient challenge?"
            ),
            "chapter_review_system_zh": (
                "你是基建经营小说的章节审稿专家。章节评估关注建设推进和经营挑战的平衡。"
            ),
            "chapter_review_system_en": (
                "You are a chapter reviewer for base-building fiction. "
                "Focus on balance between construction progress and management challenges."
            ),
            "chapter_review_instruction_zh": (
                "请关注：建设推进是否清晰？挑战是否有分量？成果展示是否令人满足？"
            ),
            "chapter_review_instruction_en": (
                "Focus on: Is construction progress clear? Are challenges significant? "
                "Is the achievement showcase satisfying?"
            ),
            "scene_rewrite_system_zh": (
                "你是基建经营小说的场景重写专家。重写时强化建设成果的展示和经营决策的深度。"
            ),
            "scene_rewrite_system_en": (
                "You are a scene rewrite specialist for base-building fiction. "
                "Strengthen construction showcase and management decision depth."
            ),
            "scene_rewrite_instruction_zh": (
                "重写时补强：建设成果的可视化（数据、环境变化、团队反应）、"
                "经营决策的取舍感和挑战的压力感。"
            ),
            "scene_rewrite_instruction_en": (
                "Prioritize: construction result visualization (data, environmental changes, team reactions), "
                "trade-off perception in decisions, and challenge pressure."
            ),
            "chapter_rewrite_system_zh": (
                "你是基建经营小说的章节重写专家。确保章节的建设推进清晰、挑战有力、成果令人满足。"
            ),
            "chapter_rewrite_system_en": (
                "You are a chapter rewrite specialist for base-building fiction. "
                "Ensure clear construction progress, impactful challenges, and satisfying achievements."
            ),
            "chapter_rewrite_instruction_zh": (
                "重写时补强：建设阶段目标的清晰度、挑战的分量、成果回报的满足感。"
            ),
            "chapter_rewrite_instruction_en": (
                "Prioritize: construction phase goal clarity, challenge significance, "
                "achievement payoff satisfaction."
            ),
        },
    },
    # ------------------------------------------------------------------
    # EASTERN-AESTHETIC
    # ------------------------------------------------------------------
    "eastern-aesthetic": {
        "name": "东方美学 / Eastern-Aesthetic",
        "description": (
            "以东方美学意境和诗意叙事为核心的类型，"
            "强调文字质感、意境营造和文化底蕴。"
        ),
        "scene_weights": {
            "style": 1.3,
            "emotion": 1.2,
            "voice_consistency": 1.2,
            "hook": 0.8,
        },
        "chapter_weights": {
            "thematic_resonance": 1.3,
            "character_voice_distinction": 1.2,
            "style": 1.2,
            "pacing_rhythm": 1.1,
        },
        "scene_threshold_override": None,
        "chapter_threshold_override": None,
        "signal_keywords": {
            "conflict_terms_zh": [
                "道心", "因果", "天劫", "情劫", "宿命",
                "执念", "放下", "选择", "牺牲", "守护",
            ],
            "conflict_terms_en": [
                "dao heart", "karma", "tribulation", "love tribulation", "fate",
                "obsession", "letting go", "choice", "sacrifice", "protection",
            ],
            "emotion_terms_zh": [
                "寂寥", "超然", "悲悯", "淡然", "怅然",
                "空灵", "沉静", "温润", "萧索", "清冷",
            ],
            "emotion_terms_en": [
                "solitude", "transcendence", "compassion", "serenity", "melancholy",
                "ethereal", "stillness", "gentle warmth", "desolation", "cool detachment",
            ],
            "hook_terms_zh": [
                "异象", "天机", "劫难", "觉醒", "蜕变",
                "宿敌", "预言",
            ],
            "hook_terms_en": [
                "omen", "heavenly secret", "calamity", "awakening", "metamorphosis",
                "fated enemy", "prophecy",
            ],
            "info_terms_zh": [
                "道", "缘", "因果", "天地", "法则", "禅意",
            ],
            "info_terms_en": [
                "dao", "destiny", "karma", "heaven and earth", "law", "zen",
            ],
        },
        "finding_messages": {
            "conflict_low_zh": (
                "冲突缺乏东方美学特有的内在张力——道心之争、因果纠缠或情与理的抉择。"
            ),
            "conflict_low_en": (
                "Conflict lacks the inner tension unique to eastern aesthetics — "
                "dao-heart struggle, karmic entanglement, or the dilemma between emotion and reason."
            ),
            "conflict_clarity_low_zh": (
                "角色面临的核心抉择不够清晰，读者看不出这个选择的分量和代价。"
            ),
            "conflict_clarity_low_en": (
                "The core dilemma is unclear; readers cannot perceive the weight and cost of the choice."
            ),
            "emotion_low_zh": (
                "情感表达缺少东方美学特有的含蓄和意境，过于直白或过于西方化。"
            ),
            "emotion_low_en": (
                "Emotional expression lacks the subtlety and mood characteristic of eastern aesthetics; "
                "it is too blunt or too Westernized."
            ),
            "emotional_movement_low_zh": (
                "角色的内心变化缺少诗意的层次感，从一种心境到另一种心境的转变不够自然流畅。"
            ),
            "emotional_movement_low_en": (
                "The character's inner shift lacks poetic layering; "
                "the transition from one mental state to another is not fluid."
            ),
            "dialogue_low_zh": (
                "对话缺少文言美感和哲思意味，人物的言语没有体现出文化修养和个性。"
            ),
            "dialogue_low_en": (
                "Dialogue lacks classical elegance and philosophical undertones; "
                "characters' speech does not reflect cultural cultivation."
            ),
            "hook_low_zh": (
                "场景尾部缺少东方叙事特有的余韵和留白感，结束得过于仓促或刻意。"
            ),
            "hook_low_en": (
                "The scene ending lacks the lingering resonance and space-for-imagination "
                "characteristic of eastern narrative; it ends too abruptly or artificially."
            ),
            "payoff_low_zh": (
                "场景的美学回报不足——读者没有在文字中感受到意境之美、哲思之深或文化之厚。"
            ),
            "payoff_low_en": (
                "Aesthetic payoff is insufficient — readers do not feel beauty of mood, "
                "depth of philosophy, or cultural richness in the prose."
            ),
            "voice_low_zh": (
                "叙事语言缺乏东方美学的韵味，语感过于现代白话或过于生硬，"
                "没有形成独特的文学质感。"
            ),
            "voice_low_en": (
                "Narrative language lacks eastern aesthetic cadence; "
                "it is too modern-colloquial or too stiff, without forming a unique literary texture."
            ),
            "contract_low_zh": (
                "当前场景没有兑现 scene contract 中承诺的意境营造或哲思表达。"
            ),
            "contract_low_en": (
                "The scene has not delivered the mood-building or philosophical expression promised by the scene contract."
            ),
        },
        "plan_rubric": {
            "required_checks": [
                "aesthetic_theme_consistency",
                "philosophical_depth_check",
            ],
            "min_antagonist_forces": 1,
            "require_power_system_tiers": False,
            "require_relationship_milestones": False,
            "require_clue_chain": False,
            "require_theme_per_volume": True,
            "min_key_reveals_per_volume": 1,
            "require_foreshadowing": True,
            "llm_evaluation_prompt_zh": (
                "请检查规划中是否包含统一的美学主题（意象系统、色调基调、哲学内核），"
                "以及每一卷的美学风格是否在统一框架内有所递进和变化。"
            ),
            "llm_evaluation_prompt_en": (
                "Verify the plan has a unified aesthetic theme (image system, tonal palette, philosophical core), "
                "and whether each volume's aesthetic style progresses and varies within the unified framework."
            ),
        },
        "planner_prompts": {
            "book_spec_system_zh": (
                "你是一位专精东方美学幻想小说的策划总监。你重视文字质感、意境营造和哲思深度。"
                "你的核心职责是确保书籍规格中包含统一的美学框架、诗意的叙事风格和深厚的文化底蕴。"
            ),
            "book_spec_system_en": (
                "You are a senior editor for eastern-aesthetic fantasy fiction. "
                "You value prose quality, mood creation, and philosophical depth. "
                "Your core duty is to ensure the book spec has a unified aesthetic framework, "
                "poetic narrative style, and deep cultural foundation."
            ),
            "book_spec_instruction_zh": (
                "书籍规格必须明确：美学框架（核心意象系统、色调基调、文化底蕴来源）、"
                "哲学内核（探讨什么命题？道与情？天命与自由？因果与抉择？）、"
                "叙事风格定位（偏古典还是偏现代？偏含蓄还是偏热烈？）。"
            ),
            "book_spec_instruction_en": (
                "Book spec must define: aesthetic framework (core image system, tonal palette, cultural sources), "
                "philosophical core (what questions? dao vs. emotion? fate vs. freedom? karma vs. choice?), "
                "narrative style positioning (classical vs. modern? subtle vs. passionate?)."
            ),
            "world_spec_system_zh": (
                "你是东方美学小说的世界观设计师。世界设定不只是地理和规则，更是意境的载体——"
                "山水即心境，四季即人生，天地即哲理。"
            ),
            "world_spec_system_en": (
                "You are a worldbuilding specialist for eastern-aesthetic fiction. "
                "The world is not just geography and rules — it is a vessel for mood: "
                "landscape mirrors mind, seasons mirror life, heaven and earth mirror philosophy."
            ),
            "world_spec_instruction_zh": (
                "世界设定必须包含：自然景观的意象意义、季节变化与情节节奏的对应关系、"
                "文化体系的美学特征（礼仪、服饰、饮食、建筑的风格统一性）。"
            ),
            "world_spec_instruction_en": (
                "World spec must include: symbolic meaning of natural landscapes, "
                "seasonal changes corresponding to plot rhythm, "
                "cultural system aesthetics (unified style in etiquette, clothing, cuisine, architecture)."
            ),
            "cast_spec_system_zh": (
                "你是东方美学小说的角色设计专家。角色是美学的化身——"
                "他们的言行举止、气质修养和内心世界都应该体现东方文化的韵味。"
            ),
            "cast_spec_system_en": (
                "You are a character designer for eastern-aesthetic fiction. "
                "Characters embody aesthetics — their speech, bearing, temperament, "
                "and inner world must all reflect eastern cultural grace."
            ),
            "cast_spec_instruction_zh": (
                "角色列表必须包含：每个角色的美学气质定位、"
                "内心的哲学命题或情感执念、以及与整体美学主题的呼应关系。"
            ),
            "cast_spec_instruction_en": (
                "Cast must include: each character's aesthetic temperament, "
                "inner philosophical question or emotional obsession, "
                "and resonance with the overall aesthetic theme."
            ),
            "volume_plan_system_zh": (
                "你是东方美学小说的卷级规划师。每一卷围绕一个美学主题和哲学命题展开。"
            ),
            "volume_plan_system_en": (
                "You are a volume planner for eastern-aesthetic fiction. "
                "Each volume revolves around an aesthetic theme and philosophical question."
            ),
            "volume_plan_instruction_zh": (
                "每卷必须明确：美学主题（核心意象和色调）、哲学命题（探讨什么问题）、"
                "情感弧线（从什么心境到什么心境）和风格基调。"
            ),
            "volume_plan_instruction_en": (
                "Each volume must define: aesthetic theme (core imagery and palette), "
                "philosophical question, emotional arc (from what mood to what mood), and style tone."
            ),
            "outline_system_zh": (
                "你是东方美学小说的章节大纲师。确保每章的文字质感和意境营造不低于情节推进的优先级。"
            ),
            "outline_system_en": (
                "You are a chapter outliner for eastern-aesthetic fiction. "
                "Ensure prose quality and mood-building are prioritized alongside plot progression."
            ),
            "outline_instruction_zh": (
                "每章必须有明确的意境目标（营造什么氛围和美感）、"
                "哲思锚点（本章在整体哲学命题上推进了什么）、"
                "以及场景的美学节奏设计（张弛交替，留有呼吸空间）。"
            ),
            "outline_instruction_en": (
                "Every chapter must have: a clear mood target (what atmosphere and beauty to create), "
                "philosophical anchor (what the chapter advances in the overall philosophical question), "
                "and aesthetic rhythm design (alternating intensity with breathing space)."
            ),
        },
        "judge_prompts": {
            "scene_review_system_zh": (
                "你是东方美学小说的场景审稿专家。评估的核心是文字质感、意境营造和哲思深度。"
                "好的东方美学场景让读者感受到诗意之美，而不仅仅是故事推进。"
            ),
            "scene_review_system_en": (
                "You are a scene reviewer for eastern-aesthetic fiction. "
                "Core criteria: prose quality, mood creation, and philosophical depth. "
                "A good eastern-aesthetic scene lets readers feel poetic beauty, not just plot advancement."
            ),
            "scene_review_instruction_zh": (
                "请关注：文字质感是否有东方美学的韵味？意境营造是否成功？"
                "哲思表达是否自然融入叙事？角色的言行是否体现文化修养？"
            ),
            "scene_review_instruction_en": (
                "Focus on: Does prose have eastern-aesthetic cadence? Is mood-building successful? "
                "Is philosophical expression naturally woven into narrative? "
                "Do character actions reflect cultural cultivation?"
            ),
            "chapter_review_system_zh": (
                "你是东方美学小说的章节审稿专家。章节评估关注美学统一性、意境连贯性和哲思深度。"
            ),
            "chapter_review_system_en": (
                "You are a chapter reviewer for eastern-aesthetic fiction. "
                "Focus on aesthetic unity, mood coherence, and philosophical depth."
            ),
            "chapter_review_instruction_zh": (
                "请关注：本章的美学风格是否统一？意境是否有层次感和连贯性？"
                "哲思命题是否有推进？文字质感是否保持稳定？"
            ),
            "chapter_review_instruction_en": (
                "Focus on: Is the aesthetic style unified? Does mood have layering and coherence? "
                "Does the philosophical theme advance? Is prose quality stable?"
            ),
            "scene_rewrite_system_zh": (
                "你是东方美学小说的场景重写专家。重写时必须提升文字的诗意质感和意境的营造力度，"
                "同时保持哲思表达的自然流畅。"
            ),
            "scene_rewrite_system_en": (
                "You are a scene rewrite specialist for eastern-aesthetic fiction. "
                "Elevate poetic prose quality and mood-building intensity "
                "while keeping philosophical expression naturally fluid."
            ),
            "scene_rewrite_instruction_zh": (
                "重写时补强：文字的诗意质感（用意象、通感和节奏变化替代平铺直叙）、"
                "意境营造（通过环境、光影、声音营造氛围）、"
                "哲思融入（让哲理通过角色行动和选择自然体现，而非说教）。"
            ),
            "scene_rewrite_instruction_en": (
                "Prioritize: poetic prose quality (replace flat narration with imagery, synesthesia, rhythm shifts), "
                "mood-building (use environment, light, sound to create atmosphere), "
                "philosophical integration (express philosophy through action and choice, not preaching)."
            ),
            "chapter_rewrite_system_zh": (
                "你是东方美学小说的章节重写专家。确保章节美学风格统一、意境有层次、哲思有推进。"
            ),
            "chapter_rewrite_system_en": (
                "You are a chapter rewrite specialist for eastern-aesthetic fiction. "
                "Ensure unified aesthetic style, layered mood, and advancing philosophy."
            ),
            "chapter_rewrite_instruction_zh": (
                "重写时补强：美学风格的统一性、意境的层次感和连贯性、"
                "哲思命题在叙事中的自然渗透。"
            ),
            "chapter_rewrite_instruction_en": (
                "Prioritize: aesthetic style unity, mood layering and coherence, "
                "natural philosophical permeation within narrative."
            ),
        },
    },
}


# ---------------------------------------------------------------------------
# Fuzzy keyword map for genre name -> category fallback
# ---------------------------------------------------------------------------

_GENRE_NAME_KEYWORD_MAP: dict[str, str] = {
    # action-progression
    "仙": "action-progression",
    "修仙": "action-progression",
    "末日": "action-progression",
    "异能": "action-progression",
    "升级": "action-progression",
    "litrpg": "action-progression",
    "progression": "action-progression",
    "xianxia": "action-progression",
    "cultivation": "action-progression",
    "apocalypse": "action-progression",
    # relationship-driven
    "言情": "relationship-driven",
    "浪漫": "relationship-driven",
    "宫斗": "relationship-driven",
    "romance": "relationship-driven",
    "romantasy": "relationship-driven",
    "恋爱": "relationship-driven",
    "甜宠": "relationship-driven",
    "虐恋": "relationship-driven",
    "dark romance": "relationship-driven",
    # suspense-mystery
    "悬疑": "suspense-mystery",
    "推理": "suspense-mystery",
    "侦探": "suspense-mystery",
    "怪谈": "suspense-mystery",
    "thriller": "suspense-mystery",
    "mystery": "suspense-mystery",
    "suspense": "suspense-mystery",
    "恐怖": "suspense-mystery",
    "无限流": "suspense-mystery",
    # strategy-worldbuilding
    "争霸": "strategy-worldbuilding",
    "战争": "strategy-worldbuilding",
    "历史": "strategy-worldbuilding",
    "科技": "strategy-worldbuilding",
    "strategy": "strategy-worldbuilding",
    "epic fantasy": "strategy-worldbuilding",
    "space opera": "strategy-worldbuilding",
    # esports-competition
    "电竞": "esports-competition",
    "esports": "esports-competition",
    "竞技": "esports-competition",
    # female-growth-ncp
    "女性成长": "female-growth-ncp",
    "女强": "female-growth-ncp",
    "大女主": "female-growth-ncp",
    # base-building
    "基建": "base-building",
    "经营": "base-building",
    "种田": "base-building",
    "base-building": "base-building",
    # eastern-aesthetic
    "东方美学": "eastern-aesthetic",
    "古风": "eastern-aesthetic",
    "仙侠美学": "eastern-aesthetic",
    "eastern aesthetic": "eastern-aesthetic",
}


# ---------------------------------------------------------------------------
# Profile loader (cached)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_genre_review_profiles() -> dict[str, GenreReviewProfile]:
    """Build and cache all genre review profiles from raw data.

    Returns a mapping of ``category_key`` -> ``GenreReviewProfile``.
    """
    profiles: dict[str, GenreReviewProfile] = {}
    for category_key, raw in _GENRE_REVIEW_PROFILES.items():
        profiles[category_key] = GenreReviewProfile(
            category_key=category_key,
            name=raw.get("name", category_key),
            description=raw.get("description", ""),
            scene_weights=GenreReviewWeights(**raw.get("scene_weights", {})),
            chapter_weights=GenreChapterReviewWeights(
                **raw.get("chapter_weights", {}),
            ),
            scene_threshold_override=raw.get("scene_threshold_override"),
            chapter_threshold_override=raw.get("chapter_threshold_override"),
            signal_keywords=GenreSignalKeywords(
                **raw.get("signal_keywords", {}),
            ),
            finding_messages=GenreFindingMessages(
                **raw.get("finding_messages", {}),
            ),
            plan_rubric=GenrePlanRubric(**raw.get("plan_rubric", {})),
            planner_prompts=GenrePlannerPrompts(
                **raw.get("planner_prompts", {}),
            ),
            judge_prompts=GenreJudgePrompts(**raw.get("judge_prompts", {})),
        )
    return profiles


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


def resolve_genre_review_profile(
    genre: str,
    sub_genre: str | None = None,
    genre_preset_key: str | None = None,
    story_facets: object | None = None,
) -> GenreReviewProfile:
    """Resolve the best-matching genre review profile.

    Resolution strategy (first match wins):
    0. If *story_facets* is provided, use the Facet Review Blender for
       dynamic weight mixing (new multi-dimensional path).
    1. If *genre_preset_key* is provided, look it up in
       ``_GENRE_TO_CATEGORY_MAP``.
    2. Otherwise attempt ``infer_genre_preset()`` from the writing presets
       module and map the resulting key.
    3. Fall back to fuzzy keyword matching against the *genre* (and
       *sub_genre*) strings.
    4. Return the ``"default"`` profile if nothing matches.
    """
    # --- strategy 0: facet-based dynamic blending ---
    if story_facets is not None:
        try:
            from bestseller.domain.facets import StoryFacets
            from bestseller.services.facet_review_blender import build_facet_review_profile

            if isinstance(story_facets, StoryFacets):
                return build_facet_review_profile(story_facets)
            elif isinstance(story_facets, dict):
                facets = StoryFacets(**story_facets)
                return build_facet_review_profile(facets)
        except Exception:
            logger.debug("Facet review blender failed; falling through to legacy path.", exc_info=True)
    profiles = load_genre_review_profiles()

    # --- strategy 1: direct preset key lookup ---
    if genre_preset_key:
        category = _GENRE_TO_CATEGORY_MAP.get(genre_preset_key)
        if category and category in profiles:
            return profiles[category]

    # --- strategy 2: infer via writing_presets ---
    try:
        from bestseller.services.writing_presets import infer_genre_preset

        inferred = infer_genre_preset(genre, sub_genre)
        if inferred is not None:
            category = _GENRE_TO_CATEGORY_MAP.get(inferred.key)
            if category and category in profiles:
                return profiles[category]
    except Exception:
        logger.debug("Could not infer genre preset; falling through to keyword match.")

    # --- strategy 3: fuzzy keyword match ---
    haystack = " ".join(part for part in [genre, sub_genre] if part).lower()
    for keyword, category in _GENRE_NAME_KEYWORD_MAP.items():
        if keyword in haystack and category in profiles:
            return profiles[category]

    # --- strategy 4: default ---
    return profiles["default"]


# ---------------------------------------------------------------------------
# Phase A2 — Numeric threshold accessor.
#
# The prose profile above carries scoring weights + keywords + prompt
# overrides. Phase B/C/D also need numeric thresholds (hook baseline,
# strand max-gap, debt multiplier, etc.) that are centralized in
# ``genre_profile_thresholds``. This thin accessor lets callers resolve
# both layers from the same entrypoint if they want.
# ---------------------------------------------------------------------------


def load_thresholds(genre_id: str | None):  # type: ignore[no-untyped-def]
    """Return the ``GenreProfileThresholds`` for a category key.

    This is a re-export so callers can keep ``from
    bestseller.services.genre_review_profiles import load_thresholds`` as
    their single import point. Implementation lives in
    ``genre_profile_thresholds`` to keep this mega-file from ballooning
    further.
    """

    from bestseller.services.genre_profile_thresholds import resolve_thresholds

    return resolve_thresholds(genre_id)
