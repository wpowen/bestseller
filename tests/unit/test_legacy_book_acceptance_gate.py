from __future__ import annotations

import pytest

from bestseller.services.legacy_book_acceptance_gate import (
    evaluate_legacy_book_acceptance,
)

pytestmark = pytest.mark.unit


def test_legacy_book_acceptance_passes_complete_clean_book() -> None:
    report = evaluate_legacy_book_acceptance(
        scorecard={
            "quality_score": 88.0,
            "missing_chapters": 0,
            "chapters_blocked": 0,
            "length_cv": 0.08,
            "hype_missing_chapters": 0,
        },
        premium_gate_report={"passed": True},
        repair_plan={"task_count": 0},
        model_execution_ready=True,
    )

    assert report.passed is True
    assert report.readiness_level == "ready"
    assert report.findings == ()


def test_legacy_book_acceptance_blocks_current_historical_failure_shape() -> None:
    report = evaluate_legacy_book_acceptance(
        scorecard={
            "quality_score": 54.93,
            "missing_chapters": 400,
            "chapters_blocked": 7,
            "length_cv": 0.1512,
            "hype_missing_chapters": 59,
        },
        premium_gate_report={"passed": True},
        repair_plan={"task_count": 40},
        model_execution_ready=False,
    )
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert report.readiness_level == "blocked"
    assert {
        "book_incomplete",
        "scorecard_below_acceptance_bar",
        "blocked_chapters_remaining",
        "length_stability_below_bar",
        "hype_assignments_missing",
        "repair_tasks_remaining",
        "model_execution_unavailable",
    }.issubset(codes)


def test_legacy_book_acceptance_ignores_medium_only_repair_backlog() -> None:
    report = evaluate_legacy_book_acceptance(
        scorecard={
            "quality_score": 88.0,
            "missing_chapters": 0,
            "chapters_blocked": 0,
            "length_cv": 0.08,
            "hype_missing_chapters": 0,
        },
        premium_gate_report={"passed": True},
        repair_plan={
            "task_count": 15,
            "priority_counts": {"medium": 15},
        },
        model_execution_ready=True,
    )

    assert report.passed is True
    assert report.metrics["repair_task_count"] == 0


def test_legacy_book_acceptance_blocks_planned_chapters_without_drafts() -> None:
    report = evaluate_legacy_book_acceptance(
        scorecard={
            "quality_score": 88.0,
            "missing_chapters": 0,
            "draftless_chapters": 43,
            "chapters_blocked": 0,
            "length_cv": 0.08,
            "hype_missing_chapters": 0,
        },
        premium_gate_report={"passed": True},
        repair_plan={"task_count": 0},
        model_execution_ready=True,
    )

    assert report.passed is False
    assert report.readiness_level == "blocked"
    assert report.metrics["draftless_chapters"] == 43
    assert {finding.code for finding in report.findings} == {
        "planned_chapters_without_current_drafts"
    }


def test_legacy_book_acceptance_blocks_structural_gate_failure() -> None:
    report = evaluate_legacy_book_acceptance(
        scorecard={
            "quality_score": 90.0,
            "missing_chapters": 0,
            "chapters_blocked": 0,
            "length_cv": 0.05,
            "hype_missing_chapters": 0,
        },
        premium_gate_report={"passed": False},
        repair_plan={"task_count": 0},
        model_execution_ready=True,
    )

    assert report.passed is False
    assert report.readiness_level == "blocked"
    assert {finding.code for finding in report.findings} == {
        "premium_gate_not_passed"
    }
