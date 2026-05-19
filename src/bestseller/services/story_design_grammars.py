"""Category-specific story design grammars.

Prompt packs describe writing method and tone.  Story design grammars describe
what kind of state must change for each category so books do not collapse into
one shared missing-parent/revenge skeleton.
"""

from __future__ import annotations

from functools import lru_cache
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
import yaml

logger = logging.getLogger(__name__)

DEFAULT_MACRO_STRUCTURE_OPTIONS = [
    "lotus_mainline",
    "progressive_staircase",
    "parallel_unit",
    "hybrid",
]
DEFAULT_READER_DESIRE_TYPES = [
    "survival_safety",
    "gain_greed",
    "belonging_love",
    "respect_value",
    "self_realization",
    "curiosity",
    "control",
]
DEFAULT_CONFLICT_EVENT_TYPES = [
    "emotion_event",
    "desire_lock",
    "obstacle_escalation",
    "method_search",
    "execution_turn",
    "payoff_feedback",
]
DEFAULT_OBSTACLE_TYPES = [
    "resource_limit",
    "opponent_pressure",
    "rule_cost",
    "relationship_cost",
    "information_block",
]
DEFAULT_INFORMATION_GAP_MODES = [
    "reader_knows_less",
    "reader_knows_equal",
    "reader_knows_more",
    "protagonist_knows_less",
    "others_hide_truth",
]
DEFAULT_EVENT_CYCLE_ROLES = [
    "trigger",
    "desire_lock",
    "obstacle_escalation",
    "method_search",
    "execution_turn",
    "payoff_feedback",
    "reaction_reset",
    "bridge_hook",
]


class StoryDesignGrammar(BaseModel, frozen=True):
    """A category-specific plot grammar with prompt-ready constraints."""

    model_config = ConfigDict(extra="ignore")

    key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    applies_to_categories: list[str] = Field(default_factory=list)
    required_contracts: list[str] = Field(default_factory=list)
    state_variables: list[str] = Field(default_factory=list)
    chapter_change_vectors: list[str] = Field(default_factory=list)
    reader_rewards: list[str] = Field(default_factory=list)
    hook_or_aftereffect_types: list[str] = Field(default_factory=list)
    macro_structure_options: list[str] = Field(
        default_factory=lambda: list(DEFAULT_MACRO_STRUCTURE_OPTIONS)
    )
    reader_desire_types: list[str] = Field(
        default_factory=lambda: list(DEFAULT_READER_DESIRE_TYPES)
    )
    conflict_event_types: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CONFLICT_EVENT_TYPES)
    )
    obstacle_types: list[str] = Field(default_factory=lambda: list(DEFAULT_OBSTACLE_TYPES))
    information_gap_modes: list[str] = Field(
        default_factory=lambda: list(DEFAULT_INFORMATION_GAP_MODES)
    )
    event_cycle_roles: list[str] = Field(default_factory=lambda: list(DEFAULT_EVENT_CYCLE_ROLES))
    forbidden_defaults: list[str] = Field(default_factory=list)


def _story_design_grammar_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "story_design_grammars"


