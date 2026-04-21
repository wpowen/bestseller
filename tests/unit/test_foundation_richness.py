"""Tests for the foundation richness gate.

The xianxia-upgrade-1776137730 project (道种破虚) was the canonical failure
case: 24 volumes × 1 antagonist = 24 identical "survival pressure" volume
goals. These tests lock in the architectural invariants that prevent the
cast spec from ever getting thin enough to force that convergence
downstream.
"""

from __future__ import annotations

import pytest

from bestseller.services.foundation_richness import (
    FORCES_PER_VOLUME_RATIO,
    MAX_FORCE_SHARE,
    MIN_VOLUME_COVERAGE,
    render_foundation_constraints_block,
    scan_cast_foundation_richness,
)


# ---------------------------------------------------------------------------
# Baseline: healthy cast passes cleanly
# ---------------------------------------------------------------------------

def _make_healthy_cast(volume_count: int = 12) -> dict:
    """Six diverse forces, each covering a distinct 2-volume slice."""

    # 12 volumes / 4 ratio = 3 forces minimum; we give 6 to exceed the floor
    forces = [
        {"name": "survival pressure faction", "force_type": "faction",
         "active_volumes": [1, 2]},
        {"name": "political intrigue house", "force_type": "systemic",
         "active_volumes": [3, 4]},
        {"name": "betrayal cell", "force_type": "character",
         "active_volumes": [5, 6]},
        {"name": "faction war alliance", "force_type": "faction",
         "active_volumes": [7, 8]},
        {"name": "existential void entity", "force_type": "environment",
         "active_volumes": [9, 10]},
        {"name": "internal reckoning", "force_type": "internal",
         "active_volumes": [11, 12]},
    ]
    supporting_cast = [
        {"name": f"antag_{i}", "role": "antagonist"} for i in range(4)
    ]
    return {
        "antagonist_forces": forces,
        "supporting_cast": supporting_cast,
    }


def test_healthy_cast_passes():
    cast = _make_healthy_cast()
    report = scan_cast_foundation_richness(cast, volume_count=12)
    assert report.critical_count == 0
    assert report.warning_count == 0
    assert report.force_count == 6
    assert report.coverage_ratio == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Force count insufficiency
# ---------------------------------------------------------------------------

def test_insufficient_force_count_24_vol_1_force_is_critical():
    # 24 vols → requires ceil(24/4) = 6 forces; we give 1.
    cast = {
        "antagonist_forces": [
            {"name": "元婴老者势力", "force_type": "character",
             "active_volumes": list(range(1, 25))},
        ],
        "supporting_cast": [],
    }
    report = scan_cast_foundation_richness(cast, volume_count=24)
    codes = {f.code for f in report.findings}
    assert "insufficient_force_count" in codes
    assert "single_force_dominance" in codes  # 100% share
    assert report.forces_required == 6
    assert report.is_critical


def test_empty_forces_is_critical():
    cast = {"antagonist_forces": [], "supporting_cast": []}
    report = scan_cast_foundation_richness(cast, volume_count=10)
    codes = {f.code for f in report.findings}
    assert "insufficient_force_count" in codes
    # Coverage check short-circuits when forces empty — we only report
    # counting issues.
    assert "insufficient_volume_coverage" not in codes


# ---------------------------------------------------------------------------
# Volume coverage
# ---------------------------------------------------------------------------

def test_partial_active_volumes_coverage_flagged_critical():
    # 10 volumes, 3 forces that only cover vols 1-3 (30%).
    cast = {
        "antagonist_forces": [
            {"name": "a", "force_type": "faction", "active_volumes": [1]},
            {"name": "b", "force_type": "faction", "active_volumes": [2]},
            {"name": "c", "force_type": "faction", "active_volumes": [3]},
        ],
        "supporting_cast": [
            {"name": "antag1", "role": "antagonist"},
            {"name": "antag2", "role": "antagonist"},
            {"name": "antag3", "role": "antagonist"},
            {"name": "antag4", "role": "antagonist"},
        ],
    }
    report = scan_cast_foundation_richness(cast, volume_count=10)
    codes = {f.code for f in report.findings}
    assert "insufficient_volume_coverage" in codes
    payload = next(f.payload for f in report.findings
                   if f.code == "insufficient_volume_coverage")
    assert sorted(payload["uncovered"]) == [4, 5, 6, 7, 8, 9, 10]


