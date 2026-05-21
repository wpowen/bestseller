from __future__ import annotations

import pytest

from bestseller.services.action_scene_structure_gate import evaluate_action_scene_structure

pytestmark = pytest.mark.unit


def test_action_scene_structure_passes_complete_structured_contract() -> None:
    report = evaluate_action_scene_structure(
        scene_type="fight",
        chapter=4,
        scene_number=2,
        scene_contract={
            "action_sequence": ["林渊格挡黑线。", "他诱敌离开香案。", "铜镜反照出真身。"],
            "fight_objective": "夺回铜镜。",
            "failure_cost": "失败会暴露阴阳眼并失去父亲线索。",
            "opponent_advantage": "周老板占据祠堂机关。",
            "tactic_shift": "从正面硬闯改为诱敌离开香案。",
            "emotion_driver": "林渊不想再让妹妹替自己受罚。",
            "turning_point": "铜镜裂纹映出第二个周老板。",
            "exit_state_delta": "林渊拿到线索但被全镇通缉。",
        },
    )

    assert report.passed is True
    assert report.metrics["is_action_scene"] is True
    assert report.issues == ()


def test_action_scene_structure_reports_missing_contract_fields() -> None:
    report = evaluate_action_scene_structure(
        scene_type="combat",
        chapter=5,
        scene_number=1,
        scene_contract={
            "action_sequence": ["两人交手三招。"],
            "fight_objective": "逃出药铺。",
        },
    )

    codes = {issue.id for issue in report.issues}

    assert report.passed is False
    assert "ACTION_SCENE_FAILURE_COST_MISSING" in codes
    assert "ACTION_SCENE_OPPONENT_ADVANTAGE_MISSING" in codes
    assert "ACTION_SCENE_TACTIC_SHIFT_MISSING" in codes
    assert "ACTION_SCENE_EMOTION_DRIVER_MISSING" in codes
    assert "ACTION_SCENE_TURNING_POINT_MISSING" in codes
    assert "ACTION_SCENE_STATE_DELTA_MISSING" in codes


def test_action_scene_structure_skips_non_action_scene() -> None:
    report = evaluate_action_scene_structure(
        scene_type="conversation",
        chapter=6,
        scene_text="林渊和妹妹在药铺低声讨论父亲旧名。",
    )

    assert report.passed is True
    assert report.metrics["is_action_scene"] is False
