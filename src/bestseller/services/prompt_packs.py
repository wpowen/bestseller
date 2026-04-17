from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ObligatoryScene(BaseModel, frozen=True):
    """A genre-required scene type that must appear somewhere in the novel."""

    code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    timing: str = Field(
        default="any",
        description=(
            "When this scene should appear: "
            "'act_1', 'act_2_midpoint', 'act_3', 'final_chapter', 'any'."
        ),
    )
    check_keywords: list[str] = Field(default_factory=list)


class PromptPackFragments(BaseModel):
    global_rules: str | None = None
    planner_book_spec: str | None = None
    planner_world_spec: str | None = None
    planner_cast_spec: str | None = None
    planner_volume_plan: str | None = None
    planner_outline: str | None = None
    scene_writer: str | None = None
    scene_review: str | None = None
    scene_rewrite: str | None = None
    chapter_review: str | None = None
    chapter_rewrite: str | None = None
    structure_guidance: str | None = None

    # --- 方法论扩展片段 (Methodology-driven fragments) ---
    emotion_engineering: str | None = None
    conflict_stakes: str | None = None
    hook_design: str | None = None
    core_loop: str | None = None
    dialogue_rules: str | None = None
    visual_writing: str | None = None
    opening_rules: str | None = None
    climax_design: str | None = None
    pacing_guidance: str | None = None
    character_design: str | None = None
    reversal_design: str | None = None
    reaction_amplification: str | None = None


