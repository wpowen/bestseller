"""Pacing engine (Stage D) — cliffhanger taxonomy + beat position + tension targets.

Targets the diagnosed "节奏停滞" symptom: across the 89-chapter sample the
book stays at the same tension register, reuses the same cliffhanger type
chapter after chapter, and pushes key reveals (mother identity, core-origin)
way past their planned percentile.

Exposes:
  * `CLIFFHANGER_TYPES` — 7 canonical hook types with recommended distribution
  * `evaluate_hook_diversity` — forbid consecutive same-type hooks
  * `target_beat_for_chapter` — maps chapter → beat position on master sheet
  * `target_tension_for_chapter` — maps chapter → expected 0-10 tension score
  * `score_tension` — 6-component tension score (planner-supplied inputs)

No LLM calls. No DB reads.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Cliffhanger (章末钩子) taxonomy
# ---------------------------------------------------------------------------

CLIFFHANGER_TYPES: dict[str, str] = {
    "suspense":    "悬念型（一个大问题悬着）",
    "twist":       "转折型（预期方向突然反转）",
    "crisis":      "危机型（迫在眉睫的具体威胁）",
    "revelation":  "启示型（揭开一片关键真相）",
    "sudden":      "突发型（突然发生某事，未定性）",
    "emotional":   "情感型（关系/内心重击）",
    "philosophical": "哲思型（留一个开放性 question）",
}

# Recommended book-level distribution (sums to 1.0)
CLIFFHANGER_DISTRIBUTION: dict[str, float] = {
    "suspense":    0.25,
    "twist":       0.15,
    "crisis":      0.20,
    "revelation":  0.15,
    "sudden":      0.10,
    "emotional":   0.10,
    "philosophical": 0.05,
}


def evaluate_hook_diversity(
    recent_hook_types: list[str | None],
    *,
    forbid_run_length: int = 3,
) -> dict[str, Any]:
    """Decide what hook types the next chapter should avoid.

    Parameters
    ----------
    recent_hook_types : list[str | None]
        Ordered most-recent-first list of hook_type strings (may contain None).
    forbid_run_length : int
        If the last N chapters have the same hook type, that type must be
        forbidden for the next chapter.

    Returns
    -------
    dict with keys:
      * forbid : list of forbidden hook_type ids
      * recent : the input (echoed for prompt rendering)
      * suggested : a list of hook types prioritised by distribution under-use
    """
    forbid: set[str] = set()
    last_run = recent_hook_types[:forbid_run_length]
    last_run_non_null = [h for h in last_run if h]
    if (
        len(last_run_non_null) == forbid_run_length
        and len(set(last_run_non_null)) == 1
    ):
        forbid.add(last_run_non_null[0])

    # Also forbid immediate repetition of the very last hook.
    if recent_hook_types and recent_hook_types[0]:
        forbid.add(recent_hook_types[0])

    # Suggested: prioritise types that are under-represented vs distribution.
    # Count by a reasonable window — last 20 chapters.
    counter = Counter(h for h in recent_hook_types[:20] if h)
    total = max(sum(counter.values()), 1)
    deviation: list[tuple[float, str]] = []
    for key, target_frac in CLIFFHANGER_DISTRIBUTION.items():
        observed = counter.get(key, 0) / total
        deviation.append((target_frac - observed, key))
    deviation.sort(reverse=True)  # most-underrepresented first
    suggested = [k for _, k in deviation if k not in forbid]

    return {
        "forbid": sorted(forbid),
        "recent": list(recent_hook_types[:forbid_run_length]),
        "suggested": suggested[:5],
    }


# ---------------------------------------------------------------------------
# Master beat sheet — 100% percentile based
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BeatPositionEntry:
    percentile_low: float
    percentile_high: float
    beat_name: str
    tension_target: float  # 0-10 (chapter-level, rolling mean target)
    notes: str


BEAT_SHEET: list[BeatPositionEntry] = [
    BeatPositionEntry(0.00, 0.05, "Hook",                    6.0, "开局钩子（设下第一个悬念）"),
    BeatPositionEntry(0.05, 0.20, "World Open + First Crisis", 5.0, "世界展开 + 初次危机"),
    BeatPositionEntry(0.20, 0.28, "PT1 Volume Climax",       8.0, "卷一高潮 / First Plot Point"),
    BeatPositionEntry(0.28, 0.37, "Rising + Subplot Fork",   5.5, "上升段，副线分叉"),
    BeatPositionEntry(0.37, 0.45, "Pinch 1",                 6.5, "First Temptation / Pinch 1"),
    BeatPositionEntry(0.45, 0.55, "Midpoint False Victory",  9.0, "中点翻盘 + False Victory"),
    BeatPositionEntry(0.55, 0.65, "Rising 2 + Pinch 2",      7.5, "Pinch 2 / Regression"),
    BeatPositionEntry(0.65, 0.75, "All is Lost",             8.5, "外低内高：外部最低点，内心风暴"),
    BeatPositionEntry(0.75, 0.82, "Dark Night + Epiphany",   7.0, "PT2 Dark Night / Epiphany"),
    BeatPositionEntry(0.82, 0.90, "Counter-attack / Payoff", 7.5, "反攻，伏笔兑现"),
    BeatPositionEntry(0.90, 0.97, "Climax",                  9.5, "终战（最高点）"),
    BeatPositionEntry(0.97, 1.01, "New Equilibrium",         5.0, "新均衡 + 续作钩子"),
]


def target_beat_for_chapter(
    chapter_number: int,
    total_chapters: int,
) -> BeatPositionEntry:
    """Return the beat-sheet entry that this chapter falls into."""
    total = max(total_chapters, chapter_number, 1)
    p = chapter_number / total
    for entry in BEAT_SHEET:
        if entry.percentile_low <= p < entry.percentile_high:
            return entry
    return BEAT_SHEET[-1]


def target_tension_for_chapter(chapter_number: int, total_chapters: int) -> float:
    return target_beat_for_chapter(chapter_number, total_chapters).tension_target


# ---------------------------------------------------------------------------
# Tension scoring — 6 components
# ---------------------------------------------------------------------------

TENSION_WEIGHTS: dict[str, float] = {
    "stakes":       0.25,
    "conflict":     0.20,
    "pace":         0.15,
    "novelty":      0.15,
    "emotion":      0.15,
    "info_density": 0.10,
}


def score_tension(components: dict[str, float]) -> float:
    """Compute a 0-10 tension score from 6 planner-supplied components.

    Each component should be in [0, 10]. Missing keys default to 5.0.
    """
    total = 0.0
    for key, weight in TENSION_WEIGHTS.items():
        v = float(components.get(key, 5.0))
        v = max(0.0, min(10.0, v))
        total += v * weight
    return round(total, 2)


def evaluate_tension_variance(
    recent_scores: list[float],
    *,
    window: int = 10,
    min_std: float = 1.5,
) -> dict[str, Any]:
    """Flag a "same-rhythm-cycle" risk when recent tension variance is too low."""
    window_vals = [s for s in recent_scores[:window] if isinstance(s, (int, float))]
    if len(window_vals) < 5:
        return {"sufficient_sample": False, "std": None, "flag_flat": False}
    mean = sum(window_vals) / len(window_vals)
    variance = sum((v - mean) ** 2 for v in window_vals) / len(window_vals)
    std = variance ** 0.5
    return {
        "sufficient_sample": True,
        "std": round(std, 3),
        "mean": round(mean, 3),
        "flag_flat": std < min_std,
    }