def test_full_coverage_meets_minimum():
    # 10 volumes, 3 forces covering every volume via overlap — meets floor.
    cast = {
        "antagonist_forces": [
            {"name": "a", "force_type": "faction",
             "active_volumes": [1, 2, 3, 4]},
            {"name": "b", "force_type": "character",
             "active_volumes": [4, 5, 6]},
            {"name": "c", "force_type": "systemic",
             "active_volumes": [7, 8, 9, 10]},
        ],
        "supporting_cast": [
            {"name": f"a{i}", "role": "antagonist"} for i in range(4)
        ],
    }
    report = scan_cast_foundation_richness(cast, volume_count=10)
    codes = {f.code for f in report.findings}
    assert "insufficient_volume_coverage" not in codes


# ---------------------------------------------------------------------------
# Single-force dominance
# ---------------------------------------------------------------------------

def test_single_force_over_40_percent_is_critical():
    # 10 volumes, one force spans 6/10 = 60% (above 40% cap).
    cast = {
        "antagonist_forces": [
            {"name": "dominant", "force_type": "faction",
             "active_volumes": [1, 2, 3, 4, 5, 6]},
            {"name": "b", "force_type": "systemic",
             "active_volumes": [7, 8]},
            {"name": "c", "force_type": "character",
             "active_volumes": [9, 10]},
        ],
        "supporting_cast": [
            {"name": f"a{i}", "role": "antagonist"} for i in range(4)
        ],
    }
    report = scan_cast_foundation_richness(cast, volume_count=10)
    codes = {f.code for f in report.findings}
    assert "single_force_dominance" in codes
    finding = next(f for f in report.findings
                   if f.code == "single_force_dominance")
    assert finding.payload["force"] == "dominant"


def test_force_share_at_threshold_not_flagged():
    # 10 volumes, largest force at 4/10 = 40% exactly (not > threshold).
    cast = {
        "antagonist_forces": [
            {"name": "a", "force_type": "faction",
             "active_volumes": [1, 2, 3, 4]},
            {"name": "b", "force_type": "systemic",
             "active_volumes": [5, 6, 7]},
            {"name": "c", "force_type": "character",
             "active_volumes": [8, 9, 10]},
        ],
        "supporting_cast": [
            {"name": f"a{i}", "role": "antagonist"} for i in range(4)
        ],
    }
    report = scan_cast_foundation_richness(cast, volume_count=10)
    codes = {f.code for f in report.findings}
    assert "single_force_dominance" not in codes


# ---------------------------------------------------------------------------
# Force type diversity
# ---------------------------------------------------------------------------

def test_single_force_type_is_warning_only():
    # All forces are "character" — warning, not critical.
    cast = {
        "antagonist_forces": [
            {"name": "alpha", "force_type": "character",
             "active_volumes": [1, 2]},
            {"name": "beta", "force_type": "character",
             "active_volumes": [3, 4]},
            {"name": "gamma", "force_type": "character",
             "active_volumes": [5, 6]},
        ],
        "supporting_cast": [
            {"name": f"a{i}", "role": "antagonist"} for i in range(4)
        ],
    }
    report = scan_cast_foundation_richness(cast, volume_count=6)
    finding = next(
        (f for f in report.findings if f.code == "insufficient_force_type_diversity"),
        None,
    )
    assert finding is not None
    assert finding.severity == "warning"


# ---------------------------------------------------------------------------
# Generic force names
# ---------------------------------------------------------------------------

