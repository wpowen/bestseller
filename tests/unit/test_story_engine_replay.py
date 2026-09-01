from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bestseller.domain.story_engine import (
    StoryEngineDefinition,
    replay_receipts,
    validate_engine_definition,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/story_engine/valid_ten_chapters.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_valid_ten_chapter_receipts_replay_to_final_state() -> None:
    payload = _fixture()
    result = replay_receipts(StoryEngineDefinition.from_mapping(payload), payload["chapters"])
    assert result.applied_count == 10
    assert result.state.values["exposure"].value == 5


def test_before_mismatch_leaves_replay_state_unchanged() -> None:
    payload = _fixture()
    broken = copy.deepcopy(payload["chapters"][:1])
    broken[0]["transitions"][0]["before"] = 99
    with pytest.raises(ValueError, match="before mismatch"):
        replay_receipts(StoryEngineDefinition.from_mapping(payload), broken)


def test_duplicate_receipt_is_idempotent() -> None:
    payload = _fixture()
    receipts = payload["chapters"][:1]
    result = replay_receipts(StoryEngineDefinition.from_mapping(payload), receipts + receipts)
    assert result.applied_count == 1
    assert result.duplicate_count == 1


def test_replay_folds_future_obligations_without_duplicate_side_effects() -> None:
    payload = _fixture()
    receipts = payload["chapters"][:2]
    result = replay_receipts(
        StoryEngineDefinition.from_mapping(payload),
        [*receipts, receipts[1]],
    )
    assert result.outstanding_obligations == ("查明名册来源", "取回信物")
    assert result.duplicate_count == 1


def test_conflicting_duplicate_receipt_is_rejected() -> None:
    payload = _fixture()
    first = payload["chapters"][0]
    conflict = copy.deepcopy(first)
    conflict["opponent_counteraction"] = "同一回执身份却出现另一种反制"
    with pytest.raises(ValueError, match="conflicting duplicate receipt"):
        replay_receipts(StoryEngineDefinition.from_mapping(payload), [first, conflict])


def test_failed_replay_does_not_mutate_engine_initial_state() -> None:
    payload = _fixture()
    engine = StoryEngineDefinition.from_mapping(payload)
    broken = copy.deepcopy(payload["chapters"][:1])
    broken[0]["transitions"][0]["before"] = 99
    with pytest.raises(ValueError, match="before mismatch"):
        replay_receipts(engine, broken)
    assert engine.initial_state.values["exposure"].value == 0


def test_engine_requires_opponent_counteraction_or_future_obligation() -> None:
    payload = _fixture()
    payload["chapters"][0].pop("opponent_counteraction")
    payload["chapters"][0].pop("future_obligations")
    with pytest.raises(ValueError, match=r"counteraction|obligation"):
        validate_engine_definition(StoryEngineDefinition.from_mapping(payload))
