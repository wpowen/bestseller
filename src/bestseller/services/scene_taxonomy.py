"""Scene taxonomy for scene-level purpose and environment diversity.

Provides two orthogonal taxonomies:

1. **Scene purpose** (24 types, 4 families):
   * A 结构位类 — Inciting / First Threshold / Midpoint Reversal / Crisis / Climax / Resolution
   * B 动作推进类 — Pursuit / Infiltration / Heist / Battle / Rescue / Chase-with-Talk
   * C 信息关系类 — Revelation / Confrontation / Negotiation / Bonding / Betrayal /
     Reconciliation / Alliance
   * D 内在节奏类 — Reflection / Dilemma / Worldbuilding / Relief / Foreshadow

2. **Environment (7 dimensions)** — physical / time / weather-light /
   dominant-sense / social-density / tempo-scale / vertical-enclosure.

Constraints (evaluated against recent scenes):
  * A chapter's 3-6 scenes should cover ≥3 of the 4 families.
  * Within recent 5 scenes, no purpose may repeat.
  * New scene's 7-d env vector must differ from the prior scene in ≥3/7 dims,
    and from any of prior 3 scenes in ≥2/7 dims.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# 24 scene purposes, grouped into 4 families
# ---------------------------------------------------------------------------

FAMILY_A_STRUCTURAL: dict[str, str] = {
    "inciting":          "触发事件（故事引擎启动）",
    "first_threshold":   "跨越门槛（进入新世界 / 立誓）",
    "midpoint_reversal": "中点翻转（False Victory 或 Mirror Moment）",
    "crisis":            "危机（最坏选择逼近）",
    "climax":            "高潮（决定性对决）",
    "resolution":        "收束（新均衡 / 留白）",
}

FAMILY_B_ACTION: dict[str, str] = {
    "pursuit":          "追击（主动追 / 被动逃）",
    "infiltration":     "潜入（混入 / 刺探）",
    "heist":            "劫夺（计划 + 执行 + 反转）",
    "battle":           "战斗（正面冲突）",
    "rescue":           "营救（时间压力 + 危险度）",
    "chase_with_talk":  "边走边说（移动中的信息交换）",
}

FAMILY_C_RELATION: dict[str, str] = {
    "revelation":     "揭示（信息首次显露）",
    "confrontation":  "对峙（立场硬碰硬）",
    "negotiation":    "谈判（利益交换）",
    "bonding":        "结盟 / 亲近（情感升温）",
    "betrayal":       "背叛（信任破裂）",
    "reconciliation": "和解（裂痕修复或接纳）",
    "alliance":       "正式结盟（契约 / 仪式）",
}

FAMILY_D_INTERIOR: dict[str, str] = {
    "reflection":     "反思（内心独白 / 复盘）",
    "dilemma":        "两难（必须选，都伤）",
    "worldbuilding":  "世界展开（规则 / 历史）",
    "relief":         "喘息（轻松 / 人味 / 幽默）",
    "foreshadow":     "伏笔（种子 / 预兆）",
}

PURPOSE_FAMILIES: dict[str, dict[str, str]] = {
    "A_structural": FAMILY_A_STRUCTURAL,
    "B_action":     FAMILY_B_ACTION,
    "C_relation":   FAMILY_C_RELATION,
    "D_interior":   FAMILY_D_INTERIOR,
}


def family_of(purpose: str) -> str | None:
    for family_key, bucket in PURPOSE_FAMILIES.items():
        if purpose in bucket:
            return family_key
    return None


def purpose_label(key: str) -> str:
    for bucket in PURPOSE_FAMILIES.values():
        if key in bucket:
            return bucket[key]
    return key


def all_purposes() -> Iterable[str]:
    for bucket in PURPOSE_FAMILIES.values():
        yield from bucket.keys()


# ---------------------------------------------------------------------------
# 7-dimensional environment vector
# ---------------------------------------------------------------------------

ENV_DIMENSIONS: list[str] = [
    "physical_space",     # 物理空间
    "time_of_day",        # 时间段
    "weather_light",      # 天气 / 光照
    "dominant_sense",     # 感官主导
    "social_density",     # 社交密度
    "tempo_scale",        # 节奏尺度
    "vertical_enclosure", # 垂直封闭度
]

ENV_VALUES: dict[str, list[str]] = {
    "physical_space": [
        "underground", "indoor_enclosed", "indoor_open",
        "rooftop_exposed", "street_alley", "wilderness",
        "water", "vehicle_interior", "liminal_space",
    ],
    "time_of_day": [
        "pre_dawn_dark", "morning", "noon", "afternoon",
        "dusk", "night", "deep_night", "small_hours",
    ],
    "weather_light": [
        "blazing_sun", "overcast", "rain", "fog",
        "snow", "storm", "artificial_light", "total_dark",
    ],
    "dominant_sense": [
        "sight", "sound", "smell",
        "touch_temperature", "taste", "proprioception",
    ],
    "social_density": [
        "alone", "dyad", "triad", "small_group",
        "anonymous_crowd", "virtual_presence",
    ],
    "tempo_scale": [
        "realtime", "accelerated", "slow_motion",
        "montage", "nested_flashback",
    ],
    "vertical_enclosure": [
        "deep_underground_sealed", "ground_half_sealed",
        "ground_open", "elevated_open", "airborne",
    ],
}

ENV_LABELS_ZH: dict[str, str] = {
    # physical_space
    "underground": "地下", "indoor_enclosed": "室内密闭",
    "indoor_open": "室内开阔", "rooftop_exposed": "高处露天",
    "street_alley": "街巷", "wilderness": "荒野",
    "water": "水域", "vehicle_interior": "交通工具内",
    "liminal_space": "阈限空间",
    # time_of_day
    "pre_dawn_dark": "黎明前黑暗", "morning": "清晨",
    "noon": "正午", "afternoon": "午后", "dusk": "黄昏",
    "night": "入夜", "deep_night": "深夜", "small_hours": "凌晨",
    # weather_light
    "blazing_sun": "烈日", "overcast": "阴霾", "rain": "雨",
    "fog": "雾", "snow": "雪", "storm": "风暴",
    "artificial_light": "人工光", "total_dark": "完全黑暗",
    # dominant_sense
    "sight": "视觉", "sound": "听觉", "smell": "嗅觉",
    "touch_temperature": "触觉/温度", "taste": "味觉",
    "proprioception": "本体觉",
    # social_density
    "alone": "独处", "dyad": "二人", "triad": "三角",
    "small_group": "小组", "anonymous_crowd": "人群匿名",
    "virtual_presence": "虚拟在场",
    # tempo_scale
    "realtime": "实时", "accelerated": "加速",
    "slow_motion": "慢动作", "montage": "蒙太奇",
    "nested_flashback": "闪回嵌套",
    # vertical_enclosure
    "deep_underground_sealed": "深地下封闭",
    "ground_half_sealed": "地面半封闭",
    "ground_open": "地面开阔",
    "elevated_open": "高处开阔",
    "airborne": "空中",
}


@dataclass(frozen=True)
class EnvVector:
    """Immutable 7-dim environment signature for one scene."""

    physical_space: str | None = None
    time_of_day: str | None = None
    weather_light: str | None = None
    dominant_sense: str | None = None
    social_density: str | None = None
    tempo_scale: str | None = None
    vertical_enclosure: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {d: getattr(self, d) for d in ENV_DIMENSIONS}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EnvVector":
        return cls(**{d: (str(data[d]) if data.get(d) else None) for d in ENV_DIMENSIONS})

    def differs_on(self, other: "EnvVector") -> set[str]:
        """Return the set of dimensions where the two vectors differ."""
        diffs: set[str] = set()
        for d in ENV_DIMENSIONS:
            a, b = getattr(self, d), getattr(other, d)
            if a and b and a != b:
                diffs.add(d)
            elif (a and not b) or (b and not a):
                diffs.add(d)
        return diffs


def env_label_zh(dim: str, key: str | None) -> str:
    if not key:
        return "—"
    return ENV_LABELS_ZH.get(key, key)


# ---------------------------------------------------------------------------
# Purpose diversity rules
# ---------------------------------------------------------------------------

def evaluate_purpose_rules(
    recent_purposes: list[str],
    *,
    window: int = 5,
    forbid_same_within: int = 5,
) -> dict[str, Any]:
    """Evaluate what the next scene's purpose must avoid.

    Parameters
    ----------
    recent_purposes : list[str]
        Ordered most-recent-first list of scene purpose ids.
    window : int
        How many recent scenes to consider for family coverage.
    forbid_same_within : int
        Within this many scenes, the same purpose may not repeat.
    """
    if not recent_purposes:
        return {
            "forbid_purposes": [],
            "underused_families": sorted(PURPOSE_FAMILIES.keys()),
            "candidate_pool": list(all_purposes()),
        }

    forbid = list({p for p in recent_purposes[:forbid_same_within] if p})

    used_families = {
        family_of(p) for p in recent_purposes[:window] if p and family_of(p)
    }
    underused = [f for f in PURPOSE_FAMILIES.keys() if f not in used_families]

    # Candidate pool = everything not in forbid; prioritise purposes from
    # underused families first.
    prioritised: list[str] = []
    rest: list[str] = []
    for family_key, bucket in PURPOSE_FAMILIES.items():
        for purpose in bucket.keys():
            if purpose in forbid:
                continue
            if family_key in underused:
                prioritised.append(purpose)
            else:
                rest.append(purpose)

    return {
        "forbid_purposes": sorted(forbid),
        "underused_families": sorted(underused),
        "candidate_pool": prioritised + rest,
    }


# ---------------------------------------------------------------------------
# Environment diversity rules
# ---------------------------------------------------------------------------

def evaluate_env_rules(
    recent_envs: list[EnvVector],
    *,
    min_diff_vs_prev: int = 3,
    min_diff_vs_any_of_prev3: int = 2,
) -> dict[str, Any]:
    """Compute what the next scene's environment vector must avoid.

    Returns a dict including the concrete values the prior scene used
    (so they can be excluded) and the minimum number of dimensions that
    must change.
    """
    if not recent_envs:
        return {
            "prev_env": None,
            "prev_env_labels": None,
            "prev3_envs": [],
            "min_diff_vs_prev": min_diff_vs_prev,
            "min_diff_vs_any_of_prev3": min_diff_vs_any_of_prev3,
            "forbid_exact_matches": [],
        }

    prev = recent_envs[0]
    return {
        "prev_env": prev.as_dict(),
        "prev_env_labels": {
            d: env_label_zh(d, getattr(prev, d)) for d in ENV_DIMENSIONS
        },
        "prev3_envs": [e.as_dict() for e in recent_envs[:3]],
        "min_diff_vs_prev": min_diff_vs_prev,
        "min_diff_vs_any_of_prev3": min_diff_vs_any_of_prev3,
        "forbid_exact_matches": [e.as_dict() for e in recent_envs[:3]],
    }


# ---------------------------------------------------------------------------
# Same-location "emotional reframe" ledger
# ---------------------------------------------------------------------------

def location_visit_count(
    location: str | None,
    recent_scene_locations: Iterable[str | None],
) -> int:
    if not location:
        return 0
    return sum(1 for loc in recent_scene_locations if loc and loc == location)