def test_generic_force_names_flagged_warning():
    cast = {
        "antagonist_forces": [
            {"name": "反派", "force_type": "character",
             "active_volumes": [1, 2, 3]},
            {"name": "specific shadow guild", "force_type": "faction",
             "active_volumes": [4, 5, 6]},
        ],
        "supporting_cast": [
            {"name": f"a{i}", "role": "antagonist"} for i in range(3)
        ],
    }
    report = scan_cast_foundation_richness(cast, volume_count=6)
    finding = next(
        (f for f in report.findings if f.code == "generic_force_names"),
        None,
    )
    assert finding is not None
    assert finding.severity == "warning"
    assert "反派" in finding.payload["names"]


# ---------------------------------------------------------------------------
# Supporting-cast antagonist breadth
# ---------------------------------------------------------------------------

def test_thin_supporting_antagonists_is_warning():
    # 12 volumes requires ceil(12/3) = 4 antagonist-role supporting cast.
    cast = {
        "antagonist_forces": [
            {"name": "a", "force_type": "faction",
             "active_volumes": [1, 2, 3, 4]},
            {"name": "b", "force_type": "systemic",
             "active_volumes": [5, 6, 7, 8]},
            {"name": "c", "force_type": "character",
             "active_volumes": [9, 10, 11, 12]},
        ],
        "supporting_cast": [
            {"name": "solo_antag", "role": "antagonist"},
        ],
    }
    report = scan_cast_foundation_richness(cast, volume_count=12)
    finding = next(
        (f for f in report.findings if f.code == "insufficient_supporting_antagonists"),
        None,
    )
    assert finding is not None
    assert finding.severity == "warning"


# ---------------------------------------------------------------------------
# Xianxia real-world failure case lock-in
# ---------------------------------------------------------------------------

def test_xianxia_real_failure_case_flagged_critical():
    """Lock in the 道种破虚 failure pattern: all 24 volumes, one antagonist."""

    cast = {
        # The actual xianxia project cast had antagonist_forces effectively
        # collapsed to a single faceless pressure — either empty list or
        # one force with no active_volumes.
        "antagonist_forces": [
            {"name": "生存压力", "force_type": "character",
             "active_volumes": []},
        ],
        "supporting_cast": [
            {"name": "元婴老者", "role": "antagonist"},
        ],
    }
    report = scan_cast_foundation_richness(cast, volume_count=24)
    assert report.is_critical
    codes = {f.code for f in report.findings}
    assert "insufficient_force_count" in codes
    assert "insufficient_volume_coverage" in codes


# ---------------------------------------------------------------------------
# Prompt block rendering
# ---------------------------------------------------------------------------

def test_to_prompt_block_zh_non_empty_when_findings_present():
    cast = {"antagonist_forces": [], "supporting_cast": []}
    report = scan_cast_foundation_richness(cast, volume_count=12)
    block = report.to_prompt_block(language="zh-CN")
    assert block
    assert "基础素材丰富度修复" in block
    assert "antagonist_forces" in block
    # We should see the literal required-forces count (3 for 12 volumes).
    assert "3" in block


def test_to_prompt_block_en_non_empty_when_findings_present():
    cast = {"antagonist_forces": [], "supporting_cast": []}
    report = scan_cast_foundation_richness(cast, volume_count=12)
    block = report.to_prompt_block(language="en-US")
    assert block
    assert "FOUNDATION RICHNESS REPAIR" in block
    assert "antagonist_forces" in block


def test_to_prompt_block_empty_when_no_findings():
    cast = _make_healthy_cast()
    report = scan_cast_foundation_richness(cast, volume_count=12)
    assert report.to_prompt_block(language="zh-CN") == ""
    assert report.to_prompt_block(language="en-US") == ""


def test_render_foundation_constraints_block_zh_has_numeric_floors():
    block = render_foundation_constraints_block(volume_count=24, language="zh-CN")
    assert "antagonist_forces" in block
    # 24 / 4 = 6 forces required; check the number survives rendering.
    assert "6" in block
    # 40% cap → at most 9 volumes.
    assert "9" in block


