from __future__ import annotations

import pytest

from bestseller.services.genre_review_profiles import (
    _GENRE_TO_CATEGORY_MAP,
    GenreReviewProfile,
    resolve_genre_review_profile,
)


pytestmark = pytest.mark.unit


# ── helpers ─────────────────────────────────────────────────────────────


def _all_scene_weight_values(profile: GenreReviewProfile) -> list[float]:
    """Return every numeric weight from the profile's scene_weights."""
    sw = profile.scene_weights
    return [
        sw.goal,
        sw.conflict,
        sw.conflict_clarity,
        sw.emotion,
        sw.emotional_movement,
        sw.dialogue,
        sw.style,
        sw.voice_consistency,
        sw.hook,
        sw.hook_strength,
        sw.payoff_density,
        sw.contract_alignment,
        sw.pacing_alignment,
        sw.subplot_presence,
        sw.scene_sequel_alignment,
    ]


def _simple_average(scores: list[float]) -> float:
    return sum(scores) / len(scores) if scores else 0.0


def _weighted_average(scores: list[float], weights: list[float]) -> float:
    total_w = sum(weights)
    if total_w == 0:
        return 0.0
    return sum(s * w for s, w in zip(scores, weights)) / total_w


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════


def test_default_profile_all_weights_one() -> None:
    """Default profile has all scene weights equal to 1.0."""
    profile = resolve_genre_review_profile("", None)
    weights = _all_scene_weight_values(profile)
    assert all(
        w == 1.0 for w in weights
    ), f"Expected all weights = 1.0 for default profile, got {weights}"


def test_all_genre_preset_keys_mapped() -> None:
    """Every key in _GENRE_TO_CATEGORY_MAP resolves to a valid profile."""
    for preset_key in _GENRE_TO_CATEGORY_MAP:
        profile = resolve_genre_review_profile("", None, genre_preset_key=preset_key)
        assert isinstance(profile, GenreReviewProfile), (
            f"Preset key '{preset_key}' did not resolve to a GenreReviewProfile"
        )
        assert profile.category_key, (
            f"Preset key '{preset_key}' resolved to a profile with empty category_key"
        )


def test_action_progression_weights() -> None:
    """Action-progression profile emphasises conflict and de-emphasises emotion."""
    profile = resolve_genre_review_profile("action-progression", None)
    sw = profile.scene_weights
    assert sw.conflict > 1.0, f"Expected conflict > 1.0, got {sw.conflict}"
    assert sw.emotion < 1.0, f"Expected emotion < 1.0, got {sw.emotion}"


def test_relationship_driven_weights() -> None:
    """Relationship-driven profile emphasises emotion and de-emphasises conflict."""
    profile = resolve_genre_review_profile("女性成长", "情感拉扯")
    sw = profile.scene_weights
    assert sw.emotion > 1.0, f"Expected emotion > 1.0, got {sw.emotion}"
    assert sw.conflict < 1.0, f"Expected conflict < 1.0, got {sw.conflict}"


def test_resolve_by_genre_string_fuzzy() -> None:
    """Chinese genre string '仙侠升级' resolves to action-progression."""
    profile = resolve_genre_review_profile("仙侠升级", None)
    assert profile.category_key == "action-progression", (
        f"Expected 'action-progression', got '{profile.category_key}'"
    )


def test_resolve_by_genre_string_romance() -> None:
    """Chinese genre string '女性成长' resolves to relationship-driven."""
    profile = resolve_genre_review_profile("女性成长", None)
    assert profile.category_key == "relationship-driven", (
        f"Expected 'relationship-driven', got '{profile.category_key}'"
    )


def test_resolve_unknown_genre_returns_default() -> None:
    """An unrecognised genre string falls back to the 'default' profile."""
    profile = resolve_genre_review_profile("completely-unknown-genre-xyz", None)
    assert profile.category_key == "default", (
        f"Expected 'default', got '{profile.category_key}'"
    )


def test_signal_keywords_not_empty_for_core_categories() -> None:
    """Core category profiles must have non-empty conflict_terms_zh."""
    genre_to_category = [
        ("末日科幻", "action-progression"),
        ("女性成长", "relationship-driven"),
        ("悬疑推理", "suspense-mystery"),
    ]
    for genre_str, expected_category in genre_to_category:
        profile = resolve_genre_review_profile(genre_str, None)
        assert profile.category_key == expected_category
        terms = profile.signal_keywords.conflict_terms_zh
        assert isinstance(terms, list) and len(terms) > 0, (
            f"'{expected_category}' should have non-empty conflict_terms_zh, got {terms}"
        )


def test_planner_prompts_not_empty_for_core_categories() -> None:
    """Action-progression should have a non-empty book_spec_instruction_zh."""
    profile = resolve_genre_review_profile("action-progression", None)
    instruction = profile.planner_prompts.book_spec_instruction_zh
    assert isinstance(instruction, str) and len(instruction) > 0, (
        f"Expected non-empty book_spec_instruction_zh, got {instruction!r}"
    )


def test_judge_prompts_not_empty_for_core_categories() -> None:
    """Action-progression should have a non-empty scene_review_instruction_zh."""
    profile = resolve_genre_review_profile("action-progression", None)
    instruction = profile.judge_prompts.scene_review_instruction_zh
    assert isinstance(instruction, str) and len(instruction) > 0, (
        f"Expected non-empty scene_review_instruction_zh, got {instruction!r}"
    )


def test_finding_messages_differ_by_category() -> None:
    """Finding messages should differ between action-progression and relationship-driven."""
    action_profile = resolve_genre_review_profile("action-progression", None)
    relationship_profile = resolve_genre_review_profile("relationship-driven", None)

    action_msg = action_profile.finding_messages.conflict_low_zh
    relationship_msg = relationship_profile.finding_messages.conflict_low_zh

    assert action_msg != relationship_msg, (
        f"Expected different conflict_low_zh messages, but both are: {action_msg!r}"
    )


def test_weighted_score_differs_from_simple_average() -> None:
    """Weighted scoring for action-progression should differ from a simple average.

    We provide uniform dimension scores except conflict and emotion, then
    compare a weighted aggregate using the action-progression weights against a
    plain average.  Because action-progression boosts conflict and reduces
    emotion, the two aggregates should differ.
    """
    default_profile = resolve_genre_review_profile("", None)
    action_profile = resolve_genre_review_profile("action-progression", None)

    # Fabricate per-dimension scores: uniform 0.7, except conflict=0.9, emotion=0.5
    dimension_scores = [
        0.7,  # goal
        0.9,  # conflict  (high)
        0.7,  # conflict_clarity
        0.5,  # emotion   (low)
        0.7,  # emotional_movement
        0.7,  # dialogue
        0.7,  # style
        0.7,  # voice_consistency
        0.7,  # hook
        0.7,  # hook_strength
        0.7,  # payoff_density
        0.7,  # contract_alignment
    ]

    default_weights = _all_scene_weight_values(default_profile)
    action_weights = _all_scene_weight_values(action_profile)

    default_overall = _weighted_average(dimension_scores, default_weights)
    action_overall = _weighted_average(dimension_scores, action_weights)

    # Because action-progression amplifies conflict (0.9) and dampens emotion
    # (0.5), the weighted result must differ from the default equal-weight result.
    assert default_overall != pytest.approx(action_overall, abs=1e-6), (
        f"Expected different weighted scores, got default={default_overall:.4f} "
        f"vs action={action_overall:.4f}"
    )
