"""Tests for the foreshadowing scaling gate.

Locks in the scaling contract that prevents the observed production
starvation where long novels (800-1200 chapters) were producing only
5-8 clues total.
"""

from __future__ import annotations

import pytest

from bestseller.services.foreshadowing_scaling import (
    MAX_REASONABLE_PAID_OFF_CLUES,
    MAX_REASONABLE_PLANTED_CLUES,
    MIN_PAID_OFF_CLUES,
    MIN_PLANTED_CLUES,
    compute_foreshadowing_bounds,
    render_foreshadowing_constraints_block,
    scan_volume_plan_foreshadowing,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _volume(
    number: int,
    *,
    plants: int = 1,
    payoffs: int = 1,
    chapter_count: int = 15,
) -> dict:
    return {
        "volume_number": number,
        "volume_title": f"Vol {number}",
        "chapter_count_target": chapter_count,
        "foreshadowing_planted": [
            f"vol{number}_plant_{i}" for i in range(plants)
        ],
        "foreshadowing_paid_off": [
            f"vol{number}_payoff_{i}" for i in range(payoffs)
        ],
    }


def _healthy_plan(volume_count: int = 10, total_chapters: int = 300) -> list[dict]:
    """Healthy density: 4 plants + 3 payoffs per volume → clears the
    300-chapter floor (30 plants, 25 payoffs). First volume skews plants
    (setup); last volume skews payoffs (resolution) but still plants at
    least 1 (sequel/epilogue hook)."""

    plan: list[dict] = []
    for i in range(volume_count):
        is_first = i == 0
        is_last = i == volume_count - 1
        plan.append(
            _volume(
                i + 1,
                plants=6 if is_first else (1 if is_last else 4),
                payoffs=0 if is_first else (6 if is_last else 3),
                chapter_count=total_chapters // volume_count,
            )
        )
    return plan


# ---------------------------------------------------------------------------
# Bounds math
# ---------------------------------------------------------------------------

def test_compute_bounds_enforces_absolute_minimums_for_novellas():
    bounds = compute_foreshadowing_bounds(30)
    assert bounds["planted"].floor == MIN_PLANTED_CLUES
    assert bounds["paid_off"].floor == MIN_PAID_OFF_CLUES


def test_compute_bounds_scales_with_chapter_count():
    small = compute_foreshadowing_bounds(100)
    big = compute_foreshadowing_bounds(1200)
    assert big["planted"].floor >= small["planted"].floor
    assert big["paid_off"].floor >= small["paid_off"].floor


def test_compute_bounds_ceiling_never_below_floor():
    for chapters in (1, 0, -10, 5, 100, 1200):
        bounds = compute_foreshadowing_bounds(chapters)
        for key in ("planted", "paid_off"):
            assert bounds[key].ceiling >= bounds[key].floor


def test_max_reasonable_ceilings_honoured_for_short_novels():
    bounds = compute_foreshadowing_bounds(10)
    assert bounds["planted"].ceiling >= MAX_REASONABLE_PLANTED_CLUES
    assert bounds["paid_off"].ceiling >= MAX_REASONABLE_PAID_OFF_CLUES


# ---------------------------------------------------------------------------
# Healthy plan passes
# ---------------------------------------------------------------------------

def test_healthy_plan_passes():
    plan = _healthy_plan(volume_count=10, total_chapters=300)
    report = scan_volume_plan_foreshadowing(plan, total_chapters=300)
    assert report.critical_count == 0
    # 6 + 4*8 + 1 = 39 plants; 0 + 3*8 + 6 = 30 payoffs
    assert report.planted_count == 39
    assert report.paid_off_count == 30
    assert report.volume_count == 10
    assert report.is_critical is False


# ---------------------------------------------------------------------------
# Starvation — matches the 6-book production data
# ---------------------------------------------------------------------------

def test_starved_plants_daozhongpoxu_case():
    """316 chapters with 24 volumes: floor = ceil(316/10) = 32. A plan with
    only 5 clues total (typical of the observed starvation) must be
    flagged critical."""

    plan = [_volume(1, plants=5, payoffs=0)]  # everything in vol 1
    plan += [_volume(i, plants=0, payoffs=0) for i in range(2, 25)]
    report = scan_volume_plan_foreshadowing(plan, total_chapters=316)

    codes = {f.code for f in report.findings}
    assert "starved_foreshadowing_plants" in codes
    assert report.is_critical is True


def test_starved_payoffs_critical():
    plan = [_volume(1, plants=30, payoffs=0)]
    # Plants meet floor; payoffs below floor
    plan += [_volume(i, plants=0, payoffs=0) for i in range(2, 13)]
    report = scan_volume_plan_foreshadowing(plan, total_chapters=300)
    codes = {f.code for f in report.findings}
    assert "starved_foreshadowing_payoffs" in codes


def test_bloated_plants_warning():
    # 200 clues across 100 chapters → ceiling is max(40, 20) = 40, so 200 > 40 is bloat
    plan = [_volume(i + 1, plants=20, payoffs=1) for i in range(10)]
    report = scan_volume_plan_foreshadowing(plan, total_chapters=100)
    codes = {f.code for f in report.findings}
    assert "bloated_foreshadowing_plants" in codes
    bloat = next(f for f in report.findings if f.code == "bloated_foreshadowing_plants")
    assert bloat.severity == "warning"


# ---------------------------------------------------------------------------
# Per-volume dead zones
# ---------------------------------------------------------------------------

def test_volume_plant_dead_zone_critical():
    """V1 has 30 plants; V2-V10 each have 0 → dead zones at V2+."""

    plan = [_volume(1, plants=30, payoffs=0)]
    plan += [_volume(i, plants=0, payoffs=2) for i in range(2, 11)]
    report = scan_volume_plan_foreshadowing(plan, total_chapters=300)

    codes = {f.code for f in report.findings}
    assert "volume_plant_dead_zone" in codes
    dz = next(f for f in report.findings if f.code == "volume_plant_dead_zone")
    assert dz.severity == "critical"
    # V2-V10 should all appear in the payload
    assert set(range(2, 11)).issubset(dz.payload["volumes"])


def test_volume_payoff_dead_zone_critical():
    """V2-V10 have 0 payoffs → all flagged."""

    plan = [_volume(1, plants=4, payoffs=0)]
    plan += [_volume(i, plants=3, payoffs=0) for i in range(2, 11)]
    report = scan_volume_plan_foreshadowing(plan, total_chapters=300)

    codes = {f.code for f in report.findings}
    assert "volume_payoff_dead_zone" in codes


def test_volume_1_exempt_from_dead_zone_rule():
    """V1 with 0 plants + 0 payoffs should NOT be flagged as dead zone
    (V1 is expected to be pure setup)."""

    plan = [_volume(1, plants=0, payoffs=0)]
    plan += [_volume(i, plants=4, payoffs=2) for i in range(2, 11)]
    report = scan_volume_plan_foreshadowing(plan, total_chapters=300)

    # Neither dead-zone finding should fire since we exempt V1
    codes = {f.code for f in report.findings}
    assert 1 not in (report.volumes_without_plants)
    assert 1 not in (report.volumes_without_payoffs)


def test_consecutive_plant_dead_streak_critical():
    """V2-V5 with no plants in a row is a structural dead zone."""

    plan = [_volume(1, plants=4, payoffs=0)]
    # V2-V5: no plants
    plan += [_volume(i, plants=0, payoffs=2) for i in range(2, 6)]
    # V6+: back to healthy
    plan += [_volume(i, plants=3, payoffs=2) for i in range(6, 11)]
    report = scan_volume_plan_foreshadowing(plan, total_chapters=300)

    codes = {f.code for f in report.findings}
    assert "consecutive_plant_dead_streak" in codes
    streak = next(
        f for f in report.findings if f.code == "consecutive_plant_dead_streak"
    )
    # The streak starts at V2 and runs through V5
    assert 2 in streak.payload["volumes"]
    assert 5 in streak.payload["volumes"]


# ---------------------------------------------------------------------------
# String extraction — handles both plain strings and dict entries
# ---------------------------------------------------------------------------

def test_accepts_dict_entries_for_clues():
    """Clue entries may arrive as dicts with a 'description' key
    (LLM output variations)."""

    plan = [
        {
            "volume_number": 1,
            "foreshadowing_planted": [
                {"description": "first plant"},
                {"clue": "second plant"},
                "third plant",
            ],
            "foreshadowing_paid_off": [],
        },
        _volume(2, plants=10, payoffs=3),
    ]
    report = scan_volume_plan_foreshadowing(plan, total_chapters=300)
    # V1 should count 3 plants despite mixed-shape entries
    assert report.planted_count >= 13  # 3 + 10


def test_empty_plan_yields_critical_starvation():
    report = scan_volume_plan_foreshadowing([], total_chapters=300)
    assert report.planted_count == 0
    assert report.paid_off_count == 0
    assert report.is_critical is True
    codes = {f.code for f in report.findings}
    assert "starved_foreshadowing_plants" in codes


def test_accepts_envelope_with_volumes_key():
    """Volume plan may arrive as {'volumes': [...]} rather than a raw list."""

    plan_envelope = {"volumes": _healthy_plan(10, 300)}
    report = scan_volume_plan_foreshadowing(plan_envelope, total_chapters=300)
    assert report.volume_count == 10
    assert report.critical_count == 0


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def test_report_to_prompt_block_lists_bounds():
    plan = [_volume(1, plants=0, payoffs=0), _volume(2, plants=0, payoffs=0)]
    report = scan_volume_plan_foreshadowing(plan, total_chapters=300)
    block = report.to_prompt_block(language="zh-CN")
    assert block
    assert str(report.planted_bounds.floor) in block
    assert str(report.paid_off_bounds.floor) in block


def test_report_to_prompt_block_empty_when_no_findings():
    plan = _healthy_plan(10, 300)
    report = scan_volume_plan_foreshadowing(plan, total_chapters=300)
    assert report.to_prompt_block() == ""


def test_render_constraints_block_zh():
    block = render_foreshadowing_constraints_block(
        total_chapters=300, volume_count=10, language="zh-CN"
    )
    assert "300" in block
    assert "10" in block
    assert "foreshadowing_planted" in block
    assert "foreshadowing_paid_off" in block
    assert "硬性要求" in block


def test_render_constraints_block_en():
    block = render_foreshadowing_constraints_block(
        total_chapters=544, volume_count=16, language="en-US"
    )
    assert "544" in block
    assert "16" in block
    assert "foreshadowing_planted" in block.lower() or "planted" in block.lower()
    assert "hard constraints" in block.lower()


def test_render_constraints_block_handles_single_volume_plan():
    block = render_foreshadowing_constraints_block(
        total_chapters=30, volume_count=1, language="zh-CN"
    )
    # Must not divide by zero or negative
    assert block


# ---------------------------------------------------------------------------
# English messaging
# ---------------------------------------------------------------------------

def test_english_messages_when_language_en():
    plan = [_volume(1, plants=5, payoffs=0)]
    plan += [_volume(i, plants=0, payoffs=0) for i in range(2, 13)]
    report = scan_volume_plan_foreshadowing(
        plan, total_chapters=300, language="en-US"
    )
    starved = next(
        f for f in report.findings if f.code == "starved_foreshadowing_plants"
    )
    assert "chapters" in starved.message.lower()
    assert "need" in starved.message.lower()


# ---------------------------------------------------------------------------
# is_critical predicate
# ---------------------------------------------------------------------------

def test_is_critical_only_when_critical_findings_exist():
    # warning-only (bloat) does not mark critical
    plan = [_volume(i + 1, plants=30, payoffs=3) for i in range(10)]
    # 300 plants on 100 chapters → way above ceiling = bloat warning
    report = scan_volume_plan_foreshadowing(plan, total_chapters=100)
    assert report.warning_count >= 1
    assert report.critical_count == 0
    assert report.is_critical is False
