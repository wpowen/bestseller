"""Foreshadowing scaling gate — validate that a generated VolumePlan has
enough planted/paid-off clue events for the planned novel length.

Root cause this module addresses
--------------------------------

Audit of 6 production novels showed systemic foreshadowing starvation:

    slug                           chapters   clues   payoffs   chapters/clue
    xianxia-upgrade-1776137730     1200       7       6         171
    female-no-cp-1776303225        800        6       5         133
    romantasy-1776330993           800        5       4         160
    superhero-fiction-1776147970   800        8       7         100
    superhero-fiction-1776301343   800        5       4         160

Every project is producing one clue every 100-170 chapters. That means:

  * Most chapters have NO active foreshadow thread (the `foreshadowing.py`
    dead-zone detector would flag ~80% of chapters).
  * Volume plans default to ONE planted + ONE paid-off per volume from
    ``_fallback_volume_plan``, and the LLM prompt never asks for more.
  * The few clues that do exist are spread so thin they feel disconnected
    — readers can't build a pattern recognition loop.

This module is the **foreshadowing-level peer** of ``foundation_richness``
(cast) and ``world_richness`` (world) and runs at the same point in the
planner flow: right after volume_plan generation, right before the
outline prompt consumes the plan.

Scaling formulas (calibrated against the 6-book data; see
:func:`compute_foreshadowing_bounds`):

    total planted  : floor = max(8, chapters/10),  ceiling = max(40, chapters/5)
    total paid_off : floor = max(6, chapters/12),  ceiling = max(36, chapters/6)
    per-volume     : each non-opening volume ≥ 1 planted;
                     each non-final volume ≥ 1 paid_off

Below floor → "starved_foreshadowing" critical finding. Above ceiling →
"bloated_foreshadowing" warning.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Chapter divisors. Calibrated so that the 6-book audit cases (100-170
# chapters/clue) fall solidly below floor:
#   * 316-chapter novel → planted_floor = 31, paid_floor = 26
#   * 800-chapter novel → planted_floor = 80, paid_floor = 66
#   * 1200-chapter novel → planted_floor = 120, paid_floor = 100
# The observed range (5-8 clues) is ~15× too low even at the low end.
PLANTED_CHAPTER_FLOOR_DIVISOR: int = 10
PLANTED_CHAPTER_CEILING_DIVISOR: int = 5

PAID_OFF_CHAPTER_FLOOR_DIVISOR: int = 12
PAID_OFF_CHAPTER_CEILING_DIVISOR: int = 6

# Absolute minimums: even a short novella benefits from this much
# foreshadowing density; anything less and the structure feels flat.
MIN_PLANTED_CLUES: int = 8
MIN_PAID_OFF_CLUES: int = 6

# Absolute ceilings: even mega-series don't benefit from more than this
# at the volume_plan level (additional subtle threads can still be added
# at scene-plan granularity).
MAX_REASONABLE_PLANTED_CLUES: int = 40
MAX_REASONABLE_PAID_OFF_CLUES: int = 36

# A pair of consecutive volumes with ZERO planted clues between them is
# a critical dead zone — downstream chapters will have nothing to unwind.
# This threshold flags the starvation pattern regardless of chapter count.
MAX_CONSECUTIVE_EMPTY_PLANT_VOLUMES: int = 1


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ForeshadowingFinding:
    """One audit finding against the volume plan's foreshadowing breadth."""

    code: str              # short identifier, stable across runs
    severity: str          # "critical" | "warning"
    message: str           # human-readable message (zh or en)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ForeshadowingBounds:
    """Floor/ceiling bounds for one metric, derived from chapter count."""

    floor: int
    ceiling: int


