from __future__ import annotations

import pytest

from bestseller.services.checker_schema import CheckerIssue, CheckerReport
from bestseller.services.methodology_health import (
    build_methodology_health_report,
    compute_longform_chaos_index,
    methodology_repair_actions,
)

pytestmark = pytest.mark.unit


def _report(issue_id: str) -> CheckerReport:
    issue = CheckerIssue(
        id=issue_id,
        type="methodology",
        severity="high",
        location="chapter 1",
        description="risk",
        suggestion="repair",
        can_override=True,
    )
    return CheckerReport(
        agent="test",
        chapter=1,
        overall_score=85,
        passed=False,
        issues=(issue,),
    )


def test_methodology_health_reports_profile_coverage_and_top_issues() -> None:
    report = build_methodology_health_report(
        checker_reports=(
            _report("OPENING_CH1_PRESSURE_MISSING"),
            _report("OPENING_CH1_PRESSURE_MISSING"),
            _report("CHEKHOV_USE_OVERDUE"),
        ),
        latest_chapter_number=35,
        longform_inputs={"overdue_clue_count": 3, "setup_payoff_debt_count": 2},
        longform_chaos_enabled=True,
    )

    assert report["enabled"] is True
    assert report["methodology_profile_id"] == "plova_structured_writing_v1"
    assert report["coverage"]["verified_sources"] == 35
    assert report["coverage"]["pending_sources"] == 3
    assert "opening_three_function" in report["active_gates"]
    assert report["top_methodology_issues"][0] == {
        "code": "OPENING_CH1_PRESSURE_MISSING",
        "count": 2,
    }
    assert report["longform_chaos"]["enabled"] is True


def test_methodology_health_without_profile_is_disabled() -> None:
    report = build_methodology_health_report(profile_id=None)

    assert report == {"enabled": False, "reason": "methodology_profile_missing"}


def test_longform_chaos_index_is_audit_only_before_threshold() -> None:
    report = compute_longform_chaos_index(
        latest_chapter_number=12,
        inputs={"overdue_clue_count": 8, "stale_truth_count": 2},
        enabled=True,
        start_after_chapter=30,
    )

    assert report["enabled"] is True
    assert report["audit_only"] is True
    assert report["risk_level"] == "audit_only"
    assert report["score"] > 0


def test_longform_chaos_index_reports_high_risk_after_threshold() -> None:
    report = compute_longform_chaos_index(
        latest_chapter_number=45,
        inputs={
            "line_balance": 0.1,
            "foreshadowing_debt": 0.0,
            "timeline_stability": 0.2,
            "entry_freshness": 0.2,
            "world_reveal_control": 0.3,
            "outline_executability": 0.1,
        },
        enabled=True,
        start_after_chapter=30,
    )

    assert report["risk_level"] in {"high", "critical"}
    assert report["top_repairs"][0]["component"] == "foreshadowing_debt"


def test_methodology_repair_actions_cover_opening_action_chekhov_and_chaos() -> None:
    actions = methodology_repair_actions(
        {
            "enabled": True,
            "pending_sources": ["plova.36"],
            "top_methodology_issues": [
                {"code": "OPENING_CH1_PRESSURE_MISSING", "count": 1},
                {"code": "ACTION_SCENE_OBJECTIVE_MISSING", "count": 1},
                {"code": "CHEKHOV_USE_OVERDUE", "count": 1},
            ],
            "longform_chaos": {"risk_level": "high"},
        }
    )
    action_names = [action["action"] for action in actions]

    assert "review_methodology_pending_sources" in action_names
    assert "repair_opening_three_function" in action_names
    assert "review_action_scene_structure" in action_names
    assert "review_chekhov_overdue" in action_names
    assert "repair_longform_chaos" in action_names
