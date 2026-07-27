from __future__ import annotations

import pytest

from bestseller.domain.story_state import (
    MonotonicPolicy,
    StateCategory,
    StoryState,
    StoryStateTransition,
    apply_story_state_transitions,
    validate_story_state_transition,
)


def test_numeric_regression_is_rejected_by_increasing_policy() -> None:
    transition = StoryStateTransition(
        key="trust",
        category=StateCategory.RELATIONSHIP,
        before=62,
        operator="set",
        after=55,
        evidence="betrayal revealed",
        monotonic=MonotonicPolicy.INCREASING,
    )

    with pytest.raises(ValueError, match="monotonic"):
        validate_story_state_transition(transition)


def test_transaction_does_not_partially_apply_invalid_batch() -> None:
    state = StoryState.from_mapping({"power": {"category": "capability", "value": 1}})
    transitions = [
        StoryStateTransition("power", "capability", 1, "set", 2, "training"),
        StoryStateTransition(
            "trust", "relationship", 62, "set", 55, "betrayal", "increasing"
        ),
    ]

    with pytest.raises(ValueError):
        apply_story_state_transitions(state, transitions)
    assert state.values["power"].value == 1


def test_transition_application_is_deterministic_and_supports_legacy_values() -> None:
    state = StoryState.from_mapping({"ammo": 3})
    apply_story_state_transitions(
        state,
        [StoryStateTransition("ammo", "resource", 3, "add", 5, "spent")],
    )
    assert state.values["ammo"].value == 5