@dataclass(frozen=True)
class ForeshadowingScalingReport:
    """Aggregate scan of a volume plan's foreshadowing density."""

    total_chapters: int
    volume_count: int
    planted_count: int
    planted_bounds: ForeshadowingBounds
    paid_off_count: int
    paid_off_bounds: ForeshadowingBounds
    volumes_without_plants: tuple[int, ...]
    volumes_without_payoffs: tuple[int, ...]
    findings: tuple[ForeshadowingFinding, ...]

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def is_critical(self) -> bool:
        return self.critical_count > 0

    def to_prompt_block(self, *, language: str = "zh-CN") -> str:
        """Render the report into a repair prompt block telling the LLM
        exactly how many planted/paid-off clues each volume needs."""

        if not self.findings:
            return ""

        is_en = _is_english(language)
        lines: list[str] = []
        if is_en:
            lines.append("[FORESHADOWING SCALING REPAIR — hard requirements]")
            lines.append(
                f"- Across all {self.volume_count} volumes, the `foreshadowing_planted` "
                f"arrays MUST collectively contain between "
                f"{self.planted_bounds.floor} and "
                f"{self.planted_bounds.ceiling} distinct planted clue items "
                f"(currently {self.planted_count})."
            )
            lines.append(
                f"- `foreshadowing_paid_off` arrays MUST collectively contain "
                f"between {self.paid_off_bounds.floor} and "
                f"{self.paid_off_bounds.ceiling} distinct payoff items "
                f"(currently {self.paid_off_count})."
            )
            lines.append(
                "- Every volume from Volume 2 onward MUST plant at least 1 "
                "new clue (dead-zone volumes collapse the story tension)."
            )
            lines.append(
                "- Every volume except the first MUST pay off at least 1 "
                "earlier-planted clue."
            )
            lines.append(
                "- Clues should be CONCRETE (a specific object/name/event), "
                "not vague (e.g. 'an omen', 'a secret')."
            )
            lines.append("")
            lines.append("Current findings (fix ALL critical):")
        else:
            lines.append("【伏笔密度修复 — 硬性要求】")
            lines.append(
                f"- 全书共 {self.volume_count} 卷，所有卷的 "
                f"foreshadowing_planted 数组累计必须在 "
                f"{self.planted_bounds.floor} 到 "
                f"{self.planted_bounds.ceiling} 条之间"
                f"（当前累计 {self.planted_count} 条）。"
            )
            lines.append(
                f"- foreshadowing_paid_off 累计必须在 "
                f"{self.paid_off_bounds.floor} 到 "
                f"{self.paid_off_bounds.ceiling} 条之间"
                f"（当前累计 {self.paid_off_count} 条）。"
            )
            lines.append(
                "- 从第 2 卷开始，每卷都必须至少埋下 1 条新伏笔"
                "（伏笔空缺的卷会让故事张力塌陷）。"
            )
            lines.append(
                "- 除第 1 卷外，每卷都必须至少回收 1 条前期埋下的伏笔。"
            )
            lines.append(
                "- 伏笔条目必须具体（具体物件/人名/事件），不要写成"
                "『一个征兆』『一个秘密』这种空泛描述。"
            )
            lines.append("")
            lines.append("当前审查结果（所有 critical 项必须修复）：")

        for finding in self.findings:
            bullet = "×" if finding.severity == "critical" else "!"
            lines.append(f"  {bullet} [{finding.severity}] {finding.code}: {finding.message}")

        return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_english(language: str | None) -> bool:
    if not language:
        return False
    return language.lower().startswith("en")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
        except Exception:
            return {}
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        return {k: v for k, v in value.__dict__.items() if not k.startswith("_")}
    return {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return []
    return [_mapping(item) for item in value if item is not None]


def _string_list(value: Any) -> list[str]:
    """Coerce a possibly-mixed iterable into a list of non-empty strings."""

    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                out.append(stripped)
        elif isinstance(item, dict):
            # Support shapes like {"description": "..."} / {"clue": "..."}
            for key in ("description", "clue", "text", "label", "detail"):
                if isinstance(item.get(key), str) and item[key].strip():
                    out.append(item[key].strip())
                    break
    return out


def _bounded(
    chapters: int,
    *,
    floor_divisor: int,
    ceiling_divisor: int,
    min_floor: int,
    min_ceiling: int,
) -> ForeshadowingBounds:
    """Compute a (floor, ceiling) pair for one metric from chapter count."""

    chapters = max(int(chapters or 0), 1)
    floor = max(min_floor, math.ceil(chapters / max(floor_divisor, 1)))
    ceiling = max(min_ceiling, chapters // max(ceiling_divisor, 1))
    if ceiling < floor:
        ceiling = floor * 2
    return ForeshadowingBounds(floor=floor, ceiling=ceiling)


def compute_foreshadowing_bounds(total_chapters: int) -> dict[str, ForeshadowingBounds]:
    """Derive (floor, ceiling) for planted and paid_off clue counts."""

    return {
        "planted": _bounded(
            total_chapters,
            floor_divisor=PLANTED_CHAPTER_FLOOR_DIVISOR,
            ceiling_divisor=PLANTED_CHAPTER_CEILING_DIVISOR,
            min_floor=MIN_PLANTED_CLUES,
            min_ceiling=MAX_REASONABLE_PLANTED_CLUES,
        ),
        "paid_off": _bounded(
            total_chapters,
            floor_divisor=PAID_OFF_CHAPTER_FLOOR_DIVISOR,
            ceiling_divisor=PAID_OFF_CHAPTER_CEILING_DIVISOR,
            min_floor=MIN_PAID_OFF_CLUES,
            min_ceiling=MAX_REASONABLE_PAID_OFF_CLUES,
        ),
    }


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan_volume_plan_foreshadowing(
    volume_plan: Any,
    *,
    total_chapters: int,
    language: str = "zh-CN",
) -> ForeshadowingScalingReport:
    """Audit a VolumePlan for scale-appropriate foreshadowing density.

    Parameters
    ----------
    volume_plan
        A list of VolumePlanEntryInput-shaped dicts (or pydantic models).
    total_chapters
        Target chapter count for the novel. Scales the bounds.
    language
        Locale for generated messages.

    Returns
    -------
    ForeshadowingScalingReport
        Empty findings means the volume plan's foreshadowing density is
        healthy enough to pass through to outline + scene generation.
    """

    is_en = _is_english(language)

    # Normalize to a list of dicts. Accept raw lists, {"volumes": [...]}
    # envelopes, or pydantic models.
    raw_volumes: list[dict[str, Any]] = []
    if isinstance(volume_plan, list):
        raw_volumes = _mapping_list(volume_plan)
    else:
        vp_dict = _mapping(volume_plan)
        if isinstance(vp_dict.get("volumes"), list):
            raw_volumes = _mapping_list(vp_dict["volumes"])

    # Sort by volume_number for stable reporting.
    raw_volumes.sort(key=lambda v: int(v.get("volume_number") or 0))

    volume_count = len(raw_volumes)
    bounds = compute_foreshadowing_bounds(total_chapters)
    findings: list[ForeshadowingFinding] = []

    # Per-volume plant/payoff extraction + total counts
    per_volume_plants: list[list[str]] = []
    per_volume_payoffs: list[list[str]] = []
    for vol in raw_volumes:
        per_volume_plants.append(_string_list(vol.get("foreshadowing_planted")))
        per_volume_payoffs.append(_string_list(vol.get("foreshadowing_paid_off")))

    planted_total = sum(len(p) for p in per_volume_plants)
    paid_off_total = sum(len(p) for p in per_volume_payoffs)

    volumes_without_plants = tuple(
        int(raw_volumes[i].get("volume_number") or i + 1)
        for i, plants in enumerate(per_volume_plants)
        # First volume is exempt from the "no plants" rule (Volume 1 is
        # mostly setup; the per-volume expectation kicks in at Volume 2).
        if i > 0 and not plants
    )
    volumes_without_payoffs = tuple(
        int(raw_volumes[i].get("volume_number") or i + 1)
        for i, payoffs in enumerate(per_volume_payoffs)
        # First volume is exempt (no prior plants to cash in).
        if i > 0 and not payoffs
    )

    # ── Total planted floor/ceiling ──────────────────────────────────
    if planted_total < bounds["planted"].floor:
        findings.append(
            ForeshadowingFinding(
                code="starved_foreshadowing_plants",
                severity="critical",
                message=(
                    f"volume_plan plants only {planted_total} clues across "
                    f"{total_chapters} chapters; need ≥ "
                    f"{bounds['planted'].floor}. "
                    "Chapter-level dead zones are guaranteed."
                    if is_en
                    else f"{total_chapters} 章规划累计只埋下 {planted_total} 条伏笔，"
                    f"至少需要 {bounds['planted'].floor} 条，否则章节层必然出现空洞。"
                ),
                payload={
                    "count": planted_total,
                    "floor": bounds["planted"].floor,
                },
            )
        )
    elif planted_total > bounds["planted"].ceiling:
        findings.append(
            ForeshadowingFinding(
                code="bloated_foreshadowing_plants",
                severity="warning",
                message=(
                    f"volume_plan plants {planted_total} clues across "
                    f"{total_chapters} chapters; > ceiling "
                    f"{bounds['planted'].ceiling}. "
                    "Most clues will never resolve."
                    if is_en
                    else f"{total_chapters} 章规划累计埋下 {planted_total} 条伏笔，"
                    f"超过上限 {bounds['planted'].ceiling}，多数伏笔将无从回收。"
                ),
                payload={
                    "count": planted_total,
                    "ceiling": bounds["planted"].ceiling,
                },
            )
        )

    # ── Total paid_off floor/ceiling ─────────────────────────────────
    if paid_off_total < bounds["paid_off"].floor:
        findings.append(
            ForeshadowingFinding(
                code="starved_foreshadowing_payoffs",
                severity="critical",
                message=(
                    f"volume_plan pays off only {paid_off_total} clues across "
                    f"{total_chapters} chapters; need ≥ "
                    f"{bounds['paid_off'].floor}. "
                    "Reader payoff rhythm will be starved."
                    if is_en
                    else f"{total_chapters} 章规划累计只回收 {paid_off_total} 条伏笔，"
                    f"至少需要 {bounds['paid_off'].floor} 条，否则读者爽点节奏饥饿。"
                ),
                payload={
                    "count": paid_off_total,
                    "floor": bounds["paid_off"].floor,
                },
            )
        )
    elif paid_off_total > bounds["paid_off"].ceiling:
        findings.append(
            ForeshadowingFinding(
                code="bloated_foreshadowing_payoffs",
                severity="warning",
                message=(
                    f"volume_plan pays off {paid_off_total} clues across "
                    f"{total_chapters} chapters; > ceiling "
                    f"{bounds['paid_off'].ceiling}."
                    if is_en
                    else f"{total_chapters} 章规划累计回收 {paid_off_total} 条伏笔，"
                    f"超过上限 {bounds['paid_off'].ceiling}。"
                ),
                payload={
                    "count": paid_off_total,
                    "ceiling": bounds["paid_off"].ceiling,
                },
            )
        )

    # ── Volume-level dead zones ──────────────────────────────────────
    if volumes_without_plants:
        findings.append(
            ForeshadowingFinding(
                code="volume_plant_dead_zone",
                severity="critical",
                message=(
                    f"Volumes with zero planted clues (V2+ must plant ≥ 1): "
                    f"{list(volumes_without_plants)}"
                    if is_en
                    else f"未埋下任何伏笔的卷（第 2 卷起每卷至少 1 条）："
                    f"{list(volumes_without_plants)}"
                ),
                payload={"volumes": list(volumes_without_plants)},
            )
        )

    if volumes_without_payoffs:
        findings.append(
            ForeshadowingFinding(
                code="volume_payoff_dead_zone",
                severity="critical",
                message=(
                    f"Volumes with zero paid-off clues (V2+ must pay off "
                    f"≥ 1): {list(volumes_without_payoffs)}"
                    if is_en
                    else f"未回收任何伏笔的卷（第 2 卷起每卷至少 1 条）："
                    f"{list(volumes_without_payoffs)}"
                ),
                payload={"volumes": list(volumes_without_payoffs)},
            )
        )

    # ── Consecutive-volume dead streaks ──────────────────────────────
    # A single V2+ volume missing plants is already flagged above. This
    # specifically catches *streaks*: two or more consecutive volumes
    # with zero plants is a structural dead zone even for short novels.
    streak_start: int | None = None
    worst_streak_run: tuple[int, int] | None = None
    for i, plants in enumerate(per_volume_plants):
        if i == 0:
            # Volume 1 doesn't reset the streak — it just contributes 0
            # plants implicitly (per our V1-exempt rule).
            continue
        if not plants:
            if streak_start is None:
                streak_start = i
        else:
            if streak_start is not None:
                run_len = i - streak_start
                if run_len > MAX_CONSECUTIVE_EMPTY_PLANT_VOLUMES:
                    if worst_streak_run is None or run_len > (
                        worst_streak_run[1] - worst_streak_run[0]
                    ):
                        worst_streak_run = (streak_start, i)
            streak_start = None
    # Close trailing streak
    if streak_start is not None:
        run_len = len(per_volume_plants) - streak_start
        if run_len > MAX_CONSECUTIVE_EMPTY_PLANT_VOLUMES:
            if worst_streak_run is None or run_len > (
                worst_streak_run[1] - worst_streak_run[0]
            ):
                worst_streak_run = (streak_start, len(per_volume_plants))

    if worst_streak_run is not None:
        start_idx, end_idx = worst_streak_run
        first_vol = int(raw_volumes[start_idx].get("volume_number") or start_idx + 1)
        last_vol = int(raw_volumes[end_idx - 1].get("volume_number") or end_idx)
        findings.append(
            ForeshadowingFinding(
                code="consecutive_plant_dead_streak",
                severity="critical",
                message=(
                    f"Volumes {first_vol}-{last_vol} are a consecutive plant "
                    "dead zone (no new clues across 2+ volumes); chapters here "
                    "will have nothing to foreshadow into."
                    if is_en
                    else f"第 {first_vol}-{last_vol} 卷连续 2 卷以上未埋下任何新伏笔，"
                    "这一段章节层完全失去未来钩子。"
                ),
                payload={
                    "volumes": list(range(first_vol, last_vol + 1)),
                },
            )
        )

    return ForeshadowingScalingReport(
        total_chapters=max(int(total_chapters or 0), 1),
        volume_count=volume_count,
        planted_count=planted_total,
        planted_bounds=bounds["planted"],
        paid_off_count=paid_off_total,
        paid_off_bounds=bounds["paid_off"],
        volumes_without_plants=volumes_without_plants,
        volumes_without_payoffs=volumes_without_payoffs,
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Prompt-block renderer for the *upstream* volume-plan prompt
# ---------------------------------------------------------------------------

def render_foreshadowing_constraints_block(
    *,
    total_chapters: int,
    volume_count: int,
    language: str = "zh-CN",
) -> str:
    """Render the up-front constraints injected into the volume-plan prompt.

    This prevents the starvation failure mode (1 clue per 100+ chapters
    observed across all 6 production books) by telling the LLM
    explicitly how many plants/payoffs to produce per volume up front.
    """

    bounds = compute_foreshadowing_bounds(total_chapters)

    # Per-volume rough guidance: split the aggregate floor across volumes.
    volume_count = max(int(volume_count or 0), 1)
    plants_per_vol_floor = max(1, math.ceil(bounds["planted"].floor / volume_count))
    payoffs_per_vol_floor = max(1, math.ceil(bounds["paid_off"].floor / volume_count))

    if _is_english(language):
        return (
            "[FORESHADOWING DENSITY HARD CONSTRAINTS]\n"
            f"- Target plan: {total_chapters} chapters across "
            f"{volume_count} volumes.\n"
            f"- Aggregate `foreshadowing_planted` across ALL volumes MUST be "
            f"between {bounds['planted'].floor} and "
            f"{bounds['planted'].ceiling} (~{plants_per_vol_floor} plants "
            "per volume on average).\n"
            f"- Aggregate `foreshadowing_paid_off` across ALL volumes MUST be "
            f"between {bounds['paid_off'].floor} and "
            f"{bounds['paid_off'].ceiling} (~{payoffs_per_vol_floor} payoffs "
            "per volume on average).\n"
            "- Every volume from Volume 2 onward MUST plant ≥ 1 new clue.\n"
            "- Every volume except the first MUST pay off ≥ 1 earlier clue.\n"
            "- Clues must be CONCRETE — name a specific object, person, "
            "date, place, or event. Do not emit vague placeholders like "
            "'an omen' or 'a hidden truth'.\n"
            "- Plants and payoffs must reference DIFFERENT clues — don't "
            "plant and resolve the same clue inside one volume.\n"
        )

    return (
        "【伏笔密度硬性要求】\n"
        f"- 全书规划：{total_chapters} 章，共 {volume_count} 卷。\n"
        f"- 所有卷的 foreshadowing_planted 累计总数必须在 "
        f"{bounds['planted'].floor} 到 {bounds['planted'].ceiling} 条之间"
        f"（平均每卷 ≥ {plants_per_vol_floor} 条）。\n"
        f"- 所有卷的 foreshadowing_paid_off 累计总数必须在 "
        f"{bounds['paid_off'].floor} 到 {bounds['paid_off'].ceiling} 条之间"
        f"（平均每卷 ≥ {payoffs_per_vol_floor} 条）。\n"
        "- 从第 2 卷开始，每卷必须新埋下 ≥ 1 条伏笔；除第 1 卷外，"
        "每卷必须回收 ≥ 1 条前期伏笔。\n"
        "- 伏笔条目必须具体——指名具体的物件/人物/日期/地点/事件。"
        "禁止『一个征兆』『一个隐藏的秘密』这种空泛占位。\n"
        "- 同一卷内不可『刚埋下就立即回收』——plant 与 pay_off 必须是不同伏笔。\n"
    )
