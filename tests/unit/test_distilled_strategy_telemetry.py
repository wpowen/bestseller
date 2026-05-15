from __future__ import annotations

from bestseller.services.checker_schema import CheckerIssue, CheckerReport
from bestseller.services.distilled_strategy_compiler import (
    DistilledStrategyCard,
    SelectedMechanism,
)
from bestseller.services.distilled_strategy_telemetry import (
    summarize_distilled_strategy_outcomes,
)


def _card() -> DistilledStrategyCard:
    return DistilledStrategyCard(
        aggregate_key="otherworld-cross-system",
        maturity_score=0.62,
        maturity_status="review",
        source_count=2,
        selected_mechanisms=[
            SelectedMechanism(
                mechanism_id="cross-system-rule-arbitrage",
                source_confidence=0.86,
                design_role="series_engine",
                adaptation_instruction="转化为本项目因果链。",
                required_project_specific_binding="绑定到失效航图。",
                failure_mode="未绑定项目元素。",
            ),
            SelectedMechanism(
                mechanism_id="professional-symbol-decoding",
                source_confidence=0.76,
                design_role="chapter_rhythm",
                adaptation_instruction="转化为本项目场景功能。",
                required_project_specific_binding="绑定到遗迹航标。",
                failure_mode="未绑定场景目标。",
            ),
        ],
    )


def test_summarize_distilled_strategy_outcomes_counts_usage_and_rewrites() -> None:
    summary = summarize_distilled_strategy_outcomes(
        _card(),
        usage_records=[
            {
                "mechanism_id": "cross-system-rule-arbitrage",
                "passed": True,
                "quality_delta": 4,
            },
            {
                "mechanism_id": "cross-system-rule-arbitrage",
                "rewrite_triggered": True,
                "quality_delta": -2,
            },
            {
                "mechanism_id": "professional-symbol-decoding",
                "copy_risk": True,
            },
        ],
    )

    by_id = {row["mechanism_id"]: row for row in summary["mechanisms"]}
    assert by_id["cross-system-rule-arbitrage"]["planned_uses"] == 2
    assert by_id["cross-system-rule-arbitrage"]["passed_uses"] == 1
    assert by_id["cross-system-rule-arbitrage"]["rewrite_triggered_uses"] == 1
    assert by_id["cross-system-rule-arbitrage"]["average_quality_delta"] == 1.0
    assert summary["totals"]["copy_risk_incidents"] == 1


def test_summarize_distilled_strategy_outcomes_counts_checker_copy_risk() -> None:
    report = CheckerReport(
        agent="distilled-strategy-gate",
        chapter=0,
        overall_score=70,
        passed=False,
        issues=(
            CheckerIssue(
                id="DISTILLED_STRATEGY_COPY_RISK",
                type="distilled_strategy",
                severity="critical",
                location="plan",
                description="copy risk",
                suggestion="rewrite",
                can_override=False,
            ),
        ),
    )

    summary = summarize_distilled_strategy_outcomes(
        _card(),
        checker_reports=[report],
    )

    assert summary["totals"]["copy_risk_incidents"] == 1
