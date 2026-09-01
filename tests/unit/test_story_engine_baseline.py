from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
from typing import Any

import pytest

from bestseller.domain.story_engine import (
    StoryEngineDefinition,
    evaluate_story_engine_baseline,
    replay_receipts,
)

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures/story_engine"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


def _field(report: Any, name: str) -> Any:
    if isinstance(report, dict):
        return report[name]
    if is_dataclass(report):
        return asdict(report)[name]
    return getattr(report, name)


@pytest.mark.parametrize(
    "name", ["valid_urban_ten_chapters.json", "valid_mystery_ten_chapters.json"]
)
def test_valid_ten_chapter_fixture_replays_and_passes_structure_baseline(name: str) -> None:
    payload = _load(name)
    engine = StoryEngineDefinition.from_mapping(payload)

    replay = replay_receipts(engine, payload["chapters"])
    report = evaluate_story_engine_baseline(payload)

    assert replay.applied_count == 10
    assert _field(report, "chapter_count") == 10
    assert _field(report, "repeated_fingerprint_count") == 0
    assert _field(report, "duplicate_transition_pattern_count") == 0
    assert _field(report, "state_reset_count") == 0
    assert _field(report, "opponent_response_coverage") == pytest.approx(1.0)
    assert _field(report, "obligation_coverage") == pytest.approx(1.0)
    assert _field(report, "transition_evidence_coverage") == pytest.approx(1.0)
    assert _field(report, "blocking_codes") == []
    assert _field(report, "structure_passed") is True


def test_legacy_failure_fixture_exposes_all_structural_blockers() -> None:
    payload = _load("legacy_failure_ten_chapters.json")
    report = evaluate_story_engine_baseline(payload)

    assert _field(report, "chapter_count") == 10
    assert _field(report, "repeated_fingerprint_count") >= 1
    assert _field(report, "duplicate_transition_pattern_count") >= 1
    assert _field(report, "state_reset_count") >= 1
    assert _field(report, "opponent_response_coverage") < 0.5
    assert _field(report, "obligation_coverage") < 0.5
    assert _field(report, "transition_evidence_coverage") == pytest.approx(1.0)
    blocking_codes = set(_field(report, "blocking_codes"))
    assert {"REPEATED_FINGERPRINT", "DUPLICATE_TRANSITION_PATTERN", "STATE_RESET"} <= blocking_codes
    assert _field(report, "structure_passed") is False
