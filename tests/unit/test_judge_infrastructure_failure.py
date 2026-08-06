"""A judge verdict has three outcomes, not two.

"The judge timed out" is not "the prose is bad". Conflating them sends a good
chapter into a rewrite loop that cannot possibly fix it, and records a 0.0 that
poisons every quality statistic downstream.
"""

from __future__ import annotations

import pytest

from bestseller.domain.llm_quality_judge import (
    JUDGE_INFRASTRUCTURE_FAILURE_CODES,
    LLMQualityJudgeResult,
)


def _infra_failure(score: float = 0.0) -> LLMQualityJudgeResult:
    return LLMQualityJudgeResult.model_validate(
        {
            "pass": False,
            "overall_score": score,
            "blocking_issues": [
                {
                    "code": "CHAPTER_JUDGE_UNAVAILABLE",
                    "severity": "critical",
                    "evidence": "LLM chapter quality judge returned fallback content.",
                }
            ],
        }
    )


def _quality_reject(score: float = 0.55) -> LLMQualityJudgeResult:
    return LLMQualityJudgeResult.model_validate(
        {
            "pass": False,
            "overall_score": score,
            "blocking_issues": [
                {
                    "code": "OPENING_PULL_WEAK",
                    "severity": "critical",
                    "evidence": "开篇三段没有出现具体动作。",
                }
            ],
        }
    )


def _passing(score: float = 0.88) -> LLMQualityJudgeResult:
    return LLMQualityJudgeResult.model_validate(
        {"pass": True, "overall_score": score, "dimension_scores": {"readability": 0.9}}
    )


@pytest.mark.unit
def test_infrastructure_failure_is_distinguished_from_quality_rejection() -> None:
    infra = _infra_failure()
    rejected = _quality_reject()

    assert infra.infrastructure_failure is True
    assert infra.quality_rejected is False, "must not drive a prose rewrite"

    assert rejected.infrastructure_failure is False
    assert rejected.quality_rejected is True


@pytest.mark.unit
def test_a_passing_verdict_is_neither() -> None:
    result = _passing()
    assert result.infrastructure_failure is False
    assert result.quality_rejected is False


@pytest.mark.unit
@pytest.mark.parametrize("code", sorted(JUDGE_INFRASTRUCTURE_FAILURE_CODES))
def test_every_registered_infra_code_is_recognised(code: str) -> None:
    result = LLMQualityJudgeResult.model_validate(
        {"pass": False, "blocking_issues": [{"code": code, "severity": "critical"}]}
    )
    assert result.infrastructure_failure is True


@pytest.mark.unit
def test_infra_code_matching_is_case_insensitive() -> None:
    result = LLMQualityJudgeResult.model_validate(
        {
            "pass": False,
            "blocking_issues": [
                {"code": "chapter_judge_unavailable", "severity": "critical"}
            ],
        }
    )
    assert result.infrastructure_failure is True


@pytest.mark.unit
def test_median_ignores_samples_that_never_answered() -> None:
    """One timed-out sample must not drag a good chapter under the floor."""

    import statistics

    samples = [_passing(0.88), _infra_failure(0.0), _passing(0.86)]

    naive_median = statistics.median(r.overall_score for r in samples)
    answered = [r for r in samples if not r.infrastructure_failure]
    corrected_median = statistics.median(r.overall_score for r in answered)

    assert naive_median == pytest.approx(0.86)
    assert corrected_median == pytest.approx(0.87)
    # The regression this guards: with a 0.87 floor, the naive median fails a
    # chapter that two out of three judges actually passed.
    assert naive_median < 0.87 <= corrected_median


@pytest.mark.unit
def test_all_samples_failing_reports_infrastructure_not_a_zero_score() -> None:
    samples = [_infra_failure(), _infra_failure(), _infra_failure()]
    answered = [r for r in samples if not r.infrastructure_failure]

    assert answered == []
    # The aggregator returns the infra result itself rather than fabricating a
    # 0.0 quality verdict from nothing.
    assert samples[0].infrastructure_failure is True