def test_render_foundation_constraints_block_en():
    block = render_foundation_constraints_block(volume_count=12, language="en-US")
    assert "FOUNDATION RICHNESS" in block
    assert "antagonist_forces" in block


# ---------------------------------------------------------------------------
# Input normalization edges
# ---------------------------------------------------------------------------

def test_active_volumes_out_of_range_ignored():
    # Volumes outside [1, volume_count] shouldn't poison the coverage set.
    cast = {
        "antagonist_forces": [
            {"name": "a", "force_type": "faction",
             "active_volumes": [1, 2, 99, -3, "bad"]},
            {"name": "b", "force_type": "character",
             "active_volumes": [3, 4]},
            {"name": "c", "force_type": "systemic",
             "active_volumes": [5]},
        ],
        "supporting_cast": [
            {"name": f"a{i}", "role": "antagonist"} for i in range(3)
        ],
    }
    report = scan_cast_foundation_richness(cast, volume_count=5)
    assert report.distinct_volume_coverage == 5
    assert report.coverage_ratio == pytest.approx(1.0)


def test_pydantic_style_mapping_is_accepted():
    class _PseudoModel:
        def model_dump(self):
            return {
                "antagonist_forces": [
                    {"name": "a", "force_type": "faction",
                     "active_volumes": [1, 2, 3]},
                    {"name": "b", "force_type": "systemic",
                     "active_volumes": [4, 5, 6]},
                ],
                "supporting_cast": [
                    {"name": "antag", "role": "antagonist"},
                ],
            }

    report = scan_cast_foundation_richness(_PseudoModel(), volume_count=6)
    assert report.force_count == 2
    assert report.distinct_volume_coverage == 6


def test_volume_count_zero_treated_as_one():
    # Defensive — volume_count=0 shouldn't crash; min 1.
    cast = {"antagonist_forces": [], "supporting_cast": []}
    report = scan_cast_foundation_richness(cast, volume_count=0)
    assert report.volume_count == 1
    assert report.forces_required == 1


def test_determinism():
    cast = _make_healthy_cast(12)
    r1 = scan_cast_foundation_richness(cast, volume_count=12)
    r2 = scan_cast_foundation_richness(cast, volume_count=12)
    assert r1 == r2


# ---------------------------------------------------------------------------
# Ratio constants expose tunable floors
# ---------------------------------------------------------------------------

def test_tunable_constants_exported():
    assert FORCES_PER_VOLUME_RATIO >= 1
    assert 0 < MAX_FORCE_SHARE < 1
    assert 0 < MIN_VOLUME_COVERAGE <= 1


# ---------------------------------------------------------------------------
# Duplicate force names — the "24 copies of 生存压力" xianxia post-repair bug
# ---------------------------------------------------------------------------

def test_duplicate_force_names_flagged_critical():
    """When the LLM produces six forces all named '生存压力', each covering a
    different volume, the coverage math passes but the per-volume narrative
    still reads as "same antagonist every volume". Richness must reject it."""

    forces = [
        {"name": "生存压力", "force_type": "faction", "active_volumes": [v]}
        for v in range(1, 25)
    ]
    cast = {
        "antagonist_forces": forces,
        "supporting_cast": [
            {"name": f"antag_{i}", "role": "antagonist"} for i in range(8)
        ],
    }
    report = scan_cast_foundation_richness(cast, volume_count=24)
    codes = {f.code for f in report.findings}
    assert "duplicate_force_names" in codes
    dup_finding = next(f for f in report.findings if f.code == "duplicate_force_names")
    assert dup_finding.severity == "critical"
    assert "生存压力" in dup_finding.payload["names"]


def test_unique_force_names_do_not_trigger_duplicate_flag():
    """Healthy cast with distinct names should not trigger the duplicate flag."""

    cast = _make_healthy_cast(12)
    report = scan_cast_foundation_richness(cast, volume_count=12)
    codes = {f.code for f in report.findings}
    assert "duplicate_force_names" not in codes
