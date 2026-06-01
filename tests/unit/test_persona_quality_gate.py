from __future__ import annotations

import pytest

from bestseller.domain.reader_persona import (
    PersonaScore,
    PersonaSimulationResult,
    PersonaWeights,
)
from bestseller.services.persona_quality_gate import (
    PERSONA_ABANDON_RATE_HIGH,
    evaluate_persona_quality,
)

pytestmark = pytest.mark.unit


def test_blocks_high_abandon_rate() -> None:
    result = PersonaSimulationResult(
        chapter_position=3,
        per_persona=[
            PersonaScore(
                persona_key="laobai",
                persona_label="老白",
                overall_score=0.4,
                abandon_probability=0.6,
                channel_scores={"payoff_density": 0.15},
            )
        ],
        weighted_score=0.5,
        abandon_rate=0.4,
    )
    report = evaluate_persona_quality(result, max_abandon_rate=0.35)
    assert PERSONA_ABANDON_RATE_HIGH in report.auto_repair_codes
