from __future__ import annotations

from bestseller.services.distilled_strategy_compiler import (
    DistilledStrategyCard,
    SelectedMechanism,
)
from bestseller.services.distilled_strategy_gate import (
    distilled_strategy_gate_snapshot,
    evaluate_distilled_strategy_consumption,
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
            )
        ],
        required_state_variables=["cross_system_understanding"],
        required_change_vectors=["exploit_rule_gap"],
        reader_reward_mix=["knowledge_arbitrage"],
        anti_copy_boundaries=["exact-opening-chain"],
    )


def test_distilled_strategy_gate_passes_when_plan_consumes_state() -> None:
    report = evaluate_distilled_strategy_consumption(
        _card(),
        story_design_kernel={
            "change_vectors": ["exploit_rule_gap"],
            "plot_tree": [
                {
                    "label": "航图误判线",
                    "current_state": "cross_system_understanding low",
                    "target_state": "cross_system_understanding rises with cost",
                }
            ],
        },
        volume_plan=[
            {
                "volume_number": 1,
                "core_payoff": "knowledge_arbitrage with exposure cost",
            }
        ],
    )

    assert report.passed is True
    assert report.metrics["consumed_signal_count"] >= 3
    snapshot = distilled_strategy_gate_snapshot(report)
    assert snapshot["passed"] is True


def test_distilled_strategy_gate_blocks_unconsumed_strategy() -> None:
    report = evaluate_distilled_strategy_consumption(
        _card(),
        story_design_kernel={"change_vectors": ["relationship_shift"]},
        volume_plan=[{"volume_number": 1, "core_payoff": "public duel"}],
    )

    assert report.passed is False
    assert any(issue.id == "DISTILLED_STRATEGY_NOT_CONSUMED" for issue in report.issues)


def test_distilled_strategy_gate_blocks_copy_risk_boundary() -> None:
    report = evaluate_distilled_strategy_consumption(
        _card(),
        plan_text="The outline repeats exact-opening-chain before changing the world.",
    )

    assert report.passed is False
    assert any(issue.id == "DISTILLED_STRATEGY_COPY_RISK" for issue in report.issues)
    assert report.blocks_write is True


def test_distilled_strategy_gate_reports_missing_card() -> None:
    report = evaluate_distilled_strategy_consumption(None, plan_text="some plan")

    assert report.passed is True
    assert any(issue.id == "DISTILLED_STRATEGY_MISSING" for issue in report.issues)
