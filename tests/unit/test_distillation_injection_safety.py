from __future__ import annotations

import pytest

from bestseller.services.distilled_strategy_compiler import (
    assess_distilled_strategy_injection,
)
from bestseller.services.quality_levers.integrator import (
    WriterLeverContext,
    build_writer_quality_levers_block,
)

pytestmark = pytest.mark.unit


def _card(**overrides: object) -> dict[str, object]:
    card: dict[str, object] = {
        "aggregate_key": "suspense-mystery",
        "genre_profile_key": "suspense-mystery",
        "source_count": 8,
        "maturity_score": 0.80,
        "maturity_status": "review",
        "provenance_status": "anonymous_aggregate",
        "privacy_status": "redacted",
        "selected_mechanisms": [
            {
                "mechanism_id": "evidence-pressure",
                "source_confidence": 0.86,
                "design_role": "series_engine",
                "adaptation_instruction": "变换为项目专属证据链。",
                "required_project_specific_binding": "绑定到本书调查代价。",
                "failure_mode": "未绑定项目因果。",
            }
        ],
    }
    card.update(overrides)
    return card


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"source_count": 0}, "source_count_zero"),
        ({"maturity_score": 0.69}, "maturity_gate_failed"),
        ({"provenance_status": "missing"}, "provenance_gate_failed"),
        ({"privacy_status": "missing"}, "privacy_gate_failed"),
        ({"genre_profile_key": "distillation-generic"}, "genre_profile_mismatch"),
    ],
)
def test_unsafe_distillation_cards_cannot_enter_writer_prompt(
    overrides: dict[str, object],
    expected_reason: str,
) -> None:
    card = _card(**overrides)

    decision = assess_distilled_strategy_injection(card)
    writer_block = build_writer_quality_levers_block(
        WriterLeverContext(chapter_number=4, distilled_strategy_card=card)
    )

    assert decision.allowed is False
    assert expected_reason in decision.reason_codes
    assert "evidence-pressure" not in writer_block


def test_approved_anonymous_aggregate_injects_selected_effects_only() -> None:
    card = _card()

    writer_block = build_writer_quality_levers_block(
        WriterLeverContext(chapter_number=4, distilled_strategy_card=card)
    )

    assert "evidence-pressure" in writer_block
    assert "策略卡" in writer_block
