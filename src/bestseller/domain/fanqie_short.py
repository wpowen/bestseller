"""番茄短故事（Fanqie short story）领域契约与校验。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from pydantic import BaseModel, Field

from bestseller.domain.enums import ProjectType

if TYPE_CHECKING:
    from bestseller.domain.project import ProjectCreate

FANQIE_SHORT_CONTENT_MODE = "fanqie_short_story"
FANQIE_SHORT_PLATFORM_KEY = "tomato_short"
DEFAULT_UNLOCK_LINE_RATIO = 0.30
DEFAULT_SIGNING_TARGET_UNLOCKS = 1000
MIN_TOTAL_WORDS = 5_000
MAX_TOTAL_WORDS = 30_000
MIN_SEGMENT_COUNT = 4
MAX_SEGMENT_COUNT = 8

LENGTH_PRESET_SPECS: dict[str, dict[str, int]] = {
    "fanqie-short-8k": {"target_words": 8_000, "segment_count": 4},
    "fanqie-short-15k": {"target_words": 15_000, "segment_count": 6},
    "fanqie-short-25k": {"target_words": 25_000, "segment_count": 8},
}

DEFAULT_LENGTH_KEY = "fanqie-short-15k"

SCENE_MIN_SCORE = 0.78
SCENE_HOOK_MIN_SCORE = 0.80
SCENE_PAYOFF_MIN_SCORE = 0.80


class FanqieShortBeat(BaseModel):
    segment_number: int = Field(ge=1)
    beat_role: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=2000)
    payoff: str = Field(default="", max_length=2000)
    emotional_turn: str = Field(default="", max_length=1000)
    opening_contract: dict[str, Any] = Field(default_factory=dict)
    unlock_contract: dict[str, Any] = Field(default_factory=dict)
    ability_cost_contract: dict[str, Any] = Field(default_factory=dict)
    payoff_contract: dict[str, Any] = Field(default_factory=dict)
    closure_contract: dict[str, Any] = Field(default_factory=dict)
    continuity_contract: dict[str, Any] = Field(default_factory=dict)


class FanqieShortBeatSheet(BaseModel):
    title: str = Field(default="", max_length=500)
    logline: str = Field(default="", max_length=2000)
    pov: str = Field(default="first_person", max_length=64)
    beats: list[FanqieShortBeat] = Field(default_factory=list)
    unlock_milestone_segment: int = Field(default=2, ge=1)


def resolve_length_preset(length_key: str | None) -> dict[str, int]:
    key = (length_key or "").strip() or DEFAULT_LENGTH_KEY
    spec = LENGTH_PRESET_SPECS.get(key)
    if spec is None:
        raise ValueError(
            f"Unknown fanqie short length_key: {key}. "
            f"Expected one of: {sorted(LENGTH_PRESET_SPECS)}"
        )
    return dict(spec)


def build_fanqie_short_metadata(
    *,
    length_key: str | None = None,
    pov: str = "first_person",
    segment_count: int | None = None,
    target_words: int | None = None,
    unlock_line_ratio: float = DEFAULT_UNLOCK_LINE_RATIO,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    spec = resolve_length_preset(length_key)
    words = int(target_words or spec["target_words"])
    segments = int(segment_count or spec["segment_count"])
    merged: dict[str, object] = {
        "content_mode": FANQIE_SHORT_CONTENT_MODE,
        "platform_key": FANQIE_SHORT_PLATFORM_KEY,
        "length_key": length_key or DEFAULT_LENGTH_KEY,
        "pov": pov,
        "unlock_line_ratio": unlock_line_ratio,
        "segment_count": segments,
        "signing_target_unlocks": DEFAULT_SIGNING_TARGET_UNLOCKS,
        "export_format": "single_markdown",
        "fanqie_short_quality": {
            "scene_min_score": SCENE_MIN_SCORE,
            "hook_min_score": SCENE_HOOK_MIN_SCORE,
            "payoff_min_score": SCENE_PAYOFF_MIN_SCORE,
            "title_pattern": "开局公开羞辱/危机 + 我让反派当众自爆/翻车/认罪",
            "title_avoid": ["抽象功能名", "只写设定名", "没有冲突和爽点结果"],
            "goldfinger_visible_deadline_words": 50,
            "first_feedback_deadline_words": 200,
            "opening_deadline_words": 300,
            "first_payoff_deadline_ratio": unlock_line_ratio,
            "closure_required": True,
            "continuity_required": True,
        },
    }
    if extra:
        merged.update(dict(extra))
    return merged


def is_fanqie_short_metadata(metadata: Mapping[str, Any] | None) -> bool:
    if not metadata:
        return False
    if metadata.get("content_mode") == FANQIE_SHORT_CONTENT_MODE:
        return True
    if metadata.get("platform_key") == FANQIE_SHORT_PLATFORM_KEY:
        return True
    return False


def is_fanqie_short_project(
    project: object | Mapping[str, Any] | None,
    *,
    project_type: str | ProjectType | None = None,
) -> bool:
    if project_type is not None:
        normalized = (
            project_type.value if isinstance(project_type, ProjectType) else str(project_type)
        )
        if normalized == ProjectType.FANQIE_SHORT.value:
            return True
    if project is None:
        return False
    ptype = getattr(project, "project_type", None) or (
        project.get("project_type") if isinstance(project, Mapping) else None
    )
    if ptype == ProjectType.FANQIE_SHORT.value:
        return True
    meta = getattr(project, "metadata_json", None) or getattr(project, "metadata", None)
    if isinstance(project, Mapping):
        meta = meta or project.get("metadata_json") or project.get("metadata")
    if isinstance(meta, Mapping):
        return is_fanqie_short_metadata(meta)
    return False


def validate_fanqie_short_project(payload: "ProjectCreate") -> None:
    if payload.project_type != ProjectType.FANQIE_SHORT:
        return
    if payload.target_word_count < MIN_TOTAL_WORDS or payload.target_word_count > MAX_TOTAL_WORDS:
        raise ValueError(
            f"Fanqie short target_word_count must be between {MIN_TOTAL_WORDS} and "
            f"{MAX_TOTAL_WORDS}, got {payload.target_word_count}"
        )
    if (
        payload.target_chapters < MIN_SEGMENT_COUNT
        or payload.target_chapters > MAX_SEGMENT_COUNT
    ):
        raise ValueError(
            f"Fanqie short segment count (target_chapters) must be between "
            f"{MIN_SEGMENT_COUNT} and {MAX_SEGMENT_COUNT}, got {payload.target_chapters}"
        )
    meta = payload.metadata or {}
    if not is_fanqie_short_metadata(meta):
        raise ValueError(
            "Fanqie short projects require metadata.content_mode=fanqie_short_story "
            "or metadata.platform_key=tomato_short"
        )


def segment_target_words(total_words: int, segment_count: int) -> int:
    return max(1500, int(total_words / max(segment_count, 1)))


def apply_fanqie_short_writing_profile(
    base: dict[str, Any] | None,
    *,
    pov: str = "first_person",
) -> dict[str, Any]:
    profile = dict(base or {})
    style = dict(profile.get("style") or {})
    style["pov_type"] = "first-person" if pov == "first_person" else "third-limited"
    style["sentence_style"] = "short-punchy"
    style["prose_style"] = "fanqie-short-story"
    style["info_density"] = "lean"
    style["dialogue_ratio"] = 0.48
    profile["style"] = style

    market = dict(profile.get("market") or {})
    market["platform_target"] = "番茄小说·短故事"
    market["content_mode"] = "番茄短故事单篇完结"
    market["prompt_pack_key"] = "fanqie_short"
    market["reader_promise"] = (
        "单篇完结、第一人称、前30%必须完成冲突亮相与第一次反击信号，"
        "若有金手指需在开篇50字左右可见并立刻生效，禁止章末连载式悬念。"
    )
    market["hook_deadline_words"] = 300
    market["payoff_rhythm"] = "前200字有第一次小反馈；全文高密度短回报，30%前完成第一次小爆点"
    market["title_strategy"] = "公开羞辱/开局危机 + 我让反派当众自爆/翻车/认罪"
    market["update_strategy"] = "单篇完结上传"
    profile["market"] = market

    serialization = dict(profile.get("serialization") or {})
    serialization["opening_mandate"] = (
        "开篇50字内主角进入聚光灯，若有金手指必须可见；100字内出现可感冲突；"
        "200字内给第一次撤回、露馅、证据、小打脸或能力反馈；"
        "禁止长篇背景说明。"
    )
    serialization["first_three_chapter_goal"] = "不适用：短故事按段推进，前30%完成核心循环建立。"
    serialization["chapter_ending_rule"] = (
        "段末可留情绪悬停，但禁止连载式「下章揭晓」；"
        "全文必须在末段收束主线。"
    )
    serialization["free_chapter_strategy"] = "前30%为免费段，须包含冲突、反击信号、第一次小爆点。"
    profile["serialization"] = serialization

    methodology = dict(profile.get("methodology") or {})
    methodology["scene_threshold_override"] = SCENE_MIN_SCORE
    profile["methodology"] = methodology
    return profile


# ── 题材「适合短篇」标注（番茄短故事选题材用）────────────────────────────

_SHORT_STORY_SUITABLE_GENRE_KEYS: frozenset[str] = frozenset(
    {
        "urban-power-reversal",
        "urban-blacktech",
        "urban-xiuxian-2-0",
        "urban-realistic-family",
        "suspense-detective",
        "rule-horror",
        "folk-mystery",
        "exorcist-detective",
        "psychological-thriller",
        "thriller-conspiracy",
        "female-growth-romance",
        "female-no-cp",
        "palace-revenge",
        "palace-mystery-female",
        "cn-romantasy-court",
        "human-nature-game",
        "rebirth-business",
        "horror-tycoon",
        "apocalypse-supply",
        "apocalypse-rule",
        "apocalypse-survival",
        "eastern-aesthetic-fantasy",
        "dark-romance",
        "enemies-to-lovers",
        "slow-burn-romance",
        "paranormal-romance",
        "youth-campus-growth",
        "bl-relationship-case",
        "game-esports",
        "detective-procedural",
        "cozy-mystery",
    }
)

_SHORT_STORY_UNSUITABLE_GENRE_KEYS: frozenset[str] = frozenset(
    {
        "xianxia-upgrade",
        "infinite-flow",
        "starsea-war",
        "beast-taming-upgrade",
        "history-hegemony",
        "historical-research-travel",
        "mecha-warfare",
        "epic-fantasy",
        "space-opera",
        "military-scifi",
        "litrpg-progression",
        "gamelit-isekai",
        "cultivation-western",
        "monster-evolution",
        "superhero-fiction",
        "portal-fantasy",
        "cozy-fantasy",
        "apocalypse-basebuilding",
        "blacktech-techtree",
        "game-retro-nostalgia",
        "royal-road",
        "kindle-unlimited",
    }
)

_SHORT_STORY_POSITIVE_KEYWORDS: tuple[str, ...] = (
    "都市",
    "悬疑",
    "言情",
    "情感",
    "复仇",
    "打脸",
    "逆袭",
    "脑洞",
    "灵异",
    "怪谈",
    "虐",
    "甜宠",
    "豪门",
    "重生",
    "追妻",
    "火葬场",
    "反转",
    "惊悚",
    "伦理",
    "家庭",
    "职场",
    "校园",
    "宫斗",
    "末世",
    "horror",
    "thriller",
    "mystery",
    "romance",
    "urban",
    "revenge",
    "suspense",
)

_SHORT_STORY_NEGATIVE_KEYWORDS: tuple[str, ...] = (
    "仙侠升级",
    "修仙升级",
    "无限流",
    "星际战争",
    "争霸",
    "长篇升级",
    "百万字",
    "群像史诗",
    "space opera",
    "litrpg",
    "progression fantasy",
    "epic fantasy",
)


def _genre_preset_text_blob(payload: Mapping[str, Any]) -> str:
    parts = [
        str(payload.get("key") or ""),
        str(payload.get("name") or ""),
        str(payload.get("genre") or ""),
        str(payload.get("sub_genre") or ""),
        str(payload.get("description") or ""),
    ]
    for field in ("heat_domains", "reader_rewards", "narrative_drives", "commercial_signals"):
        values = payload.get(field)
        if isinstance(values, list):
            parts.extend(str(v) for v in values)
    return " ".join(parts).lower()


def _contains_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def evaluate_genre_suitable_for_short_story(payload: Mapping[str, Any]) -> bool:
    """判断题材 preset 是否适合番茄短故事（单篇、强钩子、单线）。"""
    if "suitable_for_short_story" in payload:
        return bool(payload["suitable_for_short_story"])

    key = str(payload.get("key") or "")
    if key in _SHORT_STORY_UNSUITABLE_GENRE_KEYS:
        return False
    if key in _SHORT_STORY_SUITABLE_GENRE_KEYS:
        return True

    platforms = [str(item) for item in (payload.get("recommended_platforms") or [])]
    on_fanqie = any("番茄" in platform for platform in platforms)
    language = str(payload.get("language") or "zh-CN")
    if not on_fanqie and not language.lower().startswith("zh"):
        return _contains_keyword(_genre_preset_text_blob(payload), _SHORT_STORY_POSITIVE_KEYWORDS)

    text = _genre_preset_text_blob(payload)
    if _contains_keyword(text, _SHORT_STORY_NEGATIVE_KEYWORDS):
        return False

    chapter_opts = payload.get("target_chapter_options") or []
    if (
        isinstance(chapter_opts, list)
        and chapter_opts
        and min(int(v) for v in chapter_opts if isinstance(v, (int, float))) >= 20
        and _contains_keyword(text, ("修仙", "仙侠", "玄幻升级"))
    ):
        return False

    if _contains_keyword(text, _SHORT_STORY_POSITIVE_KEYWORDS):
        return on_fanqie or language.lower().startswith("zh")

    return False


def ensure_fanqie_short_genre_compatible(
    genre_key: str,
    *,
    suitable: bool,
) -> None:
    if not suitable:
        raise ValueError(
            f"题材「{genre_key}」未标注为适合短篇，无法在番茄短故事模式下使用。"
            "请选择带「适合短篇」标签的题材。"
        )
