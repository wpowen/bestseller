from __future__ import annotations

import pytest

from bestseller.services.premium_benchmark import (
    builtin_premium_benchmark_cases,
    run_premium_benchmark_cases,
)

pytestmark = pytest.mark.unit


def test_builtin_premium_benchmark_cases_cover_good_and_bad_fixtures() -> None:
    cases = builtin_premium_benchmark_cases()
    case_ids = {case.case_id for case in cases}

    assert len(cases) == 6
    assert "xianxia-survival-good" in case_ids
    assert "xianxia-survival-bad-empty-upgrade" in case_ids
    assert "rule-mystery-good" in case_ids
    assert "rule-mystery-bad-missing-rules" in case_ids
    assert "female-no-cp-good" in case_ids
    assert "female-no-cp-bad-missing-agency" in case_ids


def test_run_premium_benchmark_cases_passes_all_expected_outcomes() -> None:
    result = run_premium_benchmark_cases()

    assert result.passed is True
    assert result.passed_case_count == 6
    assert result.failed_case_count == 0


def test_premium_benchmark_bad_xianxia_fails_for_expected_codes() -> None:
    case = next(
        item
        for item in builtin_premium_benchmark_cases()
        if item.case_id == "xianxia-survival-bad-empty-upgrade"
    )

    result = run_premium_benchmark_cases([case])
    case_result = result.case_results[0]

    assert case_result.passed is True
    assert case_result.gate_passed is False
    assert "progression_engine_missing" in case_result.actual_blocking_codes
    assert "faction_ecology_missing" in case_result.actual_blocking_codes
