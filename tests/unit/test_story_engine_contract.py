from __future__ import annotations

import json
from pathlib import Path

import pytest

from bestseller.domain.story_engine import (
    ChoiceConsequenceRow,
    ChoiceOption,
    StoryEngineDefinition,
    StoryEngineMaturity,
    assess_maturity,
    canonical_json_hash,
    count_choice_fingerprints,
    validate_engine_definition,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/story_engine/valid_ten_chapters.json"


def test_canonical_hash_is_stable_for_mapping_order() -> None:
    left = {"engine_id": "x", "state": {"b": 2, "a": 1}}
    right = {"state": {"a": 1, "b": 2}, "engine_id": "x"}
    assert canonical_json_hash(left) == canonical_json_hash(right)


def test_engine_requires_at_least_two_reachable_real_options() -> None:
    engine = StoryEngineDefinition(
        engine_id="x",
        initial_state={},
        choices=[
            ChoiceOption("stay", "留在原地", reachable_state_hash="same"),
            ChoiceOption("stay-copy", "换个说法继续原地", reachable_state_hash="same"),
        ],
    )
    with pytest.raises(ValueError, match="reachable-state hash"):
        validate_engine_definition(engine)


def test_engine_rejects_empty_transition_evidence() -> None:
    row = ChoiceConsequenceRow(
        choice_id="leave",
        transitions=[
            {
                "key": "exposure",
                "category": "exposure",
                "before": 0,
                "operator": "set",
                "after": 1,
                "evidence": "",
            }
        ],
    )
    with pytest.raises(ValueError, match="evidence"):
        row.validate()


def test_choice_fingerprint_repetition_is_counted() -> None:
    counts = count_choice_fingerprints(["hide|door", "trade|token", "hide|door"])
    assert counts == {"hide|door": 2, "trade|token": 1}


def test_maturity_without_reader_evidence_is_insufficient_data() -> None:
    payload = json.loads(FIXTURE.read_text())
    assert assess_maturity(payload) is StoryEngineMaturity.INSUFFICIENT_DATA
