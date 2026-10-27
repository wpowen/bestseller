from __future__ import annotations

import pytest

from bestseller.services.architecture_realization import audit_architecture_realization

pytestmark = pytest.mark.unit


def _complete_rows():
    stages = ("setup", "incongruity", "reaction", "situation_change")
    architecture = [{"unit_id": f"u{index}"} for index in range(1, 5)]
    materialization = [
        {
            "unit_id": f"u{index}",
            "scene_id": f"s{index}",
            "choice": "坚持公开报价",
            "cost": "失去短期暴利",
            "state_delta": "商队开始接受关城规则",
            "opponent_adaptation": "走私据点联合压价",
            "realization_revision": "r2",
            "effect": "comedy_engine",
            "stage": stage,
            "callback": index == 4,
        }
        for index, stage in enumerate(stages, 1)
    ]
    prose = [
        {"unit_id": f"u{index}", "text": "角色行动、反应并改变了关系状态。"}
        for index in range(1, 5)
    ]
    receipts = [{"unit_id": f"u{index}", "realization_revision": "r2"} for index in range(1, 5)]
    return architecture, materialization, prose, receipts


def test_architecture_choice_cost_opponent_and_comedy_are_realized_in_prose() -> None:
    report = audit_architecture_realization(
        *_complete_rows(),
        selected_effects=("comedy_engine",),
    )

    assert report.passed is True
    assert report.realized_units == ("u1", "u2", "u3", "u4")


def test_orphaned_unit_and_stale_receipt_block_realization() -> None:
    architecture, materialization, prose, receipts = _complete_rows()
    materialization = materialization[:-1]
    receipts[0] = {"unit_id": "u1", "realization_revision": "r1"}

    report = audit_architecture_realization(
        architecture,
        materialization,
        prose,
        receipts,
        selected_effects=("comedy_engine",),
    )

    assert report.passed is False
    assert "ARCHITECTURE_UNIT_ORPHANED" in report.blocking_codes
    assert "STALE_REALIZATION_VERDICT" in report.blocking_codes
    assert "EFFECT_CONTRACT_UNREALIZED" in report.blocking_codes
