# ruff: noqa: I001, RUF001
from __future__ import annotations

import pytest

from bestseller.services.reverse_outline_gate import (
    build_story_state_snapshot,
    evaluate_reverse_outline_gate,
    reverse_outline_report_to_dict,
)


pytestmark = pytest.mark.unit


def _story_design_kernel() -> dict[str, object]:
    return {
        "version": 1,
        "shape": {
            "length_class": "long",
            "publication_mode": "web_serial",
            "outline_depth": "chapter",
            "primary_duties": ["forward_pull", "visible_system_change"],
            "ending_contract": "close current loop while opening next desire",
        },
        "reader_promise": "每章都让资源、关系或制度位置发生可见变化。",
        "premise_contract": {
            "unique_hook": "灵田产出与信任债绑定",
            "core_question": "主角能否把个人信任扩展成宗门制度?",
            "commercial_pull": "经营成果、关系债和规则漏洞互相兑现。",
            "forbidden_defaults": ["家庭创伤或身世旧案默认驱动", "神秘玉佩"],
        },
        "character_conflict_contracts": [
            {
                "character_key": "protagonist",
                "external_goal": "获得第一块灵田经营权",
                "internal_need": "学会授权",
                "pressure_source": "宗门收益考核",
                "choice_axis": "控制还是信任",
                "change_vector": "资源权限变化",
            }
        ],
        "world_conflict_contracts": [
            {
                "axis": "灵田规则",
                "rule": "信任债影响灵田产出",
                "visible_cost": "关系破裂会让产出下降",
                "escalation_path": "从个人关系扩展到宗门制度",
            }
        ],
        "structure_strategy": {
            "macro_strategy": "经营闭环逐步扩大",
            "chapter_engine": "每章推进一个资源账或关系账",
            "pacing_rule": "短兑现与长债务交替",
            "freshness_rule": "连续三章不得重复同一压力源",
        },
        "plot_tree": [
            {
                "key": "mainline",
                "line_type": "main",
                "label": "灵田经营权",
                "role": "驱动外部目标",
                "current_state": "没有资源入口",
                "target_state": "稳定产出",
                "failure_if_removed": "故事失去经营推进",
            }
        ],
        "beat_schedule": [
            {
                "chapter_range": "1-3",
                "duty": "建立资源账",
                "state_change": "从无资格到获得试运营资格",
                "payoff": "经营规则第一次兑现",
                "hook_or_aftereffect": "资格绑定隐藏债务",
            }
        ],
        "change_vectors": ["资源权限变化", "信任边界变化", "制度压力变化"],
    }


def _outline() -> dict[str, object]:
    return {
        "batch_name": "volume-1-outline",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "灵田试约",
                "goal": "主角争取灵田试运营资格，让资源权限从无到有。",
                "main_conflict": "宗门执事要求三日内交出产出方案，否则资格转给竞争者。",
                "hook_description": "资格通过，但隐藏债务被写进契约。",
                "scenes": [
                    {
                        "story": "主角用信任债规则说服盟友临时入局。",
                        "emotion": "从孤立转为谨慎合作。",
                    }
                ],
            }
        ],
    }


def test_state_snapshot_extracts_chapter_state_changes() -> None:
    snapshot = build_story_state_snapshot(_story_design_kernel(), _outline())

    assert snapshot["chapter_count"] == 1
    assert snapshot["chapter_state_changes"][0]["chapter_number"] == 1
    assert "资源权限变化" in snapshot["kernel_change_vectors"]


def test_reverse_outline_gate_passes_stateful_outline() -> None:
    report = evaluate_reverse_outline_gate(_story_design_kernel(), _outline())

    assert report.passed is True
    assert report.blocking_findings == ()
    assert reverse_outline_report_to_dict(report)["passed"] is True


def test_reverse_outline_gate_flags_forbidden_default_motivation() -> None:
    outline = _outline()
    chapter = outline["chapters"][0]
    assert isinstance(chapter, dict)
    chapter["main_conflict"] = "主角因为父亲失踪，被迫寻找神秘玉佩。"

    report = evaluate_reverse_outline_gate(_story_design_kernel(), outline)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "forbidden_default_motivation" in codes


def test_reverse_outline_gate_flags_chapter_without_state_change() -> None:
    outline = _outline()
    chapter = outline["chapters"][0]
    assert isinstance(chapter, dict)
    chapter["goal"] = "推进主线。"
    chapter["main_conflict"] = "继续承压推进。"
    chapter["hook_description"] = "出现新的问题。"
    chapter["scenes"] = []

    report = evaluate_reverse_outline_gate(_story_design_kernel(), outline)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "chapter_missing_state_change" in codes
