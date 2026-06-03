from __future__ import annotations

import pytest

from bestseller.domain.reader_persona import (
    PersonaScore,
    PersonaSimulationResult,
)
from bestseller.services.persona_quality_gate import (
    PERSONA_ABANDON_RATE_HIGH,
    PERSONA_PAYOFF_DENSITY_LOW,
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


def test_can_keep_payoff_density_advisory_for_short_validation_opening() -> None:
    result = PersonaSimulationResult(
        chapter_position=1,
        per_persona=[
            PersonaScore(
                persona_key="commute",
                persona_label="通勤爽文党",
                overall_score=0.8,
                abandon_probability=0.1,
                channel_scores={"payoff_density": 0.1},
            )
        ],
        weighted_score=0.8,
        abandon_rate=0.1,
    )

    report = evaluate_persona_quality(
        result,
        min_weighted_score=0.62,
        max_abandon_rate=0.35,
        min_payoff_density=0.22,
        block_on_payoff=False,
    )

    assert PERSONA_PAYOFF_DENSITY_LOW not in report.auto_repair_codes
    assert report.passed
