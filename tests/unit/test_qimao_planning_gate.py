from __future__ import annotations

import pytest

from bestseller.services.qimao_planning_gate import (
    evaluate_qimao_planning_gate,
    qimao_planning_gate_report_to_dict,
)

pytestmark = pytest.mark.unit


def _good_contract() -> dict[str, object]:
    return {
        "qimao_opening_contract": {
            "opening_incident": "女主推门进账房时，族叔正按着账童抢账本，威胁不交就烧掉母亲旧案证据。",
            "first_page_conflict": "女主在前600字内被逼交出账本，否则母亲旧案会被销毁。",
            "protagonist_immediate_goal": "先保住账本并确认谁在灭口。",
            "visible_loss_if_fail": "失败会失去母亲翻案的唯一证据。",
            "protagonist_edge": "她能从账目细节看出别人忽略的漏洞。",
            "edge_limit": "账本只能证明第一层异常，不能直接推翻主谋。",
            "chapter_1_small_turn": "主角用账页错位反制上门逼迫的人。",
            "chapter_2_reveal": "逼迫她的人并不是主谋，而是被旧案牵制的中间人。",
            "chapter_3_payoff": "主角拿到账房暗格里的第一份签押证据，确认灭口者与族叔相连。",
            "first_10000_loop": "触发冲突 -> 主角行动 -> 收益/代价 -> 新钩子",
            "forbidden_opening_modes": ["background_exposition", "normal_day", "scenery_first"],
        }
    }


def test_qimao_planning_gate_passes_complete_contract() -> None:
    report = evaluate_qimao_planning_gate(_good_contract())

    assert report.passed is True
    assert report.findings == ()


def test_qimao_planning_gate_rejects_abstract_visual_anchor_contract() -> None:
    report = evaluate_qimao_planning_gate({
        "opening_quality_contract": {
            "opening_incident": (
                "【三秒定调原则】开篇即以一件引发异象的器物建立视觉锚点，"
                "同步呈现主角的差异化身份与即时危机。避免冗长背景交代，"
                "通过器物异动场景自然带出志怪世界观。"
            ),
            "first_page_conflict": "围绕器灵异化的新一层压力开始成形。",
            "protagonist_immediate_goal": "苏砚必须立刻追查器物流向与母亲死亡真相。",
            "visible_loss_if_fail": "失败会失去母亲死亡真相、铭纹鼎下落和自身生机。",
            "protagonist_edge": "苏砚能与器灵共感，看见别人忽略的器物残痕。",
            "edge_limit": "优势只能解决第一轮压力，不能直接跳过主线代价。",
            "chapter_1_small_turn": "苏砚主动行动，完成一次局部反制或信息差建立。",
            "chapter_2_reveal": "第二章放出改变局势判断的新信息。",
            "chapter_3_payoff": "第三章完成一个小回报并打开下一轮危险。",
            "first_10000_loop": (
                "主角利用差异化优势抢先一步 -> 得到短回报 -> 引来更大反压 "
                "-> 被迫升级手段 -> 揭开更深真相"
            ),
            "forbidden_opening_modes": ["background_exposition", "normal_day"],
        }
    })

    codes = {finding.code for finding in report.findings}
    assert report.passed is False
    assert "abstract_opening_incident" in codes
    assert "abstract_first_page_conflict" in codes
    assert "non_immediate_protagonist_goal" in codes
    assert "meta_chapter_3_payoff" in codes


def test_qimao_planning_gate_fails_missing_contract() -> None:
    report = evaluate_qimao_planning_gate({})

    assert report.passed is False
    assert [finding.code for finding in report.findings] == ["missing_opening_quality_contract"]


def test_qimao_planning_gate_fails_rejection_shaped_contract() -> None:
    report = evaluate_qimao_planning_gate({
        "qimao_opening_contract": {
            "opening_incident": "主角在清晨醒来，先介绍世界观背景和家族设定。",
            "protagonist_immediate_goal": "",
            "visible_loss_if_fail": "",
            "protagonist_edge": "",
            "chapter_3_payoff": "",
            "first_10000_loop": "继续讲主线并铺垫世界观",
            "forbidden_opening_modes": [],
        }
    })

    codes = {finding.code for finding in report.findings}
    assert report.passed is False
    assert "ordinary_entry" in codes
    assert "missing_first_page_conflict" in codes
    assert "missing_protagonist_goal" in codes
    assert "missing_visible_loss" in codes
    assert "missing_protagonist_edge" in codes
    assert "missing_chapter_3_payoff" in codes
    assert "missing_chapter_1_small_turn" in codes
    assert "first_10k_loop_missing" in codes
    assert "missing_forbidden_opening_modes" in codes


def test_qimao_planning_gate_report_serializes_to_dict() -> None:
    report = evaluate_qimao_planning_gate({"qimao_opening_contract": {}})
    payload = qimao_planning_gate_report_to_dict(report)

    assert payload["passed"] is False
    assert payload["findings"][0]["code"] == "missing_opening_quality_contract"
