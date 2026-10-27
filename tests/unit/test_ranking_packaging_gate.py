# ruff: noqa: RUF001
from __future__ import annotations

import pytest

from bestseller.services.ranking_packaging_gate import evaluate_ranking_candidate_packaging


def _verdict(total: float, values: dict[str, float]) -> dict:
    return {
        "total": total,
        "dimensions": [{"key": key, "score": score} for key, score in values.items()],
    }


TITLE = _verdict(88, {"hook_power": 4.2, "graspable_subject": 4.0})
BLURB = _verdict(
    86,
    {
        "hook_strength": 4.1,
        "emotion_charge": 3.7,
        "concreteness": 4.0,
        "comprehensibility": 3.8,
        "open_loop_end": 4.0,
        "genre_signal": 4.1,
    },
)
PERSONA = {"llm_used": True, "samples": 3, "clicks": 2, "avg_score": 7.0}


@pytest.mark.unit
def test_strong_package_with_real_persona_is_ranking_candidate():
    report = evaluate_ranking_candidate_packaging(
        title_verdict=TITLE,
        blurb_verdict=BLURB,
        persona_report=PERSONA,
    )
    assert report["passed"] is True
    assert report["status"] == "PASS"
    assert report["blocking_codes"] == []


@pytest.mark.unit
def test_weak_title_and_blurb_fail_independent_hard_dimensions():
    report = evaluate_ranking_candidate_packaging(
        title_verdict=_verdict(85, {"hook_power": 2.0, "graspable_subject": 2.0}),
        blurb_verdict=_verdict(
            80,
            {
                "hook_strength": 2.0,
                "emotion_charge": 2.0,
                "concreteness": 2.0,
                "comprehensibility": 2.0,
                "open_loop_end": 2.0,
                "genre_signal": 2.0,
            },
        ),
        persona_report=PERSONA,
    )
    assert report["status"] == "BLOCKED"
    assert "TITLE_HOOK_POWER_BELOW_FLOOR" in report["blocking_codes"]
    assert "BLURB_HOOK_STRENGTH_BELOW_FLOOR" in report["blocking_codes"]
    assert "BLURB_EMOTION_CHARGE_BELOW_FLOOR" in report["blocking_codes"]


@pytest.mark.unit
def test_blurb_cannot_hide_weak_open_loop_and_genre_signal_behind_total_score():
    report = evaluate_ranking_candidate_packaging(
        title_verdict=TITLE,
        blurb_verdict=_verdict(
            86,
            {
                "hook_strength": 4.5,
                "emotion_charge": 4.5,
                "concreteness": 4.5,
                "comprehensibility": 4.5,
                "open_loop_end": 3.0,
                "genre_signal": 2.5,
            },
        ),
        persona_report=PERSONA,
    )

    assert report["status"] == "BLOCKED"
    assert "BLURB_OPEN_LOOP_END_BELOW_FLOOR" in report["blocking_codes"]
    assert "BLURB_GENRE_SIGNAL_BELOW_FLOOR" in report["blocking_codes"]


@pytest.mark.unit
def test_one_of_three_clicks_fails_even_when_average_score_is_high():
    report = evaluate_ranking_candidate_packaging(
        title_verdict=TITLE,
        blurb_verdict=BLURB,
        persona_report={"llm_used": True, "samples": 3, "clicks": 1, "avg_score": 9.0},
    )
    assert report["status"] == "BLOCKED"
    assert "PERSONA_CLICK_RATE_BELOW_RANKING_FLOOR" in report["blocking_codes"]


@pytest.mark.unit
def test_llm_unavailable_is_inconclusive_not_a_fake_pass():
    report = evaluate_ranking_candidate_packaging(
        title_verdict=TITLE,
        blurb_verdict=BLURB,
        persona_report={"llm_used": False, "samples": 0, "clicks": 0},
    )
    assert report["passed"] is False
    assert report["status"] == "INCONCLUSIVE"
    assert "PERSONA_EVIDENCE_UNAVAILABLE" in report["blocking_codes"]


@pytest.mark.unit
def test_comedy_requires_auditable_visible_packaging_evidence():
    missing = evaluate_ranking_candidate_packaging(
        title_verdict=TITLE,
        blurb_verdict=BLURB,
        persona_report=PERSONA,
        selected_effects=("comedy_engine",),
        effect_realization={},
    )
    assert missing["status"] == "BLOCKED"
    assert "COMEDY_PACKAGING_REALIZATION_MISSING" in missing["blocking_codes"]

    present = evaluate_ranking_candidate_packaging(
        title_verdict=TITLE,
        blurb_verdict=BLURB,
        persona_report=PERSONA,
        selected_effects=("comedy_engine",),
        title_text="荒城通关簿",
        blurb_text="狮人本想强闯，界碑却先给绕路开出更高价，领队被迫咬牙成交。",
        effect_realization={
            "comedy_engine": {
                "passed": True,
                "evidence": "领队被迫咬牙成交",
                "evidence_quote": "领队被迫咬牙成交",
            }
        },
    )
    assert present["status"] == "PASS"


@pytest.mark.unit
def test_comedy_evidence_must_be_bound_to_actual_title_or_blurb():
    report = evaluate_ranking_candidate_packaging(
        title_verdict=TITLE,
        blurb_verdict=BLURB,
        persona_report=PERSONA,
        selected_effects=("comedy_engine",),
        title_text="荒城通关簿",
        blurb_text="商洛接手废城，决定重建商路。",
        effect_realization={
            "comedy_engine": {
                "passed": True,
                "evidence": "领队被迫咬牙成交",
                "evidence_quote": "领队被迫咬牙成交",
            }
        },
    )
    assert report["status"] == "BLOCKED"
    assert "COMEDY_PACKAGING_REALIZATION_MISSING" in report["blocking_codes"]


@pytest.mark.unit
@pytest.mark.parametrize("bad_score", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_scores_cannot_bypass_hard_floors(bad_score: float):
    report = evaluate_ranking_candidate_packaging(
        title_verdict=_verdict(bad_score, {"hook_power": bad_score, "graspable_subject": 4}),
        blurb_verdict=BLURB,
        persona_report=PERSONA,
    )
    assert report["passed"] is False
    assert "TITLE_TOTAL_BELOW_RANKING_FLOOR" in report["blocking_codes"]
    assert "TITLE_HOOK_POWER_BELOW_FLOOR" in report["blocking_codes"]


@pytest.mark.unit
def test_report_contains_structured_metrics_and_findings():
    report = evaluate_ranking_candidate_packaging(
        title_verdict=TITLE,
        blurb_verdict=BLURB,
        persona_report=PERSONA,
    )
    assert {"passed", "status", "blocking_codes", "findings", "metrics"} <= report.keys()
    assert report["metrics"]["persona_clicks"] == pytest.approx(2)


def test_fabricated_structured_comedy_cannot_replace_a_quote():
    report = evaluate_ranking_candidate_packaging(
        title_verdict=TITLE, blurb_verdict=BLURB, persona_report=PERSONA,
        selected_effects=("comedy_engine",), title_text="抢修", blurb_text="维修员赶往旧站。",
        effect_realization={"comedy_engine": {"passed": True,
            "evidence_quote": "领队被迫咬牙成交", "expectation": "要钱",
            "misalignment": "反被收费", "reaction": "笑场", "situation_change": "成交"}},
    )
    assert "COMEDY_PACKAGING_REALIZATION_MISSING" in report['blocking_codes']
