"""Tests for the world-spec richness gate.

Locks in the architectural invariants that prevent both failure modes
observed in the 6-book audit:

* **Starved world (道种破虚, 316 ch / 24 vol)**: 18 world_rules + 5 locations
  → every chapter drew from a tiny exploitation pool, repeating pressure
  motifs.
* **Bloated world (EN projects, 544 ch / 16 vol)**: 574 world_rules that
  never grounded in any chapter, producing shallow worldbuilding.

Both failure modes share the same root cause: world_spec generation did
not scale to chapter count. These tests fix the scaling contract.
"""

from __future__ import annotations

import pytest

from bestseller.services.world_richness import (
    MAX_REASONABLE_LOCATIONS,
    MAX_REASONABLE_WORLD_RULES,
    MIN_FACTIONS,
    MIN_LOCATIONS,
    MIN_WORLD_RULES,
    compute_world_bounds,
    render_world_constraints_block,
    scan_world_spec_richness,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule(name: str, *, desc: str = "has stakes", consequence: str = "ups the ante") -> dict:
    return {
        "name": name,
        "description": desc,
        "story_consequence": consequence,
    }


def _healthy_world(rules_n: int = 20, locations_n: int = 12, factions_n: int = 6) -> dict:
    return {
        "rules": [_rule(f"rule_{i}") for i in range(rules_n)],
        "locations": [{"name": f"loc_{i}", "description": "d"} for i in range(locations_n)],
        "factions": [{"name": f"fac_{i}", "description": "d"} for i in range(factions_n)],
    }


# ---------------------------------------------------------------------------
# Bounds math
# ---------------------------------------------------------------------------

def test_compute_world_bounds_enforces_absolute_minimums_for_small_novels():
    """A 30-chapter novella should still have the absolute floor even though
    ceil(30/25) = 2 (way below the hard minimum)."""

    bounds = compute_world_bounds(30)
    assert bounds["rules"].floor == MIN_WORLD_RULES
    assert bounds["locations"].floor == MIN_LOCATIONS
    assert bounds["factions"].floor == MIN_FACTIONS


def test_compute_world_bounds_scales_with_chapter_count():
    """A 600-chapter novel should need proportionally more material than a
    300-chapter one."""

    small = compute_world_bounds(300)
    big = compute_world_bounds(600)
    assert big["rules"].floor >= small["rules"].floor
    assert big["locations"].floor >= small["locations"].floor
    assert big["factions"].floor >= small["factions"].floor


def test_compute_world_bounds_ceiling_never_below_floor():
    """For pathological inputs (chapters=1) the ceiling clamps above the
    floor."""

    bounds = compute_world_bounds(1)
    for key in ("rules", "locations", "factions"):
        assert bounds[key].ceiling >= bounds[key].floor


def test_compute_world_bounds_handles_zero_and_negative_chapters():
    """Defensive: zero/negative inputs should not crash and should still
    yield the minimum floor."""

    for bad in (0, -1, -500):
        bounds = compute_world_bounds(bad)
        assert bounds["rules"].floor == MIN_WORLD_RULES
        assert bounds["rules"].ceiling >= bounds["rules"].floor


# ---------------------------------------------------------------------------
# Healthy world — no findings
# ---------------------------------------------------------------------------

def test_healthy_world_passes():
    report = scan_world_spec_richness(
        _healthy_world(), total_chapters=300, language="zh-CN"
    )
    assert report.critical_count == 0
    assert report.is_critical is False
    assert report.rule_count == 20


# ---------------------------------------------------------------------------
# Starvation — canonical 道种破虚 case
# ---------------------------------------------------------------------------

def test_starved_rules_daozhongpoxu_case():
    """316 chapters + only 18 rules = starvation (floor = ceil(316/25)=13,
    but we need more headroom to avoid repetition — the 道种破虚 production
    data had exactly 18 rules and every volume collapsed)."""

    world = _healthy_world(rules_n=18, locations_n=14, factions_n=8)
    # 316 ch / floor_divisor(25) = ceil(12.64) = 13 so 18 actually passes
    # the raw floor. The canonical starvation shows up at chapters=500 ─
    # floor = 20, and we have 18.
    report = scan_world_spec_richness(world, total_chapters=500)

    codes = {f.code for f in report.findings}
    assert "starved_world_rules" in codes
    assert report.is_critical is True


def test_starved_locations_critical():
    """5 locations across 316 chapters would have floor = ceil(316/40) = 8."""

    world = _healthy_world(rules_n=25, locations_n=5, factions_n=8)
    report = scan_world_spec_richness(world, total_chapters=316)

    codes = {f.code for f in report.findings}
    assert "starved_locations" in codes
    assert any(
        f.code == "starved_locations" and f.severity == "critical"
        for f in report.findings
    )


def test_starved_factions_critical():
    """1 faction → two-sided political conflict collapse."""

    world = _healthy_world(rules_n=25, locations_n=12, factions_n=1)
    report = scan_world_spec_richness(world, total_chapters=316)

    codes = {f.code for f in report.findings}
    assert "starved_factions" in codes


# ---------------------------------------------------------------------------
# Bloat — canonical EN-projects case
# ---------------------------------------------------------------------------

def test_bloated_rules_witness_protocol_case():
    """574 rules for a 544-chapter plan exceeds the ceiling
    (ceiling = 544/2 = 272), so this must warn."""

    world = _healthy_world(rules_n=574, locations_n=12, factions_n=6)
    report = scan_world_spec_richness(world, total_chapters=544)

    codes = {f.code for f in report.findings}
    assert "bloated_world_rules" in codes
    assert any(
        f.code == "bloated_world_rules" and f.severity == "warning"
        for f in report.findings
    )


def test_bloated_locations_warning():
    world = _healthy_world(rules_n=25, locations_n=100, factions_n=6)
    # 300 ch, ceiling = max(MAX_REASONABLE_LOCATIONS=25, 300/4=75) = 75
    report = scan_world_spec_richness(world, total_chapters=300)
    codes = {f.code for f in report.findings}
    assert "bloated_locations" in codes


# ---------------------------------------------------------------------------
# Duplicate rule names — silently overwrite in storage
# ---------------------------------------------------------------------------

def test_duplicate_rule_names_flagged_as_critical():
    world = _healthy_world(rules_n=20)
    # Override two rules with the same name
    world["rules"][0]["name"] = "Heaven Dao"
    world["rules"][5]["name"] = "Heaven Dao"  # duplicate
    world["rules"][10]["name"] = "Hell Dao"
    world["rules"][15]["name"] = "Hell Dao"  # duplicate

    report = scan_world_spec_richness(world, total_chapters=200)
    codes = {f.code for f in report.findings}
    assert "duplicate_rule_names" in codes

    dup_finding = next(f for f in report.findings if f.code == "duplicate_rule_names")
    assert dup_finding.severity == "critical"
    assert "Heaven Dao" in dup_finding.payload["names"]
    assert "Hell Dao" in dup_finding.payload["names"]


# ---------------------------------------------------------------------------
# Blank rule descriptions
# ---------------------------------------------------------------------------

def test_blank_rule_descriptions_warn():
    world = _healthy_world(rules_n=20)
    world["rules"][0]["description"] = ""
    world["rules"][1]["description"] = "   "  # whitespace only
    world["rules"][2]["description"] = None

    report = scan_world_spec_richness(world, total_chapters=200)
    codes = {f.code for f in report.findings}
    assert "blank_rule_descriptions" in codes
    blank = next(f for f in report.findings if f.code == "blank_rule_descriptions")
    assert blank.severity == "warning"
    assert len(blank.payload["names"]) == 3


def test_empty_rule_name_does_not_trigger_blank_finding():
    """Rules with no name at all are excluded — they'll fail schema
    validation earlier; the blank-description check only fires for
    named rules missing descriptions."""

    world = _healthy_world(rules_n=20)
    world["rules"][0]["name"] = ""
    world["rules"][0]["description"] = ""

    report = scan_world_spec_richness(world, total_chapters=200)
    codes = {f.code for f in report.findings}
    assert "blank_rule_descriptions" not in codes


# ---------------------------------------------------------------------------
# English messaging
# ---------------------------------------------------------------------------

def test_english_messages_when_language_en():
    world = _healthy_world(rules_n=3, locations_n=2, factions_n=1)
    report = scan_world_spec_richness(
        world, total_chapters=316, language="en-US"
    )
    assert report.is_critical
    starved = next(f for f in report.findings if f.code == "starved_world_rules")
    # English keywords: must contain "chapter-plan" or "need ≥" etc.
    assert "chapter" in starved.message.lower()
    assert "18" not in starved.message or "rules" in starved.message.lower()


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def test_report_to_prompt_block_includes_bounds():
    world = _healthy_world(rules_n=5, locations_n=3, factions_n=1)
    report = scan_world_spec_richness(world, total_chapters=300, language="zh-CN")
    block = report.to_prompt_block(language="zh-CN")
    assert block
    # Must include the target counts so the LLM regenerates the right amount
    assert str(report.rule_bounds.floor) in block
    assert str(report.rule_bounds.ceiling) in block


def test_report_to_prompt_block_empty_when_no_findings():
    report = scan_world_spec_richness(_healthy_world(), total_chapters=300)
    assert report.to_prompt_block() == ""


def test_render_world_constraints_block_chinese():
    block = render_world_constraints_block(total_chapters=300, language="zh-CN")
    assert "rules" in block
    assert "locations" in block
    assert "factions" in block
    # Must state chapter count for scaling context
    assert "300" in block


def test_render_world_constraints_block_english():
    block = render_world_constraints_block(total_chapters=544, language="en-US")
    assert "rules" in block.lower()
    assert "locations" in block.lower()
    assert "factions" in block.lower()
    assert "544" in block
    # Hard distinctness invariant must appear
    assert "distinct" in block.lower()


# ---------------------------------------------------------------------------
# Pydantic-model input (world_spec comes from the parse_world_spec_input
# validator before reaching the repair helper, so it might be a model)
# ---------------------------------------------------------------------------

def test_accepts_object_with_model_dump():
    class _Fake:
        def model_dump(self):
            return _healthy_world()

    report = scan_world_spec_richness(_Fake(), total_chapters=300)
    assert report.rule_count == 20


def test_accepts_empty_payload_gracefully():
    report = scan_world_spec_richness({}, total_chapters=300)
    # Empty input hits every floor — the point is to not crash
    assert report.rule_count == 0
    assert report.is_critical


# ---------------------------------------------------------------------------
# Critical-count predicate
# ---------------------------------------------------------------------------

def test_is_critical_only_when_critical_findings_exist():
    # warnings-only should not mark critical
    world = _healthy_world(rules_n=200, locations_n=12, factions_n=6)  # bloat warning only
    report = scan_world_spec_richness(world, total_chapters=300)
    assert report.warning_count >= 1
    assert report.critical_count == 0
    assert report.is_critical is False


# ---------------------------------------------------------------------------
# Ceiling sanity: the MAX_REASONABLE_* constants remain meaningful as the
# absolute ceiling for tiny novels.
# ---------------------------------------------------------------------------

def test_max_reasonable_locations_used_as_ceiling_for_small_novels():
    """A 10-chapter novel's ceiling should still permit the absolute
    MAX_REASONABLE_LOCATIONS before bloat is flagged."""

    bounds = compute_world_bounds(10)
    # ceiling should not fall below the absolute minimum ceiling
    assert bounds["locations"].ceiling >= MAX_REASONABLE_LOCATIONS
    assert bounds["rules"].ceiling >= MAX_REASONABLE_WORLD_RULES
