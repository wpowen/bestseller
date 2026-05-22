from __future__ import annotations

import pytest

from bestseller.domain.concept_leap import ConceptPool, ConceptSeed
from bestseller.services.concept_leap import (
    DEFAULT_CONCEPT_POOLS,
    generate_concept_leap,
    render_concept_candidate_block,
)

pytestmark = pytest.mark.unit


def test_default_pools_has_eight_distinct_pools() -> None:
    names = [p.name for p in DEFAULT_CONCEPT_POOLS]
    assert len(names) == 8
    assert len(set(names)) == 8


def test_default_pools_have_non_empty_seeds() -> None:
    for pool in DEFAULT_CONCEPT_POOLS:
        assert pool.seeds, f"pool {pool.name} is empty"
        for seed in pool.seeds:
            assert 0 <= seed.saturation_score <= 1


def test_generate_returns_top_k_candidates() -> None:
    result = generate_concept_leap(top_k=3, sample_size=40, seed=42)

    assert len(result.candidates) == 3
    assert result.seed == 42
    for candidate in result.candidates:
        assert len(candidate.seeds) == 4
        assert 0 <= candidate.combined_score <= 1


def test_generate_is_deterministic_with_seed() -> None:
    a = generate_concept_leap(seed=7, sample_size=40, top_k=5)
    b = generate_concept_leap(seed=7, sample_size=40, top_k=5)

    assert [c.signature for c in a.candidates] == [c.signature for c in b.candidates]


def test_generate_pools_per_candidate_default_is_four() -> None:
    result = generate_concept_leap(seed=1, sample_size=20, top_k=2)
    for candidate in result.candidates:
        assert len(candidate.pools) == 4
        assert len(candidate.seeds) == 4


def test_generate_with_pools_per_candidate_three() -> None:
    result = generate_concept_leap(
        seed=1, pools_per_candidate=3, sample_size=20, top_k=2
    )
    for candidate in result.candidates:
        assert len(candidate.pools) == 3


def test_generate_rejects_too_few_pools() -> None:
    small_pools = [
        ConceptPool(name="p1", label="P1", seeds=[ConceptSeed(key="a", label="A")]),
        ConceptPool(name="p2", label="P2", seeds=[ConceptSeed(key="b", label="B")]),
    ]
    with pytest.raises(ValueError):
        generate_concept_leap(pools=small_pools, pools_per_candidate=3)


def test_generate_respects_pool_names_filter() -> None:
    result = generate_concept_leap(
        pool_names=["mythology", "science", "structure", "emotion"],
        seed=11,
        sample_size=20,
        top_k=3,
    )

    assert set(result.pools_sampled) == {"mythology", "science", "structure", "emotion"}
    for candidate in result.candidates:
        assert set(candidate.pools) <= {"mythology", "science", "structure", "emotion"}


def test_generate_respects_forbidden_seed_keys() -> None:
    result = generate_concept_leap(
        seed=3,
        sample_size=40,
        top_k=5,
        forbidden_seed_keys=["cthulhu", "kunlun"],
    )

    for candidate in result.candidates:
        keys = {s.key for s in candidate.seeds}
        assert "cthulhu" not in keys
        assert "kunlun" not in keys


def test_generate_saturation_cutoff_filters() -> None:
    # Cutoff 0.0 should reject everything; no candidates returned.
    result = generate_concept_leap(
        seed=1, sample_size=40, top_k=5, saturation_cutoff=0.0
    )
    assert result.candidates == []


def test_candidate_has_rationale_and_premise_hint() -> None:
    result = generate_concept_leap(seed=2, sample_size=30, top_k=1)
    candidate = result.best()
    assert candidate is not None
    assert candidate.rationale
    assert candidate.premise_hint
    assert candidate.signature == " × ".join(s.key for s in candidate.seeds)


def test_render_block_emits_zh() -> None:
    result = generate_concept_leap(seed=5, sample_size=30, top_k=1)
    block = render_concept_candidate_block(result.best())

    assert "概念跨界候选" in block
    assert "组合签名" in block
    assert "前提提示" in block


def test_render_block_handles_none() -> None:
    assert render_concept_candidate_block(None) == ""


def test_render_block_supports_english() -> None:
    result = generate_concept_leap(seed=5, sample_size=20, top_k=1)
    block = render_concept_candidate_block(result.best(), language="en")

    assert "Concept Leap Candidate" in block


def test_candidates_sorted_by_combined_score() -> None:
    result = generate_concept_leap(seed=9, sample_size=80, top_k=10)
    scores = [c.combined_score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)


def test_top_candidate_combined_score_is_high() -> None:
    # With a wide search space, the top mashup should score reasonably well.
    result = generate_concept_leap(seed=13, sample_size=100, top_k=1)
    best = result.best()
    assert best is not None
    assert best.combined_score >= 0.4
