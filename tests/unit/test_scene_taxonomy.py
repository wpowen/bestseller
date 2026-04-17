"""Unit tests for the scene taxonomy + purpose/env diversity blocks."""

from __future__ import annotations

import pytest

from bestseller.services.deduplication import (
    build_env_diversity_block,
    build_scene_purpose_diversity_block,
)
from bestseller.services.scene_taxonomy import (
    ENV_DIMENSIONS,
    EnvVector,
    PURPOSE_FAMILIES,
    all_purposes,
    evaluate_env_rules,
    evaluate_purpose_rules,
    family_of,
    location_visit_count,
    purpose_label,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Purpose taxonomy
# ---------------------------------------------------------------------------

def test_twenty_four_total_purposes() -> None:
    assert sum(len(b) for b in PURPOSE_FAMILIES.values()) == 24


def test_family_of_known_purposes() -> None:
    assert family_of("inciting") == "A_structural"
    assert family_of("heist") == "B_action"
    assert family_of("bonding") == "C_relation"
    assert family_of("reflection") == "D_interior"
    assert family_of("not-a-purpose") is None


def test_purpose_label_returns_chinese_gloss() -> None:
    assert "追击" in purpose_label("pursuit")


# ---------------------------------------------------------------------------
# evaluate_purpose_rules
# ---------------------------------------------------------------------------

def test_evaluate_purpose_rules_empty() -> None:
    result = evaluate_purpose_rules([])
    assert result["forbid_purposes"] == []
    assert set(result["underused_families"]) == set(PURPOSE_FAMILIES.keys())


def test_evaluate_purpose_rules_forbids_recent() -> None:
    result = evaluate_purpose_rules(["revelation", "confrontation", "pursuit"])
    assert "revelation" in result["forbid_purposes"]
    assert "confrontation" in result["forbid_purposes"]


def test_evaluate_purpose_rules_marks_underused_families() -> None:
    # Three recent purposes all in family B and C → A and D underused.
    result = evaluate_purpose_rules(["pursuit", "battle", "bonding"])
    assert "A_structural" in result["underused_families"]
    assert "D_interior" in result["underused_families"]
    assert "B_action" not in result["underused_families"]


def test_evaluate_purpose_rules_prioritises_underused() -> None:
    result = evaluate_purpose_rules(["pursuit", "battle"])
    # A structural purpose should appear before a B-action purpose in pool.
    pool = result["candidate_pool"]
    structural = next(p for p in pool if family_of(p) == "A_structural")
    action_idx = next(
        (i for i, p in enumerate(pool) if family_of(p) == "B_action"), 10**9
    )
    structural_idx = pool.index(structural)
    assert structural_idx < action_idx


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def test_env_dimensions_length() -> None:
    assert len(ENV_DIMENSIONS) == 7


def test_env_vector_from_dict_round_trip() -> None:
    data = {
        "physical_space": "underground",
        "time_of_day": "deep_night",
        "weather_light": "artificial_light",
        "dominant_sense": "sound",
        "social_density": "dyad",
        "tempo_scale": "realtime",
        "vertical_enclosure": "deep_underground_sealed",
    }
    v = EnvVector.from_dict(data)
    assert v.as_dict() == data


def test_env_vector_differs_on_counts_changes() -> None:
    a = EnvVector(physical_space="underground", time_of_day="night")
    b = EnvVector(physical_space="rooftop_exposed", time_of_day="noon")
    assert a.differs_on(b) == {"physical_space", "time_of_day"}


def test_evaluate_env_rules_empty() -> None:
    result = evaluate_env_rules([])
    assert result["prev_env"] is None
    assert result["forbid_exact_matches"] == []


def test_evaluate_env_rules_returns_prev_labels() -> None:
    envs = [EnvVector(physical_space="underground", time_of_day="deep_night")]
    result = evaluate_env_rules(envs)
    assert result["prev_env"]["physical_space"] == "underground"
    assert result["prev_env_labels"]["physical_space"] == "地下"


# ---------------------------------------------------------------------------
# Block renderers
# ---------------------------------------------------------------------------

def test_build_scene_purpose_diversity_block_includes_forbid_and_pool() -> None:
    block = build_scene_purpose_diversity_block(
        ["revelation", "confrontation", "pursuit"], language="zh-CN",
    )
    assert "场景目的" in block
    assert "revelation" in block


def test_build_env_diversity_block_renders_prev_env_zh() -> None:
    envs = [{
        "physical_space": "underground",
        "time_of_day": "deep_night",
        "weather_light": "artificial_light",
        "dominant_sense": "sound",
        "social_density": "dyad",
        "tempo_scale": "realtime",
        "vertical_enclosure": "deep_underground_sealed",
    }]
    block = build_env_diversity_block(envs, language="zh-CN")
    assert "地下" in block
    assert "深夜" in block
    assert "3" in block  # min_diff_vs_prev = 3


def test_build_env_diversity_block_empty_history() -> None:
    block = build_env_diversity_block([], language="zh-CN")
    assert "尚无" in block


# ---------------------------------------------------------------------------
# Location ledger helper
# ---------------------------------------------------------------------------

def test_location_visit_count() -> None:
    assert location_visit_count("下水道", ["下水道", "下水道", "塔楼", None]) == 2
    assert location_visit_count(None, ["下水道"]) == 0


def test_all_purposes_iter() -> None:
    names = list(all_purposes())
    assert len(names) == 24
    assert len(set(names)) == 24  # no duplicates
