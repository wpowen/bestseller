from bestseller.services.benchmark_arena import (
    ArenaRequiredCell,
    FormalArenaPair,
    evaluate_required_arena_cells,
)
from bestseller.services.benchmark_experiment import (
    build_required_cell_manifest,
    seal_experiment_roster,
    seal_required_cell_manifest,
)


def _pairs(score=1.0):
    return [
        FormalArenaPair(str(g), str(b), str(a), score)
        for g in range(8)
        for a in range(8)
        for b in range(12)
    ]


def test_missing_required_cell_cannot_be_covered_by_another_cell() -> None:
    t2 = ArenaRequiredCell("g", "opening_ch1", "T2")
    t1 = ArenaRequiredCell("g", "opening_ch1", "T1")
    passed, results = evaluate_required_arena_cells(
        {t2: _pairs()}, required_cells={t1, t2}, bootstrap_iterations=10
    )
    assert passed is False
    assert results[t1.key].status == "INCONCLUSIVE"


def test_manifest_is_complete_and_seal_rejects_mutation() -> None:
    manifest = build_required_cell_manifest(genres=("g",), narrative_stages=("s",))
    assert len(manifest) == 2
    sealed = seal_required_cell_manifest(manifest)
    sealed.verify()
    sealed.payload[0]["tier"] = "TAMPERED"
    try:
        sealed.verify()
    except ValueError as exc:
        assert str(exc) == "SEALED_ROSTER_MISMATCH"
    else:
        raise AssertionError("mutation must invalidate a sealed manifest")


def test_roster_seal_uses_canonical_hash() -> None:
    left = seal_experiment_roster({"b": 2, "a": 1})
    right = seal_experiment_roster({"a": 1, "b": 2})
    assert left.manifest_hash == right.manifest_hash
