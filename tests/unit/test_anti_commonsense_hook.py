from __future__ import annotations

from bestseller.services.anti_commonsense_hook import (
    build_hook_duplicate_risk_fn,
    build_hook_spec_from_mechanism,
    generate_hook_candidates,
)
from bestseller.services.anti_commonsense_mechanisms import list_mechanisms


def test_mechanism_catalog_contains_required_eight_keys() -> None:
    mechanisms = list_mechanisms()
    keys = {item.key for item in mechanisms}

    assert len(mechanisms) == 8
    assert {
        "death_grows",
        "forced_loss",
        "emotion_value",
        "hide_anti_trope",
        "misunderstanding",
        "fourth_disaster",
        "rule_horror",
        "profession_reversal",
    } == keys


def test_generate_hook_candidates_is_deterministic_and_threshold_aware() -> None:
    first = generate_hook_candidates(genre="都市", count=3, seed=7)
    second = generate_hook_candidates(genre="都市", count=3, seed=7)

    assert [item.spec.one_liner for item in first] == [item.spec.one_liner for item in second]
    assert first
    assert first[0].score.h_norm >= 30
    assert first[0].spec.constraints
    assert first[0].spec.anti_cheat
    assert first[0].spec.costs


def test_all_mechanism_one_liners_avoid_broken_must_prefixes() -> None:
    forbidden = ("必须必须", "必须越", "必须最")

    for mechanism in list_mechanisms():
        spec = build_hook_spec_from_mechanism(mechanism, genre="都市")
        assert not any(token in spec.one_liner for token in forbidden), spec.one_liner
        assert "偏偏" in spec.one_liner or "却只能" in spec.one_liner


def test_threshold_selection_prefers_passing_hook_when_available() -> None:
    candidates = generate_hook_candidates(genre="悬疑", count=6, seed=11, min_h_norm=30)
    game_candidates = generate_hook_candidates(genre="游戏", count=3, seed=11, min_h_norm=30)

    assert candidates
    assert candidates[0].score.h_norm >= 30
    assert game_candidates
    assert game_candidates[0].score.h_norm >= 30


def test_duplicate_risk_fn_marks_near_duplicate_and_affects_payload() -> None:
    baseline = generate_hook_candidates(genre="都市", count=6, seed=7, min_h_norm=30)
    duplicate_risk_fn = build_hook_duplicate_risk_fn([baseline[0].spec.one_liner])

    reranked = generate_hook_candidates(
        genre="都市",
        count=6,
        seed=7,
        min_h_norm=30,
        duplicate_risk_fn=duplicate_risk_fn,
        rank_weights={"duplicate_risk": 0.8},
    )

    assert any(item.duplicate_risk > 0 for item in reranked)
    assert reranked[0].duplicate_risk < 1