class PromptPack(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    version: str = Field(default="1.0", min_length=1, max_length=32)
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    writing_profile_overrides: dict[str, Any] = Field(default_factory=dict)
    fragments: PromptPackFragments = Field(default_factory=PromptPackFragments)
    obligatory_scenes: list[ObligatoryScene] = Field(default_factory=list)


def _prompt_pack_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "prompt_packs"


@lru_cache(maxsize=1)
def load_prompt_pack_registry() -> dict[str, PromptPack]:
    registry: dict[str, PromptPack] = {}
    pack_dir = _prompt_pack_dir()
    if not pack_dir.exists():
        return registry
    for path in sorted(pack_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        pack = PromptPack.model_validate(raw)
        registry[pack.key] = pack
    return registry


def list_prompt_packs() -> list[PromptPack]:
    return list(load_prompt_pack_registry().values())


def get_prompt_pack(key: str | None) -> PromptPack | None:
    if not key:
        return None
    return load_prompt_pack_registry().get(key)


def infer_default_prompt_pack_key(genre: str, sub_genre: str | None = None) -> str | None:
    label = f"{genre} {sub_genre or ''}".lower()
    # ── 2026 trending subgenre routes (check FIRST so they win over generic tokens) ──
    # 都市修仙 / 修仙 2.0 — must beat generic 都市 and 仙 routes
    if any(token in label for token in ("都市修仙", "修仙2.0", "修仙二.0", "灵气复苏", "系统修仙", "urban-cultivation", "urban cultivation")):
        return "urban-cultivation-2.0"
    # 社畜摆烂 / 沙雕种田
    if any(token in label for token in ("社畜", "摆烂", "沙雕", "躺平", "slacker")):
        return "shezhu-bailan-comedy"
    # 娱乐圈甜宠
    if any(token in label for token in ("娱乐圈", "选秀", "剧组", "直播带货", "entertainment-circle", "entertainment")):
        return "entertainment-sweet"
    # Cozy LitRPG (Eng cozy + litrpg combo)
    if any(token in label for token in ("cozy-litrpg", "cozy litrpg", "magical inn", "treasured bakery", "cozy crafting")):
        return "cozy-litrpg"
    # System Apocalypse — Healer / Support
    if any(token in label for token in ("system apocalypse", "system-apocalypse", "healer mc", "support class", "healer-mc")):
        return "system-apocalypse-healer"
    # Villainess / Novel-Extra reincarnation
    if any(token in label for token in ("villainess", "otome isekai", "novel extra", "novel-extra", "反派千金", "穿成反派", "穿书反派")):
        return "villainess-reincarnation"

    # ── Pre-existing routes ──
    # Apocalypse / survival (check before sci-fi to catch "末日科幻" correctly)
    if any(token in label for token in ("末日", "囤货", "废土")):
        return "apocalypse-supply-chain"
    # Suspense & mystery
    if any(token in label for token in ("推理", "探案", "怪谈", "诡事", "民俗", "悬疑", "恐怖", "惊悚")):
        return "suspense-mystery"
    # Female-lead palace drama (check before history to catch "宫斗权谋")
    if any(token in label for token in ("宫斗", "大女主", "后宫", "心理暗战", "女帝")):
        return "female-palace"
    # Historical & strategy
    if any(token in label for token in ("历史", "争霸", "经商", "穿越", "考据", "权谋", "三国", "战国")):
        return "history-strategy"
    # Sci-fi & space
    if any(token in label for token in ("星海", "星际", "黑科技", "机甲", "太空", "科幻")):
        return "scifi-starwar"
    # Game & esport
    if any(token in label for token in ("游戏", "电竞", "无限流", "怪物猎人", "副本", "系统流")):
        return "game-esport"
    # Eastern aesthetic fantasy
    if any(token in label for token in ("东方美学", "国风", "水墨", "诗词", "古典仙侠")):
        return "eastern-aesthetic"
    # Xianxia / xuanhuan
    if any(token in label for token in ("仙", "玄幻", "奇幻", "升级", "修真")):
        return "xianxia-upgrade-core"
    # Urban power
    if any(token in label for token in ("都市", "异能", "现实")):
        return "urban-power-reversal"
    # Romance / female-frequency (general)
    if any(token in label for token in ("女频", "言情", "成长", "恋爱")):
        return "romance-tension-growth"
    return None


def resolve_prompt_pack(key: str | None, *, genre: str, sub_genre: str | None = None) -> PromptPack | None:
    explicit = get_prompt_pack(key)
    if explicit is not None:
        return explicit
    return get_prompt_pack(infer_default_prompt_pack_key(genre, sub_genre))


def render_prompt_pack_prompt_block(pack: PromptPack | None) -> str:
    if pack is None:
        return ""
    lines = [
        f"Prompt Pack：{pack.name}（{pack.key} v{pack.version}）",
        f"- 定位：{pack.description}",
    ]
    if pack.tags:
        lines.append(f"- 关键词：{'、'.join(pack.tags)}")
    if pack.source_notes:
        lines.append(f"- 设计说明：{'；'.join(pack.source_notes)}")
    if pack.anti_patterns:
        lines.append(f"- 明确避免：{'；'.join(pack.anti_patterns)}")
    if pack.fragments.global_rules:
        lines.append(f"- Pack 级写法规则：{pack.fragments.global_rules}")
    return "\n".join(lines)


def render_prompt_pack_fragment(pack: PromptPack | None, fragment_name: str) -> str:
    if pack is None:
        return ""
    value = getattr(pack.fragments, fragment_name, None)
    return value.strip() if isinstance(value, str) and value.strip() else ""


# Methodology fragment keys that should be injected into scene writing prompts.
_METHODOLOGY_SCENE_FRAGMENTS = (
    "emotion_engineering",
    "conflict_stakes",
    "hook_design",
    "core_loop",
    "dialogue_rules",
    "visual_writing",
    "pacing_guidance",
    "reaction_amplification",
)

# Methodology fragment keys for chapter review prompts.
_METHODOLOGY_REVIEW_FRAGMENTS = (
    "emotion_engineering",
    "conflict_stakes",
    "hook_design",
    "core_loop",
    "pacing_guidance",
)

# Methodology fragment keys for planner/outline prompts.
_METHODOLOGY_PLANNER_FRAGMENTS = (
    "opening_rules",
    "climax_design",
    "character_design",
    "reversal_design",
    "core_loop",
)


def render_methodology_block(
    pack: PromptPack | None,
    *,
    phase: str = "scene",
) -> str:
    """Render combined methodology guidance block for a given phase.

    Args:
        pack: Resolved prompt pack (may be None).
        phase: One of "scene", "review", "planner".

    Returns:
        Multi-line string with all applicable methodology fragments, or "".
    """
    if pack is None:
        return ""

    if phase == "review":
        keys = _METHODOLOGY_REVIEW_FRAGMENTS
    elif phase == "planner":
        keys = _METHODOLOGY_PLANNER_FRAGMENTS
    else:
        keys = _METHODOLOGY_SCENE_FRAGMENTS

    sections: list[str] = []
    for key in keys:
        value = getattr(pack.fragments, key, None)
        if isinstance(value, str) and value.strip():
            sections.append(f"【{key}】\n{value.strip()}")

    if not sections:
        return ""
    return "## 写法方法论指导\n\n" + "\n\n".join(sections)
