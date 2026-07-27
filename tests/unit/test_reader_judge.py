from __future__ import annotations

import pytest

from bestseller.services.reader_judge import (
    _parse_judge_json,
    aggregate_prose_quality,
)

pytestmark = pytest.mark.unit


def test_aggregate_weighted() -> None:
    dims = {
        "opening_pull": 0.8,
        "payoff_density": 0.6,
        "emotional_impact": 0.7,
        "anti_abandon": 0.5,
        "ai_taste": 0.4,
        "human_voice": 0.9,
    }
    score = aggregate_prose_quality(dims)
    assert 0.0 <= score <= 1.0
    # Six-axis rubric (payoff_density still the heaviest single weight).
    assert score == pytest.approx(
        (
            0.8 * 0.18
            + 0.6 * 0.22
            + 0.7 * 0.18
            + 0.5 * 0.14
            + 0.4 * 0.14
            + 0.9 * 0.14
        ),
        abs=1e-6,
    )


def test_aggregate_empty_returns_neutral() -> None:
    assert aggregate_prose_quality({}) == pytest.approx(0.7)


def test_parse_judge_json_extracts_embedded_object() -> None:
    raw = '说明\n{"opening_pull":0.8,"payoff_density":0.6,"comment":"ok"}\n尾'
    data = _parse_judge_json(raw)
    assert data is not None
    assert data["opening_pull"] == 0.8


def test_parse_judge_json_handles_garbage() -> None:
    assert _parse_judge_json("not json") is None
    assert _parse_judge_json("") is None
