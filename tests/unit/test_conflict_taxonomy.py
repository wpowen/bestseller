"""Unit tests for the conflict taxonomy + its diversity block."""

from __future__ import annotations

import pytest

from bestseller.services.conflict_taxonomy import (
    ConflictTuple,
    EMERGING_POOL,
    GENRE_POOLS,
    candidate_pool_for_genre,
    conflict_similarity,
    evaluate_switching_rules,
    should_inject_emerging,
)
from bestseller.services.deduplication import build_conflict_diversity_block

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# ConflictTuple
# ---------------------------------------------------------------------------

def test_conflict_tuple_from_dict_round_trip() -> None:
    raw = {
        "object": "person",
        "layer": "personal_relation",
        "nature": "information_asymmetry",
        "resolvability": "resolvable",
        "conflict_id": "林鸢-姜澄-信任裂痕",
    }
    t = ConflictTuple.from_dict(raw)
    assert t is not None
    assert t.as_dict() == raw


def test_conflict_tuple_from_dict_rejects_missing() -> None:
    assert ConflictTuple.from_dict({"object": "person"}) is None


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def test_conflict_similarity_identical_is_one() -> None:
    t = ConflictTuple("person", "personal_relation", "information_asymmetry", "resolvable", "id-1")
    assert conflict_similarity(t, t) == pytest.approx(1.0)


def test_conflict_similarity_completely_different_is_zero() -> None:
    a = ConflictTuple("person", "personal_relation", "antagonistic", "resolvable")
    b = ConflictTuple("nature", "cosmic", "temporal_irreversible", "tragic_inevitable")
    assert conflict_similarity(a, b) == 0.0


def test_conflict_similarity_weighted_partial() -> None:
    # layer match → 0.3
    a = ConflictTuple("person", "personal_relation", "antagonistic", "resolvable")
    b = ConflictTuple("society", "personal_relation", "moral_dilemma", "transformative")
    assert conflict_similarity(a, b) == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Switching rules
# ---------------------------------------------------------------------------

def test_evaluate_switching_rules_empty_history() -> None:
    rules = evaluate_switching_rules([])
    assert not rules["must_switch_axis_ab"]
    assert rules["forbid_object"] == []


def test_evaluate_switching_rules_forbids_prev_axes() -> None:
    recent = [
        ConflictTuple("person", "personal_relation", "antagonistic", "resolvable"),
    ]
    rules = evaluate_switching_rules(recent)
    assert "person" in rules["forbid_object"]
    assert "personal_relation" in rules["forbid_layer"]


def test_evaluate_switching_rules_triggers_internal_requirement() -> None:
    # 5 consecutive non-inner scenes → needs_internal True
    recent = [
        ConflictTuple("person", "personal_relation", "antagonistic", "resolvable")
        for _ in range(5)
    ]
    rules = evaluate_switching_rules(recent)
    assert rules["needs_internal"] is True


def test_evaluate_switching_rules_ignores_internal_when_present() -> None:
    recent = [
        ConflictTuple("person", "personal_relation", "antagonistic", "resolvable"),
        ConflictTuple("self", "inner_desire", "value_clash", "transformative"),
        ConflictTuple("group", "communal", "resource_scarcity", "dynamic_equilibrium"),
    ]
    rules = evaluate_switching_rules(recent)
    assert rules["needs_internal"] is False


def test_evaluate_switching_rules_forbids_overused_conflict_id() -> None:
    recent = [
        ConflictTuple("person", "personal_relation", "antagonistic", "resolvable", "cid-A"),
        ConflictTuple("self", "inner_desire", "value_clash", "transformative", "cid-A"),
        ConflictTuple("group", "communal", "resource_scarcity", "dynamic_equilibrium", "cid-A"),
        ConflictTuple("society", "institutional", "moral_dilemma", "resolvable", "cid-B"),
    ]
    rules = evaluate_switching_rules(recent)
    assert "cid-A" in rules["forbid_conflict_id"]
    assert "cid-B" not in rules["forbid_conflict_id"]


# ---------------------------------------------------------------------------
# Emerging cadence
# ---------------------------------------------------------------------------

def test_should_inject_emerging_first_time_at_ch10() -> None:
    assert should_inject_emerging(10, None) is True
    assert should_inject_emerging(5, None) is False


def test_should_inject_emerging_respects_cadence() -> None:
    assert should_inject_emerging(60, 30) is True   # 30-chapter gap → inject
    assert should_inject_emerging(40, 30) is False  # only 10 chapters → wait


# ---------------------------------------------------------------------------
# Genre pools
# ---------------------------------------------------------------------------

def test_genre_pool_female_lead_no_cp_available() -> None:
    pool = candidate_pool_for_genre("female_lead_no_cp")
    assert "self_identity" in pool
    assert "female_lineage" in pool


def test_genre_pool_unknown_returns_empty() -> None:
    assert candidate_pool_for_genre("nonexistent-genre") == []
    assert candidate_pool_for_genre(None) == []


def test_all_expected_genre_pools_present() -> None:
    for key in ("female_lead_no_cp", "cultivation_xianxia", "crime_thriller",
                "romance", "sci_fi", "epic_fantasy"):
        assert key in GENRE_POOLS, f"Missing genre pool: {key}"


# ---------------------------------------------------------------------------
# Prompt block renderer
# ---------------------------------------------------------------------------

def test_build_conflict_diversity_block_empty_history_zh() -> None:
    block = build_conflict_diversity_block([], language="zh-CN")
    assert "Stage A" in block
    assert "近场尚无" in block


def test_build_conflict_diversity_block_includes_forbidden_axes() -> None:
    recent = [{
        "object": "person",
        "layer": "personal_relation",
        "nature": "information_asymmetry",
        "resolvability": "resolvable",
        "conflict_id": "林鸢-姜澄",
    }]
    block = build_conflict_diversity_block(
        recent, genre_pool_key="female_lead_no_cp", language="zh-CN",
    )
    assert "禁用" in block
    assert "人际对抗" in block
    assert "self_identity" in block  # genre pool leaked into prompt


def test_build_conflict_diversity_block_emerging_injection() -> None:
    block = build_conflict_diversity_block(
        [], inject_emerging=True, language="zh-CN",
    )
    assert "Emerging" in block
    # At least one emerging entry should be listed
    assert any(item in block for item in EMERGING_POOL)


def test_build_conflict_diversity_block_en_language() -> None:
    block = build_conflict_diversity_block(
        [{"object": "person", "layer": "personal_relation",
          "nature": "antagonistic", "resolvability": "resolvable"}],
        language="en",
    )
    assert "FORBID" in block or "conflict" in block.lower()


# ---------------------------------------------------------------------------
# Invariant: the set of axes is small and stable
# ---------------------------------------------------------------------------

def test_axis_counts_match_plan() -> None:
    from bestseller.services.conflict_taxonomy import (
        LAYER_TYPES,
        NATURE_TYPES,
        OBJECT_TYPES,
        RESOLVABILITY_TYPES,
    )
    assert len(OBJECT_TYPES) == 7
    assert len(LAYER_TYPES) == 6
    assert len(NATURE_TYPES) == 7
    assert len(RESOLVABILITY_TYPES) == 4
