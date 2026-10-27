from bestseller.services.benchmark_arena import (
    ArenaRequiredCell,
    FormalArenaPair,
    arena_point_estimate,
    evaluate_formal_arena_cell,
)


def _pairs(score=1.0):
    return [
        FormalArenaPair(str(g), str(b), str(a), score)
        for g in range(8)
        for a in range(8)
        for b in range(12)
    ]


def test_formula_is_equal_at_generated_and_author_levels() -> None:
    pairs = _pairs()
    pairs[0] = FormalArenaPair("0", "0", "0", 0.0)
    assert arena_point_estimate(pairs) == 767 / 768


def test_invalid_pair_is_zero_and_power_metadata_is_sealed() -> None:
    pairs = _pairs(1.0)
    pairs[0] = FormalArenaPair("0", "0", "0", 1.0, valid=False)
    result = evaluate_formal_arena_cell(
        ArenaRequiredCell("g", "opening_ch1", "T2"),
        pairs,
        seed=7,
        bootstrap_iterations=20,
        achieved_power=0.9,
    )
    assert result.point_estimate == 767 / 768
    assert result.h0 == 0.50
    assert result.h1 == 0.60
    assert result.mde == 0.10
    assert result.planned_required_pairs == 96
    assert result.achieved_power == 0.9
    assert result.status == "PASS"


def test_power_or_floor_is_inconclusive_but_powered_threshold_failure_is_blocked() -> None:
    cell = ArenaRequiredCell("g", "opening_ch1", "T1")
    pairs = _pairs(0.0)
    underpowered = evaluate_formal_arena_cell(cell, pairs, bootstrap_iterations=10)
    assert underpowered.status == "INCONCLUSIVE"
    assert "POWER_BELOW_0_80" in underpowered.reasons

    blocked = evaluate_formal_arena_cell(cell, pairs, bootstrap_iterations=10, achieved_power=0.9)
    assert blocked.status == "BLOCKED"
    assert blocked.h0 == 0.35 and blocked.h1 == 0.45
