from __future__ import annotations

import pytest

from bestseller.domain.contradiction import (
    ContradictionCheckResult,
    ContradictionViolation,
    ContradictionWarning,
)

pytestmark = pytest.mark.unit


# ── ContradictionViolation model ─────────────────────────────────


def test_contradiction_violation_model() -> None:
    violation = ContradictionViolation(
        check_type="character_knowledge_leak",
        severity="error",
        message="Character knows something they should not.",
        evidence="falsely_believes: the king is alive",
    )
    assert violation.check_type == "character_knowledge_leak"
    assert violation.severity == "error"
    assert violation.message == "Character knows something they should not."
    assert violation.evidence == "falsely_believes: the king is alive"


def test_contradiction_warning_model() -> None:
    warning = ContradictionWarning(
        check_type="stale_clue",
        message="Clue CLU-001 is overdue by 5 chapters.",
        recommendation="Resolve the clue in an upcoming scene.",
    )
    assert warning.check_type == "stale_clue"
    assert warning.message == "Clue CLU-001 is overdue by 5 chapters."
    assert warning.recommendation == "Resolve the clue in an upcoming scene."


def test_contradiction_warning_default_recommendation() -> None:
    warning = ContradictionWarning(
        check_type="dead_end_arc",
        message="Arc ARC-01 has no recent beats.",
    )
    assert warning.recommendation == ""


# ── ContradictionCheckResult model ───────────────────────────────


def test_contradiction_check_result_model() -> None:
    violation = ContradictionViolation(
        check_type="timeline_order",
        severity="error",
        message="Non-monotonic story_order detected.",
    )
    warning = ContradictionWarning(
        check_type="dormant_antagonist",
        message="Plan PLAN-01 has been dormant for 12 chapters.",
    )
    result = ContradictionCheckResult(
        passed=False,
        violations=[violation],
        warnings=[warning],
        checks_run=5,
    )
    assert result.passed is False
    assert len(result.violations) == 1
    assert len(result.warnings) == 1
    assert result.checks_run == 5


def test_check_result_passed_when_no_violations() -> None:
    result = ContradictionCheckResult(
        passed=True,
        violations=[],
        warnings=[
            ContradictionWarning(
                check_type="stale_clue",
                message="Minor stale clue detected.",
            )
        ],
        checks_run=3,
    )
    assert result.passed is True
    assert len(result.violations) == 0
    assert len(result.warnings) == 1


def test_check_result_failed_when_violations() -> None:
    result = ContradictionCheckResult(
        passed=False,
        violations=[
            ContradictionViolation(
                check_type="character_knowledge_leak",
                severity="error",
                message="Knowledge leak in scene 3.",
            ),
            ContradictionViolation(
                check_type="timeline_order",
                severity="error",
                message="Timeline event out of order.",
            ),
        ],
        warnings=[],
        checks_run=5,
    )
    assert result.passed is False
    assert len(result.violations) == 2
    assert result.violations[0].check_type == "character_knowledge_leak"
    assert result.violations[1].check_type == "timeline_order"


def test_check_result_defaults() -> None:
    result = ContradictionCheckResult(passed=True)
    assert result.violations == []
    assert result.warnings == []
    assert result.checks_run == 0
