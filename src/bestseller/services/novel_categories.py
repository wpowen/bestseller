"""Service for loading and resolving novel category research data.

Each category defines genre-specific challenge evolution pathways,
protagonist archetypes, world rule templates, quality traps, and
disqualifiers.  These replace the hardcoded conflict phase system
in the planner with per-category structures.

Follows the same YAML-loading pattern as ``prompt_packs.py``.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ChapterConflictTemplates(BaseModel, frozen=True):
    """Per-chapter-phase conflict description templates."""

    setup_zh: str = ""
    setup_en: str = ""
    investigation_zh: str = ""
    investigation_en: str = ""
    pressure_zh: str = ""
    pressure_en: str = ""
    reversal_zh: str = ""
    reversal_en: str = ""
    climax_zh: str = ""
    climax_en: str = ""


class ChallengePhase(BaseModel, frozen=True):
    """One stage in a category's challenge evolution pathway."""

    phase_key: str = Field(min_length=1, max_length=64)
    phase_name_zh: str = Field(min_length=1, max_length=60)
    phase_name_en: str = Field(min_length=1, max_length=60)
    description_zh: str = ""
    description_en: str = ""
    typical_force_types: list[str] = Field(default_factory=list)
    volume_goal_template_zh: str = ""
    volume_goal_template_en: str = ""
    volume_climax_template_zh: str = ""
    volume_climax_template_en: str = ""
    volume_obstacle_template_zh: str = ""
    volume_obstacle_template_en: str = ""
    volume_resolution_template_zh: str = ""
    volume_resolution_template_en: str = ""
    chapter_conflict_templates: ChapterConflictTemplates = Field(
        default_factory=ChapterConflictTemplates,
    )


class ProtagonistArchetype(BaseModel, frozen=True):
    """A category-specific protagonist archetype."""

    archetype_key: str = Field(min_length=1, max_length=64)
    name_zh: str = ""
    name_en: str = ""
    core_wound_zh: str = ""
    core_wound_en: str = ""
    external_goal_template_zh: str = ""
    external_goal_template_en: str = ""
    internal_need_template_zh: str = ""
    internal_need_template_en: str = ""
    arc_trajectory_zh: str = ""
    arc_trajectory_en: str = ""
    flaw_zh: str = ""
    flaw_en: str = ""
    strength_zh: str = ""
    strength_en: str = ""


class WorldRuleTemplate(BaseModel, frozen=True):
    """A category-specific world rule template."""

    rule_template_key: str = Field(min_length=1, max_length=64)
    name_zh: str = ""
    name_en: str = ""
    description_zh: str = ""
    description_en: str = ""
    story_consequence_zh: str = ""
    story_consequence_en: str = ""
    exploitation_potential_zh: str = ""
    exploitation_potential_en: str = ""


class QualityTrap(BaseModel, frozen=True):
    """A common anti-pattern for a category."""

    trap_key: str = Field(min_length=1, max_length=64)
    description_zh: str = ""
    description_en: str = ""
    severity: str = "warning"  # "critical" | "warning"


class Disqualifier(BaseModel, frozen=True):
    """A hard failure condition for a category."""

    rule_zh: str = ""
    rule_en: str = ""
    severity: str = "fatal"  # "fatal" | "critical"


