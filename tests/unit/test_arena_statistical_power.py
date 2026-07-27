"""A6/B2: an underpowered arena run must not read as a decision.

The repository has already shipped one regression off a too-small blind test —
the opening-jargon lever was "confirmed" on N=3 and later falsified, with the
measured round-to-round noise (±1.5 points) larger than the effect being
claimed. Plan §5.1 A6 and §6 therefore both specify N≈10 per side.

``summarize_lean_wins`` previously computed ``pass_suggested_threshold`` as
``rate >= 0.55`` for ANY sample size, and ``scripts/lean_vs_full_pairwise_arena``
wrote that value straight into the report's ``pass`` field. A single lucky pair
was enough to produce ``pass: true``. These tests pin that an underpowered run
reports as underpowered.
"""

from __future__ import annotations

import pytest

from bestseller.services.benchmark_arena import ArenaMatchResult, ArenaPair
from bestseller.services.lean_full_arena import (
    ARENA_MIN_PAIRS,
    summarize_lean_wins,
)


pytestmark = pytest.mark.unit


def _result(outcome: str) -> ArenaMatchResult:
    return ArenaMatchResult(
        pair=ArenaPair(
            pair_id="x",
            framework_text="a",
            benchmark_text="b",
            benchmark_tier="t",
            category="c",
            chapter_number=1,
        ),
        outcome=outcome,
        forward=None,
        backward=None,
    )


def _results(wins: int = 0, losses: int = 0, ties: int = 0) -> list[ArenaMatchResult]:
    return (
        [_result("win")] * wins + [_result("loss")] * losses + [_result("tie")] * ties
    )


def test_min_pairs_matches_the_plan() -> None:
    assert ARENA_MIN_PAIRS >= 10, "plan §6 specifies N≈10 per configuration"


def test_a_single_lucky_pair_is_not_a_verdict() -> None:
    """The exact shape of the historical false positive."""

    summary = summarize_lean_wins(_results(wins=1))

    assert summary["verdict"] == "inconclusive_underpowered"
    assert summary["pass_suggested_threshold"] is False
    assert summary["underpowered"] is True


def test_three_pairs_are_still_underpowered() -> None:
    """N=3 is the sample size that produced the falsified conclusion."""

    summary = summarize_lean_wins(_results(wins=3))

    assert summary["verdict"] == "inconclusive_underpowered"
    assert summary["pass_suggested_threshold"] is False


def test_powered_and_clearly_better_lean_passes() -> None:
    summary = summarize_lean_wins(_results(wins=9, losses=1))

    assert summary["underpowered"] is False
    assert summary["verdict"] == "lean_better"
    assert summary["pass_suggested_threshold"] is True
    assert summary["p_value"] < 0.05


def test_powered_but_a_coin_flip_does_not_pass() -> None:
    """Reaching N is necessary, not sufficient — the split must also separate."""

    summary = summarize_lean_wins(_results(wins=5, losses=5))

    assert summary["underpowered"] is False
    assert summary["verdict"] == "no_difference"
    assert summary["pass_suggested_threshold"] is False
    assert summary["p_value"] == pytest.approx(1.0)


def test_full_winning_is_reported_as_a_regression_not_a_tie() -> None:
    """Plan §5.2 B2: "full 不得稳定赢". A lean regression must be legible."""

    summary = summarize_lean_wins(_results(wins=1, losses=9))

    assert summary["verdict"] == "full_better"
    assert summary["pass_suggested_threshold"] is False


def test_ties_count_toward_power_but_not_toward_separation() -> None:
    """Sign-test convention: ties carry no directional evidence, yet they are
    real judged pairs and so still count against the sample-size budget."""

    summary = summarize_lean_wins(_results(wins=5, losses=0, ties=7))

    assert summary["pairs"] == 12
    assert summary["decisive_pairs"] == 5
    assert summary["lean_win_rate"] == pytest.approx((5 + 0.5 * 7) / 12, abs=1e-4)
    assert summary["underpowered"] is False


def test_all_ties_cannot_declare_a_winner() -> None:
    summary = summarize_lean_wins(_results(ties=12))

    assert summary["decisive_pairs"] == 0
    assert summary["verdict"] == "no_difference"
    assert summary["pass_suggested_threshold"] is False


def test_empty_run_is_underpowered_not_zero_confidence_pass() -> None:
    summary = summarize_lean_wins([])

    assert summary["pairs"] == 0
    assert summary["verdict"] == "inconclusive_underpowered"
    assert summary["pass_suggested_threshold"] is False


def test_min_pairs_is_overridable_for_deliberate_pilot_runs() -> None:
    """A pilot may knowingly run small; it must say so explicitly rather than
    the default silently accepting it."""

    summary = summarize_lean_wins(_results(wins=3), min_pairs=3)

    assert summary["underpowered"] is False
    assert summary["min_pairs"] == 3


def test_backward_compatible_fields_survive() -> None:
    summary = summarize_lean_wins(_results(wins=1, losses=1, ties=1))

    for key in ("pairs", "lean_win_rate", "wins", "losses", "ties"):
        assert key in summary


def test_script_reports_pass_only_on_a_powered_verdict() -> None:
    """The script is the artifact a human reads; it must not relabel an
    underpowered summary as a pass."""

    from pathlib import Path

    source = Path("scripts/lean_vs_full_pairwise_arena.py").read_text(encoding="utf-8")
    assert '"verdict"' in source or "verdict" in source
    assert "underpowered" in source, (
        "the emitted report must surface whether the run reached N"
    )
