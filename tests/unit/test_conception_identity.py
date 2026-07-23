from __future__ import annotations

import pytest

from bestseller.domain.enums import IntentDiffSeverity
from bestseller.services.creation_intent_contract import (
    build_creation_intent_contract,
    diff_creation_intents,
)
from bestseller.services.genre_intent_contract import contract_from_selection

pytestmark = pytest.mark.unit


def test_same_creation_identity_has_no_diff() -> None:
    genre = contract_from_selection({"channel": "male", "genre": "xianxia"})
    contract = build_creation_intent_contract(genre, chapter_count=50)

    assert diff_creation_intents(contract, contract).items == ()


def test_revision_cannot_silently_replace_hook_or_genre_identity() -> None:
    old = build_creation_intent_contract(
        contract_from_selection({"channel": "male", "genre": "xianxia"}),
        hook_spec={"mechanism_key": "forge-debt"},
    )
    new = old.model_copy(
        update={
            "hook_spec": {"mechanism_key": "bloodline-awakening"},
            "genre_intent": contract_from_selection({"channel": "male", "genre": "xuanhuan"}),
        }
    )

    diff = diff_creation_intents(old, new)
    assert diff.has_hard_conflicts
    assert any(
        item.path == "hook_spec.mechanism_key" and item.severity is IntentDiffSeverity.HARD
        for item in diff.items
    )
    assert any(
        item.path == "genre_intent.genre_key" and item.severity is IntentDiffSeverity.HARD
        for item in diff.items
    )


def test_materialized_protagonist_identity_is_a_hard_diff() -> None:
    diff = diff_creation_intents(
        {"book_spec": {"protagonist": {"name": "庄溯", "age": 19}}},
        {"book_spec": {"protagonist": {"name": "陆沉舟", "age": 14}}},
    )
    assert {item.path for item in diff.hard_conflicts} == {
        "book_spec.protagonist.age",
        "book_spec.protagonist.name",
    }