class NovelCategoryResearch(BaseModel, frozen=True):
    """Complete category research data loaded from YAML."""

    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=60)
    name_en: str = Field(default="", max_length=60)
    reader_promise_zh: str = ""
    reader_promise_en: str = ""
    challenge_evolution_pathway: list[ChallengePhase] = Field(default_factory=list)
    protagonist_archetypes: list[ProtagonistArchetype] = Field(default_factory=list)
    world_rule_templates: list[WorldRuleTemplate] = Field(default_factory=list)
    quality_traps: list[QualityTrap] = Field(default_factory=list)
    disqualifiers: list[Disqualifier] = Field(default_factory=list)
    tolerance_ranges: dict[str, Any] = Field(default_factory=dict)
    signal_keywords: dict[str, list[str]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _novel_categories_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "novel_categories"


@lru_cache(maxsize=1)
def load_novel_category_registry() -> dict[str, NovelCategoryResearch]:
    """Load all category YAML files from ``config/novel_categories/``."""
    registry: dict[str, NovelCategoryResearch] = {}
    cat_dir = _novel_categories_dir()
    if not cat_dir.exists():
        return registry
    for path in sorted(cat_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                continue
            cat = NovelCategoryResearch.model_validate(raw)
            registry[cat.key] = cat
        except Exception:
            logger.warning("Failed to load novel category from %s", path, exc_info=True)
    return registry


def list_novel_categories() -> list[NovelCategoryResearch]:
    """Return all loaded category research entries."""
    return list(load_novel_category_registry().values())


def get_novel_category(key: str | None) -> NovelCategoryResearch | None:
    """Return a single category by key, or *None*."""
    if not key:
        return None
    return load_novel_category_registry().get(key)


def resolve_novel_category(
    genre: str,
    sub_genre: str | None = None,
    *,
    genre_preset_key: str | None = None,
) -> NovelCategoryResearch:
    """Resolve the best-matching category for a genre/sub-genre pair.

    Resolution strategy mirrors ``resolve_genre_review_profile``:
    1. Direct preset key lookup via ``_GENRE_TO_CATEGORY_MAP``.
    2. Infer via ``infer_genre_preset()`` and map the result.
    3. Fuzzy keyword matching.
    4. Return ``"default"`` category.
    """
    registry = load_novel_category_registry()
    if not registry:
        return _empty_default()

    # Import the shared map from genre_review_profiles.
    try:
        from bestseller.services.genre_review_profiles import _GENRE_TO_CATEGORY_MAP
    except ImportError:
        _GENRE_TO_CATEGORY_MAP: dict[str, str] = {}  # type: ignore[no-redef]

    # Strategy 1: direct preset key lookup
    if genre_preset_key:
        category_key = _GENRE_TO_CATEGORY_MAP.get(genre_preset_key)
        if category_key and category_key in registry:
            return registry[category_key]

    # Strategy 2: infer via writing_presets
    try:
        from bestseller.services.writing_presets import infer_genre_preset

        inferred = infer_genre_preset(genre, sub_genre)
        if inferred is not None:
            category_key = _GENRE_TO_CATEGORY_MAP.get(inferred.key)
            if category_key and category_key in registry:
                return registry[category_key]
    except Exception:
        logger.debug("Could not infer genre preset; falling through to keyword match.")

    # Strategy 3: fuzzy keyword matching
    haystack = " ".join(part for part in [genre, sub_genre] if part).lower()
    for keyword, category_key in _GENRE_NAME_KEYWORD_MAP.items():
        if keyword in haystack and category_key in registry:
            return registry[category_key]

    # Strategy 4: default
    return registry.get("default", _empty_default())


# Keyword fallback map (mirrors genre_review_profiles)
_GENRE_NAME_KEYWORD_MAP: dict[str, str] = {
    # action-progression
    "仙": "action-progression",
    "修仙": "action-progression",
    "玄幻": "action-progression",
    "末日": "action-progression",
    "异能": "action-progression",
    "升级": "action-progression",
    "xianxia": "action-progression",
    "litrpg": "action-progression",
    "progression": "action-progression",
    "cultivation": "action-progression",
    # relationship-driven
    "言情": "relationship-driven",
    "浪漫": "relationship-driven",
    "宫斗": "relationship-driven",
    "romance": "relationship-driven",
    "romantasy": "relationship-driven",
    # suspense-mystery
    "推理": "suspense-mystery",
    "悬疑": "suspense-mystery",
    "怪谈": "suspense-mystery",
    "mystery": "suspense-mystery",
    "thriller": "suspense-mystery",
    # strategy-worldbuilding
    "权谋": "strategy-worldbuilding",
    "历史": "strategy-worldbuilding",
    "争霸": "strategy-worldbuilding",
    "strategy": "strategy-worldbuilding",
    # esports-competition
    "电竞": "esports-competition",
    "游戏": "esports-competition",
    "esport": "esports-competition",
    # female-growth-ncp
    "大女主": "female-growth-ncp",
    "女帝": "female-growth-ncp",
    # base-building
    "种田": "base-building",
    "基建": "base-building",
    "经营": "base-building",
    # eastern-aesthetic
    "东方美学": "eastern-aesthetic",
    "国风": "eastern-aesthetic",
    "水墨": "eastern-aesthetic",
}


def _empty_default() -> NovelCategoryResearch:
    """Return a minimal default category when no configs are loaded."""
    return NovelCategoryResearch(key="default", name="通用", name_en="Default")


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def render_category_anti_patterns(
    category: NovelCategoryResearch,
    *,
    is_en: bool = False,
    max_traps: int = 6,
) -> str:
    """Render quality traps as a MUST AVOID block for LLM prompts."""
    traps = category.quality_traps[:max_traps]
    if not traps:
        return ""
    if is_en:
        header = "## MUST AVOID — Category-Specific Anti-Patterns"
        lines = [header]
        for trap in traps:
            lines.append(f"- [{trap.severity.upper()}] {trap.description_en or trap.description_zh}")
    else:
        header = "## 【必须避免的品类陷阱】"
        lines = [header]
        for trap in traps:
            lines.append(f"- [{trap.severity.upper()}] {trap.description_zh}")

    # Append disqualifiers
    if category.disqualifiers:
        lines.append("")
        if is_en:
            lines.append("### DISQUALIFIERS (automatic failure)")
            for dq in category.disqualifiers:
                lines.append(f"- {dq.rule_en or dq.rule_zh}")
        else:
            lines.append("### 一票否决项")
            for dq in category.disqualifiers:
                lines.append(f"- {dq.rule_zh}")

    return "\n".join(lines)


def render_category_reader_promise(
    category: NovelCategoryResearch,
    *,
    is_en: bool = False,
) -> str:
    """Render the reader promise for prompt injection."""
    promise = category.reader_promise_en if is_en else category.reader_promise_zh
    if not promise:
        return ""
    if is_en:
        return f"## Reader Promise\n{promise}"
    return f"## 读者承诺\n{promise}"


def render_category_challenge_evolution_summary(
    category: NovelCategoryResearch,
    *,
    is_en: bool = False,
) -> str:
    """Render the expected challenge evolution pathway for LLM context."""
    pathway = category.challenge_evolution_pathway
    if not pathway:
        return ""
    if is_en:
        header = "## Challenge Evolution Pathway"
        lines = [header]
        for i, phase in enumerate(pathway, 1):
            lines.append(
                f"{i}. **{phase.phase_name_en}** — {phase.description_en or phase.description_zh}"
            )
    else:
        header = "## 挑战进化路径"
        lines = [header]
        for i, phase in enumerate(pathway, 1):
            lines.append(
                f"{i}. **{phase.phase_name_zh}** — {phase.description_zh}"
            )
    return "\n".join(lines)
