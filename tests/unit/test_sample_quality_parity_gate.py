from __future__ import annotations

from bestseller.services.sample_quality_parity_gate import (
    evaluate_sample_quality_parity,
)


def _good_kwargs() -> dict[str, object]:
    return {
        "category_key": "suspense-mystery",
        "chapter_count": 30,
        "final_verdict": "pass",
        "review_overall_score": 0.86,
        "scorecard_quality_score": 84.0,
        "whole_book_quality_report": {"passed": True},
        "premium_gate_report": {
            "passed": True,
            "capability_snapshot": {
                "category_hard_engine": {
                    "passed": True,
                    "category_key": "suspense-mystery",
                }
            },
        },
        "reference_distance_score": 0.81,
        "fallback_count": 0,
        "export_status": "exported",
    }


def test_sample_quality_parity_gate_passes_complete_evidence() -> None:
    report = evaluate_sample_quality_parity(**_good_kwargs())

    assert report.passed is True
    assert report.readiness_level == "ready"
    assert report.findings == ()
    assert report.metrics["category_hard_engine_passed"] is True


def test_sample_quality_parity_gate_blocks_low_scorecard() -> None:
    kwargs = _good_kwargs()
    kwargs["scorecard_quality_score"] = 74.0

    report = evaluate_sample_quality_parity(**kwargs)

    assert report.passed is False
    assert report.readiness_level == "partial"
    assert {finding.code for finding in report.findings} == {"scorecard_below_parity"}


def test_sample_quality_parity_gate_blocks_missing_reference_distance() -> None:
    kwargs = _good_kwargs()
    kwargs["reference_distance_score"] = None

    report = evaluate_sample_quality_parity(**kwargs)

    assert report.passed is False
    assert report.readiness_level == "blocked"
    assert "reference_distance_missing" in {finding.code for finding in report.findings}


def test_sample_quality_parity_gate_blocks_low_reference_distance() -> None:
    kwargs = _good_kwargs()
    kwargs["reference_distance_score"] = 0.61

    report = evaluate_sample_quality_parity(**kwargs)

    assert report.passed is False
    assert report.readiness_level == "blocked"
    assert "reference_distance_too_low" in {finding.code for finding in report.findings}


def test_sample_quality_parity_gate_blocks_missing_category_hard_engine() -> None:
    kwargs = _good_kwargs()
    kwargs["premium_gate_report"] = {"passed": True, "capability_snapshot": {}}

    report = evaluate_sample_quality_parity(**kwargs)

    assert report.passed is False
    assert report.readiness_level == "blocked"
    assert "category_hard_engine_not_passed" in {
        finding.code for finding in report.findings
    }


def test_sample_quality_parity_gate_blocks_short_pilot() -> None:
    kwargs = _good_kwargs()
    kwargs["chapter_count"] = 29

    report = evaluate_sample_quality_parity(**kwargs)

    assert report.passed is False
    assert report.readiness_level == "blocked"
    assert "pilot_chapter_count_below_parity" in {
        finding.code for finding in report.findings
    }
