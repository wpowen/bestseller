from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.contradiction import (
    ContradictionCheckResult,
    ContradictionViolation,
    ContradictionWarning,
)
from bestseller.services.contradiction import (
    _check_character_knowledge_leaks,
    _extract_keywords,
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


def test_extract_keywords_filters_generic_english_stopwords() -> None:
    keywords = _extract_keywords(
        "Maya reads the letter with Kade before the powered community displacement."
    )

    assert "with" not in keywords
    assert "the" not in keywords
    assert {"maya", "letter", "kade", "powered", "community", "displacement"}.issubset(
        keywords
    )


@pytest.mark.asyncio
async def test_character_knowledge_leak_ignores_stopwords_and_participant_names() -> None:
    class _Result:
        def scalars(self) -> "_Result":
            return self

        def all(self) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    name="Kade Mercer",
                    knowledge_state_json={
                        "falsely_believes": [
                            "Registration program could work with proper safeguards",
                            "Maya is simply closing a door",
                        ],
                        "unaware_of": [],
                    },
                )
            ]

    class _Session:
        async def execute(self, _stmt: object) -> _Result:
            return _Result()

    violations, warnings = await _check_character_knowledge_leaks(
        _Session(),
        uuid4(),
        365,
        ["Kade Mercer", "Maya Mercer"],
        (
            "Maya reads the letter with Kade and recognizes their father's "
            "handwriting before the powered community displacement."
        ),
        language="en",
    )

    assert violations == []
    assert warnings == []


@pytest.mark.asyncio
async def test_character_knowledge_leak_ignores_single_low_signal_overlap() -> None:
    class _Result:
        def scalars(self) -> "_Result":
            return self

        def all(self) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    name="Rowan Ashford",
                    knowledge_state_json={
                        "falsely_believes": [
                            "Believed the cost meant sacrifice/death for herself"
                        ],
                        "unaware_of": [],
                    },
                )
            ]

    class _Session:
        async def execute(self, _stmt: object) -> _Result:
            return _Result()

    violations, warnings = await _check_character_knowledge_leaks(
        _Session(),
        uuid4(),
        74,
        ["Rowan Ashford"],
        "Rowan sees that the ritual has a cost, but no one names the price yet.",
        language="en",
    )

    assert violations == []
    assert warnings == []


@pytest.mark.asyncio
async def test_character_knowledge_leak_still_blocks_specific_overlap() -> None:
    class _Result:
        def scalars(self) -> "_Result":
            return self

        def all(self) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    name="Rowan Ashford",
                    knowledge_state_json={
                        "falsely_believes": [
                            "Believed the cost meant sacrifice/death for herself"
                        ],
                        "unaware_of": [],
                    },
                )
            ]

    class _Session:
        async def execute(self, _stmt: object) -> _Result:
            return _Result()

    violations, warnings = await _check_character_knowledge_leaks(
        _Session(),
        uuid4(),
        74,
        ["Rowan Ashford"],
        "Rowan learns the old bargain requires sacrifice before the door opens.",
        language="en",
    )

    assert len(violations) == 1
    assert warnings == []


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