@lru_cache(maxsize=1)
def load_story_design_grammar_registry() -> dict[str, StoryDesignGrammar]:
    """Load all grammar YAML files from ``config/story_design_grammars``."""

    registry: dict[str, StoryDesignGrammar] = {}
    grammar_dir = _story_design_grammar_dir()
    if not grammar_dir.exists():
        return registry
    for path in sorted(grammar_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                continue
            grammar = StoryDesignGrammar.model_validate(raw)
            registry[grammar.key] = grammar
        except Exception:
            logger.warning("Failed to load story design grammar from %s", path, exc_info=True)
    return registry


def list_story_design_grammars() -> list[StoryDesignGrammar]:
    return list(load_story_design_grammar_registry().values())


def get_story_design_grammar(key: str | None) -> StoryDesignGrammar | None:
    if not key:
        return None
    return load_story_design_grammar_registry().get(key)


def resolve_story_design_grammar(
    category_key: str | None = None,
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> StoryDesignGrammar:
    """Resolve the best grammar by explicit category key or genre text."""

    registry = load_story_design_grammar_registry()
    if not registry:
        return _empty_default()
    if category_key and category_key in registry:
        return registry[category_key]

    inferred = _infer_category_key(
        genre=genre,
        sub_genre=_sub_genre_or_empty(sub_genre),
        metadata=metadata,
    )
    if inferred and inferred in registry:
        return registry[inferred]
    return registry.get("default", _empty_default())


def _sub_genre_or_empty(sub_genre: str | None) -> str:
    return sub_genre or ""


def render_story_design_grammar_prompt_block(grammar: StoryDesignGrammar | None) -> str:
    """Render category grammar into a compact prompt block."""

    if grammar is None:
        return ""
    lines = [
        f"## Story Design Grammar: {grammar.name} ({grammar.key})",
        f"- Required contracts: {', '.join(grammar.required_contracts)}",
        f"- State variables: {', '.join(grammar.state_variables)}",
        f"- Chapter change vectors: {', '.join(grammar.chapter_change_vectors)}",
        f"- Reader rewards: {', '.join(grammar.reader_rewards)}",
        f"- Hook/aftereffect types: {', '.join(grammar.hook_or_aftereffect_types)}",
        f"- Macro structure options: {', '.join(grammar.macro_structure_options)}",
        f"- Reader desire types: {', '.join(grammar.reader_desire_types)}",
        f"- Conflict event types: {', '.join(grammar.conflict_event_types)}",
        f"- Obstacle types: {', '.join(grammar.obstacle_types)}",
        f"- Information gap modes: {', '.join(grammar.information_gap_modes)}",
        f"- Event cycle roles: {', '.join(grammar.event_cycle_roles)}",
        f"- Forbidden defaults: {', '.join(grammar.forbidden_defaults)}",
    ]
    return "\n".join(line for line in lines if not line.endswith(": "))


def _infer_category_key(
    *,
    genre: str | None,
    sub_genre: str = "",
    metadata: dict[str, Any] | None = None,
) -> str | None:
    haystack = " ".join(
        [
            _text(genre),
            _text(sub_genre),
            *_flatten_metadata_text(metadata or {}),
        ]
    ).lower()
    for keyword, key in _GENRE_NAME_KEYWORD_MAP.items():
        if keyword in haystack:
            return key
    return None


def _empty_default() -> StoryDesignGrammar:
    return StoryDesignGrammar(
        key="default",
        name="通用剧情语法",
        state_variables=["目标", "压力", "代价", "选择", "结果"],
        chapter_change_vectors=["目标推进", "压力升级", "代价显化", "关系变化", "信息变化"],
        reader_rewards=["短兑现", "新悬念", "角色选择", "局势变化", "情绪余波"],
        macro_structure_options=[
            "lotus_mainline",
            "progressive_staircase",
            "parallel_unit",
            "hybrid",
        ],
        reader_desire_types=[
            "survival_safety",
            "gain_greed",
            "belonging_love",
            "respect_value",
            "self_realization",
            "curiosity",
            "control",
        ],
        conflict_event_types=[
            "emotion_event",
            "desire_lock",
            "obstacle_escalation",
            "method_search",
            "execution_turn",
            "payoff_feedback",
        ],
        obstacle_types=["resource_limit", "opponent_pressure", "rule_cost", "relationship_cost"],
        information_gap_modes=[
            "reader_knows_less",
            "reader_knows_equal",
            "reader_knows_more",
            "protagonist_knows_less",
            "others_hide_truth",
        ],
        event_cycle_roles=[
            "trigger",
            "desire_lock",
            "obstacle_escalation",
            "method_search",
            "execution_turn",
            "payoff_feedback",
            "reaction_reset",
            "bridge_hook",
        ],
        forbidden_defaults=["家庭创伤或身世旧案作为默认动机", "天降外挂", "退婚开局", "神秘老人", "反派无因作恶"],
    )


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _flatten_metadata_text(value: object) -> list[str]:
    if isinstance(value, dict):
        parts: list[str] = []
        for item in value.values():
            parts.extend(_flatten_metadata_text(item))
        return parts
    if isinstance(value, list | tuple | set):
        parts = []
        for item in value:
            parts.extend(_flatten_metadata_text(item))
        return parts
    text = _text(value)
    return [text] if text else []


_GENRE_NAME_KEYWORD_MAP: dict[str, str] = {
    "异界": "otherworld-cross-system",
    "otherworld": "otherworld-cross-system",
    "cross-system": "otherworld-cross-system",
    "仙": "action-progression",
    "修仙": "action-progression",
    "玄幻": "action-progression",
    "升级": "action-progression",
    "litrpg": "action-progression",
    "progression": "action-progression",
    "种田": "base-building",
    "基建": "base-building",
    "经营": "base-building",
    "东方美学": "eastern-aesthetic",
    "国风": "eastern-aesthetic",
    "水墨": "eastern-aesthetic",
    "电竞": "esports-competition",
    "游戏": "esports-competition",
    "esport": "esports-competition",
    "大女主": "female-growth-ncp",
    "女帝": "female-growth-ncp",
    "无cp": "female-growth-ncp",
    "无CP": "female-growth-ncp",
    "言情": "relationship-driven",
    "恋爱": "relationship-driven",
    "romance": "relationship-driven",
    "relationship": "relationship-driven",
    "悬疑": "suspense-mystery",
    "推理": "suspense-mystery",
    "探案": "suspense-mystery",
    "mystery": "suspense-mystery",
    "thriller": "suspense-mystery",
    "权谋": "strategy-worldbuilding",
    "历史": "strategy-worldbuilding",
    "争霸": "strategy-worldbuilding",
    "strategy": "strategy-worldbuilding",
    "都市": "urban-contemporary",
    "职场": "urban-contemporary",
    "娱乐圈": "urban-contemporary",
    "现实题材": "urban-contemporary",
    "urban": "urban-contemporary",
    "workplace": "urban-contemporary",
    "科幻": "science-fiction-progression",
    "机甲": "science-fiction-progression",
    "星际": "science-fiction-progression",
    "黑科技": "science-fiction-progression",
    "scifi": "science-fiction-progression",
    "sci-fi": "science-fiction-progression",
    "mecha": "science-fiction-progression",
    "武侠": "wuxia-jianghu",
    "江湖": "wuxia-jianghu",
    "门派": "wuxia-jianghu",
    "侠义": "wuxia-jianghu",
    "wuxia": "wuxia-jianghu",
    "jianghu": "wuxia-jianghu",
}
