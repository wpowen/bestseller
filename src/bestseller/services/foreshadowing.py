"""Phase-6: Foreshadowing density analysis.

Analyses the distribution of clue planting and payoff recovery across a novel,
detecting imbalances such as:
- Dead zones (3+ consecutive chapters with no clue activity)
- Orphan clues (planted but not recovered well past their expected payoff chapter)
- Front/back loading (all planting in Act 1, all recovery in Act 3)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ForeshadowingDensityResult(BaseModel, frozen=True):
    """Result of foreshadowing density analysis."""

    balance_score: float = Field(ge=0.0, le=1.0)
    act1_plants: int = 0
    act1_recoveries: int = 0
    act2_plants: int = 0
    act2_recoveries: int = 0
    act3_plants: int = 0
    act3_recoveries: int = 0
    dead_zone_chapters: list[tuple[int, int]] = Field(default_factory=list)
    orphan_clue_codes: list[str] = Field(default_factory=list)


def analyze_foreshadowing_density(
    *,
    clues: list[Any],
    payoffs: list[Any],
    total_chapters: int,
) -> ForeshadowingDensityResult:
    """Analyse clue/payoff distribution across the novel.

    Parameters
    ----------
    clues:
        List of ClueModel-like objects with ``planted_in_chapter_number``,
        ``expected_payoff_by_chapter_number``, ``actual_paid_off_chapter_number``,
        ``clue_code``, and ``status``.
    payoffs:
        List of PayoffModel-like objects with ``actual_chapter_number``.
    total_chapters:
        Target or current total chapter count for the project.
    """
    if total_chapters < 1:
        return ForeshadowingDensityResult(balance_score=1.0)

    # Act boundaries
    act1_end = max(1, round(total_chapters * 0.25))
    act2_end = max(act1_end + 1, round(total_chapters * 0.75))

    # Count planting and recovery events per chapter
    plant_chapters: set[int] = set()
    recovery_chapters: set[int] = set()

    act1_plants = act2_plants = act3_plants = 0
    act1_recoveries = act2_recoveries = act3_recoveries = 0

    for clue in clues:
        ch = getattr(clue, "planted_in_chapter_number", None)
        if ch is not None:
            plant_chapters.add(ch)
            if ch <= act1_end:
                act1_plants += 1
            elif ch <= act2_end:
                act2_plants += 1
            else:
                act3_plants += 1

        paid_ch = getattr(clue, "actual_paid_off_chapter_number", None)
        if paid_ch is not None:
            recovery_chapters.add(paid_ch)
            if paid_ch <= act1_end:
                act1_recoveries += 1
            elif paid_ch <= act2_end:
                act2_recoveries += 1
            else:
                act3_recoveries += 1

    for payoff in payoffs:
        ch = getattr(payoff, "actual_chapter_number", None)
        if ch is not None:
            recovery_chapters.add(ch)
            if ch <= act1_end:
                act1_recoveries += 1
            elif ch <= act2_end:
                act2_recoveries += 1
            else:
                act3_recoveries += 1

    # Dead zone detection: 3+ consecutive chapters with no clue activity
    active_chapters = plant_chapters | recovery_chapters
    dead_zones: list[tuple[int, int]] = []
    streak_start: int | None = None
    for ch in range(1, total_chapters + 1):
        if ch not in active_chapters:
            if streak_start is None:
                streak_start = ch
        else:
            if streak_start is not None and ch - streak_start >= 3:
                dead_zones.append((streak_start, ch - 1))
            streak_start = None
    # Close any trailing streak
    if streak_start is not None and (total_chapters + 1) - streak_start >= 3:
        dead_zones.append((streak_start, total_chapters))

    # Orphan clue detection: planted clues past expected payoff but not recovered
    orphan_codes: list[str] = []
    for clue in clues:
        status = getattr(clue, "status", "planted")
        if status in ("paid_off", "cancelled"):
            continue
        expected = getattr(clue, "expected_payoff_by_chapter_number", None)
        actual = getattr(clue, "actual_paid_off_chapter_number", None)
        if actual is not None:
            continue
        if expected is not None and expected < total_chapters:
            code = getattr(clue, "clue_code", "?")
            orphan_codes.append(code)

    # Balance score: penalise dead zones and orphans
    total_events = (
        act1_plants + act2_plants + act3_plants
        + act1_recoveries + act2_recoveries + act3_recoveries
    )
    if total_events == 0:
        balance = 1.0
    else:
        # Spread penalty: ideal is even distribution across 3 acts
        act_event_counts = [
            act1_plants + act1_recoveries,
            act2_plants + act2_recoveries,
            act3_plants + act3_recoveries,
        ]
        max_act = max(act_event_counts)
        min_act = min(act_event_counts)
        spread_ratio = min_act / max_act if max_act > 0 else 1.0

        # Dead zone penalty
        dead_chapter_count = sum(end - start + 1 for start, end in dead_zones)
        dead_penalty = min(0.3, dead_chapter_count * 0.03)

        # Orphan penalty
        orphan_penalty = min(0.3, len(orphan_codes) * 0.05)

        balance = max(0.0, min(1.0, spread_ratio - dead_penalty - orphan_penalty))

    return ForeshadowingDensityResult(
        balance_score=round(balance, 2),
        act1_plants=act1_plants,
        act1_recoveries=act1_recoveries,
        act2_plants=act2_plants,
        act2_recoveries=act2_recoveries,
        act3_plants=act3_plants,
        act3_recoveries=act3_recoveries,
        dead_zone_chapters=dead_zones,
        orphan_clue_codes=orphan_codes,
    )
