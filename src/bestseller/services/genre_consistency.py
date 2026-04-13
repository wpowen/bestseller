"""Genre-specific consistency rules for novel generation.

Provides progression system validation for genre-specific mechanics:
- Xianxia: cultivation tier tracking (炼气→筑基→金丹→元婴→化神→...)
- LitRPG: stat block validation (STR, VIT, AGI, etc.)
- Universal: power system terminology consistency

These rules are injected into the writer prompt as hard constraints and
validated post-generation to catch violations.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Genre progression profiles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GenreConsistencyProfile:
    """Defines genre-specific progression rules."""
    progression_system: str
    tier_names: tuple[str, ...] = ()
    tier_direction: str = "monotonic_up"
    stat_names: tuple[str, ...] = ()
    stat_direction: str = "monotonic_up"
    required_tracking: tuple[str, ...] = ()
    format_rules: dict[str, str] = field(default_factory=dict)


# Pre-defined genre profiles
XIANXIA_PROFILE = GenreConsistencyProfile(
    progression_system="cultivation_tiers",
    tier_names=(
        "炼气", "筑基", "金丹", "元婴", "化神",
        "合体", "大乘", "渡劫", "仙人",
    ),
    tier_direction="monotonic_up",
    required_tracking=("cultivation_level", "martial_arts", "artifacts", "spiritual_root"),
)

LITRPG_PROFILE = GenreConsistencyProfile(
    progression_system="stat_block",
    stat_names=("STR", "VIT", "AGI", "INT", "WIS", "PER", "LCK", "HP", "MP"),
    stat_direction="monotonic_up",
    required_tracking=("level", "skills", "experience", "stats"),
    format_rules={"stat_block": "consistent_format_across_chapters"},
)

WUXIA_PROFILE = GenreConsistencyProfile(
    progression_system="martial_arts_tiers",
    tier_names=(
        "入门", "小成", "大成", "登峰造极", "返璞归真",
    ),
    tier_direction="monotonic_up",
    required_tracking=("martial_arts_level", "techniques", "inner_force"),
)

GENRE_PROFILES: dict[str, GenreConsistencyProfile] = {
    "xianxia": XIANXIA_PROFILE,
    "xianxia-upgrade": XIANXIA_PROFILE,
    "cultivation": XIANXIA_PROFILE,
    "litrpg": LITRPG_PROFILE,
    "litrpg-progression": LITRPG_PROFILE,
    "gamelit": LITRPG_PROFILE,
    "wuxia": WUXIA_PROFILE,
    "martial-arts": WUXIA_PROFILE,
}


def get_genre_profile(genre: str, sub_genre: str | None = None) -> GenreConsistencyProfile | None:
    """Look up genre consistency profile by genre/sub_genre."""
    for key in [sub_genre, genre]:
        if key and key.lower() in GENRE_PROFILES:
            return GENRE_PROFILES[key.lower()]
    # Partial match
    for key in [sub_genre, genre]:
        if not key:
            continue
        for profile_key, profile in GENRE_PROFILES.items():
            if profile_key in key.lower() or key.lower() in profile_key:
                return profile
    return None


# ---------------------------------------------------------------------------
# Xianxia cultivation level tracking
# ---------------------------------------------------------------------------

def get_cultivation_tier_index(
    tier_text: str,
    tier_names: tuple[str, ...],
) -> int:
    """Return the index of a cultivation tier, or -1 if not found."""
    for i, tier in enumerate(tier_names):
        if tier in tier_text:
            return i
    return -1


def validate_xianxia_progression(
    character_name: str,
    current_level_text: str,
    previous_level_text: str,
    tier_names: tuple[str, ...],
) -> list[str]:
    """Check that cultivation level has not regressed."""
    current_idx = get_cultivation_tier_index(current_level_text, tier_names)
    previous_idx = get_cultivation_tier_index(previous_level_text, tier_names)

    if current_idx < 0 or previous_idx < 0:
        return []

    if current_idx < previous_idx:
        return [
            f"[修为回退] {character_name}: 从「{previous_level_text}」降至「{current_level_text}」。"
            f"修为境界只能提升，不可无故降低。如确需降级，必须在正文中给出明确原因（如散功、受伤等）。"
        ]
    return []


# ---------------------------------------------------------------------------
# LitRPG stat tracking
# ---------------------------------------------------------------------------

_STAT_BLOCK_RE = re.compile(
    r"(STR|VIT|AGI|INT|WIS|PER|LCK|HP|MP|Level)\s*[：:]\s*(\d+)",
    re.IGNORECASE,
)


def extract_stat_block(text: str) -> dict[str, int]:
    """Extract stat values from text containing a stat block."""
    stats: dict[str, int] = {}
    for match in _STAT_BLOCK_RE.finditer(text):
        stat_name = match.group(1).upper()
        stat_value = int(match.group(2))
        stats[stat_name] = stat_value
    return stats


def validate_litrpg_stats(
    current_stats: dict[str, int],
    previous_stats: dict[str, int],
    character_name: str = "protagonist",
) -> list[str]:
    """Check that LitRPG stats have not decreased without explanation."""
    warnings: list[str] = []
    for stat_name, current_value in current_stats.items():
        previous_value = previous_stats.get(stat_name)
        if previous_value is not None and current_value < previous_value:
            warnings.append(
                f"[数值回退] {character_name} {stat_name}: {previous_value} → {current_value}。"
                f"属性值只能增长，不可无故降低。如果有debuff/诅咒等机制导致降低，必须在正文中明确说明。"
            )
    return warnings


def validate_litrpg_skill_inventory(
    current_skills: list[str],
    previous_skills: list[str],
    character_name: str = "protagonist",
) -> list[str]:
    """Check that skills have not been silently removed."""
    removed = set(previous_skills) - set(current_skills)
    if removed:
        return [
            f"[技能消失] {character_name}: 以下技能从上一章存在但本章消失: {', '.join(sorted(removed))}。"
            f"如果技能被替换/遗忘，必须在正文中明确说明机制。"
        ]
    return []


# ---------------------------------------------------------------------------
# Prompt constraint rendering
# ---------------------------------------------------------------------------

def build_genre_constraint_block(
    profile: GenreConsistencyProfile,
    character_states: dict[str, dict[str, Any]],
    *,
    language: str = "zh-CN",
) -> str:
    """Render genre-specific constraints for the writer prompt.

    Parameters
    ----------
    profile : GenreConsistencyProfile
        The genre's consistency rules.
    character_states : dict
        Mapping of character_name → {cultivation_level, stats, skills, ...}
    """
    if not character_states:
        return ""

    is_zh = language.lower().startswith("zh")
    lines: list[str] = []

    if profile.progression_system == "cultivation_tiers" and profile.tier_names:
        if is_zh:
            lines.append("【修仙境界约束】")
            lines.append(f"境界体系（从低到高）: {' → '.join(profile.tier_names)}")
            for name, state in character_states.items():
                level = state.get("cultivation_level", "")
                if level:
                    lines.append(f"• {name}: 当前「{level}」— 只能提升，不可回退")
        else:
            lines.append("[CULTIVATION TIER CONSTRAINTS]")
            lines.append(f"Tier system (low→high): {' → '.join(profile.tier_names)}")
            for name, state in character_states.items():
                level = state.get("cultivation_level", "")
                if level:
                    lines.append(f"• {name}: current '{level}' — can only advance, never regress")

    elif profile.progression_system == "stat_block" and profile.stat_names:
        if is_zh:
            lines.append("【LitRPG ��性约束】")
            lines.append(f"属性名: {', '.join(profile.stat_names)}")
            lines.append("规则: 所有数值只能增长，格式必须与前一章保持一致")
            for name, state in character_states.items():
                stats = state.get("stats", {})
                if stats:
                    stat_str = ", ".join(f"{k}={v}" for k, v in stats.items())
                    lines.append(f"• {name}: {stat_str}")
        else:
            lines.append("[LITRPG STAT CONSTRAINTS]")
            lines.append(f"Stats tracked: {', '.join(profile.stat_names)}")
            lines.append("Rule: all values can only increase; format must match previous chapter")
            for name, state in character_states.items():
                stats = state.get("stats", {})
                if stats:
                    stat_str = ", ".join(f"{k}={v}" for k, v in stats.items())
                    lines.append(f"• {name}: {stat_str}")

    return "\n".join(lines) if lines else ""
