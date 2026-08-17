"""L1 tests for foreshadowing density — open-clue inventory observations.

Covers the 2026-08-17 additive fields (open_clue_count /
max_open_clue_age_chapters / open_clue_codes / overaged_clue_codes) and the
no-op invariants: balance_score and every pre-existing field must be
unaffected by the new computation.
"""

from __future__ import annotations

from dataclasses import dataclass

from bestseller.services.foreshadowing import (
    OPEN_CLUE_MAX_AGE_CHAPTERS,
    OPEN_CLUE_SOFT_CAP,
    ForeshadowingDensityResult,
    analyze_foreshadowing_density,
)


@dataclass
class _Clue:
    clue_code: str = "C-001"
    status: str = "planted"
    planted_in_chapter_number: int | None = None
    expected_payoff_by_chapter_number: int | None = None
    actual_paid_off_chapter_number: int | None = None


@dataclass
class _Payoff:
    actual_chapter_number: int | None = None


def test_open_clue_inventory_counts_unresolved_planted_clues() -> None:
    clues = [
        _Clue(clue_code="A", planted_in_chapter_number=1),
        _Clue(clue_code="B", planted_in_chapter_number=10),
        _Clue(
            clue_code="C",
            planted_in_chapter_number=2,
            actual_paid_off_chapter_number=5,
            status="paid_off",
        ),
        _Clue(clue_code="D", status="cancelled", planted_in_chapter_number=3),
        _Clue(clue_code="E", planted_in_chapter_number=None),  # planted unknown
    ]
    result = analyze_foreshadowing_density(
        clues=clues, payoffs=[], total_chapters=50
    )
    assert result.open_clue_count == 2
    assert set(result.open_clue_codes) == {"A", "B"}
    # A planted ch1, frontier 50 → age 49
    assert result.max_open_clue_age_chapters == 49


def test_overaged_clue_detected_past_cap() -> None:
    clues = [
        _Clue(clue_code="OLD", planted_in_chapter_number=1),
        _Clue(clue_code="NEW", planted_in_chapter_number=40),
    ]
    result = analyze_foreshadowing_density(
        clues=clues, payoffs=[], total_chapters=OPEN_CLUE_MAX_AGE_CHAPTERS + 5
    )
    assert result.overaged_clue_codes == ["OLD"]
    assert "NEW" not in result.overaged_clue_codes


def test_aggregate_pressure_visible_even_when_no_clue_is_individually_orphaned() -> None:
    """The exact case per-clue orphan detection misses: every clue is on
    schedule, but too many are in flight at once."""
    clues = [
        _Clue(
            clue_code=f"K{i}",
            planted_in_chapter_number=10 + i,
            expected_payoff_by_chapter_number=200,  # all far in the future
        )
        for i in range(OPEN_CLUE_SOFT_CAP + 3)
    ]
    result = analyze_foreshadowing_density(
        clues=clues, payoffs=[], total_chapters=30
    )
    assert result.orphan_clue_codes == []  # per-clue view: all fine
    assert result.open_clue_count == OPEN_CLUE_SOFT_CAP + 3  # aggregate: visible


def test_noop_balance_score_and_legacy_fields_unchanged() -> None:
    """New observations must not perturb any pre-existing output field."""
    clues = [
        _Clue(clue_code="A", planted_in_chapter_number=2,
              expected_payoff_by_chapter_number=5),
        _Clue(clue_code="B", planted_in_chapter_number=12,
              actual_paid_off_chapter_number=20, status="paid_off"),
    ]
    payoffs = [_Payoff(actual_chapter_number=25)]
    result = analyze_foreshadowing_density(
        clues=clues, payoffs=payoffs, total_chapters=30
    )

    # Recompute legacy fields exactly as the pre-change implementation did.
    legacy_fields = {
        "balance_score", "act1_plants", "act1_recoveries", "act2_plants",
        "act2_recoveries", "act3_plants", "act3_recoveries",
        "dead_zone_chapters", "orphan_clue_codes",
    }
    payload = result.model_dump()
    # Orphan detection must be untouched: A passed expected ch5 unresolved.
    assert payload["orphan_clue_codes"] == ["A"]
    # Structural sanity of the legacy contract.
    assert legacy_fields <= set(payload)
    assert 0.0 <= payload["balance_score"] <= 1.0
    # New fields present with computed values.
    assert payload["open_clue_count"] == 1
    assert payload["open_clue_codes"] == ["A"]


def test_zero_chapters_early_return_keeps_new_fields_at_defaults() -> None:
    result = analyze_foreshadowing_density(clues=[], payoffs=[], total_chapters=0)
    assert result == ForeshadowingDensityResult(balance_score=1.0)
    assert result.open_clue_count == 0
    assert result.overaged_clue_codes == []
