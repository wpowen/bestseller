from __future__ import annotations

import pytest

from bestseller.domain.contradiction import (
    CharacterKnowledgeState,
    CharacterStagnationWarning,
)

pytestmark = pytest.mark.unit


# ── CharacterKnowledgeState model ───────────────────────────────


def test_character_knowledge_state_model() -> None:
    state = CharacterKnowledgeState(
        character_name="Lin Mei",
        as_of_chapter=12,
        knows=["the king is dead", "the treasury is empty"],
        falsely_believes=["the prince is loyal"],
        unaware_of=["the assassin's identity"],
    )
    assert state.character_name == "Lin Mei"
    assert state.as_of_chapter == 12
    assert len(state.knows) == 2
    assert "the king is dead" in state.knows
    assert state.falsely_believes == ["the prince is loyal"]
    assert state.unaware_of == ["the assassin's identity"]


def test_character_knowledge_state_defaults() -> None:
    state = CharacterKnowledgeState(
        character_name="Zhang Wei",
        as_of_chapter=1,
    )
    assert state.knows == []
    assert state.falsely_believes == []
    assert state.unaware_of == []


# ── CharacterStagnationWarning model ────────────────────────────


def test_character_stagnation_warning_model() -> None:
    warning = CharacterStagnationWarning(
        character_name="Chen Yu",
        last_update_chapter=5,
        chapters_since_update=8,
        stagnant_fields=["emotional_state", "arc_state"],
    )
    assert warning.character_name == "Chen Yu"
    assert warning.last_update_chapter == 5
    assert warning.chapters_since_update == 8
    assert len(warning.stagnant_fields) == 2
    assert "emotional_state" in warning.stagnant_fields


def test_character_stagnation_warning_defaults() -> None:
    warning = CharacterStagnationWarning(
        character_name="Li Hua",
        last_update_chapter=10,
        chapters_since_update=3,
    )
    assert warning.stagnant_fields == []


# ── Knowledge state accumulation logic ──────────────────────────


def test_knowledge_state_accumulation_logic() -> None:
    """Given two knowledge states for the same character at different chapters,
    verify that merging produces the union of `knows` and updates
    `falsely_believes` correctly.
    """
    state_ch5 = CharacterKnowledgeState(
        character_name="Lin Mei",
        as_of_chapter=5,
        knows=["the king is dead"],
        falsely_believes=["the prince is loyal", "the treasury is full"],
        unaware_of=["the assassin's identity"],
    )

    state_ch10 = CharacterKnowledgeState(
        character_name="Lin Mei",
        as_of_chapter=10,
        knows=["the treasury is empty", "the prince betrayed the king"],
        falsely_believes=[],
        unaware_of=[],
    )

    # Merge: union of knows, update falsely_believes by removing
    # items that conflict with new knowledge.
    merged_knows = list(dict.fromkeys(state_ch5.knows + state_ch10.knows))

    # Remove false beliefs that are contradicted by new knowledge keywords.
    new_knowledge_text = " ".join(state_ch10.knows).lower()
    updated_falsely_believes = [
        belief
        for belief in state_ch5.falsely_believes
        if not any(
            word in new_knowledge_text
            for word in belief.lower().split()
            if len(word) > 3
        )
    ]

    merged = CharacterKnowledgeState(
        character_name=state_ch5.character_name,
        as_of_chapter=state_ch10.as_of_chapter,
        knows=merged_knows,
        falsely_believes=updated_falsely_believes,
        unaware_of=state_ch10.unaware_of,
    )

    assert merged.as_of_chapter == 10
    assert "the king is dead" in merged.knows
    assert "the treasury is empty" in merged.knows
    assert "the prince betrayed the king" in merged.knows
    assert len(merged.knows) == 3

    # "the prince is loyal" should be removed because "prince" appears
    # in new knowledge; "the treasury is full" should be removed because
    # "treasury" appears in new knowledge.
    assert "the prince is loyal" not in merged.falsely_believes
    assert "the treasury is full" not in merged.falsely_believes
