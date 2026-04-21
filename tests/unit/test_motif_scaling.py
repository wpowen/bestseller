"""Tests for the motif-placement scaling gate.

Locks in the scaling contract that prevents the observed production
starvation where long novels (300-1200 chapters, 20+ volumes) were
producing exactly 4 motif placements across the entire book, leaving
the theme layer invisible in 80 %+ of chapters.
"""

from __future__ import annotations

import pytest

from bestseller.services.motif_scaling import (
    EXPECTED_PLACEMENT_TYPES,
    MIN_TOTAL_MOTIFS,
    MIN_TOTAL_MOTIFS_CEILING,
    MOTIFS_PER_VOLUME_CEILING,
    MOTIFS_PER_VOLUME_FLOOR,
    compute_motif_bounds,
    render_motif_constraints_block,
    scan_motif_placements,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _placement(volume: int, ptype: str = "echo", chapter: int | None = None) -> dict:
    return {
        "theme_arc_id": "main-theme",
        "motif_label": f"主题意象-{ptype}",
        "placement_type": ptype,
        "volume_number": volume,
        "chapter_number": chapter or (volume * 10),
        "description": f"第{chapter or volume * 10}章的主题意象。",
        "status": "planned",
    }


def _healthy_schedule(volume_count: int = 10) -> list[dict]:
    """2 placements per volume covering plant/echo/transform/resolve."""

    rhythm = EXPECTED_PLACEMENT_TYPES  # plant, echo, transform, resolve
    out: list[dict] = []
    for v in range(1, volume_count + 1):
        # Each volume gets 2 placements; rotate across the rhythm so all
        # four canonical placement_types appear.
        for i in range(2):
            ptype = rhythm[(2 * (v - 1) + i) % len(rhythm)]
            out.append(_placement(v, ptype=ptype, chapter=(v - 1) * 10 + i + 1))
    return out


# ---------------------------------------------------------------------------
# Bounds math
# ---------------------------------------------------------------------------

def test_compute_motif_bounds_enforces_absolute_minimums_for_tiny_books():
    bounds = compute_motif_bounds(1)
    assert bounds.floor == MIN_TOTAL_MOTIFS
    assert bounds.ceiling >= MIN_TOTAL_MOTIFS_CEILING
    assert bounds.ceiling >= bounds.floor


def test_compute_motif_bounds_scales_with_volume_count():
    small = compute_motif_bounds(5)
    big = compute_motif_bounds(24)
    assert big.floor >= small.floor
    assert big.ceiling >= small.ceiling


def test_compute_motif_bounds_linear_per_volume():
    """Floor and ceiling should hit the per-volume multipliers once past
    the absolute minimums."""

    bounds = compute_motif_bounds(24)
    assert bounds.floor == MOTIFS_PER_VOLUME_FLOOR * 24
    assert bounds.ceiling == MOTIFS_PER_VOLUME_CEILING * 24


def test_compute_motif_bounds_ceiling_always_at_or_above_floor():
    """For any realistic or pathological volume count, ceiling must not
    fall below floor. Floor/ceiling both scale linearly with volumes."""

    for volumes in (1, 5, 16, 24, 48, 100, 500):
        bounds = compute_motif_bounds(volumes)
        assert bounds.ceiling >= bounds.floor, (volumes, bounds)
        assert bounds.ceiling >= MIN_TOTAL_MOTIFS_CEILING
        assert bounds.floor >= MIN_TOTAL_MOTIFS


def test_compute_motif_bounds_handles_zero_and_negative_volumes():
    for bad in (0, -1, -500):
        bounds = compute_motif_bounds(bad)
        assert bounds.floor == MIN_TOTAL_MOTIFS
        assert bounds.ceiling >= bounds.floor


# ---------------------------------------------------------------------------
# Healthy schedule passes
# ---------------------------------------------------------------------------

def test_healthy_schedule_passes():
    schedule = _healthy_schedule(volume_count=10)
    report = scan_motif_placements(
        schedule, total_chapters=300, volume_count=10
    )
    assert report.placement_count == 20  # 10 volumes × 2
    assert report.volume_count == 10
    assert report.critical_count == 0
    assert report.is_critical is False
    # All canonical types must appear
    assert report.placement_types_missing == ()


# ---------------------------------------------------------------------------
# Starvation — canonical 道种破虚 case (24 volumes, 4 placements total)
# ---------------------------------------------------------------------------

def test_starved_motifs_daozhongpoxu_case():
    """24 volumes should need ≥ 48 placements; 4 total is catastrophic
    starvation."""

    # Place all 4 in the first volume to match the pre-fix narrative
    # builder (position 0, total//4, total//2, total-1 — but for 24
    # volumes the first quartile still lands inside volume 1 or 2).
    schedule = [
        _placement(1, ptype="plant"),
        _placement(1, ptype="echo"),
        _placement(2, ptype="transform"),
        _placement(24, ptype="resolve"),
    ]
    report = scan_motif_placements(
        schedule, total_chapters=316, volume_count=24
    )

    codes = {f.code for f in report.findings}
    assert "starved_motif_placements" in codes
    assert "volume_motif_dead_zone" in codes
    assert report.is_critical is True
    # V3-V23 should appear in the dead-zone payload
    dz = next(f for f in report.findings if f.code == "volume_motif_dead_zone")
    assert 3 in dz.payload["volumes"]
    assert 23 in dz.payload["volumes"]


def test_bloated_motifs_warning():
    """100 placements across 10 volumes (10 per volume) exceeds the
    per-volume ceiling (40 total); must warn."""

    schedule = []
    for v in range(1, 11):
        for i in range(10):
            schedule.append(_placement(v, ptype="echo"))
    report = scan_motif_placements(
        schedule, total_chapters=300, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "bloated_motif_placements" in codes
    bloat = next(
        f for f in report.findings if f.code == "bloated_motif_placements"
    )
    assert bloat.severity == "warning"


# ---------------------------------------------------------------------------
# Per-volume dead zones
# ---------------------------------------------------------------------------

def test_volume_motif_dead_zone_flags_missing_volume():
    """A volume in the middle of the book that has no motif is a
    structural hole regardless of total count."""

    schedule = _healthy_schedule(volume_count=10)
    # Strip all placements from volume 5
    schedule = [p for p in schedule if p["volume_number"] != 5]
    report = scan_motif_placements(
        schedule, total_chapters=300, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "volume_motif_dead_zone" in codes
    dz = next(f for f in report.findings if f.code == "volume_motif_dead_zone")
    assert 5 in dz.payload["volumes"]


def test_consecutive_motif_dead_streak_critical():
    """V3-V6 with no motifs in a row is a structural dead zone."""

    schedule = _healthy_schedule(volume_count=10)
    schedule = [
        p for p in schedule if p["volume_number"] not in {3, 4, 5, 6}
    ]
    report = scan_motif_placements(
        schedule, total_chapters=300, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "consecutive_motif_dead_streak" in codes
    streak = next(
        f for f in report.findings if f.code == "consecutive_motif_dead_streak"
    )
    # The streak should span V3-V6 inclusive
    assert 3 in streak.payload["volumes"]
    assert 6 in streak.payload["volumes"]


def test_single_empty_volume_not_flagged_as_streak():
    """One orphan volume is tolerated (the rhythm sometimes skips a beat);
    only 2+ consecutive empties trigger the streak finding."""

    schedule = _healthy_schedule(volume_count=10)
    schedule = [p for p in schedule if p["volume_number"] != 6]
    report = scan_motif_placements(
        schedule, total_chapters=300, volume_count=10
    )
    codes = {f.code for f in report.findings}
    # volume_motif_dead_zone fires (V6 is empty) but the streak finding
    # does NOT (no consecutive dead volumes).
    assert "volume_motif_dead_zone" in codes
    assert "consecutive_motif_dead_streak" not in codes


# ---------------------------------------------------------------------------
# Missing placement types
# ---------------------------------------------------------------------------

def test_missing_placement_types_warns_when_above_floor():
    """If total >= floor but placement_type set is incomplete, warn."""

    # Build a healthy-count schedule but only "plant" types.
    schedule = []
    for v in range(1, 11):
        for i in range(2):
            schedule.append(_placement(v, ptype="plant"))
    report = scan_motif_placements(
        schedule, total_chapters=300, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "missing_motif_placement_types" in codes
    miss = next(
        f for f in report.findings if f.code == "missing_motif_placement_types"
    )
    assert miss.severity == "warning"
    # At least echo/transform/resolve should be called out.
    assert "echo" in miss.payload["missing"]
    assert "resolve" in miss.payload["missing"]


def test_missing_placement_types_not_warned_when_below_floor():
    """When starved, the missing-type warning should be suppressed in
    favour of the starvation finding (no point warning about missing
    types when there aren't enough placements in the first place)."""

    schedule = [_placement(1, ptype="plant")]
    report = scan_motif_placements(
        schedule, total_chapters=300, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "starved_motif_placements" in codes
    assert "missing_motif_placement_types" not in codes


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_schedule_yields_critical_starvation():
    report = scan_motif_placements(
        [], total_chapters=300, volume_count=10
    )
    assert report.placement_count == 0
    assert report.is_critical is True
    codes = {f.code for f in report.findings}
    assert "starved_motif_placements" in codes
    assert "volume_motif_dead_zone" in codes


def test_accepts_pydantic_like_entries():
    """Placement entries may arrive as objects with model_dump()
    (pydantic) or plain objects."""

    class _FakePlacement:
        def __init__(self, v: int, ptype: str):
            self._v = v
            self._ptype = ptype

        def model_dump(self):
            return {
                "volume_number": self._v,
                "placement_type": self._ptype,
            }

    schedule = [
        _FakePlacement(v, ptype=EXPECTED_PLACEMENT_TYPES[i % 4])
        for i, v in enumerate(range(1, 11))
        for _ in range(2)
    ]
    report = scan_motif_placements(
        schedule, total_chapters=300, volume_count=10
    )
    assert report.placement_count == 20
    assert report.critical_count == 0


def test_handles_placements_without_volume_number():
    """Placements that only carry chapter_number (legacy narrative
    specs) are counted toward the total but not per-volume — we still
    warn about the dead zones."""

    schedule = [
        {"placement_type": "plant", "chapter_number": 1},
        {"placement_type": "echo", "chapter_number": 50},
        {"placement_type": "transform", "chapter_number": 150},
        {"placement_type": "resolve", "chapter_number": 299},
    ]
    report = scan_motif_placements(
        schedule, total_chapters=300, volume_count=10
    )
    # Still fails the floor (4 << 20) and the per-volume dead-zone check.
    codes = {f.code for f in report.findings}
    assert "starved_motif_placements" in codes
    assert "volume_motif_dead_zone" in codes


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def test_report_to_prompt_block_lists_bounds():
    report = scan_motif_placements(
        [], total_chapters=300, volume_count=10
    )
    block = report.to_prompt_block(language="zh-CN")
    assert block
    assert str(report.placement_bounds.floor) in block
    assert str(report.placement_bounds.ceiling) in block


def test_report_to_prompt_block_empty_when_no_findings():
    report = scan_motif_placements(
        _healthy_schedule(10), total_chapters=300, volume_count=10
    )
    assert report.to_prompt_block() == ""


def test_render_constraints_block_zh():
    block = render_motif_constraints_block(
        total_chapters=316, volume_count=24, language="zh-CN"
    )
    assert "316" in block
    assert "24" in block
    assert "motif" in block.lower()
    assert "硬性要求" in block


def test_render_constraints_block_en():
    block = render_motif_constraints_block(
        total_chapters=544, volume_count=16, language="en-US"
    )
    assert "544" in block
    assert "16" in block
    assert "motif" in block.lower()
    assert "hard constraints" in block.lower()


def test_render_constraints_block_handles_single_volume():
    block = render_motif_constraints_block(
        total_chapters=30, volume_count=1, language="zh-CN"
    )
    # Must not divide by zero or crash
    assert block
    assert str(MIN_TOTAL_MOTIFS) in block or "8" in block


# ---------------------------------------------------------------------------
# English messaging
# ---------------------------------------------------------------------------

def test_english_messages_when_language_en():
    schedule = [_placement(1, ptype="plant")]
    report = scan_motif_placements(
        schedule, total_chapters=316, volume_count=24, language="en-US"
    )
    starved = next(
        f for f in report.findings if f.code == "starved_motif_placements"
    )
    assert "motif" in starved.message.lower()
    assert "volumes" in starved.message.lower()


# ---------------------------------------------------------------------------
# is_critical predicate
# ---------------------------------------------------------------------------

def test_is_critical_only_when_critical_findings_exist():
    """A bloat warning alone must not mark the report critical."""

    # Healthy per-volume coverage but way over ceiling: 100 placements
    # on 10 volumes.
    schedule = []
    for v in range(1, 11):
        for i in range(10):
            schedule.append(
                _placement(v, ptype=EXPECTED_PLACEMENT_TYPES[i % 4])
            )
    report = scan_motif_placements(
        schedule, total_chapters=300, volume_count=10
    )
    assert report.warning_count >= 1
    assert report.critical_count == 0
    assert report.is_critical is False
