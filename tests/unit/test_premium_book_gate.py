# ruff: noqa: RUF001
from __future__ import annotations

import pytest

from bestseller.services.premium_book_gate import (
    evaluate_premium_project_readiness,
    premium_book_gate_report_to_dict,
)

pytestmark = pytest.mark.unit


def _valid_xianxia_metadata() -> dict[str, object]:
    return {
        "world_spec": {
            "power_system": {
                "name": "青炉修行体系",
                "realms": ["炼气", "筑基", "金丹"],
            },
            "factions": [
                {
                    "name": "青炉宗",
                    "goal": "垄断外门筑基丹",
                    "relationship_to_protagonist": "观察并压价",
                }
            ],
        },
        "cast_spec": {
            "protagonist": {
                "name": "沈砚",
                "decision_policy": {
                    "core_rule": "先保命，再换取可复用资源。",
                    "will_not_do": ["无代价突破"],
                },
                "relationships": [
                    {
                        "target": "港务官",
                        "relationship_type": "交易盟友",
                        "tension_summary": "互相不信任但有共同敌人",
                    }
                ],
            }
        },
        "premium_state_ledger_report": {"passed": True, "findings": []},
        "premium_state_snapshot": {
            "passed": True,
            "resource_balances": {"沈砚": {"筑基丹": 1}},
            "rule_state": {},
            "faction_pressure_queue": [
                {
                    "faction": "青炉宗",
                    "trigger": "沈砚取得筑基丹",
                    "reaction": "派外门执事压价收购",
                }
            ],
            "relationship_state": {
                "沈砚 -> 港务官": {
                    "axes": {"trust": "有限合作"},
                    "last_active_choice": "主动交出丹药换船票",
                }
            },
            "open_agency_debts": [],
        },
    }


def test_premium_book_gate_accepts_complete_xianxia_engine() -> None:
    report = evaluate_premium_project_readiness(
        _valid_xianxia_metadata(),
        genre="仙侠",
        sub_genre="凡人流",
    )

    assert report.passed is True
    assert report.score >= 80
    assert report.blocking_findings == ()
    assert report.capability_snapshot["progression_engine"] is True
    assert report.capability_snapshot["faction_ecology"] is True


def test_premium_book_gate_blocks_rule_heavy_project_without_rules() -> None:
    metadata = {
        "cast_spec": {
            "protagonist": {
                "name": "林渊",
                "decision_policy": {"core_rule": "先验证规则，再破局。"},
            }
        },
        "premium_state_ledger_report": {"passed": True, "findings": []},
        "premium_state_snapshot": {"passed": True},
    }

    report = evaluate_premium_project_readiness(
        metadata,
        genre="民俗悬疑",
        sub_genre="规则怪谈",
    )
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "rule_system_missing" in codes
    assert any("规则系统" in action for action in report.recommended_repair_actions)


def test_premium_book_gate_blocks_invalid_premium_state_ledger() -> None:
    metadata = _valid_xianxia_metadata()
    metadata["premium_state_ledger_report"] = {
        "passed": False,
        "findings": [
            {
                "code": "generic_faction_reaction",
                "severity": "critical",
                "message": "Faction reaction is generic.",
                "path": "faction_reactions[0]",
            }
        ],
    }

    report = evaluate_premium_project_readiness(
        metadata,
        genre="仙侠",
        sub_genre="凡人流",
    )
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "premium_state_ledger_blocking" in codes
    assert "generic_faction_reaction" in report.blocking_findings[0].message


def test_premium_book_gate_blocks_relationship_genre_without_relationship_agency() -> None:
    metadata = {
        "cast_spec": {
            "protagonist": {
                "name": "苏棠",
                "decision_policy": {"core_rule": "事业优先，不用恋爱解决核心困境。"},
            }
        },
        "premium_state_ledger_report": {"passed": True, "findings": []},
        "premium_state_snapshot": {"passed": True},
    }

    report = evaluate_premium_project_readiness(
        metadata,
        genre="女频",
        sub_genre="女性成长无CP",
    )
    payload = premium_book_gate_report_to_dict(report)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert payload["passed"] is False
    assert "relationship_agency_missing" in codes
