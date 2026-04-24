"""Reader Hype Engine — 爽感引擎 (Phase 0 core + Phase 1 budget primitives).

Targets the diagnosed "0% 爽感" gap: the existing pipeline strongly penalises
defects (L4/L5 gates) but never rewards — or even demands — the payoff beats
that keep readers hooked chapter after chapter (打脸/装逼/升级/逆袭/反转/搞笑/金手指).

This module mirrors ``pacing_engine.py`` in shape:
  * ``HYPE_DISTRIBUTION``           — target per-type distribution (sums to 1)
  * ``evaluate_hype_diversity``     — forbid consecutive same-type hype moments
  * ``HYPE_DENSITY_CURVE``          — percentile → (expected types, min count,
                                      intensity target), aligned with
                                      ``pacing_engine.BEAT_SHEET``
  * ``target_hype_for_chapter``     — chapter → band
  * ``score_hype``                  — 6-component intensity score [0, 10]
  * ``select_recipe_for_chapter``   — LRU recipe selection from a preset deck
  * ``pick_hype_for_chapter``       — the single public entry point used by
                                      ``prompt_constructor.build_chapter_prompt``

Phase 1 primitives (``HypeMoment``, ``HypeScheme``) live here so that the
invariants + diversity-budget extensions can import from a single module.

No LLM calls. No DB reads. Pure functions + frozen dataclasses.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Literal, Mapping

from bestseller.services.checker_schema import CheckerIssue, CheckerReport


# ---------------------------------------------------------------------------
# HypeType — 12 canonical payoff flavours.
# ---------------------------------------------------------------------------


class HypeType(str, Enum):
    FACE_SLAP = "face_slap"                      # 打脸
    POWER_REVEAL = "power_reveal"                # 装逼 / 亮底牌
    LEVEL_UP = "level_up"                        # 升级 / 突破
    REVERSAL = "reversal"                        # 翻盘 / 反转
    COUNTERATTACK = "counterattack"              # 反击 / 还击
    UNDERDOG_WIN = "underdog_win"                # 逆袭 / 扮猪吃虎
    GOLDEN_FINGER_REVEAL = "golden_finger_reveal"  # 金手指揭示
    COMEDIC_BEAT = "comedic_beat"                # 搞笑 / 吐槽
    REVENGE_CLOSURE = "revenge_closure"          # 复仇闭环
    CARESS_BY_FATE = "caress_by_fate"            # 际遇 / 奇遇
    STATUS_JUMP = "status_jump"                  # 身份跃升
    DOMINATION = "domination"                    # 碾压 / 吊打


# Recommended book-level distribution (sums to 1.0). Drives the "under-used"
# ordering in ``evaluate_hype_diversity`` — when a book has been heavy on
# ``FACE_SLAP`` and light on ``COMEDIC_BEAT``, the diversity suggester pushes
# COMEDIC_BEAT up.
HYPE_DISTRIBUTION: dict[HypeType, float] = {
    HypeType.FACE_SLAP:            0.15,
    HypeType.POWER_REVEAL:         0.12,
    HypeType.LEVEL_UP:             0.10,
    HypeType.REVERSAL:             0.08,
    HypeType.COUNTERATTACK:        0.10,
    HypeType.UNDERDOG_WIN:         0.08,
    HypeType.GOLDEN_FINGER_REVEAL: 0.08,
    HypeType.COMEDIC_BEAT:         0.10,
    HypeType.REVENGE_CLOSURE:      0.05,
    HypeType.CARESS_BY_FATE:       0.05,
    HypeType.STATUS_JUMP:          0.05,
    HypeType.DOMINATION:           0.04,
}


# ---------------------------------------------------------------------------
# HypeRecipe — per-preset "配方" that turns a HypeType into concrete beats.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HypeRecipe:
    """One concrete payoff pattern, authored per preset.

    ``key`` is globally unique across a project's recipe_deck so the LRU
    rotation (``select_recipe_for_chapter``) can dedupe by recipe even when
    two recipes share a ``hype_type``. ``forbidden_with`` lists other recipe
    keys that must not appear in the same chapter (e.g. two "大打脸" in one
    chapter reads as overkill).
    """

    key: str
    hype_type: HypeType
    trigger_keywords: tuple[str, ...]
    narrative_beats: tuple[str, ...]
    intensity_floor: float = 6.0
    cadence_hint: str = ""
    forbidden_with: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# HypeDensityBand — percentile window → expected types + min count + target.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HypeDensityBand:
    percentile_low: float
    percentile_high: float
    expected_types: tuple[HypeType, ...]
    min_count_per_chapter: int
    intensity_target: float  # 0-10
    notes: str


# 7-segment curve. Boundaries align with ``pacing_engine.BEAT_SHEET`` percentiles:
# 0.00 / 0.05 / 0.20 / 0.45 / 0.55 / 0.75 / 0.90 / 1.01 (sentinel upper).
HYPE_DENSITY_CURVE: tuple[HypeDensityBand, ...] = (
    HypeDensityBand(
        percentile_low=0.00,
        percentile_high=0.05,
        expected_types=(
            HypeType.FACE_SLAP,
            HypeType.POWER_REVEAL,
            HypeType.GOLDEN_FINGER_REVEAL,
        ),
        min_count_per_chapter=2,
        intensity_target=7.5,
        notes="黄金三章开局：必须立刻亮出金手指 / 第一个打脸 / 第一个亮底牌",
    ),
    HypeDensityBand(
        percentile_low=0.05,
        percentile_high=0.20,
        expected_types=(
            HypeType.FACE_SLAP,
            HypeType.POWER_REVEAL,
            HypeType.LEVEL_UP,
            HypeType.UNDERDOG_WIN,
        ),
        min_count_per_chapter=1,
        intensity_target=7.0,
        notes="世界展开：每章至少 1 个爽点峰值，保持开场执行强度",
    ),
    HypeDensityBand(
        percentile_low=0.20,
        percentile_high=0.45,
        expected_types=(
            HypeType.COUNTERATTACK,
            HypeType.STATUS_JUMP,
            HypeType.LEVEL_UP,
            HypeType.COMEDIC_BEAT,
        ),
        min_count_per_chapter=1,
        intensity_target=7.5,
        notes="卷一高潮 + 上升段：爽点类型多样化，引入搞笑调味",
    ),
    HypeDensityBand(
        percentile_low=0.45,
        percentile_high=0.55,
        expected_types=(
            HypeType.REVERSAL,
            HypeType.DOMINATION,
            HypeType.FACE_SLAP,
        ),
        min_count_per_chapter=1,
        intensity_target=8.5,
        notes="中点翻盘：本章必须有高强度爽点，推荐反转 / 碾压 / 大打脸",
    ),
    HypeDensityBand(
        percentile_low=0.55,
        percentile_high=0.75,
        expected_types=(
            HypeType.REVENGE_CLOSURE,
            HypeType.UNDERDOG_WIN,
            HypeType.COUNTERATTACK,
        ),
        min_count_per_chapter=1,
        intensity_target=7.5,
        notes="上升段 2 + All is Lost：旧仇闭环 + 逆袭节拍",
    ),
    HypeDensityBand(
        percentile_low=0.75,
        percentile_high=0.90,
        expected_types=(
            HypeType.LEVEL_UP,
            HypeType.CARESS_BY_FATE,
            HypeType.COUNTERATTACK,
            HypeType.STATUS_JUMP,
        ),
        min_count_per_chapter=1,
        intensity_target=8.0,
        notes="Dark Night + 反攻：奇遇 / 升级为终战铺垫",
    ),
    HypeDensityBand(
        percentile_low=0.90,
        percentile_high=1.01,
        expected_types=(
            HypeType.DOMINATION,
            HypeType.STATUS_JUMP,
            HypeType.REVERSAL,
            HypeType.POWER_REVEAL,
        ),
        min_count_per_chapter=2,
        intensity_target=9.0,
        notes="终战 + 新均衡：高强度双爽点收尾，STATUS_JUMP 完成身份跃升",
    ),
)


def target_hype_for_chapter(
    chapter_number: int,
    total_chapters: int,
    pacing_profile: str = "medium",
) -> HypeDensityBand:
    """Return the density band for this chapter, adjusted by pacing profile.

    ``pacing_profile`` shifts ``intensity_target`` by ±0.5 so fast-paced
    presets (e.g. 诡豪) get an extra push and slow-paced ones relax. Other
    band fields are returned as-is — only intensity bends.
    """

    total = max(total_chapters, chapter_number, 1)
    p = chapter_number / total
    base = HYPE_DENSITY_CURVE[-1]
    for band in HYPE_DENSITY_CURVE:
        if band.percentile_low <= p < band.percentile_high:
            base = band
            break

    profile = (pacing_profile or "medium").lower()
    if profile == "fast":
        shift = 0.5
    elif profile == "slow":
        shift = -0.5
    else:
        shift = 0.0
    if shift == 0.0:
        return base
    return HypeDensityBand(
        percentile_low=base.percentile_low,
        percentile_high=base.percentile_high,
        expected_types=base.expected_types,
        min_count_per_chapter=base.min_count_per_chapter,
        intensity_target=max(0.0, min(10.0, base.intensity_target + shift)),
        notes=base.notes,
    )


# ---------------------------------------------------------------------------
# Hype intensity scoring — 6 components, mirrors ``pacing_engine.score_tension``.
# ---------------------------------------------------------------------------


HYPE_SCORE_WEIGHTS: dict[str, float] = {
    "setup_contrast":    0.20,  # 前戏反差（静→爆、压制→翻盘）
    "reveal_clarity":    0.15,  # 亮牌清晰（能力/身份/底牌毫不含糊）
    "audience_reaction": 0.15,  # 旁观者反应（对手脸色/围观哗然）
    "stakes_lift":       0.20,  # 风险抬升（对手更强、代价更重）
    "sensory_punch":     0.15,  # 感官冲击（视听嗅触的锋利细节）
    "pacing_crisp":      0.15,  # 节奏干净（短句、重拳、不拖沓）
}


def score_hype(components: Mapping[str, float]) -> float:
    """Compute a 0-10 hype intensity score from 6 components.

    Each component should be in [0, 10]; missing keys default to 5.0. The
    result is rounded to 2 decimals so score comparisons stay stable across
    floating-point round-trips.
    """

    total = 0.0
    for key, weight in HYPE_SCORE_WEIGHTS.items():
        v = float(components.get(key, 5.0))
        v = max(0.0, min(10.0, v))
        total += v * weight
    return round(total, 2)


# ---------------------------------------------------------------------------
# Diversity — forbid same-type in consecutive chapters, suggest under-used.
# ---------------------------------------------------------------------------


def evaluate_hype_diversity(
    recent_hype_types: list[HypeType | str | None],
    recent_recipe_keys: list[str | None] | None = None,
    *,
    forbid_run_length: int = 2,
    recipe_memory: int = 5,
) -> dict[str, Any]:
    """Decide which hype types and recipe keys the next chapter should avoid.

    Parameters
    ----------
    recent_hype_types :
        Most-recent-first list of HypeType values (enum members or their
        ``.value`` strings or None). Drives the "no 2 consecutive same-type"
        rule — stricter than cliffhanger's 3-run because a HypeType is the
        dominant flavour of the chapter.
    recent_recipe_keys :
        Most-recent-first list of recipe keys used. Keys appearing in the
        last ``recipe_memory`` chapters are forbidden outright (prevents
        recipe staleness even when the HypeType rotates).
    forbid_run_length :
        If the last N chapters share the same HypeType, that type is
        forbidden for the next chapter. Defaults to 2 (stricter than
        cliffhangers) because readers tune out repeat payoffs faster.
    recipe_memory :
        How many recent chapters' recipe keys to forbid.
    """

    forbid_types: set[str] = set()
    forbid_recipes: set[str] = set()

    normalised = [_normalise_type(t) for t in recent_hype_types]

    # Rule 1: immediate repeat — always forbid the most recent type.
    if normalised and normalised[0]:
        forbid_types.add(normalised[0])

    # Rule 2: N-run of the same type — forbid the type.
    last_run = normalised[:forbid_run_length]
    last_run_non_null = [t for t in last_run if t]
    if (
        len(last_run_non_null) == forbid_run_length
        and len(set(last_run_non_null)) == 1
    ):
        forbid_types.add(last_run_non_null[0])

    if recent_recipe_keys:
        for key in recent_recipe_keys[:recipe_memory]:
            if key:
                forbid_recipes.add(str(key))

    # Suggested ordering: under-used vs HYPE_DISTRIBUTION first.
    counter: Counter[str] = Counter(t for t in normalised[:20] if t)
    total = max(sum(counter.values()), 1)
    deviation: list[tuple[float, str]] = []
    for hype_type, target_frac in HYPE_DISTRIBUTION.items():
        observed = counter.get(hype_type.value, 0) / total
        deviation.append((target_frac - observed, hype_type.value))
    deviation.sort(reverse=True)
    suggested = [v for _, v in deviation if v not in forbid_types]

    return {
        "forbid_types": sorted(forbid_types),
        "forbid_recipe_keys": sorted(forbid_recipes),
        "recent_types": [t for t in normalised[:forbid_run_length] if t],
        "suggested": suggested[:5],
    }


# ---------------------------------------------------------------------------
# Phase A1 — Unified CheckerReport adapter.
# ---------------------------------------------------------------------------


def build_hype_checker_report(
    *,
    chapter: int,
    observed_intensity: float | None,
    target_intensity: float,
    observed_count: int,
    min_count: int,
    missing_types: tuple[str, ...] = (),
    diversity: dict[str, Any] | None = None,
    golden_three_weak: bool = False,
) -> CheckerReport:
    """Wrap hype-engine evaluations into the Phase A1 schema.

    Hype issues are *soft* — a quiet arc can legitimately dip below target
    density if the book plans a bigger swing afterward. Golden-three weak
    (chapters 1-3) is bumped to ``high`` because the opening-chapter
    commercial bar is non-negotiable by genre convention.
    """

    issues: list[CheckerIssue] = []

    if observed_count < min_count:
        issues.append(
            CheckerIssue(
                id="SOFT_HYPE_DENSITY_LOW",
                type="hype",
                severity="high" if chapter <= 3 else "medium",
                location="整章",
                description=(
                    f"本章爽点数 {observed_count} 低于目标下限 {min_count}"
                ),
                suggestion="提升爽点密度或重新选择配方（recipe）",
                can_override=True,
                allowed_rationales=("ARC_TIMING", "GENRE_CONVENTION"),
            )
        )

    if observed_intensity is not None and observed_intensity + 1.5 < target_intensity:
        issues.append(
            CheckerIssue(
                id="SOFT_HYPE_INTENSITY_LOW",
                type="hype",
                severity="medium",
                location="整章",
                description=(
                    f"爽点强度 {observed_intensity:.2f} 低于目标 {target_intensity:.2f}"
                ),
                suggestion="增强执行：更具体的打脸细节 / 更大反差的装逼",
                can_override=True,
                allowed_rationales=("ARC_TIMING", "EDITORIAL_INTENT"),
            )
        )

    if missing_types:
        issues.append(
            CheckerIssue(
                id="SOFT_HYPE_TYPE_MISSING",
                type="hype",
                severity="medium",
                location="整章",
                description=f"本章未覆盖到期望类型：{', '.join(missing_types)}",
                suggestion=f"插入至少一个 {missing_types[0]} 节拍",
                can_override=True,
                allowed_rationales=("ARC_TIMING", "GENRE_CONVENTION"),
            )
        )

    if diversity and diversity.get("forbid_types"):
        issues.append(
            CheckerIssue(
                id="SOFT_HYPE_REPEAT",
                type="hype",
                severity="medium",
                location="整章",
                description=f"最近连续出现类型：{diversity['forbid_types']}",
                suggestion=(
                    f"建议切换至：{', '.join(diversity.get('suggested', [])[:3])}"
                ),
                can_override=True,
                allowed_rationales=("ARC_TIMING", "EDITORIAL_INTENT"),
            )
        )

    if golden_three_weak:
        issues.append(
            CheckerIssue(
                id="SOFT_GOLDEN_THREE_WEAK",
                type="hype",
                severity="high",
                location="黄金三章",
                description="黄金三章爽点强度不达标，可能影响留存",
                suggestion="强化开篇的金手指亮相 / 首次打脸执行细节",
                can_override=True,
                allowed_rationales=("GENRE_CONVENTION", "EDITORIAL_INTENT"),
            )
        )

    passed = not issues
    penalty = sum(
        {"critical": 25, "high": 15, "medium": 8, "low": 3}[i.severity] for i in issues
    )
    score = max(0, 100 - penalty)
    summary = (
        "爽感引擎审查通过" if passed
        else f"爽感引擎发现 {len(issues)} 条软建议，可通过 Override Contract 签署"
    )
    return CheckerReport(
        agent="hype-engine",
        chapter=chapter,
        overall_score=score,
        passed=passed,
        issues=tuple(issues),
        metrics={
            "observed_count": observed_count,
            "min_count": min_count,
            "observed_intensity": observed_intensity,
            "target_intensity": target_intensity,
            "missing_types": list(missing_types),
        },
        summary=summary,
    )


def _normalise_type(value: HypeType | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, HypeType):
        return value.value
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Recipe selection — LRU with preference for expected_types in the band.
# ---------------------------------------------------------------------------


def select_recipe_for_chapter(
    band: HypeDensityBand,
    deck: Iterable[HypeRecipe],
    recent_recipe_keys: list[str | None] | None = None,
    recent_hype_types: list[HypeType | str | None] | None = None,
    *,
    recipe_memory: int = 5,
) -> HypeRecipe | None:
    """Pick the best recipe from ``deck`` given the band and recent history.

    Preference order:
      1. Recipes whose ``hype_type`` matches ``band.expected_types`` AND
         whose ``key`` has not been used in the last ``recipe_memory``
         chapters AND whose ``hype_type`` is not in the forbid-list.
      2. Any recipe not in the forbid-list and not recently used.
      3. LRU fallback — the recipe whose key is furthest back in history.
      4. ``None`` when the deck is empty.

    The caller is expected to receive the forbid-list via
    ``evaluate_hype_diversity``; here we only take raw recent_* for
    simplicity so the function stays easy to unit-test.
    """

    deck_list = list(deck)
    if not deck_list:
        return None

    diversity = evaluate_hype_diversity(
        recent_hype_types or [],
        recent_recipe_keys or [],
        recipe_memory=recipe_memory,
    )
    forbid_types = set(diversity["forbid_types"])
    forbid_recipes = set(diversity["forbid_recipe_keys"])

    expected_types = set(t.value for t in band.expected_types)

    def tier(recipe: HypeRecipe) -> int:
        """Lower tier wins."""
        if recipe.key in forbid_recipes:
            return 3
        if recipe.hype_type.value in forbid_types:
            return 2
        if recipe.hype_type.value in expected_types:
            return 0
        return 1

    # Recency index — most-recent keys get the highest score. Unused keys
    # default to 0 so they rank as "least recently used" and are preferred
    # by the ascending sort below.
    recency: dict[str, int] = {}
    keys = list(recent_recipe_keys or [])
    for idx, key in enumerate(keys):
        if key and str(key) not in recency:
            recency[str(key)] = len(keys) - idx

    def recency_score(recipe: HypeRecipe) -> int:
        return recency.get(recipe.key, 0)

    deck_list.sort(key=lambda r: (tier(r), recency_score(r)))
    chosen = deck_list[0]
    # If every recipe is in forbid_recipes we still return the LRU one —
    # the deck was exhausted and no-op is worse than a soft repeat.
    return chosen


def pick_hype_for_chapter(
    band: HypeDensityBand,
    deck: Iterable[HypeRecipe],
    recent_hype_types: list[HypeType | str | None] | None = None,
    recent_recipe_keys: list[str | None] | None = None,
    *,
    recipe_memory: int = 5,
) -> tuple[HypeType | None, HypeRecipe | None, float]:
    """Single public entry-point called by ``prompt_constructor``.

    Returns ``(hype_type, recipe, intensity_target)``.
      * ``hype_type`` — the enum chosen for this chapter (None when deck
        is empty AND the band has no expected types).
      * ``recipe`` — optional recipe from the deck (None when deck empty).
      * ``intensity_target`` — already pacing-profile adjusted by the
        caller (``band.intensity_target``); returned here as a convenience
        so prompt builders don't need to reach back into the band.
    """

    recipe = select_recipe_for_chapter(
        band,
        deck,
        recent_recipe_keys=recent_recipe_keys,
        recent_hype_types=recent_hype_types,
        recipe_memory=recipe_memory,
    )
    if recipe is not None:
        hype_type: HypeType | None = recipe.hype_type
        intensity = max(band.intensity_target, recipe.intensity_floor)
    else:
        # No deck — fall back to "first expected type the caller hasn't
        # used back-to-back".
        forbid = set(
            evaluate_hype_diversity(
                recent_hype_types or [],
                recent_recipe_keys or [],
                recipe_memory=recipe_memory,
            )["forbid_types"]
        )
        hype_type = next(
            (t for t in band.expected_types if t.value not in forbid),
            band.expected_types[0] if band.expected_types else None,
        )
        intensity = band.intensity_target

    return hype_type, recipe, round(intensity, 2)


# ---------------------------------------------------------------------------
# Phase 1 primitives — HypeMoment (budget row) + HypeScheme (invariant slice).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HypeMoment:
    """One recorded hype peak, keyed by chapter.

    Persisted inside ``DiversityBudget.hype_moments`` as JSONB. The recipe
    key is the string-safe way to dedupe across recipes that share a
    hype_type.
    """

    chapter_no: int
    hype_type: HypeType
    recipe_key: str | None
    intensity: float


@dataclass(frozen=True)
class HypeScheme:
    """Per-project hype contract, sits inside ``ProjectInvariants``.

    * ``recipe_deck`` — the authored recipes from the writing preset.
      Empty tuple → engine no-op (used by legacy projects that predate
      migration 0019 so they never touch prompts, checks, or scorecards).
    * ``comedic_beat_density_target`` — [0, 1]; what fraction of chapters
      should contain at least one ``COMEDIC_BEAT``. Preset-declared.
    * ``payoff_window_chapters`` — for Phase 3 SetupPayoffTracker; default
      5 chapters from humiliation → counterattack.
    * ``reader_promise`` / ``selling_points`` / ``chapter_hook_strategy`` /
      ``hook_keywords`` — free-text fields pulled from the writing preset
      and surfaced to the LLM via ``prompt_constructor``.
    """

    recipe_deck: tuple[HypeRecipe, ...] = ()
    comedic_beat_density_target: float = 0.1
    payoff_window_chapters: int = 5
    reader_promise: str = ""
    selling_points: tuple[str, ...] = ()
    hook_keywords: tuple[str, ...] = ()
    chapter_hook_strategy: str = ""
    min_hype_per_chapter: int = 1  # Can be 0 for slow literary presets.

    @property
    def is_empty(self) -> bool:
        """True when the engine should skip rendering / checking / scoring."""
        return (
            not self.recipe_deck
            and not self.reader_promise
            and not self.selling_points
        )


def hype_scheme_to_dict(scheme: HypeScheme) -> dict[str, Any]:
    """Serialize a ``HypeScheme`` to JSONB-safe primitives."""

    return {
        "recipe_deck": [_recipe_to_dict(r) for r in scheme.recipe_deck],
        "comedic_beat_density_target": scheme.comedic_beat_density_target,
        "payoff_window_chapters": scheme.payoff_window_chapters,
        "reader_promise": scheme.reader_promise,
        "selling_points": list(scheme.selling_points),
        "hook_keywords": list(scheme.hook_keywords),
        "chapter_hook_strategy": scheme.chapter_hook_strategy,
        "min_hype_per_chapter": scheme.min_hype_per_chapter,
    }


def hype_scheme_from_dict(data: Mapping[str, Any] | None) -> HypeScheme:
    """Deserialize a ``HypeScheme``; missing / malformed data → empty scheme."""

    if not data:
        return HypeScheme()

    deck_raw = data.get("recipe_deck") or ()
    recipes: list[HypeRecipe] = []
    for row in deck_raw:
        recipe = _recipe_from_dict(row)
        if recipe is not None:
            recipes.append(recipe)

    return HypeScheme(
        recipe_deck=tuple(recipes),
        comedic_beat_density_target=float(
            data.get("comedic_beat_density_target", 0.1)
        ),
        payoff_window_chapters=int(data.get("payoff_window_chapters", 5)),
        reader_promise=str(data.get("reader_promise") or ""),
        selling_points=tuple(data.get("selling_points") or ()),
        hook_keywords=tuple(data.get("hook_keywords") or ()),
        chapter_hook_strategy=str(data.get("chapter_hook_strategy") or ""),
        min_hype_per_chapter=int(data.get("min_hype_per_chapter", 1)),
    )


def _recipe_to_dict(recipe: HypeRecipe) -> dict[str, Any]:
    return {
        "key": recipe.key,
        "hype_type": recipe.hype_type.value,
        "trigger_keywords": list(recipe.trigger_keywords),
        "narrative_beats": list(recipe.narrative_beats),
        "intensity_floor": recipe.intensity_floor,
        "cadence_hint": recipe.cadence_hint,
        "forbidden_with": list(recipe.forbidden_with),
    }


def _recipe_from_dict(data: Mapping[str, Any] | None) -> HypeRecipe | None:
    if not isinstance(data, Mapping):
        return None
    try:
        return HypeRecipe(
            key=str(data["key"]),
            hype_type=HypeType(data["hype_type"]),
            trigger_keywords=tuple(data.get("trigger_keywords") or ()),
            narrative_beats=tuple(data.get("narrative_beats") or ()),
            intensity_floor=float(data.get("intensity_floor", 6.0)),
            cadence_hint=str(data.get("cadence_hint") or ""),
            forbidden_with=tuple(data.get("forbidden_with") or ()),
        )
    except (KeyError, TypeError, ValueError):
        return None


def hype_scheme_from_preset_overrides(
    overrides: Mapping[str, Any] | None,
) -> HypeScheme:
    """Build a ``HypeScheme`` from a writing-preset ``writing_profile_overrides`` dict.

    The preset stores hype-specific config under the ``hype`` namespace
    (``recipe_deck``, ``comedic_beat_density_target``, ``min_hype_per_chapter``,
    ``payoff_window_chapters``) and the reader-facing framing under the
    ``market`` namespace (``reader_promise``, ``selling_points``,
    ``hook_keywords``, ``chapter_hook_strategy``). Both namespaces feed the
    same ``HypeScheme`` so prompt construction sees one contract.

    Missing or malformed input → empty ``HypeScheme`` (engine no-op).
    """

    if not isinstance(overrides, Mapping):
        return HypeScheme()

    hype = overrides.get("hype") if isinstance(overrides.get("hype"), Mapping) else {}
    market = (
        overrides.get("market") if isinstance(overrides.get("market"), Mapping) else {}
    )

    recipes: list[HypeRecipe] = []
    for row in hype.get("recipe_deck") or ():
        recipe = _recipe_from_dict(row)
        if recipe is not None:
            recipes.append(recipe)

    return HypeScheme(
        recipe_deck=tuple(recipes),
        comedic_beat_density_target=float(
            hype.get("comedic_beat_density_target", 0.1)
        ),
        payoff_window_chapters=int(hype.get("payoff_window_chapters", 5)),
        min_hype_per_chapter=int(hype.get("min_hype_per_chapter", 1)),
        reader_promise=str(market.get("reader_promise") or ""),
        selling_points=tuple(market.get("selling_points") or ()),
        hook_keywords=tuple(market.get("hook_keywords") or ()),
        chapter_hook_strategy=str(market.get("chapter_hook_strategy") or ""),
    )


def hype_moment_to_dict(moment: HypeMoment) -> dict[str, Any]:
    return {
        "chapter_no": moment.chapter_no,
        "hype_type": moment.hype_type.value,
        "recipe_key": moment.recipe_key,
        "intensity": moment.intensity,
    }


def hype_moment_from_dict(data: Mapping[str, Any] | None) -> HypeMoment | None:
    if not isinstance(data, Mapping):
        return None
    try:
        return HypeMoment(
            chapter_no=int(data["chapter_no"]),
            hype_type=HypeType(data["hype_type"]),
            recipe_key=(
                str(data["recipe_key"]) if data.get("recipe_key") else None
            ),
            intensity=float(data.get("intensity", 0.0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Phase 2 primitives — classify_hype + extract_ending_sentence (basic).
# ---------------------------------------------------------------------------


_HYPE_KEYWORDS_ZH: dict[HypeType, tuple[str, ...]] = {
    HypeType.FACE_SLAP: (
        "打脸", "羞辱", "啪", "耳光", "僵住", "脸色铁青", "脸色煞白",
        "愣在原地", "哑口无言", "噤声",
    ),
    HypeType.POWER_REVEAL: (
        "亮出", "亮牌", "列阵", "真身", "本尊", "显露", "展露",
        "亮相", "一掌压下",
    ),
    HypeType.LEVEL_UP: (
        "突破", "晋阶", "进阶", "暴涨", "蜕变", "凝聚", "升华", "飞跃",
    ),
    HypeType.REVERSAL: (
        "反转", "反杀", "翻盘", "逆转", "伪装", "假意", "暗中", "将计就计",
    ),
    HypeType.COUNTERATTACK: (
        "反击", "反咬", "还击", "回敬", "以彼之道", "反手", "反制",
    ),
    HypeType.UNDERDOG_WIN: (
        "扮猪", "低估", "被小看", "被轻视", "翻身", "掀桌", "扭转乾坤",
    ),
    HypeType.GOLDEN_FINGER_REVEAL: (
        "金手指", "解锁", "觉醒", "激活", "开启", "金光", "神识", "金色",
    ),
    HypeType.COMEDIC_BEAT: (
        "吐槽", "嘀咕", "翻白眼", "无语", "冷笑话", "抽搐", "揶揄", "打趣",
    ),
    HypeType.REVENGE_CLOSURE: (
        "复仇", "了结", "旧仇", "宿怨", "结清", "清算", "总账",
    ),
    HypeType.CARESS_BY_FATE: (
        "奇遇", "际遇", "机缘", "契合", "认主", "感应", "机遇",
    ),
    HypeType.STATUS_JUMP: (
        "跃升", "登顶", "名册", "跻身", "跃居", "新晋", "晋级", "更上",
    ),
    HypeType.DOMINATION: (
        "碾压", "吊打", "压制", "屠戮", "镇压", "横扫", "无可抵挡",
    ),
}


_HYPE_KEYWORDS_EN: dict[HypeType, tuple[str, ...]] = {
    HypeType.FACE_SLAP: (
        "humiliated", "slapped", "stunned", "silenced", "dumbstruck",
        "lost her voice", "lost his voice",
    ),
    HypeType.POWER_REVEAL: (
        "revealed", "unveiled", "summoned", "manifested", "arrayed",
    ),
    HypeType.LEVEL_UP: (
        "ascended", "leveled up", "breakthrough", "transcended",
        "surged in power",
    ),
    HypeType.REVERSAL: (
        "reversed", "turned the tables", "twisted", "flipped",
    ),
    HypeType.COUNTERATTACK: (
        "counterattack", "retaliated", "struck back", "repaid",
    ),
    HypeType.UNDERDOG_WIN: (
        "underdog", "underestimated", "overcame", "turned the tide",
    ),
    HypeType.GOLDEN_FINGER_REVEAL: (
        "unlocked", "awakened", "activated", "cheat code",
    ),
    HypeType.COMEDIC_BEAT: (
        "mocked", "quipped", "smirked", "rolled his eyes", "dry tone",
        "dry humor", "deadpan",
    ),
    HypeType.REVENGE_CLOSURE: (
        "avenged", "settled old scores", "closed the circle",
    ),
    HypeType.CARESS_BY_FATE: (
        "fateful", "destiny", "resonated", "encountered",
    ),
    HypeType.STATUS_JUMP: (
        "ranked", "joined the ranks", "elite list", "rose to",
    ),
    HypeType.DOMINATION: (
        "dominated", "crushed", "overpowered", "subjugated",
    ),
}


def _keywords_for_language(language: str) -> dict[HypeType, tuple[str, ...]]:
    return (
        _HYPE_KEYWORDS_ZH
        if (language or "").lower().startswith("zh")
        else _HYPE_KEYWORDS_EN
    )


def classify_hype(
    text: str,
    language: str = "zh-CN",
    *,
    segment: Literal["full", "head", "tail"] = "full",
    head_chars: int = 1200,
    tail_chars: int = 1500,
) -> tuple[HypeType, float] | None:
    """Classify the dominant HypeType of ``text``.

    Returns ``(hype_type, confidence_score)`` where ``confidence_score`` is
    a rough 0-10 estimate based on keyword hit count. Returns ``None`` when
    no keyword scores at all (caller must treat that as "unclassified", not
    as "no hype" — audit checks use presence of assigned recipe keywords as
    the primary signal, classifier is the fallback).

    ``segment`` lets callers scope the classifier:
      * "full" — whole chapter (default; used by audit_loop)
      * "head" — first ``head_chars`` characters (used by GoldenThreeCheck)
      * "tail" — last ``tail_chars`` characters (used by audit to detect
                 "hype hogging the ending" anti-pattern)
    """

    if not text:
        return None

    if segment == "head":
        sample = text[:head_chars]
    elif segment == "tail":
        sample = text[-tail_chars:]
    else:
        sample = text

    if not sample.strip():
        return None

    table = _keywords_for_language(language)
    is_en = (language or "").lower().startswith("en")
    haystack = sample.lower() if is_en else sample

    scores: dict[HypeType, int] = {}
    for hype_type, keywords in table.items():
        hits = 0
        for kw in keywords:
            needle = kw.lower() if is_en else kw
            hits += haystack.count(needle)
        if hits:
            scores[hype_type] = hits

    if not scores:
        return None

    # Winner by hit count; confidence = min(hits, 10) as a 0-10 proxy.
    best_type, best_hits = max(scores.items(), key=lambda kv: kv[1])
    confidence = float(min(best_hits, 10))
    return best_type, confidence


# Chinese sentence terminators (including full-width and half-width variants).
_ENDING_TERMINATORS_ZH = "。！？…"
_ENDING_TERMINATORS_EN = ".!?"


def extract_ending_sentence(text: str, language: str = "zh-CN") -> str:
    """Return the final sentence of ``text`` for EndingSentenceImpactCheck.

    Walks back from the last non-whitespace character to the nearest
    sentence terminator, returning the trailing clause. Falls back to the
    last non-empty line when no terminator is found (e.g. truncated draft).
    """

    if not text or not text.strip():
        return ""

    trimmed = text.rstrip()
    terminators = (
        _ENDING_TERMINATORS_ZH
        if (language or "").lower().startswith("zh")
        else _ENDING_TERMINATORS_EN
    )

    # Walk past any trailing terminator to find the real content.
    end = len(trimmed)
    while end > 0 and trimmed[end - 1] in terminators + " \n\t":
        end -= 1
    # Now find the start of the final sentence: nearest prior terminator.
    start = end
    while start > 0 and trimmed[start - 1] not in terminators + "\n":
        start -= 1
    sentence = trimmed[start:end].strip()
    if sentence:
        return sentence

    # Fallback: last non-empty line.
    for line in reversed(trimmed.splitlines()):
        if line.strip():
            return line.strip()
    return ""


# ---------------------------------------------------------------------------
# Phase 3 primitives — GoldenFingerLadder (preset-declared + growth-curve fallback).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenFingerRung:
    """One step on the golden-finger capability ladder.

    ``unlock_percentile`` is a half-open [low, high) window keyed to a
    chapter's percentile position in the novel. ``hype_type_anchor`` is
    usually ``GOLDEN_FINGER_REVEAL`` (the unveil) or ``LEVEL_UP`` (the
    power surge); prompt construction uses it to bias recipe selection
    in the unlock chapter. ``signal_keywords`` are optional — empty on
    engine-extracted rungs so validators don't over-constrain prose for
    projects that didn't author their own ladder.
    """

    rung_index: int
    unlock_percentile: tuple[float, float]
    capability: str
    signal_keywords: tuple[str, ...] = ()
    hype_type_anchor: HypeType = HypeType.GOLDEN_FINGER_REVEAL
    unlock_chapter_hint: int | None = None


@dataclass(frozen=True)
class GoldenFingerLadder:
    """Ordered capability rungs + provenance.

    ``source``:
      * ``"preset_declared"`` — the writing preset shipped an explicit
        ladder; trust the capabilities, anchors, and keywords verbatim.
      * ``"engine_extracted"`` — built from ``character.growth_curve`` by
        ``extract_ladder_from_growth_curve`` as a fallback so legacy
        presets still get a ladder. Prompt/validator consumers should
        loosen any keyword-hit expectations for extracted rungs.
    """

    rungs: tuple[GoldenFingerRung, ...]
    source: Literal["preset_declared", "engine_extracted"]

    @property
    def is_empty(self) -> bool:
        return not self.rungs

    def rung_for_chapter(
        self, chapter_no: int, total_chapters: int
    ) -> GoldenFingerRung | None:
        """Return the rung whose percentile window covers ``chapter_no``.

        Matching is inclusive on the low boundary, exclusive on the high
        boundary so a chapter sitting exactly on a rung boundary belongs
        to the upper rung. The final rung is special-cased: its high
        boundary is treated as inclusive so percentile 1.0 maps to the
        last rung rather than returning ``None``. ``unlock_chapter_hint``
        takes precedence when set — a rung authored with
        ``unlock_chapter_hint=7`` claims chapter 7 regardless of
        percentile.
        """

        if not self.rungs or chapter_no <= 0 or total_chapters <= 0:
            return None

        for rung in self.rungs:
            if rung.unlock_chapter_hint == chapter_no:
                return rung

        percentile = chapter_no / total_chapters
        last_index = len(self.rungs) - 1
        for idx, rung in enumerate(self.rungs):
            low, high = rung.unlock_percentile
            if idx == last_index:
                if low <= percentile <= high + 1e-9:
                    return rung
            else:
                if low <= percentile < high:
                    return rung
        return None


def extract_ladder_from_growth_curve(
    growth_curve: str,
    total_chapters: int,
) -> GoldenFingerLadder:
    """Derive a ``GoldenFingerLadder`` from a preset's ``growth_curve`` string.

    Splits on either ``->`` or the full-width arrow variant; trims
    whitespace and drops empty segments. Percentile windows are evenly
    distributed across ``[0, 1]`` with the last window closed at 1.0.
    ``signal_keywords`` is empty (too noisy to guess from free text), and
    the anchor alternates between ``GOLDEN_FINGER_REVEAL`` (the unveil)
    and ``LEVEL_UP`` (the climb) starting from rung 1 so the sequence
    reads as a repeating unveil → climb → unveil → climb cadence.

    A blank or single-segment ``growth_curve`` still returns an empty
    ladder — a one-stage trajectory has no "rung" to unlock.
    """

    if not growth_curve or total_chapters <= 0:
        return GoldenFingerLadder(rungs=(), source="engine_extracted")

    # Accept both the ASCII and full-width arrow separators. ``-->`` is
    # redundant with ``->`` once we split; ``→`` is the single-char
    # full-width variant occasionally used by presets.
    normalized = growth_curve.replace("→", "->")
    raw_segments = [seg.strip() for seg in normalized.split("->")]
    segments = [s for s in raw_segments if s]
    if len(segments) < 2:
        return GoldenFingerLadder(rungs=(), source="engine_extracted")

    n = len(segments)
    rungs: list[GoldenFingerRung] = []
    for idx, capability in enumerate(segments):
        low = idx / n
        high = (idx + 1) / n if idx < n - 1 else 1.0
        anchor = (
            HypeType.GOLDEN_FINGER_REVEAL if idx % 2 == 0 else HypeType.LEVEL_UP
        )
        rungs.append(
            GoldenFingerRung(
                rung_index=idx + 1,
                unlock_percentile=(round(low, 6), round(high, 6)),
                capability=capability,
                signal_keywords=(),
                hype_type_anchor=anchor,
                unlock_chapter_hint=None,
            )
        )

    return GoldenFingerLadder(rungs=tuple(rungs), source="engine_extracted")


def _rung_to_dict(rung: GoldenFingerRung) -> dict[str, Any]:
    return {
        "rung_index": rung.rung_index,
        "unlock_percentile": list(rung.unlock_percentile),
        "capability": rung.capability,
        "signal_keywords": list(rung.signal_keywords),
        "hype_type_anchor": rung.hype_type_anchor.value,
        "unlock_chapter_hint": rung.unlock_chapter_hint,
    }


def _rung_from_dict(data: Mapping[str, Any] | None) -> GoldenFingerRung | None:
    if not isinstance(data, Mapping):
        return None
    try:
        percentile_raw = data.get("unlock_percentile") or (0.0, 1.0)
        low, high = float(percentile_raw[0]), float(percentile_raw[1])
        return GoldenFingerRung(
            rung_index=int(data["rung_index"]),
            unlock_percentile=(low, high),
            capability=str(data["capability"]),
            signal_keywords=tuple(data.get("signal_keywords") or ()),
            hype_type_anchor=HypeType(
                data.get("hype_type_anchor")
                or HypeType.GOLDEN_FINGER_REVEAL.value
            ),
            unlock_chapter_hint=(
                int(data["unlock_chapter_hint"])
                if data.get("unlock_chapter_hint") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def golden_finger_ladder_to_dict(ladder: GoldenFingerLadder) -> dict[str, Any]:
    return {
        "rungs": [_rung_to_dict(r) for r in ladder.rungs],
        "source": ladder.source,
    }


def golden_finger_ladder_from_dict(
    data: Mapping[str, Any] | None,
) -> GoldenFingerLadder:
    if not data:
        return GoldenFingerLadder(rungs=(), source="engine_extracted")
    rungs_raw = data.get("rungs") or ()
    rungs = tuple(
        r
        for r in (_rung_from_dict(row) for row in rungs_raw)
        if r is not None
    )
    source_raw = str(data.get("source") or "engine_extracted")
    source: Literal["preset_declared", "engine_extracted"] = (
        "preset_declared"
        if source_raw == "preset_declared"
        else "engine_extracted"
    )
    return GoldenFingerLadder(rungs=rungs, source=source)


__all__ = [
    "HypeType",
    "HypeRecipe",
    "HypeDensityBand",
    "HypeMoment",
    "HypeScheme",
    "GoldenFingerRung",
    "GoldenFingerLadder",
    "HYPE_DISTRIBUTION",
    "HYPE_DENSITY_CURVE",
    "HYPE_SCORE_WEIGHTS",
    "target_hype_for_chapter",
    "score_hype",
    "evaluate_hype_diversity",
    "select_recipe_for_chapter",
    "pick_hype_for_chapter",
    "classify_hype",
    "extract_ending_sentence",
    "extract_ladder_from_growth_curve",
    "hype_scheme_to_dict",
    "hype_scheme_from_dict",
    "hype_scheme_from_preset_overrides",
    "hype_moment_to_dict",
    "hype_moment_from_dict",
    "golden_finger_ladder_to_dict",
    "golden_finger_ladder_from_dict",
]
