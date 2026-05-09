from __future__ import annotations

import pytest

from bestseller.services.premium_state_ledger import validate_premium_state_ledger

pytestmark = pytest.mark.unit


def test_valid_premium_state_ledger_passes() -> None:
    report = validate_premium_state_ledger(
        {
            "progression_events": [
                {
                    "event_type": "resource_spent",
                    "subject": "沈砚",
                    "resource_key": "筑基丹",
                    "cause": "换取港务官放行",
                }
            ],
            "rule_events": [
                {
                    "rule_code": "R-001",
                    "visible_effect": "执法堂封港",
                    "cost": "散修身份暴露",
                }
            ],
            "faction_reactions": [
                {
                    "faction": "执法堂",
                    "trigger": "筑基丹消失",
                    "reaction": "封锁码头并优先查散修",
                }
            ],
            "relationship_events": [
                {
                    "character_a": "沈砚",
                    "character_b": "港务官",
                    "axis": "trust",
                    "after": "有限合作",
                    "active_choice": "主动交出丹药",
                }
            ],
            "agency_debts": [
                {
                    "owner": "沈砚",
                    "debt": "补回筑基资源",
                    "due_window": "5章内",
                }
            ],
        }
    )

    assert report.passed is True
    assert report.findings == ()


def test_invalid_premium_state_ledger_reports_actionable_findings() -> None:
    report = validate_premium_state_ledger(
        {
            "progression_events": [
                {"event_type": "breakthrough", "subject": "沈砚"},
            ],
            "rule_events": [
                {"rule_code": "R-001", "visible_effect": "雾气变冷"},
            ],
            "faction_reactions": [
                {"faction": "执法堂", "trigger": "筑基丹消失", "reaction": "所有势力震惊"},
            ],
            "relationship_events": [
                {"character_a": "沈砚", "character_b": "港务官", "after": "有限合作"},
            ],
            "agency_debts": [
                {"owner": "沈砚", "debt": "补回筑基资源"},
            ],
        }
    )

    codes = {finding.code for finding in report.findings}
    assert report.passed is False
    assert "progression_cause_missing" in codes
    assert "rule_cost_or_exploit_missing" in codes
    assert "generic_faction_reaction" in codes
    assert "relationship_axis_missing" in codes
    assert "relationship_active_choice_missing" in codes
    assert "agency_debt_due_missing" in codes
